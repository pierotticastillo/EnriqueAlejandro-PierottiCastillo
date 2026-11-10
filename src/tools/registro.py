"""Registro de tools de solo lectura.

Cada tool declara su nombre, su descripción, el modelo de entrada y el modelo
de salida. El registro es lo que el servidor MCP publica y lo que los tests
recorren: una tool nueva se agrega acá y queda expuesta y probada.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from tools.cliente import ClientePlanta, ErrorHerramienta
from tools.equipos import equipos
from tools.historial import historial_ordenes, historial_sensores
from tools.inventario import inventario, lista_materiales
from tools.manuales import busqueda_manuales
from tools.modelos import (
    ConsultaCatalogoTareas,
    ConsultaEquipos,
    ConsultaHistorialOrdenes,
    ConsultaHistorialSensores,
    ConsultaInventario,
    ConsultaListaMateriales,
    ConsultaManuales,
    HistorialSensores,
    ListaEquipos,
    ListaFragmentos,
    ListaMateriales,
    ListaOrdenes,
    ListaRepuestos,
    ListaTareas,
)
from tools.rag import busqueda_rag
from tools.tareas import catalogo_tareas

Entrada = type[BaseModel]
Salida = type[BaseModel]


@dataclass(frozen=True)
class Tool:
    """Una tool de solo lectura: entrada, salida y función."""

    nombre: str
    descripcion: str
    entrada: Entrada
    salida: Salida
    funcion: Callable[..., Any]


REGISTRO: dict[str, Tool] = {}


def registrar(tool: Tool) -> Tool:
    """Agrega una tool al registro."""
    REGISTRO[tool.nombre] = tool
    return tool


def _tool(
    nombre: str,
    descripcion: str,
    entrada: Entrada,
    salida: Salida,
    funcion: Callable[..., Any],
) -> Tool:
    return registrar(Tool(nombre, descripcion, entrada, salida, funcion))


_tool(
    "equipos",
    "Equipos de la planta PIA-3 con sus nombres de campo, manómetro local y "
    "sensores con sus límites de alarma (de /api/planta).",
    ConsultaEquipos,
    ListaEquipos,
    equipos,
)
_tool(
    "historial_sensores",
    "Historial de un sensor (una muestra por minuto) con mínimo, máximo, promedio "
    "y tendencia de la ventana consultada. Los filtros son 'tag' (el equipo, por "
    "ejemplo B-102) y 'variable' (la variable del sensor, por ejemplo "
    "goteo_empaquetadura).",
    ConsultaHistorialSensores,
    HistorialSensores,
    historial_sensores,
)
_tool(
    "historial_ordenes",
    "Historial de órdenes de trabajo del archivo, con equipo, tareas, repuestos y "
    "el texto libre de quien hizo el trabajo.",
    ConsultaHistorialOrdenes,
    ListaOrdenes,
    historial_ordenes,
)
_tool(
    "inventario",
    "Stock de repuestos por almacén (yacimiento y central), con costo y equipos "
    "a los que corresponde cada repuesto.",
    ConsultaInventario,
    ListaRepuestos,
    inventario,
)
_tool(
    "lista_materiales",
    "Repuestos que le corresponden a un equipo, según la lista de materiales del "
    "manual de buenas prácticas.",
    ConsultaListaMateriales,
    ListaMateriales,
    lista_materiales,
)
_tool(
    "catalogo_tareas",
    "Catálogo de tareas del plan de mantenimiento: estándar y no estándar, con si "
    "detiene el equipo, horas, ejecutor, repuestos y el procedimiento del fabricante.",
    ConsultaCatalogoTareas,
    ListaTareas,
    catalogo_tareas,
)
_tool(
    "busqueda_manuales",
    "Búsqueda de texto en los manuales: devuelve fragmentos con su cita "
    "(por ejemplo MBP-4 §10.2) para poder citarlos en la respuesta.",
    ConsultaManuales,
    ListaFragmentos,
    busqueda_manuales,
)
_tool(
    "busqueda_rag",
    "Búsqueda híbrida (léxica + embeddings) en manuales, contrato e historial: "
    "devuelve los fragmentos vigentes con su cita para poder citarlos.",
    ConsultaManuales,
    ListaFragmentos,
    busqueda_rag,
)


def ejecutar(nombre: str, cliente: ClientePlanta, argumentos: dict[str, Any]) -> Any:
    """Ejecuta una tool del registro validando la entrada."""
    tool = REGISTRO.get(nombre)
    if tool is None:
        raise ErrorHerramienta(f"Tool desconocida: {nombre}", herramienta=nombre)
    try:
        consulta = tool.entrada.model_validate(argumentos or {})
    except Exception as exc:
        raise ErrorHerramienta(
            f"Argumentos inválidos para {nombre}: {exc}", herramienta=nombre
        ) from exc
    return tool.funcion(cliente, consulta)
