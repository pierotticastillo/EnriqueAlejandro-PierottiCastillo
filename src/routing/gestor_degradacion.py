"""Gestor de degradación controlada (Ítem 3.12 del Plan de implementación 3).

Selecciona el nivel de servicio según la disponibilidad de recursos y avisa
explícitamente cada degradación (nunca en silencio).

El gestor mantiene un nivel actual y permite:

* `degradar(...)` — baja al siguiente nivel cuando hay un fallo, generando un
  aviso explícito para el usuario.
* `recuperar(...)` — sube al nivel anterior cuando el recurso vuelve a estar
  disponible.
* `estado_actual()` — devuelve el nivel actual sin cambiarlo.
"""

from __future__ import annotations

from dataclasses import dataclass

from .degradacion import (
    NIVELES,
    NivelDegradacion,
    NivelDegradacionInfo,
    anterior_nivel,
    siguiente_nivel,
)


@dataclass(frozen=True)
class ResultadoDegradacion:
    """Resultado de una selección de nivel de degradación."""

    nivel: NivelDegradacion
    info: NivelDegradacionInfo
    se_degrado: bool  # True si el nivel es distinto de "completo"
    motivo: str  # por qué se está en este nivel
    aviso_usuario: str  # mensaje para mostrar al usuario (vacío si no se degrada)


class GestorDegradacion:
    """Selecciona el nivel de servicio según disponibilidad."""

    def __init__(self, *, nivel_inicial: NivelDegradacion = "completo") -> None:
        if nivel_inicial not in NIVELES:
            raise ValueError(f"nivel_inicial no válido: {nivel_inicial}")
        self._nivel_actual = nivel_inicial
        self._historial: list[ResultadoDegradacion] = []

    # ------------------------------------------------------------------ propiedades

    @property
    def nivel_actual(self) -> NivelDegradacion:
        """Nivel de servicio actual."""
        return self._nivel_actual

    @property
    def se_ha_degradado(self) -> bool:
        """True si el nivel actual es distinto de completo."""
        return self._nivel_actual != "completo"

    @property
    def aviso_actual(self) -> str:
        """Aviso del nivel actual para mostrar al usuario (vacío en completo)."""
        if self._nivel_actual == "completo":
            return ""
        return NIVELES[self._nivel_actual].aviso_usuario

    # ------------------------------------------------------------------ alta

    def degradar(self, *, motivo: str) -> ResultadoDegradacion:
        """Degrada al siguiente nivel disponible.

        Genera un aviso explícito para el usuario (nunca en silencio). Si ya
        está en el último nivel (derivacion), se mantiene y el aviso refleja
        ese estado.
        """
        siguiente = siguiente_nivel(self._nivel_actual)
        if siguiente is None:
            # Ya está en el último nivel.
            nivel = self._nivel_actual
        else:
            self._nivel_actual = siguiente
            nivel = siguiente

        info = NIVELES[nivel]
        resultado = ResultadoDegradacion(
            nivel=nivel,
            info=info,
            se_degrado=nivel != "completo",
            motivo=motivo,
            aviso_usuario=info.aviso_usuario if nivel != "completo" else "",
        )
        self._historial.append(resultado)
        return resultado

    def recuperar(self, *, motivo: str = "recurso recuperado") -> ResultadoDegradacion:
        """Intenta recuperar al nivel anterior (mejor) cuando vuelve la disponibilidad.

        Si ya está en completo, se mantiene.
        """
        anterior = anterior_nivel(self._nivel_actual)
        if anterior is None:
            nivel = self._nivel_actual
        else:
            self._nivel_actual = anterior
            nivel = anterior

        info = NIVELES[nivel]
        resultado = ResultadoDegradacion(
            nivel=nivel,
            info=info,
            se_degrado=nivel != "completo",
            motivo=motivo,
            aviso_usuario=info.aviso_usuario if nivel != "completo" else "",
        )
        self._historial.append(resultado)
        return resultado

    def estado_actual(self) -> ResultadoDegradacion:
        """Devuelve el nivel actual sin cambiarlo."""
        info = NIVELES[self._nivel_actual]
        return ResultadoDegradacion(
            nivel=self._nivel_actual,
            info=info,
            se_degrado=self._nivel_actual != "completo",
            motivo="estado actual",
            aviso_usuario=self.aviso_actual,
        )

    def historial(self) -> tuple[ResultadoDegradacion, ...]:
        """Devuelve todos los cambios de nivel registrados."""
        return tuple(self._historial)

    def resumen(self) -> dict[str, object]:
        """Devuelve un resumen del estado del gestor."""
        ultimos = self._historial[-1] if self._historial else None
        return {
            "nivel_actual": self._nivel_actual,
            "se_ha_degradado": self._nivel_actual != "completo",
            "aviso_usuario": self.aviso_actual,
            "total_cambios": len(self._historial),
            "ultimo_motivo": ultimos.motivo if ultimos else "",
            "ultimo_aviso": ultimos.aviso_usuario if ultimos else "",
        }


#: Instancia global simple (opcional). Se prefiere inyectar el gestor.
gestor_degradacion = GestorDegradacion()
