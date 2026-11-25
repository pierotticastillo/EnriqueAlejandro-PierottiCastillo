"""Model Routing: elegir en tiempo de ejecución qué modelo de la lista cerrada atiende cada pedido.

El router es **opcional**: si no está configurado, el sistema usa el comportamiento
actual (Qwen2.5:3b para lenguaje, Nemotron para visión). Todas las estrategias
operan exclusivamente sobre modelos de la lista cerrada (`src/modelos_lista.py`).
"""

from .batching import BatchNoUrgente, ItemBatch, ResultadoBatch
from .cache_respuestas import (
    CacheRespuestas,
    ResultadoConsultaCache,
    cache_respuestas,
)
from .cache_tools import (
    CacheTools,
    ResultadoConsultaCacheTool,
    cache_tools,
)
from .cadena_fallback import CadenaFallback
from .capacidad import capacidad_filtrada
from .cascada import Cascada, ResultadoCascada
from .clasificador import etiquetar_complejidad
from .costo_resolucion import (
    CalculadoraCostoResolucion,
    ComparacionCostoResolucion,
    CostoPorResolucion,
    calculadora_costo_resolucion,
)
from .costos import PrecioModelo, TablaCostos, tabla_costos
from .degradacion import (
    NIVELES,
    NIVELES_DEGRADACION,
    NivelDegradacion,
    NivelDegradacionInfo,
    anterior_nivel,
    info_para_nivel,
    siguiente_nivel,
)
from .fallback_nivel import (
    CADENA_COMPLETA,
    NivelFallback,
    ResultadoCadena,
    ResultadoNivel,
)
from .gestor_degradacion import (
    GestorDegradacion,
    ResultadoDegradacion,
    gestor_degradacion,
)
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
from .metricas_tokens import (
    MetricaTokensPorSegundo,
    calcular_tokens_por_segundo,
    metricas_tokens_por_segundo,
    registrar_metrica_tokens,
    resumen_velocidad,
)
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
    "PrecioModelo",
    "RegistroMetricasRouter",
    "ResultadoCascada",
    "ResultadoValidacion",
    "Router",
    "TablaCostos",
    "CalculadoraCostoResolucion",
    "ComparacionCostoResolucion",
    "CostoPorResolucion",
    "CadenaFallback",
    "CADENA_COMPLETA",
    "NivelFallback",
    "ResultadoCadena",
    "ResultadoNivel",
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
    "tabla_costos",
    "calculadora_costo_resolucion",
    "MetricaTokensPorSegundo",
    "calcular_tokens_por_segundo",
    "registrar_metrica_tokens",
    "resumen_velocidad",
    "metricas_tokens_por_segundo",
    "BatchNoUrgente",
    "ItemBatch",
    "ResultadoBatch",
    "valida_json",
    "valida_monto",
    "valida_no_vacio",
    "valida_tool_existe",
    "CacheRespuestas",
    "ResultadoConsultaCache",
    "cache_respuestas",
    "CacheTools",
    "ResultadoConsultaCacheTool",
    "cache_tools",
    "NivelDegradacion",
    "NivelDegradacionInfo",
    "NIVELES",
    "NIVELES_DEGRADACION",
    "anterior_nivel",
    "siguiente_nivel",
    "info_para_nivel",
    "GestorDegradacion",
    "ResultadoDegradacion",
    "gestor_degradacion",
]
