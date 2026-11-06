"""Unidades de la planta: manómetros, conversiones y unidad canónica."""

from __future__ import annotations

import pytest

from planta.unidades import convertir, normalizar_unidad, unidad_canonica, unidad_manometro


@pytest.mark.parametrize(
    ("dicho", "esperado"),
    [
        ("kilos", "kg/cm2"),
        ("kilogramos", "kg/cm2"),
        ("kg/cm²", "kg/cm2"),
        ("psi", "psi"),
        ("libras", "psi"),
        ("bar", "bar"),
        ("grados", "degC"),
        ("metros", "m"),
        ("gotas por minuto", "gotas/min"),
        ("mm/s", "mm/s"),
    ],
)
def test_normalizar_unidad(dicho: str, esperado: str) -> None:
    assert normalizar_unidad(dicho) == esperado


def test_unidad_del_manometro(planta: dict) -> None:
    assert unidad_manometro("B-101", planta) == "kg/cm2"
    assert unidad_manometro("B-102", planta) == "psi"
    assert unidad_manometro("B-103", planta) == "bar"


def test_unidad_canonica(planta: dict) -> None:
    assert unidad_canonica("presion_descarga_local", "B-102", planta) == "psi"
    assert unidad_canonica("presion_diferencial", "F-201A", planta) == "kg/cm2"
    assert unidad_canonica("nivel", "TK-301", planta) == "m"
    assert unidad_canonica("temperatura_rodamiento_ir", "B-101", planta) == "degC"


def test_conversion_de_presion() -> None:
    assert convertir(14.2233, "psi", "kg/cm2") == pytest.approx(1.0)
    assert convertir(1.0, "kg/cm2", "psi") == pytest.approx(14.2233)
    assert convertir(1.0, "bar", "kg/cm2") == pytest.approx(0.980665)
    assert convertir(5.0, "m", "m") == 5.0
