"""Cliente HTTP de la API de la planta para las tools de solo lectura.

Una sola política de red para todas las tools: timeout, reintentos con espera
creciente y errores tipados. Si la planta no responde, la tool falla con un
mensaje claro en vez de dejar al llamador esperando.

Los reintentos usan la política unificada de ``tools.politica_reintento``:
reintenta ``429``, ``5xx`` y errores de red transitorios; no reintenta
``400``, ``401``, ``403``, ``422``; backoff exponencial con jitter.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from tools.politica_reintento import PoliticaReintento, ejecutar_con_reintento

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
        politica: PoliticaReintento | None = None,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._timeout_s = timeout_s
        self._politica = politica or PoliticaReintento(
            max_intentos=max(1, reintentos + 1),
            espera_base_s=espera_s,
        )
        self._cliente = cliente or httpx.Client(timeout=timeout_s, trust_env=True)

    def cerrar(self) -> None:
        self._cliente.close()

    def get(self, ruta: str, *, params: dict[str, Any] | None = None) -> Any:
        """GET a la planta. Devuelve el JSON o levanta ``ErrorHerramienta``."""
        url = f"{self._api_url}/{ruta.lstrip('/')}"

        def llamar() -> Any:
            respuesta = self._cliente.get(url, params=params, timeout=self._timeout_s)
            if respuesta.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"HTTP {respuesta.status_code}",
                    request=respuesta.request,
                    response=respuesta,
                )
            respuesta.raise_for_status()
            return respuesta.json()

        try:
            return ejecutar_con_reintento(
                llamar,
                max_intentos=self._politica.max_intentos,
                espera_base_s=self._politica.espera_base_s,
                espera_maxima_s=self._politica.espera_maxima_s,
                jitter=self._politica.jitter,
                idempotente=True,
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status < 500:
                raise ErrorHerramienta(
                    f"La planta rechazó la consulta (HTTP {status})",
                    url=url,
                ) from exc
            raise ErrorHerramienta(
                f"No se pudo consultar la planta (HTTP {status})", url=url
            ) from exc
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise ErrorHerramienta(
                f"No se pudo consultar la planta: {exc}", url=url
            ) from exc
        except (TimeoutError, RuntimeError) as exc:
            raise ErrorHerramienta(
                f"No se pudo consultar la planta: {exc}", url=url
            ) from exc
        except ValueError as exc:
            raise ErrorHerramienta(
                "La planta devolvió una respuesta que no es JSON", url=url
            ) from exc
