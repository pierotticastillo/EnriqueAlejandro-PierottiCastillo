"""Cliente del modelo de nube con visión (NVIDIA): **describe** fotos, no decide.

Es el cuarto modelo de la lista cerrada: ``Nemotron 3 Nano Omni 30B A3B``
(``nvidia/nemotron-3-nano-omni-30b-a3b-reasoning``), servido por la API de
NVIDIA. Vive en la nube, no en la PC de planta.

**Lo que el modelo hace y lo que no hace**

* Hace: describir lo que se ve en una foto de la planta (¿se ve daño
  estructural? ¿qué se ve?), para que quien revise la foto no tenga que
  adivinar qué está mirando.
* No hace: decidir si la orden se aprueba, si la falla es una u otra, ni
  afirmar que "no hay daño". Eso está en código
  (``analisis/aprobacion.py``) y no se toca.

**Por qué es seguro**

* La salida se valida contra un esquema (``SalidaVision``) antes de usarla: si
  el modelo devuelve otra cosa, no se usa.
* Si algo falla (no hay clave, la red está caída, la respuesta no valida), el
  resultado es **indeterminado**: nunca "no hay daño". Una foto que no se pudo
  leer sigue yendo a revisión humana.
* El prompt **no lleva el texto libre del operador**: sólo la foto y una
  pregunta cerrada sobre daño estructural.

**Por qué la llamada sale por el proxy del enlace**

El contenedor de la planta está en redes ``internal: true``: no tiene ruta
directa a internet. La única salida sancionada es el proxy del enlace
(``enlace:3128``), el mismo que usa la salida a internet de la planta. El
modelo de visión no abre un camino nuevo.
"""

from __future__ import annotations

import base64
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger("analisis.vision")

#: Tope de tokens de la respuesta. El modelo es de razonamiento: si el tope es
#: chico, gasta todo en razonamiento y la respuesta final queda cortada
#: (``finish_reason: length``). Medido: ~190 tokens de razonamiento + la
#: respuesta.
MAX_TOKENS = 800
#: Tope de caracteres de la descripción, por si el modelo se extiende.
MAX_CARACTERES = 400

#: El modelo a veces envuelve el JSON en un bloque de código.
_BLOQUE_JSON = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

PREGUNTA = (
    "Sos un inspector de una planta de inyección de agua. Esta es una foto de "
    "un equipo de la planta. ¿Se ve daño estructural (grieta, rotura, "
    "deformación permanente o pérdida de material)? "
    'Respondé SÓLO con un JSON: {"danio_estructural": true|false, "descripcion": "..."}'
)


class ErrorVision(Exception):
    """El modelo de visión no se pudo usar."""


class SalidaVision(BaseModel):
    """Lo que se le pide al modelo: si hay daño estructural y qué se ve.

    ``extra="forbid"``: si el modelo inventa campos, la salida se rechaza.
    """

    model_config = ConfigDict(extra="forbid")

    danio_estructural: bool
    descripcion: str = Field(min_length=1, max_length=MAX_CARACTERES)


@dataclass(frozen=True)
class ResultadoVision:
    """Qué se pudo saber de una foto.

    ``estado="indeterminado"`` significa que **no se sabe**: la clave no está,
    la red no llegó, la respuesta no validó. Nunca significa "no hay daño".
    """

    estado: Literal["ok", "indeterminado"]
    danio_estructural: bool | None
    descripcion: str
    detalle: str
    modelo: str
    tokens_prompt: int = 0
    tokens_completion: int = 0
    segundos: float = 0.0

    @property
    def hay_danio(self) -> bool:
        """¿El modelo vio daño estructural? Sólo si la salida validó."""
        return self.estado == "ok" and bool(self.danio_estructural)

    @property
    def utilizable(self) -> bool:
        """¿La descripción sirve para avisarle a un humano?"""
        return self.estado == "ok" and bool(self.descripcion.strip())


class ClienteVision:
    """Cliente del modelo de visión de la lista cerrada."""

    def __init__(
        self,
        url: str,
        modelo: str,
        api_key: str,
        *,
        max_tokens: int = MAX_TOKENS,
        timeout_s: float = 30.0,
        temperature: float = 0.0,
        proxy: str | None = None,
        cliente: Any = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._modelo = modelo
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._timeout_s = timeout_s
        self._temperature = temperature
        self._proxy = proxy
        self._cliente = cliente

    @property
    def nombre(self) -> str:
        """El nombre del modelo, para dejarlo escrito en la bitácora."""
        return self._modelo

    @property
    def habilitado(self) -> bool:
        """¿Se puede llamar? Sin clave, no."""
        return bool(self._api_key.strip() and self._modelo.strip())

    # ------------------------------------------------------------------ público

    def describir(self, ruta: Path) -> ResultadoVision:
        """Describe una foto. Nunca levanta: si falla, queda indeterminado."""
        if not self.habilitado:
            return self._indeterminado("no hay clave de API configurada")
        try:
            imagen = _imagen_base64(ruta)
        except OSError as exc:
            return self._indeterminado(f"no se pudo leer la foto: {exc}")
        except ValueError as exc:
            return self._indeterminado(str(exc))

        cuerpo = {
            "model": self._modelo,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PREGUNTA},
                        {"type": "image_url", "image_url": {"url": imagen}},
                    ],
                }
            ],
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "stream": False,
        }
        inicio = time.monotonic()
        try:
            datos = self._posterior(cuerpo)
        except ErrorVision as exc:
            logger.warning("El modelo de visión no respondió: %s", exc)
            return self._indeterminado(str(exc), segundos=time.monotonic() - inicio)
        segundos = time.monotonic() - inicio

        try:
            salida = _validar(datos)
        except ErrorVision as exc:
            logger.warning("La salida del modelo de visión no validó: %s", exc)
            return self._indeterminado(str(exc), segundos=segundos)

        uso = datos.get("usage") or {}
        return ResultadoVision(
            estado="ok",
            danio_estructural=salida.danio_estructural,
            descripcion=salida.descripcion.strip(),
            detalle="ok",
            modelo=self._modelo,
            tokens_prompt=int(uso.get("prompt_tokens") or 0),
            tokens_completion=int(uso.get("completion_tokens") or 0),
            segundos=segundos,
        )

    # ------------------------------------------------------------------ internos

    def _posterior(self, cuerpo: dict[str, Any]) -> dict[str, Any]:
        cabeceras = {"Authorization": f"Bearer {self._api_key}"}
        try:
            if self._cliente is not None:
                respuesta = self._cliente.post(
                    f"{self._url}/chat/completions",
                    json=cuerpo,
                    headers=cabeceras,
                    timeout=self._timeout_s,
                )
            else:
                import httpx

                # `trust_env=False`: el tráfico no sale por el proxy del
                # entorno. Si se configuró un proxy (en la simulación, el del
                # enlace), se usa ése y sólo ése.
                with httpx.Client(
                    timeout=self._timeout_s,
                    trust_env=False,
                    proxy=self._proxy,
                ) as cliente:
                    respuesta = cliente.post(
                        f"{self._url}/chat/completions",
                        json=cuerpo,
                        headers=cabeceras,
                    )
        except Exception as exc:  # noqa: BLE001 - cualquier fallo queda indeterminado
            via = f" por el proxy {self._proxy}" if self._proxy else " de forma directa"
            raise ErrorVision(
                f"No se pudo llamar a la API de NVIDIA{via}: {exc}"
            ) from exc
        if respuesta.status_code != 200:
            raise ErrorVision(f"La API de NVIDIA respondió {respuesta.status_code}")
        try:
            return respuesta.json()
        except ValueError as exc:
            raise ErrorVision("La API de NVIDIA devolvió una respuesta que no es JSON") from exc

    def _indeterminado(self, detalle: str, *, segundos: float = 0.0) -> ResultadoVision:
        return ResultadoVision(
            estado="indeterminado",
            danio_estructural=None,
            descripcion="",
            detalle=detalle,
            modelo=self._modelo,
            segundos=segundos,
        )


def crear_cliente_vision(settings: Any) -> ClienteVision | None:
    """El cliente del modelo de visión, si el que está configurado es el de la lista.

    Si no hay clave, o el modelo configurado no es el de la lista cerrada, no
    se devuelve cliente: el sistema sigue **sin** llamar a la nube y las fotos
    van a revisión humana (fail-safe).
    """
    from modelos_lista import CLAVE_VISION, modelo_de

    if not settings.nvidia_api_key.strip():
        return None
    if modelo_de(CLAVE_VISION, settings.nvidia_modelo) is None:
        logger.warning(
            "El modelo de visión configurado (%r) no está en la lista cerrada: "
            "no se llama a la nube",
            settings.nvidia_modelo,
        )
        return None
    return ClienteVision(
        settings.nvidia_url,
        settings.nvidia_modelo,
        settings.nvidia_api_key,
        max_tokens=settings.nvidia_max_tokens,
        timeout_s=settings.nvidia_timeout_s,
        temperature=settings.nvidia_temperature,
        proxy=settings.nvidia_proxy or None,
    )


def _validar(datos: dict[str, Any]) -> SalidaVision:
    """Saca el JSON de la respuesta del modelo y lo valida contra el esquema."""
    mensaje = (datos.get("choices") or [{}])[0].get("message") or {}
    contenido = str(mensaje.get("content") or "").strip()
    if not contenido:
        # Los modelos de razonamiento a veces dejan el texto en otro campo y
        # `content` en null: si no hay contenido, no hay salida que validar.
        raise ErrorVision("la respuesta no trae contenido")
    try:
        salida = SalidaVision.model_validate_json(_json_de(contenido))
    except ValidationError as exc:
        raise ErrorVision(f"la salida no cumple el esquema: {exc.errors()[:2]}") from exc
    except ValueError as exc:
        raise ErrorVision(f"la salida no es JSON: {exc}") from exc
    return salida


def _json_de(texto: str) -> str:
    """Saca el objeto JSON del texto, aunque venga en un bloque de código."""
    bloque = _BLOQUE_JSON.search(texto)
    if bloque:
        return bloque.group(1)
    inicio = texto.find("{")
    fin = texto.rfind("}")
    if inicio == -1 or fin <= inicio:
        raise ValueError("no hay un objeto JSON en la respuesta")
    return texto[inicio : fin + 1]


def _imagen_base64(ruta: Path) -> str:
    """La foto como ``data:`` URL, que es como la API la espera."""
    if not ruta.exists():
        raise ValueError(f"la foto no existe: {ruta}")
    tipo = _tipo_mime(ruta)
    datos = base64.b64encode(ruta.read_bytes()).decode("ascii")
    return f"data:{tipo};base64,{datos}"


def _tipo_mime(ruta: Path) -> str:
    """El tipo MIME de la foto, por su extensión."""
    extension = ruta.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(extension, "image/jpeg")
