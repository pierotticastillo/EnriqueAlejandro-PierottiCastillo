"""Cadena de fallback de 5 niveles (Ítem 3.11 del Plan de implementación 3).

Ante un error 429/5xx/timeout/filtro de contenido, el sistema recorre la
cadena hasta que un nivel tenga éxito:

1. **Reintentar** con backoff (usa `PoliticaReintento` de P1-17)
2. **Otra región** u otro proveedor, mismo modelo
3. **Modelo alternativo** de capacidad similar
4. **Modelo chico** o local
5. **Degradar** respuesta reducida

Cada nivel responde peor que el anterior, pero **responde**. Si se llega al
nivel 5, se avisa explícitamente (nunca degradar en silencio).
"""

from __future__ import annotations

from collections.abc import Callable

from .fallback_nivel import (
    CADENA_COMPLETA,
    NivelFallback,
    ResultadoCadena,
    ResultadoNivel,
)

#: Tipo de callback para cada nivel de la cadena.
#: Recibe el error original y devuelve un ResultadoNivel.
CallbackNivel = Callable[[Exception], ResultadoNivel]


class CadenaFallback:
    """Recorre la cadena de fallback de 5 niveles hasta que uno tenga éxito.

    Cada nivel recibe un callback opcional. Si no hay callback para un nivel,
    se salta. El nivel 5 (degradar) siempre se intenta si se llega hasta él,
    aunque su callback devuelva exito=False: es el nivel de último recurso.
    """

    def __init__(
        self,
        *,
        callbacks: dict[NivelFallback, CallbackNivel] | None = None,
        politica_reintento: object | None = None,
    ) -> None:
        self._callbacks: dict[NivelFallback, CallbackNivel] = dict(callbacks or {})
        self._politica = politica_reintento

    def _nivel_registrado(self, nivel: NivelFallback) -> bool:
        return nivel in self._callbacks

    def ejecutar_con_fallback(self, accion_principal: Callable[[], object]) -> ResultadoCadena:
        """Ejecuta `accion_principal` y, si falla, recorre la cadena de fallback.

        Devuelve un `ResultadoCadena` con el nivel alcanzado, la respuesta y
        los niveles probados.
        """
        niveles_probados: list[ResultadoNivel] = []

        # 1. Intento principal
        try:
            respuesta = accion_principal()
            return ResultadoCadena(
                exito=True,
                nivel_alcanzado=None,
                respuesta=str(respuesta),
                niveles_probados=tuple(niveles_probados),
                motivo="éxito en el intento principal",
            )
        except Exception as exc_original:
            error_actual = exc_original

        # 2. Recorrer la cadena de fallback
        for nivel in CADENA_COMPLETA:
            callback = self._callbacks.get(nivel)

            if callback is None:
                # Nivel sin callback: se salta (excepto degradar, que se intenta
                # siempre si se llega hasta aquí)
                if nivel != "degradar":
                    continue
                # Para degradar sin callback, devolvemos fallo en ese nivel
                niveles_probados.append(
                    ResultadoNivel(
                        nivel=nivel,
                        exito=False,
                        motivo="sin callback de degradar registrado",
                        error_original=str(error_actual),
                    )
                )
                continue

            try:
                resultado = callback(error_actual)
            except Exception as exc_callback:
                # El propio callback falló: registramos y seguimos al siguiente
                niveles_probados.append(
                    ResultadoNivel(
                        nivel=nivel,
                        exito=False,
                        motivo=f"el callback del nivel falló: {exc_callback}",
                        error_original=str(error_actual),
                    )
                )
                error_actual = exc_callback
                continue

            niveles_probados.append(resultado)

            if resultado.exito:
                return ResultadoCadena(
                    exito=True,
                    nivel_alcanzado=nivel,
                    respuesta=resultado.respuesta,
                    niveles_probados=tuple(niveles_probados),
                    motivo=resultado.motivo or f"éxito en el nivel {nivel}",
                )

            # El nivel no tuvo éxito: el error para el siguiente nivel es el
            # error original (o el del callback si lo devolvió)
            error_actual = Exception(resultado.motivo or str(error_actual))

        # Se agotaron todos los niveles sin éxito
        return ResultadoCadena(
            exito=False,
            nivel_alcanzado=niveles_probados[-1].nivel if niveles_probados else None,
            respuesta=None,
            niveles_probados=tuple(niveles_probados),
            motivo="se agotaron todos los niveles de fallback sin éxito",
        )

    def registrar_callback(self, nivel: NivelFallback, callback: CallbackNivel) -> None:
        """Registra o reemplaza el callback para un nivel de la cadena."""
        self._callbacks[nivel] = callback

    def niveles_registrados(self) -> tuple[NivelFallback, ...]:
        """Devuelve los niveles que tienen callback registrado."""
        return tuple(sorted(self._callbacks.keys(), key=CADENA_COMPLETA.index))
