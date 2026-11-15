"""Manifiesto inmutable de evidencia y ledger append-only de decisiones.

La evidencia que sostiene una orden y la que sustenta un cierre dejan de
vivir únicamente dentro del JSON mutable ``aprobacion.reglas_evaluadas``.
A partir de esta revisión hay dos registros separados:

* ``manifiesto_evidencia``: una fotografía inmutable de la evidencia usada
  por una orden, un cierre o una decisión humana;
* ``ledger_decisiones``: la cadena append-only que permite reconstruir una
  orden desde el evento hasta el estado final.

Las dos tablas bloquean ``UPDATE`` y ``DELETE`` con un trigger de
PostgreSQL.  No usan claves foráneas hacia las tablas operativas: un evento,
una orden o un cierre pueden eliminarse por su propia política, pero la
evidencia que ya decidió una orden se conserva para la auditoría.

El hash del manifiesto es SHA-256 de la representación JSONB canónica de
PostgreSQL.  Un constraint de la tabla obliga a que coincida con el
contenido, de modo que cualquier alteración sería detectable.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-14
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


DDL_UP: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS manifiesto_evidencia (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
        tipo TEXT NOT NULL CHECK (tipo IN ('orden', 'cierre', 'decision_cierre')),
        referencia_id TEXT NOT NULL,
        evento_id TEXT NOT NULL,
        orden_id TEXT NOT NULL,
        parte_id TEXT,
        contenido JSONB NOT NULL,
        hash_sha256 TEXT NOT NULL,
        CONSTRAINT ux_manifiesto_evidencia_referencia UNIQUE (tipo, referencia_id),
        CONSTRAINT ck_manifiesto_evidencia_hash CHECK (
            hash_sha256 ~ '^[0-9a-f]{64}$'
            AND hash_sha256 = encode(
                sha256(convert_to(contenido::text, 'UTF8')),
                'hex'
            )
        )
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_manifiesto_evidencia_orden "
    "ON manifiesto_evidencia(orden_id, creado_en, id)",
    "CREATE INDEX IF NOT EXISTS ix_manifiesto_evidencia_tipo "
    "ON manifiesto_evidencia(tipo, creado_en)",
    """
    CREATE TABLE IF NOT EXISTS ledger_decisiones (
        id BIGSERIAL PRIMARY KEY,
        creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
        tipo TEXT NOT NULL CHECK (
            tipo IN (
                'evento', 'evidencia', 'analisis', 'propuesta', 'reglas',
                'aprobacion', 'cierre', 'decision_cierre', 'trayectoria'
            )
        ),
        referencia_id TEXT,
        evento_id TEXT NOT NULL,
        orden_id TEXT NOT NULL,
        evidencia JSONB NOT NULL DEFAULT '{}'::jsonb,
        analisis JSONB NOT NULL DEFAULT '{}'::jsonb,
        propuesta JSONB NOT NULL DEFAULT '{}'::jsonb,
        reglas JSONB NOT NULL DEFAULT '{}'::jsonb,
        actor TEXT,
        rol TEXT,
        estado TEXT,
        modelo TEXT,
        herramientas JSONB NOT NULL DEFAULT '[]'::jsonb,
        tokens_prompt INTEGER NOT NULL DEFAULT 0,
        tokens_completion INTEGER NOT NULL DEFAULT 0,
        costo_usd NUMERIC(14, 6) NOT NULL DEFAULT 0,
        segundos NUMERIC(14, 3) NOT NULL DEFAULT 0,
        datos JSONB NOT NULL DEFAULT '{}'::jsonb,
        idempotencia TEXT NOT NULL UNIQUE
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_ledger_decisiones_orden "
    "ON ledger_decisiones(orden_id, creado_en, id)",
    "CREATE INDEX IF NOT EXISTS ix_ledger_decisiones_tipo "
    "ON ledger_decisiones(tipo, creado_en, id)",
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
    CREATE TRIGGER trg_manifiesto_evidencia_append_only
    BEFORE UPDATE OR DELETE ON manifiesto_evidencia
    FOR EACH ROW EXECUTE FUNCTION pia3_rechazar_modificacion_append_only()
    """,
    """
    CREATE TRIGGER trg_ledger_decisiones_append_only
    BEFORE UPDATE OR DELETE ON ledger_decisiones
    FOR EACH ROW EXECUTE FUNCTION pia3_rechazar_modificacion_append_only()
    """,
    # Backfill de las órdenes históricas. Lo que el esquema anterior no
    # guardaba de forma estructurada queda marcado como no_disponible; nunca
    # se inventa una fila, una foto, una tendencia o un fragmento.
    """
    WITH ordenes AS (
        SELECT o.id AS orden_id, o.evento_id, o.estado,
               COALESCE(
                   (SELECT a.reglas_evaluadas
                    FROM aprobacion a
                    WHERE a.orden_id = o.id
                    ORDER BY a.creada_en DESC, a.id DESC LIMIT 1),
                   '{}'::jsonb
               ) AS reglas,
               COALESCE(e.payload, '{}'::jsonb) AS evento
        FROM orden o
        LEFT JOIN evento_sistema e ON e.id = o.evento_id
    ), contenido AS (
        SELECT ordenes.*,
               jsonb_build_object(
                   'version', 1,
                   'evento_id', evento_id,
                   'orden_id', orden_id,
                   'parte', jsonb_build_object(
                       'estado', CASE
                           WHEN COALESCE(evento ->> 'parte_id', '') <> ''
                           THEN 'disponible' ELSE 'no_disponible' END,
                       'id', COALESCE(evento ->> 'parte_id', 'no_disponible'),
                       'filas', '[]'::jsonb
                   ),
                   'fotos', jsonb_build_object(
                       'estado', CASE
                           WHEN jsonb_typeof(reglas -> 'evidencia_vision') = 'array'
                            AND jsonb_array_length(reglas -> 'evidencia_vision') > 0
                           THEN 'disponible' ELSE 'no_disponible' END,
                       'elementos', COALESCE(
                           (
                               SELECT jsonb_agg(jsonb_build_object(
                                   'id', COALESCE(foto ->> 'foto_id', 'no_disponible'),
                                   'url', COALESCE(foto ->> 'foto_url', 'no_disponible'),
                                   'ruta_local', COALESCE(
                                       foto ->> 'ruta_local', 'no_disponible'
                                   ),
                                   'hash_sha256', COALESCE(
                                       foto ->> 'hash_sha256', 'no_disponible'
                                   ),
                                   'vision', foto
                               ))
                               FROM jsonb_array_elements(
                                   CASE WHEN jsonb_typeof(reglas -> 'evidencia_vision') = 'array'
                                   THEN reglas -> 'evidencia_vision'
                                   ELSE '[]'::jsonb END
                               ) AS foto
                           ), '[]'::jsonb
                       )
                   ),
                   'tendencia', jsonb_build_object(
                       'estado', 'no_disponible', 'elementos', '[]'::jsonb
                   ),
                   'fragmentos', jsonb_build_object(
                       'estado', CASE
                           WHEN jsonb_typeof(reglas -> 'citas') = 'array'
                            AND jsonb_array_length(reglas -> 'citas') > 0
                           THEN 'parcial' ELSE 'no_disponible' END,
                       'elementos', COALESCE(
                           (
                               SELECT jsonb_agg(jsonb_build_object(
                                   'cita', cita,
                                   'documento', CASE
                                       WHEN position('§' in cita) > 0
                                       THEN split_part(cita, '§', 1)
                                       ELSE 'no_disponible' END,
                                   'seccion', CASE
                                       WHEN position('§' in cita) > 0
                                       THEN split_part(cita, '§', 2)
                                       ELSE 'no_disponible' END,
                                   'texto', 'no_disponible'
                               ))
                               FROM jsonb_array_elements_text(
                                   CASE WHEN jsonb_typeof(reglas -> 'citas') = 'array'
                                   THEN reglas -> 'citas'
                                   ELSE '[]'::jsonb END
                               ) AS cita
                           ), '[]'::jsonb
                       )
                   ),
                   'citas', CASE
                       WHEN jsonb_typeof(reglas -> 'citas') = 'array'
                       THEN reglas -> 'citas' ELSE '[]'::jsonb END,
                   'origen', 'migracion_0009'
               ) AS manifiesto
        FROM ordenes
    )
    INSERT INTO manifiesto_evidencia (
        tipo, referencia_id, evento_id, orden_id, parte_id,
        contenido, hash_sha256
    )
    SELECT
        'orden', orden_id, evento_id, orden_id,
        NULLIF(evento ->> 'parte_id', ''),
        manifiesto,
        encode(sha256(convert_to(manifiesto::text, 'UTF8')), 'hex')
    FROM contenido
    ON CONFLICT (tipo, referencia_id) DO NOTHING
    """,
    # Backfill de cierres técnicos históricos.
    """
    WITH cierres AS (
        SELECT c.id, c.orden_id, o.evento_id, c.externa_id, c.tecnico,
               c.empresa, c.transcripcion, c.tareas_hechas,
               c.repuestos_usados, c.horas, c.prueba_final, c.diferencias
        FROM cierre c
        JOIN orden o ON o.id = c.orden_id
    ), contenido AS (
        SELECT cierres.*,
               jsonb_build_object(
                   'version', 1,
                   'evento_id', evento_id,
                   'orden_id', orden_id,
                   'cierre_id', COALESCE(externa_id, id::text),
                   'evidencia_cierre', jsonb_build_object(
                       'tecnico', COALESCE(tecnico, 'no_disponible'),
                       'empresa', COALESCE(empresa, 'no_disponible'),
                       'transcripcion', COALESCE(transcripcion, 'no_disponible'),
                       'tareas', COALESCE(tareas_hechas, '[]'::jsonb),
                       'repuestos', COALESCE(repuestos_usados, '[]'::jsonb),
                       'horas', jsonb_build_object(
                           'estado', CASE
                               WHEN horas IS NULL THEN 'no_disponible' ELSE 'disponible' END,
                           'valor', CASE
                               WHEN horas IS NULL
                               THEN to_jsonb('no_disponible'::text)
                               ELSE to_jsonb(horas) END
                       ),
                       'prueba_final', jsonb_build_object(
                           'estado', CASE
                               WHEN prueba_final IS NULL
                               THEN 'no_disponible' ELSE 'disponible' END,
                           'valor', COALESCE(prueba_final, 'no_disponible')
                       ),
                       'diferencias', COALESCE(diferencias, '[]'::jsonb),
                       'fotos', jsonb_build_object(
                           'estado', 'no_disponible', 'elementos', '[]'::jsonb
                       )
                   ),
                   'origen', 'migracion_0009'
               ) AS manifiesto
        FROM cierres
    )
    INSERT INTO manifiesto_evidencia (
        tipo, referencia_id, evento_id, orden_id, parte_id,
        contenido, hash_sha256
    )
    SELECT
        'cierre', COALESCE(externa_id, id::text), evento_id, orden_id, NULL,
        manifiesto,
        encode(sha256(convert_to(manifiesto::text, 'UTF8')), 'hex')
    FROM contenido
    ON CONFLICT (tipo, referencia_id) DO NOTHING
    """,
    # Backfill de las decisiones humanas históricas.
    """
    WITH decisiones AS (
        SELECT dc.id, dc.cierre_id, dc.orden_id, dc.decision,
               dc.estado_orden, dc.actor, dc.rol, dc.motivo, dc.evidencia,
               o.evento_id, c.externa_id
        FROM decision_cierre dc
        JOIN orden o ON o.id = dc.orden_id
        JOIN cierre c ON c.id = dc.cierre_id
    ), base AS (
        SELECT decisiones.*,
               mc.contenido AS manifiesto_cierre,
               h.numero AS habilita, h.monto AS monto_habilita
        FROM decisiones
        LEFT JOIN manifiesto_evidencia mc
          ON mc.tipo = 'cierre'
         AND mc.referencia_id = COALESCE(decisiones.externa_id, decisiones.cierre_id::text)
        LEFT JOIN habilitacion h ON h.orden_id = decisiones.orden_id
    ), contenido AS (
        SELECT base.*,
               jsonb_build_object(
                   'version', 1,
                   'evento_id', evento_id,
                   'orden_id', orden_id,
                   'cierre_id', COALESCE(externa_id, cierre_id::text),
                   'evidencia_cierre', COALESCE(
                       manifiesto_cierre -> 'evidencia_cierre', evidencia
                   ),
                   'decision', jsonb_build_object(
                       'decision', decision,
                       'estado', estado_orden,
                       'actor', actor,
                       'rol', rol,
                       'motivo', COALESCE(motivo, 'no_disponible')
                   ),
                   'certificacion', jsonb_build_object(
                       'estado', CASE
                           WHEN decision = 'certificar' THEN 'certificada'
                           WHEN decision = 'observar' THEN 'observada'
                           ELSE 'no_disponible' END,
                       'habilita', jsonb_build_object(
                           'estado', CASE
                               WHEN habilita IS NULL
                               THEN 'no_disponible' ELSE 'disponible' END,
                           'numero', COALESCE(habilita, 'no_disponible'),
                           'monto_usd', CASE
                               WHEN monto_habilita IS NULL
                               THEN to_jsonb('no_disponible'::text)
                               ELSE to_jsonb(monto_habilita) END
                       )
                   ),
                   'origen', 'migracion_0009'
               ) AS manifiesto
        FROM base
    )
    INSERT INTO manifiesto_evidencia (
        tipo, referencia_id, evento_id, orden_id, parte_id,
        contenido, hash_sha256
    )
    SELECT
        'decision_cierre', COALESCE(externa_id, cierre_id::text),
        evento_id, orden_id, NULL, manifiesto,
        encode(sha256(convert_to(manifiesto::text, 'UTF8')), 'hex')
    FROM contenido
    ON CONFLICT (tipo, referencia_id) DO NOTHING
    """,
    # El ledger histórico conserva la cadena completa. Cuando el esquema
    # anterior no guardaba un dato, el JSON queda vacío o no_disponible; no
    # se reconstruye una decisión que no ocurrió.
    """
    INSERT INTO ledger_decisiones (
        tipo, referencia_id, evento_id, orden_id, actor, rol, estado,
        datos, idempotencia
    )
    SELECT
        'evento', o.evento_id, o.evento_id, o.id,
        'codigo', 'sistema', o.estado::text,
        COALESCE(e.payload, '{}'::jsonb),
        'evento:' || o.evento_id || ':' || o.id
    FROM orden o
    LEFT JOIN evento_sistema e ON e.id = o.evento_id
    ON CONFLICT (idempotencia) DO NOTHING
    """,
    """
    INSERT INTO ledger_decisiones (
        tipo, referencia_id, evento_id, orden_id, evidencia,
        actor, rol, estado, modelo, herramientas,
        tokens_prompt, tokens_completion, costo_usd, segundos,
        idempotencia
    )
    SELECT
        'evidencia', o.evento_id, o.evento_id, o.id,
        jsonb_build_object(
            'manifiesto_id', m.id,
            'hash_sha256', m.hash_sha256
        ),
        'codigo', 'sistema', o.estado::text,
        (
            SELECT string_agg(DISTINCT foto -> 'vision' ->> 'modelo', ', ')
            FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(m.contenido -> 'fotos' -> 'elementos') = 'array'
                THEN m.contenido -> 'fotos' -> 'elementos'
                ELSE '[]'::jsonb END
            ) AS foto
            WHERE COALESCE(foto -> 'vision' ->> 'modelo', '') NOT IN ('', 'no_disponible')
        ),
        COALESCE(
            (
                SELECT jsonb_agg(DISTINCT herramienta ORDER BY herramienta)
                FROM (
                    SELECT 'vision.describir' AS herramienta
                    WHERE EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(
                            CASE WHEN jsonb_typeof(
                                m.contenido -> 'fotos' -> 'elementos'
                            ) = 'array'
                            THEN m.contenido -> 'fotos' -> 'elementos'
                            ELSE '[]'::jsonb END
                        ) AS foto
                        WHERE COALESCE(foto -> 'vision' ->> 'modelo', '')
                              NOT IN ('', 'no_disponible')
                    )
                    UNION ALL
                    SELECT 'rag.contexto.construir_contexto'
                    WHERE m.contenido -> 'fragmentos' ->> 'estado' = 'disponible'
                ) herramientas
            ),
            '[]'::jsonb
        ),
        (
            SELECT COALESCE(sum(COALESCE((foto -> 'vision' ->> 'tokens_prompt')::numeric, 0)), 0)
            FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(m.contenido -> 'fotos' -> 'elementos') = 'array'
                THEN m.contenido -> 'fotos' -> 'elementos'
                ELSE '[]'::jsonb END
            ) AS foto
        ),
        (
            SELECT COALESCE(
                sum(COALESCE((foto -> 'vision' ->> 'tokens_completion')::numeric, 0)),
                0
            )
            FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(m.contenido -> 'fotos' -> 'elementos') = 'array'
                THEN m.contenido -> 'fotos' -> 'elementos'
                ELSE '[]'::jsonb END
            ) AS foto
        ),
        0,
        (
            SELECT COALESCE(sum(COALESCE((foto -> 'vision' ->> 'segundos')::numeric, 0)), 0)
            FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(m.contenido -> 'fotos' -> 'elementos') = 'array'
                THEN m.contenido -> 'fotos' -> 'elementos'
                ELSE '[]'::jsonb END
            ) AS foto
        ),
        'evidencia:' || o.evento_id || ':' || o.id
    FROM orden o
    JOIN manifiesto_evidencia m
      ON m.tipo = 'orden' AND m.referencia_id = o.id
    ON CONFLICT (idempotencia) DO NOTHING
    """,
    """
    INSERT INTO ledger_decisiones (
        tipo, referencia_id, evento_id, orden_id, analisis,
        actor, rol, estado, herramientas, idempotencia
    )
    SELECT
        'analisis', o.evento_id, o.evento_id, o.id,
        jsonb_build_object(
            'falla', reglas -> 'falla',
            'causas', COALESCE(reglas -> 'causas', '[]'::jsonb),
            'citas', COALESCE(reglas -> 'citas', '[]'::jsonb),
            'reincidencia', COALESCE((reglas ->> 'reincidencia')::boolean, false)
        ),
        'codigo', 'sistema', o.estado::text,
        CASE WHEN m.contenido -> 'fragmentos' ->> 'estado' = 'disponible'
        THEN '["rag.contexto.construir_contexto"]'::jsonb
        ELSE '[]'::jsonb END,
        'analisis:' || o.evento_id || ':' || o.id
    FROM orden o
    JOIN manifiesto_evidencia m
      ON m.tipo = 'orden' AND m.referencia_id = o.id
    LEFT JOIN LATERAL (
        SELECT a.reglas_evaluadas AS reglas
        FROM aprobacion a
        WHERE a.orden_id = o.id
        ORDER BY a.creada_en DESC, a.id DESC LIMIT 1
    ) r ON true
    ON CONFLICT (idempotencia) DO NOTHING
    """,
    """
    INSERT INTO ledger_decisiones (
        tipo, referencia_id, evento_id, orden_id, propuesta,
        actor, rol, estado, idempotencia
    )
    SELECT
        'propuesta', o.evento_id, o.evento_id, o.id,
        jsonb_build_object(
            'equipo', o.equipo,
            'tareas', COALESCE(
                (SELECT jsonb_agg(t.tarea_id ORDER BY t.tarea_id)
                 FROM orden_tarea t WHERE t.orden_id = o.id),
                '[]'::jsonb
            ),
            'repuestos', COALESCE(
                (SELECT jsonb_agg(jsonb_build_object(
                    'codigo', r.repuesto_id,
                    'cantidad', r.cantidad
                ) ORDER BY r.repuesto_id)
                 FROM orden_repuesto r WHERE r.orden_id = o.id),
                '[]'::jsonb
            ),
            'costo_usd', o.costo_estimado,
            'detiene_equipo', o.detencion,
            'reduce_inyeccion', o.reduce_inyeccion,
            'ejecutor', o.ejecutor
        ),
        'codigo', 'sistema', o.estado::text,
        'propuesta:' || o.evento_id || ':' || o.id
    FROM orden o
    ON CONFLICT (idempotencia) DO NOTHING
    """,
    """
    INSERT INTO ledger_decisiones (
        tipo, referencia_id, evento_id, orden_id, reglas,
        actor, rol, estado, herramientas, idempotencia
    )
    SELECT
        'reglas', o.evento_id, o.evento_id, o.id,
        COALESCE(r.reglas, '{}'::jsonb),
        'codigo', 'sistema', o.estado::text,
        CASE WHEN m.contenido -> 'fragmentos' ->> 'estado' = 'disponible'
        THEN '["rag.contexto.construir_contexto"]'::jsonb
        ELSE '[]'::jsonb END,
        'reglas:' || o.evento_id || ':' || o.id
    FROM orden o
    JOIN manifiesto_evidencia m
      ON m.tipo = 'orden' AND m.referencia_id = o.id
    LEFT JOIN LATERAL (
        SELECT a.reglas_evaluadas AS reglas
        FROM aprobacion a
        WHERE a.orden_id = o.id
        ORDER BY a.creada_en DESC, a.id DESC LIMIT 1
    ) r ON true
    ON CONFLICT (idempotencia) DO NOTHING
    """,
    """
    INSERT INTO ledger_decisiones (
        tipo, referencia_id, evento_id, orden_id, evidencia,
        propuesta, reglas, actor, rol, estado, datos, idempotencia
    )
    SELECT
        'aprobacion', a.orden_id, o.evento_id, a.orden_id,
        CASE WHEN m.id IS NULL THEN '{}'::jsonb ELSE jsonb_build_object(
            'manifiesto_id', m.id,
            'hash_sha256', m.hash_sha256
        ) END,
        jsonb_build_object(
            'tareas', COALESCE(
                (SELECT jsonb_agg(t.tarea_id ORDER BY t.tarea_id)
                 FROM orden_tarea t WHERE t.orden_id = o.id),
                '[]'::jsonb
            ),
            'repuestos', COALESCE(
                (SELECT jsonb_agg(jsonb_build_object(
                    'codigo', r.repuesto_id,
                    'cantidad', r.cantidad
                ) ORDER BY r.repuesto_id)
                 FROM orden_repuesto r WHERE r.orden_id = o.id),
                '[]'::jsonb
            ),
            'costo_usd', o.costo_estimado
        ),
        a.reglas_evaluadas,
        COALESCE(a.usuario, 'codigo'),
        COALESCE(a.rol, 'sistema'),
        a.decision,
        jsonb_build_object('decision', a.decision, 'motivo', a.motivo),
        'aprobacion:' || a.orden_id || ':' || a.decision || ':' ||
            COALESCE(a.usuario, 'sin_usuario')
    FROM aprobacion a
    JOIN orden o ON o.id = a.orden_id
    LEFT JOIN manifiesto_evidencia m
      ON m.tipo = 'orden' AND m.referencia_id = o.id
    ON CONFLICT (idempotencia) DO NOTHING
    """,
    """
    INSERT INTO ledger_decisiones (
        tipo, referencia_id, evento_id, orden_id, evidencia,
        propuesta, actor, rol, estado, datos, idempotencia
    )
    SELECT
        'cierre', COALESCE(c.externa_id, c.id::text), o.evento_id, c.orden_id,
        jsonb_build_object(
            'manifiesto_id', m.id,
            'hash_sha256', m.hash_sha256
        ),
        jsonb_build_object(
            'tareas', COALESCE(c.tareas_hechas, '[]'::jsonb),
            'repuestos', COALESCE(c.repuestos_usados, '[]'::jsonb),
            'horas', c.horas,
            'prueba_final', c.prueba_final
        ),
        'codigo', 'sistema', 'Ejecutada',
        jsonb_build_object('diferencias', COALESCE(c.diferencias, '[]'::jsonb)),
        'cierre:' || COALESCE(c.externa_id, c.id::text)
    FROM cierre c
    JOIN orden o ON o.id = c.orden_id
    JOIN manifiesto_evidencia m
      ON m.tipo = 'cierre'
     AND m.referencia_id = COALESCE(c.externa_id, c.id::text)
    ON CONFLICT (idempotencia) DO NOTHING
    """,
    """
    INSERT INTO ledger_decisiones (
        tipo, referencia_id, evento_id, orden_id, evidencia,
        reglas, actor, rol, estado, datos, idempotencia
    )
    SELECT
        'decision_cierre', COALESCE(c.externa_id, dc.cierre_id::text),
        o.evento_id, dc.orden_id,
        jsonb_build_object(
            'manifiesto_id', m.id,
            'hash_sha256', m.hash_sha256
        ),
        jsonb_build_object(
            'tipo', 'cierre_humano',
            'decision', dc.decision,
            'rol', dc.rol,
            'cierre_id', COALESCE(c.externa_id, dc.cierre_id::text),
            'evidencia', dc.evidencia
        ),
        dc.actor, dc.rol, dc.estado_orden,
        jsonb_build_object(
            'decision', dc.decision,
            'motivo', dc.motivo,
            'habilita', CASE
                WHEN h.numero IS NULL THEN 'no_disponible' ELSE h.numero END,
            'monto_usd', CASE
                WHEN h.monto IS NULL
                THEN to_jsonb('no_disponible'::text)
                ELSE to_jsonb(h.monto) END
        ),
        'decision_cierre:' || COALESCE(c.externa_id, dc.cierre_id::text)
    FROM decision_cierre dc
    JOIN cierre c ON c.id = dc.cierre_id
    JOIN orden o ON o.id = dc.orden_id
    JOIN manifiesto_evidencia m
      ON m.tipo = 'decision_cierre'
     AND m.referencia_id = COALESCE(c.externa_id, dc.cierre_id::text)
    LEFT JOIN habilitacion h ON h.orden_id = dc.orden_id
    ON CONFLICT (idempotencia) DO NOTHING
    """,
    # Cada paso histórico de trayectoria conserva modelo, herramienta,
    # argumentos, tokens, costo y tiempo.
    """
    INSERT INTO ledger_decisiones (
        tipo, referencia_id, evento_id, orden_id,
        actor, rol, estado, modelo, herramientas,
        tokens_prompt, tokens_completion, costo_usd, segundos,
        datos, idempotencia
    )
    SELECT
        'trayectoria', rt.id::text, COALESCE(rt.evento_id, ''), rt.orden_id,
        NULL, NULL, o.estado::text, rt.modelo,
        CASE WHEN rt.herramienta IS NULL THEN '[]'::jsonb
             ELSE jsonb_build_array(rt.herramienta) END,
        rt.tokens_prompt, rt.tokens_completion, rt.costo_usd, rt.segundos,
        jsonb_build_object(
            'etapa', rt.etapa,
            'tipo', rt.tipo,
            'argumentos', COALESCE(rt.argumentos, '{}'::jsonb),
            'salida', COALESCE(rt.salida, '{}'::jsonb),
            'error', rt.error,
            'equipo', rt.equipo,
            'paso', rt.paso
        ),
        'trayectoria:' || rt.id::text
    FROM registro_trayectoria rt
    JOIN orden o ON o.id = rt.orden_id
    ON CONFLICT (idempotencia) DO NOTHING
    """,
]


DDL_DOWN: list[str] = [
    "DROP TRIGGER IF EXISTS trg_ledger_decisiones_append_only ON ledger_decisiones",
    "DROP TRIGGER IF EXISTS trg_manifiesto_evidencia_append_only ON manifiesto_evidencia",
    "DROP FUNCTION IF EXISTS pia3_rechazar_modificacion_append_only()",
    "DROP TABLE IF EXISTS ledger_decisiones",
    "DROP TABLE IF EXISTS manifiesto_evidencia",
]


def upgrade() -> None:
    for sentencia in DDL_UP:
        op.execute(sentencia)


def downgrade() -> None:
    for sentencia in DDL_DOWN:
        op.execute(sentencia)
