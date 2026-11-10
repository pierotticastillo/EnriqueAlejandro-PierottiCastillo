"""Tests de la decisión del planificador y del registro de la trayectoria.

Requieren un PostgreSQL con las migraciones aplicadas:

    TEST_DATABASE_URL=postgresql+psycopg://pia3:pia3@localhost:55432/pia3 pytest

Sin esa variable los tests se omiten para no romper una corrida sin base.

Lo que se comprueba:

* la decisión del planificador sólo toca órdenes **pendientes**;
* el rechazo necesita motivo;
* la decisión queda escrita en ``aprobacion`` con su usuario;
* la Central ve las órdenes con sus tareas y sus repuestos;
* la trayectoria se escribe en ``registro_trayectoria`` y se puede leer de
  nuevo por orden.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest
from planta_contrato import EstadoOrden, OrdenSistema, Repuesto
from sqlalchemy import create_engine, text

from agente.trayectoria import RegistradorTrayectoria
from db.repositorio import Repositorio

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL no definida",
)


@pytest.fixture(scope="module")
def engine():
    motor = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    with motor.connect() as conexion:
        conexion.execute(text("SELECT 1"))
    yield motor
    motor.dispose()


@pytest.fixture
def repositorio(engine) -> Repositorio:
    return Repositorio(engine)


def _evento(conexion, equipo: str = "B-102") -> str:
    evento_id = f"ev-{uuid.uuid4()}"
    conexion.execute(
        text(
            """
            INSERT INTO evento_sistema (id, origen, estado, payload)
            VALUES (:id, 'parte', 'Escalado', CAST(:payload AS jsonb))
            """
        ),
        {
            "id": evento_id,
            "payload": json.dumps({"equipo": equipo, "resumen": "B-102 goteo"}),
        },
    )
    return evento_id


def _orden(evento_id: str, *, estado: EstadoOrden = EstadoOrden.pendiente) -> OrdenSistema:
    return OrdenSistema(
        id=f"ORD-B-102-TX-T01-{uuid.uuid4().hex[:8]}",
        ts="2026-11-10T09:00:00-03:00",
        estado=estado,
        equipo="B-102",
        evento_ids=[evento_id],
        tareas=["T-B-EMP"],
        detiene_equipo=True,
        reduce_inyeccion=False,
        ejecutor="propio",
        repuestos=[Repuesto(codigo="TX-EMP-KIT", cantidad=1)],
        motivo="Tarea estándar: la orden no para la bomba",
    )


def _limpiar(engine, orden_ids: list[str], evento_id: str) -> None:
    with engine.begin() as conexion:
        for orden_id in orden_ids:
            conexion.execute(text("DELETE FROM orden WHERE id = :id"), {"id": orden_id})
            conexion.execute(
                text("DELETE FROM registro_trayectoria WHERE orden_id = :id"), {"id": orden_id}
            )
        conexion.execute(text("DELETE FROM evento_sistema WHERE id = :id"), {"id": evento_id})


# ------------------------------------------------------------- decisión del planificador


def test_aprobar_una_orden_pendiente_cambia_su_estado(repositorio, engine) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id)
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": False}, costo_usd=420)

        fila = repositorio.decidir_orden(orden.id, "Aprobada", usuario="planificador")

        assert fila is not None
        assert fila["estado"] == "Aprobada"
        assert repositorio.obtener_orden(orden.id)["estado"] == "Aprobada"
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_rechazar_una_orden_pendiente_deja_el_motivo(repositorio, engine) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id)
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": False})

        fila = repositorio.decidir_orden(orden.id, "Rechazada", motivo="falta el repuesto")

        assert fila is not None
        assert fila["estado"] == "Rechazada"
        assert fila["motivo"] == "falta el repuesto"
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_rechazar_sin_motivo_es_un_error(repositorio, engine) -> None:
    """El motivo del rechazo es obligatorio: el sistema no adivina."""
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id)
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": False})

        with pytest.raises(ValueError):
            repositorio.decidir_orden(orden.id, "Rechazada")

        assert repositorio.obtener_orden(orden.id)["estado"] == "Pendiente de aprobacion"
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_una_decision_invalida_es_un_error(repositorio, engine) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id)
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": False})
        with pytest.raises(ValueError):
            repositorio.decidir_orden(orden.id, "Ejecutada")
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_una_orden_ya_decidida_no_se_vuelve_a_decidir(repositorio, engine) -> None:
    """La decisión es de una sola vez: una orden aprobada no se rechaza después."""
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id)
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": False})
        assert repositorio.decidir_orden(orden.id, "Aprobada") is not None

        assert repositorio.decidir_orden(orden.id, "Rechazada", motivo="cambié de opinión") is None

        assert repositorio.obtener_orden(orden.id)["estado"] == "Aprobada"
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_una_orden_inexistente_no_se_decide(repositorio) -> None:
    assert repositorio.decidir_orden("ORD-NO-EXISTE", "Aprobada") is None


def test_la_decision_queda_escrita_con_su_usuario(repositorio, engine) -> None:
    """La decisión queda en ``aprobacion``: se puede auditar quién decidió."""
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id)
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": False, "reglas": []})
        repositorio.decidir_orden(orden.id, "Aprobada", usuario="juan")

        with engine.connect() as conexion:
            filas = conexion.execute(
                text(
                    "SELECT decision, usuario, reglas_evaluadas FROM aprobacion "
                    "WHERE orden_id = :id ORDER BY creada_en"
                ),
                {"id": orden.id},
            ).mappings().all()
        decisiones = [fila["decision"] for fila in filas]
        assert "Aprobada" in decisiones
        # La fila de las reglas se conserva: la decisión del planificador no
        # borra la evaluación que la dejó pendiente.
        assert any(fila["usuario"] == "juan" for fila in filas)
        assert any(fila["reglas_evaluadas"] for fila in filas)
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_la_central_ve_las_ordenes_con_sus_tareas_y_repuestos(repositorio, engine) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id)
    try:
        repositorio.guardar_orden(
            orden,
            reglas={"cumple": False},
            costo_usd=420,
            repuestos=[{"codigo": "TX-EMP-KIT", "costo_unitario": 420, "disponible": True}],
        )

        ordenes = repositorio.ordenes_para_central()
        fila = next(f for f in ordenes if f["id"] == orden.id)

        assert fila["equipo"] == "B-102"
        assert fila["estado"] == "Pendiente de aprobacion"
        assert fila["tareas"] == ["T-B-EMP"]
        assert fila["repuestos"][0]["codigo"] == "TX-EMP-KIT"
        assert float(fila["repuestos"][0]["cantidad"]) == 1
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_las_pendientes_van_primero_en_la_central(repositorio, engine) -> None:
    """Lo que espera decisión se ve primero: es lo que hay que hacer."""
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    pendiente = _orden(evento_id)
    aprobada = OrdenSistema(
        id=f"ORD-B-103-TX-T02-{uuid.uuid4().hex[:8]}",
        ts="2026-11-10T10:00:00-03:00",
        estado=EstadoOrden.aprobada,
        equipo="B-103",
        evento_ids=[evento_id],
        tareas=["T-B-VAL"],
        detiene_equipo=True,
        reduce_inyeccion=False,
        ejecutor="propio",
        repuestos=[],
        motivo=None,
    )
    try:
        repositorio.guardar_orden(aprobada, reglas={"cumple": True})
        repositorio.guardar_orden(pendiente, reglas={"cumple": False})

        ordenes = repositorio.ordenes_para_central()
        assert ordenes[0]["id"] == pendiente.id
    finally:
        _limpiar(engine, [pendiente.id, aprobada.id], evento_id)


# ------------------------------------------------------------------- trayectoria


def test_la_trayectoria_se_escribe_y_se_lee_por_orden(engine) -> None:
    """Cada paso queda con su modelo, sus tools, sus tokens y su tiempo."""
    registrador = RegistradorTrayectoria(engine)
    registrador.registrar(
        etapa="agente",
        tipo="modelo",
        modelo="qwen2.5:3b-instruct",
        argumentos={"prompt": "consultá los manuales"},
        salida={"justificacion": "la empaquetadura está gastada"},
        tokens_prompt=812,
        tokens_completion=96,
        costo_usd=0.0,
        segundos=4.5,
        orden_id="ORD-TRAYEC-1",
        equipo="B-101",
        paso=1,
    )
    registrador.registrar(
        etapa="agente",
        tipo="tool",
        herramienta="busqueda_rag",
        argumentos={"consulta": "empaquetadura", "top_k": 5},
        salida="TX350-D §8.3",
        segundos=0.2,
        orden_id="ORD-TRAYEC-1",
        equipo="B-101",
        paso=2,
    )
    try:
        pasos = registrador.pasos("ORD-TRAYEC-1")
        assert len(pasos) == 2
        modelo = next(paso for paso in pasos if paso["tipo"] == "modelo")
        assert modelo["modelo"] == "qwen2.5:3b-instruct"
        assert modelo["tokens_prompt"] == 812
        assert modelo["tokens_completion"] == 96
        assert float(modelo["segundos"]) == 4.5
        herramienta = next(paso for paso in pasos if paso["tipo"] == "tool")
        assert herramienta["herramienta"] == "busqueda_rag"
        assert herramienta["argumentos"]["consulta"] == "empaquetadura"
    finally:
        with engine.begin() as conexion:
            conexion.execute(
                text("DELETE FROM registro_trayectoria WHERE orden_id = :id"),
                {"id": "ORD-TRAYEC-1"},
            )


def test_la_trayectoria_acepta_una_orden_real(engine, repositorio) -> None:
    """El registro se ata a la orden que lo originó."""
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id)
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": False})
        registrador = RegistradorTrayectoria(engine)
        registrador.registrar(
            etapa="planificador",
            tipo="decision",
            argumentos={"decision": "Aprobada", "usuario": "planificador"},
            orden_id=orden.id,
            evento_id=evento_id,
            equipo="B-102",
        )
        pasos = registrador.pasos(orden.id)
        assert len(pasos) == 1
        assert pasos[0]["etapa"] == "planificador"
    finally:
        _limpiar(engine, [orden.id], evento_id)
