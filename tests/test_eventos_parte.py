"""Clasificación y creación de eventos para partes confirmados."""

from datetime import UTC, datetime

from planta_contrato import TipoEvento

from deteccion.partes import clasificar_partes
from planta.parte_extractor import ExtractorParte, Hallazgo
from planta.unidades import convertir

TS = datetime(2026, 11, 4, 12, tzinfo=UTC)


def _planta() -> dict:
    return {
        "sensores": [
            {
                "tag": "B-102",
                "variable": "presion_descarga",
                "unidad": "kg/cm2",
                "alarma_baja": 95,
                "alarma_alta": 125,
            }
        ]
    }


def _clasificar(filas: list[Hallazgo], *, fotos: int = 0):
    return clasificar_partes(
        nota_id="NOTA-14",
        parte_id="parte-uuid",
        ts=TS,
        filas=filas,
        planta=_planta(),
        fotos=fotos,
    )


def test_parte_normal_genera_sin_novedad_idempotente() -> None:
    eventos = _clasificar([Hallazgo("B-102", "presion_descarga_local", 1600, "psi")])
    assert len(eventos) == 1
    evento = eventos[0]
    assert evento.tipo is TipoEvento.sin_novedad
    assert evento.id == "parte:NOTA-14:B-102"
    assert evento.parte_id == "parte-uuid"
    assert evento.nota_ids == ["NOTA-14"]
    assert evento.equipo == "B-102"


def test_parte_fuera_de_limite_convierte_unidades_del_catalogo() -> None:
    limite_psi = convertir(125, "kg/cm2", "psi")
    eventos = _clasificar([Hallazgo("B-102", "presion_descarga_local", limite_psi, "psi")])
    assert len(eventos) == 1
    evento = eventos[0]
    assert evento.tipo is TipoEvento.escalado
    assert evento.equipo == "B-102"
    assert "presion_descarga_local" in evento.resumen


def test_hallazgo_textual_canonico_genera_evento_escalado() -> None:
    eventos = _clasificar(
        [
            Hallazgo(
                "B-101",
                "goteo_empaquetadura",
                None,
                None,
                "pérdida importante por empaquetadura",
            )
        ]
    )
    assert len(eventos) == 1
    assert eventos[0].tipo is TipoEvento.escalado
    assert "pérdida importante" in eventos[0].resumen


def test_un_goteo_medido_escala_el_equipo() -> None:
    """El goteo que el operador midió escala el equipo.

    La fila llega con la observación derivada del valor (142 gotas/min por
    encima de los 10 del goteo normal, TX350-D §8.3). Sin esa observación el
    evento salía como "no hay novedad" y el equipo no se escalaba.
    """
    eventos = _clasificar(
        [
            Hallazgo(
                "B-102",
                "goteo_empaquetadura",
                142.0,
                "gotas/min",
                "goteo por empaquetadura",
            )
        ]
    )
    assert len(eventos) == 1
    assert eventos[0].tipo is TipoEvento.escalado
    assert "goteo por empaquetadura" in eventos[0].resumen


def test_parte_vacio_no_falla_y_genera_evento() -> None:
    eventos = _clasificar([])
    assert len(eventos) == 1
    assert eventos[0].tipo is TipoEvento.sin_novedad
    assert eventos[0].equipo is None
    assert eventos[0].id == "parte:NOTA-14"


def test_cada_equipo_de_la_nota_tiene_su_propio_evento() -> None:
    """Un evento por equipo: el corrector califica cada equipo por separado."""
    eventos = _clasificar(
        [
            Hallazgo("B-101", "presion_descarga_local", 110, "kg/cm2"),
            Hallazgo(
                "B-102",
                "goteo_empaquetadura",
                None,
                None,
                "pérdida importante por empaquetadura",
            ),
            Hallazgo("F-201A", "presion_diferencial", 1.2, "kg/cm2"),
        ]
    )
    por_equipo = {evento.equipo: evento for evento in eventos}
    assert set(por_equipo) == {"B-101", "B-102", "F-201A"}
    assert por_equipo["B-101"].tipo is TipoEvento.sin_novedad
    assert por_equipo["B-102"].tipo is TipoEvento.escalado
    assert por_equipo["F-201A"].tipo is TipoEvento.sin_novedad


def test_ids_de_eventos_son_distintos_por_equipo() -> None:
    eventos = _clasificar(
        [
            Hallazgo("B-101", "presion_descarga_local", 110, "kg/cm2"),
            Hallazgo("B-102", "presion_descarga_local", 1600, "psi"),
        ]
    )
    ids = {evento.id for evento in eventos}
    assert len(ids) == 2
    assert ids == {"parte:NOTA-14:B-101", "parte:NOTA-14:B-102"}


def test_equipo_en_reserva_o_mantenimiento_no_es_anomalia() -> None:
    """MBP-4: una bomba parada en reserva o en mantenimiento no es una falla."""
    eventos = _clasificar(
        [
            Hallazgo("B-101", "estado", None, None, "parada, en reserva"),
            Hallazgo("B-103", "estado", None, None, "parada por mantenimiento"),
        ]
    )
    assert all(evento.tipo is TipoEvento.sin_novedad for evento in eventos)


def test_equipo_disparado_es_anomalia() -> None:
    eventos = _clasificar(
        [Hallazgo("B-102", "estado", None, None, "disparada, no arranca")]
    )
    assert eventos[0].tipo is TipoEvento.escalado


def test_equipo_parado_en_reserva_no_es_anomalia_desde_la_nota(planta) -> None:
    """La observación sale de la nota, no de una fila armada a mano."""
    extraccion = ExtractorParte(planta).extraer("La 1 está disparada, no arranca.")
    eventos = clasificar_partes(
        nota_id="NOTA-14",
        parte_id="parte-uuid",
        ts=TS,
        filas=extraccion.filas,
        planta=_planta(),
    )
    assert len(eventos) == 1
    assert eventos[0].tipo is TipoEvento.escalado
    assert eventos[0].equipo == "B-101"


def test_evento_escalado_lleva_el_contexto_del_parte() -> None:
    eventos = _clasificar(
        [
            Hallazgo(
                "B-102",
                "goteo_empaquetadura",
                None,
                None,
                "pérdida importante por empaquetadura",
            )
        ],
        fotos=2,
    )
    resumen = eventos[0].resumen
    assert "1 fila(s)" in resumen
    assert "2 foto(s)" in resumen


def test_nota_sin_novedad_no_es_un_equipo() -> None:
    """El tag virtual PLANTA no es un equipo: el evento sale sin equipo."""
    eventos = _clasificar(
        [Hallazgo("PLANTA", "estado", None, None, "Sin novedad")]
    )
    assert len(eventos) == 1
    assert eventos[0].equipo is None
    assert eventos[0].tipo is TipoEvento.sin_novedad
