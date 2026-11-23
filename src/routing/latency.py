"""Estrategia de routing por latencia: elige el modelo que responde más rápido.

Mantiene un historial simple de tiempos de respuesta por modelo y devuelve el
que tenga menor latencia promedio en los últimos N minutos. Se usa cuando hay
varios modelos aptos para el mismo pedido (tras filtrar por capacidad).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

#: Claves de modelo de la lista cerrada.
MODELO_LLM = "llm"
MODELO_VISION = "vision"

#: Ventana de tiempo para medir latencia (últimos 5 minutos).
VENTANA_MINUTOS = 5


@dataclass(frozen=True)
class MedicionLatencia:
    modelo: str
    segundos: float
    timestamp: datetime


class HistorialLatencia:
    """Registro de mediciones de latencia por modelo."""

    def __init__(self) -> None:
        self._mediciones: dict[str, deque[MedicionLatencia]] = {
            MODELO_LLM: deque(),
            MODELO_VISION: deque(),
        }

    def registrar(self, modelo: str, segundos: float) -> None:
        """Agrega una medición de latencia para un modelo."""
        ahora = datetime.now()
        medicion = MedicionLatencia(modelo=modelo, segundos=segundos, timestamp=ahora)
        cola = self._mediciones.setdefault(modelo, deque())
        cola.append(medicion)
        self._purgar_viejas(cola, ahora)

    def promedio_segundos(self, modelo: str) -> float | None:
        """Devuelve la latencia promedio del modelo en la ventana de tiempo."""
        ahora = datetime.now()
        cola = self._mediciones.get(modelo)
        if not cola:
            return None
        self._purgar_viejas(cola, ahora)
        if not cola:
            return None
        return sum(m.segundos for m in cola) / len(cola)

    def _purgar_viejas(self, cola: deque[MedicionLatencia], ahora: datetime) -> None:
        corte = ahora - timedelta(minutes=VENTANA_MINUTOS)
        while cola and cola[0].timestamp < corte:
            cola.popleft()


def proveedor_por_latencia(
    candidatos: list[str],
    historial: HistorialLatencia,
) -> str:
    """Devuelve el candidato con menor latencia promedio reciente.

    Si no hay mediciones para ninguno, devuelve el primero de la lista.
    """
    mejor: str | None = None
    mejor_latencia: float | None = None

    for modelo in candidatos:
        lat = historial.promedio_segundos(modelo)
        if lat is None:
            # Sin mediciones: asumir latencia aceptable y seguir.
            return modelo
        if mejor_latencia is None or lat < mejor_latencia:
            mejor = modelo
            mejor_latencia = lat

    return mejor or candidatos[0]
