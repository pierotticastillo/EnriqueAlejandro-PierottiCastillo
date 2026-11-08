"""La lista de precios del contrato de servicios (CT-SVP-2025 §2.1).

El monto de un habilita sale de **la orden y esta lista**, nunca de lo que
declara el cierre de la contratista (CT-SVP-2025 §3.2). Por eso los precios se
leen del propio contrato que publica la planta y no de una tabla escrita a
mano en el código: si el contrato cambia, el monto cambia con él.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tools.cliente import ClientePlanta
from tools.manuales import CorpusManuales, numero, tabla

# El contrato vigente de Servicios Patagónicos (Anexo II).
DOCUMENTO_CONTRATO = "CT-SVP-2025"
SECCION_PRECIOS = "2.1"
CITA_PRECIOS = "CT-SVP-2025 §2.1"

# El código de la tarea que cubre el ítem: "T-B-ROD", "I-VIB".
_CODIGO_TAREA = re.compile(r"\b((?:T|I)-[A-Z0-9]+(?:-[A-Z0-9]+)*)\b")


@dataclass(frozen=True)
class PrecioContrato:
    """Un ítem de la lista de precios, con la tarea del plan que lo cubre."""

    codigo: str
    descripcion: str
    unidad: str
    precio_usd: float
    horas_incluidas: float | None = None
    tareas: tuple[str, ...] = ()
    cita: str = CITA_PRECIOS

    @property
    def precio_cerrado(self) -> bool:
        """Precio cerrado: se paga completo aunque el trabajo lleve menos horas."""
        return "cerrado" in self.unidad.lower()

    @property
    def por_equipo(self) -> bool:
        """Se cobra por cada equipo medido."""
        return "equipo" in self.unidad.lower()

    @property
    def por_hora(self) -> bool:
        """Se cobra por hora trabajada."""
        return self.unidad.strip().lower().startswith("hora")


def precios_contrato(
    cliente: ClientePlanta | None = None,
    *,
    corpus: CorpusManuales | None = None,
) -> tuple[PrecioContrato, ...]:
    """Los precios vigentes del contrato, leídos de CT-SVP-2025 §2.1."""
    if corpus is None:
        if cliente is None:
            raise ValueError("Se necesita un cliente de la planta o un corpus")
        corpus = CorpusManuales(cliente)
    seccion = corpus.seccion(DOCUMENTO_CONTRATO, SECCION_PRECIOS)
    if seccion is None:
        return ()
    return precios_desde_texto(str(seccion.get("texto") or ""))


def precios_desde_texto(texto: str) -> tuple[PrecioContrato, ...]:
    """Los precios de la tabla de §2.1, parseados del texto del contrato."""
    salida: list[PrecioContrato] = []
    filas = tabla(texto)
    for fila in filas[1:]:
        if len(fila) < 4 or not fila[0].startswith("SVP-"):
            continue
        precio = numero(fila[3])
        if precio is None:
            continue
        horas = numero(fila[4]) if len(fila) > 4 else None
        salida.append(
            PrecioContrato(
                codigo=fila[0],
                descripcion=fila[1],
                unidad=fila[2],
                precio_usd=precio,
                horas_incluidas=horas,
                tareas=tuple(_CODIGO_TAREA.findall(fila[1])),
            )
        )
    return tuple(salida)


class ListaPrecios:
    """La lista de precios, consultable por código de tarea."""

    def __init__(self, precios: tuple[PrecioContrato, ...] = ()) -> None:
        self._precios = precios
        self._por_tarea: dict[str, PrecioContrato] = {}
        for precio in precios:
            for tarea in precio.tareas:
                self._por_tarea.setdefault(tarea, precio)

    @property
    def precios(self) -> tuple[PrecioContrato, ...]:
        return self._precios

    def de_tarea(self, codigo: str) -> PrecioContrato | None:
        """El ítem del contrato que cubre una tarea del plan."""
        return self._por_tarea.get(codigo)

    def de_codigo(self, codigo: str) -> PrecioContrato | None:
        """El ítem del contrato con ese código (SVP-01, SVP-04...)."""
        return next((precio for precio in self._precios if precio.codigo == codigo), None)

    def __len__(self) -> int:
        return len(self._precios)

    def __iter__(self):
        return iter(self._precios)
