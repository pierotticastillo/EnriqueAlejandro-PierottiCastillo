"""Degradación controlada en 4 niveles (Ítem 3.12 del Plan de implementación 3).

La presentación de Semana_5 sugiere una **degradación controlada** con niveles
predefinidos:

1. **completo** — modelo principal con todas las herramientas disponibles.
2. **reducido** — modelo más chico, sin lo costoso.
3. **mínimo** — caché o plantilla predefinida.
4. **derivación** — mensaje claro a una persona.

Como sugiere la presentación, **"nunca degradar en silencio"** para pagos,
salud o acciones irreversibles: cada cambio de nivel genera un aviso explícito
para el usuario.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

NivelDegradacion = Literal["completo", "reducido", "minimo", "derivacion"]

#: Niveles de degradación, en orden de calidad (de mejor a peor).
NIVELES_DEGRADACION: tuple[NivelDegradacion, ...] = (
    "completo",
    "reducido",
    "minimo",
    "derivacion",
)


@dataclass(frozen=True)
class NivelDegradacionInfo:
    """Información de un nivel de degradación."""

    nivel: NivelDegradacion
    descripcion: str
    aviso_usuario: str  # mensaje que se muestra al usuario al estar en este nivel
    requiere_confirmacion: bool  # True si se debe avisar explícitamente


#: Tabla de los 4 niveles definidos, con su aviso para el usuario.
NIVELES: dict[NivelDegradacion, NivelDegradacionInfo] = {
    "completo": NivelDegradacionInfo(
        nivel="completo",
        descripcion="Modelo principal con todas las herramientas disponibles.",
        aviso_usuario="",
        requiere_confirmacion=False,
    ),
    "reducido": NivelDegradacionInfo(
        nivel="reducido",
        descripcion="Modelo más chico; se desactivan las herramientas costosas.",
        aviso_usuario=(
            "El servicio está operando en modo reducido: algunas funciones "
            "avanzadas no están disponibles."
        ),
        requiere_confirmacion=True,
    ),
    "minimo": NivelDegradacionInfo(
        nivel="minimo",
        descripcion="Respuesta desde caché o plantilla predefinida.",
        aviso_usuario=(
            "El servicio está operativo con capacidad limitada: se muestra una "
            "respuesta predefinida."
        ),
        requiere_confirmacion=True,
    ),
    "derivacion": NivelDegradacionInfo(
        nivel="derivacion",
        descripcion="Derivación a una persona (operador humano).",
        aviso_usuario=(
            "No se pudo procesar automáticamente. Se ha derivado su solicitud a un operador humano."
        ),
        requiere_confirmacion=True,
    ),
}


def info_para_nivel(nivel: NivelDegradacion) -> NivelDegradacionInfo:
    """Devuelve la información de un nivel de degradación."""
    if nivel not in NIVELES:
        raise ValueError(f"Nivel de degradación no válido: {nivel}")
    return NIVELES[nivel]


def indice_para_nivel(nivel: NivelDegradacion) -> int:
    """Devuelve el índice del nivel en la cadena (0 = completo, 3 = derivacion)."""
    return NIVELES_DEGRADACION.index(nivel)


def siguiente_nivel(nivel: NivelDegradacion) -> NivelDegradacion | None:
    """Devuelve el siguiente nivel de degradación (peor), o None si ya está en el último."""
    indice = indice_para_nivel(nivel)
    if indice >= len(NIVELES_DEGRADACION) - 1:
        return None
    return NIVELES_DEGRADACION[indice + 1]


def anterior_nivel(nivel: NivelDegradacion) -> NivelDegradacion | None:
    """Devuelve el nivel anterior (mejor), o None si ya está en completo."""
    indice = indice_para_nivel(nivel)
    if indice <= 0:
        return None
    return NIVELES_DEGRADACION[indice - 1]
