"""Caché de tools (Ítem 3.10 del Plan de implementación 3).

La presentación de Semana_5 sugiere un **caché de tools**: si una herramienta
ya se ejecutó con los mismos argumentos, se devuelve el resultado cacheado sin
volver a ejecutarla. Tiene TTL configurable y se invalida cuando cambian los
datos subyacentes.

La clave se deriva del nombre de la herramienta y sus argumentos serializados.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class EntradaCacheTool:
    """Una entrada del caché de tools."""

    valor: Any
    nombre_tool: str  # para invalidar por herramienta
    expira_en: float  # timestamp de expiración (monotónico)
    creada_en: float = field(default_factory=time.monotonic)
    hits: int = 0


@dataclass(frozen=True)
class ResultadoConsultaCacheTool:
    """Resultado de consultar el caché de tools."""

    golpe: bool  # True si se encontró una entrada válida y no expirada
    valor: Any | None = None
    clave: str = ""
    hits: int = 0


class CacheTools:
    """Caché de resultados de herramientas con TTL e invalidación.

    * `obtener(...)` devuelve el resultado cacheado si existe y no expiró.
    * `guardar(...)` almacena un resultado bajo una clave canónica.
    * `invalidar(clave)` borra una entrada concreta.
    * `invalidar_por_tool(nombre_tool)` borra todas las entradas de una tool.
    * `limpiar_expirados()` descarta las entradas cuya TTL se cumplió.
    """

    def __init__(self, *, ttl_s: float = 300.0, max_entradas: int = 1000) -> None:
        if ttl_s <= 0:
            raise ValueError("ttl_s debe ser > 0")
        if max_entradas < 1:
            raise ValueError("max_entradas debe ser >= 1")

        self._ttl_s = ttl_s
        self._max_entradas = max_entradas
        self._entradas: dict[str, EntradaCacheTool] = {}

    # ------------------------------------------------------------------ alta

    def clave(self, *, nombre_tool: str, argumentos: dict[str, Any] | None = None) -> str:
        """Devuelve la clave canónica para una ejecución de tool."""
        payload: dict[str, Any] = {"nombre_tool": nombre_tool}
        if argumentos is not None:
            payload["argumentos"] = argumentos
        canonico = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(canonico.encode("utf-8")).hexdigest()

    def obtener(
        self, *, nombre_tool: str, argumentos: dict[str, Any] | None = None
    ) -> ResultadoConsultaCacheTool:
        """Devuelve el resultado cacheado si existe y no expiró, si no un fallo."""
        clave = self.clave(nombre_tool=nombre_tool, argumentos=argumentos)
        entrada = self._entradas.get(clave)

        if entrada is None:
            return ResultadoConsultaCacheTool(golpe=False, clave=clave)

        if time.monotonic() > entrada.expira_en:
            del self._entradas[clave]
            return ResultadoConsultaCacheTool(golpe=False, clave=clave)

        entrada_actualizada = replace(entrada, hits=entrada.hits + 1)
        self._entradas[clave] = entrada_actualizada
        return ResultadoConsultaCacheTool(
            golpe=True,
            valor=entrada.valor,
            clave=clave,
            hits=entrada_actualizada.hits,
        )

    def guardar(
        self,
        *,
        nombre_tool: str,
        valor: Any,
        argumentos: dict[str, Any] | None = None,
    ) -> str:
        """Almacena un resultado bajo su clave canónica y devuelve la clave."""
        clave = self.clave(nombre_tool=nombre_tool, argumentos=argumentos)
        ahora = time.monotonic()
        self._entradas[clave] = EntradaCacheTool(
            valor=valor,
            nombre_tool=nombre_tool,
            expira_en=ahora + self._ttl_s,
            creada_en=ahora,
            hits=0,
        )
        return clave

    def invalidar(self, clave: str) -> bool:
        """Borra una entrada concreta. Devuelve True si existía."""
        return self._entradas.pop(clave, None) is not None

    def invalidar_por_tool(self, nombre_tool: str) -> int:
        """Borra todas las entradas de una herramienta. Devuelve cuántas borró."""
        claves = [c for c, e in self._entradas.items() if e.nombre_tool == nombre_tool]
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
        herramientas = {e.nombre_tool for e in self._entradas.values()}
        return {
            "entradas_total": len(self._entradas),
            "activas": activas,
            "expiradas": expiradas,
            "ttl_s": self._ttl_s,
            "max_entradas": self._max_entradas,
            "hits_totales": total_hits,
            "herramientas": sorted(herramientas),
        }


#: Instancia global simple (opcional). Se prefiere inyectar el caché.
cache_tools = CacheTools()
