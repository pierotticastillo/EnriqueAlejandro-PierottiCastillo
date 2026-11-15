"""Integridad e inmutabilidad de las decisiones y de la evidencia.

La revisión ``0009`` protege ``manifiesto_evidencia`` y
``ledger_decisiones`` contra ``UPDATE`` y ``DELETE``, pero PostgreSQL no
ejecuta triggers de fila para ``TRUNCATE``. La revisión ``0008`` permite una
decisión por cierre, pero todavía admite estados imposibles, certificaciones
finales duplicadas y cierres de aclaración sin relación con la observación.

Esta revisión:

* protege ``decision_cierre``, ``manifiesto_evidencia`` y
  ``ledger_decisiones`` contra ``UPDATE``, ``DELETE`` y ``TRUNCATE``;
* vincula decisión, cierre, orden, certificación y habilita con FKs
  compuestas restrictivas;
* admite una sola certificación final y un solo habilita por orden;
* declara un único ciclo de aclaración
  ``Observada -> Ejecutada -> Certificada`` y lo hace trazable;
* aplica en PostgreSQL la máquina de estados y la inmutabilidad del contenido
  que antes sólo existían en la capa de aplicación.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-14
"""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


DDL_UP: list[str] = [
    # Fallar antes de cambiar el esquema si una fila histórica violaría una
    # invariante nueva. La evidencia nunca se repara ni se borra de forma
    # automática.
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM decision_cierre dc
            JOIN cierre c ON c.id = dc.cierre_id
            WHERE c.orden_id <> dc.orden_id
        ) THEN
            RAISE EXCEPTION
                'decision_cierre contiene decisiones ligadas a un cierre de otra orden';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM decision_cierre
            WHERE NOT (
                (decision = 'cerrar' AND rol = 'supervisor' AND estado_orden = 'Cerrada')
                OR (decision = 'certificar' AND rol = 'inspector' AND estado_orden = 'Certificada')
                OR (decision = 'observar' AND rol = 'inspector' AND estado_orden = 'Observada')
            )
        ) THEN
            RAISE EXCEPTION
                'decision_cierre contiene combinaciones incompatibles de decisión, rol y estado';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM decision_cierre
            WHERE decision = 'observar'
              AND (motivo IS NULL OR btrim(motivo) = '')
        ) THEN
            RAISE EXCEPTION
                'decision_cierre contiene una observación sin motivo';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM certificacion c
            LEFT JOIN cierre ce ON ce.id = c.cierre_id
            WHERE c.cierre_id IS NULL OR ce.orden_id <> c.orden_id
        ) THEN
            RAISE EXCEPTION
                'certificacion contiene una certificación ligada a otro cierre u orden';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM certificacion
            GROUP BY cierre_id
            HAVING count(*) > 1
        ) THEN
            RAISE EXCEPTION
                'certificacion contiene más de una certificación para un cierre';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM certificacion
            WHERE decision = 'certifica'
            GROUP BY orden_id
            HAVING count(*) > 1
        ) THEN
            RAISE EXCEPTION
                'certificacion contiene más de una certificación final por orden';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM decision_cierre
            WHERE decision = 'certificar'
            GROUP BY orden_id
            HAVING count(*) > 1
        ) THEN
            RAISE EXCEPTION
                'decision_cierre contiene más de una certificación final por orden';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM habilitacion
            GROUP BY orden_id
            HAVING count(*) > 1
        ) THEN
            RAISE EXCEPTION
                'habilitacion contiene más de una autorización para una orden';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM habilitacion h
            WHERE NOT EXISTS (
                SELECT 1
                FROM decision_cierre d
                JOIN certificacion c
                  ON c.cierre_id = d.cierre_id
                 AND c.decision = 'certifica'
                WHERE d.orden_id = h.orden_id
                  AND d.decision = 'certificar'
            )
        ) THEN
            RAISE EXCEPTION
                'habilitacion contiene una autorización sin certificación final';
        END IF;
    END
    $$
    """,
    # Claves compuestas: la identidad de un cierre, decisión o certificación
    # pertenece a una y sólo una orden.
    """
    ALTER TABLE cierre
    ADD CONSTRAINT ux_cierre_id_orden UNIQUE (id, orden_id)
    """,
    """
    ALTER TABLE decision_cierre
    ADD CONSTRAINT ux_decision_cierre_id_orden UNIQUE (id, orden_id)
    """,
    """
    ALTER TABLE certificacion
    ADD CONSTRAINT ux_certificacion_id_orden UNIQUE (id, orden_id)
    """,
    # Las referencias independientes con cascada se sustituyen por una única
    # FK restrictiva: una decisión no puede desaparecer al borrar su padre.
    """
    ALTER TABLE decision_cierre
    DROP CONSTRAINT decision_cierre_cierre_id_fkey,
    DROP CONSTRAINT decision_cierre_orden_id_fkey
    """,
    """
    ALTER TABLE decision_cierre
    ADD CONSTRAINT fk_decision_cierre_cierre_orden
    FOREIGN KEY (cierre_id, orden_id)
    REFERENCES cierre(id, orden_id)
    ON DELETE RESTRICT
    """,
    """
    ALTER TABLE decision_cierre
    ADD CONSTRAINT ck_decision_cierre_accion_rol_estado
    CHECK (
        (decision = 'cerrar' AND rol = 'supervisor' AND estado_orden = 'Cerrada')
        OR (decision = 'certificar' AND rol = 'inspector' AND estado_orden = 'Certificada')
        OR (decision = 'observar' AND rol = 'inspector' AND estado_orden = 'Observada')
    )
    """,
    """
    ALTER TABLE decision_cierre
    ADD CONSTRAINT ck_decision_cierre_observar_motivo
    CHECK (
        decision <> 'observar'
        OR (motivo IS NOT NULL AND btrim(motivo) <> '')
    )
    """,
    # Una orden tiene una sola decisión de cierre por cierre y una sola
    # certificación final. La observación puede tener su ciclo de aclaración.
    "CREATE UNIQUE INDEX ux_certificacion_cierre ON certificacion(cierre_id)",
    """
    CREATE UNIQUE INDEX ux_certificacion_final_orden
    ON certificacion(orden_id)
    WHERE decision = 'certifica'
    """,
    """
    CREATE UNIQUE INDEX ux_decision_certificacion_orden
    ON decision_cierre(orden_id)
    WHERE decision = 'certificar'
    """,
    """
    ALTER TABLE certificacion
    ADD CONSTRAINT fk_certificacion_cierre_orden
    FOREIGN KEY (cierre_id, orden_id)
    REFERENCES cierre(id, orden_id)
    ON DELETE RESTRICT
    """,
    """
    ALTER TABLE certificacion
    ADD CONSTRAINT ck_certificacion_rol
    CHECK (rol IS NULL OR rol = 'inspector')
    """,
    # La aclaración se modela sin inventar un estado externo: el segundo
    # cierre apunta al cierre observado y ambos pertenecen a la misma orden.
    "ALTER TABLE cierre ADD COLUMN aclara_cierre_id UUID",
    """
    UPDATE cierre aclaracion
    SET aclara_cierre_id = observada.id
    FROM cierre observada
    JOIN decision_cierre d
      ON d.cierre_id = observada.id
     AND d.decision = 'observar'
    WHERE aclaracion.orden_id = observada.orden_id
      AND aclaracion.creado_en > observada.creado_en
      AND EXISTS (
          SELECT 1
          FROM decision_cierre dc
          WHERE dc.cierre_id = aclaracion.id
            AND dc.decision IN ('certificar', 'cerrar')
      )
      AND NOT EXISTS (
          SELECT 1
          FROM cierre ya_aclarada
          WHERE ya_aclarada.aclara_cierre_id = observada.id
      )
    """,
    """
    ALTER TABLE cierre
    ADD CONSTRAINT fk_cierre_aclaracion_orden
    FOREIGN KEY (aclara_cierre_id, orden_id)
    REFERENCES cierre(id, orden_id)
    ON DELETE RESTRICT
    """,
    """
    ALTER TABLE cierre
    ADD CONSTRAINT ck_cierre_aclaracion_distinta
    CHECK (aclara_cierre_id IS NULL OR aclara_cierre_id <> id)
    """,
    # El habilita queda ligado a la decisión y a la certificación que lo
    # autorizan. Las filas históricas se enlazan antes de volver obligatorios
    # esos identificadores.
    "ALTER TABLE habilitacion ADD COLUMN decision_cierre_id UUID",
    "ALTER TABLE habilitacion ADD COLUMN certificacion_id UUID",
    """
    UPDATE habilitacion h
    SET decision_cierre_id = d.id,
        certificacion_id = c.id
    FROM decision_cierre d
    JOIN certificacion c
      ON c.cierre_id = d.cierre_id
     AND c.decision = 'certifica'
    WHERE d.orden_id = h.orden_id
      AND d.decision = 'certificar'
      AND c.orden_id = h.orden_id
    """,
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM habilitacion
            WHERE decision_cierre_id IS NULL OR certificacion_id IS NULL
        ) THEN
            RAISE EXCEPTION
                'No se pudo enlazar todo habilita con su decisión y certificación';
        END IF;
    END
    $$
    """,
    "ALTER TABLE habilitacion ALTER COLUMN decision_cierre_id SET NOT NULL",
    "ALTER TABLE habilitacion ALTER COLUMN certificacion_id SET NOT NULL",
    """
    ALTER TABLE habilitacion
    ADD CONSTRAINT fk_habilitacion_decision_orden
    FOREIGN KEY (decision_cierre_id, orden_id)
    REFERENCES decision_cierre(id, orden_id)
    ON DELETE RESTRICT
    """,
    """
    ALTER TABLE habilitacion
    ADD CONSTRAINT fk_habilitacion_certificacion_orden
    FOREIGN KEY (certificacion_id, orden_id)
    REFERENCES certificacion(id, orden_id)
    ON DELETE RESTRICT
    """,
    """
    CREATE UNIQUE INDEX ux_habilitacion_orden
    ON habilitacion(orden_id)
    """,
    # La máquina de estados también vive en PostgreSQL. La aplicación ya la
    # respeta; esta Trigger es la defensa final contra un camino futuro que
    # intente saltarse el flujo.
    """
    CREATE OR REPLACE FUNCTION pia3_validar_transicion_orden()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF NEW.estado NOT IN ('Borrador', 'Pendiente de aprobacion', 'Aprobada') THEN
                RAISE EXCEPTION
                    'Una orden no puede insertarse directamente en %', NEW.estado
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END IF;

        IF NEW.estado IS NOT DISTINCT FROM OLD.estado THEN
            RETURN NEW;
        END IF;

        IF NOT (
            (OLD.estado = 'Borrador'
                AND NEW.estado IN ('Pendiente de aprobacion', 'Aprobada'))
            OR (OLD.estado = 'Pendiente de aprobacion'
                AND NEW.estado IN ('Aprobada', 'Rechazada'))
            OR (OLD.estado = 'Aprobada' AND NEW.estado = 'Ejecutada')
            OR (OLD.estado = 'Ejecutada'
                AND NEW.estado IN ('Observada', 'Cerrada', 'Certificada'))
            OR (OLD.estado = 'Observada' AND NEW.estado = 'Ejecutada')
        ) THEN
            RAISE EXCEPTION
                'Transición inválida para la orden %: % -> %',
                OLD.id, OLD.estado, NEW.estado
                USING ERRCODE = '55000';
        END IF;

        IF NEW.estado = 'Ejecutada' AND NOT EXISTS (
            SELECT 1 FROM cierre WHERE orden_id = NEW.id
        ) THEN
            RAISE EXCEPTION
                'La orden % no puede ejecutarse sin un cierre técnico', NEW.id
                USING ERRCODE = '55000';
        END IF;

        IF OLD.estado = 'Observada' AND NEW.estado = 'Ejecutada' AND NOT EXISTS (
            SELECT 1
            FROM cierre aclaracion
            JOIN cierre observada ON observada.id = aclaracion.aclara_cierre_id
            JOIN decision_cierre d ON d.cierre_id = observada.id
            WHERE aclaracion.orden_id = NEW.id
              AND d.decision = 'observar'
        ) THEN
            RAISE EXCEPTION
                'La orden % sólo puede volver a Ejecutada mediante una aclaración vinculada',
                NEW.id
                USING ERRCODE = '55000';
        END IF;

        IF OLD.estado = 'Ejecutada' AND NEW.estado = 'Observada' AND EXISTS (
            SELECT 1 FROM cierre WHERE orden_id = NEW.id AND aclara_cierre_id IS NOT NULL
        ) THEN
            RAISE EXCEPTION
                'La orden % ya agotó su ciclo de aclaración', NEW.id
                USING ERRCODE = '55000';
        END IF;

        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER trg_orden_validar_transicion
    BEFORE INSERT OR UPDATE OF estado ON orden
    FOR EACH ROW EXECUTE FUNCTION pia3_validar_transicion_orden()
    """,
    # Una orden con decisión humana o con contenido técnico ya decidido no
    # puede ser degradada por SQL directo. El motivo sí puede cambiar como
    # parte de la decisión humana o del cierre de aclaración.
    """
    CREATE OR REPLACE FUNCTION pia3_proteger_contenido_orden()
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
                   (OLD.estado = 'Ejecutada'
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
    """
    CREATE TRIGGER trg_orden_proteger_contenido
    BEFORE UPDATE ON orden
    FOR EACH ROW EXECUTE FUNCTION pia3_proteger_contenido_orden()
    """,
    # Las tareas y repuestos forman parte del contenido protegido. El borrado
    # en cascada de una orden padre se permite; la modificación directa no.
    """
    CREATE OR REPLACE FUNCTION pia3_proteger_contenido_hijo_orden()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    DECLARE
        orden_padre TEXT := COALESCE(NEW.orden_id, OLD.orden_id);
        estado_padre estado_orden;
        humano BOOLEAN;
    BEGIN
        IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
            RETURN OLD;
        END IF;

        SELECT estado INTO estado_padre FROM orden WHERE id = orden_padre;
        SELECT EXISTS (
            SELECT 1
            FROM aprobacion
            WHERE orden_id = orden_padre
              AND usuario IS NOT NULL
              AND btrim(usuario) <> ''
        ) INTO humano;

        IF estado_padre IN (
            'Ejecutada', 'Observada', 'Cerrada', 'Certificada', 'Rechazada'
        ) OR humano THEN
            RAISE EXCEPTION
                'Las tareas y repuestos de la orden % ya decidida no pueden modificarse',
                orden_padre
                USING ERRCODE = '55000';
        END IF;

        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER trg_orden_tarea_proteger_contenido
    BEFORE INSERT OR UPDATE OR DELETE ON orden_tarea
    FOR EACH ROW EXECUTE FUNCTION pia3_proteger_contenido_hijo_orden()
    """,
    """
    CREATE TRIGGER trg_orden_repuesto_proteger_contenido
    BEFORE INSERT OR UPDATE OR DELETE ON orden_repuesto
    FOR EACH ROW EXECUTE FUNCTION pia3_proteger_contenido_hijo_orden()
    """,
    # Un habilita sólo puede apuntar a una decisión de certificación y a la
    # certificación correspondiente de la misma orden.
    """
    CREATE OR REPLACE FUNCTION pia3_validar_habilitacion()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM decision_cierre d
            JOIN certificacion c
              ON c.cierre_id = d.cierre_id
             AND c.id = NEW.certificacion_id
             AND c.orden_id = NEW.orden_id
             AND c.decision = 'certifica'
            WHERE d.id = NEW.decision_cierre_id
              AND d.orden_id = NEW.orden_id
              AND d.decision = 'certificar'
              AND d.estado_orden = 'Certificada'
        ) THEN
            RAISE EXCEPTION
                'El habilita % no está ligado a una certificación final válida',
                NEW.numero
                USING ERRCODE = '23505';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER trg_habilitacion_validar_certificacion
    BEFORE INSERT OR UPDATE ON habilitacion
    FOR EACH ROW EXECUTE FUNCTION pia3_validar_habilitacion()
    """,
    # Las tres tablas de evidencia canónica son append-only, incluido
    # TRUNCATE. PostgreSQL no dispara triggers de fila para TRUNCATE.
    """
    CREATE OR REPLACE FUNCTION pia3_rechazar_modificacion_append_only()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        RAISE EXCEPTION '% es append-only: la operación % no está permitida',
            TG_TABLE_NAME, TG_OP
            USING ERRCODE = '55000';
    END;
    $$
    """,
    """
    CREATE TRIGGER trg_decision_cierre_append_only
    BEFORE UPDATE OR DELETE ON decision_cierre
    FOR EACH ROW EXECUTE FUNCTION pia3_rechazar_modificacion_append_only()
    """,
    """
    CREATE TRIGGER trg_manifiesto_evidencia_append_only_truncate
    BEFORE TRUNCATE ON manifiesto_evidencia
    FOR EACH STATEMENT EXECUTE FUNCTION pia3_rechazar_modificacion_append_only()
    """,
    """
    CREATE TRIGGER trg_ledger_decisiones_append_only_truncate
    BEFORE TRUNCATE ON ledger_decisiones
    FOR EACH STATEMENT EXECUTE FUNCTION pia3_rechazar_modificacion_append_only()
    """,
    """
    CREATE TRIGGER trg_decision_cierre_append_only_truncate
    BEFORE TRUNCATE ON decision_cierre
    FOR EACH STATEMENT EXECUTE FUNCTION pia3_rechazar_modificacion_append_only()
    """,
]


DDL_DOWN: list[str] = [
    "DROP TRIGGER IF EXISTS trg_decision_cierre_append_only_truncate ON decision_cierre",
    "DROP TRIGGER IF EXISTS trg_ledger_decisiones_append_only_truncate ON ledger_decisiones",
    "DROP TRIGGER IF EXISTS trg_manifiesto_evidencia_append_only_truncate ON manifiesto_evidencia",
    "DROP TRIGGER IF EXISTS trg_decision_cierre_append_only ON decision_cierre",
    "DROP TRIGGER IF EXISTS trg_habilitacion_validar_certificacion ON habilitacion",
    "DROP FUNCTION IF EXISTS pia3_validar_habilitacion()",
    "DROP TRIGGER IF EXISTS trg_orden_repuesto_proteger_contenido ON orden_repuesto",
    "DROP TRIGGER IF EXISTS trg_orden_tarea_proteger_contenido ON orden_tarea",
    "DROP FUNCTION IF EXISTS pia3_proteger_contenido_hijo_orden()",
    "DROP TRIGGER IF EXISTS trg_orden_proteger_contenido ON orden",
    "DROP FUNCTION IF EXISTS pia3_proteger_contenido_orden()",
    "DROP TRIGGER IF EXISTS trg_orden_validar_transicion ON orden",
    "DROP FUNCTION IF EXISTS pia3_validar_transicion_orden()",
    "DROP INDEX IF EXISTS ux_habilitacion_orden",
    "ALTER TABLE habilitacion DROP CONSTRAINT IF EXISTS fk_habilitacion_certificacion_orden",
    "ALTER TABLE habilitacion DROP CONSTRAINT IF EXISTS fk_habilitacion_decision_orden",
    "ALTER TABLE habilitacion DROP COLUMN IF EXISTS certificacion_id",
    "ALTER TABLE habilitacion DROP COLUMN IF EXISTS decision_cierre_id",
    "ALTER TABLE cierre DROP CONSTRAINT IF EXISTS ck_cierre_aclaracion_distinta",
    "ALTER TABLE cierre DROP CONSTRAINT IF EXISTS fk_cierre_aclaracion_orden",
    "ALTER TABLE cierre DROP COLUMN IF EXISTS aclara_cierre_id",
    "ALTER TABLE certificacion DROP CONSTRAINT IF EXISTS ck_certificacion_rol",
    "ALTER TABLE certificacion DROP CONSTRAINT IF EXISTS fk_certificacion_cierre_orden",
    "DROP INDEX IF EXISTS ux_decision_certificacion_orden",
    "DROP INDEX IF EXISTS ux_certificacion_final_orden",
    "DROP INDEX IF EXISTS ux_certificacion_cierre",
    "ALTER TABLE decision_cierre DROP CONSTRAINT IF EXISTS ux_decision_cierre_id_orden",
    "ALTER TABLE certificacion DROP CONSTRAINT IF EXISTS ux_certificacion_id_orden",
    "ALTER TABLE decision_cierre DROP CONSTRAINT IF EXISTS ck_decision_cierre_observar_motivo",
    "ALTER TABLE decision_cierre DROP CONSTRAINT IF EXISTS ck_decision_cierre_accion_rol_estado",
    "ALTER TABLE decision_cierre DROP CONSTRAINT IF EXISTS fk_decision_cierre_cierre_orden",
    """
    ALTER TABLE decision_cierre
    ADD CONSTRAINT decision_cierre_cierre_id_fkey
    FOREIGN KEY (cierre_id) REFERENCES cierre(id) ON DELETE CASCADE
    """,
    """
    ALTER TABLE decision_cierre
    ADD CONSTRAINT decision_cierre_orden_id_fkey
    FOREIGN KEY (orden_id) REFERENCES orden(id) ON DELETE CASCADE
    """,
    "ALTER TABLE cierre DROP CONSTRAINT IF EXISTS ux_cierre_id_orden",
]


def upgrade() -> None:
    for sentencia in DDL_UP:
        op.execute(sentencia)


def downgrade() -> None:
    for sentencia in DDL_DOWN:
        op.execute(sentencia)
