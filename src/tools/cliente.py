"""Cliente HTTP de la API de la planta para las tools de solo lectura.

Una sola política de red para todas las tools: timeout, reintentos con espera
creciente y errores tipados. Si la planta no responde, la tool falla con un
mensaje claro en vez de dejar al llamador esperando.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("tools.cliente")


class ErrorHerramienta(Exception):
    """Error de una tool, con el contexto mínimo para diagnosticar y para la traza."""

    def __init__(
        self,
        mensaje: str,
        *,
        herramienta: str | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.herramienta = herramienta
        self.url = url

    def __str__(self) -> str:
        partes = [self.mensaje]
        if self.herramienta:
            partes.insert(0, f"[{self.herramienta}]")
        if self.url:
            partes.append(f"({self.url})")
        return " ".join(partes)


class ClientePlanta:
    """Cliente de la API de la planta: GET, timeout, reintentos y errores."""

    def __init__(
        self,
        api_url: str,
        *,
        timeout_s: float = 10.0,
        reintentos: int = 2,
        espera_s: float = 0.5,
        cliente: httpx.Client | None = None,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._timeout_s = timeout_s
        self._reintentos = max(0, reintentos)
        self._espera_s = espera_s
        self._cliente = cliente or httpx.Client(timeout=timeout_s, trust_env=True)

    def cerrar(self) -> None:
        self._cliente.close()

    def get(self, ruta: str, *, params: dict[str, Any] | None = None) -> Any:
        """GET a la planta. Devuelve el JSON o levanta ``ErrorHerramienta``."""
        url = f"{self._api_url}/{ruta.lstrip('/')}"
        ultimo_error: Exception | None = None
        for intento in range(self._reintentos + 1):
            try:
                respuesta = self._cliente.get(url, params=params, timeout=self._timeout_s)
                if respuesta.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"HTTP {respuesta.status_code}",
                        request=respuesta.request,
                        response=respuesta,
                    )
                respuesta.raise_for_status()
                return respuesta.json()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status < 500:
                    raise ErrorHerramienta(
                        f"La planta rechazó la consulta (HTTP {status})",
                        url=url,
                    ) from exc
                ultimo_error = exc
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                ultimo_error = exc
            except ValueError as exc:
                raise ErrorHerramienta(
                    "La planta devolvió una respuesta que no es JSON", url=url
                ) from exc

            if intento < self._reintentos:
                espera = self._espera_s * (2**intento)
                logger.warning(
                    "Fallo consultando %s (%s); reintento en %.1fs", url, ultimo_error, espera
                )
                time.sleep(espera)

        raise ErrorHerramienta(
            f"No se pudo consultar la planta: {ultimo_error}", url=url
        ) from ultimo_error
