"""Persistencia inmutable del manifiesto y del ledger de evidencia (P0-03)."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import pytest
from planta_contrato import EstadoOrden, OrdenSistema, Repuesto
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

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
            "payload": json.dumps(
                {
                    "equipo": equipo,
                    "resumen": "B-102 goteo",
                    "parte_id": "parte-confirmado-1",
                }
            ),
        },
    )
    return evento_id


def _orden(
    evento_id: str,
    *,
    estado: EstadoOrden = EstadoOrden.pendiente,
    ejecutor: str = "propio",
) -> OrdenSistema:
    return OrdenSistema(
        id=f"ORD-B-102-TX-T01-{uuid.uuid4().hex[:8]}",
        ts="2026-11-14T09:00:00-03:00",
        estado=estado,
        equipo="B-102",
        evento_ids=[evento_id],
        tareas=["T-B-EMP"],
        detiene_equipo=True,
        reduce_inyeccion=False,
        ejecutor=ejecutor,
        repuestos=[Repuesto(codigo="TX-EMP-KIT", cantidad=1)],
        motivo="La evidencia exige revisión humana" if estado is EstadoOrden.pendiente else None,
    )


def _manifiesto(evento_id: str, orden_id: str) -> dict[str, Any]:
    return {
        "version": 1,
        "evento_id": evento_id,
        "orden_id": orden_id,
        "parte": {
            "estado": "disponible",
            "id": "parte-confirmado-1",
            "filas": [
                {
                    "tag": "B-102",
                    "variable": "vibracion",
                    "valor": 8.3,
                    "unidad": "mm/s",
                    "observacion": "goteo audible",
                }
            ],
        },
        "fotos": {
            "estado": "disponible",
            "elementos": [
                {
                    "id": "foto-1",
                    "url": "https://planta.example/fotos/B-102.jpg",
                    "ruta_local": "/datos/NOTA-1/B-102.jpg",
                    "hash_sha256": "a" * 64,
                    "vision": {
                        "estado": "ok",
                        "danio_estructural": True,
                        "descripcion": "grieta visible",
                        "detalle": "ok",
                        "modelo": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
                        "tokens_prompt": 861,
                        "tokens_completion": 191,
                        "segundos": 2.9,
                    },
                }
            ],
        },
        "tendencia": {
            "estado": "disponible",
            "elementos": [
                {
                    "variable": "vibracion",
                    "tag": "B-102",
                    "unidad": "mm/s",
                    "desde": "2026-11-14T03:00:00+00:00",
                    "hasta": "2026-11-14T09:00:00+00:00",
                    "ventana": "6 h",
                    "fuente": "historiador",
                    "muestras": 24,
                    "minimo": 4.1,
                    "maximo": 8.3,
                    "promedio": 6.2,
                    "tendencia": "sube",
                    "resumen": "Tendencia vibracion últimas 6 h",
                }
            ],
        },
        "fragmentos": {
            "estado": "disponible",
            "elementos": [
                {
                    "cita": "MBP-4 §13.2",
                    "documento": "MBP-4",
                    "seccion": "13.2",
                    "texto": "Inspeccionar el equipo.",
                    "titulo": "Inspección",
                    "vigente": True,
                }
            ],
        },
        "citas": ["MBP-4 §13.2"],
    }


def _limpiar(engine, orden_id: str, evento_id: str) -> None:
    """Limpia la orden si todavía no tiene una decisión humana terminal.

    ``decision_cierre`` es append-only y, desde la migración ``0010``, impide
    que sus padres se borren por cascada.  La prueba conserva esa evidencia.
    """
    with engine.begin() as conexion:
        decidida = conexion.execute(
            text("SELECT count(*) FROM decision_cierre WHERE orden_id = :id"),
            {"id": orden_id},
        ).scalar_one()
        if not decidida:
            conexion.execute(text("DELETE FROM orden WHERE id = :id"), {"id": orden_id})
        referencias = conexion.execute(
            text("SELECT count(*) FROM orden WHERE evento_id = :id"),
            {"id": evento_id},
        ).scalar_one()
        if not referencias:
            conexion.execute(
                text("DELETE FROM evento_sistema WHERE id = :id"),
                {"id": evento_id},
            )


def test_manifiesto_y_ledger_permiten_reconstruir_la_orden(
    repositorio,
    engine,
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id)
    try:
        repositorio.guardar_orden(
            orden,
            manifiesto=_manifiesto(evento_id, orden.id),
            reglas={
                "cumple": False,
                "falla": "TX-T01",
                "causas": [{"causa": "desgaste"}],
                "citas": ["MBP-4 §13.2"],
                "evidencia_vision": [
                    {
                        "foto_id": "foto-1",
                        "foto_url": "https://planta.example/fotos/B-102.jpg",
                        "hash_sha256": "a" * 64,
                        "modelo": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
                    }
                ],
            },
            analisis={"falla": {"codigo": "TX-T01"}},
            propuesta={"tareas": ["T-B-EMP"], "costo_usd": 420},
        )

        manifiesto = repositorio.obtener_manifiesto("orden", orden.id)
        assert manifiesto is not None
        assert manifiesto["hash_verificado"] is True
        assert manifiesto["contenido"]["parte"]["id"] == "parte-confirmado-1"
        assert manifiesto["contenido"]["fotos"]["elementos"][0]["hash_sha256"] == "a" * 64
        assert manifiesto["contenido"]["tendencia"]["elementos"][0]["ventana"] == "6 h"
        assert manifiesto["contenido"]["fragmentos"]["elementos"][0]["cita"] == "MBP-4 §13.2"

        ledger = repositorio.obtener_ledger_de_orden(orden.id)
        tipos = {entrada["tipo"] for entrada in ledger}
        assert {"evento", "evidencia", "analisis", "propuesta", "reglas"} <= tipos
        evidencia = next(entrada for entrada in ledger if entrada["tipo"] == "evidencia")
        assert evidencia["modelo"].startswith("nvidia/")
        assert "vision.describir" in evidencia["herramientas"]
        assert evidencia["tokens_prompt"] == 861
        assert evidencia["tokens_completion"] == 191
        assert float(evidencia["segundos"]) == 2.9

        reconstruccion = repositorio.reconstruir_orden(orden.id)
        assert reconstruccion is not None
        assert reconstruccion["orden"]["id"] == orden.id
        assert reconstruccion["evento"]["id"] == evento_id
        assert reconstruccion["manifiestos"][0]["hash_verificado"] is True
        assert reconstruccion["integridad"]["manifiestos_verificados"] is True
        assert reconstruccion["integridad"]["entradas_ledger"] == len(ledger)

        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conexion:
                conexion.execute(
                    text(
                        "UPDATE manifiesto_evidencia "
                        "SET contenido = CAST(:contenido AS jsonb) WHERE id = :id"
                    ),
                    {
                        "contenido": json.dumps({"alterado": True}),
                        "id": manifiesto["id"],
                    },
                )
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conexion:
                conexion.execute(
                    text("DELETE FROM manifiesto_evidencia WHERE id = :id"),
                    {"id": manifiesto["id"]},
                )
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conexion:
                conexion.execute(
                    text("DELETE FROM ledger_decisiones WHERE orden_id = :id"),
                    {"id": orden.id},
                )
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conexion:
                conexion.execute(text("TRUNCATE TABLE manifiesto_evidencia"))
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conexion:
                conexion.execute(text("TRUNCATE TABLE ledger_decisiones"))

        decision = repositorio.decidir_orden(
            orden.id,
            "Aprobada",
            usuario="ana",
            rol="planificador",
        )
        assert decision is not None
        aprobacion = next(
            entrada
            for entrada in repositorio.obtener_ledger_de_orden(orden.id)
            if entrada["tipo"] == "aprobacion"
        )
        assert aprobacion["actor"] == "ana"
        assert aprobacion["rol"] == "planificador"
        assert aprobacion["estado"] == "Aprobada"
        assert aprobacion["evidencia"]["hash_sha256"] == manifiesto["hash_sha256"]
    finally:
        _limpiar(engine, orden.id, evento_id)


def test_el_cierre_y_la_certificacion_tienen_manifiesto_y_ledger(
    repositorio,
    engine,
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, estado=EstadoOrden.aprobada, ejecutor="Servipat")
    cierre_id = f"CIERRE-{uuid.uuid4().hex[:8]}"
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        repositorio.registrar_cierre(
            cierre_id=cierre_id,
            orden_id=orden.id,
            anio=2026,
            tecnico="Ramón Cayupán",
            empresa="Servipat",
            transcripcion="Reemplacé el sello.",
            tareas_hechas=["T-B-EMP"],
            repuestos_usados=[{"codigo": "TX-EMP-KIT", "cantidad": 1}],
            horas=2.5,
            prueba_final="quedó en servicio",
            diferencias=[],
            fotos=[
                {
                    "id": "foto-cierre-1",
                    "url": "https://planta.example/fotos/cierre.jpg",
                    "ruta_local": "/datos/CIERRE/cierre.jpg",
                    "hash_sha256": "b" * 64,
                    "vision": {
                        "estado": "no_disponible",
                        "danio_estructural": "no_disponible",
                        "descripcion": "no_disponible",
                        "detalle": "no_disponible",
                        "modelo": "no_disponible",
                        "tokens_prompt": 0,
                        "tokens_completion": 0,
                        "segundos": 0.0,
                    },
                }
            ],
        )

        resultado = repositorio.decidir_cierre_humano(
            orden_id=orden.id,
            decision="certificar",
            actor="marta",
            rol="inspector",
            monto_usd=1150.0,
            fuente_monto="CT-SVP-2025 §2.1",
        )
        assert resultado["habilita"].startswith("HAB-2026-")

        cierre = repositorio.obtener_manifiesto("cierre", cierre_id)
        decision = repositorio.obtener_manifiesto("decision_cierre", cierre_id)
        assert cierre is not None
        assert decision is not None
        assert cierre["hash_verificado"] is True
        assert decision["hash_verificado"] is True
        foto = cierre["contenido"]["evidencia_cierre"]["fotos"]["elementos"][0]
        assert foto["id"] == "foto-cierre-1"
        assert foto["hash_sha256"] == "b" * 64
        assert decision["contenido"]["decision"]["actor"] == "marta"
        assert decision["contenido"]["decision"]["rol"] == "inspector"
        assert decision["contenido"]["certificacion"]["estado"] == "certificada"
        assert decision["contenido"]["certificacion"]["habilita"]["numero"].startswith("HAB-2026-")

        ledger = repositorio.obtener_ledger_de_orden(orden.id)
        cierre_ledger = next(entrada for entrada in ledger if entrada["tipo"] == "cierre")
        decision_ledger = next(
            entrada for entrada in ledger if entrada["tipo"] == "decision_cierre"
        )
        assert cierre_ledger["estado"] == "Ejecutada"
        assert decision_ledger["actor"] == "marta"
        assert decision_ledger["rol"] == "inspector"
        assert decision_ledger["estado"] == "Certificada"
        assert decision_ledger["datos"]["habilita"].startswith("HAB-2026-")
    finally:
        _limpiar(engine, orden.id, evento_id)
