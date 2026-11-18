"""Tests de casos límite de las reglas de aprobación (Anexo Entrega 2).

La regla de costo dice: menor que USD 2.500. Los casos límite del anexo son:
- USD 2.499: cumple (está por debajo del tope).
- USD 2.500: no cumple (es igual al tope, ``>=`` lo rechaza).

También se prueban los otros casos límite mencionados:
- T-B-VAL a USD 1.900: tarea estándar, costo bajo, cumple.
- TX-ROD-CIG sin stock en el yacimiento: no cumple la regla de stock.
- Reserva no disponible: no cumple la regla de inyección.

Estos tests se ejecutan sin base de datos ni modelo de lenguaje.
"""

from __future__ import annotations

from analisis.aprobacion import Contexto, evaluar
from analisis.catalogo import CatalogoManuales
from analisis.modelos import BorradorOrden, RepuestoOrden
from manuales import documentos


def _catalogo() -> CatalogoManuales:
    return CatalogoManuales.desde_documentos(documentos())


def _contexto(
    catalogo: CatalogoManuales,
    *,
    bombas: dict[str, str] | None = None,
    almacen: str = "yacimiento",
    revision_humana: bool = False,
    motivo_revision_humana: str | None = None,
) -> Contexto:
    return Contexto(
        catalogo=catalogo,
        estado_bombas=bombas if bombas is not None else {"B-101": "marcha", "B-102": "parada"},
        almacen=almacen,
        revision_humana=revision_humana,
        motivo_revision_humana=motivo_revision_humana,
    )


def _borrador(
    *,
    equipo: str = "B-101",
    tareas: tuple[str, ...] = ("T-B-EMP",),
    detiene_equipo: bool = False,
    reduce_inyeccion: bool = False,
    ejecutor: str = "propio",
    repuestos: tuple[RepuestoOrden, ...] = (),
    costo_usd: float = 0.0,
    citas: tuple[str, ...] = ("TX350-D §8.3",),
    evidencia: tuple[str, ...] = (),
    motivo: str | None = None,
) -> BorradorOrden:
    return BorradorOrden(
        id="ORD-TEST",
        equipo=equipo,
        falla="TX-T01",
        tareas=tareas,
        detiene_equipo=detiene_equipo,
        reduce_inyeccion=reduce_inyeccion,
        ejecutor=ejecutor,
        repuestos=repuestos,
        costo_usd=costo_usd,
        evidencia=evidencia,
        citas=citas,
        motivo=motivo,
    )


# ------------------------------------------------------------------- costo límite


def test_costo_2499_cumple_el_tope():
    """USD 2.499 está por debajo del tope de 2.500: cumple."""
    catalogo = _catalogo()
    borrador = _borrador(costo_usd=2499.0)
    resultado = evaluar(borrador, _contexto(catalogo))
    assert resultado.cumple is True
    falladas = {r.codigo for r in resultado.reglas_falladas}
    assert "costo" not in falladas


def test_costo_2500_no_cumple_el_tope():
    """USD 2.500 es igual al tope: ``>=`` lo rechaza."""
    catalogo = _catalogo()
    borrador = _borrador(costo_usd=2500.0)
    resultado = evaluar(borrador, _contexto(catalogo))
    assert resultado.cumple is False
    falladas = {r.codigo for r in resultado.reglas_falladas}
    assert "costo" in falladas


def test_costo_2500_01_no_cumple_el_tope():
    """USD 2.500.01 excede el tope: no cumple."""
    catalogo = _catalogo()
    borrador = _borrador(costo_usd=2500.01)
    resultado = evaluar(borrador, _contexto(catalogo))
    assert resultado.cumple is False
    falladas = {r.codigo for r in resultado.reglas_falladas}
    assert "costo" in falladas


# ------------------------------------------------------------------- T-B-VAL


def test_t_b_val_cumple_todas_las_reglas():
    """T-B-VAL a USD 1.900: tarea estándar, costo bajo, personal propio."""
    catalogo = _catalogo()
    # T-B-VAL es tarea estándar de B-102, con repuesto TX-VAL-KIT y costo 1.900.
    repuesto = RepuestoOrden(
        codigo="TX-VAL-KIT",
        cantidad=1.0,
        costo_unitario=1900.0,
        disponible=True,
    )
    borrador = _borrador(
        equipo="B-102",
        tareas=("T-B-VAL",),
        repuestos=(repuesto,),
        costo_usd=1900.0,
    )
    resultado = evaluar(borrador, _contexto(catalogo))
    assert resultado.cumple is True
    falladas = {r.codigo for r in resultado.reglas_falladas}
    assert falladas == set()


# ------------------------------------------------------------------- TX-ROD-CIG sin stock


def test_tx_rod_cig_sin_stock_no_cumple():
    """TX-ROD-CIG sin stock en el yacimiento: no cumple la regla de stock."""
    catalogo = _catalogo()
    # TX-ROD-CIG tiene stock 0 en el yacimiento (ver manuales.py).
    repuesto = RepuestoOrden(
        codigo="TX-ROD-CIG",
        cantidad=1.0,
        costo_unitario=2650.0,
        disponible=False,
    )
    borrador = _borrador(
        equipo="B-101",
        tareas=("T-B-ROD",),
        repuestos=(repuesto,),
        costo_usd=2650.0,
    )
    resultado = evaluar(borrador, _contexto(catalogo))
    assert resultado.cumple is False
    falladas = {r.codigo for r in resultado.reglas_falladas}
    assert "stock" in falladas


# ------------------------------------------------------------------- reserva no disponible


def test_reserva_no_disponible_no_cumple_inyeccion():
    """Detener el equipo sin bomba de reserva: no cumple la regla de inyección."""
    catalogo = _catalogo()
    # Todas las bombas en marcha o mantenimiento: no hay reserva parada.
    borrador = _borrador(
        equipo="B-102",
        tareas=("T-B-EMP",),
        detiene_equipo=True,
        ejecutor="propio",
        costo_usd=500.0,
    )
    # SIN_RESERVA: B-101 en marcha, B-102 en marcha, B-103 en mantenimiento.
    bombas_sin_reserva = {"B-101": "marcha", "B-102": "marcha", "B-103": "mantenimiento"}
    resultado = evaluar(borrador, _contexto(catalogo, bombas=bombas_sin_reserva))
    assert resultado.cumple is False
    falladas = {r.codigo for r in resultado.reglas_falladas}
    assert "inyeccion" in falladas


# ------------------------------------------------------------------- daño estructural


def test_dano_estructural_siempre_a_revision_humana():
    """Una foto con daño estructural grave va siempre a revisión humana."""
    catalogo = _catalogo()
    borrador = _borrador(costo_usd=500.0)
    motivo = "foto muestra grieta en carcasa"
    resultado = evaluar(
        borrador,
        _contexto(catalogo, revision_humana=True, motivo_revision_humana=motivo),
    )
    assert resultado.cumple is False
    falladas = {r.codigo for r in resultado.reglas_falladas}
    assert "revision_humana" in falladas


# ------------------------------------------------------------------- personal propio


def test_contratista_no_cumple_personal_propio():
    """El trabajo de la contratista no cumple la regla de personal propio."""
    catalogo = _catalogo()
    borrador = _borrador(ejecutor="contratista", costo_usd=500.0)
    resultado = evaluar(borrador, _contexto(catalogo))
    assert resultado.cumple is False
    falladas = {r.codigo for r in resultado.reglas_falladas}
    assert "personal" in falladas


# ------------------------------------------------------------------- tarea no estándar


def test_tarea_no_estandar_no_cumple():
    """Una tarea que no está en el catálogo del equipo no cumple."""
    catalogo = _catalogo()
    borrador = _borrador(tareas=("T-X-INEXISTENTE",), costo_usd=500.0)
    resultado = evaluar(borrador, _contexto(catalogo))
    assert resultado.cumple is False
    falladas = {r.codigo for r in resultado.reglas_falladas}
    assert "tarea_estandar" in falladas
