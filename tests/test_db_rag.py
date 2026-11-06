"""Tests de la indexación RAG contra PostgreSQL (Fase 4).

Requieren un PostgreSQL con las migraciones aplicadas:

    TEST_DATABASE_URL=postgresql+psycopg://pia3:pia3@localhost:55432/pia3 pytest

Sin esa variable se omiten, igual que los tests de idempotencia de la Fase 0.5.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

from rag.corpus import ChunkRag, DocumentoRag
from rag.embedder import EmbedderHashing
from rag.indexer import IndexadorRag
from rag.retriever import RecuperadorPgvector
from tools.modelos import ConsultaManuales

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL no definida",
)


@pytest.fixture(scope="module")
def engine():
    motor = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    with motor.connect() as conexion:
        conexion.execute(text("SELECT 1"))
    yield motor
    with motor.begin() as conexion:
        conexion.execute(text("DELETE FROM rag_documento WHERE clave LIKE 'test-%'"))
    motor.dispose()


def _limpiar_prueba(engine) -> None:
    """Borra sólo los documentos de prueba, nunca el índice real."""
    with engine.begin() as conexion:
        conexion.execute(text("DELETE FROM rag_documento WHERE clave LIKE 'test-%'"))


def _documento(texto: str = "Alejarse y avisar ante una pérdida.") -> list[DocumentoRag]:
    return [
        DocumentoRag(
            clave="test-mbp",
            titulo="Manual de prueba",
            tipo="manual_operadora",
            fabricante="Operadora",
            equipos=("B-101",),
            version="1",
            chunks=(
                ChunkRag(
                    seccion="10.2",
                    cita="TEST-MBP §10.2",
                    titulo="Pérdida",
                    texto=texto,
                ),
            ),
        )
    ]


def test_indexar_es_idempotente_y_actualiza(engine) -> None:
    _limpiar_prueba(engine)
    indexador = IndexadorRag(engine, EmbedderHashing(384))
    primero = indexador.indexar(_documento())
    segundo = indexador.indexar(_documento("Texto actualizado: detener las bombas."))
    assert primero.documentos == 1
    assert primero.chunks == 1
    assert segundo.chunks == 1
    with engine.connect() as conexion:
        documentos = conexion.execute(
            text("SELECT count(*) FROM rag_documento WHERE clave = 'test-mbp'")
        ).scalar_one()
        chunks = conexion.execute(
            text(
                "SELECT count(*) FROM rag_chunk c "
                "JOIN rag_documento d ON d.id = c.documento_id "
                "WHERE d.clave = 'test-mbp'"
            )
        ).scalar_one()
        texto_actual = conexion.execute(
            text(
                "SELECT c.texto FROM rag_chunk c "
                "JOIN rag_documento d ON d.id = c.documento_id "
                "WHERE d.clave = 'test-mbp'"
            )
        ).scalar_one()
    assert documentos == 1
    assert chunks == 1
    assert "actualizado" in texto_actual


def test_recuperador_pgvector_devuelve_cita(engine) -> None:
    _limpiar_prueba(engine)
    IndexadorRag(engine, EmbedderHashing(384)).indexar(_documento())
    recuperador = RecuperadorPgvector(engine, EmbedderHashing(384))
    assert recuperador.disponible()
    resultado = recuperador.buscar(
        ConsultaManuales(consulta="pérdida de agua", documento="test-mbp", top_k=3)
    )
    assert resultado
    assert resultado[0].cita == "TEST-MBP §10.2"
