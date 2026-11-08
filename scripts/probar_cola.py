"""Prueba en vivo de la cola de salida (Fase 6).

Encola un aviso de radio, un evento y una orden, se suscribe al bus de la
planta y comprueba tres cosas:

1. que la cola los publica **en orden** y por el tópico que corresponde;
2. que los mensajes **llegan** a la planta (no sólo que se publicaron);
3. que lo que no se puede mandar **queda en la cola** y sale después, sin
   duplicar lo pendiente.

Es la comprobación del criterio de salida de la Fase 6: «simular caída de
enlace, generar eventos, restaurar enlace y verificar que no hay
duplicados».

Uso:

    # dentro del contenedor del sistema (el bus y la base están a mano)
    docker compose exec sistema python scripts/probar_cola.py

    # desde el host: el script se copia al contenedor y corre adentro
    docker cp scripts/probar_cola.py pia3-sistema:/tmp/ && \\
        docker exec pia3-sistema python /tmp/probar_cola.py

    # dejar mensajes en la cola y ver qué queda pendiente (enlace caído)
    docker compose exec sistema python scripts/probar_cola.py --sin-drenar
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import paho.mqtt.client as mqtt
from planta_contrato import (
    EstadoOrden,
    EventoSistema,
    MensajeTablet,
    OrdenSistema,
    Pantalla,
    TipoEvento,
    Topicos,
)
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import Settings  # noqa: E402
from db.repositorio import Repositorio  # noqa: E402
from db.session import crear_engine  # noqa: E402


def _mensajes(sufijo: str) -> dict[str, tuple[str, object]]:
    """Un mensaje de cada tipo, con su tópico."""
    ahora = datetime.now(UTC)
    radio = MensajeTablet(
        id=f"prueba-{sufijo}-radio",
        ts=ahora,
        nota_ids=["NOTA-PRUEBA"],
        texto="aviso por radio de prueba",
        pantalla=Pantalla(titulo="Radio canal 1 - Supervisor de turno", filas=[]),
        espera_respuesta=False,
    )
    evento = EventoSistema(
        id=f"parte:PRUEBA:{sufijo}",
        ts=ahora,
        tipo=TipoEvento.alerta_seguridad,
        equipo="A1",
        origen="parte",
        parte_id="PRUEBA",
        nota_ids=["NOTA-PRUEBA"],
        resumen="evento de prueba de la cola de salida",
    )
    orden = OrdenSistema(
        id=f"ORD-PRUEBA-{sufijo}",
        ts=ahora,
        evento_ids=[f"parte:PRUEBA:{sufijo}"],
        equipo="A1",
        estado=EstadoOrden.pendiente,
        tareas=["T-B-EMP"],
        detiene_equipo=False,
        reduce_inyeccion=False,
        ejecutor="propio",
        repuestos=[],
        motivo="prueba de la cola de salida",
    )
    return {
        "radio": (Topicos.TABLET, radio),
        "evento": (Topicos.EVENTOS, evento),
        "orden": (Topicos.ORDENES, orden),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--espera", type=float, default=15.0, help="segundos de espera del drenaje")
    ap.add_argument(
        "--sin-drenar",
        action="store_true",
        help="no espera al drenaje: muestra lo que queda pendiente",
    )
    ap.add_argument("--broker", default="broker", help="host del bus de la planta")
    ap.add_argument("--puerto", type=int, default=1883, help="puerto del bus")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")

    recibidos: list[tuple[str, str]] = []

    def al_recibir(_cliente, _usuario, mensaje) -> None:
        recibidos.append((mensaje.topic, mensaje.payload.decode("utf-8")))

    cliente_mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    cliente_mqtt.on_message = al_recibir
    cliente_mqtt.connect(args.broker, args.puerto, 60)
    cliente_mqtt.subscribe("#", 1)
    cliente_mqtt.loop_start()
    time.sleep(2)

    settings = Settings()
    motor = crear_engine(settings.database_url)
    repo = Repositorio(motor)
    sufijo = str(int(time.time()))

    print(f"== Encolando (sufijo {sufijo})")
    for tipo, (topico, mensaje) in _mensajes(sufijo).items():
        id_outbox = repo.encolar_outbox(tipo, mensaje.model_dump(mode="json"), topico)
        print(f"  {tipo:6} -> {topico:16} · {id_outbox}")

    print("\n== Pendientes antes del drenaje")
    for _id_outbox, tipo, topico, payload in repo.pendientes_outbox():
        if sufijo in str(payload.get("id", "")):
            print(f"  {tipo:6} · {topico:16} · {payload.get('id')}")

    if not args.sin_drenar:
        print(f"\n== Esperando el drenaje ({args.espera:.0f} s)")
        time.sleep(args.espera)

    cliente_mqtt.loop_stop()

    print("\n== Lo que llegó a la planta")
    for topico, payload in recibidos:
        if sufijo in payload:
            print(f"  {topico:16} :: {json.loads(payload).get('id')}")

    print("\n== Estado final en la cola")
    with motor.connect() as conexion:
        filas = conexion.execute(
            text(
                "SELECT tipo, topico, estado, payload ->> 'id' AS mensaje "
                "FROM outbox_cola WHERE payload ->> 'id' LIKE :patron ORDER BY creado_en"
            ),
            {"patron": f"%{sufijo}%"},
        ).mappings().all()
    for fila in filas:
        print(f"  {fila['tipo']:6} · {fila['topico']:16} · {fila['estado']:9} · {fila['mensaje']}")

    esperados = {
        "sistema/tablet": "radio",
        "sistema/eventos": "evento",
        "sistema/ordenes": "orden",
    }
    llegaron = {topico for topico, payload in recibidos if sufijo in payload}
    faltantes = sorted(set(esperados) - llegaron)
    if args.sin_drenar:
        print("\n(--sin-drenar: se espera que los del radio queden pendientes)")
    elif faltantes:
        print(f"\nFALTARON: {', '.join(faltantes)}")
        sys.exit(1)
    else:
        print("\nOK: los tres mensajes salieron por su tópico y llegaron a la planta.")


if __name__ == "__main__":
    main()
