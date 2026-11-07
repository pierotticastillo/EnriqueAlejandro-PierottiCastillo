"""Fase 5: análisis de anomalías, causas probables y borrador de orden.

El paquete arma el análisis de un escalado a partir de la evidencia de campo
(parte confirmado, fotos y tendencia de sensores), del manual de fallas
(parseado del corpus del RAG, con cita) y del plan de mantenimiento (MBP-4).
Las reglas de aprobación se evalúan en código, no por el modelo.
"""

from __future__ import annotations

from analisis.aprobacion import Contexto, evaluar
from analisis.catalogo import CatalogoManuales
from analisis.causas import causalizar
from analisis.evidencia import Evidencia, reunir_parte, reunir_texto
from analisis.fallas import CatalogoFallas
from analisis.historial import es_reincidencia
from analisis.modelos import (
    Analisis,
    BorradorOrden,
    CausaProbable,
    Evaluacion,
    FallaCatalogo,
    Regla,
)
from analisis.orden import armar_borrador
from analisis.servicio import ServicioAnalisis

__all__ = [
    "Analisis",
    "BorradorOrden",
    "CausaProbable",
    "CatalogoFallas",
    "CatalogoManuales",
    "Contexto",
    "Evidencia",
    "Evaluacion",
    "FallaCatalogo",
    "Regla",
    "ServicioAnalisis",
    "armar_borrador",
    "causalizar",
    "es_reincidencia",
    "evaluar",
    "reunir_parte",
    "reunir_texto",
]
