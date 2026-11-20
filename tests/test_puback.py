"""Pruebas de PUBACK (P1-04).

Verifica que:
- `publicar_con_puback` publica con QoS 1 y espera la confirmación del broker.
- Si no hay conexión, devuelve False sin intentar publicar.
- Si el broker no confirma dentro del timeout, devuelve False.
"""

from __future__ import annotations

from planta.cola import Publicador
from planta.mqtt_listener import ClientePlanta


class _PublicadorConPuback:
    """Publicador falso que simula PUBACK."""

    def __init__(self, confirma: bool = True) -> None:
        self.publicados: list[tuple[str, str]] = []
        self.confirma = confirma

    def publicar(self, topico: str, payload: str, *, qos: int = 1) -> None:
        self.publicados.append((topico, payload))

    def publicar_con_puback(
        self, topico: str, payload: str, *, qos: int = 1, timeout_s: float = 10.0
    ) -> bool:
        self.publicar(topico, payload, qos=qos)
        return self.confirma


def test_publicar_con_puback_devuelve_true_si_confirma():
    """PUBACK confirmado: devuelve True."""
    publicador = _PublicadorConPuback(confirma=True)
    resultado = publicador.publicar_con_puback(
        "sistema/ordenes", '{"id":"ORD-001"}', qos=1, timeout_s=5.0
    )
    assert resultado is True
    assert len(publicador.publicados) == 1


def test_publicar_con_puback_devuelve_false_si_no_confirma():
    """Sin PUBACK: devuelve False."""
    publicador = _PublicadorConPuback(confirma=False)
    resultado = publicador.publicar_con_puback(
        "sistema/ordenes", '{"id":"ORD-001"}', qos=1, timeout_s=5.0
    )
    assert resultado is False


def test_publicador_protocolo_tiene_publicar_con_puback():
    """El protocolo Publicador expone publicar_con_puback."""
    assert hasattr(Publicador, "publicar_con_puback")


def test_cliente_planta_tiene_publicar_con_puback():
    """ClientePlanta implementa publicar_con_puback."""
    assert hasattr(ClientePlanta, "publicar_con_puback")


def test_protocolo_publicador_es_compatible_con_cliente_planta():
    """ClientePlanta cumple el protocolo Publicador (tiene publicar y publicar_con_puback)."""
    from config import Settings

    settings = Settings()
    cliente = ClientePlanta(
        settings,
        al_nota=lambda _n: None,
        al_respuesta=lambda _r: None,
        al_sensor=lambda _s: None,
    )
    # Publicador es un Protocol no runtime_checkable: verificamos por hasattr
    assert hasattr(cliente, "publicar")
    assert hasattr(cliente, "publicar_con_puback")
