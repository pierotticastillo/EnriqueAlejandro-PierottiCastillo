"""Control de compliance: cuatro preguntas antes de enviar datos a la nube.

Implementa el instructivo de la presentación "Proveedores y modelos por
propósito" (Semana_5, Unidad 1, página 6): antes de enviar un dato, responder
cuatro preguntas. Si alguna respuesta es "no sé", el dato no sale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class DatoViaja:
    """Descripción de los datos que se enviarían al proveedor."""

    tipo: str  # ej. "nombre y teléfono del cliente"
    son_personales: bool
    descripcion: str


@dataclass(frozen=True)
class TerminosProveedor:
    """Términos bajo los cuales viajan los datos."""

    retencion_dias: int | None  # None = no se sabe
    entrena_con_datos: Literal["sí", "no", "no sé"]
    region_procesamiento: str | None  # None = no se sabe
    jurisdiccion: str | None  # None = no se sabe


@dataclass(frozen=True)
class ResultadoCompliance:
    """Resultado del control de compliance."""

    puede_enviar: bool
    motivo: str
    preguntas: dict[str, str] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def respuesta_unknown(self) -> list[str]:
        """Devuelve las preguntas respondidas con 'no sé' o 'desconocida'."""
        return [
            k
            for k, v in self.preguntas.items()
            if v.strip().lower() == "no sé" or "desconocida" in v.lower()
        ]


class ControlCompliance:
    """Control de compliance que evalúa si se pueden enviar datos a un proveedor."""

    def __init__(
        self,
        proveedor: str,
        datos: DatoViaja,
        terminos: TerminosProveedor,
    ) -> None:
        self._proveedor = proveedor
        self._datos = datos
        self._terminos = terminos

    def evaluar(self) -> ResultadoCompliance:
        """Responde las cuatro preguntas y decide si se puede enviar.

        Reglas:
        - Si los datos no son personales, siempre se puede enviar.
        - Si los datos son personales y alguna respuesta es "no sé", no se envía.
        - Si el proveedor entrena con los datos, no se envía.
        - Si no hay región de procesamiento conocida, no se envía.
        """
        preguntas: dict[str, str] = {}

        # 1. ¿Qué datos viajan?
        preguntas["¿Qué datos viajan?"] = self._datos.tipo

        # 2. ¿Qué hace el proveedor?
        hace = (
            f"retención {self._terminos.retencion_dias or 'desconocida'} días; "
            f"entrena con datos: {self._terminos.entrena_con_datos}"
        )
        preguntas["¿Qué hace el proveedor?"] = hace

        # 3. ¿Dónde se procesan?
        preguntas["¿Dónde se procesan?"] = (
            f"{self._terminos.region_procesamiento or 'desconocida'} "
            f"({self._terminos.jurisdiccion or 'desconocida'})"
        )

        # 4. ¿Bajo qué términos?
        preguntas["¿Bajo qué términos?"] = (
            "términos del proveedor de nube; revisar contrato antes del primer request"
        )

        # Decisión
        motivo_partes: list[str] = []

        if not self._datos.son_personales:
            motivo_partes.append("los datos no son personales")
            return ResultadoCompliance(
                puede_enviar=True,
                motivo="; ".join(motivo_partes),
                preguntas=preguntas,
            )

        # Datos personales: aplicar reglas estrictas
        desconocidas = [
            k for k, v in preguntas.items()
            if "desconocida" in v or v.strip().lower() == "no sé"
        ]

        if self._terminos.entrena_con_datos == "sí":
            motivo_partes.append("el proveedor entrena con los datos")
        if self._terminos.retencion_dias is None:
            motivo_partes.append("retención desconocida")
        if self._terminos.region_procesamiento is None:
            motivo_partes.append("región de procesamiento desconocida")
        if self._terminos.jurisdiccion is None:
            motivo_partes.append("jurisdicción desconocida")
        if desconocidas:
            motivo_partes.append("respuestas desconocidas: " + ", ".join(desconocidas))

        if motivo_partes:
            return ResultadoCompliance(
                puede_enviar=False,
                motivo="no se puede enviar: " + "; ".join(motivo_partes),
                preguntas=preguntas,
            )

        return ResultadoCompliance(
            puede_enviar=True,
            motivo="datos personales con términos claros y región conocida",
            preguntas=preguntas,
        )

    def puede_enviar(self) -> bool:
        """True si el control de compliance permite enviar los datos."""
        return self.evaluar().puede_enviar

    def motivo(self) -> str:
        """Motivo de la decisión de compliance."""
        return self.evaluar().motivo


def crear_control_desde_config(
    proveedor: str,
    datos_tipo: str,
    datos_personales: bool,
    retencion_dias: int | None = None,
    entrena_con_datos: Literal["sí", "no", "no sé"] = "no sé",
    region_procesamiento: str | None = None,
    jurisdiccion: str | None = None,
) -> ControlCompliance:
    """Crea un ControlCompliance a partir de valores de configuración."""
    datos = DatoViaja(
        tipo=datos_tipo,
        son_personales=datos_personales,
        descripcion=datos_tipo,
    )
    terminos = TerminosProveedor(
        retencion_dias=retencion_dias,
        entrena_con_datos=entrena_con_datos,
        region_procesamiento=region_procesamiento,
        jurisdiccion=jurisdiccion,
    )
    return ControlCompliance(proveedor=proveedor, datos=datos, terminos=terminos)
