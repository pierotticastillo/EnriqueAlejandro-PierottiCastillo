"""Indexación del corpus en PostgreSQL + pgvector.

Cada documento queda en ``rag_documento`` y cada sección en ``rag_chunk`` con
su cita, metadatos y embedding. La operación es idempotente: el índice único
``(clave)`` de documentos y ``(documento_id, seccion)`` de fragmentos convierte
una segunda indexación en una actualización, no en un duplicado.

El indexador no inventa datos: copia los metadatos y el texto que sirve la
planta. Los límites, el stock y el historial siguen saliendo de sus endpoints;
el RAG sólo aporta texto citable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from rag.corpus import DocumentoRag
from rag.embedder import Embedder


@dataclass(frozen=True)
class ResumenIndexacion:
    documentos: int
    chunks: int
    embedder: str
    dimension: int


class IndexadorRag:
    """Vuelca un corpus en las tablas ``rag_*``."""

    def __init__(self, engine: Engine, embedder: Embedder) -> None:
        self._engine = engine
        self._embedder = embedder

    def indexar(
        self,
        documentos: list[DocumentoRag],
        *,
        reemplazar: bool = True,
    ) -> ResumenIndexacion:
        chunks = [chunk for documento in documentos for chunk in documento.chunks]
        textos = [
            f"{chunk.titulo or ''}\n{chunk.texto}".strip() for chunk in chunks
        ]
        vectores = self._embedder.embed(textos) if textos else []
        if len(vectores) != len(chunks):
            raise ValueError("El embedder devolvió una cantidad de vectores distinta")
        posicion = 0
        total_chunks = 0
        for documento in documentos:
            with self._engine.begin() as conexion:
                documento_id = conexion.execute(
                    text(
                        """
                        INSERT INTO rag_documento (
                            clave, documento, titulo, tipo, equipo, fabricante,
                            version, vigente, actualizado_en
                        )
                        VALUES (
                            :clave, :documento, :titulo, :tipo, :equipo, :fabricante,
                            :version, :vigente, now()
                        )
                        ON CONFLICT (clave) DO UPDATE SET
                            documento = EXCLUDED.documento,
                            titulo = EXCLUDED.titulo,
                            tipo = EXCLUDED.tipo,
                            equipo = EXCLUDED.equipo,
                            fabricante = EXCLUDED.fabricante,
                            version = EXCLUDED.version,
                            vigente = EXCLUDED.vigente,
                            actualizado_en = now()
                        RETURNING id
                        """
                    ),
                    {
                        "clave": documento.clave,
                        "documento": documento.clave,
                        "titulo": documento.titulo,
                        "tipo": documento.tipo,
                        "equipo": documento.equipo,
                        "fabricante": documento.fabricante,
                        "version": documento.version,
                        "vigente": documento.vigente,
                    },
                ).scalar_one()

                for chunk in documento.chunks:
                    vector = _vector(vectores[posicion])
                    posicion += 1
                    conexion.execute(
                        text(
                            """
                            INSERT INTO rag_chunk (
                                documento_id, seccion, cita, titulo, texto, embedding,
                                pagina, vigente, orden, actualizado_en
                            )
                            VALUES (
                                :documento_id, :seccion, :cita, :titulo, :texto,
                                CAST(:embedding AS vector), :pagina, :vigente, :orden, now()
                            )
                            ON CONFLICT (documento_id, seccion) DO UPDATE SET
                                cita = EXCLUDED.cita,
                                titulo = EXCLUDED.titulo,
                                texto = EXCLUDED.texto,
                                embedding = EXCLUDED.embedding,
                                pagina = EXCLUDED.pagina,
                                vigente = EXCLUDED.vigente,
                                orden = EXCLUDED.orden,
                                actualizado_en = now()
                            """
                        ),
                        {
                            "documento_id": documento_id,
                            "seccion": chunk.seccion,
                            "cita": chunk.cita,
                            "titulo": chunk.titulo,
                            "texto": chunk.texto,
                            "embedding": vector,
                            "pagina": chunk.pagina,
                            "vigente": chunk.vigente,
                            "orden": chunk.orden,
                        },
                    )
                    total_chunks += 1

                if reemplazar:
                    secciones = list(dict.fromkeys(chunk.seccion for chunk in documento.chunks))
                    if secciones:
                        conexion.execute(
                            text(
                                "DELETE FROM rag_chunk "
                                "WHERE documento_id = :documento_id "
                                "AND seccion <> ALL(:secciones)"
                            ),
                            {"documento_id": documento_id, "secciones": secciones},
                        )
                    else:
                        conexion.execute(
                            text("DELETE FROM rag_chunk WHERE documento_id = :documento_id"),
                            {"documento_id": documento_id},
                        )
        return ResumenIndexacion(
            documentos=len(documentos),
            chunks=total_chunks,
            embedder=self._embedder.nombre,
            dimension=self._embedder.dimension,
        )


def limpiar(engine: Engine) -> None:
    """Borra el índice completo. Los documentos caen por cascada."""
    with engine.begin() as conexion:
        conexion.execute(text("DELETE FROM rag_documento"))


def guardar_evaluacion(
    engine: Engine,
    *,
    suite: str,
    motor: str,
    resultado: dict[str, Any],
    detalle: dict[str, Any] | None = None,
) -> None:
    """Persiste una medición de retrieval en ``rag_evaluacion``."""
    total = resultado.get("total", {})
    with engine.begin() as conexion:
        conexion.execute(
            text(
                """
                INSERT INTO rag_evaluacion (
                    suite, motor, hit_at_5, recall_at_5, mrr, distractor_at_5,
                    rev_c_antes_d, detalle
                )
                VALUES (
                    :suite, :motor, :hit, :recall, :mrr, :distractor, :rev_c,
                    CAST(:detalle AS jsonb)
                )
                """
            ),
            {
                "suite": suite,
                "motor": motor,
                "hit": total.get("hit@5"),
                "recall": total.get("recall@5"),
                "mrr": total.get("mrr"),
                "distractor": total.get("distractor@5"),
                "rev_c": resultado.get("revision_vencida_antes"),
                "detalle": json.dumps(detalle or resultado, ensure_ascii=False),
            },
        )


def _vector(valores: list[float]) -> str:
    """Formato textual que pgvector acepta para ``CAST(... AS vector)``."""
    return "[" + ",".join(f"{valor:.7f}" for valor in valores) + "]"
