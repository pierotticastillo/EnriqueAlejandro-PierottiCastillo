"""Modelos de la Fase 5: falla catalogada, causa probable, borrador y reglas.

Son dataclasses inmutables: el análisis de un escalado es un valor, no un
proceso. Cada conclusions que se lleva al borrador o a las reglas lleva la cita
del manual que la respalda.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class RepuestoFicha:
    """Repuesto que pide la ficha de la falla en el manual del fabricante."""

    codigo: str
    cantidad: float
    texto: str


@dataclass(frozen=True)
class FallaCatalogo:
    """Una falla del catálogo de los manuales, con su cita."""

    codigo: str
    titulo: str
    documento: str
    cita: str
    tipo_equipo: str
    sintoma: str
    firma_sensores: str
    firma_recorrida: str
    causas: tuple[str, ...]
    como_confirmar: str
    accion: str
    procedimiento: str | None
    repuestos: tuple[RepuestoFicha, ...]
    detiene: bool | None
    es_seguridad: bool = False
    tabla_cita: str | None = None
    tabla_sintoma: str | None = None
    tabla_causa: str | None = None
    tabla_accion: str | None = None
    # (variable, "alto"|"bajo"): en qué sentido el manual dice que se desvía
    # cada variable. Es lo que separa "diferencial alto" de "diferencial bajo".
    direcciones: tuple[tuple[str, str], ...] = ()

    @property
    def perfil(self) -> str:
        """Texto con el que se compara la evidencia del parte."""
        return " ".join(
            parte
            for parte in (
                self.titulo,
                self.sintoma,
                self.firma_recorrida,
                self.firma_sensores,
            )
            if parte
        )


@dataclass(frozen=True)
class CausaProbable:
    """Una causa probable, con lo que la respalda y lo que falta confirmar."""

    falla: str
    causa: str
    evidencia: tuple[str, ...]
    falta_confirmar: str
    cita: str
    puntaje: float
    es_inspeccion: bool = False
    # Tarea que resuelve o inspecciona esta causa, cuando ya se sabe.
    tarea: str | None = None


@dataclass(frozen=True)
class Medicion:
    """Una medición estructurada de la evidencia.

    Sólo entra lo que el sistema midió: variable, valor, unidad y el sentido
    del desvío. **No entra el texto del operador**: la observación escrita en
    campo es no confiable y no se le muestra a ningún modelo.
    """

    variable: str
    valor: float | None = None
    unidad: str | None = None
    direccion: str | None = None

    @property
    def texto(self) -> str:
        valor = "-" if self.valor is None else f"{self.valor:g}"
        return f"{self.variable}: {valor} {self.unidad or ''}".strip()


@dataclass(frozen=True)
class RepuestoOrden:
    """Repuesto pedido por la orden, con su costo y si hay stock."""

    codigo: str
    cantidad: float
    costo_unitario: float
    disponible: bool


@dataclass(frozen=True)
class BorradorOrden:
    """El borrador de orden: lo que se publica en ``sistema/ordenes``."""

    id: str
    equipo: str
    falla: str
    tareas: tuple[str, ...]
    detiene_equipo: bool
    reduce_inyeccion: bool
    ejecutor: str
    repuestos: tuple[RepuestoOrden, ...]
    costo_usd: float
    evidencia: tuple[str, ...]
    citas: tuple[str, ...]
    motivo: str | None = None
    # Qué hay que hacer cuando la tarea no está en el catálogo del plan.
    descripcion: str | None = None
    # Títulos de las causas probables, para el informe de la orden.
    causas: tuple[str, ...] = ()


@dataclass(frozen=True)
class Regla:
    """Una regla de aprobación y si la orden la cumple."""

    codigo: str
    nombre: str
    cumple: bool
    detalle: str


@dataclass(frozen=True)
class Evaluacion:
    """Resultado de pasar la orden por las reglas de aprobación."""

    reglas: tuple[Regla, ...]
    cumple: bool
    motivo: str

    @property
    def reglas_falladas(self) -> tuple[Regla, ...]:
        return tuple(regla for regla in self.reglas if not regla.cumple)


@dataclass(frozen=True)
class Analisis:
    """Todo lo que se sabe de un escalado: falla, causas, orden y reglas."""

    evento_id: str
    equipo: str
    falla: FallaCatalogo | None
    causas: tuple[CausaProbable, ...]
    borrador: BorradorOrden
    evaluacion: Evaluacion
    citas: tuple[str, ...]
    ts: datetime | None = None
    reincidencia: bool = False
    notas: tuple[str, ...] = field(default_factory=tuple)
    # Mediciones estructuradas de la evidencia (sin texto del operador).
    mediciones: tuple[Medicion, ...] = ()
