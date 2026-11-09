"""Normalización del texto que devuelve el ASR."""

from __future__ import annotations

import pytest

from planta.texto import normalizar


@pytest.mark.parametrize(
    ("dicho", "esperado"),
    [
        # El símbolo del grado se pierde al normalizar ("59 °C" -> "59 c"),
        # así que se cambia por la palabra que el técnico dice.
        ("El rodamiento me dio 59 °C", "el rodamiento me dio 59 grados"),
        ("El rodamiento me dio 59 ºC", "el rodamiento me dio 59 grados"),
        ("El rodamiento me dio 59°", "el rodamiento me dio 59 grados"),
        # El "º" suelto es el ordinal, no los grados: "el 2º filtro".
        ("el 2º filtro", "el 2o filtro"),
        # Las tildes y las mayúsculas no estorban.
        ("El FILTRO A me dio 1,3", "el filtro a me dio 1,3"),
    ],
)
def test_normalizar(dicho: str, esperado: str) -> None:
    assert normalizar(dicho) == esperado


def test_el_asr_que_reserva_no_es_un_ordinal() -> None:
    """El ASR escribe "reservoir" cuando oye "reserva"."""
    assert normalizar("la 3 está parada, en reservoir") == "la 3 esta parada, en reserva"
