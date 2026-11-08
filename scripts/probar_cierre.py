"""Prueba en vivo del cierre de órdenes (Fase 7).

Monta el escenario completo contra la planta simulada:

1. pone el reloj de la planta en modo «por eventos» (lo mismo que usa el
   corrector oficial), para que las horas simuladas pasen rápido;
2. publica órdenes **aprobadas** de contratista. El planificador es una
   persona y el sistema no se aprueba solo una orden de contratista, así que
   para probar el flujo completo la orden se guarda en la base del sistema
   (como la aprueba el planificador) y se publica en el bus;
3. escucha ``planta/cierres`` y ``sistema/ordenes``;
4. cuando el sistema publica el estado final, muestra lo que quedó escrito
   en la base: el cierre, la certificación y el habilita.

Cada corrida usa ids de orden nuevos: la planta simulada no vuelve a
programar un trabajo si la orden ya estaba aprobada.

Uso:

    # dentro del contenedor del sistema (el bus y la base están a mano)
    docker compose exec sistema python scripts/probar_cierre.py contratista 3

    # desde el host: el script se copia al contenedor y corre adentro
    docker cp scripts/probar_cierre.py pia3-sistema:/tmp/ && \\
        docker exec pia3-sistema python /tmp/probar_cierre.py contratista 3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
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
    args = parser.parse_args()

    settings = Settings()
    repo = Repositorio(crear_engine(settings.database_url))
    marca = uuid.uuid4().hex[:6].upper()

    publicaciones: list[dict] = []
    cierres: list[dict] = []

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
        cliente.publish("sistema/ordenes", orden.model_dump_json(), qos=1)
        print(f"  Publicada la orden {orden.id} ({orden.ejecutor})")

    print("\n== Esperando los cierres de las cuadrillas (hasta 20 minutos reales)")
    limite = time.time() + 1200
    while time.time() < limite and len(cierres) < len(plantillas):
        time.sleep(3)

    if cierres:
        print("\n== Esperando el estado final del sistema")
        fin = time.time() + 300
        while time.time() < fin:
            estados = [
                p["estado"] for p in publicaciones if p["id"].startswith(f"ORD-PRUEBA-{marca}")
            ]
            if len(estados) >= len(plantillas) and all(
                estado in ("Cerrada", "Observada", "Certificada") for estado in estados
            ):
                break
            time.sleep(3)

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
                SELECT orden_id, inspector, decision, motivo
                FROM certificacion WHERE orden_id LIKE :patron ORDER BY creada_en
                """
            ),
            {"patron": f"ORD-PRUEBA-{marca}%"},
        ).mappings()
        for fila in certificaciones:
            print(f"  certificación: {fila['orden_id']} · {fila['decision']} · {fila['motivo']}")
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
