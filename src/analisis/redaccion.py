"""Redacción de la justificación técnica de una orden con el modelo local.

Esta es la única parte del sistema donde entra el modelo de lenguaje, y su
papel es **redactar**: escribir, en palabras del técnico, por qué la orden dice
lo que dice. No decide nada.

**El prompt no lleva el texto libre del operador.** Se arma con material
confiable: la ficha de la falla del catálogo, las mediciones estructuradas
(variable, valor, unidad y sentido del desvío), las tareas del plan con su
nombre y su procedimiento, los repuestos y el resultado de las reglas. La nota
del operador no entra, ni resumida ni de pasada: por construcción el modelo no
puede obedecer lo que dice una nota, porque no la ve.

**La salida se valida dos veces**: contra el esquema (`SalidaEstructurada`) y
contra las citas conocidas. Si el modelo inventa una cita, se descarta su
respuesta entera y la orden se queda con el texto determinista.
"""

from __future__ import annotations

import logging
import re

from analisis.llm import AnalysisModel, ErrorModelo, SalidaEstructurada
from analisis.modelos import Analisis

logger = logging.getLogger("analisis.redaccion")

#: Etiqueta que precede a la redacción en la bitácora de la orden.
ETIQUETA = "Justificación técnica (modelo local)"

#: Raíces de palabras con las que el modelo hablaría de una decisión. La
#: decisión no es del modelo: si su texto las menciona, se descarta entero.
_PALABRAS_DE_DECISION = ("aprob", "rechaz", "autoriz", "sin planificador")

#: Un documento citado dentro del texto: «TX350-D», «MBP-4», «FC48-3». Las
#: partes de un código más largo no se toman: «TX-EMP-KIT» es un repuesto.
_DOCUMENTO = re.compile(r"(?<![\w-])[A-Z][A-Z0-9]{1,6}[-–][A-Z0-9]{1,6}(?![\w-])")


def redactar(
    modelo: AnalysisModel,
    analisis: Analisis,
    *,
    tareas: dict[str, str] | None = None,
) -> str | None:
    """La justificación técnica de la orden, o ``None`` si no se pudo tener.

    ``None`` no es un error: es el camino normal cuando el modelo no está, no
    responde o devuelve algo que no valida. La orden ya está publicada y
    aprobada (o no) por las reglas, con o sin esta redacción.
    """
    if modelo is None:
        return None
    permitidas = citas_permitidas(analisis)
    conocidas = {_normalizar(cita) for cita in permitidas}
    try:
        salida = modelo.invoke(construir_prompt(analisis, tareas=tareas))
    except ErrorModelo as exc:
        logger.warning("El modelo local no pudo redactar la orden: %s", exc)
        return None
    except Exception:  # noqa: BLE001 - el modelo no puede tumbar el análisis
        logger.warning("Falló la redacción con el modelo local", exc_info=True)
        return None
    desconocidas = [
        cita for cita in salida.citas if _normalizar(cita) not in conocidas
    ]
    if desconocidas:
        logger.warning(
            "El modelo local-citó documentos desconocidos (%s): se descarta su redacción",
            ", ".join(desconocidas),
        )
        return None
    decision = _palabra_de_decision(salida.resumen)
    if decision is not None:
        logger.warning(
            "El modelo local habló de una decisión (%r): se descarta su redacción",
            decision,
        )
        return None
    inventados = _documentos_desconocidos(salida.resumen, analisis)
    if inventados:
        logger.warning(
            "El modelo local mencionó documentos que no están en la lista (%s): "
            "se descarta su redacción",
            ", ".join(inventados),
        )
        return None
    return formatear(salida, permitidas)


def _palabra_de_decision(texto: str) -> str | None:
    """La palabra con la que el modelo habla de una decisión, si hay alguna."""
    lowered = texto.lower()
    for palabra in _PALABRAS_DE_DECISION:
        if palabra in lowered:
            return palabra
    return None


def _documentos_desconocidos(texto: str, analisis: Analisis) -> list[str]:
    """Los documentos que menciona el texto y no están entre los conocidos.

    Se comparan sin guiones y en minúsculas, y se acepta también el prefijo:
    «TX-350» es el mismo documento que «TX350-D». Lo que no se acepta es un
    documento que no viene del catálogo (por ejemplo, «FC99-Z»).
    """
    conocidos = _documentos_conocidos(analisis)
    if not conocidos:
        return []
    normalizados = {_normalizar_documento(documento) for documento in conocidos}
    salida: list[str] = []
    for candidato in _DOCUMENTO.findall(texto):
        limpio = _normalizar_documento(candidato)
        if any(limpio == otro or limpio.startswith(otro) for otro in normalizados):
            continue
        if any(otro.startswith(limpio) for otro in normalizados):
            continue
        salida.append(candidato)
    return sorted(set(salida))


def _documentos_conocidos(analisis: Analisis) -> list[str]:
    """Los documentos de manual que respaldan esta orden."""
    documentos: list[str] = []
    if analisis.falla is not None and analisis.falla.documento:
        documentos.append(analisis.falla.documento)
    for cita in analisis.citas:
        documento = cita.split("§")[0].strip()
        if documento and documento not in documentos:
            documentos.append(documento)
    return documentos


def _normalizar_documento(documento: str) -> str:
    return documento.replace("-", "").replace("–", "").strip().lower()


def formatear(salida: SalidaEstructurada, permitidas: list[str]) -> str:
    """La redacción, lista para guardarse en la bitácora de la orden."""
    partes = [f"{ETIQUETA}: {salida.resumen.strip()}"]
    if salida.causas:
        partes.append("Causas: " + "; ".join(causa.strip() for causa in salida.causas))
    citas = [cita for cita in salida.citas if cita.strip()] or permitidas
    if citas:
        partes.append("Citas: " + "; ".join(citas))
    partes.append(f"Confianza del modelo: {salida.confianza}")
    return "\n".join(partes)


def citas_permitidas(analisis: Analisis) -> list[str]:
    """Las citas que el modelo puede usar: las que ya están en la orden."""
    citas: list[str] = []
    for cita in analisis.citas:
        if cita and cita not in citas:
            citas.append(cita)
    if analisis.falla is not None:
        documento = analisis.falla.documento
        if documento and documento not in citas:
            citas.append(documento)
    return citas


def construir_prompt(analisis: Analisis, *, tareas: dict[str, str] | None = None) -> str:
    """El prompt con material confiable. Sin texto del operador."""
    borrador = analisis.borrador
    falla = analisis.falla
    lineas: list[str] = []

    lineas.append(f"Equipo: {borrador.equipo}")
    tipo = "detiene el equipo" if borrador.detiene_equipo else "no detiene el equipo"
    lineas.append(f"Tipo de orden: {tipo}")

    if falla is not None:
        lineas.append("")
        lineas.append("FICHA DE LA FALLA (catálogo de manuales)")
        lineas.append(f"- Código: {falla.codigo}")
        lineas.append(f"- Título: {falla.titulo}")
        lineas.append(f"- Síntoma: {falla.sintoma}")
        if falla.causas:
            lineas.append("- Causas que lista el manual: " + "; ".join(falla.causas))
        if falla.accion:
            lineas.append(f"- Acción del manual: {falla.accion}")
        if falla.como_confirmar:
            lineas.append(f"- Cómo confirmar: {falla.como_confirmar}")
        lineas.append(f"- Documento: {falla.documento}")
        lineas.append(f"- Cita: {falla.cita}")
    else:
        lineas.append("")
        lineas.append(
            "FICHA DE LA FALLA: no se identificó una falla del catálogo; "
            "la orden es una inspección."
        )

    if analisis.mediciones:
        lineas.append("")
        lineas.append("MEDICIONES DE PLANTA (sólo valores; no hay texto del operador)")
        for medicion in analisis.mediciones:
            valor = "-" if medicion.valor is None else f"{medicion.valor:g}"
            unidad = medicion.unidad or ""
            desvio = f" · desvío: {medicion.direccion}" if medicion.direccion else ""
            lineas.append(f"- {medicion.variable}: {valor} {unidad}{desvio}".rstrip())

    lineas.append("")
    lineas.append("TAREAS DEL PLAN (ya decididas)")
    for tarea in borrador.tareas:
        detalle = (tareas or {}).get(tarea)
        lineas.append(f"- {tarea}" + (f": {detalle}" if detalle else ""))

    if borrador.repuestos:
        lineas.append("")
        lineas.append("REPUESTOS (ya decididos)")
        for repuesto in borrador.repuestos:
            estado = "hay stock" if repuesto.disponible else "sin stock"
            lineas.append(
                f"- {repuesto.codigo} x{repuesto.cantidad:g} "
                f"· USD {repuesto.costo_unitario:,.0f} · {estado}"
            )

    lineas.append("")
    lineas.append("REGLAS DE APROBACIÓN (evaluadas en código; el resultado no se cambia)")
    for regla in analisis.evaluacion.reglas:
        lineas.append(f"- {regla.nombre}: {'cumple' if regla.cumple else 'no cumple'}")

    permitidas = citas_permitidas(analisis)
    lineas.append("")
    lineas.append("CITAS PERMITIDAS (sólo podés usar estas): " + ", ".join(permitidas))
    lineas.append("")
    lineas.append(
        "Escribí la justificación técnica de esta orden para el técnico de mantenimiento. "
        "No cambies tareas, repuestos, costos ni el resultado de las reglas."
    )
    return "\n".join(lineas)


def _normalizar(texto: str) -> str:
    return " ".join(texto.strip().lower().split())
