"""Caché de respuestas completas (Ítem 3.10 del Plan de implementación 3).

La presentación de Semana_5 sugiere un **caché de respuestas completas**: si una
petición idéntica a una ya respondida llega de nuevo, se devuelve la respuesta
cacheada sin llamar al modelo. Tiene TTL configurable y se invalida cuando
cambian los datos subyacentes.

La clave se deriva de una representación canónica del pedido (tipo de tarea,
datos de entrada, modelo y parámetros opcionales), de modo que dos pedidos
idénticos producen siempre la misma clave.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class EntradaCache:
    """Una entrada del caché de respuestas."""

    valor: Any
    modelo: str  # para invalidar por modelo
    expira_en: float  # timestamp de expiración (monotónico)
    creada_en: float = field(default_factory=time.monotonic)
    hits: int = 0


@dataclass(frozen=True)
class ResultadoConsultaCache:
    """Resultado de consultar el caché."""

    golpe: bool  # True si se encontró una entrada válida y no expirada
    valor: Any | None = None
    clave: str = ""
    hits: int = 0


class CacheRespuestas:
    """Caché de respuestas completas con TTL e invalidación.

    * `obtener(...)` devuelve el valor cacheado si existe y no expiró.
    * `guardar(...)` almacena una respuesta bajo una clave canónica.
    * `invalidar(clave)` borra una entrada concreta.
    * `invalidar_por_modelo(modelo)` borra todas las entradas de un modelo.
    * `limpiar_expirados()` descarta las entradas cuya TTL se cumplió.
    """

    def __init__(self, *, ttl_s: float = 300.0, max_entradas: int = 1000) -> None:
        if ttl_s <= 0:
            raise ValueError("ttl_s debe ser > 0")
        if max_entradas < 1:
            raise ValueError("max_entradas debe ser >= 1")

        self._ttl_s = ttl_s
        self._max_entradas = max_entradas
        self._entradas: dict[str, EntradaCache] = {}

    # ------------------------------------------------------------------ alta

    def clave(self, *, tipo_tarea: str, datos: Any, modelo: str, **kwargs: Any) -> str:
        """Devuelve la clave canónica para un pedido."""
        payload: dict[str, Any] = {
            "tipo_tarea": tipo_tarea,
            "datos": datos,
            "modelo": modelo,
        }
        payload.update(kwargs)
        canonico = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(canonico.encode("utf-8")).hexdigest()

    def obtener(
        self, *, tipo_tarea: str, datos: Any, modelo: str, **kwargs: Any
    ) -> ResultadoConsultaCache:
        """Devuelve el valor cacheado si existe y no expiró, si no un fallo."""
        clave = self.clave(
            tipo_tarea=tipo_tarea, datos=datos, modelo=modelo, **kwargs
        )
        entrada = self._entradas.get(clave)

        if entrada is None:
            return ResultadoConsultaCache(golpe=False, clave=clave)

        if time.monotonic() > entrada.expira_en:
            # Entrada expirada: la descartamos y devolvemos fallo.
            del self._entradas[clave]
            return ResultadoConsultaCache(golpe=False, clave=clave)

        # Entrada válida: contamos el hit.
        entrada_actualizada = replace(entrada, hits=entrada.hits + 1)
        self._entradas[clave] = entrada_actualizada
        return ResultadoConsultaCache(
            golpe=True,
            valor=entrada.valor,
            clave=clave,
            hits=entrada_actualizada.hits,
        )

    def guardar(
        self, *, tipo_tarea: str, datos: Any, valor: Any, modelo: str, **kwargs: Any
    ) -> str:
        """Almacena una respuesta bajo su clave canónica y devuelve la clave."""
        clave = self.clave(
            tipo_tarea=tipo_tarea, datos=datos, modelo=modelo, **kwargs
        )
        ahora = time.monotonic()
        self._entradas[clave] = EntradaCache(
            valor=valor,
            modelo=modelo,
            expira_en=ahora + self._ttl_s,
            creada_en=ahora,
            hits=0,
        )
        return clave

    def invalidar(self, clave: str) -> bool:
        """Borra una entrada concreta. Devuelve True si existía."""
        return self._entradas.pop(clave, None) is not None

    def invalidar_por_modelo(self, modelo: str) -> int:
        """Borra todas las entradas de un modelo. Devuelve cuántas borró."""
        claves = [c for c, e in self._entradas.items() if e.modelo == modelo]
        for c in claves:
            del self._entradas[c]
        return len(claves)

    def limpiar_expirados(self) -> int:
        """Descarta las entradas cuya TTL se cumplió. Devuelve cuántas borró."""
        ahora = time.monotonic()
        expiradas = [c for c, e in self._entradas.items() if ahora > e.expira_en]
        for c in expiradas:
            del self._entradas[c]
        return len(expiradas)

    def esta_vacia(self) -> bool:
        """True si no hay entradas."""
        return len(self._entradas) == 0

    def cantidad(self) -> int:
        """Cuántas entradas tiene el caché."""
        return len(self._entradas)

    def resumen(self) -> dict[str, object]:
        """Devuelve un resumen del estado del caché."""
        ahora = time.monotonic()
        activas = sum(1 for e in self._entradas.values() if ahora <= e.expira_en)
        expiradas = len(self._entradas) - activas
        total_hits = sum(e.hits for e in self._entradas.values())
        modelos = {e.modelo for e in self._entradas.values()}
        return {
            "entradas_total": len(self._entradas),
            "activas": activas,
            "expiradas": expiradas,
            "ttl_s": self._ttl_s,
            "max_entradas": self._max_entradas,
            "hits_totales": total_hits,
            "modelos": sorted(modelos),
        }


#: Instancia global simple (opcional). Se prefiere inyectar el caché.
cache_respuestas = CacheRespuestas()
