"""Corpus de manuales de la planta y búsqueda de texto.

La planta sirve los manuales con metadatos y cita por sección
(``/api/documentos/{id}/secciones``). Este módulo los baja, los cachea y los
busca por palabras.

El ranking es **léxico** (solapamiento de términos con la consulta). Es el
andamiaje de la Fase 3; en la Fase 4 el mismo corpus se indexa con embeddings
en pgvector y la evaluación de retrieval mide si mejora. La búsqueda nunca
devuelve una sección sin cita: toda conclusión puede citarse.
"""

from __future__ import annotations

import re
import threading
from typing import Any

from tools.cliente import ClientePlanta
from tools.modelos import ConsultaManuales, FragmentoManual

# Documento de buenas prácticas: reglas de planta, tareas y repuestos.
DOCUMENTO_BUENAS_PRACTICAS = "MBP-4"

# Palabras vacías: no aportan a un ranking.
_STOPWORDS = {
    "a",
    "al",
    "como",
    "con",
    "cual",
    "de",
    "del",
    "el",
    "en",
    "es",
    "la",
    "las",
    "lo",
    "los",
    "para",
    "por",
    "que",
    "se",
    "sin",
    "un",
    "una",
    "y",
    "sobre",
    "hay",
    "tiene",
}


def normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, para comparar texto."""
    import unicodedata

    descompuesto = unicodedata.normalize("NFKD", texto)
    sin_tildes = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sin_tildes.lower()).strip()


def tokenizar(texto: str) -> list[str]:
    """Palabras útiles de un texto."""
    return [
        palabra
        for palabra in re.findall(r"[a-z0-9]+", normalizar(texto))
        if palabra not in _STOPWORDS and len(palabra) > 2
    ]


def numero(texto: str | None) -> float | None:
    """Número de un texto del manual: ``USD 1.900`` → 1900.0, ``0,5`` → 0.5."""
    if texto is None:
        return None
    limpio = texto.replace("USD", "").replace("$", "").replace(" ", "").strip()
    if not limpio:
        return None
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", limpio):
        limpio = limpio.replace(".", "")
    elif re.fullmatch(r"\d+,\d+", limpio):
        limpio = limpio.replace(",", ".")
    try:
        return float(limpio)
    except ValueError:
        return None


def tabla(texto: str) -> list[list[str]]:
    """Filas de una tabla de markdown del manual."""
    filas: list[list[str]] = []
    for linea in texto.splitlines():
        limpia = linea.strip()
        if "|" not in limpia:
            continue
        celdas = [celda.strip() for celda in limpia.strip("|").split("|")]
        if all(set(celda) <= {"-", ":", " "} for celda in celdas):
            continue
        filas.append(celdas)
    return filas


class CorpusManuales:
    """Documentos y secciones de la planta, con cache en memoria."""

    def __init__(self, cliente: ClientePlanta) -> None:
        self._cliente = cliente
        self._documentos: dict[str, dict[str, Any]] | None = None
        self._secciones: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def documentos(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            if self._documentos is None:
                datos = self._cliente.get("/api/documentos")
                lista = (datos or {}).get("documentos") or []
                self._documentos = {
                    str(doc.get("id")): doc
                    for doc in lista
                    if isinstance(doc, dict) and doc.get("id")
                }
            return self._documentos

    def secciones(self, doc_id: str) -> list[dict[str, Any]]:
        with self._lock:
            cacheadas = self._secciones.get(doc_id)
        if cacheadas is not None:
            return cacheadas
        datos = self._cliente.get(f"/api/documentos/{doc_id}/secciones")
        secciones = [s for s in (datos or []) if isinstance(s, dict)]
        with self._lock:
            self._secciones[doc_id] = secciones
        return secciones

    def seccion(self, doc_id: str, numero_seccion: str) -> dict[str, Any] | None:
        for seccion in self.secciones(doc_id):
            if str(seccion.get("numero")) == numero_seccion:
                return seccion
        return None

    def buscar(self, consulta: ConsultaManuales) -> list[FragmentoManual]:
        """Fragmentos que mejor responden a la consulta, con su cita."""
        terminos = tokenizar(consulta.consulta)
        if not terminos:
            return []
        documentos = self.documentos()
        ids = (
            [consulta.documento]
            if consulta.documento
            else [doc_id for doc_id in documentos if doc_id]
        )
        resultados: list[FragmentoManual] = []
        for doc_id in ids:
            vigente = bool((documentos.get(doc_id) or {}).get("vigente", True))
            for seccion in self.secciones(doc_id):
                texto = str(seccion.get("texto") or "")
                titulo = str(seccion.get("titulo") or "")
                puntaje = _puntaje(terminos, texto, titulo)
                if puntaje <= 0:
                    continue
                if not vigente:
                    # Una revisión vencida no es la fuente: se marca y baja.
                    puntaje *= 0.25
                resultados.append(
                    FragmentoManual(
                        documento=doc_id,
                        seccion=str(seccion.get("numero") or ""),
                        cita=str(seccion.get("id") or f"{doc_id} §{seccion.get('numero')}"),
                        titulo=titulo or None,
                        texto=texto[:2000],
                        vigente=vigente,
                        puntaje=round(puntaje, 4),
                    )
                )
        resultados.sort(key=lambda fragmento: fragmento.puntaje or 0, reverse=True)
        return resultados[: consulta.top_k]


def _puntaje(terminos: list[str], texto: str, titulo: str) -> float:
    """Solapamiento de términos: cada cuánto aparece y dónde."""
    palabras_texto = tokenizar(texto)
    palabras_titulo = set(tokenizar(titulo))
    if not palabras_texto:
        return 0.0
    conteo: dict[str, int] = {}
    for palabra in palabras_texto:
        conteo[palabra] = conteo.get(palabra, 0) + 1
    puntaje = 0.0
    for termino in terminos:
        apariciones = conteo.get(termino, 0)
        if apariciones == 0:
            # Coincidencia por prefijo: "válvula" ~ "válvulas".
            apariciones = sum(
                cantidad
                for palabra, cantidad in conteo.items()
                if palabra.startswith(termino) or termino.startswith(palabra)
            )
        if apariciones == 0:
            continue
        puntaje += 1.0 + min(apariciones - 1, 4) * 0.2
        if termino in palabras_titulo:
            puntaje += 0.8
    return puntaje / (1.0 + len(palabras_texto) / 400.0)


def busqueda_manuales(
    cliente: ClientePlanta,
    consulta: ConsultaManuales,
    *,
    corpus: CorpusManuales | None = None,
) -> list[FragmentoManual]:
    """Tool ``busqueda_manuales``: fragmentos de manual con cita."""
    corpus = corpus or CorpusManuales(cliente)
    return corpus.buscar(consulta)
