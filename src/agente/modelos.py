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

    falla: str | None = Field(default=None, max_length=64)
    causas: list[str] = Field(default_factory=list, max_length=MAX_ITEMS)
    citas: list[str] = Field(default_factory=list, max_length=MAX_ITEMS)
    tareas: list[str] = Field(default_factory=list, max_length=MAX_ITEMS)
    justificacion: str = Field(min_length=1, max_length=MAX_CARACTERES)
    confianza: Literal["alta", "media", "baja"] = "media"


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
