"""Causas probables de un escalado, ordenadas y con su evidencia.

El catálogo de fallas del manual dice cómo se ve cada falla; la evidencia del
parte dice qué se vio en el campo. El cruce de las dos cosas da la falla
probable y, con ella, las causas que el manual lista para esa falla.

Si la evidencia no alcanza para elegir una falla, la conclusión no es una
falla: es que hay que inspeccionar (MBP-4 §13.2).
"""

from __future__ import annotations

from dataclasses import dataclass

from analisis.catalogo import CatalogoManuales
from analisis.evidencia import Evidencia
from analisis.modelos import CausaProbable, FallaCatalogo
from rag.texto import tokenizar

# Puntaje mínimo para dar por identificada una falla con la evidencia.
UMBRAL_EVIDENCIA = 0.15
# Una alarma de un solo sensor es un síntoma, no un diagnóstico: la misma
# desviación es consecuencia de varias fallas (una presión de descarga que
# cae es lo que pasa cuando la bomba se dispara, y también puede ser una
# rotura). Con sólo esa evidencia hay que pedir una firma más clara antes de
# elegir una falla: si no, la conclusión es una inspección (MBP-4 §13.2).
UMBRAL_EVIDENCIA_SENSOR = 0.6
# Las tareas de inspección del plan (MBP-4 §13.2).
TAREAS_INSPECCION = ("I-VIB", "I-VAL")
CITA_INSPECCION = "MBP-4 §13.2"


def umbral_de(evidencia: Evidencia) -> float:
    """El puntaje mínimo que hay que alcanzar para dar por identificada una falla."""
    return UMBRAL_EVIDENCIA_SENSOR if evidencia.solo_sensor else UMBRAL_EVIDENCIA


@dataclass(frozen=True)
class Candidato:
    """Una falla del catálogo con lo que la evidencia dice de ella."""

    falla: FallaCatalogo
    puntaje: float
    cobertura: float


def causalizar(
    evidencia: Evidencia,
    catalogo: CatalogoManuales,
    *,
    tipo_equipo: str,
    citas_rag: tuple[str, ...] = (),
    incluir_seguridad: bool = False,
) -> tuple[CausaProbable, ...]:
    """Las causas probables del escalado, de la más probable a la menos."""
    candidatos = _candidatos(
        evidencia,
        catalogo,
        tipo_equipo,
        citas_rag,
        incluir_seguridad=incluir_seguridad,
    )
    if not candidatos or candidatos[0].puntaje < umbral_de(evidencia):
        return (_causa_de_inspeccion(evidencia, catalogo, tipo_equipo, candidatos),)
    mejor = candidatos[0]
    return _causas_de(mejor, evidencia)


def _candidatos(
    evidencia: Evidencia,
    catalogo: CatalogoManuales,
    tipo_equipo: str,
    citas_rag: tuple[str, ...],
    *,
    incluir_seguridad: bool = False,
) -> list[Candidato]:
    salida: list[Candidato] = []
    for falla in catalogo.fallas.para_tipo(tipo_equipo):
        if falla.es_seguridad and not incluir_seguridad:
            # Las condiciones inseguras no se analizan como fallas de equipo:
            # tienen su propia vía, la alerta de seguridad.
            continue
        cobertura = _cobertura(falla, evidencia)
        if cobertura <= 0:
            continue
        puntaje = cobertura * _factor_direccion(falla, evidencia)
        if falla.cita in citas_rag:
            # El RAG también la proposes: eso confirma el cruce.
            puntaje *= 1.3
        salida.append(Candidato(falla=falla, puntaje=puntaje, cobertura=cobertura))
    salida.sort(key=lambda candidato: (-candidato.puntaje, candidato.falla.codigo))
    return salida


def _cobertura(falla: FallaCatalogo, evidencia: Evidencia) -> float:
    """Qué parte de la firma de la falla aparece en la evidencia.

    Se toma el mejor campo (título, síntoma, firma en la recorrida o firma en
    sensores): una falla que encaja en un solo campo vale más que una que
    reparte sus palabras entre varios.
    """
    if not evidencia.tokens:
        return 0.0
    campos = (
        falla.titulo,
        falla.sintoma,
        falla.firma_recorrida,
        falla.firma_sensores,
    )
    coberturas: list[float] = []
    for texto in campos:
        tokens = set(tokenizar(texto))
        if not tokens:
            continue
        coberturas.append(len(tokens & evidencia.tokens) / len(tokens))
    return max(coberturas) if coberturas else 0.0


def _factor_direccion(falla: FallaCatalogo, evidencia: Evidencia) -> float:
    """Si el manual dice "diferencial alto" y el campo vio "diferencial bajo".

    Un conflicto manda: la falla que dice lo contrario del campo no es la
    falla. Un acuerdo refuerza.
    """
    direcciones = dict(falla.direcciones)
    if not direcciones or not evidencia.direcciones:
        return 1.0
    coinciden = 0
    conflictos = 0
    for variable, direccion in evidencia.direcciones.items():
        esperada = direcciones.get(variable)
        if esperada is None:
            continue
        if esperada == direccion:
            coinciden += 1
        else:
            conflictos += 1
    if conflictos:
        return 0.4
    if coinciden:
        return 1.6
    return 1.0


def _causas_de(candidato: Candidato, evidencia: Evidencia) -> tuple[CausaProbable, ...]:
    """Las causas que el manual lista para la falla, con su evidencia."""
    falla = candidato.falla
    causas = falla.causas or (falla.accion,)
    salida: list[CausaProbable] = []
    for causa in causas:
        salida.append(
            CausaProbable(
                falla=falla.codigo,
                causa=causa,
                evidencia=_evidencia_de(causa, evidencia),
                falta_confirmar=falla.como_confirmar,
                cita=falla.cita,
                puntaje=round(candidato.puntaje, 4),
            )
        )
    return tuple(salida)


def _evidencia_de(causa: str, evidencia: Evidencia) -> tuple[str, ...]:
    """Los fragmentos de evidencia que respaldan una causa."""
    tokens = set(tokenizar(causa))
    if not tokens:
        return evidencia.descripciones
    respaldo = tuple(
        fragmento.texto
        for fragmento in evidencia.fragmentos
        if tokens & set(tokenizar(fragmento.texto))
    )
    return respaldo or evidencia.descripciones


def _causa_de_inspeccion(
    evidencia: Evidencia,
    catalogo: CatalogoManuales,
    tipo_equipo: str,
    candidatos: list[Candidato],
) -> CausaProbable:
    """La conclusión cuando la evidencia no alcanza: hay que inspeccionar."""
    tarea = _tarea_de_inspeccion(evidencia, catalogo, tipo_equipo)
    puntaje = candidatos[0].puntaje if candidatos else 0.0
    if tarea is not None:
        conclusion = (
            f"La evidencia no alcanza para identificar la falla: "
            f"corresponde {tarea.nombre} ({tarea.codigo})."
        )
        cita = tarea.cita or CITA_INSPECCION
    else:
        conclusion = (
            "La evidencia no alcanza para identificar la falla: "
            "corresponde una revisión no estándar."
        )
        cita = CITA_INSPECCION
    return CausaProbable(
        falla="",
        causa=conclusion,
        evidencia=evidencia.descripciones,
        falta_confirmar=(
            "Revisar el equipo en campo antes de pedir la intervención."
        ),
        cita=cita,
        puntaje=round(puntaje, 4),
        es_inspeccion=True,
        tarea=tarea.codigo if tarea is not None else None,
    )


def _tarea_de_inspeccion(
    evidencia: Evidencia,
    catalogo: CatalogoManuales,
    tipo_equipo: str,
):
    """La tarea de inspección que corresponde a lo que se vio en el campo."""
    if tipo_equipo != "bomba":
        # Las tareas de inspección del plan son de bombas (TX-350).
        return None
    if "vibracion" in evidencia.direcciones or "vibrac" in evidencia.tokens:
        return _tarea(catalogo, "I-VIB")
    return _tarea(catalogo, "I-VAL")


def _tarea(catalogo: CatalogoManuales, codigo: str):
    return next((tarea for tarea in catalogo.tareas if tarea.codigo == codigo), None)
