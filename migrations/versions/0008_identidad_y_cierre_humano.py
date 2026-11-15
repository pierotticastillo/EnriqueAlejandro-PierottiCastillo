"""Identidad autenticada y decisión humana de G2/G3.

La segunda entrega separa el procesamiento técnico del cierre de la decisión
final:

* ``aprobacion.rol`` y ``certificacion.rol`` conservan el rol real;
* ``decision_cierre`` es el registro append-only de la decisión humana sobre
  un cierre, con actor, rol, motivo, evidencia y fecha;
* una orden queda ``Ejecutada`` mientras espera al supervisor o al inspector;
* el habilita se emite únicamente dentro de la certificación humana.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-14
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


DDL_UP: list[str] = [
    # Las filas automáticas de reglas no tienen actor: el rol queda nullable.
    "ALTER TABLE aprobacion ADD COLUMN IF NOT EXISTS rol TEXT",
    "ALTER TABLE certificacion ADD COLUMN IF NOT EXISTS rol TEXT",
    """
    CREATE TABLE IF NOT EXISTS decision_cierre (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        cierre_id UUID NOT NULL REFERENCES cierre(id) ON DELETE CASCADE,
        orden_id TEXT NOT NULL REFERENCES orden(id) ON DELETE CASCADE,
        decision TEXT NOT NULL CHECK (decision IN ('cerrar', 'certificar', 'observar')),
        estado_orden TEXT NOT NULL CHECK (
            estado_orden IN ('Cerrada', 'Certificada', 'Observada')
        ),
        actor TEXT NOT NULL CHECK (length(btrim(actor)) > 0),
        rol TEXT NOT NULL CHECK (rol IN ('supervisor', 'inspector')),
        motivo TEXT,
        evidencia JSONB NOT NULL,
        creada_en TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ux_decision_cierre_cierre UNIQUE (cierre_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_decision_cierre_orden ON decision_cierre(orden_id)",
    "CREATE INDEX IF NOT EXISTS ix_decision_cierre_rol ON decision_cierre(rol, creada_en)",
]


DDL_DOWN: list[str] = [
    "DROP INDEX IF EXISTS ix_decision_cierre_rol",
    "DROP INDEX IF EXISTS ix_decision_cierre_orden",
    "DROP TABLE IF EXISTS decision_cierre",
    "ALTER TABLE certificacion DROP COLUMN IF EXISTS rol",
    "ALTER TABLE aprobacion DROP COLUMN IF EXISTS rol",
]


def upgrade() -> None:
    for sentencia in DDL_UP:
        op.execute(sentencia)


def downgrade() -> None:
    for sentencia in DDL_DOWN:
        op.execute(sentencia)
