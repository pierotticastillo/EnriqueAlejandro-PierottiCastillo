"""Tool ``catalogo_tareas``: tareas del plan de mantenimiento (MBP-4 capítulo 13).

El catálogo dice qué tarea resuelve qué, si detiene el equipo, quién la hace y
qué repuestos necesita. La aprobación de las órdenes se apoya en estos datos y
vive en código, no en el modelo.
"""

from __future__ import annotations

from tools.cliente import ClientePlanta
from tools.manuales import DOCUMENTO_BUENAS_PRACTICAS, CorpusManuales, numero, tabla
from tools.modelos import ConsultaCatalogoTareas, RepuestoTarea, TareaCatalogo


def catalogo_tareas(
    cliente: ClientePlanta | None,
    consulta: ConsultaCatalogoTareas,
    *,
    corpus: CorpusManuales | None = None,
) -> list[TareaCatalogo]:
    """Tool ``catalogo_tareas``: tareas estándar y no estándar del manual."""
    if corpus is None:
        if cliente is None:
            raise ValueError("Se necesita un cliente de la planta o un corpus")
        corpus = CorpusManuales(cliente)
    procedimientos = _procedimientos(corpus)
    salida: list[TareaCatalogo] = []

    for tarea in _tareas_standard(corpus):
        salida.append(tarea)
    if consulta.incluir_no_estandar:
        salida.extend(_tareas_no_estandar(corpus, procedimientos))

    salida = [        tarea
        for tarea in salida
        if (not consulta.codigo or tarea.codigo == consulta.codigo)
        and (not consulta.equipo or consulta.equipo in tarea.equipos)
    ]
    salida.sort(key=lambda tarea: tarea.codigo)
    return salida


def _tareas_standard(corpus: CorpusManuales) -> list[TareaCatalogo]:
    seccion = corpus.seccion(DOCUMENTO_BUENAS_PRACTICAS, "13.1")
    if seccion is None:
        return []
    procedimientos = _procedimientos(corpus)
    salida: list[TareaCatalogo] = []
    filas = tabla(str(seccion.get("texto") or ""))
    for fila in filas[1:]:
        if len(fila) < 8 or not fila[0]:
            continue
        repuestos = _repuestos(fila[6])
        salida.append(
            TareaCatalogo(
                codigo=fila[0],
                nombre=fila[1],
                equipos=[e.strip() for e in fila[2].split(",") if e.strip()],
                detiene=_si(fila[3]),
                horas=numero(fila[4]),
                ejecutor=fila[5] or None,
                estandar=True,
                repuestos=repuestos,
                costo_repuestos_usd=numero(fila[7]),
                procedimiento=procedimientos.get(fila[0]),
                cita="MBP-4 §13.1",
            )
        )
    return salida


def _tareas_no_estandar(
    corpus: CorpusManuales,
    procedimientos: dict[str, str],
) -> list[TareaCatalogo]:
    seccion = corpus.seccion(DOCUMENTO_BUENAS_PRACTICAS, "13.2")
    if seccion is None:
        return []
    salida: list[TareaCatalogo] = []
    filas = tabla(str(seccion.get("texto") or ""))
    for fila in filas[1:]:
        if len(fila) < 5 or not fila[0]:
            continue
        salida.append(
            TareaCatalogo(
                codigo=fila[0],
                nombre=fila[1],
                equipos=[],
                detiene=_si(fila[2]),
                horas=numero(fila[3]),
                ejecutor=fila[4] or None,
                estandar=False,
                repuestos=[],
                costo_repuestos_usd=None,
                procedimiento=procedimientos.get(fila[0]),
                cita="MBP-4 §13.2",
            )
        )
    return salida


def _procedimientos(corpus: CorpusManuales) -> dict[str, str]:
    seccion = corpus.seccion(DOCUMENTO_BUENAS_PRACTICAS, "13.3")
    if seccion is None:
        return {}
    salida: dict[str, str] = {}
    filas = tabla(str(seccion.get("texto") or ""))
    for fila in filas[1:]:
        if len(fila) < 2 or not fila[0]:
            continue
        salida[fila[0]] = fila[1]
    return salida


def _repuestos(celda: str) -> list[RepuestoTarea]:
    """``TX-EMP-KIT x 1`` → un repuesto de cantidad 1."""
    salida: list[RepuestoTarea] = []
    for parte in celda.split(","):
        texto = parte.strip()
        if not texto or texto == "-":
            continue
        if " x " in texto:
            codigo, cantidad = texto.rsplit(" x ", 1)
            salida.append(
                RepuestoTarea(codigo=codigo.strip(), cantidad=numero(cantidad) or 0.0)
            )
        else:
            salida.append(RepuestoTarea(codigo=texto, cantidad=1.0))
    return salida


def _si(celda: str) -> bool:
    return celda.strip().lower() in {"si", "sí", "yes", "true"}
