"""Esquema inicial PIA-3 (Anexo A del plan).

Revision ID: 0001
Revises:
Create Date: 2026-11-04
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


DDL_UP: list[str] = [
    'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"',
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE TYPE estado_parte AS ENUM ('Borrador', 'Confirmado')",
    "CREATE TYPE origen_evento AS ENUM ('parte', 'sensor')",
    "CREATE TYPE estado_evento AS ENUM ('Sin novedad', 'Escalado', 'AlertaSeguridad')",
    """CREATE TYPE estado_orden AS ENUM (
        'Borrador', 'Pendiente de aprobacion', 'Aprobada', 'Rechazada',
        'Ejecutada', 'Observada', 'Cerrada', 'Certificada'
    )""",
    "CREATE TYPE estado_outbox AS ENUM ('pendiente', 'enviado', 'error')",
    # Ingesta y parte por voz
    """CREATE TABLE nota (
        id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        ts            TIMESTAMPTZ NOT NULL,
        audio_url     TEXT NOT NULL,
        transcripcion TEXT,
        origen        TEXT NOT NULL DEFAULT 'normal',
        estado        TEXT NOT NULL DEFAULT 'recibida',
        creada_en     TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE parte (
        id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        nota_id       UUID NOT NULL REFERENCES nota(id) ON DELETE CASCADE,
        estado        estado_parte NOT NULL DEFAULT 'Borrador',
        creado_en     TIMESTAMPTZ NOT NULL DEFAULT now(),
        confirmado_en TIMESTAMPTZ
    )""",
    """CREATE TABLE fila_parte (
        id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        parte_id    UUID NOT NULL REFERENCES parte(id) ON DELETE CASCADE,
        tag         TEXT NOT NULL,
        variable    TEXT NOT NULL,
        valor       NUMERIC NOT NULL,
        unidad      TEXT NOT NULL,
        hora_iso    TIMESTAMPTZ NOT NULL,
        observacion TEXT,
        confianza   NUMERIC,
        UNIQUE (parte_id, tag, variable)
    )""",
    """CREATE TABLE foto (
        id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        nota_id       UUID NOT NULL REFERENCES nota(id) ON DELETE CASCADE,
        url           TEXT NOT NULL,
        ruta_local    TEXT,
        fila_parte_id UUID REFERENCES fila_parte(id) ON DELETE SET NULL
    )""",
    """CREATE TABLE conversacion_nota (
        nota_id        UUID PRIMARY KEY REFERENCES nota(id) ON DELETE CASCADE,
        mensaje_tablet JSONB,
        borrador       JSONB,
        intento        INT NOT NULL DEFAULT 0,
        estado         TEXT NOT NULL DEFAULT 'abierta',
        actualizada_en TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE pregunta_tablet (
        id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        nota_id   UUID NOT NULL REFERENCES nota(id) ON DELETE CASCADE,
        campo     TEXT NOT NULL,
        opciones  JSONB,
        respuesta TEXT,
        resuelta  BOOLEAN NOT NULL DEFAULT false,
        creada_en TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    # Detección
    """CREATE TABLE evento_sistema (
        id        TEXT PRIMARY KEY,
        origen    origen_evento NOT NULL,
        estado    estado_evento NOT NULL,
        payload   JSONB NOT NULL,
        creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE evento_nota (
        evento_id TEXT NOT NULL REFERENCES evento_sistema(id) ON DELETE CASCADE,
        nota_id   UUID NOT NULL REFERENCES nota(id) ON DELETE CASCADE,
        PRIMARY KEY (evento_id, nota_id)
    )""",
    """CREATE TABLE alerta_seguridad (
        id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        evento_id        TEXT NOT NULL REFERENCES evento_sistema(id) ON DELETE CASCADE,
        tipo             TEXT NOT NULL CHECK (tipo IN ('S1', 'S2', 'S3', 'S4')),
        publicada_en     TIMESTAMPTZ NOT NULL,
        espera_respuesta BOOLEAN NOT NULL DEFAULT false,
        evidencia        JSONB
    )""",
    # Órdenes y certificación
    """CREATE TABLE orden (
        id             TEXT PRIMARY KEY,
        evento_id      TEXT NOT NULL REFERENCES evento_sistema(id),
        equipo         TEXT NOT NULL,
        estado         estado_orden NOT NULL DEFAULT 'Borrador',
        motivo         TEXT,
        detencion      BOOLEAN NOT NULL DEFAULT false,
        costo_estimado NUMERIC,
        creada_en      TIMESTAMPTZ NOT NULL DEFAULT now(),
        actualizada_en TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE orden_tarea (
        id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        orden_id    TEXT NOT NULL REFERENCES orden(id) ON DELETE CASCADE,
        tarea_id    TEXT NOT NULL,
        descripcion TEXT,
        origen      TEXT
    )""",
    """CREATE TABLE orden_repuesto (
        id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        orden_id       TEXT NOT NULL REFERENCES orden(id) ON DELETE CASCADE,
        repuesto_id    TEXT NOT NULL,
        cantidad       NUMERIC NOT NULL,
        costo_unitario NUMERIC NOT NULL,
        disponible     BOOLEAN NOT NULL
    )""",
    """CREATE TABLE aprobacion (
        id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        orden_id         TEXT NOT NULL REFERENCES orden(id) ON DELETE CASCADE,
        decision         TEXT NOT NULL,
        motivo           TEXT,
        reglas_evaluadas JSONB NOT NULL,
        usuario          TEXT,
        creada_en        TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE cierre (
        id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        orden_id         TEXT NOT NULL REFERENCES orden(id) ON DELETE CASCADE,
        tecnico          TEXT NOT NULL,
        tareas_hechas    JSONB NOT NULL,
        repuestos_usados JSONB NOT NULL,
        prueba_final     TEXT,
        diferencias      JSONB,
        creado_en        TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE certificacion (
        id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        orden_id  TEXT NOT NULL REFERENCES orden(id) ON DELETE CASCADE,
        inspector TEXT NOT NULL,
        decision  TEXT NOT NULL CHECK (decision IN ('certifica', 'observa')),
        motivo    TEXT,
        creada_en TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE habilitacion (
        id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        orden_id     TEXT NOT NULL REFERENCES orden(id) ON DELETE CASCADE,
        numero       TEXT NOT NULL UNIQUE,
        monto        NUMERIC NOT NULL,
        moneda       TEXT NOT NULL DEFAULT 'USD',
        fuente_monto TEXT NOT NULL,
        emitida_en   TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    # RAG
    """CREATE TABLE rag_documento (
        id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        equipo     TEXT,
        fabricante TEXT,
        documento  TEXT NOT NULL,
        version    TEXT,
        vigente    BOOLEAN NOT NULL DEFAULT true
    )""",
    """CREATE TABLE rag_chunk (
        id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        documento_id UUID NOT NULL REFERENCES rag_documento(id) ON DELETE CASCADE,
        seccion      TEXT NOT NULL,
        cita         TEXT NOT NULL,
        texto        TEXT NOT NULL,
        embedding    vector(1536)
    )""",
    """CREATE INDEX ix_rag_chunk_embedding
        ON rag_chunk USING hnsw (embedding vector_cosine_ops)""",
    """CREATE TABLE rag_evaluacion (
        id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        suite         TEXT NOT NULL,
        hit_at_5      NUMERIC,
        recall_at_5   NUMERIC,
        rev_c_antes_d INT,
        ejecutada_en  TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    # Sincronización e idempotencia
    """CREATE TABLE outbox_cola (
        id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        tipo            TEXT NOT NULL,
        payload         JSONB NOT NULL,
        estado          estado_outbox NOT NULL DEFAULT 'pendiente',
        intentos        INT NOT NULL DEFAULT 0,
        proximo_intento TIMESTAMPTZ
    )""",
    """CREATE TABLE idempotency_key (
        clave         TEXT PRIMARY KEY,
        tipo          TEXT NOT NULL,
        referencia_id TEXT NOT NULL,
        creada_en     TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE latido (
        id                     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        sistema                TEXT NOT NULL,
        enlace_ok              BOOLEAN NOT NULL,
        pendientes_sincronizar INT NOT NULL DEFAULT 0,
        ts                     TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    # Índices de consulta frecuente
    "CREATE INDEX ix_fila_parte_parte_id ON fila_parte(parte_id)",
    "CREATE INDEX ix_foto_nota_id ON foto(nota_id)",
    "CREATE INDEX ix_evento_nota_nota_id ON evento_nota(nota_id)",
    "CREATE INDEX ix_orden_evento_id ON orden(evento_id)",
    "CREATE INDEX ix_rag_chunk_documento_id ON rag_chunk(documento_id)",
    "CREATE INDEX ix_outbox_pendientes ON outbox_cola(estado, proximo_intento)",
]


DDL_DOWN: list[str] = [
    "DROP INDEX IF EXISTS ix_outbox_pendientes",
    "DROP INDEX IF EXISTS ix_rag_chunk_documento_id",
    "DROP INDEX IF EXISTS ix_orden_evento_id",
    "DROP INDEX IF EXISTS ix_evento_nota_nota_id",
    "DROP INDEX IF EXISTS ix_foto_nota_id",
    "DROP INDEX IF EXISTS ix_fila_parte_parte_id",
    "DROP TABLE IF EXISTS latido",
    "DROP TABLE IF EXISTS idempotency_key",
    "DROP TABLE IF EXISTS outbox_cola",
    "DROP TABLE IF EXISTS rag_evaluacion",
    "DROP INDEX IF EXISTS ix_rag_chunk_embedding",
    "DROP TABLE IF EXISTS rag_chunk",
    "DROP TABLE IF EXISTS rag_documento",
    "DROP TABLE IF EXISTS habilitacion",
    "DROP TABLE IF EXISTS certificacion",
    "DROP TABLE IF EXISTS cierre",
    "DROP TABLE IF EXISTS aprobacion",
    "DROP TABLE IF EXISTS orden_repuesto",
    "DROP TABLE IF EXISTS orden_tarea",
    "DROP TABLE IF EXISTS orden",
    "DROP TABLE IF EXISTS alerta_seguridad",
    "DROP TABLE IF EXISTS evento_nota",
    "DROP TABLE IF EXISTS evento_sistema",
    "DROP TABLE IF EXISTS pregunta_tablet",
    "DROP TABLE IF EXISTS conversacion_nota",
    "DROP TABLE IF EXISTS foto",
    "DROP TABLE IF EXISTS fila_parte",
    "DROP TABLE IF EXISTS parte",
    "DROP TABLE IF EXISTS nota",
    "DROP TYPE IF EXISTS estado_outbox",
    "DROP TYPE IF EXISTS estado_orden",
    "DROP TYPE IF EXISTS estado_evento",
    "DROP TYPE IF EXISTS origen_evento",
    "DROP TYPE IF EXISTS estado_parte",
]


def upgrade() -> None:
    for sentencia in DDL_UP:
        op.execute(sentencia)


def downgrade() -> None:
    for sentencia in DDL_DOWN:
        op.execute(sentencia)
