"""Pruebas del archivador de evidencia de P0-06."""

from __future__ import annotations

import pytest
from scripts.archivar_p0_06 import resumen_pytest


def test_el_resumen_de_pytest_se_deriva_de_la_salida_real() -> None:
    resumen = resumen_pytest(
        "commit=abc\n"
        "....\n"
        "77 passed, 1 skipped in 11.54s\n"
    )

    assert resumen["linea"] == "77 passed, 1 skipped in 11.54s"
    assert resumen["conteos"] == {"passed": 77, "skipped": 1}


def test_el_resumen_de_pytest_rechaza_una_corrida_con_fallas() -> None:
    with pytest.raises(RuntimeError, match="Hay fallas"):
        resumen_pytest("1 failed, 76 passed in 3.20s\n")


def test_el_resumen_de_pytest_rechaza_una_salida_ilegible() -> None:
    with pytest.raises(RuntimeError, match="resumen de pytest"):
        resumen_pytest("la prueba terminó\n")
