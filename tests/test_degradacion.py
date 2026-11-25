"""Tests para la degradación controlada en 4 niveles (Ítem 3.12 del Plan de implementación 3)."""

from __future__ import annotations

import pytest

from src.routing.degradacion import (
    NIVELES,
    NIVELES_DEGRADACION,
    anterior_nivel,
    siguiente_nivel,
)
from src.routing.gestor_degradacion import GestorDegradacion, ResultadoDegradacion


def test_nivel_inicial_es_completo():
    """Por defecto, el gestor empieza en nivel completo."""
    gestor = GestorDegradacion()
    assert gestor.nivel_actual == "completo"
    assert gestor.se_ha_degradado is False
    assert gestor.aviso_actual == ""


def test_degradar_pasa_a_reducido_con_aviso():
    """degradar() baja al siguiente nivel y genera aviso explícito."""
    gestor = GestorDegradacion()
    resultado = gestor.degradar(motivo="modelo no disponible")

    assert resultado.nivel == "reducido"
    assert resultado.se_degrado is True
    assert resultado.motivo == "modelo no disponible"
    assert resultado.aviso_usuario != ""  # nunca en silencio
    assert gestor.nivel_actual == "reducido"
    assert gestor.se_ha_degradado is True


def test_degradar_hasta_derivacion():
    """Se puede degradar completo -> reducido -> minimo -> derivacion."""
    gestor = GestorDegradacion()
    r1 = gestor.degradar(motivo="f1")
    r2 = gestor.degradar(motivo="f2")
    r3 = gestor.degradar(motivo="f3")

    assert r1.nivel == "reducido"
    assert r2.nivel == "minimo"
    assert r3.nivel == "derivacion"
    assert r3.aviso_usuario != ""
    assert "operador humano" in r3.aviso_usuario


def test_no_se_degrada_mas_allá_de_derivacion():
    """Ya en derivacion, degradar() se mantiene en ese nivel."""
    gestor = GestorDegradacion()
    gestor.degradar(motivo="f1")
    gestor.degradar(motivo="f2")
    gestor.degradar(motivo="f3")  # derivacion

    resultado = gestor.degradar(motivo="f4")
    assert resultado.nivel == "derivacion"
    assert gestor.nivel_actual == "derivacion"


def test_recuperar_sube_niveles():
    """recuperar() baja el índice (mejora) cuando vuelve la disponibilidad."""
    gestor = GestorDegradacion()
    gestor.degradar(motivo="f1")  # reducido
    gestor.degradar(motivo="f2")  # minimo

    resultado = gestor.recuperar(motivo="vuelta a la normalidad")
    assert resultado.nivel == "reducido"
    assert gestor.nivel_actual == "reducido"

    resultado2 = gestor.recuperar(motivo="todo recuperado")
    assert resultado2.nivel == "completo"
    assert gestor.se_ha_degradado is False
    assert gestor.aviso_actual == ""


def test_recuperar_en_completo_se_mantiene():
    """Ya en completo, recuperar() se mantiene en completo."""
    gestor = GestorDegradacion()
    resultado = gestor.recuperar(motivo="nada que recuperar")
    assert resultado.nivel == "completo"


def test_estado_actual_no_cambia_nivel():
    """estado_actual() devuelve el nivel sin modificarlo."""
    gestor = GestorDegradacion()
    gestor.degradar(motivo="f1")
    estado = gestor.estado_actual()
    assert estado.nivel == "reducido"
    assert gestor.nivel_actual == "reducido"  # no cambió


def test_aviso_explícito_nunca_en_silencio():
    """Cada nivel distinto de completo tiene un aviso de usuario no vacío."""
    gestor = GestorDegradacion()
    for _ in range(3):
        gestor.degradar(motivo="f")
    # Comprobar directamente los avisos de cada nivel
    assert NIVELES["reducido"].aviso_usuario != ""
    assert NIVELES["minimo"].aviso_usuario != ""
    assert NIVELES["derivacion"].aviso_usuario != ""
    assert NIVELES["completo"].aviso_usuario == ""


def test_historial_registra_cambios():
    """historial() devuelve todos los cambios de nivel."""
    gestor = GestorDegradacion()
    gestor.degradar(motivo="f1")
    gestor.degradar(motivo="f2")
    historial = gestor.historial()
    assert len(historial) == 2
    assert historial[0].nivel == "reducido"
    assert historial[1].nivel == "minimo"


def test_resumen():
    """resumen() devuelve las claves esperadas."""
    gestor = GestorDegradacion()
    gestor.degradar(motivo="f1")
    resumen = gestor.resumen()
    assert resumen["nivel_actual"] == "reducido"
    assert resumen["se_ha_degradado"] is True
    assert resumen["total_cambios"] == 1
    assert resumen["ultimo_motivo"] == "f1"


def test_nivel_inicial_invalido():
    """nivel_inicial debe ser uno de los 4 niveles válidos."""
    with pytest.raises(ValueError):
        GestorDegradacion(nivel_inicial="inexistente")  # type: ignore[arg-type]


def test_resultado_degradacion_tipos():
    """ResultadoDegradacion tiene los campos esperados."""
    from src.routing.degradacion import NIVELES

    r = ResultadoDegradacion(
        nivel="reducido",
        info=NIVELES["reducido"],
        se_degrado=True,
        motivo="fallo",
        aviso_usuario="modo reducido",
    )
    assert r.nivel == "reducido"
    assert r.se_degrado is True
    assert r.motivo == "fallo"
    assert r.aviso_usuario == "modo reducido"


def test_siguiente_y_anterior_nivel():
    """siguiente_nivel() y anterior_nivel() recorren la cadena."""
    assert siguiente_nivel("completo") == "reducido"
    assert siguiente_nivel("reducido") == "minimo"
    assert siguiente_nivel("minimo") == "derivacion"
    assert siguiente_nivel("derivacion") is None

    assert anterior_nivel("derivacion") == "minimo"
    assert anterior_nivel("minimo") == "reducido"
    assert anterior_nivel("reducido") == "completo"
    assert anterior_nivel("completo") is None


def test_orden_de_niveles():
    """NIVELES_DEGRADACION tiene los 4 niveles en orden."""
    assert NIVELES_DEGRADACION == ("completo", "reducido", "minimo", "derivacion")
