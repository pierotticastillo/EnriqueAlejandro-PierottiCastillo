"""Identidad externa de notas y estado de la conversación de tablet.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-04
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


DDL_UP: list[str] = [
    # La nota de la planta viene con un id textual (NOTA-00014); la PK sigue
    # siendo UUID y el id externo es la clave idempotente de ingesta.
    "ALTER TABLE nota ADD COLUMN IF NOT EXISTS externa_id TEXT",
    "ALTER TABLE nota ADD COLUMN IF NOT EXISTS operador TEXT",
    "ALTER TABLE nota ADD COLUMN IF NOT EXISTS recorrida_id TEXT",
    "ALTER TABLE nota ADD COLUMN IF NOT EXISTS seguridad TEXT",
    "ALTER TABLE nota ADD COLUMN IF NOT EXISTS actualizada_en TIMESTAMPTZ NOT NULL DEFAULT now()",
    "ALTER TABLE nota ADD CONSTRAINT uq_nota_externa_id UNIQUE (externa_id)",
    # Una nota genera un solo parte; el borrador se actualiza en el mismo registro.
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_parte_nota_id ON parte(nota_id)",
    "ALTER TABLE parte ADD COLUMN IF NOT EXISTS mensaje_id TEXT",
    "ALTER TABLE parte ADD COLUMN IF NOT EXISTS intento INT NOT NULL DEFAULT 0",
    "ALTER TABLE conversacion_nota ADD COLUMN IF NOT EXISTS mensaje_id TEXT",
    "ALTER TABLE conversacion_nota ADD COLUMN IF NOT EXISTS pregunta_id TEXT",
    "ALTER TABLE conversacion_nota ADD COLUMN IF NOT EXISTS datos JSONB",
    "ALTER TABLE pregunta_tablet ADD COLUMN IF NOT EXISTS mensaje_id TEXT",
    "ALTER TABLE pregunta_tablet ADD COLUMN IF NOT EXISTS tags JSONB",
    "ALTER TABLE pregunta_tablet ADD COLUMN IF NOT EXISTS variable TEXT",
    "ALTER TABLE pregunta_tablet ADD COLUMN IF NOT EXISTS valor_dicho NUMERIC",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_pregunta_tablet_mensaje_id "
    "ON pregunta_tablet(mensaje_id) WHERE mensaje_id IS NOT NULL",
    "ALTER TABLE foto ADD COLUMN IF NOT EXISTS indice INT",
    # El contrato permite filas de observación sin número (estado, goteo, ruido).
    "ALTER TABLE fila_parte ALTER COLUMN valor DROP NOT NULL",
    "ALTER TABLE fila_parte ALTER COLUMN unidad DROP NOT NULL",
]


DDL_DOWN: list[str] = [
    "ALTER TABLE foto DROP COLUMN IF EXISTS indice",
    "DROP INDEX IF EXISTS ix_pregunta_tablet_mensaje_id",
    "ALTER TABLE pregunta_tablet DROP COLUMN IF EXISTS valor_dicho",
    "ALTER TABLE pregunta_tablet DROP COLUMN IF EXISTS variable",
    "ALTER TABLE pregunta_tablet DROP COLUMN IF EXISTS tags",
    "ALTER TABLE pregunta_tablet DROP COLUMN IF EXISTS mensaje_id",
    "ALTER TABLE conversacion_nota DROP COLUMN IF EXISTS datos",
    "ALTER TABLE conversacion_nota DROP COLUMN IF EXISTS pregunta_id",
    "ALTER TABLE conversacion_nota DROP COLUMN IF EXISTS mensaje_id",
    "ALTER TABLE parte DROP COLUMN IF EXISTS intento",
    "ALTER TABLE parte DROP COLUMN IF EXISTS mensaje_id",
    "DROP INDEX IF EXISTS ix_parte_nota_id",
    "ALTER TABLE nota DROP CONSTRAINT IF EXISTS uq_nota_externa_id",
    "ALTER TABLE nota DROP COLUMN IF EXISTS actualizada_en",
    "ALTER TABLE nota DROP COLUMN IF EXISTS seguridad",
    "ALTER TABLE nota DROP COLUMN IF EXISTS recorrida_id",
    "ALTER TABLE nota DROP COLUMN IF EXISTS operador",
    "ALTER TABLE nota DROP COLUMN IF EXISTS externa_id",
]


def upgrade() -> None:
    for sentencia in DDL_UP:
        op.execute(sentencia)


def downgrade() -> None:
    for sentencia in DDL_DOWN:
        op.execute(sentencia)
