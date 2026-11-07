"""Clasificación determinista de partes confirmados.

Un parte confirmado genera **un evento por equipo**: el corrector de la planta
compara el evento de cada equipo de la nota contra la verdad de campo, así que
un evento único con el primer tag anómalo deja a los demás equipos sin calificar.

Los límites salen del catálogo de la planta (``/api/planta``), nunca del manual.
La misma entrada produce siempre la misma salida: no hay LLM acá.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from planta_contrato import EventoSistema, TipoEvento

from planta.parte_extractor import Hallazgo
from planta.texto import normalizar
from planta.unidades import convertir

_VARIABLES_SENSOR = {
    "presion_descarga_local": "presion_descarga",
    "presion_succion": "presion_succion",
    "presion_diferencial": "presion_diferencial",
    "caudal": "caudal",
}

# Tag virtual: una nota que no es de un equipo en particular ("sin novedad",
# "la planta"). No es un equipo de la planta, así que el evento sale sin equipo.
TAG_PLANTA = "PLANTA"

# Observaciones que NO son una anomalía: el equipo está parado y está en
# reserva o en mantenimiento, como las anota el operador. "reservoir" es lo
# que el ASR escribe cuando oye "reserva".
_OBSERVACIONES_NORMALES = (
    "parada, en reserva",
    "parada en reserva",
    "en reserva",
    "reservoir",
    "parada por mantenimiento",
    "por mantenimiento",
    "en mantenimiento",
)
_OBSERVACIONES_ANOMALAS = (
    "rodamiento caliente",
    "diferencial alto",
    "goteo por empaquetadura",
    "perdida importante por empaquetadura",
    "rebalse a pileta",
    "nivel de aceite bajo",
    "correas flojas",
    "correas rotas",
    "correas desalineadas",
    "correas en mal estado",
    "ruido anormal",
    "golpeteo metalico",
    "piedras adentro",
    "ruido raro de rodamiento",
    "disparada, no arranca",
)


def clasificar_partes(
    *,
    nota_id: str,
    parte_id: str,
    ts: datetime,
    filas: list[Hallazgo],
    planta: dict[str, Any],
    fotos: int = 0,
) -> list[EventoSistema]:
    """Devuelve un evento por equipo del parte, en el orden en que aparecen."""
    por_equipo: dict[str, list[Hallazgo]] = {}
    for fila in filas:
        if not fila.tag or fila.tag == TAG_PLANTA:
            continue
        por_equipo.setdefault(fila.tag, []).append(fila)

    if not por_equipo:
        return [
            EventoSistema(
                id=f"parte:{nota_id}",
                ts=ts,
                tipo=TipoEvento.sin_novedad,
                equipo=None,
                origen="parte",
                parte_id=parte_id,
                nota_ids=[nota_id],
                resumen="Parte confirmado sin lecturas para reportar.",
            )
        ]

    eventos: list[EventoSistema] = []
    for tag, filas_tag in por_equipo.items():
        anomalias = [fila for fila in filas_tag if _fila_anomala(fila, planta)]
        if anomalias:
            resumen = "; ".join(_resumen_fila(fila) for fila in anomalias[:4])
            if len(anomalias) > 4:
                resumen += f"; y {len(anomalias) - 4} hallazgo(s) más"
            resumen += f" [{_contexto(filas_tag, fotos)}]"
            tipo = TipoEvento.escalado
        else:
            resumen = (
                f"{tag}: parte confirmado sin desvíos detectados "
                f"({len(filas_tag)} fila(s))."
            )
            tipo = TipoEvento.sin_novedad
        eventos.append(
            EventoSistema(
                id=f"parte:{nota_id}:{tag}",
                ts=ts,
                tipo=tipo,
                equipo=tag,
                origen="parte",
                parte_id=parte_id,
                nota_ids=[nota_id],
                resumen=resumen,
            )
        )
    return eventos


def filas_anomalas(
    filas: list[Hallazgo],
    planta: dict[str, Any],
    tag: str,
) -> list[Hallazgo]:
    """Las filas del parte que son una anomalía para ese equipo.

    El análisis de la falla se hace con lo que motivó el escalado: una
    lectura normal que el operador anotó en el mismo parte no es evidencia de
    una falla y puede confundir el cruce con el manual.
    """
    return [fila for fila in filas if fila.tag == tag and _fila_anomala(fila, planta)]


def fuentes_tendencia_parte(
    filas: list[Hallazgo],
    planta: dict[str, Any],
) -> dict[str, tuple[str, str | None]]:
    """Sensor de catálogo que sustenta cada fila anómala, por equipo.

    Sirve para agregar la tendencia de las últimas horas al evento escalado:
    el contexto del equipo, no el del primer equipo que se encontró.
    """
    fuentes: dict[str, tuple[str, str | None]] = {}
    for fila in filas:
        variable = _VARIABLES_SENSOR.get(fila.variable)
        if not variable or not fila.tag or fila.tag in fuentes:
            continue
        if not _fila_anomala(fila, planta):
            continue
        sensor = next(
            (
                item
                for item in planta.get("sensores") or []
                if item.get("tag") == fila.tag and item.get("variable") == variable
            ),
            None,
        )
        if sensor is not None:
            fuentes[fila.tag] = (variable, sensor.get("unidad"))
    return fuentes


def _fila_anomala(fila: Hallazgo, planta: dict[str, Any]) -> bool:
    observacion = normalizar(fila.observacion or "")
    if observacion:
        # Un equipo parado en reserva o en mantenimiento no es una anomalía.
        if any(marca in observacion for marca in _OBSERVACIONES_NORMALES):
            return False
        if any(marca in observacion for marca in _OBSERVACIONES_ANOMALAS):
            return True

    variable_sensor = _VARIABLES_SENSOR.get(fila.variable)
    if not variable_sensor or fila.tag is None or fila.valor is None:
        return False
    sensor = next(
        (
            item
            for item in planta.get("sensores") or []
            if item.get("tag") == fila.tag and item.get("variable") == variable_sensor
        ),
        None,
    )
    if sensor is None:
        return False

    unidad_sensor = sensor.get("unidad")
    valor = convertir(fila.valor, fila.unidad, unidad_sensor)
    return (
        (sensor.get("alarma_baja") is not None and valor <= sensor["alarma_baja"])
        or (sensor.get("alarma_alta") is not None and valor >= sensor["alarma_alta"])
        or (sensor.get("disparo") is not None and valor >= sensor["disparo"])
    )


def _resumen_fila(fila: Hallazgo) -> str:
    detalle = fila.observacion
    if detalle is None and fila.valor is not None:
        detalle = f"{fila.valor:g} {fila.unidad or ''}".strip()
    return f"{fila.tag or 'PLANTA'} {fila.variable}: {detalle or 'desvío detectado'}"


def _contexto(filas: list[Hallazgo], fotos: int) -> str:
    """Contexto mínimo del parte: cuántas filas y cuántas fotos lo respaldan."""
    partes = [f"{len(filas)} fila(s)"]
    if fotos:
        partes.append(f"{fotos} foto(s)")
    return "contexto: " + ", ".join(partes)
