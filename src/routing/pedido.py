"""Definición de un pedido que el router evalúa para elegir un modelo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Pedido:
    """Un pedido de inferencia que el router debe atender con un modelo.

    ``tipo_tarea`` clasifica la intención del pedido: clasificar, extraer,
    chat, razonar o vision. El resto de campos son señales que el router
    usa para decidir qué modelo de la lista cerrada es el más adecuado.
    """

    tipo_tarea: Literal["clasificar", "extraer", "chat", "razonar", "vision"]
    tiene_imagen: bool
    tokens_estimados: int
    requiere_tools: bool = False
    cliente_plan: str | None = None
    presupuesto_restante_usd: float | None = None

    @property
    def necesita_vision(self) -> bool:
        """True si la entrada incluye una imagen que requiere percepción multimodal."""
        return self.tiene_imagen or self.tipo_tarea == "vision"

    @property
    def necesita_ventana_grande(self) -> bool:
        """True si el input es largo y puede superar la ventana del modelo local."""
        return self.tokens_estimados > 100_000
