"""Arranque del sistema PIA-3: latido + parte por voz."""

from __future__ import annotations

import logging
import signal
import threading
import time

import httpx
from planta_contrato import LecturaSensor
from sqlalchemy.engine import Engine

from config import Settings
from db.repositorio import Repositorio
from db.session import crear_engine
from deteccion.sensores import DetectorSensores
from planta.almacenamiento import Almacenamiento
from planta.cola import publicar_pendientes
from planta.heartbeat import Heartbeat
from planta.heartbeat import crear_cliente as crear_cliente_latido
from planta.historial import Historiador
from planta.mqtt_listener import ClientePlanta
from planta.servicio import ServicioParteVoz
from planta.transcriber import crear_transcriptor

logger = logging.getLogger("planta.main")


def cargar_planta(settings: Settings, intentos: int = 20, espera_s: float = 2.0):
    """Espera a que la API de la planta esté disponible y devuelve su catálogo."""
    url = f"{settings.planta_api_url.rstrip('/')}/api/planta"
    for intento in range(1, intentos + 1):
        try:
            with httpx.Client(timeout=settings.planta_api_timeout_s, trust_env=True) as cliente:
                respuesta = cliente.get(url)
                respuesta.raise_for_status()
                datos = respuesta.json()
                logger.info(
                    "Catálogo de planta cargado: %s equipos, %s sensores",
                    len(datos.get("equipos") or []),
                    len(datos.get("sensores") or []),
                )
                return datos
        except httpx.HTTPError as exc:
            logger.warning("Planta no disponible (%s/%s): %s", intento, intentos, exc)
            time.sleep(espera_s)
    logger.error("No se pudo cargar /api/planta; se usan los alias predeterminados")
    return {}


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    planta = cargar_planta(settings)

    engine: Engine = crear_engine(settings.database_url)
    repositorio = Repositorio(engine)
    historiador = Historiador(
        settings.planta_api_url,
        timeout_s=settings.historial_timeout_s,
    )
    almacenamiento = Almacenamiento(settings.datos_dir, settings.planta_api_url)
    transcriptor = crear_transcriptor(
        settings.asr_motor,
        settings.asr_modelo,
        descarga_local=settings.asr_local_only,
        directorio_descarga=settings.asr_download_root,
        hilos_cpu=settings.asr_hilos_cpu,
    )

    referencia: dict[str, ServicioParteVoz] = {}
    cliente_ref: dict[str, ClientePlanta] = {}
    detector_sensores = DetectorSensores(planta)

    def al_nota(nota) -> None:
        referencia["servicio"].encolar_nota(nota)

    def al_respuesta(respuesta) -> None:
        referencia["servicio"].encolar_respuesta(respuesta)

    def al_sensor(lectura: LecturaSensor) -> None:
        evento = detector_sensores.evaluar(lectura)
        if evento is None:
            return
        evento = historiador.enriquecer_evento(
            evento,
            lectura.tag,
            lectura.variable,
            unidad=lectura.unidad,
        )
        if repositorio.registrar_evento_sensor(evento):
            cliente_ref["cliente"].publicar_evento(evento)
            logger.warning("Evento de sensor publicado: %s", evento.id)

    cliente = ClientePlanta(
        settings,
        al_nota=al_nota,
        al_respuesta=al_respuesta,
        al_sensor=al_sensor,
    )
    cliente_ref["cliente"] = cliente
    servicio = ServicioParteVoz(
        settings,
        repositorio,
        almacenamiento,
        transcriptor,
        cliente,
        planta,
        historiador=historiador,
    )
    referencia["servicio"] = servicio

    parar = threading.Event()

    def _detener(_signum: int, _frame: object) -> None:
        parar.set()

    for senal in (signal.SIGTERM, signal.SIGINT):
        signal.signal(senal, _detener)

    latido = Heartbeat(
        settings,
        engine=engine,
        mqtt_client=crear_cliente_latido(settings),
        # Con el enlace de vuelta salen los mensajes que quedaron en la cola
        # (por ejemplo, el aviso por radio al supervisor).
        vaciar_cola_fn=lambda: publicar_pendientes(repositorio, cliente),
    )
    hilo_latido = threading.Thread(
        target=latido.ejecutar,
        kwargs={"parar": parar},
        name="latido",
        daemon=True,
    )

    hilo_latido.start()
    servicio.iniciar()
    cliente.iniciar()

    logger.info("PIA-3 en marcha")
    parar.wait()

    logger.info("Deteniendo PIA-3")
    servicio.detener()
    cliente.detener()
    historiador.cerrar()
    hilo_latido.join(timeout=10)
    engine.dispose()


if __name__ == "__main__":
    main()
