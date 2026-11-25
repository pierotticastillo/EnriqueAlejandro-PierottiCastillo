"""Tests para la cadena de fallback de 5 niveles (Ítem 3.11 del Plan de implementación 3)."""

from __future__ import annotations

import pytest

from src.routing.cadena_fallback import CadenaFallback
from src.routing.fallback_nivel import (
    CADENA_COMPLETA,
    NivelFallback,
    ResultadoCadena,
    ResultadoNivel,
)


def _error(mensaje: str = "error de prueba") -> Exception:
    return Exception(mensaje)


def _callback_exitoso(nivel: NivelFallback, respuesta: str = "respuesta"):
    def cb(error: Exception) -> ResultadoNivel:
        return ResultadoNivel(
            nivel=nivel, exito=True, respuesta=respuesta, motivo=f"éxito en {nivel}"
        )
    return cb


def _callback_fallido(nivel: NivelFallback, motivo: str = "falló"):
    def cb(error: Exception) -> ResultadoNivel:
        return ResultadoNivel(
            nivel=nivel, exito=False, motivo=motivo, error_original=str(error)
        )
    return cb


def test_exito_principal_sin_fallback():
    """Si la acción principal tiene éxito, no se recorre la cadena."""
    cadena = CadenaFallback()
    resultado = cadena.ejecutar_con_fallback(lambda: "ok")
    assert resultado.exito is True
    resultado_nivel = None
    assert resultado.nivel_alcanzado is None
    assert resultado.respuesta == "ok"
    assert resultado.cantidad_niveles_probados == 0


def test_cadena_recorre_niveles_hasta_exito():
    """Si el principal falla y el nivel 2 tiene éxito, se detiene ahí."""
    cadena = CadenaFallback(
        callbacks={
            "reintentar": _callback_fallido("reintentar"),
            "otra_region": _callback_exitoso("otra_region", "desde otra región"),
        }
    )
    resultado = cadena.ejecutar_con_fallback(lambda: (_ for _ in ()).throw(_error("429")))
    assert resultado.exito is True
    assert resultado.nivel_alcanzado == "otra_region"
    assert resultado.respuesta == "desde otra región"
    assert resultado.cantidad_niveles_probados == 2


def test_todos_los_niveles_fallan():
    """Si todos los niveles fallan, el resultado es exito=False."""
    cadena = CadenaFallback(
        callbacks={
            "reintentar": _callback_fallido("reintentar"),
            "otra_region": _callback_fallido("otra_region"),
            "modelo_alternativo": _callback_fallido("modelo_alternativo"),
            "modelo_chico": _callback_fallido("modelo_chico"),
            "degradar": _callback_fallido("degradar"),
        }
    )
    resultado = cadena.ejecutar_con_fallback(lambda: (_ for _ in ()).throw(_error("500")))
    assert resultado.exito is False
    assert resultado.nivel_alcanzado == "degradar"
    assert resultado.cantidad_niveles_probados == 5


def test_nivel_sin_callback_se_salta():
    """Un nivel sin callback se salta (excepto degradar)."""
    cadena = CadenaFallback(
        callbacks={
            "reintentar": _callback_fallido("reintentar"),
            # 'otra_region' sin callback -> se salta
            "modelo_chico": _callback_exitoso("modelo_chico", "desde modelo chico"),
        }
    )
    resultado = cadena.ejecutar_con_fallback(lambda: (_ for _ in ()).throw(_error("429")))
    assert resultado.exito is True
    assert resultado.nivel_alcanzado == "modelo_chico"
    assert resultado.cantidad_niveles_probados == 2  # reintentar + modelo_chico


def test_degradar_sin_callback_devuelve_fallo():
    """Si se llega a degradar sin callback, es fallo en ese nivel."""
    cadena = CadenaFallback(
        callbacks={
            "reintentar": _callback_fallido("reintentar"),
            "otra_region": _callback_fallido("otra_region"),
            "modelo_alternativo": _callback_fallido("modelo_alternativo"),
            "modelo_chico": _callback_fallido("modelo_chico"),
            # 'degradar' sin callback
        }
    )
    resultado = cadena.ejecutar_con_fallback(lambda: (_ for _ in ()).throw(_error("429")))
    assert resultado.exito is False
    assert resultado.nivel_alcanzado == "degradar"


def test_callback_que_lanza_excepcion():
    """Si un callback lanza excepción, se registra y se sigue al siguiente nivel."""
    def cb_que_falla(error: Exception) -> ResultadoNivel:
        raise RuntimeError("callback explotó")

    cadena = CadenaFallback(
        callbacks={
            "reintentar": cb_que_falla,
            "otra_region": _callback_exitoso("otra_region", "desde otra región"),
        }
    )
    resultado = cadena.ejecutar_con_fallback(lambda: (_ for _ in ()).throw(_error("timeout")))
    assert resultado.exito is True
    assert resultado.nivel_alcanzado == "otra_region"
    assert resultado.cantidad_niveles_probados == 2


def test_cadena_completa_tiene_5_niveles():
    """CADENA_COMPLETA tiene exactamente los 5 niveles definidos."""
    assert CADENA_COMPLETA == (
        "reintentar",
        "otra_region",
        "modelo_alternativo",
        "modelo_chico",
        "degradar",
    )


def test_registrar_callback():
    """Se puede registrar un callback después de crear la cadena."""
    cadena = CadenaFallback()
    assert cadena.niveles_registrados() == ()
    cadena.registrar_callback("reintentar", _callback_exitoso("reintentar"))
    assert "reintentar" in cadena.niveles_registrados()


def test_resultado_nivel_con_error_original():
    """ResultadoNivel puede incluir el error original."""
    error = _error("429 Too Many Requests")
    resultado = ResultadoNivel(
        nivel="reintentar",
        exito=False,
        motivo="agotados los reintentos",
        error_original=str(error),
    )
    assert resultado.nivel == "reintentar"
    assert resultado.exito is False
    assert resultado.error_original == "429 Too Many Requests"


def test_propiedad_se_degrado():
    """se_degrado es True solo si se llegó al nivel degradar."""
    exito_temprano = ResultadoCadena(
        exito=True, nivel_alcanzado="otra_region", respuesta="ok"
    )
    assert exito_temprano.se_degrado is False

    degradado = ResultadoCadena(
        exito=True, nivel_alcanzado="degradar", respuesta="respuesta reducida"
    )
    assert degradado.se_degrado is True

    fallo_total = ResultadoCadena(
        exito=False, nivel_alcanzado="degradar", respuesta=None
    )
    assert fallo_total.se_degrado is True
