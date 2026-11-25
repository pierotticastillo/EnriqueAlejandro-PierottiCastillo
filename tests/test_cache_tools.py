"""Tests para el caché de tools (Ítem 3.10 del Plan de implementación 3)."""

from __future__ import annotations

import time

import pytest

from src.routing.cache_tools import CacheTools


def test_clave_determinista():
    """Misma herramienta y mismos argumentos producen la misma clave."""
    cache = CacheTools()
    c1 = cache.clave(nombre_tool="calcular_costo", argumentos={"monto": 100})
    c2 = cache.clave(nombre_tool="calcular_costo", argumentos={"monto": 100})
    assert c1 == c2


def test_clave_distinta_si_cambian_argumentos():
    """Argumentos diferentes producen claves diferentes."""
    cache = CacheTools()
    c1 = cache.clave(nombre_tool="calcular_costo", argumentos={"monto": 100})
    c2 = cache.clave(nombre_tool="calcular_costo", argumentos={"monto": 200})
    assert c1 != c2


def test_clave_sin_argumentos():
    """Una herramienta sin argumentos tiene su propia clave."""
    cache = CacheTools()
    c_sin = cache.clave(nombre_tool="estado_sistema")
    c_con = cache.clave(nombre_tool="estado_sistema", argumentos={})
    # Ambos son válidos y distintos (uno lleva argumentos vacío, otro no)
    assert c_sin != c_con


def test_obtener_sin_entrada_es_fallo():
    """obtener() de una clave inexistente devuelve golpe=False."""
    cache = CacheTools()
    resultado = cache.obtener(nombre_tool="calcular_costo", argumentos={"monto": 100})
    assert resultado.golpe is False
    assert resultado.valor is None


def test_guardar_y_obtener_golpe():
    """guardar() seguida de obtener() devuelve golpe=True con el valor."""
    cache = CacheTools()
    clave = cache.guardar(
        nombre_tool="calcular_costo", valor={"total": 15.5}, argumentos={"monto": 100}
    )
    resultado = cache.obtener(
        nombre_tool="calcular_costo", argumentos={"monto": 100}
    )
    assert resultado.golpe is True
    assert resultado.valor == {"total": 15.5}
    assert resultado.clave == clave


def test_hits_se_incrementan():
    """Cada obtención exitosa incrementa el contador de hits."""
    cache = CacheTools()
    cache.guardar(nombre_tool="t", valor="v", argumentos={})
    cache.obtener(nombre_tool="t", argumentos={})
    cache.obtener(nombre_tool="t", argumentos={})
    resumen = cache.resumen()
    assert resumen["hits_totales"] == 2


def test_expiracion_ttl():
    """Después del TTL, obtener() devuelve fallo y descarta la entrada."""
    cache = CacheTools(ttl_s=0.05)
    cache.guardar(nombre_tool="t", valor="v", argumentos={})
    assert cache.obtener(nombre_tool="t", argumentos={}).golpe is True

    time.sleep(0.1)
    assert cache.obtener(nombre_tool="t", argumentos={}).golpe is False
    assert cache.cantidad() == 0


def test_invalidar_por_clave():
    """invalidar(clave) borra la entrada y devuelve True si existía."""
    cache = CacheTools()
    clave = cache.guardar(nombre_tool="t", valor="v", argumentos={})
    assert cache.invalidar(clave) is True
    assert cache.invalidar(clave) is False
    assert cache.cantidad() == 0


def test_invalidar_por_tool():
    """invalidar_por_tool(nombre) borra todas las entradas de esa herramienta."""
    cache = CacheTools()
    cache.guardar(nombre_tool="calc", valor="v1", argumentos={"a": 1})
    cache.guardar(nombre_tool="calc", valor="v2", argumentos={"a": 2})
    cache.guardar(nombre_tool="otra", valor="v3", argumentos={})

    borradas = cache.invalidar_por_tool("calc")
    assert borradas == 2
    assert cache.cantidad() == 1
    assert cache.obtener(nombre_tool="otra", argumentos={}).golpe is True


def test_limpiar_expirados():
    """limpiar_expirados() descarta solo las entradas vencidas."""
    cache = CacheTools(ttl_s=0.05)
    cache.guardar(nombre_tool="t1", valor="v1", argumentos={})
    cache.guardar(nombre_tool="t2", valor="v2", argumentos={})

    time.sleep(0.1)
    borradas = cache.limpiar_expirados()
    assert borradas == 2
    assert cache.esta_vacia() is True


def test_esta_vacia_y_cantidad():
    """esta_vacia() y cantidad() reflejan el estado del caché."""
    cache = CacheTools()
    assert cache.esta_vacia() is True
    assert cache.cantidad() == 0

    cache.guardar(nombre_tool="t", valor="v", argumentos={})
    assert cache.esta_vacia() is False
    assert cache.cantidad() == 1


def test_resumen():
    """resumen() devuelve las claves esperadas."""
    cache = CacheTools(ttl_s=60.0)
    cache.guardar(nombre_tool="calc", valor="v", argumentos={"a": 1})
    resumen = cache.resumen()
    assert resumen["entradas_total"] == 1
    assert resumen["activas"] == 1
    assert resumen["expiradas"] == 0
    assert resumen["ttl_s"] == 60.0
    assert resumen["herramientas"] == ["calc"]


def test_ttl_invalido():
    """ttl_s debe ser > 0."""
    with pytest.raises(ValueError):
        CacheTools(ttl_s=0.0)


def test_max_entradas_invalido():
    """max_entradas debe ser >= 1."""
    with pytest.raises(ValueError):
        CacheTools(max_entradas=0)


def test_instancia_global_disponible():
    """cache_tools está expuesta como instancia global."""
    from src.routing.cache_tools import cache_tools

    assert cache_tools is not None
    assert cache_tools.esta_vacia() is True
