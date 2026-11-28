#!/usr/bin/env python3
"""
Servicio de router con escalamiento (Entrega 3, módulos 4.1 y 4.2).

Expone el router como un servicio invocable que:
1. Recibe un pedido (tipo de tarea, datos, imagen opcional).
2. Filtra modelos por capacidad.
3. Aplica reglas de routing (visión → nube, clasificar/extraer → económico, razonar → generalista).
4. Si hay varios aptos, usa clasificación de complejidad para elegir económico vs generalista.
5. Si no hay enlace, usa fallback local (solo modelos de planta).
6. Registra métricas de latencia, costo y resolución.
7. Devuelve la respuesta del modelo elegido.

Uso:
    python src/routing/servicio_router.py --modelo qwen2.5:0.5b-instruct --puerto 8300
"""

from __future__ import annotations

import argparse
import json
import os
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any

from .router import Router, MODELO_LLM, MODELO_VISION, PREFERENCIA_DEFECTO
from .pedido import Pedido
from .capacidad import capacidad_filtrada, CAPACIDADES, MODELO_ECONOMICO
from .clasificador import etiquetar_complejidad, modelo_por_complejidad
from .latency import HistorialLatencia, proveedor_por_latencia
from .cascada import Cascada, ResultadoCascada, MODELO_ECONOMICO as C_ECONOMICO, MODELO_GENERALISTA as C_GENERALISTA
from .costos import tabla_costos, PrecioModelo
from .metricas import registro_metricas_router, MedicionRouter
from .metricas_tokens import registrar_metrica_tokens
from .validador import valida_no_vacio, valida_json

#: Claves de modelo de la lista cerrada usadas por el servicio.
MODELO_ECONOMICO_SVC = "economico"  # Qwen2.5-0.5B-Instruct
MODELO_GENERALISTA_SVC = "llm"  # Qwen2.5-3B-Instruct

#: URL de Ollama local.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

#: URL de la API de NVIDIA para visión.
NVIDIA_URL = os.environ.get("NVIDIA_URL", "https://integrate.api.nvidia.com/v1")
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")

#: ¿Está disponible el enlace a la nube?
ENLACE_DISPONIBLE = bool(NVIDIA_API_KEY)

#: Historial de latencia del router.
_historial_latencia = HistorialLatencia()

#: Router con clasificador y latencia habilitados.
_router = Router(usar_clasificador=True, usar_latencia=True, historial=_historial_latencia)

#: Gestor de cascada para escalamiento económico → generalista.
_cascada = None  # Se inicializa en main()


def _consultar_ollama(modelo: str, prompt: str, timeout_s: int = 120) -> tuple[str, float, int, int]:
    """Llama a Ollama y devuelve (respuesta, segundos, tokens_entrada, tokens_salida)."""
    import httpx

    url = f"{OLLAMA_URL}/api/generate"
    payload = {
        "model": modelo,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0,
            "num_ctx": 4096,
            "num_thread": 2,
            "seed": 42,
        },
    }
    inicio = time.time()
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(url, json=payload)
        resp.raise_for_status()
        datos = resp.json()
        segundos = time.time() - inicio
        return (
            datos.get("response", ""),
            segundos,
            datos.get("prompt_eval_count", 0),
            datos.get("eval_count", 0),
        )
    except Exception as exc:
        segundos = time.time() - inicio
        return f"ERROR: {exc}", segundos, 0, 0


def _consultar_nvidia_vision(modelo: str, imagen_url: str, prompt: str, timeout_s: int = 30) -> tuple[str, float, int, int]:
    """Llama a la API de NVIDIA para visión."""
    import httpx

    url = f"{NVIDIA_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": modelo,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": imagen_url}},
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": 800,
    }
    inicio = time.time()
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        datos = resp.json()
        segundos = time.time() - inicio
        respuesta = datos.get("choices", [{}])[0].get("message", {}).get("content", "")
        uso = datos.get("usage", {})
        return respuesta, segundos, uso.get("prompt_tokens", 0), uso.get("completion_tokens", 0)
    except Exception as exc:
        segundos = time.time() - inicio
        return f"ERROR: {exc}", segundos, 0, 0


def _costo_usd(clave_modelo: str, tokens_entrada: int, tokens_salida: int) -> float:
    """Calcula el costo USD de una llamada según la tabla de costos."""
    try:
        return tabla_costos.costo_usd(
            clave=clave_modelo,
            tokens_entrada=tokens_entrada,
            tokens_salida=tokens_salida,
        )
    except KeyError:
        return 0.0


def _resolver_modelo(pedido: Pedido, enlace_disponible: bool = ENLACE_DISPONIBLE) -> str:
    """Resuelve qué modelo debe atender el pedido, con escalamiento y fallback sin enlace."""
    # 1. Filtrar por capacidad.
    aptos = capacidad_filtrada(pedido)
    if not aptos:
        return MODELO_LLM

    # 2. Si no hay enlace, solo modelos locales (planta).
    if not enlace_disponible:
        locales = [m for m in aptos if CAPACIDADES[m].get("ubicacion") == "planta" or m in (MODELO_LLM, MODELO_ECONOMICO_SVC)]
        if locales:
            return locales[0]
        return MODELO_LLM

    # 3. Reglas: visión → nube, clasificar/extraer → económico, razonar → generalista.
    modelo_reglas = None
    if pedido.necesita_vision:
        modelo_reglas = MODELO_VISION
    elif pedido.tipo_tarea in ("clasificar", "extraer"):
        modelo_reglas = MODELO_ECONOMICO_SVC
    elif pedido.tipo_tarea == "razonar":
        modelo_reglas = MODELO_GENERALISTA_SVC

    if modelo_reglas and modelo_reglas in aptos:
        return modelo_reglas

    # 4. Clasificación de complejidad para elegir económico vs generalista.
    if len(aptos) > 1:
        resultado = etiquetar_complejidad(pedido)
        modelo_clase = modelo_por_complejidad(resultado.complejidad)
        if modelo_clase in aptos:
            return modelo_clase

    # 5. Latencia: el más rápido recientemente.
    if _historial_latencia:
        return proveedor_por_latencia(aptos, _historial_latencia)

    # 6. Preferencia por defecto.
    for modelo in PREFERENCIA_DEFECTO:
        if modelo in aptos:
            return modelo

    return aptos[0]


def _atender_pedido(pedido: Pedido, datos: Any, imagen_url: str | None = None) -> dict[str, Any]:
    """Atiende un pedido completo: elige modelo, consulta, registra métricas."""
    modelo_elegido = _resolver_modelo(pedido)
    inicio = time.time()
    exito = False
    respuesta = ""
    tokens_entrada = 0
    tokens_salida = 0
    motivo = ""
    estrategia = "reglas"
    llamadas = 1

    # Determinar clave de modelo para costos (mapear claves de routing a claves de costos)
    clave_costo = modelo_elegido  # "llm", "vision", "economico", "asr", "embeddings"

    try:
        if modelo_elegido == MODELO_VISION and imagen_url and ENLACE_DISPONIBLE:
            # Visión: llamar a NVIDIA API.
            prompt = datos if isinstance(datos, str) else json.dumps(datos, ensure_ascii=False)
            respuesta, segundos, tokens_entrada, tokens_salida = _consultar_nvidia_vision(
                MODELO_VISION, imagen_url, prompt
            )
            exito = not respuesta.startswith("ERROR")
            motivo = f"visión nube ({MODELO_VISION})"
            estrategia = "reglas"
        elif modelo_elegido in (MODELO_LLM, MODELO_ECONOMICO_SVC):
            # Lenguaje: llamar a Ollama.
            prompt = datos if isinstance(datos, str) else json.dumps(datos, ensure_ascii=False)
            # Mapear clave de routing a nombre real de Ollama
            nombre_ollama = (
                "qwen2.5:0.5b-instruct" if modelo_elegido == MODELO_ECONOMICO_SVC else "qwen2.5:3b-instruct"
            )
            respuesta, segundos, tokens_entrada, tokens_salida = _consultar_ollama(nombre_ollama, prompt)
            exito = not respuesta.startswith("ERROR")
            motivo = f"llm local ({nombre_ollama})"
            estrategia = "reglas"
        else:
            respuesta = "ERROR: modelo no soportado o enlace no disponible"
            segundos = time.time() - inicio
            exito = False
            motivo = "modelo no soportado"
    except Exception as exc:
        segundos = time.time() - inicio
        respuesta = f"ERROR: {exc}"
        exito = False
        motivo = str(exc)

    # Registrar latencia.
    _historial_latencia.registrar(modelo_elegido, segundos)

    # Registrar métrica de tokens por segundo.
    registrar_metrica_tokens(
        paso=0,
        modelo=modelo_elegido,
        tokens_prompt=tokens_entrada,
        tokens_completion=tokens_salida,
        segundos_prompt=segundos / 2 if segundos > 0 else 0.0,
        segundos_completion=segundos / 2 if segundos > 0 else 0.0,
    )

    # Registrar métrica del router.
    costo_usd = _costo_usd(clave_costo, tokens_entrada, tokens_salida)
    registro_metricas_router.registrar(
        pedido_id=getattr(pedido, "tipo_tarea", "desconocido"),
        modelo_elegido=modelo_elegido,
        motivo=motivo,
        costo_usd=costo_usd,
        resuelto=exito,
        estrategia=estrategia,
        llamadas=llamadas,
    )

    return {
        "modelo_elegido": modelo_elegido,
        "respuesta": respuesta,
        "exito": exito,
        "segundos": round(segundos, 3),
        "tokens_entrada": tokens_entrada,
        "tokens_salida": tokens_salida,
        "costo_usd": costo_usd,
        "motivo": motivo,
        "estrategia": estrategia,
        "llamadas": llamadas,
    }


class Handler(BaseHTTPRequestHandler):
    """Handler HTTP para el servicio de router."""

    def log_message(self, formato, *args):
        """Silenciar logs de acceso por defecto."""
        pass

    def do_POST(self):
        """Recibe un pedido de routing y devuelve la respuesta del modelo."""
        try:
            longitud = int(self.headers.get("Content-Length", 0))
            cuerpo = self.rfile.read(longitud)
            solicitud = json.loads(cuerpo.decode("utf-8"))

            tipo_tarea = solicitud.get("tipo_tarea", "chat")
            datos = solicitud.get("datos", "")
            imagen_url = solicitud.get("imagen_url")
            tiene_imagen = solicitud.get("tiene_imagen", False)
            tokens_estimados = solicitud.get("tokens_estimados", 0)
            requiere_tools = solicitud.get("requiere_tools", False)

            pedido = Pedido(
                tipo_tarea=tipo_tarea,
                tiene_imagen=tiene_imagen,
                tokens_estimados=tokens_estimados,
                requiere_tools=requiere_tools,
            )

            resultado = _atender_pedido(pedido, datos, imagen_url)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(resultado, ensure_ascii=False).encode("utf-8"))
        except Exception as exc:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({"error": str(exc), "exito": False}, ensure_ascii=False).encode("utf-8")
            )

    def do_GET(self):
        """Devuelve métricas del router y endpoints de salud."""
        if self.path == "/metricas":
            resumen = registro_metricas_router.resumen()
            velocidad = metricas_tokens.resumen_velocidad() if (metricas_tokens := __import__("src.routing.metricas_tokens", fromlist=["resumen_velocidad"])) else {}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({"router": resumen, "velocidad": velocidad}, ensure_ascii=False).encode("utf-8")
            )
        elif self.path == "/health/live":
            # Liveness: el proceso está vivo.
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "alive"}, ensure_ascii=False).encode("utf-8"))
        elif self.path == "/health/ready":
            # Readiness: el router puede atender pedidos (Ollama disponible o modo local).
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({"status": "ready", "enlace": ENLACE_DISPONIBLE}, ensure_ascii=False).encode("utf-8")
            )
        else:
            self.send_response(404)
            self.end_headers()


def main() -> None:
    parser = argparse.ArgumentParser(description="Servicio de router con escalamiento (Entrega 3)")
    parser.add_argument("--puerto", type=int, default=8300, help="Puerto del servidor HTTP")
    parser.add_argument("--host", default="0.0.0.0", help="Host del servidor HTTP")
    args = parser.parse_args()

    servidor = HTTPServer((args.host, args.puerto), Handler)
    print(f"Servicio de router escuchando en {args.host}:{args.puerto}")
    print(f"Modelo económico: qwen2.5:0.5b-instruct (planta)")
    print(f"Modelo generalista: qwen2.5:3b-instruct (planta)")
    print(f"Modelo de visión: nvidia/nemotron-3-nano-omni-30b-a3b-reasoning (nube, {'disponible' if ENLACE_DISPONIBLE else 'sin clave'})")
    servidor.serve_forever()


if __name__ == "__main__":
    main()
