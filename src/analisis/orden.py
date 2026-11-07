"""Borrador de orden de trabajo: equipo, tareas, repuestos, costo y evidencia.

El borrador sale de la falla que se identificó y del plan de mantenimiento
(MBP-4): la tarea que resuelve la falla, si detiene el equipo, qué repuestos
hacen falta y cuánto cuestan. Todo lleva la cita del manual del que salió.

Si la falla se repite, el manual pide cambiar la acción (inspeccionar el
émbolo, revisar la filtración antes de volver a cambiar), así que la orden
suma una tarea no estándar y va al planificador.
"""

from __future__ import annotations

import re

from analisis.catalogo import CatalogoManuales
from analisis.evidencia import Evidencia
from analisis.modelos import (
    BorradorOrden,
    CausaProbable,
    FallaCatalogo,
    RepuestoOrden,
)
from tools.modelos import RepuestoTarea, TareaCatalogo

# Tarea para lo que no está en el catálogo (MBP-4 §13.2).
TAREA_NO_ESTANDAR = "NO-ESTANDAR"
CITA_REPUESTOS = "MBP-4 §14.2"
# Palabras con las que el manual avisa que la falla se repitió.
_REINCIDENCIA = re.compile(r"[^.;]*se repite[^.;]*[.;]?", re.IGNORECASE)


def armar_borrador(
    *,
    equipo: str,
    falla: FallaCatalogo | None,
    tarea: TareaCatalogo | None,
    catalogo: CatalogoManuales,
    estado_bombas: dict[str, str],
    evidencia: Evidencia,
    causas: tuple[CausaProbable, ...],
    reincidencia: bool = False,
    almacen: str = "yacimiento",
) -> BorradorOrden:
    """Arma el borrador de la orden a partir de la falla identificada."""
    tareas = list(_tareas(tarea))
    if reincidencia and TAREA_NO_ESTANDAR not in tareas:
        tareas.append(TAREA_NO_ESTANDAR)

    detiene = _detiene(tarea, falla)
    reduce_inyeccion = _reduce_inyeccion(equipo, detiene, estado_bombas)
    ejecutor = _ejecutor(tarea)
    repuestos = _repuestos(tarea, falla, equipo, catalogo, almacen)
    costo = sum(repuesto.cantidad * repuesto.costo_unitario for repuesto in repuestos)

    return BorradorOrden(
        id=_identificador(equipo, falla),
        equipo=equipo,
        falla=falla.codigo if falla else "",
        tareas=tuple(tareas),
        detiene_equipo=detiene,
        reduce_inyeccion=reduce_inyeccion,
        ejecutor=ejecutor,
        repuestos=repuestos,
        costo_usd=costo,
        evidencia=evidencia.descripciones,
        citas=_citas(falla, tarea),
        motivo=None,
        descripcion=_descripcion(tarea, falla, reincidencia),
        causas=tuple(causa.causa for causa in causas[:3]),
    )


def _descripcion(
    tarea: TareaCatalogo | None,
    falla: FallaCatalogo | None,
    reincidencia: bool,
) -> str | None:
    """Qué hay que hacer cuando la tarea no está en el catálogo del plan.

    MBP-4 §13.2 pide las tareas que no figuran en el plan como NO-ESTANDAR
    **con la descripción** de lo que hay que hacer. La descripción sale de la
    ficha de la falla del manual y, si la falla se repite, de lo que el manual
    pide cambiar la segunda vez.
    """
    partes: list[str] = []
    if tarea is None and falla is not None:
        accion = (falla.accion or falla.como_confirmar).strip()
        if accion:
            partes.append(accion)
    if reincidencia:
        extra = descripcion_de_reincidencia(falla)
        if extra:
            partes.append(extra)
    return " ".join(partes) or None


def _tareas(tarea: TareaCatalogo | None) -> tuple[str, ...]:
    if tarea is None:
        return (TAREA_NO_ESTANDAR,)
    return (tarea.codigo,)


def _detiene(tarea: TareaCatalogo | None, falla: FallaCatalogo | None) -> bool:
    if tarea is not None:
        return bool(tarea.detiene)
    if falla is not None and falla.detiene is not None:
        return falla.detiene
    return False


def _ejecutor(tarea: TareaCatalogo | None) -> str:
    if tarea is None:
        return "propio"
    return (tarea.ejecutor or "propio").strip().lower()


def _reduce_inyeccion(
    equipo: str,
    detiene: bool,
    estado_bombas: dict[str, str],
) -> bool:
    """¿Detener el equipo baja la inyección? Sólo si no hay bomba de reserva."""
    if not detiene:
        return False
    if estado_bombas.get(equipo) != "marcha":
        return False
    return not any(
        tag != equipo and estado == "parada" for tag, estado in estado_bombas.items()
    )


def _repuestos(
    tarea: TareaCatalogo | None,
    falla: FallaCatalogo | None,
    equipo: str,
    catalogo: CatalogoManuales,
    almacen: str,
) -> tuple[RepuestoOrden, ...]:
    """Los repuestos de la orden y si hay stock.

    Para una tarea estándar manda el plan de mantenimiento (MBP-4 §13.1), que
    ya sabe qué repuesto va con cada equipo. Para una tarea que no está en el
    catálogo manda la ficha de la falla (TX350-D §8.5, por ejemplo).
    """
    if tarea is not None and tarea.estandar:
        pedidos: list[RepuestoTarea] = list(tarea.repuestos)
    elif falla is not None:
        pedidos = [
            RepuestoTarea(codigo=repuesto.codigo, cantidad=repuesto.cantidad)
            for repuesto in falla.repuestos
        ]
    else:
        pedidos = []
    salida: list[RepuestoOrden] = []
    for pedido in pedidos:
        stock = catalogo.stock(pedido.codigo, almacen)
        salida.append(
            RepuestoOrden(
                codigo=pedido.codigo,
                cantidad=float(pedido.cantidad),
                costo_unitario=catalogo.costo_unitario(pedido.codigo),
                disponible=stock >= float(pedido.cantidad),
            )
        )
    return tuple(salida)


def _citas(
    falla: FallaCatalogo | None,
    tarea: TareaCatalogo | None,
) -> tuple[str, ...]:
    citas: list[str] = []
    if falla is not None:
        citas.append(falla.cita)
        if falla.tabla_cita and falla.tabla_cita not in citas:
            citas.append(falla.tabla_cita)
    if tarea is not None and tarea.cita and tarea.cita not in citas:
        citas.append(tarea.cita)
    if falla is not None and not tarea:
        citas.append("MBP-4 §13.2")
    if citas:
        citas.append(CITA_REPUESTOS)
    return tuple(citas)


def _identificador(equipo: str, falla: FallaCatalogo | None) -> str:
    """Id de la orden: determinista por equipo y falla.

    El mismo equipo con la misma falla no abre dos órdenes: la segunda
    actualiza la primera. Así una falla que se repite no duplica trabajo.
    """
    sufijo = falla.codigo if falla is not None else "INSPECCION"
    return f"ORD-{equipo}-{sufijo}"


def descripcion_de_reincidencia(falla: FallaCatalogo | None) -> str | None:
    """Lo que el manual pide cambiar cuando la falla se repite."""
    if falla is None:
        return None
    coincidencia = _REINCIDENCIA.search(falla.accion)
    if not coincidencia:
        return None
    return coincidencia.group(0).strip().rstrip(".").strip() or None
