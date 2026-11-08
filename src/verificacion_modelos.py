"""Verificación de la lista cerrada de modelos.

Comprueba dos cosas, y las separa:

1. **La configuración**: que el sistema esté pidiendo un modelo de la lista
   cerrada, con la versión y la configuración fijadas.
2. **El runtime**: que el modelo esté efectivamente disponible donde tiene que
   estar (la imagen del ASR, el cache del embedder, el servidor de Ollama).

Ninguna de las dos comprobadas puede tumbar el sistema: la planta tiene que
seguir funcionando aunque un modelo no esté. Lo que hacen es dejar el
incumplimiento **escrito y visible** en el arranque y en el log, que es lo que
pide la restricción 04 («solo los de la lista cerrada que publicamos»).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import Settings
from modelos_lista import (
    CLAVE_ASR,
    CLAVE_EMBEDDINGS,
    CLAVE_LLM,
    CLAVE_VISION,
    LISTA_CERRADA,
    RUTA_MODELOS,
    ModeloPermitido,
    por_clave,
)

logger = logging.getLogger("modelos.verificacion")

# Estados posibles de una comprobación.
OK = "ok"
FUERA_DE_LISTA = "fuera-de-lista"
NO_DISPONIBLE = "no-disponible"
DESHABILITADO = "deshabilitado"
ADVERTENCIA = "advertencia"


@dataclass(frozen=True)
class Resultado:
    """El resultado de verificar un modelo de la lista."""

    clave: str
    modelo: str
    estado: str
    detalle: str

    @property
    def cumple(self) -> bool:
        return self.estado in (OK, DESHABILITADO)

    @property
    def severidad(self) -> str:
        if self.estado == FUERA_DE_LISTA:
            return "ERROR"
        if self.estado == NO_DISPONIBLE:
            return "WARNING"
        return "INFO"


def verificar_configuracion(settings: Settings) -> list[Resultado]:
    """Que lo configurado sea un modelo de la lista, con su configuración."""
    resultados: list[Resultado] = []

    resultados.append(
        _configuracion_asr(settings, por_clave(CLAVE_ASR))
    )
    resultados.append(
        _configuracion_embeddings(settings, por_clave(CLAVE_EMBEDDINGS))
    )
    resultados.append(
        _configuracion_llm(settings, por_clave(CLAVE_LLM))
    )
    resultados.append(_configuracion_vision(settings))
    return resultados


def _configuracion_asr(settings: Settings, modelo: ModeloPermitido) -> Resultado:
    if not modelo.coincide(settings.asr_modelo):
        return Resultado(
            clave=modelo.clave,
            modelo=modelo.nombre,
            estado=FUERA_DE_LISTA,
            detalle=(
                f"ASR_MODELO={settings.asr_modelo!r} no está en la lista cerrada; "
                f"permitidos: {', '.join(modelo.identificadores)}"
            ),
        )
    if not settings.asr_local_only:
        return Resultado(
            clave=modelo.clave,
            modelo=modelo.nombre,
            estado=ADVERTENCIA,
            detalle=(
                "ASR_LOCAL_ONLY está apagado: el modelo es de la lista, pero "
                "podría descargarlo en runtime. La lista exige que sea local."
            ),
        )
    return Resultado(
        clave=modelo.clave,
        modelo=modelo.nombre,
        estado=OK,
        detalle=f"{settings.asr_modelo} · int8 CPU · {settings.asr_hilos_cpu} hilo(s)",
    )


def _configuracion_embeddings(settings: Settings, modelo: ModeloPermitido) -> Resultado:
    if not modelo.coincide(settings.rag_modelo):
        return Resultado(
            clave=modelo.clave,
            modelo=modelo.nombre,
            estado=FUERA_DE_LISTA,
            detalle=(
                f"RAG_MODELO={settings.rag_modelo!r} no está en la lista cerrada; "
                f"permitidos: {', '.join(modelo.identificadores)}"
            ),
        )
    if settings.rag_dimension != modelo.dimension:
        return Resultado(
            clave=modelo.clave,
            modelo=modelo.nombre,
            estado=FUERA_DE_LISTA,
            detalle=(
                f"RAG_DIMENSION={settings.rag_dimension} no es la del modelo de la "
                f"lista ({modelo.dimension}); el índice pgvector quedaría incompatible"
            ),
        )
    return Resultado(
        clave=modelo.clave,
        modelo=modelo.nombre,
        estado=OK,
        detalle=f"{settings.rag_modelo} · {settings.rag_dimension} dims",
    )


def _configuracion_llm(settings: Settings, modelo: ModeloPermitido) -> Resultado:
    if not settings.llm_activo:
        return Resultado(
            clave=modelo.clave,
            modelo=modelo.nombre,
            estado=DESHABILITADO,
            detalle=(
                "LLM_ACTIVO está apagado: el sistema redacta con el texto "
                "determinista. No depende del modelo para funcionar."
            ),
        )
    if not modelo.coincide(settings.ollama_modelo):
        return Resultado(
            clave=modelo.clave,
            modelo=modelo.nombre,
            estado=FUERA_DE_LISTA,
            detalle=(
                f"OLLAMA_MODELO={settings.ollama_modelo!r} no está en la lista cerrada; "
                f"permitido: {', '.join(modelo.identificadores)}"
            ),
        )
    return Resultado(
        clave=modelo.clave,
        modelo=modelo.nombre,
        estado=OK,
        detalle=(
            f"{settings.ollama_modelo} · {modelo.cuantizacion} · ctx {settings.ollama_num_ctx} "
            f"· {settings.ollama_num_thread} hilo(s) · timeout {settings.ollama_timeout_s:g} s"
        ),
    )


def _configuracion_vision(settings: Settings) -> Resultado:
    """El modelo de nube con visión: qué se configuró y qué se puede usar.

    La clave **nunca** se imprime: acá sólo se dice si hay una o no.

    El modelo de la lista cerrada es el Nemotron con visión vigente
    (``nvidia/nemotron-3-nano-omni-30b-a3b-reasoning``): el que la lista
    publicada dejó sin identificar fue retirado por NVIDIA (HTTP 410 Gone).
    Cualquier otro ``NVIDIA_MODELO`` queda **fuera de la lista cerrada** y se
    escribe en el log; con la clave puesta, el sistema **no** llama a la nube.
    """
    modelo = por_clave(CLAVE_VISION)
    if not settings.nvidia_modelo:
        if settings.nvidia_api_key:
            return Resultado(
                clave=CLAVE_VISION,
                modelo=modelo.nombre,
                estado=ADVERTENCIA,
                detalle=(
                    "hay NVIDIA_API_KEY pero no NVIDIA_MODELO: la clave no se usa "
                    "hasta que se configure el modelo de visión de la lista"
                ),
            )
        return Resultado(
            clave=CLAVE_VISION,
            modelo=modelo.nombre,
            estado=DESHABILITADO,
            detalle=modelo.nota,
        )
    if not modelo.coincide(settings.nvidia_modelo):
        return Resultado(
            clave=CLAVE_VISION,
            modelo=modelo.nombre,
            estado=FUERA_DE_LISTA,
            detalle=(
                f"NVIDIA_MODELO={settings.nvidia_modelo!r} no está en la lista "
                "cerrada: el modelo de visión tiene que ser el de la lista"
            ),
        )
    if not modelo.habilitado:
        return Resultado(
            clave=CLAVE_VISION,
            modelo=modelo.nombre,
            estado=DESHABILITADO,
            detalle="el modelo de visión está deshabilitado en la lista cerrada",
        )
    return Resultado(
        clave=CLAVE_VISION,
        modelo=modelo.nombre,
        estado=OK,
        detalle=(
            f"{settings.nvidia_modelo} · API de NVIDIA · "
            f"clave {'configurada' if settings.nvidia_api_key else 'ausente'}"
        ),
    )


def verificar_runtime(settings: Settings, cliente: Any = None) -> list[Resultado]:
    """Que los modelos de la lista estén donde tienen que estar.

    ``cliente`` es un cliente HTTP de ``httpx`` (o ``None``, y se abre uno
    propio). Si un modelo no está disponible se informa, no se corta el
    arranque: el sistema tiene que funcionar igual.
    """
    resultados: list[Resultado] = [
        _runtime_asr(settings),
        _runtime_embeddings(settings),
    ]
    resultados.append(_runtime_llm(settings, cliente))
    return resultados


def _runtime_asr(settings: Settings) -> Resultado:
    modelo = por_clave(CLAVE_ASR)
    raiz = settings.asr_download_root
    if not raiz:
        return Resultado(
            clave=modelo.clave,
            modelo=modelo.nombre,
            estado=NO_DISPONIBLE,
            detalle="ASR_DOWNLOAD_ROOT no está configurado: no se puede comprobar el modelo",
        )
    ruta = Path(raiz) / RUTA_MODELOS[CLAVE_ASR]
    if not ruta.exists():
        return Resultado(
            clave=modelo.clave,
            modelo=modelo.nombre,
            estado=NO_DISPONIBLE,
            detalle=f"el modelo no está descargado en {ruta}",
        )
    return Resultado(
        clave=modelo.clave,
        modelo=modelo.nombre,
        estado=OK,
        detalle=f"descargado en {ruta}",
    )


def _runtime_embeddings(settings: Settings) -> Resultado:
    modelo = por_clave(CLAVE_EMBEDDINGS)
    raiz = settings.rag_cache_dir
    if not raiz:
        return Resultado(
            clave=modelo.clave,
            modelo=modelo.nombre,
            estado=NO_DISPONIBLE,
            detalle="RAG_CACHE_DIR no está configurado: no se puede comprobar el modelo",
        )
    ruta = Path(raiz) / RUTA_MODELOS[CLAVE_EMBEDDINGS]
    if not ruta.exists():
        return Resultado(
            clave=modelo.clave,
            modelo=modelo.nombre,
            estado=NO_DISPONIBLE,
            detalle=f"el modelo no está descargado en {ruta}",
        )
    return Resultado(
        clave=modelo.clave,
        modelo=modelo.nombre,
        estado=OK,
        detalle=f"descargado en {ruta}",
    )


def _runtime_llm(settings: Settings, cliente: Any = None) -> Resultado:
    modelo = por_clave(CLAVE_LLM)
    if not settings.llm_activo:
        return Resultado(
            clave=modelo.clave,
            modelo=modelo.nombre,
            estado=DESHABILITADO,
            detalle="LLM_ACTIVO está apagado",
        )
    url = settings.ollama_url.rstrip("/")
    try:
        if cliente is None:
            import httpx

            with httpx.Client(
                timeout=settings.ollama_timeout_s,
                trust_env=False,
                proxy=getattr(settings, "ollama_proxy", None) or None,
            ) as propio:
                detalle = _consultar_ollama(propio, url, settings.ollama_modelo, modelo)
        else:
            detalle = _consultar_ollama(cliente, url, settings.ollama_modelo, modelo)
    except Exception as exc:  # noqa: BLE001 - que no responda no es motivo para parar
        return Resultado(
            clave=modelo.clave,
            modelo=modelo.nombre,
            estado=NO_DISPONIBLE,
            detalle=f"Ollama no responde en {url}: {exc}",
        )
    return Resultado(
        clave=modelo.clave,
        modelo=modelo.nombre,
        estado=OK,
        detalle=detalle,
    )


def _consultar_ollama(
    cliente: Any,
    url: str,
    nombre: str,
    modelo: ModeloPermitido,
) -> str:
    """El modelo está en el servidor y es el de la lista, con su cuantización."""
    respuesta = cliente.get(f"{url}/api/tags")
    respuesta.raise_for_status()
    datos = respuesta.json()
    nombres = {
        str(item.get("name", "")).lower(): item
        for item in (datos.get("models") or [])
        if isinstance(item, dict)
    }
    if nombre.lower() not in nombres:
        disponibles = ", ".join(sorted(nombres)) or "ninguno"
        raise RuntimeError(
            f"el modelo {nombre} no está en Ollama (disponibles: {disponibles})"
        )
    detalle = cliente.post(f"{url}/api/show", json={"model": nombre})
    detalle.raise_for_status()
    info = detalle.json()
    cuantizacion = str((info.get("details") or {}).get("quantization_level") or "")
    if modelo.cuantizacion and cuantizacion.upper() != modelo.cuantizacion.upper():
        raise RuntimeError(
            f"la cuantización de {nombre} es {cuantizacion or 'desconocida'} "
            f"y la lista fija {modelo.cuantizacion}"
        )
    parametros = str((info.get("details") or {}).get("parameter_size") or "?")
    return f"{nombre} · {cuantizacion or '?'} · {parametros} · servidor {url}"


def registrar(resultados: list[Resultado]) -> None:
    """Deja la verificación escrita en el log."""
    for resultado in resultados:
        mensaje = (
            f"{resultado.clave} | {resultado.modelo} | "
            f"{resultado.estado} | {resultado.detalle}"
        )
        if resultado.severidad == "ERROR":
            logger.error("Lista cerrada de modelos: %s", mensaje)
        elif resultado.severidad == "WARNING":
            logger.warning("Lista cerrada de modelos: %s", mensaje)
        else:
            logger.info("Lista cerrada de modelos: %s", mensaje)


def verificar(settings: Settings, cliente: Any = None) -> list[Resultado]:
    """Verificación completa: configuración y runtime."""
    resultados = verificar_configuracion(settings) + verificar_runtime(settings, cliente)
    registrar(resultados)
    return resultados


def fuera_de_lista(resultados: list[Resultado]) -> list[Resultado]:
    """Los que están fuera de la lista cerrada (o sea, un incumplimiento)."""
    return [resultado for resultado in resultados if resultado.estado == FUERA_DE_LISTA]


def resumen(resultados: list[Resultado]) -> str:
    """La tabla de la lista, para el log y para la documentación."""
    lineas = []
    for modelo in LISTA_CERRADA:
        propios = [r for r in resultados if r.clave == modelo.clave]
        estado = propios[0].estado if propios else "sin verificar"
        lineas.append(f"  {modelo.clave:10s} {modelo.nombre:42s} {estado}")
    return "\n".join(lineas)
