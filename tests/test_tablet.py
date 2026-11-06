"""Tablet: borradores, preguntas y respuestas libres del operador."""

from __future__ import annotations

from datetime import UTC, datetime

from planta.nombre_resolver import desde_api
from planta.parte_extractor import Hallazgo
from planta.tablet import (
    aplicar_correcciones,
    construir_borrador,
    construir_pregunta,
    interpretar,
)


def _filas() -> list[Hallazgo]:
    return [
        Hallazgo("B-102", "presion_descarga_local", 1610.0, "psi"),
        Hallazgo("B-101", "temperatura_rodamiento_ir", 51.0, "degC"),
    ]


def test_confirmacion(planta: dict) -> None:
    resolver = desde_api(planta)
    for texto in (
        "Sí, confirmo.",
        "Está bien, confirmo.",
        "Correcto, dale.",
        "Perfecto, confirmo.",
    ):
        assert interpretar(texto, resolver).confirmado is True


def test_correccion_no_es_confirmacion(planta: dict) -> None:
    resolver = desde_api(planta)
    respuesta = (
        "No. La dos estaba en mil seiscientos veinte psi, no en mil seiscientos diez. "
        "Corregilo y te confirmo."
    )
    interpretacion = interpretar(respuesta, resolver)
    assert interpretacion.confirmado is False
    assert len(interpretacion.correcciones) == 1


def test_corrige_valor(planta: dict) -> None:
    resolver = desde_api(planta)
    filas = _filas()
    respuesta = (
        "No. La dos estaba en mil seiscientos veinte psi, no en mil seiscientos diez. "
        "Corregilo y te confirmo."
    )
    correcciones = interpretar(respuesta, resolver).correcciones
    aplicar_correcciones(filas, correcciones, planta, resolver)
    fila = next(f for f in filas if f.tag == "B-102")
    assert fila.valor == 1620.0
    assert fila.unidad == "psi"


def test_corrige_unidad(planta: dict) -> None:
    resolver = desde_api(planta)
    filas = [Hallazgo("B-102", "presion_descarga_local", 1610.0, "kg/cm2")]
    respuesta = "Lo de la dos es en psi, no en kilos."
    correcciones = interpretar(respuesta, resolver).correcciones
    aplicar_correcciones(filas, correcciones, planta, resolver)
    assert filas[0].unidad == "psi"
    assert filas[0].valor == 1610.0


def test_corrige_equipo(planta: dict) -> None:
    resolver = desde_api(planta)
    filas = [Hallazgo("B-101", "presion_descarga_local", 1610.0, "psi")]
    respuesta = "Eso de mil seiscientos diez era de la dos, no de la uno."
    correcciones = interpretar(respuesta, resolver).correcciones
    aplicar_correcciones(filas, correcciones, planta, resolver)
    assert filas[0].tag == "B-102"
    assert filas[0].unidad == "psi"


def test_agrega_faltante(planta: dict) -> None:
    resolver = desde_api(planta)
    filas = _filas()
    respuesta = "Te faltó el pulmón, que estaba en cinco metros."
    correcciones = interpretar(respuesta, resolver).correcciones
    aplicar_correcciones(filas, correcciones, planta, resolver)
    fila = next(f for f in filas if f.tag == "TK-301")
    assert fila.variable == "nivel"
    assert fila.valor == 5.0
    assert fila.unidad == "m"


def test_quita_sobrante(planta: dict) -> None:
    resolver = desde_api(planta)
    filas = _filas()
    respuesta = "Lo de la uno no lo dije."
    correcciones = interpretar(respuesta, resolver).correcciones
    aplicar_correcciones(filas, correcciones, planta, resolver)
    assert [f.tag for f in filas] == ["B-102"]


def test_respuesta_a_pregunta_de_equipo(planta: dict) -> None:
    resolver = desde_api(planta)
    for texto in (
        "La dos.",
        "Era la dos.",
        "La correcta es la dos.",
        "Ninguna de esas, era la dos.",
    ):
        assert interpretar(texto, resolver, es_pregunta=True).tag_elegido == "B-102"


def test_borrador_vacio_tiene_una_fila(planta: dict) -> None:
    mensaje = construir_borrador("NOTA-1", datetime.now(UTC), [], 0)
    assert mensaje.pantalla is not None
    assert len(mensaje.pantalla.filas) == 1
    assert mensaje.pantalla.filas[0].tag == "PLANTA"


def test_borrador_omite_filas_pendientes(planta: dict) -> None:
    filas = [Hallazgo(None, "temperatura_rodamiento_ir", 63.0, "degC")]
    mensaje = construir_borrador("NOTA-1", datetime.now(UTC), filas, 0)
    assert mensaje.pantalla is not None
    assert mensaje.pantalla.filas[0].tag == "PLANTA"


def test_pregunta_lleva_opciones(planta: dict) -> None:
    resolver = desde_api(planta)
    from planta.parte_extractor import Pregunta

    pregunta = Pregunta(
        campo="equipo",
        texto="¿A qué equipo te referís?",
        opciones=resolver.nombres_de(["B-101", "B-102"]),
        tags=["B-101", "B-102"],
    )
    mensaje = construir_pregunta("NOTA-1", datetime.now(UTC), pregunta, 0)
    assert mensaje.id == "pia3-NOTA-1-q0"
    assert mensaje.pantalla is not None
    assert mensaje.pantalla.opciones == ["la uno", "la dos"]
    assert mensaje.pantalla.filas == []
