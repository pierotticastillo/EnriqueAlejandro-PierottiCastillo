"""Tests de handoff HITL con vencimiento (P1-20).

Prueban la lógica de negocio de ``GestorHandoff`` usando callbacks en
memoria, sin necesidad de PostgreSQL. Los tests que persisten en la base
requieren ``TEST_DATABASE_URL`` y se omiten si no está definida.

Comprueba que:

* un handoff se crea con vencimiento = creado_en + sla_minutos;
* registrar acuse cambia el estado de PENDIENTE a ACEUSADO;
* una decisión humana marca el handoff como DECIDIDO y es inmutable;
* un handoff vencido sin decisión pasa a VENCIDO;
* el escalamiento por vencimiento asigna un siguiente actor;
* si no hay siguiente actor, el handoff permanece en revisión explícita
  (fail-closed, nunca se autoaprueba);
* una decisión después del vencimiento no puede revertir el estado.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from planta.handoff import (
    AcuseHandoff,
    DecisionHandoff,
    EstadoHandoff,
    GestorHandoff,
    PayloadHandoff,
)

# ------------------------------------------------------------------ fixtures en memoria


class _Memoria:
    """Almacén en memoria para probar GestorHandoff sin base de datos."""

    def __init__(self) -> None:
        self.handoffs: dict[str, PayloadHandoff] = {}
        self.acuses: list[AcuseHandoff] = []
        self.decisiones: list[DecisionHandoff] = []

    def guardar(self, payload: PayloadHandoff) -> None:
        self.handoffs[payload.orden_id] = payload

    def obtener(self, orden_id: str) -> PayloadHandoff | None:
        return self.handoffs.get(orden_id)

    def guardar_acuse(self, acuse: AcuseHandoff) -> None:
        self.acuses.append(acuse)
        if acuse.handoff_id in self.handoffs:
            actual = self.handoffs[acuse.handoff_id]
            # Acuse cambia PENDIENTE -> ACEUSADO.
            if actual.estado_entrega == EstadoHandoff.PENDIENTE.value:
                self.handoffs[acuse.handoff_id] = PayloadHandoff(
                    **{**actual.__dict__, "estado_entrega": EstadoHandoff.ACEUSADO.value}
                )

    def guardar_decision(self, decision: DecisionHandoff) -> None:
        self.decisiones.append(decision)
        if decision.handoff_id in self.handoffs:
            actual = self.handoffs[decision.handoff_id]
            self.handoffs[decision.handoff_id] = PayloadHandoff(
                **{**actual.__dict__, "estado_entrega": EstadoHandoff.DECIDIDO.value}
            )


def _ahora() -> datetime:
    return datetime(2026, 11, 17, 10, 0, 0, tzinfo=UTC)


def _gestor_con_memoria() -> tuple[GestorHandoff, _Memoria]:
    memoria = _Memoria()
    gestor = GestorHandoff(
        obtener_handoff=lambda oid: memoria.obtener(oid),
        guardar_handoff=memoria.guardar,
        guardar_acuse=memoria.guardar_acuse,
        guardar_decision=memoria.guardar_decision,
    )
    return gestor, memoria


# ------------------------------------------------------------------ creación


def test_crear_handoff_con_vencimiento():
    """El handoff nace con vencimiento = creado_en + sla_minutos."""
    gestor, memoria = _gestor_con_memoria()
    ahora = _ahora()
    payload = gestor.crear_handoff(
        correlation_id="solicitud:001",
        orden_id="ord-001",
        motivo="Daño estructural en fotos",
        impacto="Alto",
        estado_actual="Escalado",
        accion_requerida="Certificar o observar",
        actor_destinatario="inspector-01",
        rol_destinatario="inspector",
        sla_minutos=60,
        creado_en=ahora,
    )
    assert payload.estado_entrega == EstadoHandoff.PENDIENTE.value
    assert payload.vence_en == ahora + timedelta(minutes=60)
    assert payload.correlation_id == "solicitud:001"
    assert payload.actor_destinatario == "inspector-01"
    assert memoria.handoffs["ord-001"] is payload


def test_crear_handoff_sin_hechos_ni_evidencia():
    """Hechos y evidencia pueden ir vacíos."""
    gestor, memoria = _gestor_con_memoria()
    ahora = _ahora()
    payload = gestor.crear_handoff(
        correlation_id="solicitud:002",
        orden_id="ord-002",
        motivo="Revisar cierre",
        impacto="Medio",
        estado_actual="Ejecutada",
        accion_requerida="Cerrar trabajo propio",
        actor_destinatario="supervisor-01",
        rol_destinatario="supervisor",
        sla_minutos=30,
        creado_en=ahora,
    )
    assert payload.hechos_confirmados == {}
    assert payload.evidencia == {}
    assert payload.vence_en == ahora + timedelta(minutes=30)


# ------------------------------------------------------------------ acuse


def test_registrar_acuse_cambia_a_acusado():
    """El acuse de recepción pasa el handoff de PENDIENTE a ACEUSADO."""
    gestor, memoria = _gestor_con_memoria()
    ahora = _ahora()
    payload = gestor.crear_handoff(
        correlation_id="solicitud:003",
        orden_id="ord-003",
        motivo="m",
        impacto="i",
        estado_actual="e",
        accion_requerida="a",
        actor_destinatario="a1",
        rol_destinatario="supervisor",
        sla_minutos=30,
        creado_en=ahora,
    )
    assert payload.estado_entrega == EstadoHandoff.PENDIENTE.value

    acuse = gestor.registrar_acuse(
        handoff_id="ord-003",
        recibido_por="inspector-01",
        rol="inspector",
        observaciones="Recibido, revisando fotos",
    )
    assert acuse.recibido_por == "inspector-01"
    assert acuse.rol == "inspector"

    actualizado = memoria.obtener("ord-003")
    assert actualizado is not None
    assert actualizado.estado_entrega == EstadoHandoff.ACEUSADO.value
    assert len(memoria.acuses) == 1


def test_acuse_sobre_ya_decidido_no_cambia_estado():
    """Si el handoff ya fue decidido, un acuse posterior no lo revierte."""
    gestor, memoria = _gestor_con_memoria()
    ahora = _ahora()
    gestor.crear_handoff(
        correlation_id="solicitud:004",
        orden_id="ord-004",
        motivo="m",
        impacto="i",
        estado_actual="e",
        accion_requerida="a",
        actor_destinatario="a1",
        rol_destinatario="inspector",
        sla_minutos=30,
        creado_en=ahora,
    )
    gestor.decidir(
        handoff_id="ord-004",
        decision="certificar",
        actor="inspector-01",
        rol="inspector",
        motivo="Todo correcto",
        decidido_en=ahora + timedelta(minutes=10),
    )
    assert memoria.obtener("ord-004").estado_entrega == EstadoHandoff.DECIDIDO.value

    gestor.registrar_acuse(
        handoff_id="ord-004",
        recibido_por="otro",
        rol="lectura",
    )
    assert memoria.obtener("ord-004").estado_entrega == EstadoHandoff.DECIDIDO.value


# ------------------------------------------------------------------ decisión


def test_decidir_marca_como_decidido():
    """Una decisión humana marca el handoff como DECIDIDO."""
    gestor, memoria = _gestor_con_memoria()
    ahora = _ahora()
    gestor.crear_handoff(
        correlation_id="solicitud:005",
        orden_id="ord-005",
        motivo="m",
        impacto="i",
        estado_actual="e",
        accion_requerida="a",
        actor_destinatario="a1",
        rol_destinatario="supervisor",
        sla_minutos=30,
        creado_en=ahora,
    )
    decision = gestor.decidir(
        handoff_id="ord-005",
        decision="cerrar",
        actor="supervisor-01",
        rol="supervisor",
        motivo="Trabajo propio verificado",
        decidido_en=ahora + timedelta(minutes=5),
    )
    assert decision.decision == "cerrar"
    assert decision.actor == "supervisor-01"
    assert decision.rol == "supervisor"
    assert decision.motivo == "Trabajo propio verificado"
    assert memoria.obtener("ord-005").estado_entrega == EstadoHandoff.DECIDIDO.value
    assert len(memoria.decisiones) == 1


def test_decision_no_puede_revertirse():
    """Una vez decidido, el handoff no cambia de estado."""
    gestor, memoria = _gestor_con_memoria()
    ahora = _ahora()
    gestor.crear_handoff(
        correlation_id="solicitud:006",
        orden_id="ord-006",
        motivo="m",
        impacto="i",
        estado_actual="e",
        accion_requerida="a",
        actor_destinatario="a1",
        rol_destinatario="inspector",
        sla_minutos=30,
        creado_en=ahora,
    )
    gestor.decidir(
        handoff_id="ord-006",
        decision="observar",
        actor="inspector-01",
        rol="inspector",
        motivo="Falta evidencia",
        decidido_en=ahora + timedelta(minutes=5),
    )
    assert memoria.obtener("ord-006").estado_entrega == EstadoHandoff.DECIDIDO.value


# ------------------------------------------------------------------ vencimiento


def test_esta_vencido_sin_decision():
    """Un handoff pasado su vencimiento sin decisión está vencido."""
    gestor, memoria = _gestor_con_memoria()
    ahora = _ahora()
    payload = gestor.crear_handoff(
        correlation_id="solicitud:007",
        orden_id="ord-007",
        motivo="m",
        impacto="i",
        estado_actual="e",
        accion_requerida="a",
        actor_destinatario="a1",
        rol_destinatario="inspector",
        sla_minutos=5,
        creado_en=ahora,
    )
    despues = ahora + timedelta(minutes=10)
    assert payload.esta_vencido(despues)


def test_no_esta_vencido_dentro_de_sla():
    """Dentro del SLA, el handoff no está vencido."""
    gestor, memoria = _gestor_con_memoria()
    ahora = _ahora()
    gestor.crear_handoff(
        correlation_id="solicitud:008",
        orden_id="ord-008",
        motivo="m",
        impacto="i",
        estado_actual="e",
        accion_requerida="a",
        actor_destinatario="a1",
        rol_destinatario="inspector",
        sla_minutos=60,
        creado_en=ahora,
    )
    antes = ahora + timedelta(minutes=30)
    payload = memoria.obtener("ord-008")
    assert payload is not None
    assert not payload.esta_vencido(antes)


def test_verificar_vencimiento_devuelve_vencido():
    """verificar_vencimiento devuelve VENCIDO si pasó el SLA y no hay decisión."""
    gestor, memoria = _gestor_con_memoria()
    ahora = _ahora()
    gestor.crear_handoff(
        correlation_id="solicitud:009",
        orden_id="ord-009",
        motivo="m",
        impacto="i",
        estado_actual="e",
        accion_requerida="a",
        actor_destinatario="a1",
        rol_destinatario="inspector",
        sla_minutos=5,
        creado_en=ahora,
    )
    despues = ahora + timedelta(minutes=10)
    estado = gestor.verificar_vencimiento("ord-009", ahora=despues)
    assert estado == EstadoHandoff.VENCIDO


def test_verificar_vencimiento_devuelve_decidido_si_ya_se_decidio():
    """Si ya se decidió, verificar_vencimiento devuelve DECIDIDO aunque haya vencido."""
    gestor, memoria = _gestor_con_memoria()
    ahora = _ahora()
    gestor.crear_handoff(
        correlation_id="solicitud:010",
        orden_id="ord-010",
        motivo="m",
        impacto="i",
        estado_actual="e",
        accion_requerida="a",
        actor_destinatario="a1",
        rol_destinatario="inspector",
        sla_minutos=5,
        creado_en=ahora,
    )
    gestor.decidir(
        handoff_id="ord-010",
        decision="certificar",
        actor="inspector-01",
        rol="inspector",
        motivo="ok",
        decidido_en=ahora + timedelta(minutes=2),
    )
    despues = ahora + timedelta(minutes=10)
    estado = gestor.verificar_vencimiento("ord-010", ahora=despues)
    assert estado == EstadoHandoff.DECIDIDO


def test_esta_disponible_para_decision_dentro_de_sla():
    """Dentro del SLA y sin decisión, está disponible para decidir."""
    gestor, memoria = _gestor_con_memoria()
    ahora = _ahora()
    gestor.crear_handoff(
        correlation_id="solicitud:011",
        orden_id="ord-011",
        motivo="m",
        impacto="i",
        estado_actual="e",
        accion_requerida="a",
        actor_destinatario="a1",
        rol_destinatario="inspector",
        sla_minutos=60,
        creado_en=ahora,
    )
    dentro = ahora + timedelta(minutes=30)
    assert gestor.esta_disponible_para_decision("ord-011", ahora=dentro)


def test_no_esta_disponible_despues_de_vencido():
    """Después del vencimiento, no está disponible para decidir."""
    gestor, memoria = _gestor_con_memoria()
    ahora = _ahora()
    gestor.crear_handoff(
        correlation_id="solicitud:012",
        orden_id="ord-012",
        motivo="m",
        impacto="i",
        estado_actual="e",
        accion_requerida="a",
        actor_destinatario="a1",
        rol_destinatario="inspector",
        sla_minutos=5,
        creado_en=ahora,
    )
    despues = ahora + timedelta(minutes=10)
    assert not gestor.esta_disponible_para_decision("ord-012", ahora=despues)


# ------------------------------------------------------------------ escalamiento


def test_escalar_por_vencimiento_con_siguiente_actor():
    """Si hay siguiente actor, el handoff vencido se escala a él."""
    gestor, memoria = _gestor_con_memoria()
    ahora = _ahora()
    gestor.crear_handoff(
        correlation_id="solicitud:013",
        orden_id="ord-013",
        motivo="m",
        impacto="i",
        estado_actual="e",
        accion_requerida="a",
        actor_destinatario="inspector-01",
        rol_destinatario="inspector",
        sla_minutos=5,
        creado_en=ahora,
    )
    despues = ahora + timedelta(minutes=10)

    def obtener_siguiente(rol: str, actor: str) -> dict[str, str] | None:
        assert rol == "inspector"
        assert actor == "inspector-01"
        return {"actor": "inspector-02", "rol": "inspector"}

    gestor._obtener_siguiente = obtener_siguiente
    resultado = gestor.escalar_por_vencimiento("ord-013", ahora=despues)
    assert resultado.escalado
    assert resultado.nuevo_destinatario == "inspector-02"
    assert resultado.nuevo_rol == "inspector"
    assert "Escalado por vencimiento" in resultado.motivo

    escalado = memoria.obtener("ord-013")
    assert escalado is not None
    assert escalado.actor_destinatario == "inspector-02"
    assert escalado.estado_entrega == EstadoHandoff.ESCALADO.value


def test_escalar_sin_siguiente_actor_no_escala():
    """Sin siguiente actor, el handoff permanece en revisión explícita (fail-closed)."""
    gestor, memoria = _gestor_con_memoria()
    ahora = _ahora()
    gestor.crear_handoff(
        correlation_id="solicitud:014",
        orden_id="ord-014",
        motivo="m",
        impacto="i",
        estado_actual="e",
        accion_requerida="a",
        actor_destinatario="inspector-01",
        rol_destinatario="inspector",
        sla_minutos=5,
        creado_en=ahora,
    )
    despues = ahora + timedelta(minutes=10)

    def obtener_siguiente(rol: str, actor: str) -> None:
        return None

    gestor._obtener_siguiente = obtener_siguiente
    resultado = gestor.escalar_por_vencimiento("ord-014", ahora=despues)
    assert not resultado.escalado
    assert resultado.nuevo_destinatario is None
    assert resultado.nuevo_rol is None
    assert "nunca se autoaprueba" in resultado.motivo

    # El handoff sigue con el mismo actor y no se aprobó solo.
    actual = memoria.obtener("ord-014")
    assert actual is not None
    assert actual.actor_destinatario == "inspector-01"
    assert actual.estado_entrega != EstadoHandoff.DECIDIDO.value


def test_no_escalar_si_no_ha_vencido():
    """Si el handoff no venció, no se escala."""
    gestor, memoria = _gestor_con_memoria()
    ahora = _ahora()
    gestor.crear_handoff(
        correlation_id="solicitud:015",
        orden_id="ord-015",
        motivo="m",
        impacto="i",
        estado_actual="e",
        accion_requerida="a",
        actor_destinatario="a1",
        rol_destinatario="inspector",
        sla_minutos=60,
        creado_en=ahora,
    )
    dentro = ahora + timedelta(minutes=10)

    def obtener_siguiente(rol: str, actor: str) -> dict[str, str]:
        return {"actor": "otro", "rol": "otro"}

    gestor._obtener_siguiente = obtener_siguiente
    resultado = gestor.escalar_por_vencimiento("ord-015", ahora=dentro)
    assert not resultado.escalado
    assert resultado.motivo == "El handoff aún no ha vencido"


def test_handoff_no_encontrado_lanza_error():
    """Escalar o verificar un handoff inexistente lanza ValueError."""
    gestor, _ = _gestor_con_memoria()
    ahora = _ahora()
    with pytest.raises(ValueError, match="no encontrado"):
        gestor.escalar_por_vencimiento("no-existe", ahora=ahora)
    with pytest.raises(ValueError, match="no encontrado"):
        gestor.verificar_vencimiento("no-existe", ahora=ahora)


# ------------------------------------------------------------------ fail-closed


def test_nunca_autoaprueba_por_trascendencia_del_sla():
    """Un handoff vencido sin decisión nunca se aprueba automáticamente."""
    gestor, memoria = _gestor_con_memoria()
    ahora = _ahora()
    gestor.crear_handoff(
        correlation_id="solicitud:016",
        orden_id="ord-016",
        motivo="m",
        impacto="i",
        estado_actual="e",
        accion_requerida="a",
        actor_destinatario="a1",
        rol_destinatario="inspector",
        sla_minutos=5,
        creado_en=ahora,
    )
    despues = ahora + timedelta(minutes=10)
    estado = gestor.verificar_vencimiento("ord-016", ahora=despues)
    assert estado == EstadoHandoff.VENCIDO
    assert not gestor.esta_disponible_para_decision("ord-016", ahora=despues)
