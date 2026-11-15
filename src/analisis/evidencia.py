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
from typing import Any

from analisis.sintomas import direccion_de_texto, normalizar_variable
from rag.texto import tokenizar

# "B-102 vibracion: 8.3 mm/s" — el resumen de un evento de sensor o de parte.
_RESUMEN_MEDICION = re.compile(
    r"^\s*(?P<tag>\S+)\s+(?P<variable>[A-Za-z_]+)\s*:\s*"
    r"(?P<valor>-?\d+(?:[.,]\d+)?)"
)

# El resumen que agrega el historiador. Se conserva también en forma
# estructurada para el manifiesto: fuente, ventana, muestras y extremos.
_TENDENCIA = re.compile(
    r"Tendencia\s+(?P<variable>[A-Za-z_]+)\s+últimas\s+"
    r"(?P<ventana>\d+)\s*h:\s*(?P<muestras>\d+)\s*muestras,\s*"
    r"(?P<primero>-?\d+(?:[.,]\d+)?)→(?P<ultimo>-?\d+(?:[.,]\d+)?)"
    r"(?P<unidad>[^\s(]*)\s*\((?P<direccion>sube|baja|estable)\),\s*"
    r"rango\s+(?P<minimo>-?\d+(?:[.,]\d+)?)–(?P<maximo>-?\d+(?:[.,]\d+)?)"
    r"(?P<unidad_final>[^\s.]*)"
)


@dataclass(frozen=True)
class FilaConfirmada:
    """Una fila confirmada del parte, conservada para el manifiesto."""

    tag: str
    variable: str
    valor: float | None = None
    unidad: str | None = None
    observacion: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "variable": self.variable,
            "valor": self.valor,
            "unidad": self.unidad,
            "observacion": self.observacion,
        }


@dataclass(frozen=True)
class TendenciaEvidencia:
    """Una tendencia consultada, con su ventana y su fuente.

    ``None`` significa que el dato no estaba disponible. El manifiesto lo
    convierte en la marca explícita ``no_disponible``; nunca inventa una
    muestra, una ventana o un promedio.
    """

    variable: str
    tag: str | None = None
    unidad: str | None = None
    desde: str | None = None
    hasta: str | None = None
    ventana: str | None = None
    fuente: str | None = None
    muestras: int | None = None
    minimo: float | None = None
    maximo: float | None = None
    promedio: float | None = None
    tendencia: str | None = None
    resumen: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "variable": self.variable,
            "tag": self.tag,
            "unidad": self.unidad,
            "desde": self.desde,
            "hasta": self.hasta,
            "ventana": self.ventana,
            "fuente": self.fuente,
            "muestras": self.muestras,
            "minimo": self.minimo,
            "maximo": self.maximo,
            "promedio": self.promedio,
            "tendencia": self.tendencia,
            "resumen": self.resumen,
        }


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
    # Provenience del parte y de la ventana de tendencia. Son datos del
    # escalado, no un agregado de la redacción posterior.
    parte_id: str | None = None
    filas_confirmadas: tuple[FilaConfirmada, ...] = ()
    tendencias: tuple[TendenciaEvidencia, ...] = ()

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
    parte_id: str | None = None,
    filas_confirmadas: list[object] | None = None,
    tendencias: list[TendenciaEvidencia] | None = None,
) -> Evidencia:
    """Evidencia de un parte confirmado: sus filas, sus fotos y su tendencia.

    ``filas`` son las filas anómalas que motivan el análisis. El manifiesto
    necesita además el parte confirmado completo, que llega en
    ``filas_confirmadas``.
    """
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
        parte_id=parte_id,
        filas_confirmadas=_filas_confirmadas(
            filas_confirmadas if filas_confirmadas is not None else filas
        ),
        tendencias=tuple(tendencias or []),
    )


def reunir_texto(
    *,
    equipo: str,
    texto: str,
    fotos: list[str] | None = None,
    resumen: str = "",
    planta: dict | None = None,
    tendencias: list[TendenciaEvidencia] | None = None,
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
        tendencias=tuple(tendencias or []),
    )


def _armar(
    *,
    equipo: str,
    fragmentos: list[Fragmento],
    fotos: list[str],
    resumen: str,
    planta: dict | None,
    parte_id: str | None = None,
    filas_confirmadas: list[FilaConfirmada] | None = None,
    tendencias: tuple[TendenciaEvidencia, ...] = (),
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
    tendencias_efectivas = tendencias or _tendencia_estructurada(resumen)
    return Evidencia(
        equipo=equipo,
        fragmentos=tuple(completados),
        fotos=tuple(fotos),
        tendencia=_tendencia(resumen),
        resumen=resumen,
        tokens=frozenset(tokens),
        direcciones=direcciones,
        parte_id=parte_id,
        filas_confirmadas=tuple(filas_confirmadas or []),
        tendencias=tendencias_efectivas,
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


def _filas_confirmadas(filas: list[object]) -> list[FilaConfirmada]:
    """Las filas confirmadas, sin perder el equipo al que pertenecen."""
    salida: list[FilaConfirmada] = []
    for fila in filas:
        tag = str(getattr(fila, "tag", "") or "")
        variable = str(getattr(fila, "variable", "") or "")
        if not tag or not variable:
            continue
        valor = getattr(fila, "valor", None)
        try:
            valor = float(valor) if valor is not None else None
        except (TypeError, ValueError):
            valor = None
        salida.append(
            FilaConfirmada(
                tag=tag,
                variable=variable,
                valor=valor,
                unidad=getattr(fila, "unidad", None),
                observacion=getattr(fila, "observacion", None),
            )
        )
    return salida


def _tendencia(resumen: str) -> tuple[str, ...]:
    """Las frases de tendencia que el historiador agrega al resumen del evento."""
    if "Tendencia" not in resumen:
        return ()
    return tuple(parte.strip() for parte in resumen.split("Tendencia", 1)[1:] if parte.strip())


def _tendencia_estructurada(resumen: str) -> tuple[TendenciaEvidencia, ...]:
    """Recupera del resumen legacy los datos estructurados de la tendencia.

    Es un fallback para eventos que ya venían enrichidos antes de que el
    historial entregara su muestra estructurada. No estima el promedio: si no
    está, queda ``None`` y el manifiesto lo marca como no disponible.
    """
    coincidencia = _TENDENCIA.search(resumen)
    if coincidencia is None:
        return ()
    grupos = coincidencia.groupdict()

    def numero(nombre: str) -> float:
        return float(grupos[nombre].replace(",", "."))

    unidad = grupos["unidad"] or grupos["unidad_final"] or None
    ventana = f"{int(grupos['ventana'])} h"
    return (
        TendenciaEvidencia(
            variable=grupos["variable"],
            ventana=ventana,
            fuente="historiador",
            muestras=int(grupos["muestras"]),
            minimo=numero("minimo"),
            maximo=numero("maximo"),
            promedio=None,
            tendencia=grupos["direccion"],
            unidad=unidad,
            resumen=(
                f"Tendencia {grupos['variable']} últimas {ventana}: "
                f"{grupos['muestras']} muestras, {grupos['primero']}→{grupos['ultimo']}"
                f"{(' ' + unidad) if unidad else ''} ({grupos['direccion']}), "
                f"rango {grupos['minimo']}–{grupos['maximo']}"
                f"{(' ' + unidad) if unidad else ''}."
            ),
        ),
    )
