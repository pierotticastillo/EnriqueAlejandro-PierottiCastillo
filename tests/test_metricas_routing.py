"""Tests de medición del router (Ítem 3.6 del Plan de implementación 3).

Verifica que el registro de métricas del router capture correctamente el
modelo elegido, motivo, costo y resolución, y que la comparación contra
líneas base ("siempre el grande" / "siempre el económico") funcione.
"""

from routing.metricas import RegistroMetricasRouter, registro_metricas_router


def test_registrar_medicion_router():
    """Se puede registrar una medición de routing y se conserva."""
    registro = RegistroMetricasRouter()
    registro.registrar(
        pedido_id="pedido-001",
        modelo_elegido="economico",
        motivo="tarea de clasificar, sin imagen",
        costo_usd=0.001,
        resuelto=True,
        estrategia="reglas",
        llamadas=1,
    )
    assert len(registro.mediciones) == 1
    m = registro.mediciones[0]
    assert m.pedido_id == "pedido-001"
    assert m.modelo_elegido == "economico"
    assert m.costo_usd == 0.001
    assert m.resuelto is True
    assert m.estrategia == "reglas"


def test_resumen_vacio():
    """El resumen de un registro vacío tiene ceros."""
    registro = RegistroMetricasRouter()
    resumen = registro.resumen()
    assert resumen["total_pedidos"] == 0
    assert resumen["costo_total_usd"] == 0.0
    assert resumen["tasa_resolucion"] == 0.0


def test_resumen_con_varias_mediciones():
    """El resumen agrega correctamente múltiples mediciones."""
    registro = RegistroMetricasRouter()
    registro.registrar(
        pedido_id="p1",
        modelo_elegido="economico",
        motivo="reglas",
        costo_usd=0.001,
        resuelto=True,
        estrategia="reglas",
        llamadas=1,
    )
    registro.registrar(
        pedido_id="p2",
        modelo_elegido="generalista",
        motivo="cascada: falló validador",
        costo_usd=0.011,
        resuelto=True,
        estrategia="clasificacion",
        llamadas=2,
    )
    registro.registrar(
        pedido_id="p3",
        modelo_elegido="economico",
        motivo="reglas",
        costo_usd=0.001,
        resuelto=False,
        estrategia="reglas",
        llamadas=1,
    )
    resumen = registro.resumen()
    assert resumen["total_pedidos"] == 3
    assert resumen["costo_total_usd"] == 0.013
    assert resumen["costo_promedio_usd"] == round(0.013 / 3, 4)
    assert resumen["resueltos"] == 2
    assert resumen["tasa_resolucion"] == round(2 / 3, 4)
    assert resumen["por_modelo"]["economico"]["cantidad"] == 2
    assert resumen["por_modelo"]["generalista"]["cantidad"] == 1
    assert resumen["por_estrategia"]["reglas"]["cantidad"] == 2
    assert resumen["por_estrategia"]["clasificacion"]["cantidad"] == 1


def test_comparar_con_linea_base():
    """La comparación contra línea base calcula ahorro y diferencia de resolución."""
    registro = RegistroMetricasRouter()
    registro.registrar(
        pedido_id="p1",
        modelo_elegido="economico",
        motivo="reglas",
        costo_usd=0.001,
        resuelto=True,
        estrategia="reglas",
        llamadas=1,
    )
    registro.registrar(
        pedido_id="p2",
        modelo_elegido="generalista",
        motivo="cascada",
        costo_usd=0.011,
        resuelto=True,
        estrategia="clasificacion",
        llamadas=2,
    )
    comparacion = registro.comparar_con_linea_base(
        costo_grande_por_pedido=0.08,
        costo_economico_por_pedido=0.02,
        resolucion_grande=0.92,
        resolucion_economico=0.71,
    )
    assert comparacion["router"]["costo_total_usd"] == 0.012
    assert comparacion["router"]["resueltos"] == 2
    assert comparacion["linea_base_grande"]["costo_total_usd"] == 0.16
    assert comparacion["linea_base_economico"]["costo_total_usd"] == 0.04
    # Ahorro vs grande: 0.16 - 0.012 = 0.148
    assert comparacion["ahorro_vs_grande_usd"] == 0.148
    # Ahorro vs grande %: 0.148 / 0.16 * 100 = 92.5%
    assert comparacion["ahorro_vs_grande_pct"] == 92.5
    # Diferencia de resolución vs grande: 1.0 - 0.92 = 0.08
    assert comparacion["diferencia_resolucion_vs_grande"] == 0.08


def test_registro_global_singleton():
    """El registro global es una instancia compartida."""
    assert isinstance(registro_metricas_router, RegistroMetricasRouter)
