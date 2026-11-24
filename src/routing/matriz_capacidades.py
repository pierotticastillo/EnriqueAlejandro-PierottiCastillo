"""Capability Matrix (Ítem 3.3 del Plan de implementación 3).

Matriz que cruza los modelos de la lista cerrada con las 8 dimensiones del
curso: tool calling, structured output, reasoning, visión, audio, contexto,
velocidad, costo.

**La ficha dice qué existe; la evaluación decide si alcanza.** Esta matriz es
la fuente de diseño; los valores medidos se actualizan a partir de los tests
de evaluación propia.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: Nivel de capacidad para dimensiones cualitativas.
NivelCapacidad = Literal["alto", "medio", "bajo", "nulo", "variable"]

#: Claves de modelo de la lista cerrada.
MODELO_LLM = "llm"
MODELO_VISION = "vision"
MODELO_ASR = "asr"
MODELO_EMBEDDINGS = "embeddings"


@dataclass(frozen=True)
class CapacidadModelo:
    """Capacidades de un modelo de la lista cerrada."""

    clave: str
    nombre: str
    ubicacion: str  # "planta" o "nube"
    tool_calling: NivelCapacidad
    structured_output: NivelCapacidad
    reasoning: NivelCapacidad
    vision: NivelCapacidad
    audio: NivelCapacidad
    contexto: str  # ej. "2048 tokens", "128k tokens"
    velocidad: NivelCapacidad  # alto = rápido, bajo = lento
    costo: NivelCapacidad  # alto = caro, bajo = barato
    notas: str = ""

    def apto_para(self, tarea: str) -> bool:
        """¿El modelo es apto para una tarea dada?

        Solo se rechaza si la capacidad es "nulo". Un nivel "bajo" significa
        que el modelo puede hacerlo, aunque no sea el óptimo.
        """
        if tarea == "vision" and self.vision == "nulo":
            return False
        if tarea == "razonar" and self.reasoning == "nulo":
            return False
        if tarea == "clasificar" and self.structured_output == "nulo":
            return False
        if tarea == "audio" and self.audio == "nulo":
            return False
        return True

    def es_barato(self) -> bool:
        """True si el modelo es económico para tareas simples en volumen."""
        return self.costo == "bajo"

    def es_rapido(self) -> bool:
        """True si el modelo responde con baja latencia."""
        return self.velocidad == "alto"


#: Matriz de capacidades de los modelos de la lista cerrada actual.
#: Los valores son la ficha del modelo. Se actualizan con mediciones propias.
MATRIZ_CAPACIDADES: dict[str, CapacidadModelo] = {
    MODELO_LLM: CapacidadModelo(
        clave=MODELO_LLM,
        nombre="Qwen2.5-3B-Instruct (Q4_K_M vía Ollama)",
        ubicacion="planta",
        tool_calling="medio",
        structured_output="medio",
        reasoning="bajo",
        vision="nulo",
        audio="nulo",
        contexto="2048 tokens",
        velocidad="medio",
        costo="bajo",
        notas=(
            "Modelo local sin GPU. Único que consulta tools nativamente dentro "
            "del presupuesto de 420 s."
        ),
    ),
    MODELO_VISION: CapacidadModelo(
        clave=MODELO_VISION,
        nombre="Nemotron 3 Nano Omni 30B A3B (API de NVIDIA)",
        ubicacion="nube",
        tool_calling="medio",
        structured_output="medio",
        reasoning="medio",
        vision="alto",
        audio="nulo",
        contexto="128k tokens",
        velocidad="alto",
        costo="medio",
        notas="Modelo de nube con visión. Fail-safe: salida indeterminada, nunca 'no hay daño'.",
    ),
    MODELO_ASR: CapacidadModelo(
        clave=MODELO_ASR,
        nombre="faster-whisper-base (CTranslate2)",
        ubicacion="planta",
        tool_calling="nulo",
        structured_output="nulo",
        reasoning="nulo",
        vision="nulo",
        audio="alto",
        contexto="según audio",
        velocidad="alto",
        costo="bajo",
        notas="Transcripción local, solo local, sin descarga en runtime.",
    ),
    MODELO_EMBEDDINGS: CapacidadModelo(
        clave=MODELO_EMBEDDINGS,
        nombre="paraphrase-multilingual-MiniLM-L12-v2 (384 dims)",
        ubicacion="planta",
        tool_calling="nulo",
        structured_output="nulo",
        reasoning="nulo",
        vision="nulo",
        audio="nulo",
        contexto="según texto",
        velocidad="alto",
        costo="bajo",
        notas="Embeddings para RAG y búsqueda semántica. Índice pgvector.",
    ),
}


def modelo_por_clave(clave: str) -> CapacidadModelo | None:
    """Devuelve las capacidades de un modelo por su clave."""
    return MATRIZ_CAPACIDADES.get(clave)


def modelos_aptos_para(tarea: str) -> list[str]:
    """Devuelve las claves de modelos aptos para una tarea."""
    return [clave for clave, cap in MATRIZ_CAPACIDADES.items() if cap.apto_para(tarea)]


def modelos_baratos() -> list[str]:
    """Devuelve las claves de modelos económicos."""
    return [clave for clave, cap in MATRIZ_CAPACIDADES.items() if cap.es_barato()]


def modelos_rapidos() -> list[str]:
    """Devuelve las claves de modelos rápidos."""
    return [clave for clave, cap in MATRIZ_CAPACIDADES.items() if cap.es_rapido()]
