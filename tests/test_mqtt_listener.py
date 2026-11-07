"""Tests del listener MQTT: suscripciones, publicación de órdenes y recuperación.

El listener es el que publica las órdenes de trabajo en ``sistema/ordenes`` y
el que, al conectar (o al volver de un corte), republica las órdenes pendientes
con el mismo id y el mismo estado.
"""

from __future__ import annotations

import json

import paho.mqtt.client as mqtt
import pytest
from planta_contrato import OrdenSistema, Topicos

from config import Settings
from planta.mqtt_listener import ClientePlanta


class ClienteFalso:
    """Un cliente MQTT que no conecta: sólo guarda lo que se publica."""

    def __init__(self) -> None:
        self.publicados: list[tuple[str, str]] = []
        self.suscripciones: list[object] = []
        self.on_connect = None
        self.on_message = None

    def subscribe(self, suscripciones: object) -> None:
        self.suscripciones = suscripciones  # type: ignore[assignment]

    def connect_async(self, host: str, port: int) -> None:
        self.conectado_en = (host, port)

    def loop_start(self) -> None:
        pass

    def loop_stop(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def publish(self, topico: str, payload: str, qos: int = 0):
        self.publicados.append((topico, payload))

        class Info:
            rc = mqtt.MQTT_ERR_SUCCESS

        return Info()


class Razon:
    is_failure = False


def _listener(cliente: ClienteFalso, *, al_conectar=None) -> ClientePlanta:
    return ClientePlanta(
        Settings(),
        al_nota=lambda _nota: None,
        al_respuesta=lambda _respuesta: None,
        al_sensor=lambda _lectura: None,
        al_conectar=al_conectar,
        cliente=cliente,  # type: ignore[arg-type]
    )


def test_al_conectar_se_suscribe_a_los_topicos_del_sistema() -> None:
    cliente = ClienteFalso()
    listener = _listener(cliente)
    listener.iniciar()
    cliente.on_connect(cliente, None, None, Razon(), None)  # type: ignore[misc]

    topicos = {par[0] for par in cliente.suscripciones}  # type: ignore[index]
    assert Topicos.RECORRIDAS in topicos
    assert Topicos.RESPUESTAS in topicos
    assert "planta/sensores/+/+" in topicos


def test_al_conectar_llama_al_callback_sin_argumentos() -> None:
    """El callback de recuperación no lleva argumentos: paho llama con cinco."""
    cliente = ClienteFalso()
    llamadas: list[int] = []
    listener = _listener(cliente, al_conectar=lambda: llamadas.append(1))

    listener.iniciar()
    cliente.on_connect(cliente, None, None, Razon(), None)  # type: ignore[misc]

    assert llamadas == [1]


def test_la_orden_se_publica_en_sistema_ordenes() -> None:
    cliente = ClienteFalso()
    listener = _listener(cliente)
    orden = OrdenSistema(
        id="ORD-B-102-TX-T01",
        ts="2026-11-06T09:00:00-03:00",
        estado="PendienteAprobacion",
        equipo="B-102",
        evento_ids=["parte:nota-1:B-102"],
        tareas=["T-B-EMP"],
        detiene_equipo=True,
        reduce_inyeccion=False,
        ejecutor="propio",
        repuestos=[],
        motivo="Tarea estándar: la orden no para la bomba",
    )
    listener.publicar_orden(orden)

    assert len(cliente.publicados) == 1
    topico, payload = cliente.publicados[0]
    assert topico == Topicos.ORDENES
    datos = json.loads(payload)
    assert datos["id"] == "ORD-B-102-TX-T01"
    assert datos["estado"] == "PendienteAprobacion"
    assert datos["tareas"] == ["T-B-EMP"]


def test_un_error_en_la_recuperacion_no_tumba_el_listener() -> None:
    cliente = ClienteFalso()

    def romper() -> None:
        raise RuntimeError("se cayó la base")

    listener = _listener(cliente, al_conectar=romper)
    listener.iniciar()

    # No debe levantar: el listener sigue vivo para recibir mensajes.
    cliente.on_connect(cliente, None, None, Razon(), None)  # type: ignore[misc]
    assert cliente.suscripciones


@pytest.mark.parametrize("topico", [Topicos.RECORRIDAS, "planta/sensores/B-101/caudal"])
def test_un_mensaje_invalido_no_mata_el_listener(topico: str) -> None:
    cliente = ClienteFalso()
    recibidos: list[str] = []
    listener = ClientePlanta(
        Settings(),
        al_nota=lambda _nota: recibidos.append("nota"),
        al_respuesta=lambda _respuesta: recibidos.append("respuesta"),
        al_sensor=lambda _lectura: recibidos.append("sensor"),
        cliente=cliente,  # type: ignore[arg-type]
    )
    listener.iniciar()

    class Mensaje:
        def __init__(self, topico: str, payload: bytes) -> None:
            self.topic = topico
            self.payload = payload

    cliente.on_message(cliente, None, Mensaje(topico, b"{no es json"))  # type: ignore[misc]
    assert recibidos == []
