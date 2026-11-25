"""Tabla de costos por request (Ítem 3.7 del Plan de implementación 3).

Cada modelo de la lista cerrada tiene un precio de entrada y uno de salida,
expresados en USD por millón de tokens. El costo de un request se calcula como:

    costo = (tokens_entrada × precio_entrada + tokens_salida × precio_salida) / 1.000.000

Los modelos locales de la planta (Qwen2.5:3b, Qwen2.5:0.5b, faster-whisper,
embeddings) tienen tarifa 0: no hay nada que facturar. Los modelos de nube
(NVIDIA Nemotron con visión) sí tienen tarifa diferencial entre entrada y
salida, como sugiere la presentación de Semana_5 (ej. USD 3 entrada / USD 15
salida por millón de tokens).

Esta tabla es la fuente de verdad de los precios: el router y el registro de
trayectoria la usan para calcular el costo de cada request.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PrecioModelo:
    """Precio de un modelo: entrada y salida, en USD por millón de tokens."""

    clave: str
    precio_entrada_usd_por_millon: float
    precio_salida_usd_por_millon: float


class TablaCostos:
    """Precios por modelo de la lista cerrada.

    Se inicializa con valores por defecto coherentes con la lista cerrada:
    modelos locales a USD 0 y el modelo de visión de nube con la tarifa
    diferencial de la presentación.
    """

    def __init__(self) -> None:
        self._precios: dict[str, PrecioModelo] = {}
        self._valores_por_defecto()

    def _valores_por_defecto(self) -> None:
        """Precios iniciales de la lista cerrada vigente."""
        # Modelos locales: sin costo (ejecutan en la propia planta).
        for clave in ("asr", "embeddings", "llm", "economico"):
            self._precios[clave] = PrecioModelo(
                clave=clave,
                precio_entrada_usd_por_millon=0.0,
                precio_salida_usd_por_millon=0.0,
            )
        # Modelo de nube con visión: tarifa diferencial (entrada más barata
        # que salida, como indica la presentación).
        self._precios["vision"] = PrecioModelo(
            clave="vision",
            precio_entrada_usd_por_millon=3.0,
            precio_salida_usd_por_millon=15.0,
        )

    def registrar_precio(
        self,
        *,
        clave: str,
        precio_entrada_usd_por_millon: float,
        precio_salida_usd_por_millon: float,
    ) -> None:
        """Registra o actualiza el precio de un modelo.

        Los precios deben ser >= 0. Un precio de 0 significa gratuito (modelo
        local o sin tarifa publicada).
        """
        if precio_entrada_usd_por_millon < 0 or precio_salida_usd_por_millon < 0:
            raise ValueError("Los precios no pueden ser negativos")
        self._precios[clave] = PrecioModelo(
            clave=clave,
            precio_entrada_usd_por_millon=precio_entrada_usd_por_millon,
            precio_salida_usd_por_millon=precio_salida_usd_por_millon,
        )

    def precio_entrada(self, clave: str) -> float:
        """Precio de entrada en USD por millón de tokens."""
        precio = self._precios.get(clave)
        if precio is None:
            raise KeyError(f"No hay precio registrado para el modelo: {clave}")
        return precio.precio_entrada_usd_por_millon

    def precio_salida(self, clave: str) -> float:
        """Precio de salida en USD por millón de tokens."""
        precio = self._precios.get(clave)
        if precio is None:
            raise KeyError(f"No hay precio registrado para el modelo: {clave}")
        return precio.precio_salida_usd_por_millon

    def costo_usd(self, *, clave: str, tokens_entrada: int, tokens_salida: int) -> float:
        """Costo de un request en USD.

        Fórmula: (entrada × precio_entrada + salida × precio_salida) / 1.000.000
        """
        if tokens_entrada < 0 or tokens_salida < 0:
            raise ValueError("Los tokens no pueden ser negativos")
        precio = self._precios.get(clave)
        if precio is None:
            raise KeyError(f"No hay precio registrado para el modelo: {clave}")
        costo = (
            tokens_entrada * precio.precio_entrada_usd_por_millon
            + tokens_salida * precio.precio_salida_usd_por_millon
        ) / 1_000_000
        return round(costo, 6)

    def claves(self) -> tuple[str, ...]:
        """Todas las claves de modelo con precio registrado."""
        return tuple(sorted(self._precios.keys()))

    def precios(self) -> dict[str, PrecioModelo]:
        """Copia de todos los precios registrados."""
        return dict(self._precios)


#: Instancia global de la tabla de costos (singleton simple).
tabla_costos = TablaCostos()
