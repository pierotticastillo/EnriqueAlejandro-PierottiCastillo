"""Recuperadores de fragmentos con cita.

Hay dos implementaciones del mismo contrato:

``RecuperadorMemoria``
    Construye un ``IndiceRag`` con el corpus que sirve la API y responde en el
    proceso. Es el camino del servidor MCP y la base de la evaluación.

``RecuperadorPgvector``
    Consulta la tabla ``rag_chunk`` con pgvector para los candidatos densos y
    calcula la pata léxica (BM25) en el proceso, con el mismo código que la
    evaluación, sobre los textos que guarda PostgreSQL. Normaliza ambas
    señales y las combina con el mismo peso que el índice en memoria. Es el
    camino de producción cuando el índice ya está cargado.

Por qué la pata léxica no sale del FTS de PostgreSQL: ``ts_rank_cd`` no
pondera por IDF, así que una pregunta con términos comunes (agua, bomba) la
domina. Medido sobre la suite de 71 preguntas, el FTS rindió ``hit@5 0,61``
contra ``0,85`` del BM25 de referencia. El índice GIN queda en el esquema
para consultas SQL puntuales, pero el ranking de la tool es BM25.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from config import Settings
from db.session import crear_engine
from rag.corpus import CorpusPlanta, DocumentoRag
from rag.embedder import Embedder, crear_embedder
from rag.indice import PESO_LEXICO_POR_DEFECTO, IndiceLexico, IndiceRag
from tools.cliente import ClientePlanta, ErrorHerramienta
from tools.modelos import ConsultaManuales, FragmentoManual

logger = logging.getLogger("rag.retriever")


@dataclass
class RecuperadorMemoria:
    """Búsqueda híbrida en memoria sobre el corpus de la planta."""

    indice: IndiceRag
    documentos: list[DocumentoRag]

    @classmethod
    def desde_corpus(
        cls,
        documentos: list[DocumentoRag],
        embedder: Embedder,
        *,
        peso_lexico: float = PESO_LEXICO_POR_DEFECTO,
    ) -> RecuperadorMemoria:
        return cls(
            indice=IndiceRag(documentos, embedder, peso_lexico=peso_lexico),
            documentos=documentos,
        )

    def buscar(self, consulta: ConsultaManuales) -> list[FragmentoManual]:
        return self.indice.buscar(
            consulta.consulta,
            top_k=consulta.top_k,
            documento=consulta.documento,
        )


class RecuperadorPgvector:
    """Búsqueda híbrida contra ``rag_chunk``: pgvector para lo denso, BM25 para lo léxico."""

    def __init__(
        self,
        engine: Engine,
        embedder: Embedder,
        *,
        peso_lexico: float = PESO_LEXICO_POR_DEFECTO,
        candidatos: int = 60,
    ) -> None:
        self._engine = engine
        self._embedder = embedder
        self._peso_lexico = peso_lexico
        self._candidatos = max(candidatos, 10)
        self._lexico: IndiceLexico | None = None
        self._filas_lexicas: list[tuple[str, str, str]] = []

    def disponible(self) -> bool:
        try:
            with self._engine.connect() as conexion:
                existe = conexion.execute(
                    text("SELECT to_regclass('public.rag_chunk')")
                ).scalar_one()
                if existe is None:
                    return False
                cantidad = conexion.execute(text("SELECT count(*) FROM rag_chunk")).scalar_one()
                return int(cantidad) > 0
        except Exception as exc:  # noqa: BLE001 - la base puede no estar levantada
            logger.warning("El índice pgvector no está disponible: %s", exc)
            return False

    def buscar(self, consulta: ConsultaManuales) -> list[FragmentoManual]:
        vector = self._embedder.embed([consulta.consulta])[0]
        filtro_documento = "AND d.clave = :documento" if consulta.documento else ""
        parametros: dict[str, Any] = {
            "embedding": _vector(vector),
            "candidatos": self._candidatos,
            "limite": consulta.top_k,
            "consulta": consulta.consulta,
        }
        if consulta.documento:
            parametros["documento"] = consulta.documento
        densos = self._densos(filtro_documento, parametros)
        lexicos = self._lexicos(parametros)
        fundidos = _fusionar(densos, lexicos, peso_lexico=self._peso_lexico)
        if not fundidos:
            return []
        indices = list(fundidos)[: consulta.top_k]
        return self._fragmentos(indices, fundidos)

    def _densos(self, filtro: str, parametros: dict[str, Any]) -> dict[str, float]:
        consulta = text(
            f"""
            SELECT c.id, 1 - (c.embedding <=> CAST(:embedding AS vector)) AS puntaje
            FROM rag_chunk c
            JOIN rag_documento d ON d.id = c.documento_id
            WHERE c.embedding IS NOT NULL
              AND c.vigente AND d.vigente
              {filtro}
            ORDER BY c.embedding <=> CAST(:embedding AS vector)
            LIMIT :candidatos
            """  # noqa: S608 - el filtro es una constante del código
        )
        try:
            with self._engine.connect() as conexion:
                return {
                    str(fila[0]): float(fila[1])
                    for fila in conexion.execute(consulta, parametros)
                }
        except Exception as exc:  # noqa: BLE001 - pgvector puede no estar instalado
            logger.warning("Fallo la búsqueda densa: %s", exc)
            return {}

    def _lexicos(self, parametros: dict[str, Any]) -> dict[str, float]:
        """Puntaje BM25 de los fragmentos guardados en PostgreSQL.

        El índice léxico se arma una vez por proceso con los textos que ya
        están en la base; no hace falta volver a consultar la planta ni a
        recalcular embeddings.
        """
        consulta_texto = parametros.get("consulta", "")
        documento = parametros.get("documento")
        indice, filas = self._indice_lexico()
        if indice is None or not filas:
            return {}
        puntajes = indice.puntajes(consulta_texto)
        candidatos = [
            (filas[posicion][0], puntajes[posicion])
            for posicion in range(len(filas))
            if (documento is None or filas[posicion][1] == documento)
            and puntajes[posicion] > 0
        ]
        candidatos.sort(key=lambda item: (-item[1], item[0]))
        return dict(candidatos[: self._candidatos])

    def _indice_lexico(self) -> tuple[IndiceLexico | None, list[tuple[str, str, str]]]:
        """Carga (una vez) los textos vigentes para el ranking léxico."""
        if self._lexico is not None:
            return self._lexico, self._filas_lexicas
        consulta = text(
            """
            SELECT c.id::text, d.clave, c.texto
            FROM rag_chunk c
            JOIN rag_documento d ON d.id = c.documento_id
            WHERE c.vigente AND d.vigente
            ORDER BY d.clave, c.orden, c.cita
            """
        )
        try:
            with self._engine.connect() as conexion:
                filas = [
                    (str(fila[0]), str(fila[1]), str(fila[2]))
                    for fila in conexion.execute(consulta)
                ]
        except Exception as exc:  # noqa: BLE001 - la base puede no estar levantada
            logger.warning("No se pudo leer el corpus indexado: %s", exc)
            return None, []
        self._filas_lexicas = filas
        self._lexico = IndiceLexico([fila[2] for fila in filas])
        return self._lexico, self._filas_lexicas

    def _fragmentos(
        self,
        indices: list[str],
        puntajes: dict[str, float],
    ) -> list[FragmentoManual]:
        consulta = text(
            """
            SELECT c.id, c.seccion, c.cita, c.titulo, c.texto, c.vigente,
                   d.clave AS documento
            FROM rag_chunk c
            JOIN rag_documento d ON d.id = c.documento_id
            WHERE c.id::text = ANY(:indices)
            """
        )
        with self._engine.connect() as conexion:
            filas = conexion.execute(consulta, {"indices": indices}).mappings().all()
        por_id = {str(fila["id"]): fila for fila in filas}
        resultado: list[FragmentoManual] = []
        for indice in indices:
            fila = por_id.get(indice)
            if fila is None:
                continue
            resultado.append(
                FragmentoManual(
                    documento=str(fila["documento"]),
                    seccion=str(fila["seccion"]),
                    cita=str(fila["cita"]),
                    titulo=fila["titulo"],
                    texto=str(fila["texto"])[:2000],
                    vigente=bool(fila["vigente"]),
                    puntaje=round(puntajes[indice], 6),
                )
            )
        return resultado


def crear_recuperador(
    cliente: ClientePlanta,
    *,
    settings: Settings | None = None,
    motor: str | None = None,
    embedder: Embedder | None = None,
    engine: Engine | None = None,
) -> RecuperadorMemoria | RecuperadorPgvector:
    """Crea el recuperador configurado; si pgvector no está, usa memoria."""
    settings = settings or Settings()
    motor = (motor or settings.rag_motor).strip().lower()
    embedder = embedder or crear_embedder(
        settings.rag_embedder,
        modelo=settings.rag_modelo,
        dimension=settings.rag_dimension,
        cache_dir=settings.rag_cache_dir,
    )
    if motor == "pgvector":
        try:
            motor_db = engine or crear_engine(settings.database_url)
            recuperador = RecuperadorPgvector(
                motor_db,
                embedder,
                peso_lexico=settings.rag_peso_lexico,
            )
            if recuperador.disponible():
                return recuperador
        except Exception as exc:  # noqa: BLE001 - sin base se sigue con memoria
            logger.warning("No se pudo usar pgvector (%s); se indexa en memoria", exc)
    documentos = CorpusPlanta(cliente).cargar(incluir_historial=True)
    if not documentos:
        raise ErrorHerramienta("La planta no devolvió documentos para el RAG")
    return RecuperadorMemoria.desde_corpus(
        documentos,
        embedder,
        peso_lexico=settings.rag_peso_lexico,
    )


def _fusionar(
    densos: dict[str, float],
    lexicos: dict[str, float],
    *,
    peso_lexico: float,
) -> dict[str, float]:
    """Combina las dos señales con la misma normalización que el índice en memoria.

    Cada señal se reescala a [0, 1] sobre la unión de candidatos. Sumar los
    puntajes crudos no sirve: ``ts_rank_cd`` y el coseno viven en escalas
    distintas. Fusionar por rango (RRF) también pierde precisión cuando una
    señal tiene un acierto exacto en la primera posición.
    """
    ids = set(densos) | set(lexicos)
    if not ids:
        return {}
    maximo_lexico = max(lexicos.values(), default=0.0)
    valores_densos = list(densos.values())
    minimo_denso = min(valores_densos, default=0.0)
    rango_denso = max(valores_densos, default=0.0) - minimo_denso
    puntajes: dict[str, float] = {}
    for indice in ids:
        lexico = lexicos.get(indice, 0.0) / maximo_lexico if maximo_lexico > 0 else 0.0
        if indice in densos and rango_denso > 0:
            denso = (densos[indice] - minimo_denso) / rango_denso
        else:
            denso = 0.0
        puntajes[indice] = peso_lexico * lexico + (1.0 - peso_lexico) * denso
    return dict(sorted(puntajes.items(), key=lambda item: (-item[1], item[0])))


def _vector(valores: list[float]) -> str:
    return "[" + ",".join(f"{valor:.7f}" for valor in valores) + "]"
