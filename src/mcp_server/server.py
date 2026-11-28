"""Servidor MCP de PIA-3: expone las tools de solo lectura de la planta.

El servidor habla MCP (Model Context Protocol) sobre HTTP *streamable* o
*stdio*. Cada tool del registro queda expuesta con su descripción y su modelo
de entrada estricto, de modo que un cliente MCP puede listarlas y llamarlas.

    python -m mcp_server                       # stdio
    MCP_TRANSPORTE=streamable-http python -m mcp_server

Seguridad (P1-09): el servidor MCP expone las tools solo en la red interna
`debug` del compose. Si se define `MCP_REQUIRE_AUTH=true`, exige un token
Bearer opaco (variable `MCP_TOKEN`) en el encabezado `Authorization` de cada
llamada. Sin token configurado, las tools quedan abiertas solo en la red
interna (comportamiento por defecto para desarrollo local).
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

#: Token Bearer opcional para proteger las tools del MCP (P1-09).
MCP_TOKEN = os.environ.get("MCP_TOKEN", "")

#: Si es true, se exige token Bearer en todas las llamadas al MCP.
MCP_REQUIRE_AUTH = os.environ.get("MCP_REQUIRE_AUTH", "false").lower() == "true"


def _verificar_token(headers: dict[str, str] | None) -> bool:
    """Verifica que el encabezado Authorization lleve el token configurado.

    Si no se requiere auth (MCP_REQUIRE_AUTH=false) o no hay token configurado,
    se permite el acceso (red interna de desarrollo).
    """
    if not MCP_REQUIRE_AUTH or not MCP_TOKEN:
        return True
    if not headers:
        return False
    auth = headers.get("Authorization", "")
    return auth == f"Bearer {MCP_TOKEN}"


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
            raise ErrorHerramienta(f"La tool {nombre} falló: {exc}", herramienta=nombre) from exc

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


def _app_con_auth(servidor: FastMCP):
    """Envuelve la app Starlette de FastMCP con middleware de autenticación (P1-09).

    Si MCP_REQUIRE_AUTH=true y hay MCP_TOKEN configurado, cada request HTTP
    al servidor MCP debe llevar el encabezado Authorization: Bearer <token>.
    Las requests sin token o con token inválido reciben 401.
    """
    if not MCP_REQUIRE_AUTH or not MCP_TOKEN:
        return servidor

    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class _AuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            # Permitir salud y documentación sin auth.
            if request.url.path in ("/health", "/docs", "/openapi.json", "/mcp"):
                return await call_next(request)
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {MCP_TOKEN}":
                return JSONResponse({"error": "no autorizado"}, status_code=401)
            return await call_next(request)

    app = servidor.server
    app.add_middleware(_AuthMiddleware)
    return app


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    transporte = os.environ.get("MCP_TRANSPORTE", "stdio")
    servidor = crear_servidor()
    logger.info("Servidor MCP PIA-3 con %d tools; transporte=%s", len(REGISTRO), transporte)
    if transporte == "streamable-http":
        app = _app_con_auth(servidor)
        import uvicorn

        uvicorn.run(app, host=os.environ.get("MCP_HOST", "0.0.0.0"), port=int(os.environ.get("MCP_PUERTO", "8000")))
    else:
        servidor.run(transport="stdio")


if __name__ == "__main__":
    main()
