"""Corpus documental de la planta, listo para indexar.

La planta sirve el mismo corpus por dos vías:

* ``GET /api/documentos`` y ``GET /api/documentos/{id}/secciones`` para el
  sistema en ejecución;
* los archivos ``indice.json``, ``fragmentos.jsonl`` y
  ``historial/ordenes.json`` para evaluar el retrieval sin levantar la planta.

Las dos vías producen las mismas estructuras (``DocumentoRag`` y ``ChunkRag``),
con la cita de cada fragmento y los metadatos que pide la Fase 4: equipo,
fabricante, documento, sección, cita y vigencia.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.cliente import ClientePlanta, ErrorHerramienta


@dataclass(frozen=True)
class ChunkRag:
    """Un fragmento citable del corpus."""

    seccion: str
    cita: str
    texto: str
    titulo: str | None = None
    pagina: int | None = None
    vigente: bool = True
    orden: int = 0

    @property
    def texto_busqueda(self) -> str:
        """Texto que se embebe: el título ayuda a desambiguar secciones cortas."""
        return f"{self.titulo or ''}\n{self.texto}".strip()


@dataclass(frozen=True)
class DocumentoRag:
    """Un documento con sus fragmentos y los metadatos de la fuente."""

    clave: str
    titulo: str
    tipo: str
    fabricante: str | None = None
    equipos: tuple[str, ...] = ()
    version: str | None = None
    vigente: bool = True
    chunks: tuple[ChunkRag, ...] = field(default_factory=tuple)

    @property
    def equipo(self) -> str | None:
        return ", ".join(self.equipos) or None


def _texto(valor: Any) -> str:
    return str(valor).strip() if valor is not None else ""


def _entero(valor: Any) -> int | None:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


class CorpusPlanta:
    """Corpus leído de la API de la planta."""

    def __init__(self, cliente: ClientePlanta) -> None:
        self._cliente = cliente

    def documentos(self) -> dict[str, dict[str, Any]]:
        datos = self._cliente.get("/api/documentos") or {}
        lista = datos.get("documentos") if isinstance(datos, dict) else datos
        return {
            str(documento.get("id")): documento
            for documento in lista or []
            if isinstance(documento, dict) and documento.get("id")
        }

    def secciones(self, documento: str) -> list[dict[str, Any]]:
        datos = self._cliente.get(f"/api/documentos/{documento}/secciones") or []
        return [seccion for seccion in datos if isinstance(seccion, dict)]

    def ordenes(self) -> list[dict[str, Any]]:
        try:
            datos = self._cliente.get("/api/historial-ordenes") or []
        except ErrorHerramienta:
            # El historial no es imprescindible para buscar en los manuales.
            return []
        return [orden for orden in datos if isinstance(orden, dict)]

    def cargar(self, *, incluir_historial: bool = True) -> list[DocumentoRag]:
        documentos = self.documentos()
        resultado: list[DocumentoRag] = []
        for clave, datos in documentos.items():
            if incluir_historial and (clave == "historial" or datos.get("tipo") == "historial"):
                # El historial se indexa desde `/api/historial-ordenes`, que
                # además trae las órdenes del escenario. Indexarlo también como
                # documento duplicaría las órdenes en la tabla.
                continue
            resultado.append(self._documento(clave, datos))
        if incluir_historial:
            historial = _documento_historial(self.ordenes())
            if historial.chunks:
                resultado.append(historial)
        return resultado

    def _documento(self, clave: str, datos: dict[str, Any]) -> DocumentoRag:
        vigente_documento = bool(datos.get("vigente", True))
        equipos = tuple(str(equipo) for equipo in datos.get("equipos") or [])
        chunks: list[ChunkRag] = []
        for orden, seccion in enumerate(self.secciones(clave)):
            texto = _texto(seccion.get("texto"))
            if not texto:
                continue
            numero = _texto(seccion.get("numero"))
            chunks.append(
                ChunkRag(
                    seccion=numero,
                    cita=_texto(seccion.get("id")) or f"{clave} §{numero}",
                    texto=texto,
                    titulo=_texto(seccion.get("titulo")) or None,
                    pagina=_entero(seccion.get("pagina")),
                    vigente=bool(seccion.get("vigente", vigente_documento)),
                    orden=orden,
                )
            )
        return DocumentoRag(
            clave=clave,
            titulo=_texto(datos.get("titulo")) or clave,
            tipo=_texto(datos.get("tipo")) or "documento",
            fabricante=_texto(datos.get("emisor")) or None,
            equipos=equipos,
            version=_texto(datos.get("revision")) or None,
            vigente=vigente_documento,
            chunks=tuple(chunks),
        )

class CorpusArchivos:
    """Corpus leído de una copia local de ``datos/documentos`` de la planta."""

    def __init__(self, base: str | Path) -> None:
        self._base = Path(base)

    def cargar(self, *, incluir_historial: bool = True) -> list[DocumentoRag]:
        indice = json.loads((self._base / "indice.json").read_text(encoding="utf-8"))
        fragmentos = [
            json.loads(linea)
            for linea in (self._base / "fragmentos.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if linea
        ]
        por_documento: dict[str, dict[str, dict[str, Any]]] = {}
        for fragmento in fragmentos:
            por_documento.setdefault(str(fragmento.get("doc")), {})[
                str(fragmento.get("numero"))
            ] = fragmento

        documentos: list[DocumentoRag] = []
        for datos in indice.get("documentos") or []:
            clave = _texto(datos.get("id"))
            # El historial del índice viene vacío: las órdenes se leen del
            # archivo (o de la API) y se agregan una sola vez más abajo.
            if not clave or clave == "historial":
                continue
            secciones = por_documento.get(clave, {})
            chunks: list[ChunkRag] = []
            for orden, seccion in enumerate(datos.get("secciones") or []):
                fragmento = secciones.get(_texto(seccion.get("numero"))) or {}
                texto = _texto(fragmento.get("texto"))
                if not texto:
                    continue
                numero = _texto(seccion.get("numero"))
                chunks.append(
                    ChunkRag(
                        seccion=numero,
                        cita=_texto(fragmento.get("id")) or f"{clave} §{numero}",
                        texto=texto,
                        titulo=_texto(seccion.get("titulo")) or None,
                        pagina=_entero(seccion.get("pagina")),
                        vigente=bool(fragmento.get("vigente", datos.get("vigente", True))),
                        orden=orden,
                    )
                )
            documentos.append(
                DocumentoRag(
                    clave=clave,
                    titulo=_texto(datos.get("titulo")) or clave,
                    tipo=_texto(datos.get("tipo")) or "documento",
                    fabricante=_texto(datos.get("emisor")) or None,
                    equipos=tuple(str(equipo) for equipo in datos.get("equipos") or []),
                    version=_texto(datos.get("revision")) or None,
                    vigente=bool(datos.get("vigente", True)),
                    chunks=tuple(chunks),
                )
            )
        if incluir_historial:
            archivo = self._base / "historial" / "ordenes.json"
            if archivo.exists():
                ordenes = json.loads(archivo.read_text(encoding="utf-8"))
                historial = _documento_historial(ordenes)
                if historial.chunks:
                    documentos.append(historial)
        return documentos


def _documento_historial(ordenes: list[dict[str, Any]]) -> DocumentoRag:
    chunks: list[ChunkRag] = []
    vistos: set[str] = set()
    for indice, orden in enumerate(ordenes):
        identificador = _texto(orden.get("id"))
        if not identificador or identificador in vistos:
            # La planta puede devolver la misma orden en el archivo histórico y
            # en el precalentamiento del escenario: se indexa una sola vez.
            continue
        vistos.add(identificador)
        chunks.append(
            ChunkRag(
                seccion=identificador,
                cita=f"historial {identificador}",
                texto=_texto_orden(orden),
                titulo=_titulo_orden(orden),
                vigente=True,
                orden=indice,
            )
        )
    return DocumentoRag(
        clave="historial",
        titulo="Historial de órdenes de trabajo",
        tipo="historial",
        fabricante="PIA-3",
        chunks=tuple(chunks),
    )


def _texto_orden(orden: dict[str, Any]) -> str:
    tareas = " ".join(str(tarea) for tarea in orden.get("tareas") or [])
    repuestos = " ".join(
        f"{repuesto.get('codigo', '')} x{repuesto.get('cantidad', '')}"
        for repuesto in orden.get("repuestos") or []
        if isinstance(repuesto, dict)
    )
    partes = [
        _texto(orden.get("id")),
        _texto(orden.get("fecha_aviso")),
        _texto(orden.get("equipo")),
        _texto(orden.get("tipo")),
        tareas,
        _texto(orden.get("codigo_falla")),
        _texto(orden.get("aviso")),
        _texto(orden.get("trabajo_realizado")),
        repuestos,
        _texto(orden.get("tecnico")),
    ]
    return " | ".join(parte for parte in partes if parte)


def _titulo_orden(orden: dict[str, Any]) -> str:
    fecha = _texto(orden.get("fecha_aviso"))[:10]
    return " · ".join(
        parte
        for parte in (
            _texto(orden.get("equipo")),
            fecha,
            _texto(orden.get("tipo")),
            _texto(orden.get("id")),
        )
        if parte
    )
