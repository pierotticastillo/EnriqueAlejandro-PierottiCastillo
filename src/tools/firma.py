"""Herramientas para publicar una tool con la firma de su modelo de entrada.

FastMCP arma el esquema de entrada a partir de la firma de la función. Para no
escribir una función por tool, se genera la firma desde el modelo Pydantic: el
esquema que ve el cliente MCP es el del modelo estricto, con ``extra="forbid"``.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel
from pydantic_core import PydanticUndefined


def firma_de_modelo(modelo: type[BaseModel]) -> inspect.Signature:
    """Firma con un parámetro por campo del modelo."""
    parametros = []
    for nombre, campo in modelo.model_fields.items():
        default = campo.default
        if default is PydanticUndefined:
            default = inspect.Parameter.empty
        parametros.append(
            inspect.Parameter(
                nombre,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=campo.annotation,
                default=default,
            )
        )
    return inspect.Signature(parametros, return_annotation=Any)


def armar_llamador(
    modelo: type[BaseModel],
    funcion: Callable[[BaseModel], Any],
    salida: type[BaseModel] | None = None,
) -> Callable[..., Any]:
    """Arma una función con la firma del modelo que llama a ``funcion``.

    Si se pasa ``salida``, el resultado se valida contra ese modelo (por
    ejemplo ``ListaEquipos``, que lleva la lista de equipos dentro de un
    campo) y se devuelve ya validado.
    """

    def _llamar(**argumentos: Any) -> Any:
        resultado = funcion(modelo.model_validate(argumentos))
        if salida is None:
            return resultado
        return _validar_salida(salida, resultado)

    _llamar.__signature__ = firma_de_modelo(modelo)  # type: ignore[attr-defined]
    _llamar.__annotations__ = {
        nombre: parametro.annotation
        for nombre, parametro in _llamar.__signature__.parameters.items()  # type: ignore[attr-defined]
    }
    # FastMCP exige anotación de retorno para la salida estructurada.
    _llamar.__annotations__["return"] = salida if salida is not None else Any
    if salida is not None:
        _llamar.__signature__ = _llamar.__signature__.replace(  # type: ignore[attr-defined]
            return_annotation=salida
        )
    return _llamar


def _validar_salida(salida: type[BaseModel], resultado: Any) -> BaseModel:
    """Valida el resultado de una tool contra su modelo de salida.

    Las tools que devuelven una lista usan ``envolver``: la lista entra en un
    campo con nombre porque el protocolo MCP exige que ``structuredContent``
    sea un objeto y no un arreglo.
    """
    if isinstance(resultado, salida):
        return resultado
    envolver = getattr(salida, "envolver", None)
    if envolver is not None and isinstance(resultado, list):
        return envolver(resultado)
    return salida.model_validate(resultado)
