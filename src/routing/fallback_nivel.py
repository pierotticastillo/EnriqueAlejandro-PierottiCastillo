"""Niveles de la cadena de fallback (Ítem 3.11 del Plan de implementación 3).

La presentación de Semana_5 sugiere una **cadena de fallback de 5 niveles** que
se dispara ante errores 429/5xx/timeout/filtro de contenido:

1. **Reintentar** con backoff, pocas veces
2. **Otra región** u otro proveedor, mismo modelo
3. **Modelo alternativo** de capacidad similar
4. **Modelo chico** o local
5. **Degradar** respuesta reducida

Cada nivel responde peor que el anterior, pero **responde**. Si se llega al
nivel 5, se avisa explícitamente (nunca degradar en silencio).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


#: Niveles de la cadena, en orden de ejecución.
NivelFallback = Literal[
    "reintentar",
    "otra_region",
    "modelo_alternativo",
    "modelo_chico",
    "degradar",
]

CADENA_COMPLETA: tuple[NivelFallback, ...] = (
    "reintentar",
    "otra_region",
    "modelo_alternativo",
    "modelo_chico",
    "degradar",
)


@dataclass(frozen=True)
class ResultadoNivel:
    """Resultado de intentar un nivel de la cadena."""

    nivel: NivelFallback
    exito: bool
    respuesta: str | None = None
    motivo: str = ""
    error_original: str | None = None


@dataclass(frozen=True)
class ResultadoCadena:
    """Resultado completo de recorrer la cadena de fallback."""

    exito: bool
    nivel_alcanzado: NivelFallback | None
    respuesta: str | None = None
    niveles_probados: tuple[ResultadoNivel, ...] = ()
    motivo: str = ""

    @property
    def se_degrado(self) -> bool:
        """True si se llegó al nivel 5 (degradar)."""
        return self.nivel_alcanzado == "degradar"

    @property
    def cantidad_niveles_probados(self) -> int:
        return len(self.niveles_probados)