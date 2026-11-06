"""Detector determinista de lecturas de sensores según el catálogo de planta."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from planta_contrato import EventoSistema, LecturaSensor, TipoEvento


@dataclass(frozen=True)
class LimitesSensor:
    tag: str
    variable: str
    unidad: str
    alarma_baja: float | None = None
    alarma_alta: float | None = None
    disparo: float | None = None


@dataclass
class _EstadoSensor:
    ultima_ts: datetime | None = None
    racha_fuera: int = 0
    activo: bool = False


class DetectorSensores:
    """Emite un Escalado al sostenerse una alarma; ``disparo`` es inmediato.

    Los límites se leen únicamente de ``/api/planta``. Las muestras fuera de
    orden o duplicadas no cuentan para alcanzar la persistencia.
    """

    def __init__(self, planta: dict[str, Any], *, muestras_sostenidas: int = 3) -> None:
        if muestras_sostenidas < 1:
            raise ValueError("muestras_sostenidas debe ser al menos 1")
        self._muestras_sostenidas = muestras_sostenidas
        self._limites: dict[tuple[str, str], LimitesSensor] = {}
        self._estados: dict[tuple[str, str], _EstadoSensor] = {}
        for item in planta.get("sensores") or []:
            if not isinstance(item, dict) or not item.get("tag") or not item.get("variable"):
                continue
            limite = LimitesSensor(
                tag=str(item["tag"]),
                variable=str(item["variable"]),
                unidad=str(item.get("unidad") or ""),
                alarma_baja=_numero(item.get("alarma_baja")),
                alarma_alta=_numero(item.get("alarma_alta")),
                disparo=_numero(item.get("disparo")),
            )
            self._limites[(limite.tag, limite.variable)] = limite

    def evaluar(self, lectura: LecturaSensor) -> EventoSistema | None:
        clave = (lectura.tag, lectura.variable)
        limite = self._limites.get(clave)
        if limite is None:
            return None

        estado = self._estados.setdefault(clave, _EstadoSensor())
        if estado.ultima_ts is not None and lectura.ts <= estado.ultima_ts:
            return None
        estado.ultima_ts = lectura.ts

        if lectura.calidad.value != "buena" or lectura.valor is None:
            estado.racha_fuera = 0
            return None

        valor = lectura.valor
        disparo = limite.disparo is not None and valor >= limite.disparo
        fuera = (
            (limite.alarma_baja is not None and valor <= limite.alarma_baja)
            or (limite.alarma_alta is not None and valor >= limite.alarma_alta)
        )
        if not disparo and not fuera:
            estado.racha_fuera = 0
            estado.activo = False
            return None

        if estado.activo:
            return None
        estado.racha_fuera = estado.racha_fuera + 1 if fuera else 0
        if not disparo and estado.racha_fuera < self._muestras_sostenidas:
            return None

        estado.activo = True
        razon = "disparo" if disparo else "alarma sostenida"
        return EventoSistema(
            id=f"sensor:{lectura.tag}:{lectura.variable}:{lectura.ts.isoformat()}",
            ts=lectura.ts,
            tipo=TipoEvento.escalado,
            equipo=lectura.tag,
            origen="sensor",
            parte_id=None,
            nota_ids=[],
            resumen=(
                f"{lectura.tag} {lectura.variable}: {valor:g} {lectura.unidad}; "
                f"{razon} (límites de alarma: baja={limite.alarma_baja}, "
                f"alta={limite.alarma_alta}, disparo={limite.disparo})."
            ),
        )


def _numero(valor: Any) -> float | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return float(valor)
