"""Integridad financiera y terminales defendidos en PostgreSQL.

La revisión ``0010`` protege el contenido decidido y modela el ciclo de
aclaración, pero todavía deja tres bordes que la aplicación no debe decidir:

* una orden puede entrar por SQL directamente en un estado terminal sin la
  decisión, certificación o habilita correspondientes;
* ``habilitacion`` conserva el monto, pero puede alterarse o eliminarse;
* el backfill de aclaraciones no comprueba que un candidato sea único.

Esta revisión:

* permite que el cierre técnico escriba su motivo en la transición legítima
  ``Aprobada -> Ejecutada`` después de una aprobación humana, sin permitir
  cambios del resto del contenido;
* exige decisión, certificación y habilita válidos para todo estado terminal;
* vuelve ``habilitacion`` append-only y exige un monto USD positivo y finito;
* hace determinista y fail-closed el backfill de aclaraciones y garantiza una
  sola aclaración por observación.

El ``downgrade`` no borra trazabilidad: retira las defensas de ``0011`` y
restaura el comportamiento más permisivo de ``0010``. No debe usarse para
cambiar el significado de un estado terminal ya persistido.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-15
"""

from __future__ import annotations

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


#: Pares históricos que 0010 usa para reconocer una aclaración. La relación
#: sólo es válida si hay exactamente una observación y una aclaración por
#: orden; la migración aborta ante cualquier otra cardinalidad.
_PARES_ACLARACION = """
WITH pares AS (
    SELECT
        aclaracion.id AS aclaracion_id,
        observada.id AS observada_id
    FROM cierre aclaracion
    JOIN cierre observada
      ON observada.orden_id = aclaracion.orden_id
     AND observada.creado_en < aclaracion.creado_en
    JOIN decision_cierre d_aclaracion
      ON d_aclaracion.cierre_id = aclaracion.id
     AND d_aclaracion.decision IN ('certificar', 'cerrar')
    JOIN decision_cierre d_observada
      ON d_observada.cierre_id = observada.id
     AND d_observada.decision = 'observar'
)
"""


DDL_UP: list[str] = [
    # No se reparan datos históricos automáticamente. Si no se puede demostrar
    # que el monto y la trazabilidad son válidos, la migración falla antes de
    # cambiar el esquema.
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM habilitacion
            WHERE monto <= 0
               OR monto = 'NaN'::numeric
               OR monto = 'Infinity'::numeric
               OR monto = '-Infinity'::numeric
        ) THEN
            RAISE EXCEPTION
                'habilitacion contiene un monto no positivo, NaN o infinito';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM habilitacion
            WHERE moneda <> 'USD'
        ) THEN
            RAISE EXCEPTION
                'habilitacion contiene una moneda distinta de USD';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM orden o
            WHERE o.estado IN ('Cerrada', 'Certificada', 'Observada')
              AND NOT EXISTS (
                  SELECT 1
                  FROM decision_cierre d
                  JOIN cierre c
                    ON c.id = d.cierre_id
                   AND c.orden_id = d.orden_id
                  WHERE d.orden_id = o.id
                    AND d.estado_orden = o.estado::text
                    AND c.id = (
                        SELECT c2.id
                        FROM cierre c2
                        WHERE c2.orden_id = o.id
                        ORDER BY c2.creado_en DESC, c2.id DESC
                        LIMIT 1
                    )
              )
        ) THEN
            RAISE EXCEPTION
                'Hay órdenes terminales sin una decisión humana válida';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM orden o
            WHERE o.estado = 'Observada'
              AND NOT EXISTS (
                  SELECT 1
                  FROM certificacion c
                  JOIN decision_cierre d ON d.cierre_id = c.cierre_id
                  WHERE c.orden_id = o.id
                    AND c.decision = 'observa'
                    AND d.decision = 'observar'
              )
        ) THEN
            RAISE EXCEPTION
                'Hay órdenes observadas sin una certificación válida';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM orden o
            WHERE o.estado = 'Certificada'
              AND NOT EXISTS (
                  SELECT 1
                  FROM decision_cierre d
                  JOIN certificacion c
                    ON c.cierre_id = d.cierre_id
                   AND c.orden_id = d.orden_id
                  JOIN habilitacion h
                    ON h.decision_cierre_id = d.id
                   AND h.certificacion_id = c.id
                   AND h.orden_id = o.id
                  WHERE d.orden_id = o.id
                    AND d.decision = 'certificar'
                    AND d.estado_orden = 'Certificada'
                    AND c.decision = 'certifica'
                    AND h.monto > 0
                    AND h.moneda = 'USD'
              )
        ) THEN
            RAISE EXCEPTION
                'Hay órdenes certificadas sin certificación o habilita válido';
        END IF;
    END
    $$
    """,
    # La relación de aclaración debe ser unívoca en ambos sentidos. Cero o
    # más de un candidato significa que el histórico no permite decidir sin
    # inventar provenance.
    f"""
    DO $$
    BEGIN
        IF EXISTS (
            {_PARES_ACLARACION}
            SELECT 1
            FROM (
                SELECT aclaracion_id
                FROM pares
                GROUP BY aclaracion_id
                HAVING count(*) <> 1
                UNION ALL
                SELECT observada_id
                FROM pares
                GROUP BY observada_id
                HAVING count(*) <> 1
            ) ambiguos
            LIMIT 1
        ) THEN
            RAISE EXCEPTION
                'El backfill de aclaraciones es ambiguo: cada relación debe ser unívoca';
        END IF;
    END
    $$
    """,
    # Una relación ya presente que no sea el único candidato también aborta.
    f"""
    DO $$
    BEGIN
        IF EXISTS (
            {_PARES_ACLARACION}
            SELECT 1
            FROM pares
            JOIN cierre aclaracion ON aclaracion.id = pares.aclaracion_id
            WHERE aclaracion.aclara_cierre_id IS NOT NULL
              AND aclaracion.aclara_cierre_id IS DISTINCT FROM pares.observada_id
            LIMIT 1
        ) THEN
            RAISE EXCEPTION
                'Una aclaración ya persistida no coincide con su único candidato';
        END IF;
    END
    $$
    """,
    # Toda relación existente debe apuntar a una observación anterior que tenga
    # decisión de observar y a una aclaración con decisión final.
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM cierre aclaracion
            JOIN cierre observada
              ON observada.id = aclaracion.aclara_cierre_id
             AND observada.orden_id = aclaracion.orden_id
            WHERE aclaracion.aclara_cierre_id IS NOT NULL
              AND (
                  observada.creado_en >= aclaracion.creado_en
                  OR NOT EXISTS (
                      SELECT 1
                      FROM decision_cierre d
                      WHERE d.cierre_id = observada.id
                        AND d.decision = 'observar'
                  )
                  OR EXISTS (
                      SELECT 1
                      FROM decision_cierre d
                      WHERE d.cierre_id = aclaracion.id
                        AND d.decision NOT IN ('certificar', 'cerrar')
                  )
              )
            LIMIT 1
        ) THEN
            RAISE EXCEPTION
                'Hay una relación de aclaración sin observación válida o con decisión no final';
        END IF;
    END
    $$
    """,
    # Backfill determinista: se completa únicamente la relación cuyo par es
    # único en ambos sentidos. La precomprobación anterior garantiza que no
    # se.resolve un caso ambiguo.
    f"""
    {_PARES_ACLARACION}
    , candidatos AS (
        SELECT pares.*,
               count(*) OVER (PARTITION BY pares.aclaracion_id) AS por_aclaracion,
               count(*) OVER (PARTITION BY pares.observada_id) AS por_observada
        FROM pares
    )
    UPDATE cierre aclaracion
    SET aclara_cierre_id = candidatos.observada_id
    FROM candidatos
    WHERE candidatos.aclaracion_id = aclaracion.id
      AND candidatos.por_aclaracion = 1
      AND candidatos.por_observada = 1
      AND aclaracion.aclara_cierre_id IS NULL
    """,
    # Una observación admite una sola aclaración, incluso si la aclaración
    # todavía espera su decisión final.
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM cierre
            WHERE aclara_cierre_id IS NOT NULL
            GROUP BY aclara_cierre_id
            HAVING count(*) > 1
        ) THEN
            RAISE EXCEPTION
                'Hay más de una aclaración para la misma observación';
        END IF;
    END
    $$
    """,
    # Una observación admite una sola aclaración.
    """
    CREATE UNIQUE INDEX ux_cierre_aclaracion_observada
    ON cierre(aclara_cierre_id)
    WHERE aclara_cierre_id IS NOT NULL
    """,
    # El habilita es dinero y evidencia: no se modifica ni se elimina.
    """
    CREATE TRIGGER trg_habilitacion_append_only
    BEFORE UPDATE OR DELETE ON habilitacion
    FOR EACH ROW EXECUTE FUNCTION pia3_rechazar_modificacion_append_only()
    """,
    """
    CREATE TRIGGER trg_habilitacion_append_only_truncate
    BEFORE TRUNCATE ON habilitacion
    FOR EACH STATEMENT EXECUTE FUNCTION pia3_rechazar_modificacion_append_only()
    """,
    """
    ALTER TABLE habilitacion
    ADD CONSTRAINT ck_habilitacion_monto_positivo CHECK (
        monto > 0
        AND monto <> 'NaN'::numeric
        AND monto <> 'Infinity'::numeric
        AND monto <> '-Infinity'::numeric
    )
    """,
    """
    ALTER TABLE habilitacion
    ADD CONSTRAINT ck_habilitacion_moneda_usd CHECK (moneda = 'USD')
    """,
    # La defensa de PostgreSQL exige que el estado terminal tenga la decisión
    # que lo justifica. Para Certificada exige además la certificación y el
    # habilita linked; la aplicación los emite antes de cambiar el estado,
    # dentro de la misma transacción.
    """
    CREATE OR REPLACE FUNCTION pia3_validar_terminal_orden_0011()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    DECLARE
        cierre_actual UUID;
    BEGIN
        IF NEW.estado IS NOT DISTINCT FROM OLD.estado
           OR NEW.estado NOT IN ('Cerrada', 'Certificada', 'Observada') THEN
            RETURN NEW;
        END IF;

        SELECT id INTO cierre_actual
        FROM cierre
        WHERE orden_id = NEW.id
        ORDER BY creado_en DESC, id DESC
        LIMIT 1;

        IF cierre_actual IS NULL THEN
            RAISE EXCEPTION
                'La orden % no puede entrar en % sin un cierre técnico',
                NEW.id, NEW.estado
                USING ERRCODE = '55000';
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM decision_cierre d
            WHERE d.cierre_id = cierre_actual
              AND d.orden_id = NEW.id
              AND d.estado_orden = NEW.estado::text
              AND (
                  (NEW.estado = 'Cerrada' AND d.decision = 'cerrar')
                  OR (NEW.estado = 'Certificada' AND d.decision = 'certificar')
                  OR (NEW.estado = 'Observada' AND d.decision = 'observar')
              )
        ) THEN
            RAISE EXCEPTION
                'La orden % no puede entrar en % sin la decisión humana correspondiente',
                NEW.id, NEW.estado
                USING ERRCODE = '55000';
        END IF;

        IF NEW.estado = 'Observada' AND NOT EXISTS (
            SELECT 1
            FROM certificacion c
            JOIN decision_cierre d ON d.cierre_id = c.cierre_id
            WHERE c.cierre_id = cierre_actual
              AND c.orden_id = NEW.id
              AND c.decision = 'observa'
              AND d.decision = 'observar'
        ) THEN
            RAISE EXCEPTION
                'La orden % no puede entrar en Observada sin su certificación',
                NEW.id
                USING ERRCODE = '55000';
        END IF;

        IF NEW.estado = 'Certificada' AND NOT EXISTS (
            SELECT 1
            FROM decision_cierre d
            JOIN certificacion c
              ON c.cierre_id = d.cierre_id
             AND c.orden_id = d.orden_id
             AND c.decision = 'certifica'
            JOIN habilitacion h
              ON h.decision_cierre_id = d.id
             AND h.certificacion_id = c.id
             AND h.orden_id = NEW.id
            WHERE d.cierre_id = cierre_actual
              AND d.orden_id = NEW.id
              AND d.decision = 'certificar'
              AND d.estado_orden = 'Certificada'
              AND h.monto > 0
              AND h.moneda = 'USD'
        ) THEN
            RAISE EXCEPTION
                'La orden % no puede entrar en Certificada sin certificación y habilita válido',
                NEW.id
                USING ERRCODE = '55000';
        END IF;

        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER trg_orden_validar_transicion_terminal_0011
    BEFORE UPDATE OF estado ON orden
    FOR EACH ROW EXECUTE FUNCTION pia3_validar_terminal_orden_0011()
    """,
    # 0010 bloqueaba el motivo técnico en Aprobada -> Ejecutada. Se cambia el
    # trigger a una función 0011 que permite exclusivamente ese motivo; el
    # resto del contenido decidido continúa bloqueado.
    """
    CREATE OR REPLACE FUNCTION pia3_proteger_contenido_orden_0011()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    DECLARE
        humano BOOLEAN;
        cambio_motivo BOOLEAN;
    BEGIN
        SELECT EXISTS (
            SELECT 1
            FROM aprobacion
            WHERE orden_id = OLD.id
              AND usuario IS NOT NULL
              AND btrim(usuario) <> ''
        ) INTO humano;

        IF NOT (
            OLD.estado IN ('Ejecutada', 'Observada', 'Cerrada', 'Certificada', 'Rechazada')
            OR humano
        ) THEN
            RETURN NEW;
        END IF;

        cambio_motivo := NEW.motivo IS DISTINCT FROM OLD.motivo;
        IF NEW.equipo IS DISTINCT FROM OLD.equipo
           OR NEW.evento_id IS DISTINCT FROM OLD.evento_id
           OR NEW.detencion IS DISTINCT FROM OLD.detencion
           OR NEW.costo_estimado IS DISTINCT FROM OLD.costo_estimado
           OR NEW.ejecutor IS DISTINCT FROM OLD.ejecutor
           OR NEW.reduce_inyeccion IS DISTINCT FROM OLD.reduce_inyeccion
           OR (
               cambio_motivo
               AND NOT (
                   (OLD.estado = 'Aprobada' AND NEW.estado = 'Ejecutada')
                   OR (OLD.estado = 'Ejecutada'
                       AND NEW.estado IN ('Observada', 'Cerrada', 'Certificada'))
                   OR (OLD.estado = 'Observada' AND NEW.estado = 'Ejecutada')
               )
           )
        THEN
            RAISE EXCEPTION
                'El contenido de la orden % ya decidido no puede modificarse', OLD.id
                USING ERRCODE = '55000';
        END IF;

        RETURN NEW;
    END;
    $$
    """,
    "DROP TRIGGER trg_orden_proteger_contenido ON orden",
    """
    CREATE TRIGGER trg_orden_proteger_contenido
    BEFORE UPDATE ON orden
    FOR EACH ROW EXECUTE FUNCTION pia3_proteger_contenido_orden_0011()
    """,
]


DDL_DOWN: list[str] = [
    "DROP TRIGGER IF EXISTS trg_orden_proteger_contenido ON orden",
    """
    CREATE TRIGGER trg_orden_proteger_contenido
    BEFORE UPDATE ON orden
    FOR EACH ROW EXECUTE FUNCTION pia3_proteger_contenido_orden()
    """,
    "DROP FUNCTION IF EXISTS pia3_proteger_contenido_orden_0011()",
    "DROP TRIGGER IF EXISTS trg_orden_validar_transicion_terminal_0011 ON orden",
    "DROP TRIGGER IF EXISTS trg_orden_validar_terminal_0011 ON orden",
    "DROP FUNCTION IF EXISTS pia3_validar_terminal_orden_0011()",
    "DROP TRIGGER IF EXISTS trg_habilitacion_append_only_truncate ON habilitacion",
    "DROP TRIGGER IF EXISTS trg_habilitacion_append_only ON habilitacion",
    "ALTER TABLE habilitacion DROP CONSTRAINT IF EXISTS ck_habilitacion_moneda_usd",
    "ALTER TABLE habilitacion DROP CONSTRAINT IF EXISTS ck_habilitacion_monto_positivo",
    "DROP INDEX IF EXISTS ux_cierre_aclaracion_observada",
]


def upgrade() -> None:
    for sentencia in DDL_UP:
        op.execute(sentencia)


def downgrade() -> None:
    for sentencia in DDL_DOWN:
        op.execute(sentencia)
