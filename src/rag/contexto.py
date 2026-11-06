"""Context builder para el análisis de anomalías (Fase 5) y la memoria.

El contexto de un escalado tiene cuatro partes: el parte confirmado, las fotos,
la tendencia de los sensores y los fragmentos de manual que sostienen una
conclusión. El builder no decide: junta la evidencia, recorta el texto a un
presupuesto y deja la lista de citas que el análisis debe usar.

Regla de la Fase 4: **toda conclusión cita**. Si un fragmento no tiene cita no
entra al contexto; si el análisis afirma algo con una cita que no está en el
contexto, ``validar_citas`` lo detecta.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rag.retriever import RecuperadorMemoria, RecuperadorPgvector
from tools.modelos import ConsultaManuales, FragmentoManual

_CITA = re.compile(
    r"\b(?:[A-Z][A-Z0-9-]+|historial)\s*§?\s*"
    r"(?:[A-Z]{2}-\d{2}-\d{4}|\d+(?:\.\d+)*|[A-Z](?:\.\d+)?)\b"
)


class ModeloContexto(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TendenciaSensor(ModeloContexto):
    tag: str
    variable: str
    minimo: float | None = None
    maximo: float | None = None
    promedio: float | None = None
    tendencia: str | None = None
    desde: str | None = None
    hasta: str | None = None


class ContextoAnalisis(ModeloContexto):
    """Evidencia lista para el análisis y las citas que la respaldan."""

    evento_id: str
    equipo: str | None = None
    estado: str
    resumen: str
    filas: list[dict[str, Any]] = Field(default_factory=list)
    fotos: list[str] = Field(default_factory=list)
    tendencia: list[TendenciaSensor] = Field(default_factory=list)
    fragmentos: list[FragmentoManual] = Field(default_factory=list)
    citas: list[str] = Field(default_factory=list)
    presupuesto_agotado: bool = False

    def a_prompt(self) -> str:
        """Texto compacto para el modelo de análisis."""
        lineas = [
            f"Evento: {self.evento_id}",
            f"Equipo: {self.equipo or 'planta'}",
            f"Estado: {self.estado}",
            f"Resumen: {self.resumen}",
        ]
        if self.filas:
            lineas.append("Parte confirmado:")
            lineas.extend(f"- {fila}" for fila in self.filas)
        if self.fotos:
            lineas.append(f"Fotos: {len(self.fotos)}")
        if self.tendencia:
            lineas.append("Tendencia:")
            lineas.extend(
                f"- {sensor.tag}/{sensor.variable}: {sensor.tendencia}"
                for sensor in self.tendencia
            )
        if self.fragmentos:
            lineas.append("Manual:")
            lineas.extend(
                f"- [{fragmento.cita}] {fragmento.texto}" for fragmento in self.fragmentos
            )
        return "\n".join(lineas)


def construir_contexto(
    *,
    evento_id: str,
    estado: str,
    resumen: str,
    recuperador: RecuperadorMemoria | RecuperadorPgvector,
    consulta: str,
    equipo: str | None = None,
    filas: list[dict[str, Any]] | None = None,
    fotos: list[str] | None = None,
    tendencia: list[dict[str, Any]] | None = None,
    max_fragmentos: int = 5,
    max_caracteres: int = 6000,
) -> ContextoAnalisis:
    """Arma el contexto de un escalado con fragmentos citables."""
    fragmentos = recuperador.buscar(
        ConsultaManuales(consulta=consulta, top_k=max_fragmentos)
    )
    seleccionados: list[FragmentoManual] = []
    citas_vistas: set[str] = set()
    caracteres = 0
    agotado = False
    for fragmento in fragmentos:
        if not fragmento.cita or fragmento.cita in citas_vistas:
            continue
        restante = max_caracteres - caracteres
        if restante <= 0:
            agotado = True
            break
        texto = fragmento.texto
        if len(texto) > restante:
            # Preferimos un fragmento completo a uno cortado a mitad de frase;
            # sólo el primero se recorta si él solo no entra en el presupuesto.
            if seleccionados:
                agotado = True
                break
            texto = texto[:restante].rstrip()
            agotado = True
        seleccionados.append(fragmento.model_copy(update={"texto": texto}))
        citas_vistas.add(fragmento.cita)
        caracteres += len(texto)
    return ContextoAnalisis(
        evento_id=evento_id,
        equipo=equipo,
        estado=estado,
        resumen=resumen,
        filas=filas or [],
        fotos=fotos or [],
        tendencia=[TendenciaSensor.model_validate(sensor) for sensor in tendencia or []],
        fragmentos=seleccionados,
        citas=[fragmento.cita for fragmento in seleccionados],
        presupuesto_agotado=agotado,
    )


def citas_en_texto(texto: str) -> set[str]:
    """Citas con forma de manual o de orden que aparecen en un texto."""
    return {coincidencia.group(0).strip() for coincidencia in _CITA.finditer(texto)}


def validar_citas(texto: str, contexto: ContextoAnalisis) -> list[str]:
    """Citas del texto que no están respaldadas por el contexto."""
    permitidas = set(contexto.citas)
    return sorted(cita for cita in citas_en_texto(texto) if cita not in permitidas)
