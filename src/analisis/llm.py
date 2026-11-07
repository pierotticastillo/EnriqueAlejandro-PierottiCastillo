"""Cliente del modelo de lenguaje local (Ollama): un auxiliar acotado.

El plan pide que el análisis esté detrás de una interfaz
``AnalysisModel.invoke(prompt, images) -> StructuredOutput``. Esta es esa
interfaz, con una implementación contra el modelo de la lista cerrada
(``Qwen2.5-3B-Instruct`` Q4_K_M servido por Ollama).

**Lo que el modelo hace y lo que no hace**

* Hace: redactar la justificación técnica de una orden, a partir de material
  confiable (catálogo de fallas, mediciones estructuradas, tareas, reglas).
* No hace: identificar la falla, elegir repuestos, aprobar la orden ni escribir
  el motivo. Eso está en código (`analisis/aprobacion.py`) y no se toca.

**Por qué es seguro**

* La salida se valida contra un esquema (`SalidaEstructurada`) antes de
  usarla: si el modelo devuelve otra cosa, no se usa.
* Las citas que devuelve se comprueban contra las citas conocidas: si inventa
  una, no se usa.
* El prompt no lleva el texto libre del operador (eso lo pone el prompt de
  sistema, no el usuario: el modelo no ve la nota).
* Si Ollama no está, no responde o tarda más que el timeout, el sistema sigue
  con el texto determinista. **El sistema no depende de este modelo.**
* La configuración es la que fija la lista: contexto de 2048, 2 hilos,
  temperatura 0, semilla fija y ``keep_alive=0`` para que el modelo se
  descargue de memoria al terminar (la planta tiene 8 GB).
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger("analisis.llm")

#: Tope de tokens de la respuesta: el modelo no puede escribir un tratado.
MAX_TOKENS = 250
#: Tope de caracteres del resumen, por si el modelo se extiende.
MAX_CARACTERES = 1200


class ErrorModelo(Exception):
    """El modelo no se pudo usar: se cae al texto determinista."""


class SalidaEstructurada(BaseModel):
    """Lo que se le pide al modelo: texto y citas, nada más.

    ``extra="forbid"``: si el modelo inventa campos, la salida se rechaza.
    """

    model_config = ConfigDict(extra="forbid")

    resumen: str = Field(min_length=1, max_length=MAX_CARACTERES)
    causas: list[str] = Field(default_factory=list, max_length=5)
    citas: list[str] = Field(default_factory=list, max_length=5)
    confianza: Literal["alta", "media", "baja"] = "media"


class AnalysisModel(Protocol):
    """La interfaz que pide el plan para el modelo de análisis."""

    def invoke(
        self, prompt: str, images: Sequence[str] = ()
    ) -> SalidaEstructurada:  # pragma: no cover - es un protocolo
        ...


class ModeloOllama:
    """El modelo de la lista cerrada, servido por Ollama en la PC de planta."""

    def __init__(
        self,
        url: str,
        modelo: str,
        *,
        num_ctx: int = 2048,
        num_thread: int = 2,
        timeout_s: float = 30.0,
        seed: int = 42,
        keep_alive: int = 0,
        max_tokens: int = MAX_TOKENS,
        proxy: str | None = None,
        cliente: Any = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._modelo = modelo
        self._num_ctx = num_ctx
        self._num_thread = num_thread
        self._timeout_s = timeout_s
        self._seed = seed
        self._keep_alive = keep_alive
        self._max_tokens = max_tokens
        self._proxy = proxy
        self._cliente = cliente

    @property
    def nombre(self) -> str:
        """El nombre del modelo, para dejarlo escrito en la bitácora."""
        return self._modelo

    # ------------------------------------------------------------------ público

    def invoke(self, prompt: str, images: Sequence[str] = ()) -> SalidaEstructurada:
        """Le pide la redacción al modelo y devuelve la salida validada."""
        cuerpo = {
            "model": self._modelo,
            "messages": [
                {"role": "system", "content": _instruccion()},
                {"role": "user", "content": prompt},
            ],
            # El esquema le dice a Ollama qué tiene que devolver: la salida
            # sale validada por construcción de la librería, no por filtro de texto.
            "format": SalidaEstructurada.model_json_schema(),
            "stream": False,
            "keep_alive": self._keep_alive,
            "options": {
                "num_ctx": self._num_ctx,
                "num_thread": self._num_thread,
                "temperature": 0,
                "seed": self._seed,
                "num_predict": self._max_tokens,
            },
        }
        if images:
            cuerpo["images"] = [_imagen_base64(ruta) for ruta in images]
        datos = self._posterior(cuerpo)
        return _validar(datos)

    # ------------------------------------------------------------------ internos

    def _posterior(self, cuerpo: dict[str, Any]) -> dict[str, Any]:
        try:
            if self._cliente is not None:
                respuesta = self._cliente.post(
                    f"{self._url}/api/chat", json=cuerpo, timeout=self._timeout_s
                )
            else:
                import httpx

                # `trust_env=False`: el tráfico a Ollama no sale por el proxy
                # del entorno. Si se configuró un proxy (en la simulación, el
                # del enlace), se usa ése y sólo ése.
                with httpx.Client(
                    timeout=self._timeout_s,
                    trust_env=False,
                    proxy=self._proxy,
                ) as cliente:
                    respuesta = cliente.post(f"{self._url}/api/chat", json=cuerpo)
        except Exception as exc:  # noqa: BLE001 - cualquier fallo cae al determinista
            raise ErrorModelo(f"No se pudo llamar a Ollama: {exc}") from exc
        if respuesta.status_code != 200:
            raise ErrorModelo(f"Ollama respondió {respuesta.status_code}")
        try:
            return respuesta.json()
        except ValueError as exc:
            raise ErrorModelo("Ollama devolvió una respuesta que no es JSON") from exc


def _validar(datos: dict[str, Any]) -> SalidaEstructurada:
    """Saca el texto del modelo y lo valida contra el esquema."""
    contenido = str((datos.get("message") or {}).get("content") or "").strip()
    if not contenido:
        raise ErrorModelo("Ollama devolvió una respuesta vacía")
    try:
        carga = json.loads(contenido)
    except json.JSONDecodeError as exc:
        raise ErrorModelo(f"El modelo no devolvió JSON: {exc}") from exc
    if not isinstance(carga, dict):
        raise ErrorModelo("El modelo devolvió un JSON que no es un objeto")
    try:
        salida = SalidaEstructurada.model_validate(carga)
    except ValidationError as exc:
        raise ErrorModelo(f"La salida del modelo no valida: {exc}") from exc
    return salida


def _instruccion() -> str:
    """La instrucción de sistema: qué hacer y qué no hacer."""
    return (
        "Sos un asistente técnico de una planta de inyección de agua. "
        "Redactás la justificación técnica de una orden de trabajo a partir de "
        "la ficha de la falla, las mediciones y las tareas del plan. "
        "Respondés únicamente con el JSON del esquema. "
        "Reglas: no aprobás ni rechazás órdenes, no mencionás estados de "
        "aprobación, no cambiás tareas, repuestos, costos ni motivos, no "
        "agregás información que no esté en los datos, no citás documentos que "
        "no estén en la lista de citas provista, no escribís números de "
        "documento ni de sección dentro del texto (las referencias van sólo en "
        "el campo citas) y no repetís el texto del operador."
    )


def _imagen_base64(ruta: str) -> str:
    """Una imagen en base64, para los modelos que aceptan visión."""
    try:
        return base64.b64encode(Path(ruta).read_bytes()).decode("ascii")
    except OSError as exc:
        raise ErrorModelo(f"No se pudo leer la imagen {ruta}: {exc}") from exc


def crear_modelo(settings: Any) -> ModeloOllama | None:
    """El modelo de análisis, o ``None`` si no hay ninguno activo.

    ``None`` significa "seguí con el texto determinista": es un estado normal,
    no un error. El sistema no necesita el modelo para funcionar.
    """
    if not getattr(settings, "llm_activo", False):
        return None
    return ModeloOllama(
        settings.ollama_url,
        settings.ollama_modelo,
        num_ctx=settings.ollama_num_ctx,
        num_thread=settings.ollama_num_thread,
        timeout_s=settings.ollama_timeout_s,
        seed=settings.ollama_seed,
        keep_alive=settings.ollama_keep_alive,
        proxy=getattr(settings, "ollama_proxy", None) or None,
    )
