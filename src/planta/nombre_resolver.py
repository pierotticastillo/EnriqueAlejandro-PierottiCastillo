"""Resolución de los nombres informales que usa el operador.

La fuente de verdad es ``GET /api/planta``: cada equipo trae ``nombres`` con
los alias que se dicen en campo ("la uno", "la del medio", "el pulmón", ...).
Si el operador dice solo "la bomba" o "el filtro", la mención queda ambigua y
el sistema tiene que preguntar.

El ASR a menudo garbla los nombres ("la dos" → "Lados", "la bomba" → "el
aboma"). El patrón de alias con espacios opcionales ya cubre el primer caso.
Para el segundo, se agrega un fallback de distancia de edición (Levenshtein)
que reconoce el alias más cercano cuando la coincidencia exacta falla.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from planta.texto import normalizar

# Alias mínimos para poder trabajar sin la API (tests y arranque degradado).
# En producción se reemplazan por los que publica /api/planta.
ALIAS_PREDETERMINADOS: dict[str, list[str]] = {
    "B-101": ["la uno", "la 1", "la de la punta", "la del portón", "bomba uno"],
    "B-102": ["la dos", "la 2", "la del medio", "bomba dos"],
    "B-103": ["la tres", "la 3", "la del fondo", "la de reserva", "bomba tres"],
    "F-201A": ["el filtro A", "el primario", "el de la izquierda", "el grueso"],
    "F-201B": ["el filtro B", "el secundario", "el de la derecha", "el fino"],
    "TK-301": ["el pulmón", "el tanque", "el tanque pulmón"],
    "FT-401": ["el caudalímetro", "el colector"],
}
TIPO_PREDETERMINADO: dict[str, str] = {
    "B-101": "bomba",
    "B-102": "bomba",
    "B-103": "bomba",
    "F-201A": "filtro",
    "F-201B": "filtro",
    "TK-301": "tanque",
    "FT-401": "caudalimetro",
}

GENERICOS = {
    "bomba": ("bomba", "la bomba"),
    "filtro": ("filtro", "el filtro"),
}


@dataclass(frozen=True)
class Mencion:
    """Un equipo nombrado (o una mención genérica) dentro de un texto."""

    tag: str | None
    texto: str
    inicio: int
    fin: int
    generico: str | None = None

    @property
    def es_ambigua(self) -> bool:
        return self.tag is None and self.generico is not None


class ResolutorNombres:
    """Resuelve alias contra el catálogo de equipos de la planta."""

    def __init__(
        self,
        equipos: list[dict[str, Any]] | None = None,
        *,
        alias: dict[str, list[str]] | None = None,
        tipos: dict[str, str] | None = None,
    ) -> None:
        self._alias: dict[str, str] = {}
        self._nombres: dict[str, str] = {}
        self._tipos: dict[str, str] = {}

        alias = alias or ALIAS_PREDETERMINADOS
        tipos = tipos or TIPO_PREDETERMINADO

        if equipos:
            alias, tipos = _desde_planta(equipos, alias, tipos)

        for tag, nombres in alias.items():
            self._tipos[tag] = tipos.get(tag, "")
            if nombres:
                self._nombres[tag] = nombres[0]
            for nombre in nombres:
                self._alias[normalizar(nombre)] = tag
            self._alias[normalizar(tag)] = tag

        # Cada alias tiene su propio grupo en el patrón: así, cuando el ASR
        # junta las palabras del alias, el tag se busca por el grupo que
        # matcheó y no por el texto exacto ("lados" por "la dos").
        self._alias_por_grupo: dict[str, str] = {}
        for grupo, nombre in _alias_ordenados(self._alias):
            self._alias_por_grupo[grupo] = self._alias[nombre]

        self._patron_alias = (
            re.compile(
                "|".join(
                    rf"(?P<{grupo}>(?<!\w){_alias_partido(nombre)}(?!\w))"
                    for grupo, nombre in _alias_ordenados(self._alias)
                )
            )
            if self._alias
            else None
        )
        self._patron_generico = re.compile(
            r"(?<!\w)(?:(?:la|el|un|una)\s+)?(bomba|filtro)(?!\s+(?:uno|dos|tres|[123]|[ab])(?!\w))"
        )

    def resolver(self, texto: str, desde: int = 0) -> Mencion | None:
        """Devuelve la primera mención (explícita o genérica) desde ``desde``.

        Primero busca coincidencia exacta con el patrón de alias (incluyendo
        palabras juntas, ej. "Lados" por "la dos"). Si no encuentra nada,
        aplica fuzzy matching (distancia de edición ≤ 2) para reconocer
        nombres garblados por el ASR (ej. "el aboma" por "la bomba").
        """
        normalizado = normalizar(texto)
        desde = max(0, min(desde, len(normalizado)))
        explicita = self._buscar_explicita(normalizado, desde)
        if explicita is not None:
            return explicita
        # Fallback: fuzzy matching cuando el ASR sustituyó palabras
        fuzzy = self._buscar_fuzzy(normalizado, desde)
        if fuzzy is not None:
            return fuzzy
        return self._buscar_generica(normalizado, desde)

    def _buscar_fuzzy(self, texto: str, desde: int) -> Mencion | None:
        """Busca el alias más cercano por distancia de edición.

        Fallback conservador para cuando el ASR sustituyó palabras ("el aboma"
        por "la bomba"). Se divide el texto en tokens y se compara cada token
        con los alias conocidos. Solo se acepta una coincidencia si:

        * el token tiene al menos 4 caracteres (evita confundir "dos"/"uno"/"tres"),
        * la distancia es ≤ 1 para tokens de 4-5 caracteres,
        * la distancia es ≤ 2 para tokens de 6+ caracteres.

        Esto evita falsos positivos como asociar "dos" con "uno" o "bomba"
        con cualquier alias corto.
        """
        if not self._alias:
            return None

        mejor_tag: str | None = None
        mejor_distancia = 99
        mejor_inicio = -1
        mejor_fin = -1

        tokens = re.findall(r"\w+", texto)
        for token in tokens:
            if len(token) < 4:
                continue
            umbral = 1 if len(token) <= 5 else 2
            for alias_normalizado, tag in self._alias.items():
                distancia = _distancia_edicion(token, alias_normalizado)
                if distancia <= umbral and distancia < mejor_distancia:
                    mejor_distancia = distancia
                    mejor_tag = tag
                    idx = texto.find(token)
                    mejor_inicio = idx
                    mejor_fin = idx + len(token)

        if mejor_tag is not None:
            return Mencion(
                tag=mejor_tag,
                texto=texto[mejor_inicio:mejor_fin] if mejor_inicio >= 0 else "",
                inicio=mejor_inicio,
                fin=mejor_fin,
            )
        return None

    def resolver_tag(self, texto: str) -> str | None:
        """Tag de la primera mención explícita, o ``None``."""
        mencion = self._buscar_explicita(normalizar(texto), 0)
        return mencion.tag if mencion else None

    def candidatos(self, generico: str) -> list[str]:
        """Tags que podrían ser la mención genérica."""
        tipo = "bomba" if generico.startswith("b") else "filtro"
        return [tag for tag, t in self._tipos.items() if t == tipo]

    def nombre_canonico(self, tag: str) -> str:
        return self._nombres.get(tag, tag)

    def nombres_de(self, tags: list[str]) -> list[str]:
        return [self.nombre_canonico(tag) for tag in tags]

    def _buscar_explicita(self, texto: str, desde: int) -> Mencion | None:
        if self._patron_alias is None:
            return None
        mejor: re.Match[str] | None = None
        for coincidencia in self._patron_alias.finditer(texto, desde):
            if mejor is None or coincidencia.start() < mejor.start():
                mejor = coincidencia
            elif coincidencia.start() == mejor.start() and coincidencia.end() > mejor.end():
                mejor = coincidencia
            if mejor.start() == desde:
                break
        if mejor is None:
            return None
        return Mencion(
            tag=self._alias_por_grupo.get(mejor.lastgroup or "", ""),
            texto=mejor.group(0),
            inicio=mejor.start(),
            fin=mejor.end(),
        )

    def _buscar_generica(self, texto: str, desde: int) -> Mencion | None:
        coincidencia = self._patron_generico.search(texto, desde)
        if coincidencia is None:
            return None
        generico = coincidencia.group(1)
        return Mencion(
            tag=None,
            texto=coincidencia.group(0),
            inicio=coincidencia.start(),
            fin=coincidencia.end(),
            generico=generico,
        )


def _alias_ordenados(alias: dict[str, str]) -> list[tuple[str, str]]:
    """Los alias, del más largo al más corto, con un nombre de grupo cada uno.

    El más largo primero: "la del medio" tiene que ganarle a "la dos".
    """
    return [
        (f"a{indice}", nombre) for indice, nombre in enumerate(sorted(alias, key=len, reverse=True))
    ]


def _alias_partido(nombre: str) -> str:
    """El alias, con los espacios entre palabras opcionales.

    El ASR a veces junta las palabras del alias ("Lados" por "la dos",
    "elfiltro" por "el filtro"). Con los espacios opcionales el alias se
    reconoce igual, y el equipo es el mismo: lo que el técnico dijo no
    cambia por cómo lo escribió el decodificador.
    """
    return re.escape(nombre).replace(r"\ ", r"\s*")


def _desde_planta(
    equipos: list[dict[str, Any]],
    alias_base: dict[str, list[str]],
    tipos_base: dict[str, str],
) -> tuple[dict[str, list[str]], dict[str, str]]:
    alias = {tag: list(nombres) for tag, nombres in alias_base.items()}
    tipos = dict(tipos_base)
    for equipo in equipos:
        tag = equipo.get("tag")
        if not tag:
            continue
        nombres = [n for n in (equipo.get("nombres") or []) if n]
        if nombres:
            # Los alias de la API mandan; se suman los predeterminados que falten.
            alias[tag] = list(dict.fromkeys(nombres + alias.get(tag, [])))
        tipos[tag] = equipo.get("tipo") or tipos.get(tag, "")
    return alias, tipos


def desde_api(planta: dict[str, Any]) -> ResolutorNombres:
    """Construye el resolutor con la respuesta de ``/api/planta``."""
    return ResolutorNombres(planta.get("equipos") or [])


def _distancia_edicion(uno: str, otro: str) -> int:
    """Distancia de Levenshtein entre dos strings.

    Se usa para reconocer alias garblados por el ASR: "aboma" por "bomba"
    tiene distancia 1, "lados" por "dos" tiene distancia 2, etc.
    """
    if uno == otro:
        return 0
    if not uno:
        return len(otro)
    if not otro:
        return len(uno)
    anterior = list(range(len(otro) + 1))
    for i, letra in enumerate(uno, start=1):
        actual = [i]
        for j, otra in enumerate(otro, start=1):
            coste = 0 if letra == otra else 1
            valor = min(
                actual[-1] + 1,
                anterior[j] + 1,
                anterior[j - 1] + coste,
            )
            if i > 1 and j > 1 and letra == otro[j - 2] and uno[i - 2] == otra:
                valor = min(valor, anterior[j - 2] + 1)
            actual.append(valor)
        anterior = actual
    return anterior[-1]
