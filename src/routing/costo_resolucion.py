"""Costo por resolución (Ítem 3.8 del Plan de implementación 3).

El costo por resolución es el **costo total dividido por las tareas
efectivamente resueltas**. Es la métrica que sugiere la presentación de
Semana_5 para decidir entre un modelo barato que resuelve menos y uno caro
que resuelve más:

* Modelo barato: USD 0,02 por request, resuelve 60 % → USD 0,033 por resolución
* Modelo caro:  USD 0,08 por request, resuelve 85 % → USD 0,094 por resolución

El modelo barato puede parecer más barato por request, pero ser **más caro
por resolución**. Esta clase calcula esa métrica a partir de las mediciones
del router y permite comparar estrategias ("siempre el barato", "siempre el
caro", "router con cascada").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from src.routing.metricas import MedicionRouter, RegistroMetricasRouter


@dataclass(frozen=True)
class CostoPorResolucion:
    """Resultado del cálculo de costo por resolución para una estrategia."""

    estrategia: str
    costo_total_usd: float
    resueltos: int
    total_pedidos: int
    costo_por_resolucion_usd: float  # 0 si no hay resueltos
    tasa_resolucion: float  # resueltos / total_pedidos

    def __post_init__(self) -> None:
        if self.total_pedidos > 0:
            object.__setattr__(self, "tasa_resolucion", self.resueltos / self.total_pedidos)
        if self.resueltos > 0:
            object.__setattr__(self, "costo_por_resolucion_usd", self.costo_total_usd / self.resueltos)


@dataclass(frozen=True)
class ComparacionCostoResolucion:
    """Comparación de costo por resolución entre varias estrategias."""

    estrategias: list[CostoPorResolucion]
    mejor_estrategia: str  # la de menor costo por resolución

    def resumen(self) -> dict[str, object]:
        """Devuelve un diccionario con la comparación completa."""
        return {
            "mejor_estrategia": self.mejor_estrategia,
            "estrategias": [
                {
                    "estrategia": e.estrategia,
                    "costo_total_usd": round(e.costo_total_usd, 4),
                    "resueltos": e.resueltos,
                    "total_pedidos": e.total_pedidos,
                    "costo_por_resolucion_usd": round(e.costo_por_resolucion_usd, 6),
                    "tasa_resolucion": round(e.tasa_resolucion, 4),
                }
                for e in self.estrategias
            ],
        }


class CalculadoraCostoResolucion:
    """Calcula el costo por resolución a partir de mediciones del router."""

    def __init__(self, mediciones: Iterable[MedicionRouter] | None = None) -> None:
        self._mediciones: list[MedicionRouter] = list(mediciones or [])

    def agregar(self, medicion: MedicionRouter) -> None:
        """Agrega una medición al cálculo."""
        self._mediciones.append(medicion)

    def por_estrategia(self) -> list[CostoPorResolucion]:
        """Agrupa las mediciones por estrategia y calcula costo por resolución."""
        por_estrategia: dict[str, list[MedicionRouter]] = {}
        for m in self._mediciones:
            por_estrategia.setdefault(m.estrategia, []).append(m)

        resultados: list[CostoPorResolucion] = []
        for estrategia, meds in sorted(por_estrategia.items()):
            costo_total = sum(m.costo_usd for m in meds)
            resueltos = sum(1 for m in meds if m.resuelto)
            total = len(meds)
            resultados.append(
                CostoPorResolucion(
                    estrategia=estrategia,
                    costo_total_usd=costo_total,
                    resueltos=resueltos,
                    total_pedidos=total,
                    # costo_por_resolucion_usd y tasa_resolucion se calculan en __post_init__
                    costo_por_resolucion_usd=0.0,
                    tasa_resolucion=0.0,
                )
            )
        return resultados

    def comparar(self) -> ComparacionCostoResolucion:
        """Compara todas las estrategias y devuelve la mejor por costo por resolución."""
        estrategias = self.por_estrategia()
        if not estrategias:
            return ComparacionCostoResolucion(estrategias=[], mejor_estrategia="")

        # La mejor es la de menor costo por resolución (ignorando las que no resolvieron nada)
        resueltas = [e for e in estrategias if e.resueltos > 0]
        if resueltas:
            mejor = min(resueltas, key=lambda e: e.costo_por_resolucion_usd)
        else:
            mejor = estrategias[0]

        return ComparacionCostoResolucion(
            estrategias=list(estrategias),
            mejor_estrategia=mejor.estrategia,
        )

    @classmethod
    def desde_registro(
        cls, registro: RegistroMetricasRouter
    ) -> "CalculadoraCostoResolucion":
        """Crea una calculadora a partir de un RegistroMetricasRouter."""
        return cls(registro.mediciones)


#: Instancia global simple (opcional). Se prefiere inyectar la calculadora.
calculadora_costo_resolucion = CalculadoraCostoResolucion()
