"""Lectura de números dichos en español de campo.

Cubre los estilos que usa el operador de la planta:

* ``mil seiscientos treinta`` -> 1630
* ``ciento quince`` -> 115
* ``uno coma tres`` / ``uno con dos`` -> 1.3 / 1.2
* ``cero nueve`` / ``uno ocho`` -> 0.9 / 1.8
* ``dieciocho`` -> 18 (el llamador decide si es ambiguo)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from planta.texto import normalizar
from planta.unidades import normalizar_unidad

UNIDADES = {
    "cero": 0,
    "uno": 1,
    "una": 1,
    "un": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veinte": 20,
    "veintiuno": 21,
    "veintiun": 21,
    "veintiuna": 21,
    "veintidos": 22,
    "veintitres": 23,
    "veinticuatro": 24,
    "veinticinco": 25,
    "veintiseis": 26,
    "veintisiete": 27,
    "veintiocho": 28,
    "veintinueve": 29,
}
DECENAS = {
    "treinta": 30,
    "cuarenta": 40,
    "cincuenta": 50,
    "sesenta": 60,
    "setenta": 70,
    "ochenta": 80,
    "noventa": 90,
}
CENTENAS = {
    "cien": 100,
    "ciento": 100,
    "doscientos": 200,
    "trescientos": 300,
    "cuatrocientos": 400,
    "quinientos": 500,
    "seiscientos": 600,
    "setecientos": 700,
    "ochocientos": 800,
    "novecientos": 900,
}
DIGITOS = {
    "cero",
    "uno",
    "dos",
    "tres",
    "cuatro",
    "cinco",
    "seis",
    "siete",
    "ocho",
    "nueve",
}
MARCADORES_DECIMALES = {"coma", "con", "punto"}

# "un" y "una" son artículos la mayoría de las veces: "hace un golpeteo
# metálico", "tiene un ruido raro de rodamiento". Leídos como el número uno
# arruinaban la nota: en "tiene un cubilo claro de rodamiento" se publicaba
# una temperatura de 1,0 °C que **tapaba** la lectura buena de la misma
# fila, y en "la un está en 115 kilos" el artículo se comía la medición.
# Sólo son el número cuando los une ("cincuenta y un grados"), cuando lo que
# sigue es un decimal ("un con tres") o cuando lo que sigue es una unidad
# ("un metro", "un grado").
_ARTICULOS = ("un", "una")
# El ASR a veces abre un espacio después de la coma del decimal: "0, 4 de
# diferencial" por "cero coma cuatro". Sin contemplarlo, el cero se leía
# como el número y el 0,4 se perdía: el diferencial quedaba en 0 y disparaba
# la alarma de presión baja (el equipo se escalaba donde la verdad era que no
# había novedad). El separador con espacio se lee igual que el pegado.
TOKEN = re.compile(r"\d+(?:[.,]\s?\d+)?|[a-z]+")

# El ASR a veces escribe el decimal con un guion: "1-3 de diferencial" por
# "uno coma tres". Sólo se toma un dígito de cada lado: "1600-1620" es un
# rango, no un decimal.
_DECIMAL_CON_GUION = re.compile(r"(?<!\d)(\d)-(\d)(?!\d)")

# El ASR escribe los millares con punto o coma: "1.600 de descarga" por
# "mil seiscientos". En la planta los decimales se dicen con un solo dígito
# ("uno coma tres", "cero nueve"), así que un número con **tres** dígitos
# detrás del separador es un millar, no un decimal. El "0" adelante se
# excluye: "0.900" es cero coma nueve.
_MILLARES = re.compile(r"^([1-9]\d{0,2})[.,]\s?(\d{3})$")


@dataclass(frozen=True)
class NumeroDicho:
    """Un número encontrado en el texto."""

    valor: float
    decimal: bool
    dijo: str


def _entero(tokens: list[str], inicio: int) -> tuple[int | None, int]:
    total = 0
    parcial = 0
    j = inicio
    consumido = False

    while j < len(tokens):
        token = tokens[j]
        if token == "y":
            # Solo se salta la "y" si sigue un número ("treinta y un").
            # En "cinco y medio" se corta acá para leer el medio aparte.
            if j + 1 < len(tokens) and (
                tokens[j + 1] in UNIDADES or tokens[j + 1] in DECENAS or tokens[j + 1] in CENTENAS
            ):
                j += 1
                continue
            break
        if token in UNIDADES:
            parcial += UNIDADES[token]
        elif token in DECENAS:
            parcial += DECENAS[token]
        elif token in CENTENAS:
            parcial += CENTENAS[token]
        elif token == "mil":
            parcial = parcial or 1
            total += parcial * 1000
            parcial = 0
        else:
            break
        consumido = True
        j += 1

    if not consumido:
        return None, inicio
    return total + parcial, j


def _fraccion(tokens: list[str], inicio: int) -> tuple[float | None, int]:
    if inicio >= len(tokens):
        return None, inicio
    token = tokens[inicio]
    if re.fullmatch(r"\d+", token):
        return int(token), inicio + 1
    valor, fin = _entero(tokens, inicio)
    return valor, fin


def _es_articulo(tokens: list[str], indice: int) -> bool:
    """Si el "un"/"una" de esta posición es un artículo y no el número uno."""
    if indice > 0 and tokens[indice - 1] == "y":
        # "cincuenta y un grados": es el número.
        return False
    siguiente = tokens[indice + 1] if indice + 1 < len(tokens) else ""
    if siguiente in MARCADORES_DECIMALES:
        # "un con tres": es el número.
        return False
    if normalizar_unidad(siguiente) or siguiente == "diferencial":
        # "un metro", "un grado": es el número.
        return False
    return True


def parsear_numero(texto: str, *, articulos: bool = True) -> NumeroDicho | None:
    """Devuelve el primer número dicho en ``texto``, o ``None``.

    Con ``articulos`` (el valor por defecto) el "un"/"una" de "hace un
    golpeteo metálico" se lee como artículo y no como el número uno.
    Las **cantidades** se leen con ``articulos=False``: ahí el "un" sí es la
    cantidad ("usé un kit de empaquetadura" es un kit).
    """
    tokens = TOKEN.findall(_DECIMAL_CON_GUION.sub(r"\1,\2", normalizar(texto)))

    for i, token in enumerate(tokens):
        if re.fullmatch(r"\d+(?:[.,]\s?\d+)?", token):
            millares = _MILLARES.match(token)
            if millares:
                # "1.600" es mil seiscientos, no uno con seiscientos.
                return NumeroDicho(
                    float(int(millares.group(1) + millares.group(2))),
                    decimal=False,
                    dijo=token,
                )
            compacto = token.replace(" ", "")
            valor = float(compacto.replace(",", "."))
            # "50 y un grados": el ASR escribe la parte entera en dígitos y la
            # que sigue en palabras ("cincuenta y un grados" -> "50 y un
            # grados"). Sin esto se leía 50 y la temperatura quedaba un grado
            # abajo: el operador no confirmaba el borrador. Sólo se corrige
            # cuando lo que sigue es una unidad: en "3 horas y un minuto" el
            # "un" es otra cosa.
            if i + 3 < len(tokens) and tokens[i + 1] == "y" and tokens[i + 2] in ("un", "uno"):
                if normalizar_unidad(tokens[i + 3]):
                    return NumeroDicho(
                        valor + 1,
                        decimal=False,
                        dijo=f"{token} y {tokens[i + 2]}",
                    )
            # "0 con 3": el ASR escribe el decimal con la parte entera en
            # dígitos y la fracción en palabras ("cero con tres" -> "0 con
            # 3"). Sin esto se leía 0 y el diferencial de 0,3 quedaba por
            # debajo de la alarma: el filtro se escalaba sin motivo.
            if " " not in compacto and i + 2 < len(tokens):
                if tokens[i + 1] in MARCADORES_DECIMALES:
                    fraccion, fin = _fraccion(tokens, i + 2)
                    if fraccion is not None:
                        digitos = len(str(int(fraccion)))
                        return NumeroDicho(
                            valor + fraccion / (10**digitos),
                            decimal=True,
                            dijo=" ".join(tokens[i:fin]),
                        )
            return NumeroDicho(
                valor, decimal="," in compacto or "." in compacto, dijo=token
            )

        # "cero nueve", "uno ocho", "uno dos": decimal dígito a dígito.
        if i + 1 < len(tokens) and token in DIGITOS and tokens[i + 1] in DIGITOS:
            valor = UNIDADES[token] + UNIDADES[tokens[i + 1]] / 10
            return NumeroDicho(valor, decimal=True, dijo=f"{token} {tokens[i + 1]}")

        if articulos and token in _ARTICULOS and _es_articulo(tokens, i):
            # Es el artículo de "hace un golpeteo metálico": no es una
            # medición. Se sigue buscando el número de verdad.
            continue

        entero, j = _entero(tokens, i)
        if entero is None:
            continue

        # "uno coma tres", "uno con dos", "uno punto cinco"
        if j < len(tokens) and tokens[j] in MARCADORES_DECIMALES:
            fraccion, fin = _fraccion(tokens, j + 1)
            if fraccion is not None:
                digitos = len(str(int(fraccion)))
                valor = entero + fraccion / (10**digitos)
                return NumeroDicho(valor, decimal=True, dijo=" ".join(tokens[i:fin]))

        # "uno y medio"
        if j + 1 < len(tokens) and tokens[j] == "y" and tokens[j + 1] == "medio":
            return NumeroDicho(entero + 0.5, decimal=True, dijo=" ".join(tokens[i : j + 2]))

        return NumeroDicho(float(entero), decimal=False, dijo=" ".join(tokens[i:j]))

    return None
