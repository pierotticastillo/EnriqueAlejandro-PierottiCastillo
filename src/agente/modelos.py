"""Lo que el agente devuelve: una propuesta con citas, no una decisión.

``extra="forbid"`` en todos los modelos: si el modelo inventa un campo, la
salida se rechaza. Es el mismo criterio que se aplica a las tools y a la
salida del modelo de redacción.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: Tope de caracteres de la justificación: el agente escribe para el técnico de
#: mantenimiento, no un tratado.
MAX_CARACTERES = 1200

#: Cuántas causas, citas y tareas puede proponer como máximo.
MAX_ITEMS = 5


class PropuestaAgente(BaseModel):
    """La salida estructurada del agente.

    ``falla`` es el **código** de una falla del catálogo (por ejemplo
    ``F02-B-102-4``), no un texto libre: si el agente inventa un código que no
    está en los manuales, la propuesta se descarta.

    ``citas`` son las secciones del manual de las que sale lo que se propone
    (por ejemplo ``TX350-D §8.3``). Una cita que no está en la lista de
    permitidas se descarta con la propuesta entera.
    """

    model_config = ConfigDict(extra="forbid")

    falla: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Código de la falla, copiado de la ficha de la falla. Si la ficha "
            "dice 'Código: TX-T01', acá va 'TX-T01'. Null sólo si no hay "
            "ficha de falla."
        ),
    )
    causas: list[str] = Field(
        default_factory=list,
        max_length=MAX_ITEMS,
        description=(
            "Una causa del manual por elemento, copiada textualmente (por "
            "ejemplo 'Desgaste normal de los anillos')."
        ),
    )
    citas: list[str] = Field(
        default_factory=list,
        max_length=MAX_ITEMS,
        description=(
            "Una cita del manual por elemento, tal como la escribió la "
            "herramienta (por ejemplo 'TX350-D §8.3')."
        ),
    )
    tareas: list[str] = Field(
        default_factory=list,
        max_length=MAX_ITEMS,
        description=(
            "Un código de tarea del plan por elemento (por ejemplo 'T-B-EMP')."
        ),
    )
    justificacion: str = Field(
        min_length=1,
        max_length=MAX_CARACTERES,
        description=(
            "Dos o tres frases con el por qué: qué se midió, qué dice el "
            "manual y por qué esa causa. Sin repetir el código de la falla, "
            "las causas ni las citas."
        ),
    )
    confianza: Literal["alta", "media", "baja"] = Field(
        default="media",
        description="Cuánto respaldan las citas lo que se propone.",
    )


@dataclass(frozen=True)
class PropuestaValidada:
    """La propuesta del agente, ya contrastada con el catálogo.

    ``citas_descartadas`` y ``causas_descartadas`` quedan a la vista: el
    agente propuso algo que no se pudo respaldar y eso se registra, no se
    borra en silencio.
    """

    falla: str | None
    causas: tuple[str, ...]
    citas: tuple[str, ...]
    tareas: tuple[str, ...]
    justificacion: str
    confianza: str
    citas_descartadas: tuple[str, ...] = ()
    causas_descartadas: tuple[str, ...] = ()

    @property
    def vacia(self) -> bool:
        """True si el agente no proposes nada útil."""
        return not self.falla and not self.causas and not self.citas


@dataclass(frozen=True)
class ResultadoConsultaAgente:
    """El resultado completo de una consulta, incluida la提案 descartada.

    ``consultar`` sigue devolviendo sólo la propuesta válida para no cambiar el
    camino productivo.  El benchmark necesita además saber **por qué** una
    propuesta no se usó: una cita o una falla inventada no es lo mismo que un
    corte de presupuesto o una caída del modelo.

    ``estado`` es:

    * ``aceptada``: la propuesta atravesó la validación;
    * ``descartada``: el modelo respondió, pero la propuesta no se pudo respaldar;
    * ``sin_propuesta``: el modelo no devolvió una salida con el esquema esperado;
    * ``cortada``: se agotó el presupuesto;
    * ``error``: falló la consulta al modelo.
    """

    estado: Literal["aceptada", "descartada", "sin_propuesta", "cortada", "error"]
    propuesta: PropuestaValidada | None = None
    propuesta_cruda: PropuestaAgente | None = None
    motivo: str | None = None
