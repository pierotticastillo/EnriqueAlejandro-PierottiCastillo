"""Política única de retry, backoff con jitter y timeouts (P1-17).

Centraliza la clasificación de errores y la decisión de reintentar:

* **Reintenta**: timeout, ``429`` (rate limit), ``5xx`` (error transitorio del servidor).
* **No reintenta**: ``400``, ``401``, ``403``, ``422`` (errores del cliente).
* **Backoff**: exponencial, con jitter, con máximo de intentos y
  respeto del deadline externo.
* **Idempotencia**: solo se reintentan operaciones con efecto si se declara
  explícitamente que son idempotentes.

Está pensada para usarse desde ``ClientePlanta`` (API HTTP de la planta) y desde
el cliente del modelo local (Ollama), sin duplicar lógica en cada capa.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any

#: Códigos HTTP transitorios que se reintentan.
CODIGOS_REINTENTABLES: tuple[int, ...] = (429, 500, 502, 503, 504)

#: Códigos HTTP del cliente que nunca se reintentan.
CODIGOS_NO_REINTENTAR: tuple[int, ...] = (400, 401, 403, 422)

#: Marcas de error de red transitorio (también se buscan en el mensaje de excepción).
MARCAS_TRANSITORIAS: tuple[str, ...] = (
    "transport error",
    "connection reset",
    "connection aborted",
    "connection refused",
    "remote protocol error",
    "incomplete read",
    "server disconnected",
    "timeout",
)


class PoliticaReintento:
    """Política de reintento con backoff exponencial, jitter y deadline."""

    def __init__(
        self,
        *,
        max_intentos: int = 3,
        espera_base_s: float = 0.5,
        espera_maxima_s: float = 30.0,
        jitter: bool = True,
        codigos_reintentables: tuple[int, ...] = CODIGOS_REINTENTABLES,
        codigos_no_reintentar: tuple[int, ...] = CODIGOS_NO_REINTENTAR,
        marcas_transitorias: tuple[str, ...] = MARCAS_TRANSITORIAS,
    ) -> None:
        self.max_intentos = max(1, max_intentos)
        self.espera_base_s = max(0.0, espera_base_s)
        self.espera_maxima_s = max(self.espera_base_s, espera_maxima_s)
        self.jitter = jitter
        self.codigos_reintentables = codigos_reintentables
        self.codigos_no_reintentar = codigos_no_reintentar
        self.marcas_transitorias = marcas_transitorias

    # ------------------------------------------------------------------ clasificación

    def es_reintento_http(self, status_code: int) -> bool:
        """Si un código HTTP HTTP transitorio justifica reintento."""
        if status_code in self.codigos_no_reintentar:
            return False
        return status_code in self.codigos_reintentables

    def es_error_transitorio(self, exc: BaseException) -> bool:
        """Si una excepción es de red (y conviene reintentar)."""
        # Errores de red de httpx: HTTPError que no sea HTTPStatusError (error
        # del cliente) ni TimeoutException (ese lo maneja el deadline).
        try:
            from httpx import HTTPError, HTTPStatusError, TimeoutException
        except ImportError:  # pragma: no cover - httpx es dependencia directa
            HTTPError = Exception  # type: ignore[assignment, misc]
            HTTPStatusError = Exception  # type: ignore[assignment, misc]
            TimeoutException = Exception  # type: ignore[assignment, misc]

        if isinstance(exc, HTTPError) and not isinstance(exc, (HTTPStatusError, TimeoutException)):
            return True

        # También verificar por texto (para excepciones envueltas o no-httpx).
        texto = str(exc).lower()
        return any(marca in texto for marca in self.marcas_transitorias)

    # ------------------------------------------------------------------ espera

    def espera_para(self, intento: int) -> float:
        """Tiempo de espera antes del intento ``intento`` (0-indexado).

        Backoff exponencial con jitter: ``base * 2^intento`` más un jitter
        aleatorio de hasta el 25% del valor, acotado al máximo configurado.
        """
        base = self.espera_base_s * (2 ** intento)
        if self.jitter:
            base *= 1.0 + random.random() * 0.25
        return min(base, self.espera_maxima_s)

    # ------------------------------------------------------------------ ejecución

    def ejecutar(
        self,
        accion: Callable[[], Any],
        *,
        idempotente: bool = True,
        restante_s: Callable[[], float] | None = None,
    ) -> Any:
        """Ejecuta ``accion`` aplicando la política de reintento.

        ``restante_s`` es un callable que devuelve los segundos que quedan
        antes del deadline global. Si queda poco o nada de tiempo, no se
        reintenta y se propaga el error.

        ``idempotente`` indica si la operación tiene efectos secundarios que
        podrían duplicarse. Si no es idempotente y falló una vez, no se
        reintenta.
        """
        ultimo: Exception | None = None
        for intento in range(self.max_intentos):
            if restante_s is not None and restante_s() <= 0:
                raise TimeoutError(
                    "se agotó el deadline global antes de ejecutar la operación"
                ) from ultimo
            try:
                return accion()
            except Exception as exc:  # noqa: BLE001 - se clasifica más abajo
                ultimo = exc
                if intento + 1 >= self.max_intentos:
                    break
                if not self._debe_reintentar(exc, idempotente=idempotente):
                    raise
                espera = self.espera_para(intento)
                if restante_s is not None:
                    espera = min(espera, max(0.0, restante_s()))
                if espera > 0:
                    time.sleep(espera)
        raise ultimo if ultimo is not None else RuntimeError("la operación falló")

    def _debe_reintentar(self, exc: BaseException, *, idempotente: bool) -> bool:
        """Si una excepción justifica un reintento."""
        # Errores de red transitorios: solo se reintentan si la operación es
        # idempotente, para no duplicar efectos secundarios.
        if self.es_error_transitorio(exc):
            return idempotente
        # HTTP status error: mirar el código.
        try:
            from httpx import HTTPStatusError
        except ImportError:  # pragma: no cover - httpx es dependencia directa
            HTTPStatusError = Exception  # type: ignore[assignment, misc]
        if isinstance(exc, HTTPStatusError):
            status = exc.response.status_code
            if status in self.codigos_no_reintentar:
                return False
            if status in self.codigos_reintentables:
                return idempotente
        return False


def ejecutar_con_reintento(
    accion: Callable[[], Any],
    *,
    max_intentos: int = 3,
    espera_base_s: float = 0.5,
    espera_maxima_s: float = 30.0,
    jitter: bool = True,
    idempotente: bool = True,
    restante_s: Callable[[], float] | None = None,
) -> Any:
    """Ejecuta ``accion`` con una política de reintento por defecto.

    Es un atajo para no tener que instanciar ``PoliticaReintento`` en cada
    llamada. Los valores por defecto son conservadores: 3 intentos, base 0.5 s
    y jitter activado.
    """
    politica = PoliticaReintento(
        max_intentos=max_intentos,
        espera_base_s=espera_base_s,
        espera_maxima_s=espera_maxima_s,
        jitter=jitter,
    )
    return politica.ejecutar(
        accion,
        idempotente=idempotente,
        restante_s=restante_s,
    )


__all__ = [
    "PoliticaReintento",
    "ejecutar_con_reintento",
    "CODIGOS_REINTENTABLES",
    "CODIGOS_NO_REINTENTAR",
    "MARCAS_TRANSITORIAS",
]
