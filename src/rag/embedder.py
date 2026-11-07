"""Embeddings para el RAG, detrás de un protocolo.

La lista cerrada de modelos del curso todavía no está confirmada. Para no atar
el índice a un proveedor, el sistema define ``Embedder`` y ofrece dos motores:

``fastembed``
    Modelo local de ``sentence-transformers`` (ONNX, CPU) que se descarga en la
    build. Es el motor que usa Docker y el que se midió contra la suite de
    retrieval. ``paraphrase-multilingual-MiniLM-L12-v2`` produce 384 dims.

``hashing``
    Embedding determinista de términos y n-gramas, sin dependencias ni
    descargas. Se usa en tests y como respaldo cuando el modelo no está
    disponible; no pretende reemplazar al modelo semántico.

En ambos casos el vector se normaliza a norma 1 para que el coseno de pgvector
sea directamente comparable.
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
from collections import Counter
from collections.abc import Sequence
from typing import Protocol

from rag.texto import normalizar, tokenizar

logger = logging.getLogger("rag.embedder")

DIMENSION_POR_DEFECTO = 384
MODELO_POR_DEFECTO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class Embedder(Protocol):
    """Contrato mínimo de un motor de embeddings."""

    nombre: str
    dimension: int

    def embed(self, textos: Sequence[str]) -> list[list[float]]:
        """Vector normalizado por cada texto, en el mismo orden."""


def _normalizar_vector(vector: list[float]) -> list[float]:
    norma = math.sqrt(sum(valor * valor for valor in vector))
    if norma == 0:
        return vector
    return [valor / norma for valor in vector]


class EmbedderHashing:
    """Embedding local determinista por feature hashing.

    Combina palabras, pares de palabras y n-gramas de caracteres. Es rápido y
    repetible, pero **no** es un modelo semántico: sirve para tests y como red
    de seguridad si el modelo local no está instalado.
    """

    nombre = "hashing"

    def __init__(self, dimension: int = DIMENSION_POR_DEFECTO) -> None:
        if dimension <= 0:
            raise ValueError("La dimensión del embedding tiene que ser positiva")
        self.dimension = dimension

    def embed(self, textos: Sequence[str]) -> list[list[float]]:
        return [self._embed_uno(texto) for texto in textos]

    def _embed_uno(self, texto: str) -> list[float]:
        vector = [0.0] * self.dimension
        for caracteristica, peso in _caracteristicas(texto).items():
            digest = hashlib.blake2b(caracteristica.encode("utf-8"), digest_size=8).digest()
            indice = int.from_bytes(digest[:4], "little") % self.dimension
            signo = 1.0 if digest[4] & 1 else -1.0
            vector[indice] += signo * peso
        return _normalizar_vector(vector)


class EmbedderFastEmbed:
    """Embedder local ONNX de ``fastembed``.

    El modelo se carga una vez y se reutiliza. La importación es perezosa para
    que el resto del sistema (y los tests) no dependa de ``fastembed``.
    """

    nombre = "fastembed"

    def __init__(
        self,
        modelo: str = MODELO_POR_DEFECTO,
        *,
        cache_dir: str | None = None,
        hilos: int | None = 1,
    ) -> None:
        from fastembed import TextEmbedding

        self.modelo = modelo
        # `threads` limita los hilos del runtime de ONNX. Sin ese límite el
        # runtime abre un hilo por núcleo del host: sobre la cuota de 2 CPU de
        # la planta el indexado del manual le roba la CPU al ASR y cada
        # transcripción se multiplica por veinte.
        self._modelo = TextEmbedding(
            model_name=modelo,
            cache_dir=cache_dir,
            threads=hilos,
        )
        self._lock = threading.Lock()
        self.dimension = len(self._embed_bruto(["dimensión"])[0])

    def embed(self, textos: Sequence[str]) -> list[list[float]]:
        if not textos:
            return []
        return self._embed_bruto(textos)

    def _embed_bruto(self, textos: Sequence[str]) -> list[list[float]]:
        with self._lock:
            vectores = [
                [float(valor) for valor in vector]
                for vector in self._modelo.embed(list(textos), batch_size=64)
            ]
        return [_normalizar_vector(vector) for vector in vectores]


def _caracteristicas(texto: str) -> dict[str, float]:
    """Características y peso de un texto para el feature hashing."""
    normalizado = normalizar(texto)
    terminos = tokenizar(normalizado)
    pesos: Counter[str] = Counter()
    for termino in terminos:
        pesos[f"w:{termino}"] += 1.0
        if len(termino) > 3:
            relleno = f"#{termino}#"
            for indice in range(len(relleno) - 3):
                pesos[f"c:{relleno[indice:indice + 4]}"] += 0.35
    for primero, segundo in zip(terminos, terminos[1:], strict=False):
        pesos[f"b:{primero}_{segundo}"] += 0.6
    return {caracteristica: 1.0 + math.log(cantidad) for caracteristica, cantidad in pesos.items()}


def crear_embedder(
    tipo: str | None = None,
    *,
    modelo: str | None = None,
    dimension: int = DIMENSION_POR_DEFECTO,
    cache_dir: str | None = None,
    estricto: bool = False,
    hilos: int | None = 1,
) -> Embedder:
    """Crea el embedder pedido.

    Con ``estricto=False`` (el valor de las tools), si ``fastembed`` no está
    instalado se usa el embedding determinista y se deja un aviso. El indexador
    usa ``estricto=True``: no tiene sentido indexar con una dimensión distinta a
    la que después se consulta.

    ``hilos`` limita los hilos del runtime de ONNX (ver ``EmbedderFastEmbed``).
    """
    elegido = (tipo or "hashing").strip().lower()
    if elegido in {"hashing", "local", "determinista"}:
        return EmbedderHashing(dimension)
    if elegido in {"fastembed", "auto"}:
        try:
            return EmbedderFastEmbed(
                modelo or MODELO_POR_DEFECTO,
                cache_dir=cache_dir,
                hilos=hilos,
            )
        except Exception as exc:  # noqa: BLE001 - el modelo es opcional en tests
            if estricto:
                raise
            logger.warning(
                "No se pudo cargar fastembed (%s); se usa el embedding determinista", exc
            )
            return EmbedderHashing(dimension)
    raise ValueError(f"Embedder desconocido: {tipo!r}")
