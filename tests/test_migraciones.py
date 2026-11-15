"""Pruebas de las migraciones y de sus invariantes críticas.

Cada prueba usa una base PostgreSQL temporal. La base de integración no se
degrada ni se modifica para comprobar un ciclo de Alembic.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL no definida",
)

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture
def base_temporal() -> Iterator[str]:
    url = make_url(str(TEST_DATABASE_URL))
    nombre = f"pia3_mig_{uuid.uuid4().hex[:12]}"
    admin = create_engine(
        url.set(database="postgres").render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with admin.connect() as conexion:
            conexion.exec_driver_sql(f'CREATE DATABASE "{nombre}"')
        yield url.set(database=nombre).render_as_string(hide_password=False)
    finally:
        with admin.connect() as conexion:
            conexion.exec_driver_sql(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)')
        admin.dispose()


def _config(monkeypatch, database_url: str) -> Config:
    monkeypatch.setenv("DATABASE_URL", database_url)
    return Config(str(RAIZ / "alembic.ini"))


def _version(database_url: str) -> str:
    motor = create_engine(database_url)
    try:
        with motor.connect() as conexion:
            return str(
                conexion.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            )
    finally:
        motor.dispose()


def test_instalacion_limpia_y_ciclo_de_migraciones(base_temporal, monkeypatch) -> None:
    config = _config(monkeypatch, base_temporal)

    command.upgrade(config, "0010")
    assert _version(base_temporal) == "0010"
    command.upgrade(config, "head")
    assert _version(base_temporal) == "0011"

    command.downgrade(config, "0010")
    assert _version(base_temporal) == "0010"
    command.upgrade(config, "head")
    assert _version(base_temporal) == "0011"

    motor = create_engine(base_temporal)
    try:
        with motor.connect() as conexion:
            disparadores = conexion.execute(
                text(
                    """
                    SELECT count(*)
                    FROM pg_trigger
                    WHERE NOT tgisinternal
                      AND tgname IN (
                          'trg_habilitacion_append_only',
                          'trg_habilitacion_append_only_truncate',
                          'trg_orden_validar_transicion_terminal_0011'
                      )
                    """
                )
            ).scalar_one()
            constraints = conexion.execute(
                text(
                    """
                    SELECT count(*)
                    FROM pg_constraint
                    WHERE conname IN (
                        'ck_habilitacion_monto_positivo',
                        'ck_habilitacion_moneda_usd'
                    )
                    """
                )
            ).scalar_one()
            indice = conexion.execute(
                text(
                    "SELECT count(*) FROM pg_indexes "
                    "WHERE indexname = 'ux_cierre_aclaracion_observada'"
                )
            ).scalar_one()
    finally:
        motor.dispose()

    assert disparadores == 3
    assert constraints == 2
    assert indice == 1


def test_el_backfill_ambiguo_hace_fallar_la_migracion(base_temporal, monkeypatch) -> None:
    config = _config(monkeypatch, base_temporal)
    command.upgrade(config, "0009")

    motor = create_engine(base_temporal)
    try:
        with motor.begin() as conexion:
            evento_id = f"ev-ambiguo-{uuid.uuid4().hex[:8]}"
            orden_id = f"ORD-AMBIGUA-{uuid.uuid4().hex[:8]}"
            conexion.execute(
                text(
                    """
                    INSERT INTO evento_sistema (id, origen, estado, payload)
                    VALUES (:evento, 'parte', 'Escalado', '{}'::jsonb)
                    """
                ),
                {"evento": evento_id},
            )
            conexion.execute(
                text(
                    """
                    INSERT INTO orden (id, evento_id, equipo, estado)
                    VALUES (:orden, :evento, 'B-101', 'Aprobada')
                    """
                ),
                {"orden": orden_id, "evento": evento_id},
            )
            cierres: list[str] = []
            for indice, desplazamiento in enumerate((3, 2, 1), start=1):
                externa_id = f"CIERRE-AMBIGUA-{indice}-{uuid.uuid4().hex[:8]}"
                cierre_id = conexion.execute(
                    text(
                        """
                        INSERT INTO cierre (
                            externa_id, orden_id, tecnico, empresa, transcripcion,
                            tareas_hechas, repuestos_usados, horas, prueba_final,
                            diferencias, creado_en
                        )
                        VALUES (
                            :externa, :orden, 'técnico', 'Servipat', 'cierre',
                            '[]'::jsonb, '[]'::jsonb, 1.0, 'prueba', '[]'::jsonb,
                            now() - make_interval(mins => :minutos)
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "externa": externa_id,
                        "orden": orden_id,
                        "minutos": desplazamiento,
                    },
                ).scalar_one()
                cierres.append(str(cierre_id))

            for cierre_id in cierres[:2]:
                conexion.execute(
                    text(
                        """
                        INSERT INTO decision_cierre (
                            cierre_id, orden_id, decision, estado_orden,
                            actor, rol, motivo, evidencia
                        )
                        VALUES (
                            :cierre, :orden, 'observar', 'Observada',
                            'marta', 'inspector', 'falta justification', '{}'::jsonb
                        )
                        """
                    ),
                    {"cierre": cierre_id, "orden": orden_id},
                )
            conexion.execute(
                text(
                    """
                    INSERT INTO decision_cierre (
                        cierre_id, orden_id, decision, estado_orden,
                        actor, rol, motivo, evidencia
                    )
                    VALUES (
                        :cierre, :orden, 'certificar', 'Certificada',
                        'marta', 'inspector', NULL, '{}'::jsonb
                    )
                    """
                ),
                {"cierre": cierres[2], "orden": orden_id},
            )
    finally:
        motor.dispose()

    command.upgrade(config, "0010")
    assert _version(base_temporal) == "0010"

    with pytest.raises(DBAPIError, match="backfill de aclaraciones es ambiguo"):
        command.upgrade(config, "0011")

    assert _version(base_temporal) == "0010"
