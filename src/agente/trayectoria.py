"""Registro de la trayectoria: cada paso, con su modelo, sus tools y su costo.

El enunciado lo pide en la sección F: *"Cada decisión queda registrada con el
modelo usado, las herramientas llamadas y sus argumentos, los tokens, el
costo y el tiempo de cada paso"*. Y el README de la corrección del curso lo
lista explícitamente entre lo que se revisa a mano.

Acá se registra:

* cada llamada al modelo: modelo, tokens de entrada y de salida, segundos;
* cada llamada a una herramienta: nombre, argumentos y resultado;
* cada decisión de la planta: etapa, equipo y orden relacionada.

El registro va a PostgreSQL (``registro_trayectoria``), que es donde ya vive
el resto de la auditoría del sistema.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from sqlalchemy import text
from sqlalchemy.engine import Engine

from agente.presupuesto import Presupuesto

logger = logging.getLogger("agente.trayectoria")

#: Tope de caracteres que se guardan de una entrada o de una salida.
MAX_CARACTERES = 2000


class RegistradorTrayectoria:
    """Escribe los pasos en la tabla ``registro_trayectoria``."""

    def __init__(self, engine: Engine | None) -> None:
        self._engine = engine

    @property
    def activo(self) -> bool:
        return self._engine is not None

    def registrar(
        self,
        *,
        etapa: str,
        tipo: str,
        modelo: str | None = None,
        herramienta: str | None = None,
        argumentos: Any = None,
        salida: Any = None,
        tokens_prompt: int = 0,
        tokens_completion: int = 0,
        costo_usd: float = 0.0,
        segundos: float = 0.0,
        error: str | None = None,
        orden_id: str | None = None,
        evento_id: str | None = None,
        paso: int | None = None,
        equipo: str | None = None,
    ) -> None:
        """Escribe un paso. Si la base no está, no se rompe nada."""
        if self._engine is None:
            return
        try:
            with self._engine.begin() as conexion:
                conexion.execute(
                    text(
                        """
                        INSERT INTO registro_trayectoria (
                            etapa, tipo, modelo, herramienta, argumentos, salida,
                            tokens_prompt, tokens_completion, costo_usd, segundos,
                            error, orden_id, evento_id, paso, equipo
                        )
                        VALUES (
                            :etapa, :tipo, :modelo, :herramienta,
                            CAST(:argumentos AS jsonb), CAST(:salida AS jsonb),
                            :tokens_prompt, :tokens_completion, :costo_usd, :segundos,
                            :error, :orden_id, :evento_id, :paso, :equipo
                        )
                        """
                    ),
                    {
                        "etapa": etapa,
                        "tipo": tipo,
                        "modelo": modelo,
                        "herramienta": herramienta,
                        "argumentos": _json(argumentos),
                        "salida": _json(salida),
                        "tokens_prompt": int(tokens_prompt or 0),
                        "tokens_completion": int(tokens_completion or 0),
                        "costo_usd": float(costo_usd or 0.0),
                        "segundos": float(segundos or 0.0),
                        "error": _texto(error),
                        "orden_id": orden_id,
                        "evento_id": evento_id,
                        "paso": paso,
                        "equipo": equipo,
                    },
                )
        except Exception:  # noqa: BLE001 - registrar no puede tumbar el sistema
            logger.warning("No se pudo registrar el paso de la trayectoria", exc_info=True)

    def pasos(self, orden_id: str) -> list[dict[str, Any]]:
        """Los pasos registrados de una orden, en orden."""
        if self._engine is None:
            return []
        with self._engine.connect() as conexion:
            filas = (
                conexion.execute(
                    text(
                        """
                        SELECT ts, etapa, tipo, modelo, herramienta, argumentos,
                               tokens_prompt, tokens_completion, costo_usd, segundos,
                               error, paso
                        FROM registro_trayectoria
                        WHERE orden_id = :orden
                        ORDER BY ts, paso
                        """
                    ),
                    {"orden": orden_id},
                )
                .mappings()
                .all()
            )
        return [dict(fila) for fila in filas]


class TrazaAgente(BaseCallbackHandler):
    """Callback de LangChain: registra cada paso del agente.

    Se engancha al agente con ``config={"callbacks": [...]}``. Cada llamada al
    modelo y cada llamada a una herramienta dejan su fila con el modelo, los
    tokens, los argumentos y el tiempo.
    """

    def __init__(
        self,
        registrador: RegistradorTrayectoria,
        *,
        etapa: str = "agente",
        orden_id: str | None = None,
        evento_id: str | None = None,
        equipo: str | None = None,
        presupuesto: Presupuesto | None = None,
        modelo: str | None = None,
        precio_usd_por_millon: float = 0.0,
    ) -> None:
        self._registrador = registrador
        self._etapa = etapa
        self._orden_id = orden_id
        self._evento_id = evento_id
        self._equipo = equipo
        self._presupuesto = presupuesto
        # El nombre del modelo, si no viene en los parámetros de la invocación.
        self._modelo_agente = modelo
        # La tarifa del modelo: con el modelo local de la planta es 0 (no hay
        # nada que facturar), y con un modelo de nube se pone la tarifa real y
        # el costo sale de los tokens medidos.
        self._precio = float(precio_usd_por_millon or 0.0)
        self._inicios: dict[str, float] = {}
        self._modelos: dict[str, str] = {}
        self._argumentos: dict[str, Any] = {}
        self._paso = 0
        # Los totales de la consulta: los usa el resumen que se escribe
        # cuando la consulta termina (o se corta).
        self._tokens_prompt = 0
        self._tokens_completion = 0
        self._costo = 0.0
        self._segundos_modelo = 0.0

    # ------------------------------------------------------------------ totales

    @property
    def tokens_prompt(self) -> int:
        """Los tokens de entrada que gastó la consulta."""
        return self._tokens_prompt

    @property
    def tokens_completion(self) -> int:
        """Los tokens de salida que gastó la consulta."""
        return self._tokens_completion

    @property
    def costo_usd(self) -> float:
        """El costo de la consulta, con la tarifa del modelo."""
        return self._costo

    @property
    def segundos(self) -> float:
        """Cuánto tardó el modelo, sumando todas sus llamadas."""
        return self._segundos_modelo

    # El presupuesto agotado es una **condición de corte**: tiene que
    # interrumpir al agente, no quedar anotado en el log y seguir.
    raise_error = True

    # ------------------------------------------------------------------ modelo

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id") or "")
        self._inicios[run_id] = time.monotonic()
        parametros = kwargs.get("invocation_params") or {}
        modelo = str(parametros.get("model") or self._modelo_agente or serialized.get("name") or "")
        self._modelos[run_id] = modelo
        self._argumentos[run_id] = prompts[0] if prompts else ""

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id") or "")
        segundos = _segundos(self._inicios.pop(run_id, None))
        modelo = self._modelos.pop(run_id, "")
        entrada = self._argumentos.pop(run_id, "")
        salida = _texto_de(response)
        prompt_tokens, completion_tokens = _tokens(response)
        self._paso += 1
        costo = _costo(prompt_tokens, completion_tokens, self._precio)
        self._tokens_prompt += prompt_tokens
        self._tokens_completion += completion_tokens
        self._costo += costo
        self._segundos_modelo += segundos
        self._registrador.registrar(
            etapa=self._etapa,
            tipo="modelo",
            modelo=modelo,
            argumentos=entrada,
            salida=salida,
            tokens_prompt=prompt_tokens,
            tokens_completion=completion_tokens,
            costo_usd=costo,
            segundos=segundos,
            orden_id=self._orden_id,
            evento_id=self._evento_id,
            equipo=self._equipo,
            paso=self._paso,
        )
        if self._presupuesto is not None:
            # Si el presupuesto se agotó, la excepción corta el ciclo del
            # agente: es una condición de corte, no un fallo.
            self._presupuesto.consumir_paso(prompt_tokens, completion_tokens)

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id") or "")
        segundos = _segundos(self._inicios.pop(run_id, None))
        self._modelos.pop(run_id, "")
        self._argumentos.pop(run_id, "")
        self._paso += 1
        self._segundos_modelo += segundos
        self._registrador.registrar(
            etapa=self._etapa,
            tipo="modelo",
            segundos=segundos,
            error=str(error),
            orden_id=self._orden_id,
            evento_id=self._evento_id,
            equipo=self._equipo,
            paso=self._paso,
        )
        if self._presupuesto is not None:
            self._presupuesto.consumir_paso()

    # --------------------------------------------------------------- herramienta

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id") or "")
        self._inicios[run_id] = time.monotonic()
        self._modelos[run_id] = str(kwargs.get("name") or serialized.get("name") or "")
        entradas = kwargs.get("inputs")
        self._argumentos[run_id] = entradas if entradas is not None else input_str

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id") or "")
        segundos = _segundos(self._inicios.pop(run_id, None))
        herramienta = self._modelos.pop(run_id, "")
        argumentos = self._argumentos.pop(run_id, None)
        self._paso += 1
        self._registrador.registrar(
            etapa=self._etapa,
            tipo="tool",
            herramienta=herramienta,
            argumentos=argumentos,
            salida=output,
            segundos=segundos,
            orden_id=self._orden_id,
            evento_id=self._evento_id,
            equipo=self._equipo,
            paso=self._paso,
        )

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id") or "")
        segundos = _segundos(self._inicios.pop(run_id, None))
        herramienta = self._modelos.pop(run_id, "")
        argumentos = self._argumentos.pop(run_id, None)
        self._paso += 1
        self._registrador.registrar(
            etapa=self._etapa,
            tipo="tool",
            herramienta=herramienta,
            argumentos=argumentos,
            segundos=segundos,
            error=str(error),
            orden_id=self._orden_id,
            evento_id=self._evento_id,
            equipo=self._equipo,
            paso=self._paso,
        )


def _segundos(inicio: float | None) -> float:
    return 0.0 if inicio is None else time.monotonic() - inicio


def _costo(tokens_prompt: int, tokens_completion: int, precio_por_millon: float) -> float:
    """El costo de una llamada, a partir de los tokens y de la tarifa.

    La tarifa es la del modelo: con el modelo local de la planta es 0 (no hay
    nada que facturar) y con un modelo de nube se configura. El costo **sale
    de los tokens medidos**, nunca de una estimación a ojo.
    """
    if precio_por_millon <= 0:
        return 0.0
    return (tokens_prompt + tokens_completion) * precio_por_millon / 1_000_000


def _generaciones(response: Any) -> list[Any]:
    """Las generaciones de una respuesta, sea cual sea su forma.

    ``ChatResult.generations`` es una lista de generaciones; ``LLMResult``
    (que es lo que llega a los callbacks) es una lista **de listas**. Se
    aplana para no tener que distinguir los dos casos.
    """
    salida: list[Any] = []
    for generacion in getattr(response, "generations", None) or []:
        if isinstance(generacion, (list, tuple)):
            salida.extend(generacion)
        else:
            salida.append(generacion)
    return salida


def _tokens(response: Any) -> tuple[int, int]:
    """Los tokens de entrada y de salida de una respuesta del modelo.

    Ollama los manda en ``llm_output["token_usage"]``; los modelos de la
    familia de LangChain los mandan en el ``usage_metadata`` del mensaje.
    Se leen los dos.
    """
    entrada = salida = 0
    llm_output = getattr(response, "llm_output", None) or {}
    uso = llm_output.get("token_usage") or {}
    if uso:
        entrada = int(uso.get("prompt_eval_count") or uso.get("prompt_tokens") or 0)
        salida = int(uso.get("eval_count") or uso.get("completion_tokens") or 0)
    if not entrada and not salida:
        for generacion in _generaciones(response):
            metadatos = getattr(getattr(generacion, "message", None), "usage_metadata", None) or {}
            entrada += int(metadatos.get("input_tokens") or 0)
            salida += int(metadatos.get("output_tokens") or 0)
    return entrada, salida


def _texto_de(response: Any) -> str:
    """El texto que devolvió el modelo."""
    partes: list[str] = []
    for generacion in _generaciones(response):
        contenido = getattr(getattr(generacion, "message", None), "content", None)
        if contenido:
            partes.append(str(contenido))
    return "\n".join(partes)


def _json(valor: Any) -> str | None:
    """El valor, como JSON que PostgreSQL pueda guardar.

    Los textos largos se recortan **antes** de serializar: así el JSON sigue
    siendo válido (un recorte a mitad de cadena rompería el ``CAST``) y la
    fila no crece sin límite.
    """
    if valor is None:
        return None
    try:
        return json.dumps(_recortar_valor(valor), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps(_recortar(str(valor)), ensure_ascii=False)


def _recortar_valor(valor: Any) -> Any:
    """El valor, con sus textos recortados."""
    if isinstance(valor, str):
        return _recortar(valor)
    if isinstance(valor, dict):
        return {str(clave): _recortar_valor(item) for clave, item in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_recortar_valor(item) for item in valor]
    return valor


def _texto(valor: Any) -> str | None:
    if valor is None:
        return None
    return _recortar(str(valor))


def _recortar(texto: str) -> str:
    return texto if len(texto) <= MAX_CARACTERES else texto[:MAX_CARACTERES] + "…"
