"""Contexto de tendencia reciente desde el historiador de la simulación."""

from __future__ import annotations

import logging
import math
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from planta_contrato import EventoSistema, TipoEvento

logger = logging.getLogger("planta.historial")


class Historiador:
    """Consulta y resume la ventana reciente de una variable de planta."""

    def __init__(
        self,
        api_url: str,
        *,
        timeout_s: float = 2.0,
        cliente: httpx.Client | None = None,
        ventana: timedelta = timedelta(hours=6),
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._timeout_s = timeout_s
        self._cliente = cliente or httpx.Client(timeout=timeout_s, trust_env=True)
        self._ventana = ventana
        self._cache: dict[tuple[str, str, int], str] = {}
        self._lock = threading.Lock()

    def cerrar(self) -> None:
        self._cliente.close()

    def resumen_tendencia(
        self,
        tag: str,
        variable: str,
        hasta: datetime,
        *,
        unidad: str | None = None,
    ) -> str | None:
        fin = _aware(hasta)
        inicio = fin - self._ventana
        clave = (tag, variable, int(fin.timestamp() // 60))
        with self._lock:
            if clave in self._cache:
                return self._cache[clave]

        url = f"{self._api_url}/api/historian/{tag}/{variable}"
        try:
            respuesta = self._cliente.get(
                url,
                params={"desde": inicio.isoformat(), "hasta": fin.isoformat()},
                timeout=self._timeout_s,
            )
            respuesta.raise_for_status()
            puntos = _puntos_en_ventana(respuesta.json(), inicio, fin)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("No se pudo obtener la tendencia de %s/%s: %s", tag, variable, exc)
            return None

        if not puntos:
            return None
        valores = [valor for _, valor in puntos]
        primero, ultimo = valores[0], valores[-1]
        margen = max((max(valores) - min(valores)) * 0.05, abs(primero) * 0.005, 0.01)
        direccion = "sube" if ultimo - primero > margen else (
            "baja" if primero - ultimo > margen else "estable"
        )
        sufijo = f" {unidad}" if unidad else ""
        resumen = (
            f"Tendencia {variable} últimas 6 h: {len(valores)} muestras, "
            f"{primero:g}→{ultimo:g}{sufijo} ({direccion}), "
            f"rango {min(valores):g}–{max(valores):g}{sufijo}."
        )
        with self._lock:
            self._cache[clave] = resumen
        return resumen

    def enriquecer_evento(
        self,
        evento: EventoSistema,
        tag: str,
        variable: str,
        *,
        unidad: str | None = None,
    ) -> EventoSistema:
        if evento.tipo is not TipoEvento.escalado:
            return evento
        tendencia = self.resumen_tendencia(tag, variable, evento.ts, unidad=unidad)
        if not tendencia:
            return evento
        return evento.model_copy(update={"resumen": f"{evento.resumen} {tendencia}"})


def _aware(valor: datetime) -> datetime:
    if valor.tzinfo is None:
        return valor.replace(tzinfo=UTC)
    return valor


def _puntos_en_ventana(
    payload: Any,
    desde: datetime,
    hasta: datetime,
) -> list[tuple[datetime, float]]:
    if not isinstance(payload, list):
        return []
    puntos: list[tuple[datetime, float]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        ts_raw, valor_raw = item.get("ts"), item.get("valor")
        if not isinstance(ts_raw, str) or isinstance(valor_raw, bool):
            continue
        try:
            ts = _aware(datetime.fromisoformat(ts_raw))
            valor = float(valor_raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(valor) and desde <= ts <= hasta:
            puntos.append((ts, valor))
    puntos.sort(key=lambda punto: punto[0])
    return puntos
