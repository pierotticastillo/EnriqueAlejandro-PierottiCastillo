"""Medición del router (Ítem 3.6 del Plan de implementación 3).

Registra el modelo elegido, el motivo, el costo y la resolución por cada
request del router, para poder comparar contra una línea base:

* **Siempre el grande**: todas las consultas van al modelo más capaz (y caro).
* **Siempre el económico**: todas las consultas van al modelo más barato.
* **Router con cascada**: barato primero, caro si el validador rechaza.

El objetivo es verificar que el router ahorra costo sin perder resolución,
como sugiere la presentación "Capability Matrix y Model Routing" (Semana_5,
Unidad 2, página 13).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class MedicionRouter:
    """Una medición de una decisión de routing."""

    timestamp: str
    pedido_id: str
    modelo_elegido: str
    motivo: str
    costo_usd: float
    resuelto: bool
    estrategia: Literal["reglas", "clasificacion", "capacidad", "latencia", "fallback"]
    llamadas: int = 1  # 1 si solo se usó un modelo, 2 si hubo cascada


@dataclass
class RegistroMetricasRouter:
    """Registro en memoria de todas las mediciones del router.

    Se puede persistir en disco o enviar a un sistema de observabilidad
    externo. Por simplicidad se mantiene en memoria durante la ejecución.
    """

    mediciones: list[MedicionRouter] = field(default_factory=list)

    def registrar(
        self,
        *,
        pedido_id: str,
        modelo_elegido: str,
        motivo: str,
        costo_usd: float,
        resuelto: bool,
        estrategia: Literal["reglas", "clasificacion", "capacidad", "latencia", "fallback"],
        llamadas: int = 1,
    ) -> None:
        """Agrega una medición al registro."""
        medicion = MedicionRouter(
            timestamp=datetime.now().isoformat(),
            pedido_id=pedido_id,
            modelo_elegido=modelo_elegido,
            motivo=motivo,
            costo_usd=costo_usd,
            resuelto=resuelto,
            estrategia=estrategia,
            llamadas=llamadas,
        )
        self.mediciones.append(medicion)

    def resumen(self) -> dict[str, object]:
        """Devuelve un resumen agregado de todas las mediciones."""
        total = len(self.mediciones)
        if total == 0:
            return {
                "total_pedidos": 0,
                "costo_total_usd": 0.0,
                "costo_promedio_usd": 0.0,
                "resueltos": 0,
                "tasa_resolucion": 0.0,
                "por_modelo": {},
                "por_estrategia": {},
            }

        costo_total = sum(m.costo_usd for m in self.mediciones)
        resueltos = sum(1 for m in self.mediciones if m.resuelto)

        por_modelo: dict[str, dict[str, object]] = {}
        por_estrategia: dict[str, dict[str, object]] = {}

        for m in self.mediciones:
            entry_modelo = por_modelo.setdefault(
                m.modelo_elegido, {"cantidad": 0, "costo_usd": 0.0, "resueltos": 0}
            )
            entry_modelo["cantidad"] += 1
            entry_modelo["costo_usd"] += m.costo_usd
            if m.resuelto:
                entry_modelo["resueltos"] += 1

            entry_est = por_estrategia.setdefault(
                m.estrategia, {"cantidad": 0, "costo_usd": 0.0, "resueltos": 0}
            )
            entry_est["cantidad"] += 1
            entry_est["costo_usd"] += m.costo_usd
            if m.resuelto:
                entry_est["resueltos"] += 1

        return {
            "total_pedidos": total,
            "costo_total_usd": round(costo_total, 4),
            "costo_promedio_usd": round(costo_total / total, 4),
            "resueltos": resueltos,
            "tasa_resolucion": round(resueltos / total, 4),
            "por_modelo": por_modelo,
            "por_estrategia": por_estrategia,
        }

    def comparar_con_linea_base(
        self,
        costo_grande_por_pedido: float,
        costo_economico_por_pedido: float,
        resolucion_grande: float,
        resolucion_economico: float,
    ) -> dict[str, object]:
        """Compara el router contra dos líneas base simples.

        * Línea base "siempre el grande": todos los pedidos al modelo caro.
        * Línea base "siempre el económico": todos los pedidos al modelo barato.

        Devuelve el ahorro de costo y la diferencia de resolución del router
        contra cada línea base.
        """
        resumen_router = self.resumen()
        total = resumen_router["total_pedidos"]
        if total == 0:
            return {
                "linea_base_grande": {
                    "costo_total_usd": 0.0,
                    "resueltos": 0,
                    "tasa_resolucion": resolucion_grande,
                },
                "linea_base_economico": {
                    "costo_total_usd": 0.0,
                    "resueltos": 0,
                    "tasa_resolucion": resolucion_economico,
                },
                "ahorro_vs_grande_usd": 0.0,
                "ahorro_vs_grande_pct": 0.0,
                "diferencia_resolucion_vs_grande": 0.0,
                "ahorro_vs_economico_usd": 0.0,
                "ahorro_vs_economico_pct": 0.0,
                "diferencia_resolucion_vs_economico": 0.0,
            }

        costo_router = resumen_router["costo_total_usd"]
        resueltos_router = resumen_router["resueltos"]
        tasa_router = resumen_router["tasa_resolucion"]

        costo_grande_total = costo_grande_por_pedido * total
        resueltos_grande = int(resolucion_grande * total)

        costo_economico_total = costo_economico_por_pedido * total
        resueltos_economico = int(resolucion_economico * total)

        ahorro_vs_grande = costo_grande_total - costo_router
        ahorro_vs_economico = costo_economico_total - costo_router

        return {
            "linea_base_grande": {
                "costo_total_usd": round(costo_grande_total, 4),
                "costo_promedio_usd": round(costo_grande_por_pedido, 4),
                "resueltos": resueltos_grande,
                "tasa_resolucion": resolucion_grande,
            },
            "linea_base_economico": {
                "costo_total_usd": round(costo_economico_total, 4),
                "costo_promedio_usd": round(costo_economico_por_pedido, 4),
                "resueltos": resueltos_economico,
                "tasa_resolucion": resolucion_economico,
            },
            "router": {
                "costo_total_usd": costo_router,
                "costo_promedio_usd": resumen_router["costo_promedio_usd"],
                "resueltos": resueltos_router,
                "tasa_resolucion": tasa_router,
            },
            "ahorro_vs_grande_usd": round(ahorro_vs_grande, 4),
            "ahorro_vs_grande_pct": round(ahorro_vs_grande / costo_grande_total * 100, 2)
            if costo_grande_total > 0
            else 0.0,
            "diferencia_resolucion_vs_grande": round(
                tasa_router - resolucion_grande, 4
            ),
            "ahorro_vs_economico_usd": round(ahorro_vs_economico, 4),
            "ahorro_vs_economico_pct": round(
                ahorro_vs_economico / costo_economico_total * 100, 2
            )
            if costo_economico_total > 0
            else 0.0,
            "diferencia_resolucion_vs_economico": round(
                tasa_router - resolucion_economico, 4
            ),
        }

    def exportar_json(self) -> str:
        """Exporta todas las mediciones como JSON."""
        import json

        return json.dumps(
            [
                {
                    "timestamp": m.timestamp,
                    "pedido_id": m.pedido_id,
                    "modelo_elegido": m.modelo_elegido,
                    "motivo": m.motivo,
                    "costo_usd": m.costo_usd,
                    "resuelto": m.resuelto,
                    "estrategia": m.estrategia,
                    "llamadas": m.llamadas,
                }
                for m in self.mediciones
            ],
            indent=2,
            ensure_ascii=False,
        )


#: Instancia global del registro de métricas (singleton simple).
registro_metricas_router = RegistroMetricasRouter()
