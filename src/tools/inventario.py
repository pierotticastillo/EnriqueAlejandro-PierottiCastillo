"""Tools de inventario: stock de repuestos y materiales por equipo.

La fuente es el manual de buenas prácticas MBP-4 (capítulo 14): la lista de
repuestos con su stock por almacén y la lista de materiales de cada equipo.
"""

from __future__ import annotations

from tools.cliente import ClientePlanta
from tools.manuales import DOCUMENTO_BUENAS_PRACTICAS, CorpusManuales, numero, tabla
from tools.modelos import (
    ConsultaInventario,
    ConsultaListaMateriales,
    MaterialEquipo,
    RepuestoStock,
)


def inventario(
    cliente: ClientePlanta | None,
    consulta: ConsultaInventario,
    *,
    corpus: CorpusManuales | None = None,
) -> list[RepuestoStock]:
    """Tool ``inventario``: repuestos con su stock por almacén."""
    if corpus is None:
        if cliente is None:
            raise ValueError("Se necesita un cliente de la planta o un corpus")
        corpus = CorpusManuales(cliente)
    seccion = corpus.seccion(DOCUMENTO_BUENAS_PRACTICAS, "14.2")
    if seccion is None:
        return []
    materiales_por_equipo = _materiales_por_equipo(corpus)

    salida: list[RepuestoStock] = []
    filas = tabla(str(seccion.get("texto") or ""))
    for fila in filas[1:]:  # la primera fila es el encabezado
        if len(fila) < 6 or not fila[0]:
            continue
        codigo = fila[0]
        if consulta.codigo and codigo != consulta.codigo:
            continue
        equipos = [
            tag for tag, codigos in materiales_por_equipo.items() if codigo in codigos
        ]
        if consulta.equipo and consulta.equipo not in equipos:
            continue
        stock = {
            "yacimiento": numero(fila[4]) or 0.0,
            "central": numero(fila[5]) or 0.0,
        }
        salida.append(
            RepuestoStock(
                codigo=codigo,
                nombre=fila[1] or None,
                unidad=fila[2] or None,
                costo_usd=numero(fila[3]),
                stock=stock,
                disponible=stock.get(consulta.almacen, 0.0) > 0,
                equipos=equipos,
            )
        )
    return salida


def lista_materiales(
    cliente: ClientePlanta | None,
    consulta: ConsultaListaMateriales,
    *,
    corpus: CorpusManuales | None = None,
) -> list[MaterialEquipo]:
    """Tool ``lista_materiales``: repuestos que le corresponden a un equipo."""
    if corpus is None:
        if cliente is None:
            raise ValueError("Se necesita un cliente de la planta o un corpus")
        corpus = CorpusManuales(cliente)
    materiales_por_equipo = _materiales_por_equipo(corpus)
    codigos = materiales_por_equipo.get(consulta.equipo, [])
    if not codigos:
        return []
    nombres = {rep.codigo: rep for rep in inventario(cliente, ConsultaInventario(), corpus=corpus)}
    return [
        MaterialEquipo(
            codigo=codigo,
            nombre=nombres.get(codigo).nombre if codigo in nombres else None,
            costo_usd=nombres.get(codigo).costo_usd if codigo in nombres else None,
        )
        for codigo in codigos
    ]


def _materiales_por_equipo(corpus: CorpusManuales) -> dict[str, list[str]]:
    seccion = corpus.seccion(DOCUMENTO_BUENAS_PRACTICAS, "14.3")
    if seccion is None:
        return {}
    salida: dict[str, list[str]] = {}
    filas = tabla(str(seccion.get("texto") or ""))
    for fila in filas[1:]:
        if len(fila) < 2 or not fila[0]:
            continue
        salida[fila[0]] = [codigo.strip() for codigo in fila[1].split(",") if codigo.strip()]
    return salida
