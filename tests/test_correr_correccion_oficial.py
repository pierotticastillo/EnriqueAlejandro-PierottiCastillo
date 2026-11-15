"""Pruebas del ancla reproducible del envoltorio oficial."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from scripts.correr_correccion_oficial import _verificar_ancla
from sqlalchemy import create_engine, text

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL no definida",
)


def _git(carpeta: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(carpeta), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
    ).strip()


def _repo_temporal(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "pruebas@example.invalid")
    _git(repo, "config", "user.name", "Pruebas PIA-3")
    (repo / "solucion.txt").write_text("contenido\n", encoding="utf-8")
    _git(repo, "add", "solucion.txt")
    _git(repo, "commit", "-m", "ancla de prueba")
    return repo, _git(repo, "rev-parse", "HEAD")


def _version_alembic() -> str:
    motor = create_engine(str(TEST_DATABASE_URL))
    try:
        with motor.connect() as conexion:
            return str(
                conexion.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            )
    finally:
        motor.dispose()


def test_el_ancla_exige_el_commit_esperado_y_el_arbol_limpio(tmp_path) -> None:
    repo, commit = _repo_temporal(tmp_path)
    version = _version_alembic()

    verificacion = _verificar_ancla(
        repo,
        repo,
        str(TEST_DATABASE_URL),
        expected_commit=commit,
        expected_alembic=version,
        expected_correccion_commit=None,
        require_clean=False,
    )
    assert verificacion["commit"] == commit
    assert verificacion["arbol_limpio"] is True
    assert verificacion["alembic_version"] == version

    with pytest.raises(RuntimeError, match="no en"):
        _verificar_ancla(
            repo,
            repo,
            str(TEST_DATABASE_URL),
            expected_commit="0" * 40,
            expected_alembic=version,
            expected_correccion_commit=None,
            require_clean=False,
        )

    (repo / "solucion.txt").write_text("alterado\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="cambios sin confirmar"):
        _verificar_ancla(
            repo,
            repo,
            str(TEST_DATABASE_URL),
            expected_commit=commit,
            expected_alembic=version,
            expected_correccion_commit=None,
            require_clean=False,
        )


def test_el_ancla_comprueba_la_revision_de_alembic(tmp_path) -> None:
    repo, commit = _repo_temporal(tmp_path)
    _version_alembic()

    with pytest.raises(RuntimeError, match="Alembic"):
        _verificar_ancla(
            repo,
            repo,
            str(TEST_DATABASE_URL),
            expected_commit=commit,
            expected_alembic="revision-inexistente",
            expected_correccion_commit=None,
            require_clean=False,
        )
