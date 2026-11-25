"""Tests para la métrica de tokens por segundo (Ítem 3.13 del Plan de implementación 3)."""

from __future__ import annotations

from src.routing.metricas_tokens import (
    MetricaTokensPorSegundo,
    calcular_tokens_por_segundo,
    metricas_tokens_por_segundo,
    registrar_metrica_tokens,
    resumen_velocidad,
)


def test_calcular_tokens_por_segundo_basico():
    """Cálculo básico de ambas tasas."""
    m = calcular_tokens_por_segundo(
        paso=1,
        modelo="qwen2.5:3b-instruct",
        tokens_prompt=1000,
        tokens_completion=200,
        segundos_prompt=2.0,
        segundos_completion=5.0,
    )
    assert m.prompt_eval_rate_tok_s == 500.0  # 1000 / 2
    assert m.eval_rate_tok_s == 40.0  # 200 / 5
    assert m.segundos_totales == 7.0
    assert m.tokens_totales == 1200


def test_segundos_cero_no_divide_por_cero():
    """Si segundos es 0, la tasa es 0."""
    m = calcular_tokens_por_segundo(
        paso=1,
        modelo="x",
        tokens_prompt=100,
        tokens_completion=50,
        segundos_prompt=0.0,
        segundos_completion=0.0,
    )
    assert m.prompt_eval_rate_tok_s == 0.0
    assert m.eval_rate_tok_s == 0.0


def test_velocidad_promedio():
    """La velocidad promedio es tokens totales / segundos totales."""
    m = calcular_tokens_por_segundo(
        paso=1,
        modelo="x",
        tokens_prompt=100,
        tokens_completion=100,
        segundos_prompt=1.0,
        segundos_completion=1.0,
    )
    assert m.velocidad_promedio_tok_s == 100.0  # 200 / 2


def test_clasificacion_incómodo():
    """Menos de 5 tok/s es incómodo para chat."""
    m = MetricaTokensPorSegundo(
        paso=1, modelo="x", tokens_prompt=0, tokens_completion=10,
        segundos_prompt=0, segundos_completion=5,
        prompt_eval_rate_tok_s=0.0, eval_rate_tok_s=2.0,
    )
    assert m.clasificacion_velocidad() == "incómodo para chat"


def test_clasificacion_aceptable():
    """5 a 10 tok/s es aceptable."""
    m = MetricaTokensPorSegundo(
        paso=1, modelo="x", tokens_prompt=0, tokens_completion=50,
        segundos_prompt=0, segundos_completion=10,
        prompt_eval_rate_tok_s=0.0, eval_rate_tok_s=5.0,
    )
    assert m.clasificacion_velocidad() == "aceptable"


def test_clasificacion_fluido():
    """10 a 20 tok/s es fluido para leer."""
    m = MetricaTokensPorSegundo(
        paso=1, modelo="x", tokens_prompt=0, tokens_completion=100,
        segundos_prompt=0, segundos_completion=10,
        prompt_eval_rate_tok_s=0.0, eval_rate_tok_s=10.0,
    )
    assert m.clasificacion_velocidad() == "fluido para leer"


def test_clasificacion_agente():
    """Más de 20 tok/s es lo que pide un agente."""
    m = MetricaTokensPorSegundo(
        paso=1, modelo="x", tokens_prompt=0, tokens_completion=300,
        segundos_prompt=0, segundos_completion=10,
        prompt_eval_rate_tok_s=0.0, eval_rate_tok_s=30.0,
    )
    assert m.clasificacion_velocidad() == "lo que pide un agente"


def test_registrar_y_resumen():
    """Registrar métricas y obtener resumen."""
    metricas_tokens_por_segundo.clear()
    registrar_metrica_tokens(
        paso=1, modelo="llm", tokens_prompt=1000, tokens_completion=200,
        segundos_prompt=2.0, segundos_completion=5.0,
    )
    registrar_metrica_tokens(
        paso=2, modelo="llm", tokens_prompt=500, tokens_completion=100,
        segundos_prompt=1.0, segundos_completion=2.5,
    )
    resumen = resumen_velocidad()
    assert resumen["total_pasos"] == 2
    assert resumen["prompt_eval_rate_promedio_tok_s"] == 500.0  # (500 + 500) / 2
    assert resumen["eval_rate_promedio_tok_s"] == 40.0  # (40 + 40) / 2
    assert resumen["velocidad_promedio_tok_s"] == 171.43  # (171.43 + 171.43) / 2


def test_resumen_vacio():
    """Sin mediciones, el resumen está vacío."""
    metricas_tokens_por_segundo.clear()
    resumen = resumen_velocidad()
    assert resumen["total_pasos"] == 0
    assert resumen["clasificacion"] == "sin mediciones"


def test_limpiar_lista_global():
    """La lista global se puede limpiar entre tests."""
    metricas_tokens_por_segundo.clear()
    assert len(metricas_tokens_por_segundo) == 0
    registrar_metrica_tokens(
        paso=1, modelo="x", tokens_prompt=10, tokens_completion=5,
        segundos_prompt=1.0, segundos_completion=1.0,
    )
    assert len(metricas_tokens_por_segundo) == 1
    metricas_tokens_por_segundo.clear()
    assert len(metricas_tokens_por_segundo) == 0
