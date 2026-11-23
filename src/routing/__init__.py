"""Model Routing: elegir en tiempo de ejecución qué modelo de la lista cerrada atiende cada pedido.

El router es **opcional**: si no está configurado, el sistema usa el comportamiento
actual (Qwen2.5:3b para lenguaje, Nemotron para visión). Todas las estrategias
operan exclusivamente sobre modelos de la lista cerrada (`src/modelos_lista.py`).
"""

from .capacidad import capacidad_filtrada
from .cascada import Cascada, ResultadoCascada
from .clasificador import etiquetar_complejidad
from .latency import proveedor_por_latencia
from .matriz_capacidades import (
    MATRIZ_CAPACIDADES,
    MODELO_ASR,
    MODELO_EMBEDDINGS,
    MODELO_LLM,
    MODELO_VISION,
    CapacidadModelo,
    modelo_por_clave,
    modelos_aptos_para,
    modelos_baratos,
    modelos_rapidos,
)
from .metricas import MedicionRouter, RegistroMetricasRouter, registro_metricas_router
from .pedido import Pedido
from .reglas import elegir_por_reglas
from .router import Router
from .validador import (
    ResultadoValidacion,
    obtener_validador,
    valida_json,
    valida_monto,
    valida_no_vacio,
    valida_tool_existe,
)

__all__ = [
    "Cascada",
    "CapacidadModelo",
    "MATRIZ_CAPACIDADES",
    "MedicionRouter",
    "MODELO_ASR",
    "MODELO_EMBEDDINGS",
    "MODELO_LLM",
    "MODELO_VISION",
    "Pedido",
    "RegistroMetricasRouter",
    "ResultadoCascada",
    "ResultadoValidacion",
    "Router",
    "capacidad_filtrada",
    "elegir_por_reglas",
    "etiquetar_complejidad",
    "modelo_por_clave",
    "modelos_aptos_para",
    "modelos_baratos",
    "modelos_rapidos",
    "obtener_validador",
    "proveedor_por_latencia",
    "registro_metricas_router",
    "valida_json",
    "valida_monto",
    "valida_no_vacio",
    "valida_tool_existe",
]
