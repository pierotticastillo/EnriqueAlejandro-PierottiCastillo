"""Prueba el extractor con textos reales de la planta simulada.

    python scripts/probar_extractor.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

from planta.nombre_resolver import desde_api  # noqa: E402
from planta.parte_extractor import ExtractorParte  # noqa: E402

EQUIPOS = [
    {"tag": "B-101", "tipo": "bomba",
     "nombres": ["la uno", "la 1", "la de la punta", "la del portón", "bomba uno"],
     "manometro_local": {"unidad": "kg/cm2"}},
    {"tag": "B-102", "tipo": "bomba",
     "nombres": ["la dos", "la 2", "la del medio", "bomba dos"],
     "manometro_local": {"unidad": "psi"}},
    {"tag": "B-103", "tipo": "bomba",
     "nombres": ["la tres", "la 3", "la del fondo", "la de reserva", "bomba tres"],
     "manometro_local": {"unidad": "bar"}},
    {"tag": "F-201A", "tipo": "filtro",
     "nombres": ["el filtro A", "el primario", "el de la izquierda", "el grueso"],
     "manometro_local": {"unidad": "kg/cm2"}},
    {"tag": "F-201B", "tipo": "filtro",
     "nombres": ["el filtro B", "el secundario", "el de la derecha", "el fino"],
     "manometro_local": {"unidad": "kg/cm2"}},
    {"tag": "TK-301", "tipo": "tanque",
     "nombres": ["el pulmón", "el tanque", "el tanque pulmón"],
     "manometro_local": {"unidad": "m"}},
    {"tag": "FT-401", "tipo": "caudalimetro",
     "nombres": ["el caudalímetro", "el colector"],
     "manometro_local": {"unidad": "m3/d"}},
]

SENSORES = [
    {"tag": "B-101", "variable": "presion_succion", "unidad": "kg/cm2", "alarma_baja": 0.9},
    {"tag": "B-101", "variable": "presion_descarga", "unidad": "kg/cm2",
     "alarma_baja": 95, "alarma_alta": 125},
    {"tag": "B-101", "variable": "temperatura_aceite", "unidad": "degC",
     "alarma_alta": 80, "disparo": 90},
    {"tag": "B-101", "variable": "vibracion", "unidad": "mm/s",
     "alarma_alta": 7.1, "disparo": 11.2},
    {"tag": "F-201A", "variable": "presion_diferencial", "unidad": "kg/cm2",
     "alarma_baja": 0.15, "alarma_alta": 1.5},
    {"tag": "F-201B", "variable": "presion_diferencial", "unidad": "kg/cm2",
     "alarma_baja": 0.15, "alarma_alta": 1.5},
    {"tag": "FT-401", "variable": "caudal", "unidad": "m3/d", "alarma_baja": 2200},
]

# Transcripciones reales del ASR en la corrida de corrección.
TEXTOS = [
    "El de la derecha me dio 09 de diferencial.",
    "El filtro B medio 09 de diferencial. El pulmón está en 5 metros.",
    "La 1 está en 112 kilogramos de descarga el rodamiento, medio 59 grados con la pistola."
    " Ambient la bomba está en 1600 PS y de descarga, el filtro a medio 1,3 de",
    "¿Listo? ¿La bomba está parada en Reservoir?",
    "La del medio está en 1610 libras de descarga. El filtro A medió 1,3 de diferencial.",
]


def main() -> int:
    planta = {"equipos": EQUIPOS, "sensores": SENSORES}
    resolver = desde_api(planta)
    extractor = ExtractorParte(planta, resolver)
    for texto in TEXTOS:
        print("=" * 78)
        print(texto)
        extraccion = extractor.extraer(texto)
        for fila in extraccion.filas:
            print(
                f"  fila: tag={fila.tag} var={fila.variable} valor={fila.valor} "
                f"{fila.unidad} obs={fila.observacion!r}"
            )
        for pregunta in extraccion.preguntas:
            print(
                f"  PREGUNTA: {pregunta.texto} opciones={pregunta.opciones} "
                f"tags={pregunta.tags} filas={pregunta.filas}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
