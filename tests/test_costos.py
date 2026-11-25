"""Tests para la tabla de costos (Ítem 3.7 del Plan de implementación 3)."""

from __future__ import annotations

import pytest
from src.routing.costos import PrecioModelo, TablaCostos


def test_modelo_local_tiene_precio_cero():
    """Los modelos locales de la planta no cuestan nada."""
    tabla = TablaCostos()
    for clave in ("asr", "embeddings", "llm", "economico"):
        assert tabla.precio_entrada(clave) == 0.0
        assert tabla.precio_salida(clave) == 0.0


def test_vision_tiene_tarifa_diferencial():
    """El modelo de nube con visión tiene entrada más barata que salida."""
    tabla = TablaCostos()
    assert tabla.precio_entrada("vision") == 3.0
    assert tabla.precio_salida("vision") == 15.0


def test_costo_request_modelo_local():
    """Un request al modelo local cuesta 0 USD."""
    tabla = TablaCostos()
    costo = tabla.costo_usd(clave="llm", tokens_entrada=5000, tokens_salida=1000)
    assert costo == 0.0


def test_costo_request_vision():
    """Un request al modelo de visión se calcula con tarifas diferenciales."""
    tabla = TablaCostos()
    # 2.000 tokens de entrada × USD 3 / 1.000.000 = 0,006
    # 500 tokens de salida × USD 15 / 1.000.000 = 0,0075
    # Total = 0,0135
    costo = tabla.costo_usd(clave="vision", tokens_entrada=2000, tokens_salida=500)
    assert costo == 0.0135


def test_costo_request_vision_redondeo():
    """El costo se redondea a 6 decimales."""
    tabla = TablaCostos()
    costo = tabla.costo_usd(clave="vision", tokens_entrada=1, tokens_salida=1)
    assert costo == 0.000018


def test_registrar_precio_actualiza_valores():
    """Se puede registrar o actualizar el precio de un modelo."""
    tabla = TablaCostos()
    tabla.registrar_precio(
        clave="llm",
        precio_entrada_usd_por_millon=1.0,
        precio_salida_usd_por_millon=5.0,
    )
    assert tabla.precio_entrada("llm") == 1.0
    assert tabla.precio_salida("llm") == 5.0


def test_registrar_precio_negativo_falla():
    """Los precios negativos no se permiten."""
    tabla = TablaCostos()
    with pytest.raises(ValueError):
        tabla.registrar_precio(
            clave="llm",
            precio_entrada_usd_por_millon=-1.0,
            precio_salida_usd_por_millon=5.0,
        )


def test_costo_tokens_negativos_falla():
    """Los tokens negativos no se permiten."""
    tabla = TablaCostos()
    with pytest.raises(ValueError):
        tabla.costo_usd(clave="vision", tokens_entrada=-100, tokens_salida=50)


def test_modelo_sin_precio_falla():
    """Pedir el precio de un modelo no registrado lanza KeyError."""
    tabla = TablaCostos()
    with pytest.raises(KeyError):
        tabla.precio_entrada("inexistente")


def test_costo_modelo_sin_precio_falla():
    """Calcular el costo de un modelo sin precio lanza KeyError."""
    tabla = TablaCostos()
    with pytest.raises(KeyError):
        tabla.costo_usd(clave="inexistente", tokens_entrada=100, tokens_salida=50)


def test_claves_devuelve_todos_los_modelos():
    """claves() retorna todas las claves registradas, ordenadas."""
    tabla = TablaCostos()
    claves = tabla.claves()
    assert "asr" in claves
    assert "embeddings" in claves
    assert "llm" in claves
    assert "economico" in claves
    assert "vision" in claves


def test_precios_devuelve_copia():
    """precios() retorna una copia, no el diccionario interno."""
    tabla = TablaCostos()
    precios = tabla.precios()
    precios["llm"] = PrecioModelo(
        clave="llm", precio_entrada_usd_por_millon=999.0, precio_salida_usd_por_millon=999.0
    )
    # El original no se modificó
    assert tabla.precio_entrada("llm") == 0.0
