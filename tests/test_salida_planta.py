"""La salida de la planta: si el bus no está, lo que sale queda en la cola.

El cliente MQTT publica eventos, órdenes y avisos. Cuando el bus de la planta
no está, esos mensajes **no se pierden**: quedan en la cola de PostgreSQL
(``outbox_cola``) y salen, en orden, cuando el bus vuelve.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

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

from config import Settings
from planta.mqtt_listener import ClientePlanta, ErrorPublicacion


class InfoFalsa:
    """El resultado de ``publish()`` de paho."""

    def __init__(self, rc: int = mqtt.MQTT_ERR_SUCCESS) -> None:
        self.rc = rc


class ClienteMqttFalso:
    """Un cliente paho de mentira."""

    def __init__(self, *, conectado: bool = True, rc: int = mqtt.MQTT_ERR_SUCCESS) -> None:
        self._conectado = conectado
        self._rc = rc
        self.publicados: list[tuple[str, str, int]] = []

    def is_connected(self) -> bool:
        return self._conectado

    def publish(self, topico: str, payload: str, qos: int = 1) -> InfoFalsa:
        self.publicados.append((topico, payload, qos))
        return InfoFalsa(self._rc)


class ColaFalsa:
    """La cola de salida, en memoria."""

    def __init__(self) -> None:
        self.encolados: list[tuple[str, str, dict[str, Any]]] = []

    def encolar_outbox(
        self, tipo: str, payload: dict[str, Any], topico: str = "sistema/tablet"
    ) -> str:
        self.encolados.append((tipo, topico, payload))
        return f"out-{len(self.encolados)}"


def _cliente(mqtt_falso: Any, cola: Any = None) -> ClientePlanta:
    return ClientePlanta(
        Settings(),
        al_nota=lambda _: None,
        al_respuesta=lambda _: None,
        al_sensor=lambda _: None,
        cliente=mqtt_falso,
        cola=cola,
    )


def _evento() -> EventoSistema:
    return EventoSistema(
        id="parte:NOTA-00001:A1",
        ts=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
        tipo=TipoEvento.alerta_seguridad,
        equipo="A1",
        origen="parte",
        parte_id="NOTA-00001",
        nota_ids=["NOTA-00001"],
        resumen="fuga en la descarga",
    )


def _orden() -> OrdenSistema:
    return OrdenSistema(
        id="ORD-A1-1",
        ts=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
        evento_ids=["parte:NOTA-00001:A1"],
        equipo="A1",
        estado=EstadoOrden.pendiente,
        tareas=["T-B-EMP"],
        detiene_equipo=False,
        reduce_inyeccion=False,
        ejecutor="propio",
        repuestos=[],
        motivo="falta repuesto",
    )


def _mensaje() -> MensajeTablet:
    return MensajeTablet(
        id="pia3-1",
        ts=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
        nota_ids=["NOTA-00001"],
        texto="borrador",
        pantalla=Pantalla(titulo="Parte", filas=[]),
        espera_respuesta=True,
    )


# -------------------------------------------------------------- bus disponible


def test_con_el_bus_connectedo_el_evento_sale_directo() -> None:
    mqtt_falso = ClienteMqttFalso()
    cola = ColaFalsa()

    _cliente(mqtt_falso, cola).publicar_evento(_evento())

    assert cola.encolados == []
    assert len(mqtt_falso.publicados) == 1
    topico, payload, _ = mqtt_falso.publicados[0]
    assert topico == Topicos.EVENTOS
    assert '"parte:NOTA-00001:A1"' in payload


def test_con_el_bus_connectedo_la_orden_sale_directo() -> None:
    mqtt_falso = ClienteMqttFalso()
    cola = ColaFalsa()

    _cliente(mqtt_falso, cola).publicar_orden(_orden())

    assert cola.encolados == []
    assert mqtt_falso.publicados[0][0] == Topicos.ORDENES


# ---------------------------------------------------------------- bus caído


def test_sin_bus_el_evento_queda_en_la_cola() -> None:
    mqtt_falso = ClienteMqttFalso(conectado=False)
    cola = ColaFalsa()

    _cliente(mqtt_falso, cola).publicar_evento(_evento())

    assert mqtt_falso.publicados == []
    assert len(cola.encolados) == 1
    tipo, topico, payload = cola.encolados[0]
    assert tipo == "evento"
    assert topico == Topicos.EVENTOS
    assert payload["id"] == "parte:NOTA-00001:A1"


def test_sin_bus_la_orden_queda_en_la_cola() -> None:
    mqtt_falso = ClienteMqttFalso(conectado=False)
    cola = ColaFalsa()

    _cliente(mqtt_falso, cola).publicar_orden(_orden())

    assert mqtt_falso.publicados == []
    assert cola.encolados[0][0] == "orden"
    assert cola.encolados[0][1] == Topicos.ORDENES


def test_sin_bus_el_aviso_de_tablet_queda_en_la_cola() -> None:
    mqtt_falso = ClienteMqttFalso(conectado=False)
    cola = ColaFalsa()

    _cliente(mqtt_falso, cola).publicar_tablet(_mensaje())

    assert mqtt_falso.publicados == []
    assert cola.encolados[0][0] == "tablet"
    assert cola.encolados[0][1] == Topicos.TABLET


def test_si_el_bus_rechaza_el_mensaje_este_queda_en_la_cola() -> None:
    """El bus conectado pero que no acepta el mensaje: tampoco se pierde."""
    mqtt_falso = ClienteMqttFalso(rc=mqtt.MQTT_ERR_NO_CONN)
    cola = ColaFalsa()

    _cliente(mqtt_falso, cola).publicar_evento(_evento())

    assert len(cola.encolados) == 1
    assert cola.encolados[0][0] == "evento"


def test_sin_cola_un_fallo_de_publicacion_se_avisa() -> None:
    """Sin cola (por ejemplo, en una prueba), el fallo no se esconde."""
    mqtt_falso = ClienteMqttFalso(rc=mqtt.MQTT_ERR_NO_CONN)

    try:
        _cliente(mqtt_falso, None).publicar_evento(_evento())
    except ErrorPublicacion:
        pass
    else:  # pragma: no cover - el fallo tiene que verse
        raise AssertionError("tenía que haber levantado ErrorPublicacion")


def test_la_propiedad_conectado_refleja_el_bus() -> None:
    assert _cliente(ClienteMqttFalso(conectado=True)).conectado is True
    assert _cliente(ClienteMqttFalso(conectado=False)).conectado is False
