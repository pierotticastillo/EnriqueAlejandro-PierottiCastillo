"""Tests para el costo por resolución (Ítem 3.8 del Plan de implementación 3)."""

from __future__ import annotations

import pytest
from src.routing.costo_resolucion import (
    CalculadoraCostoResolucion,
)
from src.routing.metricas import MedicionRouter


def _medicion(
    *,
    pedido_id: str,
    modelo_elegido: str,
    estrategia: str,
    costo_usd: float,
    resuelto: bool,
    llamadas: int = 1,
) -> MedicionRouter:
    return MedicionRouter(
        timestamp="2026-11-27T10:00:00",
        pedido_id=pedido_id,
        modelo_elegido=modelo_elegido,
        motivo="test",
        costo_usd=costo_usd,
        resuelto=resuelto,
        estrategia=estrategia,
        llamadas=llamadas,
    )


def test_costo_por_resolucion_modelo_barato_vs_caro():
    """El modelo barato puede ser más caro por resolución si resuelve poco.

    Modelo barato: USD 0,02 por request, resuelve 60 % → USD 0,033 por resolución
    Modelo caro:   USD 0,08 por request, resuelve 85 % → USD 0,094 por resolución
    """
    mediciones = []
    # 100 pedidos al modelo barato, 60 resueltos
    for i in range(60):
        mediciones.append(
            _medicion(
                pedido_id=f"b-{i}",
                modelo_elegido="barato",
                estrategia="clasificacion",
                costo_usd=0.02,
                resuelto=True,
            )
        )
    for i in range(40):
        mediciones.append(
            _medicion(
                pedido_id=f"b-{i + 60}",
                modelo_elegido="barato",
                estrategia="clasificacion",
                costo_usd=0.02,
                resuelto=False,
            )
        )

    # 100 pedidos al modelo caro, 85 resueltos
    for i in range(85):
        mediciones.append(
            _medicion(
                pedido_id=f"c-{i}",
                modelo_elegido="caro",
                estrategia="reglas",
                costo_usd=0.08,
                resuelto=True,
            )
        )
    for i in range(15):
        mediciones.append(
            _medicion(
                pedido_id=f"c-{i + 85}",
                modelo_elegido="caro",
                estrategia="reglas",
                costo_usd=0.08,
                resuelto=False,
            )
        )

    calc = CalculadoraCostoResolucion(mediciones)
    comp = calc.comparar()

    por_est = {e.estrategia: e for e in comp.estrategias}
    assert por_est["clasificacion"].costo_por_resolucion_usd == pytest.approx(0.033333, rel=1e-4)
    assert por_est["reglas"].costo_por_resolucion_usd == pytest.approx(0.094118, rel=1e-4)
    assert comp.mejor_estrategia == "clasificacion"


def test_costo_por_resolucion_sin_resueltos():
    """Si no hay resueltos, el costo por resolución es 0."""
    mediciones = [
        _medicion(
            pedido_id="p1",
            modelo_elegido="x",
            estrategia="clasificacion",
            costo_usd=0.02,
            resuelto=False,
        ),
        _medicion(
            pedido_id="p2",
            modelo_elegido="x",
            estrategia="clasificacion",
            costo_usd=0.02,
            resuelto=False,
        ),
    ]
    calc = CalculadoraCostoResolucion(mediciones)
    comp = calc.comparar()
    assert comp.estrategias[0].costo_por_resolucion_usd == 0.0
    assert comp.estrategias[0].tasa_resolucion == 0.0


def test_costo_por_resolucion_vacio():
    """Sin mediciones, la comparación está vacía."""
    calc = CalculadoraCostoResolucion()
    comp = calc.comparar()
    assert comp.estrategias == []
    assert comp.mejor_estrategia == ""


def test_costo_por_resolucion_unico_pedido_resuelto():
    """Un solo pedido resuelto: costo por resolución = costo del pedido."""
    mediciones = [
        _medicion(
            pedido_id="p1",
            modelo_elegido="x",
            estrategia="clasificacion",
            costo_usd=0.05,
            resuelto=True,
        ),
    ]
    calc = CalculadoraCostoResolucion(mediciones)
    comp = calc.comparar()
    assert comp.estrategias[0].costo_por_resolucion_usd == 0.05
    assert comp.estrategias[0].resueltos == 1
    assert comp.estrategias[0].total_pedidos == 1


def test_costo_por_resolucion_multiple_estrategias():
    """Varias estrategias se comparan correctamente."""
    mediciones = [
        _medicion(
            pedido_id="a1",
            modelo_elegido="a",
            estrategia="reglas",
            costo_usd=0.10,
            resuelto=True,
        ),
        _medicion(
            pedido_id="a2",
            modelo_elegido="a",
            estrategia="reglas",
            costo_usd=0.10,
            resuelto=True,
        ),
        _medicion(
            pedido_id="b1",
            modelo_elegido="b",
            estrategia="clasificacion",
            costo_usd=0.02,
            resuelto=True,
        ),
        _medicion(
            pedido_id="b2",
            modelo_elegido="b",
            estrategia="clasificacion",
            costo_usd=0.02,
            resuelto=False,
        ),
    ]
    calc = CalculadoraCostoResolucion(mediciones)
    comp = calc.comparar()

    por_est = {e.estrategia: e for e in comp.estrategias}
    assert por_est["reglas"].costo_por_resolucion_usd == 0.10  # 0.20 / 2
    assert por_est["clasificacion"].costo_por_resolucion_usd == 0.04  # 0.04 / 1
    assert comp.mejor_estrategia == "clasificacion"


def test_resumen_dict():
    """resumen() devuelve un diccionario con todos los campos."""
    mediciones = [
        _medicion(
            pedido_id="p1",
            modelo_elegido="x",
            estrategia="clasificacion",
            costo_usd=0.04,
            resuelto=True,
        ),
        _medicion(
            pedido_id="p2",
            modelo_elegido="x",
            estrategia="clasificacion",
            costo_usd=0.04,
            resuelto=False,
        ),
    ]
    calc = CalculadoraCostoResolucion(mediciones)
    resumen = calc.comparar().resumen()
    assert resumen["mejor_estrategia"] == "clasificacion"
    assert len(resumen["estrategias"]) == 1
    est = resumen["estrategias"][0]
    assert est["estrategia"] == "clasificacion"
    assert est["costo_total_usd"] == 0.08
    assert est["resueltos"] == 1
    assert est["total_pedidos"] == 2
    assert est["costo_por_resolucion_usd"] == 0.08
    assert est["tasa_resolucion"] == 0.5


def test_desde_registro():
    """desde_registro crea una calculadora a partir de RegistroMetricasRouter."""
    from src.routing.metricas import RegistroMetricasRouter, registro_metricas_router

    registro = RegistroMetricasRouter()
    registro.registrar(
        pedido_id="p1",
        modelo_elegido="llm",
        motivo="test",
        costo_usd=0.03,
        resuelto=True,
        estrategia="clasificacion",
    )
    calc = CalculadoraCostoResolucion.desde_registro(registro)
    comp = calc.comparar()
    assert comp.estrategias[0].costo_por_resolucion_usd == 0.03
    assert comp.mejor_estrategia == "clasificacion"

    # No modifica el registro global
    assert len(registro_metricas_router.mediciones) == 0
