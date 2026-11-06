"""Normalización y conversión de unidades de la planta."""

from __future__ import annotations

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
