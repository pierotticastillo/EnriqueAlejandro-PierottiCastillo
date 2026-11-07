"""Reglas de aprobación de una orden, evaluadas en código.

Las reglas no cambian por lo que diga una nota: están acá, en el sistema, y se
aplican siempre igual. MBP-4 §13.2 dice que las tareas no estándar van al
planificador; §14.2 y §14.3 dan el stock y la lista de materiales por equipo;
el plan de mantenimiento fija el tope de USD 2.500 por orden.

Si una orden no cumple todas las reglas, queda *Pendiente de aprobación* con
el motivo escrito. Si cumple todas, queda *Aprobada*.
"""

from __future__ import annotations

from dataclasses import dataclass

from analisis.catalogo import CatalogoManuales
from analisis.modelos import BorradorOrden, Evaluacion, Regla

# Tope por orden del plan de mantenimiento.
COSTO_MAX_USD = 2500.0
ALMACEN = "yacimiento"


@dataclass(frozen=True)
class Contexto:
    """Lo que las reglas necesitan saber de la planta en este momento."""

    catalogo: CatalogoManuales
    estado_bombas: dict[str, str]
    almacen: str = ALMACEN
    costo_max_usd: float = COSTO_MAX_USD
    revision_humana: bool = False


def evaluar(borrador: BorradorOrden, contexto: Contexto) -> Evaluacion:
    """Pasa la orden por las reglas y devuelve el veredicto con su motivo."""
    reglas = (
        _regla_tarea_estandar(borrador, contexto),
        _regla_inyeccion(borrador, contexto),
        _regla_stock(borrador, contexto),
        _regla_costo(borrador, contexto),
        _regla_personal(borrador),
        _regla_revision_humana(borrador, contexto),
    )
    falladas = [regla for regla in reglas if not regla.cumple]
    if not falladas:
        return Evaluacion(
            reglas=reglas,
            cumple=True,
            motivo="La orden cumple las seis reglas de aprobación del plan.",
        )
    motivo = "; ".join(f"{regla.nombre}: {regla.detalle}" for regla in falladas)
    return Evaluacion(reglas=reglas, cumple=False, motivo=motivo)


def _regla_tarea_estandar(borrador: BorradorOrden, contexto: Contexto) -> Regla:
    """Regla 1: al menos una tarea, y todas estándar del equipo."""
    if not borrador.tareas:
        return Regla(
            codigo="tarea_estandar",
            nombre="Tarea estándar",
            cumple=False,
            detalle="la orden no pide ninguna tarea",
        )
    no_estandar = [
        tarea
        for tarea in borrador.tareas
        if contexto.catalogo.tarea_para_equipo(tarea, borrador.equipo) is None
    ]
    if no_estandar:
        return Regla(
            codigo="tarea_estandar",
            nombre="Tarea estándar",
            cumple=False,
            detalle=(
                f"{', '.join(no_estandar)} no es una tarea estándar de {borrador.equipo}"
            ),
        )
    return Regla(
        codigo="tarea_estandar",
        nombre="Tarea estándar",
        cumple=True,
        detalle=f"{', '.join(borrador.tareas)} es tarea estándar de {borrador.equipo}",
    )


def _regla_inyeccion(borrador: BorradorOrden, contexto: Contexto) -> Regla:
    """Regla 2: si la orden detiene el equipo, la inyección se mantiene.

    La inyección se mantiene si el equipo no está en marcha o si hay otra
    bomba parada que pueda tomar el servicio.
    """
    if not borrador.detiene_equipo:
        return Regla(
            codigo="inyeccion",
            nombre="Inyección se mantiene",
            cumple=True,
            detalle="la orden no detiene el equipo",
        )
    estado = contexto.estado_bombas.get(borrador.equipo)
    if estado != "marcha":
        return Regla(
            codigo="inyeccion",
            nombre="Inyección se mantiene",
            cumple=True,
            detalle=f"{borrador.equipo} no está en marcha (estado: {estado or 'desconocido'})",
        )
    reserva = [
        tag
        for tag, valor in contexto.estado_bombas.items()
        if tag != borrador.equipo and valor == "parada"
    ]
    if reserva:
        return Regla(
            codigo="inyeccion",
            nombre="Inyección se mantiene",
            cumple=True,
            detalle=f"la bomba de reserva {', '.join(sorted(reserva))} está parada y puede tomar",
        )
    return Regla(
        codigo="inyeccion",
        nombre="Inyección se mantiene",
        cumple=False,
        detalle="detener el equipo reduce la inyección y no hay bomba de reserva",
    )


def _regla_stock(borrador: BorradorOrden, contexto: Contexto) -> Regla:
    """Regla 3: cada repuesto es del equipo y hay stock en el almacén."""
    materiales = contexto.catalogo.materiales_de(borrador.equipo)
    problemas: list[str] = []
    for repuesto in borrador.repuestos:
        if repuesto.codigo not in materiales:
            problemas.append(f"{repuesto.codigo} fuera de la lista de materiales")
            continue
        stock = contexto.catalogo.stock(repuesto.codigo, contexto.almacen)
        if repuesto.cantidad > stock:
            problemas.append(
                f"sin stock de {repuesto.codigo} "
                f"({repuesto.cantidad:g} pedidos, {stock:g} en {contexto.almacen})"
            )
    if problemas:
        return Regla(
            codigo="stock",
            nombre="Stock completo",
            cumple=False,
            detalle="; ".join(problemas),
        )
    return Regla(
        codigo="stock",
        nombre="Stock completo",
        cumple=True,
        detalle=(
            f"hay stock en {contexto.almacen} de los {len(borrador.repuestos)} repuesto(s)"
            if borrador.repuestos
            else "la orden no pide repuestos"
        ),
    )


def _regla_costo(borrador: BorradorOrden, contexto: Contexto) -> Regla:
    """Regla 4: el costo total de la orden es menor que el tope."""
    if borrador.costo_usd >= contexto.costo_max_usd:
        return Regla(
            codigo="costo",
            nombre="Costo dentro del tope",
            cumple=False,
            detalle=(
                f"costo USD {borrador.costo_usd:,.0f} "
                f"(tope USD {contexto.costo_max_usd:,.0f})"
            ),
        )
    return Regla(
        codigo="costo",
        nombre="Costo dentro del tope",
        cumple=True,
        detalle=f"costo USD {borrador.costo_usd:,.0f}",
    )


def _regla_personal(borrador: BorradorOrden) -> Regla:
    """Regla 5: el trabajo lo hace personal propio."""
    if borrador.ejecutor != "propio":
        return Regla(
            codigo="personal",
            nombre="Personal propio",
            cumple=False,
            detalle=f"el ejecutor es {borrador.ejecutor}",
        )
    return Regla(
        codigo="personal",
        nombre="Personal propio",
        cumple=True,
        detalle="el ejecutor es propio",
    )


def _regla_revision_humana(borrador: BorradorOrden, contexto: Contexto) -> Regla:
    """Daño estructural: siempre lo decide una persona.

    Una foto que muestra daño estructural no se aprueba sola: la orden queda
    para revisión humana aunque cumpla las otras cinco reglas.
    """
    if not contexto.revision_humana:
        return Regla(
            codigo="revision_humana",
            nombre="Revisión humana",
            cumple=True,
            detalle="no hay daño estructural en la evidencia",
        )
    return Regla(
        codigo="revision_humana",
        nombre="Revisión humana",
        cumple=False,
        detalle="la evidencia muestra daño estructural: la decide una persona",
    )
