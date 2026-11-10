"""Las herramientas del agente, en LangChain.

**No hay una segunda definición de las tools.** El agente usa el mismo
registro que publica el servidor MCP (``tools.registro.REGISTRO``) y los
mismos modelos de entrada estrictos (``extra="forbid"``): el contrato que ve
el modelo es el mismo que ve un cliente MCP, con ``additionalProperties:
false`` en el esquema.

Cada tool devuelve un texto corto: es lo que el modelo lee y lo que queda
registrado en la trayectoria. Los resultados voluminosos (un historial entero,
un catálogo de tareas) se recortan para que no se coman el contexto.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import StructuredTool

from tools.cliente import ClientePlanta, ErrorHerramienta
from tools.registro import REGISTRO, Tool

logger = logging.getLogger("agente.tools")

#: Tools que necesitan el corpus de manuales para leer el plan de
#: mantenimiento. Es el mismo criterio que usa el servidor MCP.
_usan_CORPUS = {
    "inventario",
    "lista_materiales",
    "catalogo_tareas",
    "busqueda_manuales",
}

#: Tope de caracteres de la respuesta de una tool: el modelo lee un resumen,
#: no el historial entero.
MAX_CARACTERES = 1500


def construir_tools(
    cliente: ClientePlanta,
    *,
    corpus: Any | None = None,
    recuperador: Any | None = None,
) -> list[StructuredTool]:
    """Las tools del agente, tomadas del registro de tools.

    ``recuperador`` es el índice del RAG ya armado (pgvector). Si está
    disponible, la búsqueda usa ese índice; si no, la tool usa su propio
    recuperador y, en el peor caso, cae a la búsqueda léxica.
    """
    return [
        _armar(tool, cliente, corpus=corpus, recuperador=recuperador)
        for tool in REGISTRO.values()
    ]


def _armar(
    tool: Tool,
    cliente: ClientePlanta,
    *,
    corpus: Any | None,
    recuperador: Any | None,
) -> StructuredTool:
    """Una tool de LangChain a partir de una tool del registro."""
    nombre = tool.nombre

    def _llamar(**argumentos: Any) -> str:
        try:
            consulta = tool.entrada.model_validate(argumentos)
        except Exception as exc:  # noqa: BLE001 - un argumento inválido no tumba el agente
            return f"Argumentos inválidos para {nombre}: {exc}"
        try:
            if nombre == "busqueda_rag" and _usa_indice(recuperador):
                resultado = recuperador.buscar(consulta)
            elif nombre in _usan_CORPUS and corpus is not None:
                resultado = tool.funcion(cliente, consulta, corpus=corpus)
            else:
                resultado = tool.funcion(cliente, consulta)
        except ErrorHerramienta as exc:
            return f"La herramienta {nombre} falló: {exc}"
        except Exception as exc:  # noqa: BLE001 - una tool caída no tumba el agente
            logger.warning("La tool %s falló: %s", nombre, exc)
            return f"La herramienta {nombre} falló: {exc}"
        return _texto(resultado)

    _llamar.__name__ = nombre
    return StructuredTool.from_function(
        func=_llamar,
        name=nombre,
        description=tool.descripcion,
        args_schema=tool.entrada,
    )


def _usa_indice(recuperador: Any | None) -> bool:
    """Si el índice del RAG ya está armado y se puede usar.

    Mientras el índice se arma en segundo plano, o si no se pudo armar, la
    tool usa su propio recuperador y, en el peor caso, cae a la búsqueda
    léxica: el agente nunca se queda sin respuesta.
    """
    if recuperador is None:
        return False
    return bool(getattr(recuperador, "disponible", False))


def _texto(resultado: Any) -> str:
    """La salida de la tool, como texto corto para el modelo."""
    if resultado is None:
        return "Sin resultados."
    if hasattr(resultado, "model_dump"):
        datos = resultado.model_dump(mode="json")
    elif isinstance(resultado, list):
        datos = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for item in resultado
        ]
    else:
        datos = resultado
    try:
        texto = json.dumps(datos, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        texto = str(datos)
    if len(texto) > MAX_CARACTERES:
        texto = texto[:MAX_CARACTERES] + "… (recortado)"
    return texto
