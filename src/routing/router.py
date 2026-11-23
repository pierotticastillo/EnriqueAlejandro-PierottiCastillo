"""Router principal: decide qué modelo de la lista cerrada atiende cada pedido.

Combina las estrategias en este orden:

1. **Capacidad**: filtra modelos incapaces (sin visión para imágenes, sin
   ventana grande para inputs largos, etc.).
2. **Reglas**: si queda un solo candidato, se elige directamente.
3. **Clasificación**: si quedan varios y las reglas no distinguen, un modelo
   chico etiqueta la complejidad.
4. **Latencia**: entre los aptos, elige el que responde más rápido recientemente.

El router es opcional: si no se provee un historial de latencia, se salta la
etapa 4 y se usa el primero de los candidatos ordenados por preferencia.
"""

from __future__ import annotations

from .capacidad import capacidad_filtrada
from .clasificador import etiquetar_complejidad, modelo_por_complejidad
from .latency import HistorialLatencia, proveedor_por_latencia
from .pedido import Pedido
from .reglas import elegir_por_reglas

#: Claves de modelo de la lista cerrada.
MODELO_LLM = "llm"
MODELO_VISION = "vision"

#: Orden de preferencia cuando hay varios modelos aptos y no hay datos de latencia.
PREFERENCIA_DEFECTO: list[str] = [MODELO_LLM, MODELO_VISION]


class Router:
    """Elige el modelo de la lista cerrada que debe atender un pedido."""

    def __init__(
        self,
        *,
        usar_clasificador: bool = False,
        usar_latencia: bool = False,
        historial: HistorialLatencia | None = None,
    ) -> None:
        self._usar_clasificador = usar_clasificador
        self._usar_latencia = usar_latencia
        self._historial = historial

    def elegir_modelo(self, pedido: Pedido) -> str:
        """Devuelve la clave del modelo de la lista cerrada para el pedido.

        Nunca devuelve una clave fuera de la lista cerrada. Si ninguna estrategia
        puede decidir, se usa el modelo generalista local (Qwen2.5:3b-instruct).
        """
        # 1. Filtrar por capacidad: descartar modelos incapaces.
        aptos = capacidad_filtrada(pedido)
        if not aptos:
            # Ningún modelo declarado cumple las capacidades: fallback al LLM local.
            return MODELO_LLM

        # 2. Si queda uno solo, elegirlo directamente.
        if len(aptos) == 1:
            return aptos[0]

        # 3. Reglas: si las reglas distinguen un modelo claro, usarlo.
        modelo_reglas = elegir_por_reglas(pedido)
        if modelo_reglas in aptos:
            return modelo_reglas

        # 4. Clasificación: si está habilitado y hay varios aptos, etiquetar.
        if self._usar_clasificador:
            resultado = etiquetar_complejidad(pedido)
            modelo_clase = modelo_por_complejidad(resultado.complejidad)
            if modelo_clase in aptos:
                return modelo_clase

        # 5. Latencia: entre los aptos, el más rápido recientemente.
        if self._usar_latencia and self._historial is not None:
            return proveedor_por_latencia(aptos, self._historial)

        # 6. Fallback: preferencia por defecto entre los aptos.
        for modelo in PREFERENCIA_DEFECTO:
            if modelo in aptos:
                return modelo

        return aptos[0]

    def registrar_latencia(self, modelo: str, segundos: float) -> None:
        """Registra una medición de latencia para un modelo (si hay historial)."""
        if self._historial is not None:
            self._historial.registrar(modelo, segundos)
