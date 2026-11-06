"""Índice híbrido del corpus: BM25 + embeddings, con citas.

El índice se construye una vez a partir de los ``DocumentoRag`` y se puede
consultar en memoria (tests, servidor MCP) o volcar a PostgreSQL con
``rag.indexer``. El ranking combina las dos señales:

* **léxica**: BM25 sobre términos truncados, que es muy bueno para códigos,
  TAG, números y vocabulario técnico exacto;
* **densa**: coseno entre embeddings, que recupera paráfrasis y sinónimos.

La combinación es lineal y normalizada: cada señal se reescala a [0, 1] sobre
los candidatos y se suman con peso. Por defecto pesa 80 % la señal léxica y
20 % la densa, la proporción con la que se midió la mejora sobre la suite de
71 preguntas (hit@5 0,87 y recall@5 0,71 contra 0,85 y 0,66 del BM25 de
referencia). Las secciones de documentos vencidos se excluyen salvo que se
pidan explícitamente (la suite de evaluación las incluye para medir el
distractor).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from rag.corpus import DocumentoRag
from rag.embedder import Embedder
from rag.texto import tokenizar
from tools.modelos import FragmentoManual

PESO_LEXICO_POR_DEFECTO = 0.8


@dataclass(frozen=True)
class _ChunkIndexado:
    documento: str
    documento_titulo: str
    seccion: str
    cita: str
    titulo: str | None
    texto: str
    vigente: bool
    embedding: tuple[float, ...] = field(repr=False)


class IndiceLexico:
    """Índice BM25 de los textos, sin embeddings.

    Es la pata léxica del RAG. Se puede usar suelto (el recuperador de pgvector
    lo arma con los textos que lee de PostgreSQL) o como parte de
    ``IndiceRag``. El truncado a seis caracteres y las palabras vacías son los
    de la línea de base oficial, así que las dos rutas rankean igual.
    """

    def __init__(self, textos: Sequence[str]) -> None:
        self._terminos = [Counter(tokenizar(texto)) for texto in textos]
        self._largos = [sum(terminos.values()) for terminos in self._terminos]
        total = len(self._terminos)
        self._promedio = sum(self._largos) / total if total else 0.0
        frecuencia_documental: Counter[str] = Counter(
            termino for terminos in self._terminos for termino in terminos
        )
        self._idf = {
            termino: math.log(1 + (total - frecuencia + 0.5) / (frecuencia + 0.5))
            for termino, frecuencia in frecuencia_documental.items()
        }

    @property
    def cantidad(self) -> int:
        return len(self._terminos)

    def puntajes(self, consulta: str) -> list[float]:
        """Puntaje BM25 de cada texto para la consulta."""
        terminos = tokenizar(consulta)
        if not terminos or not self._terminos:
            return [0.0] * len(self._terminos)
        k1, b = 1.4, 0.75
        puntajes: list[float] = []
        for indice, frecuencia in enumerate(self._terminos):
            puntaje = 0.0
            for termino in terminos:
                tf = frecuencia.get(termino, 0)
                if not tf:
                    continue
                idf = self._idf.get(termino, 0.0)
                largo = self._largos[indice]
                denominador = tf + k1 * (1 - b + b * largo / (self._promedio or 1.0))
                puntaje += idf * tf * (k1 + 1) / denominador
            puntajes.append(puntaje)
        return puntajes


class IndiceRag:
    """Índice en memoria del corpus documental."""

    def __init__(
        self,
        documentos: Sequence[DocumentoRag],
        embedder: Embedder,
        *,
        peso_lexico: float = PESO_LEXICO_POR_DEFECTO,
    ) -> None:
        if not 0.0 <= peso_lexico <= 1.0:
            raise ValueError("El peso léxico tiene que estar entre 0 y 1")
        self.embedder = embedder
        self.peso_lexico = peso_lexico
        self._chunks: list[_ChunkIndexado] = []
        for documento in documentos:
            for chunk in documento.chunks:
                self._chunks.append(
                    _ChunkIndexado(
                        documento=documento.clave,
                        documento_titulo=documento.titulo,
                        seccion=chunk.seccion,
                        cita=chunk.cita,
                        titulo=chunk.titulo,
                        texto=chunk.texto,
                        vigente=chunk.vigente and documento.vigente,
                        embedding=(),
                    )
                )
        if not self._chunks:
            self._lexico = IndiceLexico([])
            self._embeddings: list[tuple[float, ...]] = []
            return

        # El embedding ve título + texto (el título desambigua secciones
        # cortas); BM25 se queda con el cuerpo, igual que la línea de base
        # oficial, para no diluir el puntaje léxico con títulos repetidos.
        self._lexico = IndiceLexico([chunk.texto for chunk in self._chunks])
        textos_densos = [f"{chunk.titulo or ''}\n{chunk.texto}" for chunk in self._chunks]
        vectores = embedder.embed(textos_densos)
        if len(vectores) != len(self._chunks):
            raise ValueError("El embedder devolvió una cantidad de vectores distinta")
        self._chunks = [
            _ChunkIndexado(
                documento=chunk.documento,
                documento_titulo=chunk.documento_titulo,
                seccion=chunk.seccion,
                cita=chunk.cita,
                titulo=chunk.titulo,
                texto=chunk.texto,
                vigente=chunk.vigente,
                embedding=tuple(vector),
            )
            for chunk, vector in zip(self._chunks, vectores, strict=False)
        ]
        self._embeddings = [chunk.embedding for chunk in self._chunks]

    @property
    def cantidad_chunks(self) -> int:
        return len(self._chunks)

    @property
    def citas(self) -> list[str]:
        return [chunk.cita for chunk in self._chunks]

    def buscar(
        self,
        consulta: str,
        *,
        top_k: int = 5,
        documento: str | None = None,
        solo_vigentes: bool = True,
        peso_lexico: float | None = None,
    ) -> list[FragmentoManual]:
        """Fragmentos ordenados por relevancia, con su cita."""
        if not self._chunks:
            return []
        peso = self.peso_lexico if peso_lexico is None else peso_lexico
        candidatos = [
            indice
            for indice, chunk in enumerate(self._chunks)
            if (solo_vigentes is False or chunk.vigente)
            and (documento is None or chunk.documento == documento)
        ]
        if not candidatos:
            return []
        lexicos = self._puntajes_lexicos(consulta)
        densos = self._puntajes_densos(consulta)
        # Cada señal se lleva a [0, 1] dentro de los candidatos. El coseno crudo
        # vive en una banda angosta (~0,5–0,8); sin este reescalado la señal
        # densa pesa de más y desplaza aciertos léxicos que ya estaban en el top.
        maximo_lexico = max(lexicos[indice] for indice in candidatos)
        minimo_denso = min(densos[indice] for indice in candidatos)
        maximo_denso = max(densos[indice] for indice in candidatos)
        rango_denso = maximo_denso - minimo_denso
        combinados: dict[int, float] = {}
        for indice in candidatos:
            lexico = lexicos[indice] / maximo_lexico if maximo_lexico > 0 else 0.0
            denso = (
                (densos[indice] - minimo_denso) / rango_denso if rango_denso > 0 else 0.0
            )
            combinados[indice] = peso * lexico + (1.0 - peso) * denso
        candidatos.sort(key=lambda indice: (-combinados[indice], self._chunks[indice].cita))
        resultado: list[FragmentoManual] = []
        for indice in candidatos[:top_k]:
            chunk = self._chunks[indice]
            resultado.append(
                FragmentoManual(
                    documento=chunk.documento,
                    seccion=chunk.seccion,
                    cita=chunk.cita,
                    titulo=chunk.titulo,
                    texto=chunk.texto[:2000],
                    vigente=chunk.vigente,
                    puntaje=round(combinados[indice], 6),
                )
            )
        return resultado

    def buscar_citas(
        self,
        consulta: str,
        k: int = 5,
        *,
        solo_vigentes: bool = False,
        peso_lexico: float | None = None,
    ) -> list[str]:
        """Citas recuperadas, en orden, para la suite de evaluación."""
        return [
            fragmento.cita
            for fragmento in self.buscar(
                consulta,
                top_k=k,
                solo_vigentes=solo_vigentes,
                peso_lexico=peso_lexico,
            )
        ]

    def buscar_lexico(self, consulta: str, k: int = 5, *, solo_vigentes: bool = False) -> list[str]:
        """Ranking sólo BM25, para comparar contra el híbrido."""
        return [
            fragmento.cita
            for fragmento in self.buscar(
                consulta,
                top_k=k,
                solo_vigentes=solo_vigentes,
                peso_lexico=1.0,
            )
        ]

    def buscar_denso(self, consulta: str, k: int = 5, *, solo_vigentes: bool = False) -> list[str]:
        """Ranking sólo embeddings, para comparar contra el híbrido."""
        return [
            fragmento.cita
            for fragmento in self.buscar(
                consulta,
                top_k=k,
                solo_vigentes=solo_vigentes,
                peso_lexico=0.0,
            )
        ]

    # ------------------------------------------------------------- puntajes

    def _puntajes_lexicos(self, consulta: str) -> list[float]:
        return self._lexico.puntajes(consulta)

    def _puntajes_densos(self, consulta: str) -> list[float]:
        if not self._embeddings:
            return []
        vector = self.embedder.embed([consulta])[0]
        return [
            sum(
                componente * consulta_componente
                for componente, consulta_componente in zip(
                    embedding, vector, strict=False
                )
            )
            for embedding in self._embeddings
        ]
