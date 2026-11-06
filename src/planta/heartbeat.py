"""Latido ``sistema/estado`` publicado cada 30 segundos.

El mensaje es determinista y validado con Pydantic (``extra="forbid"``) para que
cualquier consumidor pueda confiar en el contrato:

    {
      "sistema": "pia3",
      "enlace_ok": true,
      "pendientes_sincronizar": 0,
      "ts": "2026-11-04T12:00:00+00:00"
    }
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import paho.mqtt.client as mqtt
from planta_contrato import EstadoSistema
from pydantic import Field
from sqlalchemy.engine import Engine

from config import Settings
from db.session import contar_pendientes, crear_engine

logger = logging.getLogger("planta.heartbeat")


class Latido(EstadoSistema):
    """``EstadoSistema`` del contrato, con validación extra de la cola."""

    pendientes_sincronizar: int = Field(ge=0)


def ahora() -> datetime:
    """Momento actual con zona horaria UTC."""
    return datetime.now(UTC)


def comprobar_enlace(settings: Settings) -> bool:
    """Verifica el enlace externo contra ``ENLACE_CHECK_URL``.

    Sin URL configurada no se monitorea el enlace y se asume disponible.

    La planta publica el estado del radioenlace en su puerto de control
    (``GET /estado`` -> ``{"arriba": true|false}``). Si la respuesta trae ese
    campo, manda ese valor; si no, se usa el código HTTP.
    """
    if not settings.enlace_check_url:
        return True

    try:
        with httpx.Client(
            timeout=settings.enlace_timeout_s,
            follow_redirects=True,
            trust_env=True,
        ) as cliente:
            respuesta = cliente.get(settings.enlace_check_url)
    except httpx.HTTPError as exc:
        logger.warning("Enlace no disponible: %s", exc)
        return False

    if not 200 <= respuesta.status_code < 400:
        logger.warning("Enlace no disponible: HTTP %s", respuesta.status_code)
        return False

    try:
        datos = respuesta.json()
    except ValueError:
        return True
    if isinstance(datos, dict) and isinstance(datos.get("arriba"), bool):
        return bool(datos["arriba"])
    return True


class Heartbeat:
    """Genera y publica el latido, con dependencias inyectables para tests."""

    def __init__(
        self,
        settings: Settings,
        *,
        engine: Engine | None = None,
        mqtt_client: mqtt.Client | None = None,
        comprobar_enlace_fn: Callable[[], bool] | None = None,
        contar_pendientes_fn: Callable[[Engine], int] | None = None,
        vaciar_cola_fn: Callable[[], int] | None = None,
    ) -> None:
        self._settings = settings
        self._engine = engine
        self._mqtt = mqtt_client
        self._comprobar_enlace = comprobar_enlace_fn or (lambda: comprobar_enlace(settings))
        self._contar_pendientes = contar_pendientes_fn or contar_pendientes
        self._vaciar_cola = vaciar_cola_fn or (lambda: 0)
        self._aviso_cola = False
        self._ultimo_enlace: bool | None = None
        self._ultimo_latido = 0.0

    def construir(self) -> Latido:
        """Arma el latido a partir del enlace y de la cola persistente."""
        pendientes = 0
        if self._engine is not None:
            try:
                pendientes = self._contar_pendientes(self._engine)
            except Exception as exc:  # la tabla puede no existir antes de migrar
                if not self._aviso_cola:
                    logger.warning(
                        "No se pudo leer outbox_cola (%s); se reportan 0 pendientes",
                        type(exc).__name__,
                    )
                    logger.debug("Detalle de la lectura de outbox_cola", exc_info=True)
                    self._aviso_cola = True

        return Latido(
            sistema=self._settings.sistema_id,
            enlace_ok=self._comprobar_enlace(),
            pendientes_sincronizar=pendientes,
            ts=ahora(),
        )

    def publicar(self, latido: Latido) -> None:
        """Publica el latido con QoS 1 y retain."""
        if self._mqtt is None:
            return

        info = self._mqtt.publish(
            self._settings.latido_topico,
            latido.model_dump_json(),
            qos=1,
            retain=True,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.warning(
                "No se pudo publicar en %s (rc=%s)",
                self._settings.latido_topico,
                info.rc,
            )

    def _vuelta(self) -> None:
        """Una vuelta del bucle: arma el latido y lo publica si corresponde.

        El latido sale cada ``latido_intervalo_s`` y además **de inmediato**
        cuando cambia el estado del enlace: el operador tiene que saber que se
        cortó el radioenlace sin esperar al próximo latido.
        """
        latido = self.construir()
        ahora = time.monotonic()
        cambio = self._ultimo_enlace is not None and latido.enlace_ok != self._ultimo_enlace
        if ahora - self._ultimo_latido >= self._settings.latido_intervalo_s or cambio:
            self.publicar(latido)
            self._ultimo_latido = ahora
            if latido.enlace_ok:
                # Con el enlace de vuelta salen los mensajes que quedaron
                # en la cola (por ejemplo, los avisos por radio).
                self._vaciar_cola()
        self._ultimo_enlace = latido.enlace_ok

    def ejecutar(self, parar: threading.Event | None = None) -> None:
        """Bucle principal: publica de inmediato y luego cada intervalo."""
        parar = parar or threading.Event()

        def _detener(_signum: int, _frame: object) -> None:
            parar.set()

        for senal in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(senal, _detener)
            except ValueError:
                # Fuera del hilo principal (tests): se ignora.
                pass

        if self._mqtt is not None:
            self._mqtt.connect_async(self._settings.mqtt_host, self._settings.mqtt_port)
            self._mqtt.loop_start()

        logger.info(
            "Latido iniciado: sistema=%s topico=%s intervalo=%ss",
            self._settings.sistema_id,
            self._settings.latido_topico,
            self._settings.latido_intervalo_s,
        )

        while not parar.is_set():
            self._vuelta()
            parar.wait(self._settings.latido_sondeo_s)

        if self._mqtt is not None:
            self._mqtt.loop_stop()
            self._mqtt.disconnect()
        logger.info("Latido detenido")


def crear_cliente(settings: Settings) -> mqtt.Client:
    """Cliente MQTT v2 con reintentos ante caída del broker."""
    cliente = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"{settings.sistema_id}-heartbeat",
    )
    cliente.reconnect_delay_set(min_delay=1, max_delay=30)
    if settings.mqtt_usuario:
        cliente.username_pw_set(settings.mqtt_usuario, settings.mqtt_password)
    return cliente


def _al_conectar(
    _cliente: mqtt.Client,
    _userdata: object,
    _flags: object,
    reason_code: object,
    _properties: object,
) -> None:
    if getattr(reason_code, "is_failure", False):
        logger.warning("MQTT rechazó la conexión: %s", reason_code)
    else:
        logger.info("MQTT conectado")


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not settings.enlace_check_url:
        logger.warning("ENLACE_CHECK_URL no definida: enlace_ok se reporta como True")

    engine: Engine | None = None
    try:
        engine = crear_engine(settings.database_url)
    except Exception:
        logger.warning("No se pudo crear el engine de PostgreSQL", exc_info=True)

    cliente = crear_cliente(settings)
    cliente.on_connect = _al_conectar

    Heartbeat(settings, engine=engine, mqtt_client=cliente).ejecutar()


if __name__ == "__main__":
    main()
