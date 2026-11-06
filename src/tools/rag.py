"""Tool de búsqueda híbrida en los manuales (Fase 4).

Mantiene un recuperador por cliente HTTP. La primera consulta arma el índice
con el corpus que sirve la planta (manuales e historial) y las siguientes
reutilizan los embeddings. Si el índice no se puede armar, cae a la búsqueda
léxica de la Fase 3 en vez de dejar al operador sin respuesta.
"""

from __future__ import annotations

import logging
import threading
import weakref

from tools.cliente import ClientePlanta
from tools.manuales import busqueda_manuales
from tools.modelos import ConsultaManuales, FragmentoManual

logger = logging.getLogger("tools.rag")

_cache: weakref.WeakKeyDictionary[ClientePlanta, object] = weakref.WeakKeyDictionary()
_lock = threading.Lock()


def crear_recuperador(cliente: ClientePlanta) -> object:
    """Crea el recuperador configurado.

    La importación es perezosa a propósito: ``rag.retriever`` importa
    ``tools.cliente`` y el registro de tools importa este módulo, así que un
    import de ``rag`` en la cabecera cierra un ciclo al cargar ``tools``.
    """
    from rag.retriever import crear_recuperador as crear

    return crear(cliente)


def busqueda_rag(cliente: ClientePlanta, consulta: ConsultaManuales) -> list[FragmentoManual]:
    """Tool ``busqueda_rag``: fragmentos con cita, ranking híbrido."""
    try:
        recuperador = _recuperador(cliente)
        return recuperador.buscar(consulta)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - la búsqueda léxica es el respaldo
        logger.warning("RAG no disponible (%s); se usa la búsqueda léxica", exc)
        return busqueda_manuales(cliente, consulta)


def _recuperador(cliente: ClientePlanta) -> object:
    with _lock:
        existente = _cache.get(cliente)
        if existente is not None:
            return existente
        recuperador = crear_recuperador(cliente)
        _cache[cliente] = recuperador
        return recuperador
