"""Pruebas del checkpoint duradero y reanudación (P1-15 / P1-16).

Verifica que:
- Se puede guardar y cargar un checkpoint para reanudar tras un reinicio.
- No se duplican efectos al reanudar (idempotencia por clave_idempotencia).
- Un checkpoint con versión obsoleta no puede ser sobrescrito (concurrencia).
- El correlation_id agrupa los casos de una misma solicitud.
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from sqlalchemy import create_engine

from db.checkpoint import (
    ESTADO_EN_CURSO,
    ESTADO_TERMINAL,
    TAREA_ANALISIS,
    CheckpointNoEncontrado,
    CheckpointObsoleto,
    RepositorioCheckpoint,
    crear_correlation_id,
)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL no definida",
)


@pytest.fixture
def engine() -> Iterator[create_engine]:
    """Crea un engine apuntando a una base de datos temporal de PostgreSQL."""
    url = create_engine(str(TEST_DATABASE_URL)).url
    nombre = f"pia3_ckpt_{uuid.uuid4().hex[:12]}"
    admin = create_engine(
        url.set(database="postgres").render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with admin.connect() as conexion:
            conexion.exec_driver_sql(f'CREATE DATABASE "{nombre}"')
        yield create_engine(url.set(database=nombre).render_as_string(hide_password=False))
    finally:
        with admin.connect() as conexion:
            conexion.exec_driver_sql(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)')
        admin.dispose()


@pytest.fixture
def repositorio(engine: create_engine) -> RepositorioCheckpoint:
    """Crea la tabla de checkpoint y devuelve un repositorio listo."""
    with engine.begin() as conexion:
        conexion.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS checkpoint_ejecucion (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                correlation_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                tipo_tarea TEXT NOT NULL,
                paso_actual TEXT NOT NULL,
                estado TEXT NOT NULL,
                propietario TEXT NOT NULL,
                datos_confirmados JSONB NOT NULL DEFAULT '{}',
                acciones_hechas JSONB NOT NULL DEFAULT '[]',
                intentos INT NOT NULL DEFAULT 0,
                proxima_accion TEXT,
                version INT NOT NULL DEFAULT 1,
                creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
                actualizada_en TIMESTAMPTZ NOT NULL DEFAULT now(),
                ultimo_avance TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conexion.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_checkpoint_correlation "
            "ON checkpoint_ejecucion (correlation_id)"
        )
    return RepositorioCheckpoint(engine=engine)


def _guardar_checkpoint(
    repo: RepositorioCheckpoint,
    conexion,
    *,
    correlation_id: str,
    case_id: str,
    paso: str,
    estado: str,
    propietario: str,
    version: int = 1,
    proxima_accion: str | None = None,
    datos: dict | None = None,
    acciones: list | None = None,
):
    return repo.guardar(
        conexion,
        correlation_id=correlation_id,
        case_id=case_id,
        tipo_tarea=TAREA_ANALISIS,
        paso_actual=paso,
        estado=estado,
        propietario=propietario,
        datos_confirmados=datos or {},
        acciones_hechas=acciones or [],
        intentos=0,
        proxima_accion=proxima_accion,
        version=version,
    )


def test_guardar_y_cargar_checkpoint(repositorio: RepositorioCheckpoint, engine: create_engine):
    """Debe poder guardarse un checkpoint y recuperarse tras 'reinicio'."""
    correlation_id = crear_correlation_id("pia3")
    case_id = f"case-{uuid.uuid4().hex[:8]}"

    with engine.begin() as conexion:
        cp = _guardar_checkpoint(
            repositorio,
            conexion,
            correlation_id=correlation_id,
            case_id=case_id,
            paso="analisis_inicial",
            estado=ESTADO_EN_CURSO,
            propietario="agente",
            proxima_accion="consultar_manuales",
            datos={"variable": "presion_descarga", "valor": 85.0},
        )
        assert cp is not None
        assert cp.correlation_id == correlation_id
        assert cp.puede_seguir is True

    # Simular reinicio: nueva conexión, cargar desde la base
    with engine.begin() as conexion:
        cargado = repositorio.cargar_ultimo(
            conexion, correlation_id=correlation_id, case_id=case_id
        )

    assert cargado is not None
    assert cargado.correlation_id == correlation_id
    assert cargado.case_id == case_id
    assert cargado.paso_actual == "analisis_inicial"
    assert cargado.estado == ESTADO_EN_CURSO
    assert cargado.propietario == "agente"
    assert cargado.datos_confirmados == {"variable": "presion_descarga", "valor": 85.0}
    assert cargado.proxima_accion == "consultar_manuales"
    assert cargado.version == 1


def test_no_duplica_efectos_al_reanudar(repositorio: RepositorioCheckpoint, engine: create_engine):
    """Los efectos ya consumados no se deben repetir al reanudar."""
    correlation_id = crear_correlation_id("pia3")
    case_id = f"case-{uuid.uuid4().hex[:8]}"
    clave_idempotencia = "outbox:orden:ORD-001"
    efecto = {"orden_id": "ORD-001", "estado": "Aprobada"}

    with engine.begin() as conexion:
        cp = _guardar_checkpoint(
            repositorio,
            conexion,
            correlation_id=correlation_id,
            case_id=case_id,
            paso="publicar_orden",
            estado=ESTADO_EN_CURSO,
            propietario="sistema",
            proxima_accion="publicar_en_bus",
            acciones=[{"clave_idempotencia": clave_idempotencia, "efecto": efecto}],
        )
        assert cp is not None
        assert clave_idempotencia in cp.efecto_ya_hecho

    # Reanudar: el efecto ya está registrado, no se duplica
    with engine.begin() as conexion:
        cargado = repositorio.cargar_ultimo(
            conexion, correlation_id=correlation_id, case_id=case_id
        )
        assert cargado is not None
        assert clave_idempotencia in cargado.efecto_ya_hecho
        assert len(cargado.acciones_hechas) == 1


def test_version_obsoleta_falla(repositorio: RepositorioCheckpoint, engine: create_engine):
    """Un intento de guardar con versión obsoleta debe lanzar CheckpointObsoleto."""
    correlation_id = crear_correlation_id("pia3")
    case_id = f"case-{uuid.uuid4().hex[:8]}"

    with engine.begin() as conexion:
        _guardar_checkpoint(
            repositorio,
            conexion,
            correlation_id=correlation_id,
            case_id=case_id,
            paso="paso_1",
            estado=ESTADO_EN_CURSO,
            propietario="agente",
            version=1,
        )
        # Otro componente avanza a versión 2
        cp2 = repositorio.avanzar(
            conexion,
            correlation_id=correlation_id,
            paso_actual="paso_2",
            estado=ESTADO_EN_CURSO,
            propietario="agente",
        )
        assert cp2.version == 2

        # Este componente aún tiene versión 1: debe fallar
        with pytest.raises(CheckpointObsoleto):
            _guardar_checkpoint(
                repositorio,
                conexion,
                correlation_id=correlation_id,
                case_id=case_id,
                paso="paso_1",
                estado=ESTADO_EN_CURSO,
                propietario="agente",
                version=1,
            )


def test_checkpoint_no_encontrado(repositorio: RepositorioCheckpoint, engine: create_engine):
    """Cargar un checkpoint inexistente devuelve None; avanzar lanza CheckpointNoEncontrado."""
    correlation_id = crear_correlation_id("pia3")

    with engine.begin() as conexion:
        cargado = repositorio.cargar_ultimo(
            conexion, correlation_id=correlation_id
        )
        assert cargado is None

        with pytest.raises(CheckpointNoEncontrado):
            repositorio.avanzar(
                conexion,
                correlation_id=correlation_id,
                paso_actual="cualquiera",
                estado=ESTADO_TERMINAL,
                propietario="sistema",
            )


def test_correlation_id_agrupa_casos(repositorio: RepositorioCheckpoint, engine: create_engine):
    """Cada case_id tiene su propio correlation_id; no se pueden duplicar."""
    case_a = f"case-a-{uuid.uuid4().hex[:8]}"
    case_b = f"case-b-{uuid.uuid4().hex[:8]}"
    cid_a = crear_correlation_id("pia3")
    cid_b = crear_correlation_id("pia3")

    with engine.begin() as conexion:
        cp_a = _guardar_checkpoint(
            repositorio,
            conexion,
            correlation_id=cid_a,
            case_id=case_a,
            paso="parte",
            estado=ESTADO_EN_CURSO,
            propietario="agente",
        )
        cp_b = _guardar_checkpoint(
            repositorio,
            conexion,
            correlation_id=cid_b,
            case_id=case_b,
            paso="orden",
            estado=ESTADO_EN_CURSO,
            propietario="sistema",
        )
        assert cp_a is not None
        assert cp_b is not None
        assert cp_a.correlation_id != cp_b.correlation_id

    # Cada correlation_id es independiente
    with engine.begin() as conexion:
        ca = repositorio.cargar_ultimo(
            conexion, correlation_id=cid_a, case_id=case_a
        )
        cb = repositorio.cargar_ultimo(
            conexion, correlation_id=cid_b, case_id=case_b
        )
        assert ca is not None and ca.case_id == case_a
        assert cb is not None and cb.case_id == case_b
        assert ca.paso_actual == "parte"
        assert cb.paso_actual == "orden"
        assert ca.correlation_id == cid_a
        assert cb.correlation_id == cid_b


def test_crear_correlation_id_estable(prefijo: str = "pia3"):
    """El correlation_id generado es estable y con el prefijo esperado."""
    cid = crear_correlation_id(prefijo)
    assert cid.startswith(f"{prefijo}:")
    assert len(cid) > len(prefijo) + 1
