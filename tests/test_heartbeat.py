"""Tests del latido ``sistema/estado``."""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from planta_contrato import EstadoSistema
from pydantic import ValidationError

from config import Settings
from planta.heartbeat import Heartbeat, Latido, comprobar_enlace

TS = "2026-11-04T12:00:00+00:00"


def _settings(**cambios: object) -> Settings:
    return Settings(_env_file=None, **cambios)


def test_latido_rechaza_campos_extra() -> None:
    with pytest.raises(ValidationError):
        Latido(
            sistema="pia3",
            enlace_ok=True,
            pendientes_sincronizar=0,
            ts=TS,
            campo_inventado=1,
        )


def test_latido_rechaza_pendientes_negativos() -> None:
    with pytest.raises(ValidationError):
        Latido(
            sistema="pia3",
            enlace_ok=True,
            pendientes_sincronizar=-1,
            ts=TS,
        )


def test_construir_usa_las_dependencias_inyectadas() -> None:
    heartbeat = Heartbeat(
        _settings(sistema_id="prueba"),
        engine=object(),
        comprobar_enlace_fn=lambda: False,
        contar_pendientes_fn=lambda _engine: 7,
    )

    latido = heartbeat.construir()

    assert latido.sistema == "prueba"
    assert latido.enlace_ok is False
    assert latido.pendientes_sincronizar == 7


def test_ts_tiene_zona_horaria() -> None:
    latido = Heartbeat(
        _settings(),
        comprobar_enlace_fn=lambda: True,
        contar_pendientes_fn=lambda _engine: 0,
    ).construir()

    assert isinstance(latido.ts, datetime)
    assert latido.ts.tzinfo is not None


def test_payload_valida_contra_el_contrato() -> None:
    latido = Heartbeat(
        _settings(),
        comprobar_enlace_fn=lambda: True,
        contar_pendientes_fn=lambda _engine: 0,
    ).construir()

    reconstruido = EstadoSistema.model_validate_json(latido.model_dump_json())

    assert reconstruido.sistema == latido.sistema
    assert reconstruido.pendientes_sincronizar == 0


def test_sin_engine_reporta_cero_pendientes() -> None:
    latido = Heartbeat(
        _settings(),
        engine=None,
        comprobar_enlace_fn=lambda: True,
    ).construir()

    assert latido.pendientes_sincronizar == 0


def test_publicar_sin_cliente_no_falla() -> None:
    Heartbeat(_settings(), mqtt_client=None).publicar(
        Latido(
            sistema="pia3",
            enlace_ok=True,
            pendientes_sincronizar=0,
            ts=TS,
        )
    )


def _respuesta(estado: dict[str, object], codigo: int = 200) -> httpx.Response:
    return httpx.Response(
        codigo,
        content=json.dumps(estado).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "http://enlace:8081/estado"),
    )


def test_el_enlace_caido_se_lee_del_puerto_de_control(monkeypatch) -> None:
    """La planta publica ``{"arriba": false}`` cuando se cae el radioenlace."""
    monkeypatch.setattr(
        httpx.Client,
        "get",
        lambda self, *args, **kwargs: _respuesta({"arriba": False}),
    )

    assert comprobar_enlace(_settings(enlace_check_url="http://enlace:8081/estado")) is False


def test_el_enlace_levantado_se_lee_del_puerto_de_control(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx.Client,
        "get",
        lambda self, *args, **kwargs: _respuesta({"arriba": True}),
    )

    assert comprobar_enlace(_settings(enlace_check_url="http://enlace:8081/estado")) is True


def test_sin_url_de_control_el_enlace_se_asume_disponible() -> None:
    assert comprobar_enlace(_settings(enlace_check_url=None)) is True


def test_un_error_de_red_reporta_el_enlace_caido(monkeypatch) -> None:
    def _falla(self, *args, **kwargs):
        raise httpx.ConnectError("no hay enlace")

    monkeypatch.setattr(httpx.Client, "get", _falla)

    assert comprobar_enlace(_settings(enlace_check_url="http://enlace:8081/estado")) is False


def test_la_cola_se_vacia_cuando_el_enlace_vuelve() -> None:
    """Con el enlace de vuelta, el latido publica lo que quedó en la cola."""
    vaciadas: list[int] = []

    heartbeat = Heartbeat(
        _settings(),
        engine=object(),
        comprobar_enlace_fn=lambda: True,
        contar_pendientes_fn=lambda _engine: 2,
        vaciar_cola_fn=lambda: vaciadas.append(1) or 1,
    )

    heartbeat._vuelta()

    assert vaciadas == [1]


class _MqttFalso:
    """Cliente MQTT mínimo: guarda lo que se publica."""

    def __init__(self) -> None:
        self.publicados: list[dict] = []

    def publish(self, topico: str, payload: str, qos: int = 0, retain: bool = False):
        self.publicados.append(json.loads(payload))
        return SimpleNamespace(rc=0)


def test_el_latido_avisa_cuando_cambian_los_pendientes() -> None:
    """Si se encola algo con el enlace caído, el pendiente tiene que verse.

    El latido sale cada 30 s, pero lo que reporta puede cambiar en cualquier
    momento: un aviso por radio que queda en cola con el enlace caído es
    información que el supervisor necesita ya, no en el próximo latido.
    """
    mqtt_falso = _MqttFalso()
    pendientes = {"valor": 0}

    heartbeat = Heartbeat(
        _settings(latido_intervalo_s=300),
        engine=object(),
        mqtt_client=mqtt_falso,
        comprobar_enlace_fn=lambda: False,
        contar_pendientes_fn=lambda _engine: pendientes["valor"],
    )

    heartbeat._vuelta()
    assert len(mqtt_falso.publicados) == 1

    # Nada cambió: no se publica otro latido (aun con el intervalo largo).
    heartbeat._vuelta()
    assert len(mqtt_falso.publicados) == 1

    # Se encola un aviso por radio: el pendiente se ve enseguida.
    pendientes["valor"] = 1
    heartbeat._vuelta()

    assert len(mqtt_falso.publicados) == 2
    assert mqtt_falso.publicados[-1]["pendientes_sincronizar"] == 1
    assert mqtt_falso.publicados[-1]["enlace_ok"] is False


def test_el_latido_avisa_cuando_se_vacia_la_cola() -> None:
    """Cuando la cola se vacía, el latido lo dice."""
    mqtt_falso = _MqttFalso()
    pendientes = {"valor": 1}

    heartbeat = Heartbeat(
        _settings(latido_intervalo_s=300),
        engine=object(),
        mqtt_client=mqtt_falso,
        comprobar_enlace_fn=lambda: True,
        contar_pendientes_fn=lambda _engine: pendientes["valor"],
        vaciar_cola_fn=lambda: pendientes.__setitem__("valor", 0) or 1,
    )

    heartbeat._vuelta()
    assert mqtt_falso.publicados[-1]["pendientes_sincronizar"] == 1

    heartbeat._vuelta()
    assert len(mqtt_falso.publicados) == 2
    assert mqtt_falso.publicados[-1]["pendientes_sincronizar"] == 0
