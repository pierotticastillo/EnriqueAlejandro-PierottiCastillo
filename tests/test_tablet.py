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


def test_corregir_el_valor_saca_la_observacion_que_venia_del_valor_viejo(
    planta: dict,
) -> None:
    """El operador corrige "cuatro" por "cero coma cuatro": el filtro está bien.

    La observación "diferencial alto" se había derivado del valor que el
    operador dijo al principio. Si queda pegada, el filtro se escala aunque
    el valor corregido sea normal.
    """
    resolver = desde_api(planta)
    filas = [Hallazgo("F-201A", "presion_diferencial", 4.0, "kg/cm2", "diferencial alto")]
    respuesta = "No. El filtro a estaba en cero coma cuatro kilos, no en cuatro. Arreglá eso."
    correcciones = interpretar(respuesta, resolver).correcciones
    aplicar_correcciones(filas, correcciones, planta, resolver)
    assert filas[0].valor == 0.4
    assert filas[0].observacion is None


def test_corregir_el_valor_deja_la_observacion_si_el_valor_nuevo_tambien_la_tiene(
    planta: dict,
) -> None:
    """Si el valor corregido sigue fuera de límite, la observación se queda."""
    resolver = desde_api(planta)
    filas = [Hallazgo("F-201A", "presion_diferencial", 4.0, "kg/cm2", "diferencial alto")]
    respuesta = "No. El filtro a estaba en tres kilos, no en cuatro."
    correcciones = interpretar(respuesta, resolver).correcciones
    aplicar_correcciones(filas, correcciones, planta, resolver)
    assert filas[0].valor == 3.0
    assert filas[0].observacion == "diferencial alto"


def test_la_observacion_que_sale_del_texto_no_se_toca_al_corregir(planta: dict) -> None:
    """El ruido es del texto: corregir otro valor no lo borra."""
    resolver = desde_api(planta)
    filas = [
        Hallazgo("B-102", "ruido", None, None, "tiene un ruido raro de rodamiento"),
        Hallazgo("B-102", "presion_descarga_local", 1600.0, "psi"),
    ]
    respuesta = "La dos estaba en mil seiscientos diez psi, no en mil seiscientos."
    correcciones = interpretar(respuesta, resolver).correcciones
    aplicar_correcciones(filas, correcciones, planta, resolver)
    ruido = next(f for f in filas if f.variable == "ruido")
    assert ruido.observacion == "tiene un ruido raro de rodamiento"


def test_una_correccion_en_gotas_por_minuto_no_pisa_la_presion(planta: dict) -> None:
    """"Cien gotas por minuto" es del goteo, no de la presión de descarga.

    La corrección llega con su unidad ("gotas por minuto"). Si esa unidad no
    se reconoce, la corrección queda sin fila propia y se escribe sobre la
    primera del equipo: la presión de 1640 psi pasaba a ser 100 psi y el
    operador no lograba confirmar el parte.
    """
    resolver = desde_api(planta)
    filas = [
        Hallazgo("B-102", "presion_descarga_local", 1640.0, "psi"),
        Hallazgo("B-102", "goteo_empaquetadura", None, None, "goteo por empaquetadura"),
    ]
    respuesta = "No. Te faltó la dos, que estaba en cien gotas por minuto."
    correcciones = interpretar(respuesta, resolver).correcciones
    aplicar_correcciones(filas, correcciones, planta, resolver)
    goteo = next(f for f in filas if f.variable == "goteo_empaquetadura")
    presion = next(f for f in filas if f.variable == "presion_descarga_local")
    assert goteo.valor == 100.0
    assert goteo.unidad == "gotas/min"
    assert presion.valor == 1640.0
    assert presion.unidad == "psi"


def test_la_correccion_toma_el_valor_que_no_se_nega(planta: dict) -> None:
    """"Mil seiscientos cuarenta psi, no en cien" vale 1640.

    La frase trae los dos números: el que el operador dice y el que niega.
    El que manda es el primero.
    """
    resolver = desde_api(planta)
    filas = [
        Hallazgo("B-102", "presion_descarga_local", 100.0, "psi"),
        Hallazgo("B-102", "goteo_empaquetadura", 100.0, "gotas/min"),
    ]
    respuesta = (
        "No. La dos estaba en mil seiscientos cuarenta psi, no en cien. "
        "La dos estaba en cien gotas por minuto, no en cincuenta y dos."
    )
    correcciones = interpretar(respuesta, resolver).correcciones
    aplicar_correcciones(filas, correcciones, planta, resolver)
    presion = next(f for f in filas if f.variable == "presion_descarga_local")
    goteo = next(f for f in filas if f.variable == "goteo_empaquetadura")
    assert presion.valor == 1640.0
    assert presion.unidad == "psi"
    assert goteo.valor == 100.0
    assert goteo.unidad == "gotas/min"


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
