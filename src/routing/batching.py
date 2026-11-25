"""Batching de requests no urgentes (Ítem 3.9 del Plan de implementación 3).

La presentación de Semana_5 sugiere agrupar requests **no urgentes** (evaluaciones,
clasificación de reseñas del mes, indexación de documentos, reportes nocturnos)
en un solo llamado para reducir overhead y RPM. Los requests urgentes (chat,
voz, espera de usuario) **no pasan por el batch**: se envían inmediatamente.

Este módulo implementa un acumulador que:

* acepta requests con un identificador y sus datos;
* marca cuáles son urgentes (se envían de inmediato) y cuáles no (se agrupan);
* dispara el envío del batch cuando se alcanza un umbral de tamaño o de tiempo;
* permite forzar el envío con `enviar_ahora()`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Generic, TypeVar

#: Tipo de los datos de un request (qué se envía al modelo).
T = TypeVar("T")


@dataclass
class ItemBatch(Generic[T]):
    """Un elemento acumulado en el batch."""

    request_id: str
    datos: T
    urgente: bool
    creado_en: float = field(default_factory=time.monotonic)


@dataclass
class ResultadoBatch:
    """Resultado de un envío de batch."""

    enviados: int
    urgentes_enviados_inmediato: int
    motivo: str


class BatchNoUrgente(Generic[T]):
    """Acumulador de requests no urgentes para envío en batch.

    Se dispara el envío cuando se alcanza el umbral de tamaño o el umbral de
    tiempo desde el primer elemento no urgente acumulado.
    """

    def __init__(
        self,
        *,
        callback_envio: Callable[[list[ItemBatch[T]]], None],
        umbral_tamano: int = 10,
        umbral_tiempo_s: float = 30.0,
    ) -> None:
        if umbral_tamano < 1:
            raise ValueError("umbral_tamano debe ser >= 1")
        if umbral_tiempo_s <= 0:
            raise ValueError("umbral_tiempo_s debe ser > 0")

        self._callback = callback_envio
        self._umbral_tamano = umbral_tamano
        self._umbral_tiempo_s = umbral_tiempo_s
        self._pendientes: list[ItemBatch[T]] = []
        self._primer_no_urgente: float | None = None
        self._enviados_inmediato = 0
        self._enviados_en_batch = 0

    # ------------------------------------------------------------------ alta

    def agregar(self, *, request_id: str, datos: T, urgente: bool = False) -> None:
        """Agrega un request al acumulador.

        Si es urgente, se envía inmediatamente por el callback (con un batch
        de un solo elemento). Si no es urgente, se acumula hasta alcanzar un
        umbral.
        """
        if urgente:
            item = ItemBatch(request_id=request_id, datos=datos, urgente=True)
            self._callback([item])
            self._enviados_inmediato += 1
            return

        item = ItemBatch(request_id=request_id, datos=datos, urgente=False)
        self._pendientes.append(item)

        if self._primer_no_urgente is None:
            self._primer_no_urgente = time.monotonic()

        self._disparar_si_es_hora()

    def enviar_ahora(self) -> ResultadoBatch:
        """Fuerza el envío de todos los elementos no urgentes pendientes.

        Devuelve un `ResultadoBatch` con cuántos se enviaron.
        """
        pendientes = self._pendientes
        self._pendientes = []
        self._primer_no_urgente = None

        if pendientes:
            self._callback(pendientes)
            self._enviados_en_batch += len(pendientes)

        return ResultadoBatch(
            enviados=len(pendientes),
            urgentes_enviados_inmediato=self._enviados_inmediato,
            motivo="envío forzado",
        )

    def esta_vacio(self) -> bool:
        """True si no hay elementos no urgentes pendientes."""
        return len(self._pendientes) == 0

    def cantidad_pendientes(self) -> int:
        """Cuántos elementos no urgentes están acumulados esperando envío."""
        return len(self._pendientes)

    # ------------------------------------------------------------------ interna

    def _disparar_si_es_hora(self) -> None:
        """Si se alcanzó el umbral de tamaño o de tiempo, envía el batch."""
        ahora = time.monotonic()
        cumple_tamano = len(self._pendientes) >= self._umbral_tamano
        cumple_tiempo = (
            self._primer_no_urgente is not None
            and (ahora - self._primer_no_urgente) >= self._umbral_tiempo_s
        )

        if not (cumple_tamano or cumple_tiempo):
            return

        pendientes = self._pendientes
        self._pendientes = []
        self._primer_no_urgente = None

        self._callback(pendientes)
        self._enviados_en_batch += len(pendientes)

    # ------------------------------------------------------------------ stats

    def resumen(self) -> dict[str, object]:
        """Devuelve un resumen del estado del acumulador."""
        return {
            "pendientes": len(self._pendientes),
            "enviados_inmediato": self._enviados_inmediato,
            "enviados_en_batch": self._enviados_en_batch,
            "total_procesados": self._enviados_inmediato
            + self._enviados_en_batch
            + len(self._pendientes),
            "umbral_tamano": self._umbral_tamano,
            "umbral_tiempo_s": self._umbral_tiempo_s,
        }


#: Instancia global simple (opcional). Se prefiere inyectar el acumulador.
batch_no_urgente: BatchNoUrgente[object] | None = None
