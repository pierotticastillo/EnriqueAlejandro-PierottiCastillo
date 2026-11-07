"""Historial de órdenes: si la falla se repite, la acción cambia.

La planta guarda dos años de órdenes de trabajo del archivo. Si el mismo
equipo vuelve a fallar con la misma falla **dentro del ciclo de mantenimiento**,
el manual cambia la recomendación: "si se repite en el mismo émbolo, inspeccionar
el émbolo" (TX350-D §8.3), "si la falla se repite pronto, revisar la filtración
aguas arriba antes de volver a cambiar" (TX350-D §8.4). La orden que sale del
historial suma entonces una tarea no estándar y va al planificador.

La ventana importa: una orden de hace dos años no es una reincidencia, es el
historial normal de un equipo que tiene dos años de archivo. Sin ventana, toda
orden del archivo convertiría en no estándar y el sistema no aprobaría nunca
nada solo, que es justo lo contrario de lo que pide el plan.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from analisis.modelos import FallaCatalogo
from rag.texto import tokenizar

logger = logging.getLogger("analisis.historial")

# El manual habla de una falla que "se repite" o "se repite pronto": dos
# intervenciones sobre el mismo equipo dentro del mismo ciclo de mantenimiento.
# Con 45 días se distingue la reincidencia del historial de fondo.
VENTANA_REINCIDENCIA_DIAS = 45

# Los estados en que una orden ya se atendió y puede volver a repetirse.
_ORDENES_CERRADAS = {"Cerrada", "Ejecutada", "Certificada", "Rechazada"}


def es_reincidencia(
    falla: FallaCatalogo | None,
    ordenes: list[dict],
    *,
    tarea_codigo: str | None = None,
    referencia: datetime | None = None,
    ventana_dias: int = VENTANA_REINCIDENCIA_DIAS,
) -> bool:
    """¿Esta falla ya se	atendió antes en este equipo, hace poco?

    Se buscan dos señales en las órdenes del equipo:

    1. que una orden anterior haya usado la misma tarea (la misma
       intervención), y
    2. que su texto de campo hable de los mismos síntomas.

    Sólo cuentan las órdenes **cerradas** y **recientes**: una orden vieja es
    parte del historial del equipo, no una reincidencia.
    """
    if falla is None:
        return False
    firma = set(tokenizar(falla.titulo)) | set(tokenizar(falla.sintoma))
    limite = _limite(referencia, ventana_dias)
    for orden in ordenes:
        if not isinstance(orden, dict):
            continue
        estado = str(orden.get("estado") or "").strip()
        if estado and estado not in _ORDENES_CERRADAS:
            # Una orden todavía abierta no se puede haber atendido.
            continue
        fecha = _fecha_de(orden)
        if limite is not None and (fecha is None or fecha < limite):
            # Vieja (o sin fecha): es historial de fondo, no una reincidencia.
            continue
        tareas = orden.get("tareas") or []
        if tarea_codigo and tarea_codigo in tareas:
            return True
        texto = " ".join(
            str(orden.get(campo) or "") for campo in ("aviso", "trabajo_realizado", "descripcion")
        )
        if not texto:
            continue
        if len(firma & set(tokenizar(texto))) >= 2:
            return True
    return False


def _limite(referencia: datetime | None, ventana_dias: int) -> datetime | None:
    """Desde cuándo una orden cuenta como reciente."""
    if referencia is None:
        return None
    return referencia - timedelta(days=ventana_dias)


def _fecha_de(orden: dict[str, Any]) -> datetime | None:
    """La fecha de una orden del historial de la planta."""
    for campo in ("fecha_aviso", "fecha", "fecha_inicio"):
        crudo = orden.get(campo)
        if not crudo:
            continue
        try:
            return datetime.fromisoformat(str(crudo))
        except ValueError:
            logger.debug("Fecha de orden ilegible: %r", crudo)
    return None
