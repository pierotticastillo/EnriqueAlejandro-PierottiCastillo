"""Registro de la trayectoria: modelo, herramientas, tokens, costo y tiempo.

El enunciado pide, en la sección F, que *"cada decisión quede registrada con
el modelo usado, las herramientas llamadas y sus argumentos, los tokens, el
costo y el tiempo de cada paso"*. Esta tabla es ese registro.

Una fila por paso: una llamada al modelo, una llamada a una herramienta o una
decisión de la planta.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-10
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


DDL_UP: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS registro_trayectoria (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        ts TIMESTAMPTZ NOT NULL DEFAULT now(),
        etapa TEXT NOT NULL,
        tipo TEXT NOT NULL,
        modelo TEXT,
        herramienta TEXT,
        argumentos JSONB,
        salida JSONB,
        tokens_prompt INTEGER NOT NULL DEFAULT 0,
        tokens_completion INTEGER NOT NULL DEFAULT 0,
        costo_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
        segundos NUMERIC(12, 3) NOT NULL DEFAULT 0,
        error TEXT,
        orden_id TEXT,
        evento_id TEXT,
        equipo TEXT,
        paso INTEGER
    )
    """,
    # La trayectoria de una orden se consulta por orden y en orden.
    "CREATE INDEX IF NOT EXISTS ix_registro_trayectoria_orden "
    "ON registro_trayectoria(orden_id, ts)",
    # Y por etapa, para ver el gasto de un tipo de paso.
    "CREATE INDEX IF NOT EXISTS ix_registro_trayectoria_etapa "
    "ON registro_trayectoria(etapa, ts)",
]


DDL_DOWN: list[str] = [
    "DROP INDEX IF EXISTS ix_registro_trayectoria_etapa",
    "DROP INDEX IF EXISTS ix_registro_trayectoria_orden",
    "DROP TABLE IF EXISTS registro_trayectoria",
]


def upgrade() -> None:
    for sentencia in DDL_UP:
        op.execute(sentencia)


def downgrade() -> None:
    for sentencia in DDL_DOWN:
        op.execute(sentencia)
