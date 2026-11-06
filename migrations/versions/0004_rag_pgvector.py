"""RAG: metadatos de indexación, embeddings de 384 dims y evaluación.

La Fase 4 indexa los manuales y el historial con el modelo local
``paraphrase-multilingual-MiniLM-L12-v2`` (384 dimensiones). La tabla original
tenía ``vector(1536)`` como marcador; esta migración fija la dimensión real,
agrega los metadatos que pide el plan y los índices de búsqueda.

Al cambiar la dimensión se descartan los embeddings viejos (``USING NULL``):
el índice se reconstruye con ``python -m rag.cli indexar``.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-05
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


DDL_UP: list[str] = [
    # ---------------------------------------------------------- documentos
    "ALTER TABLE rag_documento ADD COLUMN IF NOT EXISTS clave TEXT",
    "ALTER TABLE rag_documento ADD COLUMN IF NOT EXISTS titulo TEXT",
    "ALTER TABLE rag_documento ADD COLUMN IF NOT EXISTS tipo TEXT",
    "ALTER TABLE rag_documento ADD COLUMN IF NOT EXISTS actualizado_en "
    "TIMESTAMPTZ NOT NULL DEFAULT now()",
    "UPDATE rag_documento SET clave = documento WHERE clave IS NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_rag_documento_clave ON rag_documento(clave)",
    # ---------------------------------------------------------- fragmentos
    "ALTER TABLE rag_chunk ADD COLUMN IF NOT EXISTS titulo TEXT",
    "ALTER TABLE rag_chunk ADD COLUMN IF NOT EXISTS pagina INT",
    "ALTER TABLE rag_chunk ADD COLUMN IF NOT EXISTS vigente BOOLEAN NOT NULL DEFAULT true",
    "ALTER TABLE rag_chunk ADD COLUMN IF NOT EXISTS orden INT NOT NULL DEFAULT 0",
    "ALTER TABLE rag_chunk ADD COLUMN IF NOT EXISTS actualizado_en "
    "TIMESTAMPTZ NOT NULL DEFAULT now()",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_rag_chunk_documento_seccion "
    "ON rag_chunk(documento_id, seccion)",
    # El modelo local produce 384 dimensiones. El USING NULL evita fallar si
    # hubiera vectores de la dimensión vieja: se reindexa.
    "DROP INDEX IF EXISTS ix_rag_chunk_embedding",
    "ALTER TABLE rag_chunk ALTER COLUMN embedding TYPE vector(384) USING NULL",
    "CREATE INDEX IF NOT EXISTS ix_rag_chunk_embedding "
    "ON rag_chunk USING hnsw (embedding vector_cosine_ops)",
    # Texto completo en español para la pata léxica del híbrido.
    "CREATE INDEX IF NOT EXISTS ix_rag_chunk_texto_fts ON rag_chunk "
    "USING gin (to_tsvector('spanish'::regconfig, texto))",
    # ---------------------------------------------------------- evaluación
    "ALTER TABLE rag_evaluacion ADD COLUMN IF NOT EXISTS motor TEXT",
    "ALTER TABLE rag_evaluacion ADD COLUMN IF NOT EXISTS mrr NUMERIC",
    "ALTER TABLE rag_evaluacion ADD COLUMN IF NOT EXISTS distractor_at_5 NUMERIC",
    "ALTER TABLE rag_evaluacion ADD COLUMN IF NOT EXISTS detalle JSONB",
]


DDL_DOWN: list[str] = [
    "ALTER TABLE rag_evaluacion DROP COLUMN IF EXISTS detalle",
    "ALTER TABLE rag_evaluacion DROP COLUMN IF EXISTS distractor_at_5",
    "ALTER TABLE rag_evaluacion DROP COLUMN IF EXISTS mrr",
    "ALTER TABLE rag_evaluacion DROP COLUMN IF EXISTS motor",
    "DROP INDEX IF EXISTS ix_rag_chunk_texto_fts",
    "DROP INDEX IF EXISTS ix_rag_chunk_embedding",
    "ALTER TABLE rag_chunk ALTER COLUMN embedding TYPE vector(1536) USING NULL",
    "CREATE INDEX IF NOT EXISTS ix_rag_chunk_embedding "
    "ON rag_chunk USING hnsw (embedding vector_cosine_ops)",
    "DROP INDEX IF EXISTS ix_rag_chunk_documento_seccion",
    "ALTER TABLE rag_chunk DROP COLUMN IF EXISTS actualizado_en",
    "ALTER TABLE rag_chunk DROP COLUMN IF EXISTS orden",
    "ALTER TABLE rag_chunk DROP COLUMN IF EXISTS vigente",
    "ALTER TABLE rag_chunk DROP COLUMN IF EXISTS pagina",
    "ALTER TABLE rag_chunk DROP COLUMN IF EXISTS titulo",
    "DROP INDEX IF EXISTS ix_rag_documento_clave",
    "ALTER TABLE rag_documento DROP COLUMN IF EXISTS actualizado_en",
    "ALTER TABLE rag_documento DROP COLUMN IF EXISTS tipo",
    "ALTER TABLE rag_documento DROP COLUMN IF EXISTS titulo",
    "ALTER TABLE rag_documento DROP COLUMN IF EXISTS clave",
]


def upgrade() -> None:
    for sentencia in DDL_UP:
        op.execute(sentencia)


def downgrade() -> None:
    for sentencia in DDL_DOWN:
        op.execute(sentencia)
