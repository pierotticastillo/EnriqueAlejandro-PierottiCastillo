"""Estrategia de routing por clasificación: un modelo chico etiqueta el pedido.

Cuando las reglas fijas no alcanzan para decidir, un modelo económico etiqueta
el pedido como "simple" o "complejo" y el router elige el modelo en función de
esa etiqueta. Esta estrategia suma una llamada al clasificador, así que se usa
solo cuando las reglas no son suficientes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .pedido import Pedido

#: Etiquetas de complejidad que el clasificador puede devolver.
Complejidad = Literal["simple", "complejo"]

#: Claves de modelo de la lista cerrada.
MODELO_ECONOMICO = "llm"
MODELO_RAZONAMIENTO = "llm"


@dataclass(frozen=True)
class ResultadoClasificacion:
    complejidad: Complejidad
    motivo: str


def etiquetar_complejidad(pedido: Pedido) -> ResultadoClasificacion:
    """Clasifica el pedido como simple o complejo usando señales ligeras.

    Esta es una implementación determinista sin LLM: se usa como respaldo hasta
    contar con un modelo clasificador dedicado en la lista cerrada. Devuelve
    ``complejo`` cuando hay imagen, muchos tokens, o la tarea es de razonamiento;
    en otro caso devuelve ``simple``.
    """
    motivos: list[str] = []

    if pedido.necesita_vision:
        motivos.append("trae imagen o tarea de visión")
    if pedido.necesita_ventana_grande:
        motivos.append("input supera 100k tokens")
    if pedido.tipo_tarea == "razonar":
        motivos.append("tarea de varios pasos")

    if motivos:
        return ResultadoClasificacion(
            complejidad="complejo",
            motivo="; ".join(motivos),
        )

    return ResultadoClasificacion(
        complejidad="simple",
        motivo="tarea simple sin imagen ni ventana grande",
    )


def modelo_por_complejidad(complejidad: Complejidad) -> str:
    """Devuelve la clave del modelo según la complejidad etiquetada."""
    if complejidad == "complejo":
        return MODELO_RAZONAMIENTO
    return MODELO_ECONOMICO
