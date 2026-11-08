"""La cola de salida sabe a qué tópico va cada mensaje.

Hasta acá la cola (``outbox_cola``) sólo guardaba avisos por radio, que van
siempre al tópico de la tablet. Con la Fase 6 la cola es **la salida de la
planta**: también se encolan eventos y órdenes, y cada mensaje tiene que saber
a dónde va cuando sale.

Por eso la fila guarda el ``topico``. Además, el registro de idempotencia
(``idempotency_key``) deja escrito qué mensaje se encoló y en qué fila: es lo
que garantiza que un evento o una orden no se encolen dos veces.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-08
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


DDL_UP: list[str] = [
    # Destino del mensaje: sin esto, la cola no sabe por dónde publicarlo.
    # El valor por defecto es el tópico de la tablet, que es donde van los
    # avisos por radio (el único tipo que estaba en la cola hasta acá).
    "ALTER TABLE outbox_cola ADD COLUMN IF NOT EXISTS topico TEXT NOT NULL "
    "DEFAULT 'sistema/tablet'",
    # La idempotencia de la cola es "no encolar dos veces **mientras está
    # pendiente**". Con el índice anterior (que no miraba el estado), un
    # mensaje que ya había salido no podía volver a encolarse: si el bus se
    # caía otra vez, ese mensaje se perdía. Con el índice parcial, cada
    # mensaje vive una sola vez en la cola a la vez.
    "DROP INDEX IF EXISTS ix_outbox_tipo_mensaje",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_outbox_tipo_mensaje "
    "ON outbox_cola(tipo, ((payload ->> 'id'))) "
    "WHERE estado = 'pendiente' AND payload ->> 'id' IS NOT NULL",
]


DDL_DOWN: list[str] = [
    "DROP INDEX IF EXISTS ix_outbox_tipo_mensaje",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_outbox_tipo_mensaje "
    "ON outbox_cola(tipo, ((payload ->> 'id'))) WHERE payload ->> 'id' IS NOT NULL",
    "ALTER TABLE outbox_cola DROP COLUMN IF EXISTS topico",
]


def upgrade() -> None:
    for sentencia in DDL_UP:
        op.execute(sentencia)


def downgrade() -> None:
    for sentencia in DDL_DOWN:
        op.execute(sentencia)
