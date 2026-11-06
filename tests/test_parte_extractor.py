"""Extractor determinista del parte por voz.

Los textos son los que publica el operador virtual de la planta; además se
cubren fotos, seguridad y las dos ambigüedades que el sistema debe preguntar.
"""

from __future__ import annotations

import pytest

from planta.parte_extractor import ExtractorParte

CASOS = [
    (
        "La del medio está en mil seiscientos treinta de descarga. "
        "La uno está en ciento quince kilogramos de descarga, "
        "el rodamiento me dio cincuenta y cinco grados con la pistola.",
        [
            ("B-102", "presion_descarga_local", 1630.0, "psi", None),
            ("B-101", "presion_descarga_local", 115.0, "kg/cm2", None),
            ("B-101", "temperatura_rodamiento_ir", 55.0, "degC", None),
        ],
    ),
    (
        "El filtro B me dio uno de diferencial.",
        [("F-201B", "presion_diferencial", 1.0, "kg/cm2", None)],
    ),
    (
        "El pulmón está en cinco metros.",
        [("TK-301", "nivel", 5.0, "m", None)],
    ),
    (
        "El filtro A me dio uno con dos de diferencial. "
        "La del fondo está parada, en reserva. El pulmón está en cinco metros.",
        [
            ("F-201A", "presion_diferencial", 1.2, "kg/cm2", None),
            ("B-103", "estado", None, None, "parada, en reserva"),
            ("TK-301", "nivel", 5.0, "m", None),
        ],
    ),
    (
        "El filtro B me dio cero nueve de diferencial. "
        "La uno está en ciento once kilos de descarga.",
        [
            ("F-201B", "presion_diferencial", 0.9, "kg/cm2", None),
            ("B-101", "presion_descarga_local", 111.0, "kg/cm2", None),
        ],
    ),
    (
        "La dos está en mil seiscientos cuarenta libras de descarga. "
        "El de la izquierda me dio uno dos de diferencial.",
        [
            ("B-102", "presion_descarga_local", 1640.0, "psi", None),
            ("F-201A", "presion_diferencial", 1.2, "kg/cm2", None),
        ],
    ),
    (
        "La de la punta está en ciento trece kilogramos de descarga.",
        [("B-101", "presion_descarga_local", 113.0, "kg/cm2", None)],
    ),
    (
        "Este, la del medio está en mil seiscientos diez psi de descarga, "
        "el rodamiento me dio cincuenta y un grados con la pistola.",
        [
            ("B-102", "presion_descarga_local", 1610.0, "psi", None),
            ("B-102", "temperatura_rodamiento_ir", 51.0, "degC", None),
        ],
    ),
    (
        "Listo, el de la izquierda me dio uno coma tres de diferencial. "
        "La uno está en ciento catorce de descarga.",
        [
            ("F-201A", "presion_diferencial", 1.3, "kg/cm2", None),
            ("B-101", "presion_descarga_local", 114.0, "kg/cm2", None),
        ],
    ),
    (
        "La tres está parada, en reserva.",
        [("B-103", "estado", None, None, "parada, en reserva")],
    ),
    (
        "El secundario me dio uno de diferencial. A ver, el pulmón está en cinco metros.",
        [
            ("F-201B", "presion_diferencial", 1.0, "kg/cm2", None),
            ("TK-301", "nivel", 5.0, "m", None),
        ],
    ),
    (
        "El filtro A me dio uno coma dos de diferencial. "
        "La de la punta está en ciento doce kilogramos de descarga.",
        [
            ("F-201A", "presion_diferencial", 1.2, "kg/cm2", None),
            ("B-101", "presion_descarga_local", 112.0, "kg/cm2", None),
        ],
    ),
]


def _filas(extraccion) -> list[tuple]:
    return [(f.tag, f.variable, f.valor, f.unidad, f.observacion) for f in extraccion.filas]


@pytest.mark.parametrize(("texto", "esperado"), CASOS)
def test_notas_reales(planta: dict, texto: str, esperado: list[tuple]) -> None:
    extraccion = ExtractorParte(planta).extraer(texto)
    assert _filas(extraccion) == esperado
    assert extraccion.preguntas == []


def test_texto_asr_sin_puntuacion_y_con_digitos(planta: dict) -> None:
    """La salida real del ASR no trae puntos y escribe los números en dígitos."""
    texto = (
        "la del medio está en 1630 de descarga "
        "la uno está en 115 kilogramos de descarga "
        "el rodamiento me dio 55 grados con la pistola"
    )
    extraccion = ExtractorParte(planta).extraer(texto)
    assert _filas(extraccion) == [
        ("B-102", "presion_descarga_local", 1630.0, "psi", None),
        ("B-101", "presion_descarga_local", 115.0, "kg/cm2", None),
        ("B-101", "temperatura_rodamiento_ir", 55.0, "degC", None),
    ]
    assert extraccion.preguntas == []


def test_texto_asr_con_decimal_en_digitos(planta: dict) -> None:
    """El ASR también puede devolver el decimal con punto (``0.9``)."""
    extraccion = ExtractorParte(planta).extraer(
        "el filtro B medio 0.9 de diferencial"
    )
    assert _filas(extraccion) == [
        ("F-201B", "presion_diferencial", 0.9, "kg/cm2", None)
    ]
    assert extraccion.preguntas == []


def test_diferencial_con_el_cero_del_asr(planta: dict) -> None:
    """El ASR escribe "cero nueve" como ``09``.

    Un diferencial de 9 kg/cm² está muy por encima de la alarma del
    catálogo (1,5), así que lo que se oyó fue 0,9.
    """
    extraccion = ExtractorParte(planta).extraer("El de la derecha me dio 09 de diferencial.")
    assert _filas(extraccion) == [
        ("F-201B", "presion_diferencial", 0.9, "kg/cm2", None)
    ]
    assert extraccion.preguntas == []


def test_unidad_psi_partida_por_el_asr(planta: dict) -> None:
    """El ASR a veces separa las letras de la unidad: "1600 PS y de descarga"."""
    extraccion = ExtractorParte(planta).extraer(
        "la del medio está en 1600 PS y de descarga"
    )
    assert _filas(extraccion) == [
        ("B-102", "presion_descarga_local", 1600.0, "psi", None)
    ]


def test_reserva_escuchada_como_reservoir(planta: dict) -> None:
    """El ASR escribe "reservoir" cuando oye "reserva"."""
    extraccion = ExtractorParte(planta).extraer("¿Listo? ¿La bomba está parada en Reservoir?")
    assert _filas(extraccion) == [(None, "estado", None, None, "parada, en reserva")]
    assert len(extraccion.preguntas) == 1
    assert extraccion.preguntas[0].campo == "equipo"
    assert extraccion.preguntas[0].filas == [0]


def test_filtro_sin_la_palabra_diferencial(planta: dict) -> None:
    """Si el filtro tiene una sola variable en el catálogo, esa es la que se oyó."""
    extraccion = ExtractorParte(planta).extraer("el filtro a medio 1,3 de")
    assert _filas(extraccion) == [
        ("F-201A", "presion_diferencial", 1.3, "kg/cm2", None)
    ]
    assert extraccion.preguntas == []


def test_equipo_ambiguo_pregunta_y_deja_fila_pendiente(planta: dict) -> None:
    extraccion = ExtractorParte(planta).extraer(
        "En la bomba, el rodamiento me dio sesenta y tres grados con la pistola."
    )
    assert _filas(extraccion) == [(None, "temperatura_rodamiento_ir", 63.0, "degC", None)]
    assert len(extraccion.preguntas) == 1
    pregunta = extraccion.preguntas[0]
    assert pregunta.campo == "equipo"
    assert pregunta.filas == [0]
    assert pregunta.opciones == ["la uno", "la dos", "la tres"]


def test_diferencial_dicho_dieciocho_pregunta_y_usa_uno_ocho(planta: dict) -> None:
    extraccion = ExtractorParte(planta).extraer("El filtro B me dio dieciocho de diferencial.")
    assert _filas(extraccion) == [
        ("F-201B", "presion_diferencial", 1.8, "kg/cm2", "diferencial alto")
    ]
    assert len(extraccion.preguntas) == 1
    assert extraccion.preguntas[0].campo == "valor"


def test_fotos_se_asocian_a_la_fila_correcta(planta: dict) -> None:
    fotos = ["/media/fotos/empaquetadura/01.jpg", "/media/fotos/correas/02.jpg"]
    extraccion = ExtractorParte(planta).extraer(
        "La dos gotea por la empaquetadura, le saqué foto. Las correas están flojas, saqué foto.",
        fotos,
    )
    assert [(f.variable, f.foto_indice) for f in extraccion.filas] == [
        ("goteo_empaquetadura", 0),
        ("estado_correas", 1),
    ]


def test_foto_del_manometro(planta: dict) -> None:
    fotos = ["/media/fotos/manometro_diferencial/03.jpg"]
    extraccion = ExtractorParte(planta).extraer(
        "El filtro A me dio uno coma seis de diferencial, le saqué foto al manómetro.",
        fotos,
    )
    fila = extraccion.filas[0]
    assert fila.variable == "presion_diferencial"
    assert fila.valor == 1.6
    assert fila.observacion == "diferencial alto"
    assert fila.foto_indice == 0


@pytest.mark.parametrize(
    ("texto", "codigo", "tag"),
    [
        (
            "¡Atención! Se rompió algo en la descarga de la uno, sale agua a presión "
            "para todos lados, me alejé. Saqué foto desde lejos.",
            "S1",
            "B-101",
        ),
        (
            "Hay olor a gas fuerte del lado del portón, viene del lado del separador.",
            "S2",
            "PLANTA",
        ),
        (
            "Sale humo del motor de la dos, hay olor a quemado.",
            "S3",
            "B-102",
        ),
        (
            "Mi compañero se golpeó la mano con la llave en el skid de filtros, "
            "está sangrando bastante.",
            "S4",
            "PLANTA",
        ),
    ],
)
def test_seguridad_local(
    planta: dict,
    texto: str,
    codigo: str,
    tag: str,
) -> None:
    extraccion = ExtractorParte(planta).extraer(texto, ["/media/fotos/rotura/01.jpg"])
    assert extraccion.seguridad == codigo
    assert len(extraccion.filas) == 1
    assert extraccion.filas[0].tag == tag
    assert extraccion.filas[0].variable == "condicion_insegura"
    assert extraccion.filas[0].valor is None


def test_nota_sin_novedad(planta: dict) -> None:
    extraccion = ExtractorParte(planta).extraer("Recorrida sin novedad, todo normal.")
    assert extraccion.sin_novedad is True
    assert extraccion.filas[0].observacion == "Sin novedad"


def test_presion_de_b103_en_bar(planta: dict) -> None:
    extraccion = ExtractorParte(planta).extraer("La del fondo está en diez bares de descarga.")
    assert _filas(extraccion) == [("B-103", "presion_descarga_local", 10.0, "bar", None)]


def test_presion_dicha_en_otra_unidad_se_convierte(planta: dict) -> None:
    # B-102 usa psi en su manómetro; si el operador dice kilos, se convierte.
    extraccion = ExtractorParte(planta).extraer("La dos está en ciento quince kilos de descarga.")
    fila = extraccion.filas[0]
    assert fila.tag == "B-102"
    assert fila.unidad == "psi"
    assert fila.valor == pytest.approx(1635.68, abs=0.1)
