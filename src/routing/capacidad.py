"""Filtros de capacidad: evita elegir modelos incapaces para el pedido.

Cruza el pedido con la matriz de capacidades (`docs/capability_matrix.md`)
y devuelve las claves de modelo que son aptas. Un modelo sin visión no puede
atender un pedido con imagen; un modelo con ventana chica no puede atender
más de 100k tokens; etc.
"""

from __future__ import annotations

from .pedido import Pedido

#: Claves de modelo de la lista cerrada.
MODELO_LLM = "llm"
MODELO_ECONOMICO = "economico"
MODELO_VISION = "vision"

#: Capacidades declaradas por modelo de la lista cerrada.
#: Se actualiza a medida que se agregan modelos (Ítem 3.3 Capability Matrix).
CAPACIDADES: dict[str, dict[str, object]] = {
    MODELO_LLM: {
        "vision": False,
        "ventana_grande": False,
        "tools": True,
        "razonamiento": "bajo",
        "ubicacion": "planta",
    },
    MODELO_ECONOMICO: {
        "vision": False,
        "ventana_grande": False,
        "tools": True,
        "razonamiento": "bajo",
        "ubicacion": "planta",
    },
    MODELO_VISION: {
        "vision": True,
        "ventana_grande": False,
        "tools": True,
        "razonamiento": "medio",
        "ubicacion": "nube",
    },
}


def capacidad_filtrada(pedido: Pedido) -> list[str]:
    """Devuelve las claves de modelo aptas para el pedido, ordenadas por preferencia.

    Un modelo queda excluido si no cumple una capacidad dura del pedido:
    - Imagen -> requiere ``vision=True``.
    - >100k tokens -> requiere ``ventana_grande=True``.
    """
    aptos: list[str] = []

    for modelo, caps in CAPACIDADES.items():
        if pedido.necesita_vision and not caps["vision"]:
            continue
        if pedido.necesita_ventana_grande and not caps["ventana_grande"]:
            continue
        aptos.append(modelo)

    return aptos
