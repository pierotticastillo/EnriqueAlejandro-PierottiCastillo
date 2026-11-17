"""Checkpoint duradero y correlation ID de extremo a extremo.

Esta revisión añade la tabla ``checkpoint_ejecucion``, que permite reanudar
una tarea tras un reinicio sin repetir efectos, y extiende las tablas de
auditría con ``correlation_id`` para propagar un identificador canónico por
solicitud a través de logs, trayectorias, ledger, outbox y artefactos.

El checkpoint es un contrato de lectura/continuación sobre el estado ya
persistido (nota, parte, evento_sistema, orden, cierre, decisiones y
trayectoria). No se crea un segundo motor de workflow: una sola tabla nueva
se justifica porque un efecto no puede persistirse atómicamente con su
estado actual sin este registro de continuación.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-17
"""

from __future__ import annotations

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


DDL_UP: list[str] = [
    """
    CREATE TABLE checkpoint_ejecucion (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        correlation_id VARCHAR(255) NOT NULL,
        case_id VARCHAR(255) NOT NULL,
        tipo_tarea VARCHAR(64) NOT NULL,
        paso_actual VARCHAR(128) NOT NULL,
        estado VARCHAR(64) NOT NULL,
        propietario VARCHAR(128) NOT NULL,
        datos_confirmados JSONB NOT NULL DEFAULT '{}'::jsonb,
        acciones_hechas JSONB NOT NULL DEFAULT '[]'::jsonb,
        intentos INTEGER NOT NULL DEFAULT 0,
        ultimo_avance TIMESTAMPTZ NOT NULL DEFAULT now(),
        proxima_accion VARCHAR(255),
        version INTEGER NOT NULL DEFAULT 1,
        creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
        actualizada_en TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE UNIQUE INDEX ux_checkpoint_correlation
    ON checkpoint_ejecucion(correlation_id)
    """,
    """
    CREATE INDEX ix_checkpoint_case_id
    ON checkpoint_ejecucion(case_id)
    """,
    """
    CREATE INDEX ix_checkpoint_estado
    ON checkpoint_ejecucion(estado)
    WHERE estado IN ('en_curso', 'esperando_humano', 'pausado')
    """,
    # El correlation_id ya viaja en ledger_decisiones.datos y en
    # manifiesto_evidencia.datos. Se añade una columna explícita para que
    # cualquier consulta pueda localizar una solicitud sin descargar el JSON.
    """
    ALTER TABLE ledger_decisiones
    ADD COLUMN correlation_id VARCHAR(255)
    """,
    """
    CREATE INDEX ix_ledger_correlation_id
    ON ledger_decisiones(correlation_id)
    WHERE correlation_id IS NOT NULL
    """,
    """
    ALTER TABLE manifiesto_evidencia
    ADD COLUMN correlation_id VARCHAR(255)
    """,
    """
    CREATE INDEX ix_manifiesto_correlation_id
    ON manifiesto_evidencia(correlation_id)
    WHERE correlation_id IS NOT NULL
    """,
    # El outbox ya existe (0003). Se le añade correlation_id para que un
    # mensaje publicado pueda rastrearse hasta la solicitud que lo originó.
    """
    ALTER TABLE outbox_cola
    ADD COLUMN correlation_id VARCHAR(255)
    """,
    """
    CREATE INDEX ix_outbox_correlation_id
    ON outbox_cola(correlation_id)
    WHERE correlation_id IS NOT NULL
    """,
]


DDL_DOWN: list[str] = [
    "DROP INDEX IF EXISTS ix_outbox_correlation_id",
    "ALTER TABLE outbox_cola DROP COLUMN IF EXISTS correlation_id",
    "DROP INDEX IF EXISTS ix_manifiesto_correlation_id",
    "ALTER TABLE manifiesto_evidencia DROP COLUMN IF EXISTS correlation_id",
    "DROP INDEX IF EXISTS ix_ledger_correlation_id",
    "ALTER TABLE ledger_decisiones DROP COLUMN IF EXISTS correlation_id",
    "DROP INDEX IF EXISTS ix_checkpoint_estado",
    "DROP INDEX IF EXISTS ix_checkpoint_case_id",
    "DROP INDEX IF EXISTS ux_checkpoint_correlation",
    "DROP TABLE IF EXISTS checkpoint_ejecucion",
]


def upgrade() -> None:
    for sentencia in DDL_UP:
        op.execute(sentencia)


def downgrade() -> None:
    for sentencia in DDL_DOWN:
        op.execute(sentencia)
