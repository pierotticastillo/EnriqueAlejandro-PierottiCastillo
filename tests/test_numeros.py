"""Lectura de números dichos en español de campo."""

from __future__ import annotations

import pytest

from planta.numeros import parsear_numero


@pytest.mark.parametrize(
    ("texto", "valor", "decimal"),
    [
        ("mil seiscientos treinta", 1630, False),
        ("mil seiscientos diez", 1610, False),
        ("ciento quince", 115, False),
        ("ciento catorce", 114, False),
        ("cincuenta y cinco", 55, False),
        ("treinta y un grados", 31, False),
        ("ciento once kilos", 111, False),
        ("mil seiscientos cuarenta", 1640, False),
        ("dos mil quinientos", 2500, False),
        ("uno coma tres", 1.3, True),
        ("uno con dos", 1.2, True),
        ("cero nueve", 0.9, True),
        ("uno ocho", 1.8, True),
        ("uno dos", 1.2, True),
        ("cinco y medio", 5.5, True),
        ("1,5", 1.5, True),
        ("1610", 1610, False),
        # El ASR escribe los millares con punto o coma: "1.600" es mil
        # seiscientos, no uno con seiscientos.
        ("1.600", 1600, False),
        ("1,600", 1600, False),
        ("1.610", 1610, False),
        # Un decimal de tres decimales con el cero adelante no son millares.
        ("0.900", 0.9, True),
        # El ASR abre un espacio después de la coma del decimal: "0, 4" es
        # cero coma cuatro, no cero.
        ("0, 4", 0.4, True),
        ("1, 1", 1.1, True),
        ("5, 2", 5.2, True),
        ("1. 600", 1600, False),
        # El ASR escribe la parte entera en dígitos y la fracción en
        # palabras: "0 con 3" es cero coma tres.
        ("0 con 3", 0.3, True),
        ("1 coma 2", 1.2, True),
        ("1 punto 5", 1.5, True),
    ],
)
def test_parsear_numero(texto: str, valor: float, decimal: bool) -> None:
    numero = parsear_numero(texto)
    assert numero is not None
    assert numero.valor == pytest.approx(valor)
    assert numero.decimal is decimal


def test_los_millares_no_se_confunden_con_un_decimal() -> None:
    """"1.600 de descarga" son mil seiscientos psi, no 1,6."""
    numero = parsear_numero("lados está en 1.600 de descarga")
    assert numero is not None
    assert numero.valor == pytest.approx(1600)
    assert numero.decimal is False


def test_sin_numero() -> None:
    assert parsear_numero("está parada, en reserva") is None


def test_decimal_con_guion() -> None:
    """El ASR escribe "uno coma tres" como "1-3"."""
    numero = parsear_numero("el filtro A medio 1-3 de diferencial")
    assert numero is not None
    assert numero.valor == pytest.approx(1.3)
    assert numero.decimal is True


def test_un_guion_entre_digitos_no_es_un_rango() -> None:
    """Un rango ("1600-1620") no es un decimal."""
    numero = parsear_numero("estaba entre 1600-1620 psi")
    assert numero is not None
    assert numero.valor == pytest.approx(1600)
    assert numero.decimal is False
