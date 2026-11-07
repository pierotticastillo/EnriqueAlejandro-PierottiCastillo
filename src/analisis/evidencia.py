"""Evidencia de un escalado: parte confirmado, fotos y tendencia de sensores.

La evidencia es lo que el sistema tiene para sostener una conclusión: las
filas del parte que el operador confirmó, las fotos que tomó y la tendencia
que muestra el historiador. Nada de esto es texto libre del operador: una
nota puede pedir que se apruebe una orden sin planificador, y eso no cambia
qué falla hay.

Cada fragmento dice qué variable se desvía y en qué sentido, para que el
catálogo del manual se pueda comparar con lo que se vio en el campo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from analisis.sintomas import direccion_de_texto, normalizar_variable
from rag.texto import tokenizar

# "B-102 vibracion: 8.3 mm/s" — el resumen de un evento de sensor o de parte.
_RESUMEN_MEDICION = re.compile(
    r"^\s*(?P<tag>\S+)\s+(?P<variable>[A-Za-z_]+)\s*:\s*"
    r"(?P<valor>-?\d+(?:[.,]\d+)?)"
)


@dataclass(frozen=True)
class Fragmento:
    """Un pedazo de evidencia con su variable y su desvío."""

    variable: str
    valor: float | None = None
    unidad: str | None = None
    observacion: str | None = None
    origen: str = "parte"

    @property
    def texto(self) -> str:
        partes = [self.variable]
        if self.observacion:
            partes.append(self.observacion)
        elif self.valor is not None:
            partes.append(f"{self.valor:g} {self.unidad or ''}".strip())
        return " · ".join(partes)


@dataclass(frozen=True)
class Evidencia:
    """Todo lo que respalda el análisis de un escalado."""

    equipo: str
    fragmentos: tuple[Fragmento, ...]
    fotos: tuple[str, ...]
    tendencia: tuple[str, ...]
    resumen: str
    tokens: frozenset[str]
    direcciones: dict[str, str]

    @property
    def texto(self) -> str:
        return " ".join(fragmento.texto for fragmento in self.fragmentos)

    @property
    def descripciones(self) -> tuple[str, ...]:
        """Las descripciones legibles de la evidencia, para el informe."""
        return tuple(fragmento.texto for fragmento in self.fragmentos)

    @property
    def solo_sensor(self) -> bool:
        """¿La evidencia viene sólo de una alarma de sensor?

        Una alarma de un solo sensor es un **síntoma**, no un diagnóstico: la
        misma desviación puede ser consecuencia de varias fallas. Lo que vio el
        operador en el parte (una observación con su firma) sí es evidencia
        directa. El umbral para dar por identificada una falla depende de esto.
        """
        return not any(fragmento.origen == "parte" for fragmento in self.fragmentos)


def reunir_parte(
    *,
    equipo: str,
    filas: list[object],
    fotos: list[str] | None = None,
    resumen: str = "",
    planta: dict | None = None,
) -> Evidencia:
    """Evidencia de un parte confirmado: sus filas, sus fotos y su tendencia."""
    fragmentos: list[Fragmento] = []
    for fila in filas:
        if getattr(fila, "tag", None) != equipo:
            continue
        variable = normalizar_variable(str(getattr(fila, "variable", "") or ""))
        if not variable:
            continue
        fragmentos.append(
            Fragmento(
                variable=variable,
                valor=getattr(fila, "valor", None),
                unidad=getattr(fila, "unidad", None),
                observacion=getattr(fila, "observacion", None),
                origen="parte",
            )
        )
    return _armar(
        equipo=equipo,
        fragmentos=fragmentos,
        fotos=fotos or [],
        resumen=resumen,
        planta=planta,
    )


def reunir_texto(
    *,
    equipo: str,
    texto: str,
    fotos: list[str] | None = None,
    resumen: str = "",
    planta: dict | None = None,
) -> Evidencia:
    """Evidencia de un texto (una nota de seguridad, por ejemplo)."""
    fragmentos = [
        Fragmento(
            variable="texto",
            observacion=texto.strip() or None,
            origen="texto",
        )
    ]
    return _armar(
        equipo=equipo,
        fragmentos=fragmentos,
        fotos=fotos or [],
        resumen=resumen,
        planta=planta,
    )


def _armar(
    *,
    equipo: str,
    fragmentos: list[Fragmento],
    fotos: list[str],
    resumen: str,
    planta: dict | None,
) -> Evidencia:
    completados = list(fragmentos)
    lectura = _medicion_del_resumen(resumen)
    if lectura is not None:
        variable, valor, unidad = lectura
        completados.append(
            Fragmento(
                variable=variable,
                valor=valor,
                unidad=unidad,
                observacion=f"{valor:g} {unidad}".strip(),
                origen="sensor",
            )
        )
    tokens = set()
    for fragmento in completados:
        tokens.update(tokenizar(fragmento.texto))
    tokens.update(tokenizar(resumen))
    direcciones = _direcciones(completados, equipo, planta)
    return Evidencia(
        equipo=equipo,
        fragmentos=tuple(completados),
        fotos=tuple(fotos),
        tendencia=_tendencia(resumen),
        resumen=resumen,
        tokens=frozenset(tokens),
        direcciones=direcciones,
    )


def _medicion_del_resumen(resumen: str) -> tuple[str, float, str] | None:
    """``B-102 vibracion: 8.3 mm/s`` → variable, valor y unidad."""
    coincidencia = _RESUMEN_MEDICION.match(resumen)
    if not coincidencia:
        return None
    variable = normalizar_variable(coincidencia.group("variable"))
    try:
        valor = float(coincidencia.group("valor").replace(",", "."))
    except ValueError:  # pragma: no cover - el regex ya garantiza un número
        return None
    resto = resumen[coincidencia.end() :]
    unidad = resto.split(";", 1)[0].strip().split(" ", 1)[-1].strip() if " " in resto else ""
    return variable, valor, unidad


def _direcciones(
    fragmentos: list[Fragmento],
    equipo: str,
    planta: dict | None,
) -> dict[str, str]:
    """En qué sentido se desvía cada variable, según el parte y las alarmas."""
    salida: dict[str, str] = {}
    for fragmento in fragmentos:
        if fragmento.variable == "texto":
            continue
        direccion = None
        if fragmento.observacion:
            direccion = direccion_de_texto(fragmento.observacion)
        if direccion is None and fragmento.valor is not None:
            direccion = _direccion_por_valor(fragmento, equipo, planta)
        if direccion is not None:
            salida.setdefault(fragmento.variable, direccion)
    return salida


def _direccion_por_valor(
    fragmento: Fragmento,
    equipo: str,
    planta: dict | None,
) -> str | None:
    """Alto o bajo según la alarma del catálogo de la planta."""
    if not planta:
        return None
    limites = [
        sensor
        for sensor in planta.get("sensores") or []
        if isinstance(sensor, dict)
        and sensor.get("tag") == equipo
        and sensor.get("variable") == fragmento.variable
    ]
    if not limites:
        return None
    alarma_alta = next(
        (float(s["alarma_alta"]) for s in limites if s.get("alarma_alta") is not None), None
    )
    alarma_baja = next(
        (float(s["alarma_baja"]) for s in limites if s.get("alarma_baja") is not None), None
    )
    if alarma_alta is not None and fragmento.valor is not None and fragmento.valor >= alarma_alta:
        return "alto"
    if alarma_baja is not None and fragmento.valor is not None and fragmento.valor <= alarma_baja:
        return "bajo"
    return None


def _tendencia(resumen: str) -> tuple[str, ...]:
    """Las frases de tendencia que el historiador agrega al resumen del evento."""
    if "Tendencia" not in resumen:
        return ()
    return tuple(parte.strip() for parte in resumen.split("Tendencia", 1)[1:] if parte.strip())
