"""Validadores baratos para la cascada de modelos (Ítem 3.2 del Plan 3).

Un validador es un **chequeo en código** que decide si la respuesta de un
modelo sirve, sin llamar a otro modelo. Si el validador rechaza, la cascada
escala al siguiente modelo. Sin un validador barato y confiable no hay
cascada: el difícil pagaría dos veces.

Validadores incluidos:
- ``valida_json``: la respuesta es un JSON válido contra un esquema.
- ``valida_monto``: el monto calculado cierra con el presupuesto esperado.
- ``valida_tool_existe``: la tool invocada existe en el registro.
- ``valida_no_vacio``: la respuesta no está vacía.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ValidationError


class ResultadoValidacion:
    """Resultado de un validador barato."""

    def __init__(self, valido: bool, motivo: str = "") -> None:
        self.valido = valido
        self.motivo = motivo

    def __bool__(self) -> bool:
        return self.valido


def valida_json(
    respuesta: str,
    esquema: type[BaseModel] | None = None,
) -> ResultadoValidacion:
    """Valida que ``respuesta`` sea un JSON válido, opcionalmente contra un esquema Pydantic.

    Si no se provee esquema, solo se comprueba que sea JSON parseable.
    """
    if not respuesta or not respuesta.strip():
        return ResultadoValidacion(False, "respuesta vacía")
    try:
        import json

        datos = json.loads(respuesta)
    except Exception as exc:  # noqa: BLE001 - cualquier error de parseo invalida
        return ResultadoValidacion(False, f"no es JSON válido: {exc}")
    if esquema is None:
        return ResultadoValidacion(True, "JSON válido")
    try:
        esquema.model_validate(datos)
        return ResultadoValidacion(True, "JSON válido contra esquema")
    except ValidationError as exc:
        return ResultadoValidacion(False, f"no cumple el esquema: {exc}")


def valida_no_vacio(respuesta: str) -> ResultadoValidacion:
    """Valida que la respuesta no esté vacía."""
    if not respuesta or not respuesta.strip():
        return ResultadoValidacion(False, "respuesta vacía")
    return ResultadoValidacion(True, "respuesta no vacía")


def valida_monto(
    respuesta: str,
    monto_esperado: float,
    tolerancia: float = 0.01,
) -> ResultadoValidacion:
    """Valida que el monto en la respuesta esté cerca del esperado.

    Busca un número en la respuesta y compara con ``monto_esperado`` dentro de
    la tolerancia. Útil para cálculos de repuestos o costos.
    """
    import re

    numeros = re.findall(r"\d+(?:[.,]\d+)?", respuesta.replace(",", "."))
    if not numeros:
        return ResultadoValidacion(False, "no se encontró un monto en la respuesta")
    try:
        valor = float(numeros[0])
    except ValueError:
        return ResultadoValidacion(False, "no se pudo parsear el monto")
    if abs(valor - monto_esperado) <= tolerancia:
        return ResultadoValidacion(True, f"monto {valor} coincide con esperado {monto_esperado}")
    return ResultadoValidacion(
        False,
        f"monto {valor} no coincide con esperado {monto_esperado} (tolerancia {tolerancia})",
    )


def _parece_herramienta(token: str) -> bool:
    """Un token parece nombre de herramienta si tiene guion bajo (snake_case)."""
    return "_" in token


def valida_tool_existe(
    respuesta: str,
    herramientas_disponibles: set[str],
) -> ResultadoValidacion:
    """Valida que las tools mencionadas en la respuesta existan en el registro.

    Busca nombres de herramientas (patrón snake_case, con guion bajo) en la
    respuesta y comprueba que estén registradas.
    """
    tokens = set(respuesta.lower().split())
    posibles_herramientas = {t for t in tokens if _parece_herramienta(t)}
    usadas = posibles_herramientas & herramientas_disponibles
    desconocidas = posibles_herramientas - herramientas_disponibles

    if not usadas:
        if desconocidas:
            return ResultadoValidacion(
                False,
                f"tools desconocidas: {', '.join(sorted(desconocidas))}",
            )
        return ResultadoValidacion(False, "no se invocó ninguna tool conocida")
    if desconocidas:
        return ResultadoValidacion(
            False,
            f"tools válidas: {', '.join(sorted(usadas))}; "
            f"desconocidas: {', '.join(sorted(desconocidas))}",
        )
    return ResultadoValidacion(True, f"tools válidas: {', '.join(sorted(usadas))}")


#: Registro de validadores por nombre, para poder elegir por configuración.
VALIDADORES: dict[str, Callable[..., ResultadoValidacion]] = {
    "json": valida_json,
    "no_vacio": valida_no_vacio,
    "monto": valida_monto,
    "tool_existe": valida_tool_existe,
}


def obtener_validador(nombre: str) -> Callable[..., ResultadoValidacion] | None:
    """Devuelve el validador registrado con ese nombre, o None si no existe."""
    return VALIDADORES.get(nombre)
