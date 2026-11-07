"""Estado de las bombas de la planta, para la regla de reserva.

La regla de aprobación necesita saber si, cuando se detiene un equipo, hay
otra bomba parada que pueda tomar el servicio. Ese estado sale de
``/api/estado``. De ahí sólo se toma el estado de las bombas (marcha, parada,
mantenimiento o disparo): ni las fallas ni las lecturas internas, que son
información interna de la simulación y no la evidencia de campo.

Si la planta no responde (por ejemplo, durante un corte de luz) se usa la
última lectura buena: no saber no es motivo para aprobar una orden que baja
la inyección.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import httpx

logger = logging.getLogger("analisis.estado")


class EstadoBombas:
    """Lee ``/api/estado`` y devuelve el estado de cada bomba."""

    def __init__(
        self,
        api_url: str,
        *,
        timeout_s: float = 2.0,
        cliente: httpx.Client | None = None,
    ) -> None:
        self._url = f"{api_url.rstrip('/')}/api/estado"
        self._timeout_s = timeout_s
        self._cliente = cliente or httpx.Client(timeout=timeout_s, trust_env=True)
        self._ultimo: dict[str, str] = {}
        self._lock = threading.Lock()

    def cerrar(self) -> None:
        self._cliente.close()

    def de_bombas(self) -> dict[str, str]:
        """Estado de cada bomba; la última lectura buena si la planta no responde."""
        try:
            respuesta = self._cliente.get(self._url, timeout=self._timeout_s)
            respuesta.raise_for_status()
            datos = respuesta.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("No se pudo leer el estado de las bombas: %s", exc)
            with self._lock:
                return dict(self._ultimo)
        estado = _estados(datos)
        if estado:
            with self._lock:
                self._ultimo = dict(estado)
            return estado
        with self._lock:
            return dict(self._ultimo)


def _estados(datos: Any) -> dict[str, str]:
    if not isinstance(datos, dict):
        return {}
    salida: dict[str, str] = {}
    for bomba in datos.get("bombas") or []:
        if not isinstance(bomba, dict):
            continue
        tag = bomba.get("tag")
        estado = bomba.get("estado")
        if tag and estado:
            salida[str(tag)] = str(estado)
    return salida
