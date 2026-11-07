"""Tests de la persistencia de órdenes (Fase 5).

Requieren un PostgreSQL con las migraciones aplicadas:

    TEST_DATABASE_URL=postgresql+psycopg://pia3:pia3@localhost:55432/pia3 pytest

Sin esa variable los tests se omiten para no romper una corrida sin base.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest
from planta_contrato import EstadoOrden, OrdenSistema, Repuesto
from sqlalchemy import create_engine, text

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
        ts="2026-11-06T09:00:00-03:00",
        estado=estado,
        equipo="B-102",
        evento_ids=[evento_id],
        tareas=["T-B-EMP"],
        detiene_equipo=True,
        reduce_inyeccion=False,
        ejecutor="propio",
        repuestos=[Repuesto(codigo="TX-EMP-KIT", cantidad=1)],
        motivo=(
            "Tarea estándar: la orden no para la bomba"
            if estado is EstadoOrden.pendiente
            else None
        ),
    )


def _limpiar(engine, orden_ids: list[str], evento_id: str) -> None:
    with engine.begin() as conexion:
        for orden_id in orden_ids:
            conexion.execute(text("DELETE FROM orden WHERE id = :id"), {"id": orden_id})
        conexion.execute(text("DELETE FROM evento_sistema WHERE id = :id"), {"id": evento_id})


def test_la_orden_se_guarda_con_sus_tareas_y_repuestos(repositorio, engine) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id)
    try:
        repositorio.guardar_orden(
            orden,
            reglas={"cumple": False, "reglas": [{"codigo": "inyeccion", "cumple": False}]},
            descripcion=None,
            costo_usd=420,
            repuestos=[{"codigo": "TX-EMP-KIT", "costo_unitario": 420, "disponible": True}],
        )
        fila = repositorio.obtener_orden(orden.id)
        assert fila is not None
        assert fila["equipo"] == "B-102"
        assert fila["estado"] == "Pendiente de aprobacion"
        assert fila["costo_estimado"] == 420

        with engine.connect() as conexion:
            tareas = conexion.execute(
                text("SELECT tarea_id FROM orden_tarea WHERE orden_id = :id"),
                {"id": orden.id},
            ).fetchall()
            repuestos = conexion.execute(
                text(
                    "SELECT repuesto_id, cantidad, disponible FROM orden_repuesto "
                    "WHERE orden_id = :id"
                ),
                {"id": orden.id},
            ).fetchall()
            aprobacion = conexion.execute(
                text("SELECT decision, reglas_evaluadas FROM aprobacion WHERE orden_id = :id"),
                {"id": orden.id},
            ).fetchall()
        assert [fila[0] for fila in tareas] == ["T-B-EMP"]
        assert repuestos[0][0] == "TX-EMP-KIT"
        assert float(repuestos[0][1]) == 1
        assert repuestos[0][2] is True
        assert len(aprobacion) == 1
        assert aprobacion[0][0] == "PendienteAprobacion"
        assert aprobacion[0][1]["cumple"] is False
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_guardar_la_misma_orden_no_la_duplica(repositorio, engine) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id)
    try:
        for _ in range(3):
            repositorio.guardar_orden(orden, reglas={"cumple": False}, costo_usd=420)
        with engine.connect() as conexion:
            cuenta = conexion.execute(
                text("SELECT count(*) FROM orden WHERE id = :id"), {"id": orden.id}
            ).scalar()
            tareas = conexion.execute(
                text("SELECT count(*) FROM orden_tarea WHERE orden_id = :id"),
                {"id": orden.id},
            ).scalar()
        assert cuenta == 1
        assert tareas == 1
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_las_ordenes_pendientes_se_recuperan_con_sus_datos(repositorio, engine) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id)
    aprobada = OrdenSistema(
        id=f"ORD-B-103-TX-T02-{uuid.uuid4().hex[:8]}",
        ts="2026-11-06T10:00:00-03:00",
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
        repositorio.guardar_orden(orden, reglas={"cumple": False}, costo_usd=420)
        repositorio.guardar_orden(aprobada, reglas={"cumple": True})

        pendientes = repositorio.ordenes_pendientes()
        ids = [fila["id"] for fila in pendientes]
        assert orden.id in ids
        assert aprobada.id not in ids

        fila = next(fila for fila in pendientes if fila["id"] == orden.id)
        assert fila["equipo"] == "B-102"
        assert fila["tareas"] == ["T-B-EMP"]
        assert fila["repuestos"][0]["codigo"] == "TX-EMP-KIT"
        assert float(fila["repuestos"][0]["cantidad"]) == 1
        assert fila["motivo"]
    finally:
        _limpiar(engine, [orden.id, aprobada.id], evento_id)


def test_las_ordenes_de_un_equipo_se_listan(repositorio, engine) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id)
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": False})
        ordenes = repositorio.ordenes_por_equipo("B-102")
        assert orden.id in [fila["id"] for fila in ordenes]
        assert ordenes[0]["estado"] == "Pendiente de aprobacion"
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_una_orden_sin_evento_no_se_guarda(repositorio) -> None:
    orden = OrdenSistema(
        id=f"ORD-B-102-TX-T01-{uuid.uuid4().hex[:8]}",
        ts="2026-11-06T09:00:00-03:00",
        estado=EstadoOrden.pendiente,
        equipo="B-102",
        evento_ids=[],
        tareas=["T-B-EMP"],
        detiene_equipo=True,
        reduce_inyeccion=False,
        ejecutor="propio",
        repuestos=[],
        motivo="sin evento",
    )
    with pytest.raises(ValueError):
        repositorio.guardar_orden(orden)
