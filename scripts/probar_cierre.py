"""Prueba en vivo del cierre de órdenes (Fase 7 + G2/G3).

Monta el escenario completo contra la planta simulada:

1. pone el reloj de la planta en modo «por eventos» (lo mismo que usa el
   corrector oficial), para que las horas simuladas pasen rápido;
2. publica órdenes **aprobadas** de contratista. El planificador es una
   persona y el sistema no se aprueba solo una orden de contratista, así que
   para probar el flujo completo la orden se guarda en la base del sistema
   (como la aprueba el planificador) y se publica en el bus;
3. escucha ``planta/cierres`` y ``sistema/ordenes``;
4. comprueba que el cierre técnico deja cada orden en ``Ejecutada``;
5. opcionalmente envía a la Central una decisión humana autenticada por
   token Bearer y muestra el estado final, la auditoría y el habilita.

Sin ``--token`` la prueba se detiene deliberadamente en ``Ejecutada``: el
sistema no puede certificar, observar ni cerrar por su cuenta.

Cada corrida usa ids de orden nuevos: la planta simulada no vuelve a
programar un trabajo si la orden ya estaba aprobada.

Uso:

    # técnico: deja las órdenes esperando decisión humana
    docker compose exec sistema python scripts/probar_cierre.py contratista 3

    # decisión humana explícita mediante un token de inspector
    docker compose exec sistema python scripts/probar_cierre.py contratista 3 \
        --token "$TOKEN_INSPECTOR" --decision certificar

    # desde el host: el script se copia al contenedor y corre adentro
    docker cp scripts/probar_cierre.py pia3-sistema:/tmp/ && \\
        docker exec pia3-sistema python /tmp/probar_cierre.py contratista 3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

import paho.mqtt.client as mqtt
from planta_contrato import OrdenSistema, Repuesto
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import Settings  # noqa: E402
from db.repositorio import Repositorio  # noqa: E402
from db.session import crear_engine  # noqa: E402

# La lista de órdenes que se prueban: con y sin repuesto, para ver las dos
# salidas del control (certificada y observada).
ORDENES: dict[str, list[dict]] = {
    "propio": [
        {
            "equipo": "F-201A",
            "tareas": ["T-F-CAR"],
            "ejecutor": "propio",
            "repuestos": [("FC-CART-25", 48.0)],
        },
    ],
    "contratista": [
        {
            "equipo": "B-101",
            "tareas": ["T-B-ROD"],
            "ejecutor": "Servipat",
            "repuestos": [("TX-ROD-CIG", 1.0)],
        },
        {
            "equipo": "B-102",
            "tareas": ["T-B-ALI"],
            "ejecutor": "Servipat",
            "repuestos": [("ACO-ELAST-90", 1.0)],
        },
        {
            "equipo": "B-103",
            "tareas": ["T-B-ROD"],
            "ejecutor": "Servipat",
            "repuestos": [],
        },
    ],
}


def _control(ruta: str, cuerpo: dict) -> dict:
    """Le habla a la API de control de la planta simulada."""
    peticion = urllib.request.Request(
        f"{Settings().planta_api_url}/api/control/{ruta}",
        data=json.dumps(cuerpo).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(peticion, timeout=60) as respuesta:
        return json.loads(respuesta.read().decode("utf-8"))


def _decidir_central(
    central_url: str,
    token: str,
    orden_id: str,
    decision: str,
    *,
    motivo: str | None = None,
) -> dict:
    """Registra en la Central una decisión con la identidad del token."""
    cuerpo = {"decision": decision}
    if motivo:
        cuerpo["motivo"] = motivo
    peticion = urllib.request.Request(
        f"{central_url.rstrip('/')}/api/cierres/{urllib.parse.quote(orden_id, safe='')}/decision",
        data=json.dumps(cuerpo).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(peticion, timeout=30) as respuesta:
            return json.loads(respuesta.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"La Central rechazó la decisión de {orden_id} ({error.code}): {detalle}"
        ) from error


def _preparar_orden(repo: Repositorio, plantilla: dict, marca: str) -> OrdenSistema:
    """Guarda la orden como lo haría el planificador al aprobarla."""
    evento_id = f"ev-prueba-{uuid.uuid4().hex[:8]}"
    with repo._engine.begin() as conexion:  # noqa: SLF001 - es una prueba
        conexion.execute(
            text(
                """
                INSERT INTO evento_sistema (id, origen, estado, payload)
                VALUES (:id, 'parte', 'Escalado', CAST(:payload AS jsonb))
                """
            ),
            {"id": evento_id, "payload": json.dumps({"equipo": plantilla["equipo"]})},
        )
    orden = OrdenSistema(
        id=f"ORD-PRUEBA-{marca}-{plantilla['equipo']}",
        ts=datetime.now(UTC),
        estado="Aprobada",
        equipo=plantilla["equipo"],
        evento_ids=[evento_id],
        tareas=list(plantilla["tareas"]),
        detiene_equipo=True,
        reduce_inyeccion=False,
        ejecutor=plantilla["ejecutor"],
        repuestos=[
            Repuesto(codigo=codigo, cantidad=cantidad)
            for codigo, cantidad in plantilla["repuestos"]
        ],
    )
    repo.guardar_orden(
        orden,
        repuestos=[{"codigo": codigo} for codigo, _cantidad in plantilla["repuestos"]],
    )
    return orden


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modo", nargs="?", default="contratista", choices=["propio", "contratista"])
    parser.add_argument("cuantas", nargs="?", type=int, default=1)
    parser.add_argument(
        "--token",
        help="token Bearer de la persona que decide (no se imprime)",
    )
    parser.add_argument(
        "--decision",
        choices=["cerrar", "certificar", "observar"],
        help="decisión humana explícita que se envía a la Central",
    )
    parser.add_argument(
        "--central-url",
        default="http://127.0.0.1:8200",
        help="dirección de la Central dentro del contenedor del sistema",
    )
    args = parser.parse_args()
    if args.decision and not args.token:
        parser.error("--decision requiere --token")
    if args.token and not args.decision:
        parser.error("--token requiere una decisión explícita con --decision")
    if args.token and args.modo == "propio" and args.decision != "cerrar":
        parser.error("el trabajo propio se cierra con --decision cerrar")
    if args.token and args.modo == "contratista" and args.decision == "cerrar":
        parser.error("el trabajo de contratista no se cierra: certificá u observá")

    settings = Settings()
    repo = Repositorio(crear_engine(settings.database_url))
    marca = uuid.uuid4().hex[:6].upper()

    publicaciones: list[dict] = []
    cierres: list[dict] = []
    ordenes_preparadas: list[OrdenSistema] = []

    def al_mensaje(_cliente, _datos, mensaje) -> None:
        try:
            carga = json.loads(mensaje.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if mensaje.topic == "sistema/ordenes":
            publicaciones.append(carga)
            if carga["id"].startswith(f"ORD-PRUEBA-{marca}"):
                print(f"  [orden] {carga['id']} · {carga['estado']} · {carga.get('motivo') or ''}")
        elif mensaje.topic == "planta/cierres":
            cierres.append(carga)
            print(f"  [cierre] {carga['id']} · orden {carga['orden_id']} · {carga['empresa']}")

    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    cliente.on_message = al_mensaje
    cliente.connect(settings.mqtt_host, settings.mqtt_port, 60)
    cliente.subscribe([("sistema/ordenes", 1), ("planta/cierres", 1)])
    cliente.loop_start()

    print("== Reloj por eventos (como el corrector oficial)")
    _control(
        "reloj-eventos",
        {
            "activo": True,
            "rapida": 1800,
            "lenta": 5,
            "silencio_s": 5,
            "minimo_s": 3,
            "maximo_s": 120,
        },
    )
    _control("auto-lento", {"activo": False})

    plantillas = ORDENES[args.modo][: args.cuantas]
    for plantilla in plantillas:
        orden = _preparar_orden(repo, plantilla, marca)
        ordenes_preparadas.append(orden)
        cliente.publish("sistema/ordenes", orden.model_dump_json(), qos=1)
        print(f"  Publicada la orden {orden.id} ({orden.ejecutor})")

    print("\n== Esperando los cierres de las cuadrillas (hasta 20 minutos reales)")
    limite = time.time() + 1200
    while time.time() < limite and len(cierres) < len(plantillas):
        time.sleep(3)

    if cierres:
        print("\n== Esperando el estado técnico Ejecutada")
        fin = time.time() + 120
        while time.time() < fin:
            estados = {
                p["id"]: p["estado"]
                for p in publicaciones
                if p["id"].startswith(f"ORD-PRUEBA-{marca}")
            }
            if len(estados) == len(plantillas) and all(
                estado == "Ejecutada" for estado in estados.values()
            ):
                break
            time.sleep(3)

        if not args.token:
            print(
                "\n== Las órdenes quedaron en Ejecutada y esperan una decisión humana.\n"
                "   Usá la Central o repetí con --token y --decision para probar G2/G3."
            )
        else:
            print("\n== Registrando la decisión humana autenticada")
            for orden in ordenes_preparadas:
                motivo = (
                    "La revisión técnica encontró diferencias sustantivas"
                    if args.decision == "observar"
                    else None
                )
                resultado = _decidir_central(
                    args.central_url,
                    args.token,
                    orden.id,
                    args.decision,
                    motivo=motivo,
                )
                print(
                    f"  {orden.id} · {resultado.get('orden', {}).get('estado')} · "
                    f"habilita={resultado.get('habilita')}"
                )

            print("\n== Esperando la publicación del estado final")
            fin = time.time() + 60
            while time.time() < fin:
                estados = {
                    p["id"]: p["estado"]
                    for p in publicaciones
                    if p["id"].startswith(f"ORD-PRUEBA-{marca}")
                }
                if len(estados) == len(plantillas) and all(
                    estado in ("Cerrada", "Observada", "Certificada")
                    for estado in estados.values()
                ):
                    break
                time.sleep(2)

    print("\n== Lo que publicó el sistema")
    for publicacion in publicaciones:
        if not publicacion["id"].startswith(f"ORD-PRUEBA-{marca}"):
            continue
        print(
            f"  {publicacion['id']} · {publicacion['estado']} · "
            f"motivo={publicacion.get('motivo')} · habilita={publicacion.get('habilita')} · "
            f"monto={publicacion.get('monto_habilita_usd')}"
        )

    print("\n== Lo que quedó en la base")
    with repo._engine.connect() as conexion:  # noqa: SLF001 - es una prueba
        cierres_db = conexion.execute(
            text(
                """
                SELECT orden_id, externa_id, empresa, transcripcion, horas, diferencias
                FROM cierre WHERE orden_id LIKE :patron ORDER BY creado_en
                """
            ),
            {"patron": f"ORD-PRUEBA-{marca}%"},
        ).mappings()
        for fila in cierres_db:
            print(f"  cierre: {fila['orden_id']} · {fila['externa_id']} · {fila['empresa']}")
            print(f"    transcripción: {fila['transcripcion']}")
            print(f"    horas: {fila['horas']} · diferencias: {fila['diferencias']}")
        certificaciones = conexion.execute(
            text(
                """
                SELECT orden_id, inspector, rol, decision, motivo
                FROM certificacion WHERE orden_id LIKE :patron ORDER BY creada_en
                """
            ),
            {"patron": f"ORD-PRUEBA-{marca}%"},
        ).mappings()
        for fila in certificaciones:
            print(
                f"  certificación: {fila['orden_id']} · {fila['decision']} · "
                f"{fila['inspector']} ({fila['rol']}) · {fila['motivo']}"
            )
        decisiones = conexion.execute(
            text(
                """
                SELECT orden_id, decision, actor, rol, motivo, creada_en
                FROM decision_cierre WHERE orden_id LIKE :patron ORDER BY creada_en
                """
            ),
            {"patron": f"ORD-PRUEBA-{marca}%"},
        ).mappings()
        for fila in decisiones:
            print(
                f"  decisión humana: {fila['orden_id']} · {fila['decision']} · "
                f"{fila['actor']} ({fila['rol']}) · {fila['motivo']}"
            )
        habilitas = conexion.execute(
            text(
                """
                SELECT orden_id, numero, monto, moneda, fuente_monto
                FROM habilitacion WHERE orden_id LIKE :patron ORDER BY emitida_en
                """
            ),
            {"patron": f"ORD-PRUEBA-{marca}%"},
        ).mappings()
        for fila in habilitas:
            print(
                f"  habilita: {fila['orden_id']} · {fila['numero']} · "
                f"{fila['monto']} {fila['moneda']} · {fila['fuente_monto']}"
            )
        ordenes = conexion.execute(
            text(
                """
                SELECT id, estado, motivo, ejecutor
                FROM orden WHERE id LIKE :patron ORDER BY id
                """
            ),
            {"patron": f"ORD-PRUEBA-{marca}%"},
        ).mappings()
        for fila in ordenes:
            print(
                f"  orden: {fila['id']} · {fila['estado']} · "
                f"ejecutor={fila['ejecutor']} · {fila['motivo']}"
            )

    cliente.loop_stop()
    cliente.disconnect()


if __name__ == "__main__":
    main()
