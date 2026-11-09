"""Normalización y conversión de unidades de la planta."""

from __future__ import annotations

import re
from typing import Any

from planta.texto import normalizar

# Factor para llevar cada unidad de presión a kg/cm2.
A_KG_CM2 = {
    "kg/cm2": 1.0,
    "psi": 1.0 / 14.2233,
    "bar": 0.980665,
}

SINONIMOS = {
    "kilos": "kg/cm2",
    "kilo": "kg/cm2",
    "kilogramos": "kg/cm2",
    "kilogramo": "kg/cm2",
    "kg": "kg/cm2",
    "kg/cm2": "kg/cm2",
    "kg/cm²": "kg/cm2",
    "psi": "psi",
    "pe ese i": "psi",
    # El ASR a veces separa las letras: "1600 PS y de descarga".
    "ps": "psi",
    # Y a veces las junta mal: "1600 preses y de descarga".
    "preses": "psi",
    "pres": "psi",
    "libras": "psi",
    "libra": "psi",
    "bar": "bar",
    "bares": "bar",
    "grados": "degC",
    "grado": "degC",
    "celsius": "degC",
    "degc": "degC",
    "metros": "m",
    "metro": "m",
    "m": "m",
    "milimetros por segundo": "mm/s",
    "mm/s": "mm/s",
    "metros cubicos por dia": "m3/d",
    "m3/d": "m3/d",
    "gotas por minuto": "gotas/min",
    "gotas/min": "gotas/min",
}

# Unidades que el operador puede **decir** en voz alta, de la más larga a la
# más corta. Al leer una frase hay que buscar primero la más larga: "gotas por
# minuto" antes que "gotas", "metros cubicos por dia" antes que "metros". Si
# una unidad no está en esta lista, la corrección del operador llega sin
# unidad y aterriza sobre la fila equivocada (una corrección en gotas/min
# pisaba la presión de descarga).
UNIDADES_HABLADAS = (
    "metros cubicos por dia",
    "milimetros por segundo",
    "gotas por minuto",
    "kilogramos",
    "kilogramo",
    "bares",
    "grados",
    "metros",
    "kilos",
    "libras",
    "libra",
    "kilo",
    "psi",
    "bar",
    "m3/d",
    "mm/s",
    "gotas/min",
    "m",
)

# Variables que siempre se expresan en una unidad fija.
UNIDAD_FIJA = {
    "presion_diferencial": "kg/cm2",
    "presion_succion": "kg/cm2",
    "temperatura_aceite": "degC",
    "temperatura_rodamiento_ir": "degC",
    "vibracion": "mm/s",
    "nivel": "m",
    "goteo_empaquetadura": "gotas/min",
    "caudal": "m3/d",
}


def normalizar_unidad(texto: str | None) -> str | None:
    if not texto:
        return None
    clave = normalizar(texto)
    return SINONIMOS.get(clave)


def unidad_de_frase(texto: str | None) -> str | None:
    """La unidad que el operador dijo en una frase, si dijo alguna.

    Se buscan las unidades habladas de la más larga a la más corta y con
    límites de palabra: "5 m" es un nivel, pero "mil seiscientos" no es un
    metro ni medio kilo.
    """
    if not texto:
        return None
    norm = normalizar(texto)
    for frase in UNIDADES_HABLADAS:
        if re.search(rf"(?<!\w){re.escape(frase)}(?!\w)", norm):
            return SINONIMOS.get(frase)
    return None


def unidad_manometro(tag: str, planta: dict[str, Any]) -> str | None:
    """Unidad del manómetro local declarada por ``/api/planta``."""
    for equipo in planta.get("equipos", []):
        if equipo.get("tag") == tag:
            manometro = equipo.get("manometro_local") or {}
            return normalizar_unidad(manometro.get("unidad"))
    return None


def unidad_canonica(variable: str, tag: str, planta: dict[str, Any]) -> str | None:
    """Unidad en la que se guarda una variable para un equipo."""
    if variable in UNIDAD_FIJA:
        return UNIDAD_FIJA[variable]
    if variable == "presion_descarga_local":
        return unidad_manometro(tag, planta) or "kg/cm2"
    return None


def convertir(valor: float, desde: str | None, hasta: str | None) -> float:
    """Convierte presiones entre kg/cm2, psi y bar; el resto no se toca."""
    if not desde or not hasta or desde == hasta:
        return valor
    if desde in A_KG_CM2 and hasta in A_KG_CM2:
        return valor * A_KG_CM2[desde] / A_KG_CM2[hasta]
    return valor
