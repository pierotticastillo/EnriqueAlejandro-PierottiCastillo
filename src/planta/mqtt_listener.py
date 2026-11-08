"""Listener MQTT de la planta: notas de recorrida, respuestas de tablet y cierres."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt
from planta_contrato import (
    EventoSistema,
    LecturaSensor,
    MensajeTablet,
    NotaCierre,
    NotaRecorrida,
    OrdenSistema,
    RespuestaOperador,
    Topicos,
)
from pydantic import ValidationError

from config import Settings
from planta.cola import RepositorioCola

logger = logging.getLogger("planta.listener")


class ErrorPublicacion(Exception):
    """El bus de la planta no aceptó el mensaje."""


class ClientePlanta:
    """Cliente MQTT con suscripciones y publicación de mensajes del sistema."""

    def __init__(
        self,
        settings: Settings,
        *,
        al_nota: Callable[[NotaRecorrida], None],
        al_respuesta: Callable[[RespuestaOperador], None],
        al_sensor: Callable[[LecturaSensor], None],
        al_cierre: Callable[[NotaCierre], None] | None = None,
        al_conectar: Callable[[], None] | None = None,
        cliente: mqtt.Client | None = None,
        cola: RepositorioCola | None = None,
    ) -> None:
        self._settings = settings
        self._al_nota = al_nota
        self._al_respuesta = al_respuesta
        self._al_sensor = al_sensor
        # El cierre de orden (Fase 7). Es opcional para que las pruebas que no
        # lo usan no tengan que saber de él.
        self._al_cierre = al_cierre
        # El callback del usuario no lleva argumentos; el de paho sí. Se
        # guardan con nombres distintos para que uno no tape al otro.
        self._callback_conectar = al_conectar
        self._cliente = cliente or self._crear_cliente()
        # Cola de salida: si el bus no está, lo que no se puede publicar no se
        # pierde, queda en PostgreSQL y sale cuando vuelve.
        self._cola = cola
        self._detenido = threading.Event()

    def _crear_cliente(self) -> mqtt.Client:
        cliente = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"{self._settings.sistema_id}-listener",
        )
        cliente.reconnect_delay_set(min_delay=1, max_delay=30)
        if self._settings.mqtt_usuario:
            cliente.username_pw_set(self._settings.mqtt_usuario, self._settings.mqtt_password)
        return cliente

    def iniciar(self) -> None:
        self._cliente.on_connect = self._al_conectar
        self._cliente.on_message = self._al_mensaje
        self._cliente.connect_async(self._settings.mqtt_host, self._settings.mqtt_port)
        self._cliente.loop_start()
        logger.info(
            "Listener MQTT iniciado en %s:%s",
            self._settings.mqtt_host,
            self._settings.mqtt_port,
        )

    def detener(self) -> None:
        self._detenido.set()
        self._cliente.loop_stop()
        self._cliente.disconnect()
        logger.info("Listener MQTT detenido")

    def publicar(self, topico: str, payload: str, *, qos: int = 1) -> None:
        """Publica un mensaje ya serializado.

        Si el bus no acepta el mensaje, **levanta**: quien publica tiene que
        saber que no salió, para que el mensaje quede en la cola en vez de
        perderse.
        """
        info = self._cliente.publish(topico, payload, qos=qos)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.warning("No se pudo publicar en %s (rc=%s)", topico, info.rc)
            raise ErrorPublicacion(f"No se pudo publicar en {topico} (rc={info.rc})")

    @property
    def conectado(self) -> bool:
        """¿Está el bus de la planta?"""
        return bool(self._cliente.is_connected())

    def _publicar_o_encolar(
        self,
        tipo: str,
        topico: str,
        payload: dict[str, Any],
        id_mensaje: str,
    ) -> None:
        """Publica; si el bus no está, encola para salir después."""
        if self._cola is not None and not self.conectado:
            id_outbox = self._cola.encolar_outbox(tipo, payload, topico)
            logger.warning(
                "Bus caído: el %s %s queda en la cola (%s)",
                tipo,
                id_mensaje,
                id_outbox,
            )
            return
        try:
            self.publicar(topico, json.dumps(payload, ensure_ascii=False))
        except ErrorPublicacion:
            if self._cola is None:
                raise
            id_outbox = self._cola.encolar_outbox(tipo, payload, topico)
            logger.warning(
                "Bus caído: el %s %s queda en la cola (%s)",
                tipo,
                id_mensaje,
                id_outbox,
            )

    def publicar_tablet(self, mensaje: MensajeTablet) -> None:
        """Publica un mensaje de tablet.

        La tablet es local, pero su mensaje también viaja por el bus: si el
        bus no está, el mensaje queda en la cola y sale cuando vuelve.
        """
        self._publicar_o_encolar(
            "tablet",
            Topicos.TABLET,
            mensaje.model_dump(mode="json"),
            mensaje.id,
        )

    def publicar_evento(self, evento: EventoSistema) -> None:
        self._publicar_o_encolar(
            "evento",
            Topicos.EVENTOS,
            evento.model_dump(mode="json"),
            evento.id,
        )

    def publicar_orden(self, orden: OrdenSistema) -> None:
        """Publica la orden de trabajo en ``sistema/ordenes``.

        Cada publicación es el estado actual de la orden: el id es el mismo
        para toda la vida de la orden, así que la planta la reconoce.
        """
        self._publicar_o_encolar(
            "orden",
            Topicos.ORDENES,
            orden.model_dump(mode="json"),
            orden.id,
        )

    def _al_conectar(
        self,
        _cliente: mqtt.Client,
        _userdata: object,
        _flags: object,
        reason_code: object,
        _properties: object,
    ) -> None:
        if getattr(reason_code, "is_failure", False):
            logger.warning("MQTT rechazó la conexión: %s", reason_code)
            return
        self._cliente.subscribe(
            [
                (Topicos.RECORRIDAS, 1),
                (Topicos.RESPUESTAS, 1),
                (Topicos.CIERRES, 1),
                (Topicos.SENSORES.format(tag="+", variable="+"), 1),
            ]
        )
        logger.info(
            "MQTT conectado; suscripciones de recorridas, respuestas, cierres y sensores activas"
        )
        if self._callback_conectar is not None:
            # Al conectar (o al reconectar) se recupera lo que quedó en la base:
            # las órdenes pendientes se vuelven a publicar con el mismo id.
            try:
                self._callback_conectar()
            except Exception:  # noqa: BLE001 - recuperar no puede tumbar el listener
                logger.exception("Falló la recuperación al conectar")

    def _al_mensaje(
        self,
        _cliente: mqtt.Client,
        _userdata: object,
        mensaje: mqtt.MQTTMessage,
    ) -> None:
        topico = mensaje.topic
        try:
            crudo = mensaje.payload.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("Payload no UTF-8 en %s", topico)
            return

        try:
            if topico == Topicos.RECORRIDAS:
                self._al_nota(NotaRecorrida.model_validate_json(crudo))
            elif topico == Topicos.RESPUESTAS:
                self._al_respuesta(RespuestaOperador.model_validate_json(crudo))
            elif topico == Topicos.CIERRES:
                if self._al_cierre is None:
                    logger.debug("Cierre recibido y nadie lo atiende: %s", topico)
                    return
                self._al_cierre(NotaCierre.model_validate_json(crudo))
            elif topico.startswith("planta/sensores/"):
                lectura = LecturaSensor.model_validate_json(crudo)
                segmentos = topico.split("/")
                if len(segmentos) != 4 or segmentos[2:] != [lectura.tag, lectura.variable]:
                    logger.warning("Tópico y lectura de sensor no coinciden: %s", topico)
                    return
                self._al_sensor(lectura)
            else:
                logger.debug("Tópico ignorado: %s", topico)
        except ValidationError as exc:
            logger.warning("Mensaje inválido en %s: %s", topico, exc)
        except Exception:  # noqa: BLE001 - el loop de MQTT no debe morir
            logger.exception("Error procesando mensaje de %s", topico)
