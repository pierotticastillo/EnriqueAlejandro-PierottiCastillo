"""Cascada con validador (Ítem 3.2 del Plan 3).

La cascada intenta primero con el modelo barato y pasa al caro solo si un
validador barato y confiable rechaza la respuesta. Sin un validador confiable
no hay cascada: el difícil pagaría dos veces.

Flujo:

1. Pedido → Modelo Económico → Validador.
2. Si válido → respuesta (1 llamada, costo bajo).
3. Si inválido → Modelo Generalista → Validador → respuesta (2 llamadas,
   costo mayor).
4. Si el validador también rechaza en el modelo caro, se devuelve la última
   respuesta con el motivo escrito.

La cascada es opcional: si no se provee un validador, se usa el comportamiento
actual (una sola llamada al modelo elegido por el router).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .validador import ResultadoValidacion, valida_no_vacio

#: Claves de modelo de la lista cerrada.
MODELO_ECONOMICO = "llm"
MODELO_GENERALISTA = "llm"


@dataclass(frozen=True)
class ResultadoCascada:
    """Resultado de una consulta por cascada."""

    respuesta: str
    modelo_usado: str
    llamadas: int
    valido: bool
    motivo: str


class Cascada:
    """Orquestador de la cascada: barato primero, caro si el validador rechaza."""

    def __init__(
        self,
        *,
        validador: Callable[[str], ResultadoValidacion] | None = None,
        modelo_barato: str = MODELO_ECONOMICO,
        modelo_caro: str = MODELO_GENERALISTA,
        consultar_modelo: Callable[[str, str], str] | None = None,
    ) -> None:
        """Crea una cascada.

        ``consultar_modelo`` es un callable que recibe (modelo, pedido) y
        devuelve la respuesta como texto. Si no se provee, la cascada no puede
        ejecutarse y lanza ``ValueError`` al intentar consultar.
        """
        self._validador = validador or valida_no_vacio
        self._modelo_barato = modelo_barato
        self._modelo_caro = modelo_caro
        self._consultar_modelo = consultar_modelo

    def consultar(self, pedido: str) -> ResultadoCascada:
        """Ejecuta la cascada sobre ``pedido``.

        Devuelve la respuesta del primer modelo que pasa el validador, o la
        última respuesta si ninguno pasa.
        """
        if self._consultar_modelo is None:
            raise ValueError("la cascada requiere un consultar_modelo configurado")

        # 1. Intento con el modelo barato.
        respuesta_barata = self._consultar_modelo(self._modelo_barato, pedido)
        validacion_barata = self._validador(respuesta_barata)

        if validacion_barata.valido:
            return ResultadoCascada(
                respuesta=respuesta_barata,
                modelo_usado=self._modelo_barato,
                llamadas=1,
                valido=True,
                motivo="válido en modelo barato",
            )

        # 2. El modelo barato falló: escalar al modelo caro.
        respuesta_cara = self._consultar_modelo(self._modelo_caro, pedido)
        validacion_cara = self._validador(respuesta_cara)

        return ResultadoCascada(
            respuesta=respuesta_cara,
            modelo_usado=self._modelo_caro,
            llamadas=2,
            valido=validacion_cara.valido,
            motivo=(
                "rechazado en modelo barato ("
                + validacion_barata.motivo
                + "); "
                + (
                    "válido en modelo caro"
                    if validacion_cara.valido
                    else "rechazado también en modelo caro"
                )
            ),
        )
