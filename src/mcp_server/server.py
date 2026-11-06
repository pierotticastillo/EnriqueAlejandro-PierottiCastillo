"""Servidor MCP de PIA-3: expone las tools de solo lectura de la planta.

El servidor habla MCP (Model Context Protocol) sobre HTTP *streamable* o
*stdio*. Cada tool del registro queda expuesta con su descripción y su modelo
de entrada estricto, de modo que un cliente MCP puede listarlas y llamarlas.

    python -m mcp_server                       # stdio
    MCP_TRANSPORTE=streamable-http python -m mcp_server
"""

from __future__ import annotations

import logging
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from tools.cliente import ClientePlanta, ErrorHerramienta
from tools.firma import armar_llamador
from tools.manuales import CorpusManuales
from tools.registro import REGISTRO

logger = logging.getLogger("mcp.server")

INSTRUCCIONES = (
    "Tools de solo lectura de la planta de inyección PIA-3. Los límites de alarma "
    "y el stock salen de la planta y del manual de buenas prácticas (MBP-4), no del "
    "modelo. Toda afirmación sobre un manual se cita con su sección."
)


def crear_servidor(cliente: ClientePlanta | None = None) -> FastMCP:
    """Arma el servidor MCP con todas las tools del registro."""
    api_url = os.environ.get("PLANTA_API_URL", "http://sim:8100")
    timeout_s = float(os.environ.get("PLANTA_API_TIMEOUT_S", "10"))
    reintentos = int(os.environ.get("PLANTA_API_REINTENTOS", "2"))
    cliente = cliente or ClientePlanta(api_url, timeout_s=timeout_s, reintentos=reintentos)
    corpus = CorpusManuales(cliente)

    servidor = FastMCP(
        os.environ.get("MCP_NOMBRE", "pia3"),
        instructions=INSTRUCCIONES,
        host=os.environ.get("MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("MCP_PUERTO", "8000")),
    )

    for nombre, tool in REGISTRO.items():
        _exponer(servidor, nombre, tool, cliente, corpus)
    return servidor


def _exponer(servidor: FastMCP, nombre: str, tool: Any, cliente: Any, corpus: Any) -> None:
    """Registra una tool en el servidor MCP."""
    entrada = tool.entrada
    usa_corpus = tool.nombre in {
        "inventario",
        "lista_materiales",
        "catalogo_tareas",
        "busqueda_manuales",
    }

    def _correr(consulta: Any) -> Any:
        try:
            if usa_corpus:
                return tool.funcion(cliente, consulta, corpus=corpus)
            return tool.funcion(cliente, consulta)
        except ErrorHerramienta:
            raise
        except Exception as exc:  # noqa: BLE001 - la tool no debe tumbar el servidor
            raise ErrorHerramienta(
                f"La tool {nombre} falló: {exc}", herramienta=nombre
            ) from exc

    llamador = armar_llamador(entrada, _correr, tool.salida)
    llamador.__name__ = nombre
    llamador.__doc__ = tool.descripcion
    servidor.add_tool(
        llamador,
        name=nombre,
        title=tool.descripcion,
        description=tool.descripcion,
        structured_output=True,
    )
    _esquema_estricto(servidor, nombre)


def _esquema_estricto(servidor: FastMCP, nombre: str) -> None:
    """El esquema MCP declara que no se aceptan campos extra."""
    herramienta = servidor._tool_manager.get_tool(nombre)
    if herramienta is not None and herramienta.parameters is not None:
        herramienta.parameters["additionalProperties"] = False


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    transporte = os.environ.get("MCP_TRANSPORTE", "stdio")
    servidor = crear_servidor()
    logger.info(
        "Servidor MCP PIA-3 con %d tools; transporte=%s", len(REGISTRO), transporte
    )
    if transporte == "streamable-http":
        servidor.run(transport="streamable-http")
    else:
        servidor.run(transport="stdio")


if __name__ == "__main__":
    main()
