"""Estrategia de routing por reglas: condiciones fijas en código.

Es la estrategia más predecible y gratuita: no suma llamadas ni depende de un
modelo clasificador. Se recomienda como primera capa del router.
"""

from __future__ import annotations

from .pedido import Pedido

#: Claves de modelo de la lista cerrada.
MODELO_LLM = "llm"
MODELO_VISION = "vision"


def elegir_por_reglas(pedido: Pedido) -> str:
    """Devuelve la clave del modelo de la lista cerrada que debe atender el pedido.

    Reglas aplicadas en orden:

    1. Si trae imagen o la tarea es visión -> modelo de visión (NVIDIA Nemotron).
    2. Si el input supera los 100k tokens -> modelo de ventana grande (por ahora
       el mismo LLM local; se revisará al agregar modelos con ventana mayor).
    3. Si la tarea es clasificar o extraer -> modelo económico (por ahora el LLM
       local; se revisará al agregar un modelo dedicado).
    4. Cualquier otro caso -> modelo generalista/local (Qwen2.5:3b-instruct).
    """
    if pedido.necesita_vision:
        return MODELO_VISION
    if pedido.necesita_ventana_grande:
        return MODELO_LLM
    if pedido.tipo_tarea in ("clasificar", "extraer"):
        return MODELO_LLM
    return MODELO_LLM
