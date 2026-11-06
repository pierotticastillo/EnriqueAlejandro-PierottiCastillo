"""Tests del servidor MCP: expone las tools y responde a una llamada."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from mcp_server.server import crear_servidor
from tools.registro import REGISTRO


class ClienteFalso:
    """Cliente de planta falso para el servidor MCP."""

    def get(self, ruta: str, *, params: dict[str, Any] | None = None) -> Any:
        if ruta == "/api/planta":
            return {
                "equipos": [
                    {
                        "tag": "B-101",
                        "tipo": "bomba",
                        "nombres": ["la uno"],
                        "manometro_local": {"unidad": "kg/cm2"},
                    }
                ],
                "sensores": [],
            }
        if ruta == "/api/documentos":
            return {"documentos": [{"id": "MBP-4", "titulo": "Manual", "vigente": True}]}
        if ruta.endswith("/secciones"):
            return [
                {
                    "id": "MBP-4 §13.1",
                    "numero": "13.1",
                    "titulo": "Tareas estándar",
                    "vigente": True,
                    "texto": (
                        "T-B-EMP | Cambio de empaquetadura | B-101 | Sí | 4 | "
                        "propio | - | USD 0"
                    ),
                }
            ]
        return []


def _servidor():
    return crear_servidor(ClienteFalso())  # type: ignore[arg-type]


def test_el_servidor_expone_las_tools_del_registro() -> None:
    servidor = _servidor()
    assert len(servidor._tool_manager.list_tools()) == len(REGISTRO)
    nombres = {tool.name for tool in servidor._tool_manager.list_tools()}
    assert "equipos" in nombres
    assert "busqueda_manuales" in nombres


def test_el_esquema_de_una_tool_es_estricto() -> None:
    servidor = _servidor()
    herramienta = next(
        tool for tool in servidor._tool_manager.list_tools() if tool.name == "equipos"
    )
    esquema = herramienta.parameters
    assert esquema.get("additionalProperties") is False
    assert "tag" in esquema["properties"]


def test_una_tool_se_puede_llamar_por_mcp() -> None:
    servidor = _servidor()
    resultado = asyncio.run(servidor.call_tool("equipos", {"tag": "B-101"}))
    contenido = resultado[0] if isinstance(resultado, tuple) else resultado
    texto = str(contenido)
    assert "B-101" in texto


def test_la_salida_estructurada_de_una_lista_es_un_objeto() -> None:
    """El protocolo MCP exige que ``structuredContent`` sea un objeto.

    Una lista desnuda se serializa como un JSON array y el cliente MCP la
    rechaza: por eso las salidas de lista van dentro de un campo con nombre.
    """
    servidor = _servidor()
    resultado = asyncio.run(servidor.call_tool("equipos", {"tag": "B-101"}))
    estructurado = resultado[1] if isinstance(resultado, tuple) else resultado
    assert isinstance(estructurado, dict)
    assert "equipos" in estructurado
    assert estructurado["equipos"][0]["tag"] == "B-101"


def test_el_esquema_de_salida_de_una_lista_declara_el_campo() -> None:
    servidor = _servidor()
    herramienta = next(
        tool for tool in servidor._tool_manager.list_tools() if tool.name == "equipos"
    )
    esquema = herramienta.output_schema
    assert esquema is not None
    assert esquema["type"] == "object"
    assert "equipos" in esquema["properties"]


def test_una_tool_con_un_valor_invalido_devuelve_error() -> None:
    """El modelo estricto de la tool rechaza el valor antes de llamar a la planta."""
    servidor = _servidor()
    with pytest.raises(ToolError):
        asyncio.run(
            servidor.call_tool(
                "historial_sensores",
                {"tag": "B-101", "variable": "presion_descarga", "limite": 0},
            )
        )


def test_una_tool_que_falla_no_tumba_el_servidor() -> None:
    class ClienteRoto:
        def get(self, ruta: str, *, params: dict[str, Any] | None = None) -> Any:
            raise RuntimeError("la planta se cayó")

    servidor = crear_servidor(ClienteRoto())  # type: ignore[arg-type]
    with pytest.raises(ToolError):
        asyncio.run(servidor.call_tool("equipos", {}))
    # El servidor sigue respondiendo: la tool que falla no lo deja caído.
    assert len(servidor._tool_manager.list_tools()) == len(REGISTRO)
