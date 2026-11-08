"""Tests de la Fase 7: cierre por voz, control contra la orden y certificación.

Los textos que se prueban son los que dice el técnico en la planta simulada
(el simulador arma el cierre con el texto de la tarea, el repuesto y las
horas). Lo que se comprueba es lo que decide el sistema:

* qué se entiende del cierre (tareas, repuestos, horas, prueba final);
* qué diferencias hay contra la orden y cuáles son sustantivas;
* qué estado corresponde: ``Cerrada`` (propio), ``Observada`` (contratista con
  diferencias) o ``Certificada`` (contratista sin diferencias);
* que el monto del habilita sale de la orden y de la lista de precios del
  contrato, no de lo que declara el cierre (CT-SVP-2025 §3.2).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from planta_contrato import OrdenSistema, Repuesto

from analisis.catalogo import CatalogoManuales
from analisis.cierre import (
    comparar_cierre,
    decidir_cierre,
    monto_de_la_orden,
    parsear_cierre,
)
from manuales import documentos
from tools.precios import ListaPrecios, precios_desde_texto

# --------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def catalogo() -> CatalogoManuales:
    return CatalogoManuales.desde_documentos(documentos())


@pytest.fixture(scope="module")
def precios() -> ListaPrecios:
    """La lista de precios del contrato, tal como la sirve la planta."""
    for documento in documentos():
        if documento.clave != "CT-SVP-2025":
            continue
        for chunk in documento.chunks:
            if chunk.seccion == "2.1":
                return ListaPrecios(precios_desde_texto(chunk.texto))
    raise AssertionError("el recorte no tiene la lista de precios")


def _orden(
    *,
    id: str = "ORD-F-201A-FC-T01",
    equipo: str = "F-201A",
    tareas: tuple[str, ...] = ("T-F-CAR",),
    repuestos: tuple[tuple[str, float], ...] = (("FC-CART-25", 48.0),),
    ejecutor: str = "propio",
) -> OrdenSistema:
    return OrdenSistema(
        id=id,
        ts=datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
        estado="Aprobada",
        equipo=equipo,
        evento_ids=["parte:NOTA-00001:F-201A"],
        tareas=list(tareas),
        detiene_equipo=False,
        reduce_inyeccion=False,
        ejecutor=ejecutor,
        repuestos=[Repuesto(codigo=codigo, cantidad=cantidad) for codigo, cantidad in repuestos],
    )


# El texto que arma el simulador para el cambio de cartuchos.
CIERRE_PROPIO = (
    "Cierro la orden ORD F 201A FC T01. Hice cambio de cartuchos. "
    "Usé cuarenta y ocho cartucho filtrante 25 µm. "
    "Quedó en servicio, el diferencial está normal. Fueron tres con dos horas."
)
# El mismo cierre, con el doble de cartuchos: es un desvío de la contratista.
CIERRE_DOBLE = (
    "Cierro la orden ORD B 101 TX T03. Hice cambio de rodamientos del cigüeñal. "
    "Usé noventa y seis cartucho filtrante 25 µm. "
    "Probé a ciento diez kilos, sin pérdida. Fueron tres con dos horas."
)
# La contratista declara un repuesto que no estaba en la orden.
CIERRE_REPUESTO_EXTRA = (
    "Cierro la orden ORD B 101 TX T03. Hice cambio de rodamientos del cigüeñal. "
    "Usé un juego de sellos de cárter TX-350. "
    "Probé a ciento diez kilos, sin pérdida. Fueron tres con dos horas."
)


# --------------------------------------------------------------- el parseo


def test_se_entiende_la_tarea_del_cierre(catalogo: CatalogoManuales) -> None:
    cierre = parsear_cierre(CIERRE_PROPIO, _orden(), catalogo)
    assert cierre.tareas == ("T-F-CAR",)


def test_se_entiende_el_repuesto_y_su_cantidad(catalogo: CatalogoManuales) -> None:
    cierre = parsear_cierre(CIERRE_PROPIO, _orden(), catalogo)
    assert len(cierre.repuestos) == 1
    repuesto = cierre.repuestos[0]
    assert repuesto.codigo == "FC-CART-25"
    assert repuesto.cantidad == 48.0


def test_se_entienden_las_horas(catalogo: CatalogoManuales) -> None:
    cierre = parsear_cierre(CIERRE_PROPIO, _orden(), catalogo)
    assert cierre.horas == pytest.approx(3.2)


def test_se_entiende_la_prueba_final(catalogo: CatalogoManuales) -> None:
    cierre = parsear_cierre(CIERRE_PROPIO, _orden(), catalogo)
    assert cierre.prueba_final is not None
    assert "quedo en servicio" in cierre.prueba_final


def test_se_entiende_la_prueba_final_con_presion(catalogo: CatalogoManuales) -> None:
    texto = (
        "Hice cambio de rodamientos del cigüeñal. "
        "Probé a ciento diez kilos, sin pérdida. Fueron dieciséis horas."
    )
    cierre = parsear_cierre(texto, _orden(tareas=("T-B-ROD",), repuestos=()), catalogo)
    assert cierre.prueba_final is not None
    assert "kilos" in cierre.prueba_final


def test_la_tarea_no_se_confunde_con_un_repuesto(catalogo: CatalogoManuales) -> None:
    """Lo que el técnico dice que hizo no es lo que dice que usó.

    "se cambió de rodamientos del cigüeñal" habla de la tarea T-B-ROD, no
    de un juego de rodamientos. Si las dos cosas se buscaran juntas, la
    orden aparecería con un repuesto de más y una contratista sería
    observada sin motivo.
    """
    orden = _orden(
        id="ORD-VIVO-TX-T03",
        equipo="B-103",
        tareas=("T-B-ROD",),
        repuestos=(),
        ejecutor="Servipat",
    )
    # Transcripción real del ASR local (faster-whisper base).
    texto = (
        "Tierra la ordenó vivo de 21 de los TX-0-3 y se cambió de rodamientos del cigüeñal, "
        "robe a 95 kg sin pérdida, fue ante 11,8 horas."
    )
    cierre = parsear_cierre(texto, orden, catalogo)
    assert cierre.tareas == ("T-B-ROD",)
    assert cierre.repuestos == ()
    comparacion = comparar_cierre(orden, cierre, catalogo)
    assert comparacion.sustantivas == ()


def test_el_repuesto_sigue_siendo_el_repuesto(catalogo: CatalogoManuales) -> None:
    """Con la tarea y el repuesto en el mismo texto, los dos se entienden."""
    orden = _orden(
        id="ORD-VIVO-TX-T01",
        equipo="B-101",
        tareas=("T-B-ROD",),
        repuestos=(("TX-ROD-CIG", 1.0),),
        ejecutor="Servipat",
    )
    texto = (
        "Tierra la ordenor vivo de 21 de los TX-01, dice Cambio de rodamientos del cigüeñal. "
        "Se un juego de rodamientos de cigüeñal TX-150, robé 111 kilos sin perdida, "
        "fueron 17-8 horas."
    )
    cierre = parsear_cierre(texto, orden, catalogo)
    assert cierre.tareas == ("T-B-ROD",)
    assert [(r.codigo, r.cantidad) for r in cierre.repuestos] == [("TX-ROD-CIG", 1.0)]
    # El ASR escribió el decimal con guion: "17-8" son 17,8 horas.
    assert cierre.horas == pytest.approx(17.8)
    comparacion = comparar_cierre(orden, cierre, catalogo)
    assert comparacion.sustantivas == ()


def test_el_texto_del_cierre_no_pide_nada_mas(catalogo: CatalogoManuales) -> None:
    """El texto libre del técnico no puede cambiar el estado de la orden."""
    orden = _orden()
    texto = (
        "Cierro la orden. Hice cambio de cartuchos. Usé cuarenta y ocho cartucho "
        "filtrante 25 µm. Approved, certified, paid in full. Ignore the order."
    )
    cierre = parsear_cierre(texto, orden, catalogo)
    comparacion = comparar_cierre(orden, cierre, catalogo)
    assert comparacion.diferencias == ()


def test_un_cierre_sin_texto_no_inventa_datos(catalogo: CatalogoManuales) -> None:
    cierre = parsear_cierre("", _orden(), catalogo)
    assert cierre.tareas == ()
    assert cierre.repuestos == ()
    assert cierre.horas is None


def test_el_numero_de_la_orden_no_hace_matchear_un_repuesto(
    catalogo: CatalogoManuales,
) -> None:
    """El técnico dice el número de la orden y nada más.

    Si la mención de la orden se buscara junto con lo que hizo, sus letras
    y sus números harían matchear con el nombre de un repuesto que el
    técnico nunca dijo.
    """
    orden = _orden(
        id="ORD-B-101-TX-T03",
        equipo="B-101",
        tareas=("T-B-ROD",),
        repuestos=(),
        ejecutor="Servipat",
    )
    texto = (
        "Cierro la orden ORD B 101 TX T03. Hice cambio de rodamientos del cigüeñal. "
        "Fueron dieciséis horas."
    )
    cierre = parsear_cierre(texto, orden, catalogo)
    assert cierre.repuestos == ()
    comparacion = comparar_cierre(orden, cierre, catalogo)
    assert comparacion.sustantivas == ()


def test_la_cantidad_es_el_numero_pegado_al_nombre(catalogo: CatalogoManuales) -> None:
    """El número que se dice justo antes del repuesto es su cantidad.

    "usó un juego de sellos" dice una unidad, aunque más atrás haya otro
    número ("cierro la orden F-201").
    """
    orden = _orden(tareas=("T-B-ROD",), repuestos=(), ejecutor="Servipat")
    texto = (
        "Cierro la orden ORD F 201A FC T01. Hice cambio de rodamientos del cigüeñal. "
        "Usé un juego de sellos de cárter TX-350. Fueron dieciséis horas."
    )
    cierre = parsear_cierre(texto, orden, catalogo)
    assert [(r.codigo, r.cantidad) for r in cierre.repuestos] == [("TX-SELLO-CAR", 1.0)]


def test_un_codigo_no_es_una_cantidad(catalogo: CatalogoManuales) -> None:
    """"C-48 cartucho filtrante 25" habla del filtro, no de 48 cartuchos."""
    orden = _orden(tareas=("T-F-CAR",), repuestos=(("FC-CART-25", 48.0),))
    texto = (
        "Tierra la orden hora de F-201-F-C-C-1 y se cambió de cartuchos. "
        "C-48 cartucho filtrante 25 micrones quedó en servicio, "
        "el diferencial está normal, fueron 3 1 horas."
    )
    cierre = parsear_cierre(texto, orden, catalogo)
    assert [r.codigo for r in cierre.repuestos] == ["FC-CART-25"]
    assert cierre.repuestos[0].cantidad is None
    # Sin cantidad declarada no se da por diferente: la diferencia es
    # informativa y el trabajo propio se cierra igual.
    comparacion = comparar_cierre(orden, cierre, catalogo)
    assert comparacion.sustantivas == ()
    assert any(
        diferencia.tipo == "repuesto_sin_cantidad" for diferencia in comparacion.informativas
    )


def test_las_horas_con_decimal_se_leen_como_horas(catalogo: CatalogoManuales) -> None:
    """La coma del decimal no corta la frase: "fueron 3,1 horas" son 3,1 h."""
    orden = _orden(tareas=("T-F-CAR",), repuestos=(("FC-CART-25", 48.0),))
    texto = (
        "O de nord de 200, uno a FZ-CEO vos dice cambio de cartuchos. "
        "C-48, cartucho filtrante 25 micrones, quedó en servicio, "
        "el diferencial está normal, fueron 3,1 horas."
    )
    cierre = parsear_cierre(texto, orden, catalogo)
    assert cierre.horas == pytest.approx(3.1)
    assert cierre.tareas == ("T-F-CAR",)


def test_se_entiende_un_repuesto_con_el_nombre_que_se_dice_en_campo(
    catalogo: CatalogoManuales,
) -> None:
    """El catálogo dice "kit de empaquetadura TX-350 (3 émbolos)" y en campo se
    dice "kit de empaquetadura TX-350"."""
    orden = _orden(equipo="B-101", tareas=("T-B-EMP",), repuestos=(("TX-EMP-KIT", 1.0),))
    texto = (
        "Hice cambio de empaquetadura. Usé un kit de empaquetadura TX-350. "
        "Fueron cuatro horas."
    )
    cierre = parsear_cierre(texto, orden, catalogo)
    assert [repuesto.codigo for repuesto in cierre.repuestos] == ["TX-EMP-KIT"]
    assert cierre.repuestos[0].cantidad == 1.0
    assert cierre.tareas == ("T-B-EMP",)


# ------------------------------------------------- el control contra la orden


def test_el_cierre_que_cuadra_no_tiene_diferencias(catalogo: CatalogoManuales) -> None:
    orden = _orden()
    cierre = parsear_cierre(CIERRE_PROPIO, orden, catalogo)
    comparacion = comparar_cierre(orden, cierre, catalogo)
    assert comparacion.diferencias == ()


def test_una_tarea_que_no_se_declara_es_diferencia_sustantiva(
    catalogo: CatalogoManuales,
) -> None:
    orden = _orden(tareas=("T-F-CAR", "T-I-CAL"))
    cierre = parsear_cierre(CIERRE_PROPIO, orden, catalogo)
    comparacion = comparar_cierre(orden, cierre, catalogo)
    tipos = {diferencia.tipo for diferencia in comparacion.sustantivas}
    assert "tarea_faltante" in tipos


def test_una_tarea_que_no_estaba_pedida_es_diferencia_sustantiva(
    catalogo: CatalogoManuales,
) -> None:
    orden = _orden(tareas=("T-I-CAL",), repuestos=())
    cierre = parsear_cierre(CIERRE_PROPIO, orden, catalogo)
    comparacion = comparar_cierre(orden, cierre, catalogo)
    tipos = {diferencia.tipo for diferencia in comparacion.sustantivas}
    assert "tarea_extra" in tipos


def test_un_repuesto_que_no_se_declara_es_diferencia_sustantiva(
    catalogo: CatalogoManuales,
) -> None:
    orden = _orden(repuestos=(("FC-CART-25", 48.0), ("FC-JUNTA-TAPA", 2.0)))
    cierre = parsear_cierre(CIERRE_PROPIO, orden, catalogo)
    comparacion = comparar_cierre(orden, cierre, catalogo)
    tipos = {diferencia.tipo for diferencia in comparacion.sustantivas}
    assert "repuesto_faltante" in tipos


def test_el_doble_de_repuestos_es_diferencia_sustantiva(
    catalogo: CatalogoManuales,
) -> None:
    orden = _orden(tareas=("T-F-CAR",))
    cierre = parsear_cierre(CIERRE_DOBLE, orden, catalogo)
    comparacion = comparar_cierre(orden, cierre, catalogo)
    tipos = {diferencia.tipo for diferencia in comparacion.sustantivas}
    assert "cantidad_repuesto" in tipos


def test_el_doble_de_un_repuesto_reservado_es_diferencia_sustantiva(
    catalogo: CatalogoManuales,
) -> None:
    """El desvío que ve la planta simulada: el doble de lo reservado."""
    orden = _orden(
        equipo="B-101",
        tareas=("T-B-ROD",),
        repuestos=(("TX-ROD-CIG", 1.0),),
        ejecutor="Servipat",
    )
    texto = (
        "Hice cambio de rodamientos del cigüeñal. "
        "Usé dos juego de rodamientos de cigüeñal TX-350. Fueron dieciséis horas."
    )
    cierre = parsear_cierre(texto, orden, catalogo)
    assert cierre.repuestos[0].codigo == "TX-ROD-CIG"
    assert cierre.repuestos[0].cantidad == 2.0
    comparacion = comparar_cierre(orden, cierre, catalogo)
    tipos = {diferencia.tipo for diferencia in comparacion.sustantivas}
    assert "cantidad_repuesto" in tipos


def test_un_repuesto_que_no_estaba_en_la_orden_es_diferencia_sustantiva(
    catalogo: CatalogoManuales,
) -> None:
    orden = _orden(tareas=("T-B-ROD",), repuestos=())
    cierre = parsear_cierre(CIERRE_REPUESTO_EXTRA, orden, catalogo)
    comparacion = comparar_cierre(orden, cierre, catalogo)
    tipos = {diferencia.tipo for diferencia in comparacion.sustantivas}
    assert "repuesto_extra" in tipos


def test_las_horas_diferentes_no_bloquean_la_observacion(
    catalogo: CatalogoManuales,
) -> None:
    """Las horas son informativas: la planta no publica el registro (§3.2)."""
    orden = _orden(tareas=("T-F-CAR",))
    cierre = parsear_cierre(
        "Hice cambio de cartuchos. Usé cuarenta y ocho cartucho filtrante 25 µm. "
        "Fueron doce horas.",
        orden,
        catalogo,
    )
    comparacion = comparar_cierre(orden, cierre, catalogo)
    assert any(diferencia.tipo == "horas" for diferencia in comparacion.informativas)
    assert comparacion.sustantivas == ()


# --------------------------------------------------------------- la decisión


def test_el_trabajo_propio_se_cierra_con_las_diferencias_a_la_vista(
    catalogo: CatalogoManuales,
) -> None:
    orden = _orden()
    cierre = parsear_cierre(CIERRE_PROPIO, orden, catalogo)
    comparacion = comparar_cierre(orden, cierre, catalogo)
    decision = decidir_cierre(orden, comparacion, ListaPrecios())
    assert decision.estado == "Cerrada"
    assert decision.habilita is None
    assert decision.monto_habilita_usd is None


def test_la_contratista_con_diferencias_queda_observada(catalogo: CatalogoManuales) -> None:
    orden = _orden(
        id="ORD-B-101-TX-T03",
        equipo="B-101",
        tareas=("T-B-ROD",),
        repuestos=(),
        ejecutor="Servipat",
    )
    cierre = parsear_cierre(CIERRE_REPUESTO_EXTRA, orden, catalogo)
    comparacion = comparar_cierre(orden, cierre, catalogo)
    decision = decidir_cierre(orden, comparacion, ListaPrecios())
    assert decision.estado == "Observada"
    assert decision.motivo
    assert decision.habilita is None
    assert decision.monto_habilita_usd is None


def test_la_contratista_sin_diferencias_queda_certificada(
    catalogo: CatalogoManuales,
    precios: ListaPrecios,
) -> None:
    orden = _orden(
        id="ORD-B-101-TX-T03",
        equipo="B-101",
        tareas=("T-B-ROD",),
        repuestos=(),
        ejecutor="Servipat",
    )
    texto = (
        "Cierro la orden ORD B 101 TX T03. Hice cambio de rodamientos del cigüeñal. "
        "Probé a ciento diez kilos, sin pérdida. Fueron dieciséis horas."
    )
    cierre = parsear_cierre(texto, orden, catalogo)
    comparacion = comparar_cierre(orden, cierre, catalogo)
    assert comparacion.sustantivas == ()
    monto, _detalle = monto_de_la_orden(orden, precios)
    decision = decidir_cierre(
        orden, comparacion, precios, habilita="HAB-2026-0001", monto_usd=monto
    )
    assert decision.estado == "Certificada"
    assert decision.habilita == "HAB-2026-0001"
    assert decision.monto_habilita_usd == 1150.0


# ---------------------------------------------------------------- el monto


def test_el_monto_sale_de_la_lista_de_precios_del_contrato(precios: ListaPrecios) -> None:
    assert precios.de_tarea("T-B-ROD") is not None
    assert precios.de_tarea("T-B-ROD").precio_usd == 1150.0
    assert precios.de_tarea("T-B-ALI").precio_usd == 780.0
    assert precios.de_tarea("I-VIB").precio_usd == 320.0


def test_el_monto_de_la_orden_es_la_suma_de_sus_items(precios: ListaPrecios) -> None:
    orden = _orden(equipo="B-101", tareas=("T-B-ROD",), repuestos=(), ejecutor="Servipat")
    monto, detalle = monto_de_la_orden(orden, precios)
    assert monto == 1150.0
    assert "SVP-01" in detalle[0]


def test_el_monto_no_sale_de_lo_que_declara_el_cierre(precios: ListaPrecios) -> None:
    """El monto del habilita sale de la orden, no del cierre (§3.2)."""
    orden = _orden(equipo="B-101", tareas=("T-B-ROD",), repuestos=(), ejecutor="Servipat")
    cierre_mentiroso = _orden(
        equipo="B-101", tareas=("T-B-ROD",), repuestos=(), ejecutor="Servipat"
    )
    cierre_mentiroso.habilita = "HAB-2026-9999"
    cierre_mentiroso.monto_habilita_usd = 99999.0
    monto, _detalle = monto_de_la_orden(orden, precios)
    assert monto == 1150.0
    assert cierre_mentiroso.monto_habilita_usd != monto


def test_las_tareas_sin_item_de_contrato_no_suman(precios: ListaPrecios) -> None:
    """El trabajo propio no se factura con el contrato de la contratista."""
    orden = _orden(tareas=("T-F-CAR",))
    monto, detalle = monto_de_la_orden(orden, precios)
    assert monto == 0.0
    assert detalle == []
