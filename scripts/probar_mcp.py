"""Prueba en vivo del servidor MCP de PIA-3 contra la planta simulada.

Conecta un cliente MCP por streamable-http al servidor dockerizado, lista las
tools y llama algunas contra la API real de la planta.

    python scripts/probar_mcp.py [--url http://localhost:58000/mcp]
"""

from __future__ import annotations

import argparse
import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:58000/mcp")
    args = parser.parse_args()

    async with streamablehttp_client(args.url) as (leer, escribir, _):
        async with ClientSession(leer, escribir) as sesion:
            await sesion.initialize()

            herramientas = await sesion.list_tools()
            print(f"TOOLS ({len(herramientas.tools)}):")
            for herramienta in herramientas.tools:
                estricto = herramienta.inputSchema.get("additionalProperties")
                print(f"  - {herramienta.name}  (additionalProperties={estricto})")

            consultas = [
                ("equipos", {"tag": "B-101"}),
                (
                    "historial_sensores",
                    {"tag": "B-101", "variable": "presion_descarga", "limite": 5},
                ),
                ("historial_ordenes", {"equipo": "B-101", "limite": 3}),
                ("inventario", {"codigo": "TX-EMP-KIT"}),
                ("lista_materiales", {"equipo": "B-101"}),
                ("catalogo_tareas", {"equipo": "B-101"}),
                ("busqueda_manuales", {"consulta": "proyeccion de agua a presion", "top_k": 3}),
                ("busqueda_rag", {"consulta": "proyeccion de agua a presion", "top_k": 3}),
                ("busqueda_rag", {"consulta": "valor de vibracion de disparo", "top_k": 3}),
            ]

            for nombre, argumentos in consultas:
                resultado = await sesion.call_tool(nombre, argumentos)
                texto = "".join(
                    bloque.text for bloque in resultado.content if hasattr(bloque, "text")
                )
                estructurado = getattr(resultado, "structuredContent", None)
                estado = "ok" if not resultado.isError else "ERROR"
                print(f"\n=== {nombre} [{estado}] ===")
                print(texto[:700])
                if estructurado is not None:
                    resumen = json.dumps(estructurado, ensure_ascii=False)[:400]
                    print(f"--- structuredContent: {resumen}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
