"""Normalización y tokenización para el corpus documental.

El mismo texto se usa para el índice léxico y para el embedder. Las dos vías
tienen que ver el corpus de la misma forma: minúsculas, sin tildes y con los
términos truncados a seis caracteres (``válvulas`` y ``válvula`` comparten
``valvul``). Los números y los códigos se conservan.
"""

from __future__ import annotations

import re
import unicodedata

# Palabras vacías del español que no discriminan en una consulta de planta.
PALABRAS_VACIAS = frozenset(
    (
        "a al algo como con cual cuales cuando de del el ella en es esta este esto hay "
        "la las le lo los mas me mi muy no o otra otras para pero por que se si sin "
        "sobre su sus un una uno y ya ante hace hacer tiene son fue ser puede qué cuál "
        "cuáles cómo cuánto cuántos dónde"
    ).split()
)

_LARGO_TERMINO = 6


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y con espacios colapsados."""
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    sin_tildes = "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", sin_tildes).strip()


def tokenizar(texto: str) -> list[str]:
    """Términos útiles, truncados para que las variantes morfológicas se junten."""
    return [
        palabra[:_LARGO_TERMINO]
        for palabra in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", normalizar(texto))
        if palabra not in PALABRAS_VACIAS
    ]
