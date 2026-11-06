"""Utilidades de texto en español para el parte por voz."""

from __future__ import annotations

import re
import unicodedata

# Ruido del ASR: cuando oye "reserva", a veces escribe "reservoir".
_RUIDO_ASR = {
    "reservoir": "reserva",
}


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y con espacios colapsados."""
    descompuesto = unicodedata.normalize("NFKD", texto)
    sin_tildes = "".join(c for c in descompuesto if not unicodedata.combining(c))
    limpio = sin_tildes.lower().replace("°", "").replace("º", "")
    limpio = re.sub(r"\s+", " ", limpio).strip()
    for ruido, correccion in _RUIDO_ASR.items():
        limpio = re.sub(rf"(?<!\w){ruido}(?!\w)", correccion, limpio)
    return limpio


def oraciones(texto: str) -> list[str]:
    """Divide en oraciones por ``.``, ``;``, ``!`` o ``?``.

    No corta un punto o una coma que esté entre dígitos, para no romper
    decimales cuando el transcriptor devuelve números escritos.
    """
    partes = re.split(r"(?<!\d)[.;!?]+(?!\d)", texto)
    return [parte.strip() for parte in partes if parte.strip()]


# El ASR de faster-whisper devuelve números en dígitos y sin puntuación. Para
# que el extractor no pierda mediciones, se corta después de la magnitud
# ("... de descarga la uno ...", "... 55 grados el rodamiento ...") cuando
# sigue el comienzo de otra cláusula. No se toca el texto ya puntuado.
_BORDE_MEDICION = re.compile(
    r"\b(?:descarga|diferencial|grados|metros|metro|bares|bar|psi|kilos|kilogramos|caudal|nivel)\b"
    r"(?=\s+(?:la|el|las|los|le|me|se)\b)"
)


_CORTE = "\x00"


def clausulas(oracion: str) -> list[str]:
    """Divide una oración por comas que separan ideas, no decimales.

    Además separa mediciones pegadas por el ASR (sin puntos ni comas), por
    ejemplo ``"... de descarga la uno está en ..."``. El corte interno usa un
    centinela para no partir decimales del ASR como ``0.9``.

    Una coma delante de un número no se toma como corte: el ASR la mete a
    veces en medio de la misma medición ("el rodamiento, medio 59 grados"), y
    partir ahí deja la variable sin su cue y se pierde la fila.

    El corte por magnitud ("... de descarga la uno ...") va **antes** que el de
    comas: si no, la coma del ASR separa el nombre del equipo ("el
    rodamiento") de su medida y la fila se pierde.
    """
    por_magnitud = [
        trozo.strip()
        for trozo in _BORDE_MEDICION.sub(
            lambda m: f"{m.group(0)}{_CORTE}", oracion
        ).split(_CORTE)
        if trozo.strip()
    ]

    resultado: list[str] = []
    for trozo in por_magnitud:
        partes = re.split(r"(?<!\d),(?!\d)", trozo)
        unidas: list[str] = []
        for parte in partes:
            if unidas and not _tiene_cifra(unidas[-1]) and _tiene_cifra(parte):
                unidas[-1] = f"{unidas[-1]}, {parte}"
            else:
                unidas.append(parte)
        resultado.extend(parte.strip() for parte in unidas if parte.strip())
    return resultado


def _tiene_cifra(texto: str) -> bool:
    return any(caracter.isdigit() for caracter in texto)
