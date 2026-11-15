"""Pruebas del archivador de evidencia de P0-06."""

from __future__ import annotations

import hashlib

import pytest
from scripts.archivar_p0_06 import (
    bytes_canonicos,
    escribir_utf8_lf,
    resumen_pytest,
    sha256_canonico,
)


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


def test_los_artefactos_se_normalizan_a_utf8_sin_bom_y_lf(tmp_path) -> None:
    origen = tmp_path / "salida.txt"
    origen.write_bytes(b"\xef\xbb\xbfprimera\r\nsegunda\r")

    canonico = bytes_canonicos(origen)

    assert canonico == b"primera\nsegunda\n"
    assert sha256_canonico(origen) == hashlib.sha256(canonico).hexdigest()


def test_los_artefactos_utf16_se_convierten_a_utf8_lf(tmp_path) -> None:
    origen = tmp_path / "powershell.txt"
    origen.write_bytes("primera\r\nsegunda\r\n".encode("utf-16"))

    assert bytes_canonicos(origen) == b"primera\nsegunda\n"


def test_la_escritura_del_archivador_usa_lf(tmp_path) -> None:
    destino = tmp_path / "manifiesto.json"

    escribir_utf8_lf(destino, '{"uno": 1}\n')

    assert destino.read_bytes() == b'{"uno": 1}\n'
