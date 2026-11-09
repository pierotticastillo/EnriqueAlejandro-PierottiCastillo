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


def parsear_numero(texto: str) -> NumeroDicho | None:
    """Devuelve el primer número dicho en ``texto``, o ``None``."""
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
            return NumeroDicho(
                valor, decimal="," in compacto or "." in compacto, dijo=token
            )

        # "cero nueve", "uno ocho", "uno dos": decimal dígito a dígito.
        if i + 1 < len(tokens) and token in DIGITOS and tokens[i + 1] in DIGITOS:
            valor = UNIDADES[token] + UNIDADES[tokens[i + 1]] / 10
            return NumeroDicho(valor, decimal=True, dijo=f"{token} {tokens[i + 1]}")

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
