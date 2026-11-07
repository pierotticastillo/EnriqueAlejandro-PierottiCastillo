"""Tests de la detección de reincidencia (Fase 5).

El manual cambia la acción cuando la falla se repite: "si se repite en el mismo
émbolo, inspeccionar el émbolo", "si la falla se repite pronto, revisar la
filtración aguas arriba antes de volver a cambiar". Pero una orden de hace dos
años es historial del equipo, no una reincidencia.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from analisis.catalogo import CatalogoManuales
from analisis.historial import VENTANA_REINCIDENCIA_DIAS, es_reincidencia
from manuales import documentos

AHORA = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def falla():
    catalogo = CatalogoManuales.desde_documentos(documentos())
    return catalogo.fallas.por_codigo("TX-T01")


def _orden(**campos) -> dict:
    base = {
        "id": "OT-26-0001",
        "equipo": "B-102",
        "tareas": ["T-B-EMP"],
        "aviso": "la dos gotea mucho la empaquetadura",
        "trabajo_realizado": "cambio de empaquetadura",
        "estado": "Cerrada",
        "fecha_aviso": "2026-11-01T09:00:00-03:00",
    }
    base.update(campos)
    return base


def test_una_orden_reciente_de_la_misma_tarea_es_reincidencia(falla) -> None:
    assert es_reincidencia(falla, [_orden()], tarea_codigo="T-B-EMP", referencia=AHORA)


def test_una_orden_vieja_no_alcanza(falla) -> None:
    orden = _orden(fecha_aviso="2024-10-16T21:30:00-03:00")
    assert not es_reincidencia(falla, [orden], tarea_codigo="T-B-EMP", referencia=AHORA)


def test_una_orden_sin_fecha_no_alcanza(falla) -> None:
    orden = _orden()
    del orden["fecha_aviso"]
    assert not es_reincidencia(falla, [orden], tarea_codigo="T-B-EMP", referencia=AHORA)


def test_una_orden_abierta_no_es_reincidencia(falla) -> None:
    orden = _orden(estado="PendienteAprobacion")
    assert not es_reincidencia(falla, [orden], tarea_codigo="T-B-EMP", referencia=AHORA)


def test_el_texto_de_campo_tambien_sirve(falla) -> None:
    orden = _orden(
        tareas=["OT-SIN-CODIGO"],
        aviso="goteo creciente en el prensaestopas de la dos",
        trabajo_realizado="se ajusto el prensaestopas",
    )
    assert es_reincidencia(falla, [orden], tarea_codigo=None, referencia=AHORA)


def test_una_orden_de_otra_falla_no_es_reincidencia(falla) -> None:
    orden = _orden(
        tareas=["T-B-COR"],
        aviso="correas cuarteadas",
        trabajo_realizado="se cambio el juego de correas",
    )
    assert not es_reincidencia(falla, [orden], tarea_codigo="T-B-EMP", referencia=AHORA)


def test_sin_falla_no_hay_reincidencia(falla) -> None:
    assert not es_reincidencia(None, [_orden()], tarea_codigo="T-B-EMP", referencia=AHORA)


def test_una_fecha_sin_zona_no_rompe_la_ventana(falla) -> None:
    """El historial de la planta puede venir sin zona horaria."""
    orden = _orden(fecha_aviso="2026-11-01T09:00:00")
    assert es_reincidencia(falla, [orden], tarea_codigo="T-B-EMP", referencia=AHORA)


def test_la_ventana_esta_documentada() -> None:
    assert VENTANA_REINCIDENCIA_DIAS > 0
