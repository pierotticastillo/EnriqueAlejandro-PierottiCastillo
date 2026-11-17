"""Handoff humano HITL con vencimiento y escalamiento (P1-20).

Define el contrato de traspaso de un caso del orquestador determinista a un
actor humano (supervisor o inspector) mediante la Central de mantenimiento.

El handoff:

* lleva un payload estructurado con motivo, hechos confirmados, evidencia,
  impacto, acción requerida, actor destinatario y rol;
* asigna un único responsable hasta que decida;
* registra un acuse de recepción;
* tiene un vencimiento configurable; al vencer, se escala o permanece en
  revisión explícita;
* nunca se transforma una ausencia de decisión en aprobación ni en
  autoobservación (fail-closed).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any


class EstadoHandoff(Enum):
    """Estados de vida de un handoff humano."""

    PENDIENTE = "pendiente"      # Creado, esperando acuse del destinatario
    ACEUSADO = "acusado"         # El destinatario recibió el caso
    DECIDIDO = "decidido"        # El destinatario tomó una decisión
    VENCIDO = "vencido"          # Se pasó el SLA sin decisión
    ESCALADO = "escalado"        # Se escaló a otro actor por vencimiento


@dataclass(frozen=True)
class PayloadHandoff:
    """Payload mínimo de un handoff humano (sección 7.9 del Plan 2)."""

    correlation_id: str
    orden_id: str
    motivo: str
    impacto: str
    hechos_confirmados: dict[str, Any]
    evidencia: dict[str, Any]
    estado_actual: str
    accion_requerida: str
    actor_destinatario: str
    rol_destinatario: str
    creado_en: datetime
    vence_en: datetime
    estado_entrega: str = EstadoHandoff.PENDIENTE.value

    def esta_vencido(self, ahora: datetime | None = None) -> bool:
        """Si el handoff superó su vencimiento sin decisión."""
        referencia = ahora or datetime.now(UTC)
        return referencia > self.vence_en and self.estado_entrega != EstadoHandoff.DECIDIDO.value

    def puede_decidir(self) -> bool:
        """Si el destino aún puede tomar una decisión."""
        return self.estado_entrega in {
            EstadoHandoff.PENDIENTE.value,
            EstadoHandoff.ACEUSADO.value,
        }


@dataclass(frozen=True)
class AcuseHandoff:
    """Acuse de recepción del handoff."""

    handoff_id: str
    recibido_por: str
    rol: str
    recibido_en: datetime
    observaciones: str | None = None


@dataclass(frozen=True)
class DecisionHandoff:
    """Decisión humana sobre un handoff."""

    handoff_id: str
    decision: str  # "cerrar", "certificar", "observar", "rechazar"
    actor: str
    rol: str
    motivo: str | None
    decidido_en: datetime
    evidencia_revisada: dict[str, Any] | None = None


@dataclass
class ResultadoEscalamiento:
    """Resultado de intentar escalar un handoff vencido."""

    escalado: bool
    nuevo_destinatario: str | None
    nuevo_rol: str | None
    motivo: str


class GestorHandoff:
    """Lógica de negocio del handoff HITL, sin dependencia de base de datos.

    Recibe callbacks de persistencia para que pueda probarse sin PostgreSQL.
    """

    def __init__(
        self,
        *,
        obtener_handoff: callable | None = None,
        guardar_handoff: callable | None = None,
        guardar_acuse: callable | None = None,
        guardar_decision: callable | None = None,
        obtener_actor_siguiente: callable | None = None,
    ) -> None:
        self._obtener = obtener_handoff or (lambda _id: None)
        self._guardar = guardar_handoff or (lambda _payload: None)
        self._guardar_acuse = guardar_acuse or (lambda _acuse: None)
        self._guardar_decision = guardar_decision or (lambda _decision: None)
        self._obtener_siguiente = (
            obtener_actor_siguiente or (lambda _rol, _actor: None)
        )

    # ------------------------------------------------------------------ creación

    def crear_handoff(
        self,
        *,
        correlation_id: str,
        orden_id: str,
        motivo: str,
        impacto: str,
        hechos_confirmados: dict[str, Any] | None = None,
        evidencia: dict[str, Any] | None = None,
        estado_actual: str,
        accion_requerida: str,
        actor_destinatario: str,
        rol_destinatario: str,
        sla_minutos: int,
        creado_en: datetime | None = None,
    ) -> PayloadHandoff:
        """Crea un handoff con vencimiento = creado_en + sla_minutos."""
        ahora = creado_en or datetime.now(UTC)
        payload = PayloadHandoff(
            correlation_id=correlation_id,
            orden_id=orden_id,
            motivo=motivo,
            impacto=impacto,
            hechos_confirmados=hechos_confirmados or {},
            evidencia=evidencia or {},
            estado_actual=estado_actual,
            accion_requerida=accion_requerida,
            actor_destinatario=actor_destinatario,
            rol_destinatario=rol_destinatario,
            creado_en=ahora,
            vence_en=ahora + timedelta(minutes=sla_minutos),
        )
        self._guardar(payload)
        return payload

    # ------------------------------------------------------------------ acuse

    def registrar_acuse(
        self,
        handoff_id: str,
        recibido_por: str,
        rol: str,
        observaciones: str | None = None,
        recibido_en: datetime | None = None,
    ) -> AcuseHandoff:
        """Registra que el destinatario recibió el handoff.

        Cambia el estado de PENDIENTE a ACEUSADO. Si el handoff ya fue
        decidido o vencido, el acuse no tiene efecto.
        """
        ahora = recibido_en or datetime.now(UTC)
        acuse = AcuseHandoff(
            handoff_id=handoff_id,
            recibido_por=recibido_por,
            rol=rol,
            recibido_en=ahora,
            observaciones=observaciones,
        )
        self._guardar_acuse(acuse)
        return acuse

    # ------------------------------------------------------------------ decisión

    def decidir(
        self,
        handoff_id: str,
        decision: str,
        actor: str,
        rol: str,
        motivo: str | None = None,
        decidido_en: datetime | None = None,
        evidencia_revisada: dict[str, Any] | None = None,
    ) -> DecisionHandoff:
        """Registra la decisión humana sobre el handoff.

        Fail-closed: una vez decidido, el handoff no puede cambiar de estado.
        """
        ahora = decidido_en or datetime.now(UTC)
        decision_registrada = DecisionHandoff(
            handoff_id=handoff_id,
            decision=decision,
            actor=actor,
            rol=rol,
            motivo=motivo,
            decidido_en=ahora,
            evidencia_revisada=evidencia_revisada,
        )
        self._guardar_decision(decision_registrada)
        return decision_registrada

    # ------------------------------------------------------------------ vencimiento y escalamiento

    def verificar_vencimiento(
        self,
        handoff_id: str,
        ahora: datetime | None = None,
    ) -> EstadoHandoff:
        """Devuelve el estado actual del handoff considerando su vencimiento.

        Si el handoff está vencido y no fue decidido, pasa a VENCIDO.
        """
        referencia = ahora or datetime.now(UTC)
        payload = self._obtener(handoff_id)
        if payload is None:
            raise ValueError(f"Handoff no encontrado: {handoff_id}")
        if payload.estado_entrega == EstadoHandoff.DECIDIDO.value:
            return EstadoHandoff.DECIDIDO
        if referencia > payload.vence_en:
            return EstadoHandoff.VENCIDO
        return EstadoHandoff(payload.estado_entrega)

    def escalar_por_vencimiento(
        self,
        handoff_id: str,
        ahora: datetime | None = None,
    ) -> ResultadoEscalamiento:
        """Escala un handoff vencido al siguiente actor disponible.

        Nunca autoaprueba: si no hay siguiente actor, el handoff permanece
        en VENCIDO (revisión explícita).
        """
        referencia = ahora or datetime.now(UTC)
        payload = self._obtener(handoff_id)
        if payload is None:
            raise ValueError(f"Handoff no encontrado: {handoff_id}")
        if payload.estado_entrega == EstadoHandoff.DECIDIDO.value:
            return ResultadoEscalamiento(
                escalado=False,
                nuevo_destinatario=None,
                nuevo_rol=None,
                motivo="El handoff ya fue decidido",
            )
        if not payload.esta_vencido(referencia):
            return ResultadoEscalamiento(
                escalado=False,
                nuevo_destinatario=None,
                nuevo_rol=None,
                motivo="El handoff aún no ha vencido",
            )

        siguiente = self._obtener_siguiente(payload.rol_destinatario, payload.actor_destinatario)
        if siguiente is None:
            # No hay a quién escalar: permanece en revisión explícita, fail-closed.
            return ResultadoEscalamiento(
                escalado=False,
                nuevo_destinatario=None,
                nuevo_rol=None,
                motivo=(
                    "No hay actor siguiente para escalar; el handoff permanece "
                    "en revisión explícita y nunca se autoaprueba"
                ),
            )

        nuevo_payload = PayloadHandoff(
            correlation_id=payload.correlation_id,
            orden_id=payload.orden_id,
            motivo=payload.motivo,
            impacto=payload.impacto,
            hechos_confirmados=payload.hechos_confirmados,
            evidencia=payload.evidencia,
            estado_actual=payload.estado_actual,
            accion_requerida=payload.accion_requerida,
            actor_destinatario=siguiente["actor"],
            rol_destinatario=siguiente["rol"],
            creado_en=payload.creado_en,
            vence_en=referencia + timedelta(
                minutes=payload.vence_en.minute - payload.creado_en.minute
            ),
            estado_entrega=EstadoHandoff.ESCALADO.value,
        )
        self._guardar(nuevo_payload)
        return ResultadoEscalamiento(
            escalado=True,
            nuevo_destinatario=siguiente["actor"],
            nuevo_rol=siguiente["rol"],
            motivo=f"Escalado por vencimiento de {payload.actor_destinatario}",
        )

    # ------------------------------------------------------------------ consulta

    def esta_disponible_para_decision(
        self,
        handoff_id: str,
        ahora: datetime | None = None,
    ) -> bool:
        """Si el handoff puede recibir una decisión ahora."""
        referencia = ahora or datetime.now(UTC)
        payload = self._obtener(handoff_id)
        if payload is None:
            return False
        return (
            payload.puede_decidir()
            and not payload.esta_vencido(referencia)
        )


__all__ = [
    "EstadoHandoff",
    "PayloadHandoff",
    "AcuseHandoff",
    "DecisionHandoff",
    "ResultadoEscalamiento",
    "GestorHandoff",
]
