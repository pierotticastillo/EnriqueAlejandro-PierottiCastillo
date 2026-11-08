"""La Fase 7 necesita guardar el cierre, la certificación y el habilita.

El esquema inicial (0001) ya tenía las tres tablas, pero les faltaba lo que
hace falta para que el cierre sea auditable y **idempotente**:

* ``cierre.externa_id``: el id de la nota de cierre que manda la planta. Con
  un índice único, la misma nota que llega dos veces no se guarda dos veces.
* ``cierre.empresa``, ``cierre.transcripcion`` y ``cierre.horas``: quién cerró
  (personal propio o contratista), lo que dijo y cuántas horas declaró.
* ``certificacion.cierre_id``: la certificación apunta al cierre que certificó,
  así que un cierre no se certifica dos veces.
* ``orden.ejecutor`` y ``orden.reduce_inyeccion``: la orden guarda quién la
  ejecuta y si baja la inyección. Antes se recalculaban al recuperar la
  orden, y con el cierre hace falta volver a publicarlos tal como se
  publicaron.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-08
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


DDL_UP: list[str] = [
    # El id externo de la nota de cierre: es lo que hace idempotente el
    # registro (la misma nota puede volver a llegar). Es una restricción
    # única y no un índice parcial: ``ON CONFLICT (externa_id)`` necesita una
    # restricción. La columna admite nulos y en PostgreSQL varios nulos no
    # chocan, así que los cierres viejos (sin id externo) no se pelean.
    "ALTER TABLE cierre ADD COLUMN IF NOT EXISTS externa_id TEXT",
    "ALTER TABLE cierre ADD COLUMN IF NOT EXISTS empresa TEXT",
    "ALTER TABLE cierre ADD COLUMN IF NOT EXISTS transcripcion TEXT",
    "ALTER TABLE cierre ADD COLUMN IF NOT EXISTS horas NUMERIC",
    "ALTER TABLE cierre DROP CONSTRAINT IF EXISTS ux_cierre_externa_id",
    "ALTER TABLE cierre ADD CONSTRAINT ux_cierre_externa_id UNIQUE (externa_id)",
    # La certificación queda atada al cierre que certificó.
    "ALTER TABLE certificacion ADD COLUMN IF NOT EXISTS cierre_id UUID",
    "CREATE INDEX IF NOT EXISTS ix_certificacion_cierre ON certificacion(cierre_id)",
    # La orden guarda quién la ejecuta y si baja la inyección.
    "ALTER TABLE orden ADD COLUMN IF NOT EXISTS ejecutor TEXT NOT NULL DEFAULT 'propio'",
    "ALTER TABLE orden ADD COLUMN IF NOT EXISTS reduce_inyeccion BOOLEAN NOT NULL DEFAULT false",
]


DDL_DOWN: list[str] = [
    "DROP INDEX IF EXISTS ix_certificacion_cierre",
    "ALTER TABLE certificacion DROP COLUMN IF EXISTS cierre_id",
    "ALTER TABLE cierre DROP CONSTRAINT IF EXISTS ux_cierre_externa_id",
    "ALTER TABLE cierre DROP COLUMN IF EXISTS horas",
    "ALTER TABLE cierre DROP COLUMN IF EXISTS transcripcion",
    "ALTER TABLE cierre DROP COLUMN IF EXISTS empresa",
    "ALTER TABLE cierre DROP COLUMN IF EXISTS externa_id",
    "ALTER TABLE orden DROP COLUMN IF EXISTS reduce_inyeccion",
    "ALTER TABLE orden DROP COLUMN IF EXISTS ejecutor",
]


def upgrade() -> None:
    for sentencia in DDL_UP:
        op.execute(sentencia)


def downgrade() -> None:
    for sentencia in DDL_DOWN:
        op.execute(sentencia)
