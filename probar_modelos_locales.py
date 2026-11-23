"""Script para probar modelos locales de Ollama y medir su comportamiento.

Mide: tiempo de respuesta, si soporta tool calls nativos, calidad de clasificación.
"""

from __future__ import annotations

import json
import time
from urllib import request
from urllib.error import URLError

OLLAMA_URL = "http://localhost:11434/api/chat"

MODELOS_A_PROBAR = [
    "qwen2.5:0.5b-instruct",
    "qwen2.5:3b-instruct",
    "phi3:latest",
    "phi4-mini:latest",
    "qwen3:4b",
    "llama3.2:3b",
]

PROMPT_CLASIFICACION = (
    "Clasifica este mensaje como 'simple' o 'complejo'. "
    'Responde SOLO con JSON: {"clasificacion": "simple" o "complejo"}. '
    "Mensaje: ¿Hasta qué hora abren el domingo?"
)

PROMPT_TOOLS = (
    "Necesito consultar el historial de sensores del equipo B-101. "
    "Usa la herramienta adecuada para obtener los datos. "
    "Responde SOLO con JSON: {\"tool\": \"nombre_de_la_tool\", \"argumentos\": {}}. "
    "Tools disponibles: busqueda_rag, historial_sensores, historial_ordenes, inventario, lista_materiales, catalogo_tareas, busqueda_manuales, equipos."
)


def probar_modelo(modelo: str, prompt: str, usar_tools: bool = False) -> dict:
    """Prueba un modelo y devuelve tiempo, respuesta y si usa tool calls nativos."""
    cuerpo = {
        "model": modelo,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0, "num_predict": 200},
    }
    if usar_tools:
        cuerpo["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": "historial_sensores",
                    "description": "Obtiene historial de sensores",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "tag": {"type": "string"},
                            "variable": {"type": "string"},
                        },
                        "required": ["tag", "variable"],
                    },
                },
            }
        ]
        cuerpo["tool_choice"] = "required"

    inicio = time.monotonic()
    try:
        req = request.Request(
            OLLAMA_URL,
            data=json.dumps(cuerpo).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(req, timeout=120) as resp:
            datos = json.loads(resp.read().decode("utf-8"))
        tiempo = time.monotonic() - inicio
        mensaje = datos.get("message", {})
        contenido = mensaje.get("content", "") or ""
        tool_calls = mensaje.get("tool_calls", [])
        return {
            "modelo": modelo,
            "tiempo_s": round(tiempo, 2),
            "contenido": contenido[:200],
            "tool_calls_nativos": len(tool_calls) > 0,
            "detalle_tool_calls": tool_calls,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "modelo": modelo,
            "tiempo_s": None,
            "contenido": "",
            "tool_calls_nativos": False,
            "detalle_tool_calls": [],
            "error": str(exc)[:200],
        }


def main() -> None:
    print("=" * 80)
    print("PRUEBA DE MODELOS LOCALES DE OLLAMA")
    print("=" * 80)
    print()

    for modelo in MODELOS_A_PROBAR:
        print(f"--- {modelo} ---")
        resultado = probar_modelo(modelo, PROMPT_CLASIFICACION)
        if resultado["error"]:
            print(f"  ERROR: {resultado['error']}")
        else:
            print(f"  Tiempo: {resultado['tiempo_s']} s")
            print(f"  Respuesta: {resultado['contenido']}")
            print(f"  Tool calls nativos: {resultado['tool_calls_nativos']}")

        print()
        resultado_tools = probar_modelo(modelo, PROMPT_TOOLS, usar_tools=True)
        if resultado_tools["error"]:
            print(f"  ERROR (tools): {resultado_tools['error']}")
        else:
            print(f"  Tiempo (tools): {resultado_tools['tiempo_s']} s")
            print(f"  Tool calls nativos: {resultado_tools['tool_calls_nativos']}")
            if resultado_tools["detalle_tool_calls"]:
                for tc in resultado_tools["detalle_tool_calls"]:
                    print(f"    - {tc.get('function', {}).get('nombre', tc)}")
        print()


if __name__ == "__main__":
    main()
