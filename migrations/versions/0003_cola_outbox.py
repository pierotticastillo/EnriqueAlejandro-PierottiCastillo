"""Cola de mensajes que esperan el radioenlace.

La cola (``outbox_cola``) necesita orden de salida (FIFO) y una clave de
idempotencia: el aviso se encola una sola vez, aunque se intente dos veces.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-05
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


DDL_UP: list[str] = [
    # Orden de salida: los mensajes de la cola salen en el orden en que se
    # encolaron (un aviso de radio no puede llegar después del siguiente).
    "ALTER TABLE outbox_cola ADD COLUMN IF NOT EXISTS creado_en TIMESTAMPTZ NOT NULL DEFAULT now()",
    # Idempotencia: el mismo mensaje no se encola dos veces.
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_outbox_tipo_mensaje "
    "ON outbox_cola(tipo, ((payload ->> 'id'))) WHERE payload ->> 'id' IS NOT NULL",
]


DDL_DOWN: list[str] = [
    "DROP INDEX IF EXISTS ix_outbox_tipo_mensaje",
    "ALTER TABLE outbox_cola DROP COLUMN IF EXISTS creado_en",
]


def upgrade() -> None:
    for sentencia in DDL_UP:
        op.execute(sentencia)


def downgrade() -> None:
    for sentencia in DDL_DOWN:
        op.execute(sentencia)
