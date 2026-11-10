"""Utilidades de texto en español para el parte por voz."""

from __future__ import annotations

import re
import unicodedata

# Ruido del ASR: cuando oye "reserva", a veces escribe "reservoir"; cuando
# oye "cero", a veces escribe "ceo".
#
# El "ceo" era un defecto del sistema: la transcripción real del decodificador
# decía "el filtro A, medio ceo, coma 3 de diferencial" y el diferencial se
# publicaba como 3,0 kg/cm2 (con la observación "diferencial alto"), cuando
# la verdad de campo era 0,3: el filtro se escalaba donde no había novedad.
# La palabra "ceo" no existe en el español de la planta, así que el arreglo
# es seguro. Salió en 7 notas de 5 corridas distintas.
_RUIDO_ASR = {
    "reservoir": "reserva",
    "ceo": "cero",
}


# El ASR escribe la temperatura con el símbolo del grado: "59 °C", "59 ºC",
# "59°". Al normalizar, el símbolo se perdía y quedaba "59 c" / "59 oc", que
# no es ninguna unidad. Se cambia por la palabra que el técnico dice
# ("grados") antes de borrar los símbolos sueltos.
_GRADO_CELSIUS = re.compile(r"[°º]\s*c(?![a-z])", re.IGNORECASE)
# El grado suelto detrás de un número ("59°") también son grados: el "º"
# suelto no, porque en español es el ordinal ("el 2º filtro").
_GRADO_SUELTO = re.compile(r"(?<=\d)\s*°(?![a-zº])")


def normalizar(texto: str) -> str:
    """Minúsculas, sin tiltes y con espacios colapsados."""
    # El símbolo del grado se busca **antes** de descomponer: el NFKD
    # convierte "º" en "o" y "59 ºC" quedaría "59 oc".
    limpio = _GRADO_CELSIUS.sub(" grados ", texto.lower())
    descompuesto = unicodedata.normalize("NFKD", limpio)
    sin_tildes = "".join(c for c in descompuesto if not unicodedata.combining(c))
    limpio = _GRADO_SUELTO.sub(" grados ", sin_tildes)
    limpio = limpio.replace("°", "")
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

# La coma de estas frases **no** separa dos ideas: es una sola observación
# ("la bomba está armada y no arranca", "la bomba está parada y en reserva").
# Si se parte por la coma, el extractor se queda con "parada" (que además está
# dentro de "disparada") y el equipo se clasifica como normal cuando es una
# falla. Se protege la coma antes de cortar y se devuelve después.
_UNIDO = "\x01"
_FRASES_UNIDAS = re.compile(
    r"\b(disparad[ao]|parad[ao])\s*,\s*"
    r"(no arranca|no prende|no enciende|en reserva|por mantenimiento)\b",
    re.IGNORECASE,
)


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
        protegido = _FRASES_UNIDAS.sub(rf"\1{_UNIDO}\2", trozo)
        partes = re.split(r"(?<!\d),(?!\d)", protegido)
        unidas: list[str] = []
        for parte in partes:
            # La frase protegida ("parada, en reserva") es una observación
            # **completa**: no se come la cláusula siguiente. Si la comiera,
            # el equipo de la medición siguiente ("la dos está en 1630 de
            # descarga") quedaba escondido detrás del equipo anterior y la
            # medición se publicaba en el equipo equivocado.
            if (
                unidas
                and not _tiene_cifra(unidas[-1])
                and _tiene_cifra(parte)
                and _UNIDO not in unidas[-1]
            ):
                unidas[-1] = f"{unidas[-1]}, {parte}"
            else:
                unidas.append(parte)
        resultado.extend(
            parte.replace(_UNIDO, ", ").strip() for parte in unidas if parte.strip()
        )
    return resultado


def _tiene_cifra(texto: str) -> bool:
    return any(caracter.isdigit() for caracter in texto)
