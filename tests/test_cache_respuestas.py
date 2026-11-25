"""Tests para el caché de respuestas completas (Ítem 3.10 del Plan de implementación 3)."""

from __future__ import annotations

import time

import pytest

from src.routing.cache_respuestas import CacheRespuestas, ResultadoConsultaCache


def _pedido() -> dict[str, object]:
    return {"texto": "resumen de la factura", "idioma": "es"}


def test_clave_determinista():
    """Dos pedidos idénticos producen la misma clave."""
    cache = CacheRespuestas()
    c1 = cache.clave(tipo_tarea="resumen", datos=_pedido(), modelo="llm")
    c2 = cache.clave(tipo_tarea="resumen", datos=_pedido(), modelo="llm")
    assert c1 == c2


def test_clave_distinta_si_cambian_datos():
    """Datos diferentes producen claves diferentes."""
    cache = CacheRespuestas()
    c1 = cache.clave(tipo_tarea="resumen", datos={"a": 1}, modelo="llm")
    c2 = cache.clave(tipo_tarea="resumen", datos={"a": 2}, modelo="llm")
    assert c1 != c2


def test_obtener_sin_entrada_es_fallo():
    """obtener() de una clave inexistente devuelve golpe=False."""
    cache = CacheRespuestas()
    resultado = cache.obtener(
        tipo_tarea="resumen", datos=_pedido(), modelo="llm"
    )
    assert resultado.golpe is False
    assert resultado.valor is None
    assert resultado.clave != ""


def test_guardar_y_obtener_golpe():
    """guardar() seguida de obtener() devuelve golpe=True con el valor."""
    cache = CacheRespuestas()
    clave = cache.guardar(
        tipo_tarea="resumen",
        datos=_pedido(),
        valor="texto resumido",
        modelo="llm",
    )
    resultado = cache.obtener(
        tipo_tarea="resumen", datos=_pedido(), modelo="llm"
    )
    assert resultado.golpe is True
    assert resultado.valor == "texto resumido"
    assert resultado.clave == clave
    assert resultado.hits == 1


def test_hits_se_incrementan():
    """Cada obtención exitosa incrementa el contador de hits."""
    cache = CacheRespuestas()
    cache.guardar(
        tipo_tarea="resumen", datos=_pedido(), valor="v", modelo="llm"
    )
    cache.obtener(tipo_tarea="resumen", datos=_pedido(), modelo="llm")
    cache.obtener(tipo_tarea="resumen", datos=_pedido(), modelo="llm")
    assert cache.cantidad() == 1
    # El resumen refleja los hits totales.
    resumen = cache.resumen()
    assert resumen["hits_totales"] == 2


def test_expiracion_ttl():
    """Después del TTL, obtener() devuelve fallo y descarta la entrada."""
    cache = CacheRespuestas(ttl_s=0.05)
    cache.guardar(
        tipo_tarea="resumen", datos=_pedido(), valor="v", modelo="llm"
    )
    assert cache.obtener(tipo_tarea="resumen", datos=_pedido(), modelo="llm").golpe is True

    time.sleep(0.1)
    resultado = cache.obtener(tipo_tarea="resumen", datos=_pedido(), modelo="llm")
    assert resultado.golpe is False
    assert cache.cantidad() == 0  # la entrada expirada se descartó


def test_invalidar_por_clave():
    """invalidar(clave) borra la entrada y devuelve True si existía."""
    cache = CacheRespuestas()
    clave = cache.guardar(
        tipo_tarea="resumen", datos=_pedido(), valor="v", modelo="llm"
    )
    assert cache.invalidar(clave) is True
    assert cache.invalidar(clave) is False  # ya no existe
    assert cache.cantidad() == 0


def test_invalidar_por_modelo():
    """invalidar_por_modelo(modelo) borra todas las entradas de ese modelo."""
    cache = CacheRespuestas()
    cache.guardar(tipo_tarea="a", datos={"x": 1}, valor="v1", modelo="llm")
    cache.guardar(tipo_tarea="b", datos={"x": 2}, valor="v2", modelo="llm")
    cache.guardar(tipo_tarea="c", datos={"x": 3}, valor="v3", modelo="vision")

    borradas = cache.invalidar_por_modelo("llm")
    assert borradas == 2
    assert cache.cantidad() == 1
    # la de visión sigue ahí
    assert cache.obtener(tipo_tarea="c", datos={"x": 3}, modelo="vision").golpe is True


def test_limpiar_expirados():
    """limpiar_expirados() descarta solo las entradas vencidas."""
    cache = CacheRespuestas(ttl_s=0.05)
    cache.guardar(tipo_tarea="a", datos={"x": 1}, valor="v1", modelo="llm")
    cache.guardar(tipo_tarea="b", datos={"x": 2}, valor="v2", modelo="llm")

    time.sleep(0.1)
    # La primera expira; la segunda también (mismo ttl). Ambas se limpian.
    borradas = cache.limpiar_expirados()
    assert borradas == 2
    assert cache.esta_vacia() is True


def test_esta_vacia_y_cantidad():
    """esta_vacia() y cantidad() reflejan el estado del caché."""
    cache = CacheRespuestas()
    assert cache.esta_vacia() is True
    assert cache.cantidad() == 0

    cache.guardar(tipo_tarea="a", datos={"x": 1}, valor="v", modelo="llm")
    assert cache.esta_vacia() is False
    assert cache.cantidad() == 1


def test_resumen():
    """resumen() devuelve las claves esperadas."""
    cache = CacheRespuestas(ttl_s=60.0)
    cache.guardar(tipo_tarea="a", datos={"x": 1}, valor="v", modelo="llm")
    resumen = cache.resumen()
    assert resumen["entradas_total"] == 1
    assert resumen["activas"] == 1
    assert resumen["expiradas"] == 0
    assert resumen["ttl_s"] == 60.0
    assert resumen["modelos"] == ["llm"]


def test_ttl_invalido():
    """ttl_s debe ser > 0."""
    with pytest.raises(ValueError):
        CacheRespuestas(ttl_s=0.0)


def test_max_entradas_invalido():
    """max_entradas debe ser >= 1."""
    with pytest.raises(ValueError):
        CacheRespuestas(max_entradas=0)


def test_resultado_consulta_tipos():
    """ResultadoConsultaCache tiene los campos esperados."""
    r = ResultadoConsultaCache(golpe=True, valor="x", clave="abc", hits=3)
    assert r.golpe is True
    assert r.valor == "x"
    assert r.clave == "abc"
    assert r.hits == 3


def test_instancia_global_disponible():
    """cache_respuestas está expuesta como instancia global."""
    from src.routing.cache_respuestas import cache_respuestas

    assert cache_respuestas is not None
    assert cache_respuestas.esta_vacia() is True
