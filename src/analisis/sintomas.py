"""Vocabulario de síntomas: qué variable se desvía y en qué sentido.

La planta y el manual no hablan el mismo idioma. La planta lee
``presion_diferencial`` y ``goteo_empaquetadura``; el manual escribe "presión
diferencial alta" y "goteo por encima de 10 gotas/min". Este módulo traduce
entre los dos y, sobre todo, distingue el sentido de la desvía: sin eso,
"diferencial alto" (cartuchos colmatados) y "diferencial bajo" (cartucho roto)
serían la misma falla.
"""

from __future__ import annotations

import re

from tools.manuales import normalizar

# Palabras que dicen que algo subió o que algo bajó.
ALTO = (
    "alta",
    "alto",
    "aument",
    "sube",
    "subiendo",
    "crece",
    "creciendo",
    "en alza",
    "alza",
    "supera",
    "mayor",
    "encima",
    "por encima",
    "mas de",
    "calient",
)
BAJO = (
    "baja",
    "bajo",
    "cae",
    "caida",
    "cayendo",
    "menor",
    "disminu",
    "reduce",
    "por debajo",
    "menos de",
    "frio",
)

# Variables de la planta y las palabras del manual que las nombran. El orden
# importa: primero va la más específica.
VARIABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("presion_diferencial", ("presion diferencial", "diferencial")),
    ("presion_succion", ("presion de succion", "succion")),
    ("presion_descarga", ("presion de descarga",)),
    ("temperatura_rodamiento", ("temperatura de rodamiento", "rodamiento")),
    ("temperatura_aceite", ("temperatura de aceite", "temperatura del aceite")),
    ("vibracion", ("vibracion",)),
    ("caudal", ("caudal",)),
    ("nivel_aceite", ("nivel de aceite", "nivel del aceite")),
)

# Nombres de variable del catálogo y del parte, al nombre canónico.
VARIABLES_CANONICAS = {
    "presion_diferencial_local": "presion_diferencial",
    "presion_descarga_local": "presion_descarga",
    "temperatura_rodamiento_ir": "temperatura_rodamiento",
    "temperatura_motor_ir": "temperatura_motor",
    "nivel_aceite_visor": "nivel_aceite",
    "estado_correas": "correas",
    "goteo_empaquetadura": "goteo_empaquetadura",
    "ruido": "ruido",
    "nivel": "nivel",
    "vibracion": "vibracion",
    "caudal": "caudal",
    "presion_succion": "presion_succion",
    "presion_descarga": "presion_descarga",
    "presion_diferencial": "presion_diferencial",
    "temperatura_aceite": "temperatura_aceite",
}


def normalizar_variable(variable: str) -> str:
    """``presion_diferencial_local`` → ``presion_diferencial``."""
    return VARIABLES_CANONICAS.get(variable, variable)


def variable_de_texto(texto: str) -> str | None:
    """La variable que nombra un texto del manual, si nombra alguna."""
    normal = normalizar(texto)
    for variable, palabras in VARIABLES:
        if any(palabra in normal for palabra in palabras):
            return variable
    return None


def direccion_de_texto(texto: str) -> str | None:
    """``"alto"``, ``"bajo"`` o ``None`` según cómo se desvía lo que dice el texto."""
    normal = normalizar(texto)
    alto = any(palabra in normal for palabra in ALTO)
    bajo = any(palabra in normal for palabra in BAJO)
    if alto and not bajo:
        return "alto"
    if bajo and not alto:
        return "bajo"
    return None


def direcciones_de_texto(texto: str) -> list[tuple[str, str]]:
    """En qué sentido se desvía cada variable que nombra el texto.

    Se lee cláusula por cláusula: "Presión diferencial alta y en aumento" es
    una desviación alta del diferencial, mientras que "Caída brusca de la
    presión diferencial" es una baja.
    """
    salida: list[tuple[str, str]] = []
    for clausula in re.split(r"[.;,]\s*", texto):
        variable = variable_de_texto(clausula)
        if variable is None:
            continue
        direccion = direccion_de_texto(clausula)
        if direccion is None:
            continue
        if (variable, direccion) not in salida:
            salida.append((variable, direccion))
    return salida
