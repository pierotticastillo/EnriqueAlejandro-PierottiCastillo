"""Tests de la persistencia del cierre, la certificación y el habilita (Fase 7).

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


def _evento(conexion, equipo: str = "F-201A") -> str:
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
            "payload": json.dumps({"equipo": equipo, "resumen": "cartuchos colmatados"}),
        },
    )
    return evento_id


def _orden(evento_id: str, *, ejecutor: str = "propio") -> OrdenSistema:
    return OrdenSistema(
        id=f"ORD-F-201A-FC-T01-{uuid.uuid4().hex[:8]}",
        ts="2026-11-08T12:00:00-03:00",
        estado=EstadoOrden.aprobada,
        equipo="F-201A",
        evento_ids=[evento_id],
        tareas=["T-F-CAR"],
        detiene_equipo=False,
        reduce_inyeccion=False,
        ejecutor=ejecutor,
        repuestos=[Repuesto(codigo="FC-CART-25", cantidad=48)],
        motivo=None,
    )


def _limpiar(engine, orden_ids: list[str], evento_id: str) -> None:
    with engine.begin() as conexion:
        for orden_id in orden_ids:
            conexion.execute(text("DELETE FROM orden WHERE id = :id"), {"id": orden_id})
        conexion.execute(text("DELETE FROM evento_sistema WHERE id = :id"), {"id": evento_id})


def _registrar(repositorio: Repositorio, orden: OrdenSistema, **datos):
    base = {
        "cierre_id": f"CIERRE-{uuid.uuid4().hex[:8]}",
        "orden_id": orden.id,
        "anio": 2026,
        "tecnico": "Ramón Cayupán",
        "empresa": "propio",
        "transcripcion": "Hice cambio de cartuchos.",
        "tareas_hechas": ["T-F-CAR"],
        "repuestos_usados": [{"codigo": "FC-CART-25", "cantidad": 48}],
        "horas": 3.2,
        "prueba_final": "quedo en servicio",
        "diferencias": [],
        "motivo": None,
    }
    return repositorio.registrar_cierre(**(base | datos))


# ------------------------------------------------------------------- los tests


def test_el_cierre_automatico_se_guarda_y_espera_decision_humana(
    repositorio, engine
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id)
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=672)
        _registrar(
            repositorio,
            orden,
            motivo="El cierre declara un repuesto que no estaba en la orden",
            diferencias=[
                {
                    "tipo": "repuesto_extra",
                    "detalle": "El cierre declara 1 de juego de sellos",
                    "sustantiva": True,
                }
            ],
        )
        with engine.connect() as conexion:
            cierres = conexion.execute(
                text(
                    "SELECT orden_id, tecnico, empresa, horas, diferencias "
                    "FROM cierre WHERE orden_id = :id"
                ),
                {"id": orden.id},
            ).fetchall()
            certificaciones = conexion.execute(
                text(
                    "SELECT count(*) FROM certificacion WHERE orden_id = :id"
                ),
                {"id": orden.id},
            ).scalar()
            orden_fila = conexion.execute(
                text("SELECT estado, motivo FROM orden WHERE id = :id"),
                {"id": orden.id},
            ).fetchone()
        assert len(cierres) == 1
        assert cierres[0][1] == "Ramón Cayupán"
        assert cierres[0][2] == "propio"
        assert float(cierres[0][3]) == 3.2
        assert cierres[0][4][0]["tipo"] == "repuesto_extra"
        assert certificaciones == 0
        assert orden_fila[0] == "Ejecutada"
        assert "no estaba en la orden" in orden_fila[1]
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_el_supervisor_cierra_el_trabajo_propio_sin_certificarlo(
    repositorio, engine
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id)
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=672)
        _registrar(repositorio, orden)

        resultado = repositorio.decidir_cierre_humano(
            orden_id=orden.id,
            decision="cerrar",
            actor="luis",
            rol="supervisor",
            motivo="el trabajo quedó conforme",
        )

        assert resultado["decision"]["estado_orden"] == "Cerrada"
        assert repositorio.obtener_orden(orden.id)["estado"] == "Cerrada"
        with engine.connect() as conexion:
            certificaciones = conexion.execute(
                text("SELECT count(*) FROM certificacion WHERE orden_id = :id"),
                {"id": orden.id},
            ).scalar()
            habilitaciones = conexion.execute(
                text("SELECT count(*) FROM habilitacion WHERE orden_id = :id"),
                {"id": orden.id},
            ).scalar()
            decision = conexion.execute(
                text(
                    "SELECT decision, actor, rol, motivo "
                    "FROM decision_cierre WHERE orden_id = :id"
                ),
                {"id": orden.id},
            ).mappings().one()
        assert certificaciones == 0
        assert habilitaciones == 0
        assert decision["decision"] == "cerrar"
        assert decision["actor"] == "luis"
        assert decision["rol"] == "supervisor"
        assert decision["motivo"] == "el trabajo quedó conforme"
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_el_habilita_se_emite_solo_tras_certificar_con_numero_y_monto(
    repositorio, engine
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor="Servipat")
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        cierre = _registrar(repositorio, orden)

        assert cierre["habilita"] is None
        assert repositorio.obtener_habilitacion(orden.id) is None

        resultado = repositorio.decidir_cierre_humano(
            orden_id=orden.id,
            decision="certificar",
            actor="marta",
            rol="inspector",
            monto_usd=1150.0,
            fuente_monto="CT-SVP-2025 §2.1",
        )

        assert resultado["habilita"].startswith("HAB-2026-")
        assert resultado["monto_habilita_usd"] == 1150.0
        habilitacion = repositorio.obtener_habilitacion(orden.id)
        assert habilitacion is not None
        assert habilitacion["moneda"] == "USD"
        assert float(habilitacion["monto"]) == 1150.0
        assert habilitacion["fuente_monto"] == "CT-SVP-2025 §2.1"
        assert repositorio.obtener_orden(orden.id)["estado"] == "Certificada"
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_el_cierre_que_vuelve_no_se_duplica(repositorio, engine) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor="Servipat")
    cierre_id = f"CIERRE-{uuid.uuid4().hex[:8]}"
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        for _ in range(3):
            _registrar(repositorio, orden, cierre_id=cierre_id)

        assert repositorio.obtener_orden(orden.id)["estado"] == "Ejecutada"
        assert repositorio.obtener_habilitacion(orden.id) is None

        resultado = repositorio.decidir_cierre_humano(
            orden_id=orden.id,
            decision="certificar",
            actor="marta",
            rol="inspector",
            monto_usd=1150.0,
        )
        repetido = repositorio.decidir_cierre_humano(
            orden_id=orden.id,
            decision="certificar",
            actor="marta",
            rol="inspector",
            monto_usd=1150.0,
        )

        assert resultado["habilita"] == repetido["habilita"]
        with engine.connect() as conexion:
            cierres = conexion.execute(
                text("SELECT count(*) FROM cierre WHERE orden_id = :id"),
                {"id": orden.id},
            ).scalar()
            decisiones = conexion.execute(
                text("SELECT count(*) FROM decision_cierre WHERE orden_id = :id"),
                {"id": orden.id},
            ).scalar()
            certificaciones = conexion.execute(
                text("SELECT count(*) FROM certificacion WHERE orden_id = :id"),
                {"id": orden.id},
            ).scalar()
            habilitaciones = conexion.execute(
                text("SELECT count(*) FROM habilitacion WHERE orden_id = :id"),
                {"id": orden.id},
            ).scalar()
        assert cierres == 1
        assert decisiones == 1
        assert certificaciones == 1
        assert habilitaciones == 1
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_los_numeros_de_habilita_no_se_repiten(repositorio, engine) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    ordenes = [_orden(evento_id, ejecutor="Servipat") for _ in range(2)]
    try:
        for orden in ordenes:
            repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
            _registrar(repositorio, orden)
            repositorio.decidir_cierre_humano(
                orden_id=orden.id,
                decision="certificar",
                actor="marta",
                rol="inspector",
                monto_usd=1150.0,
            )
        numeros = [
            repositorio.obtener_habilitacion(orden.id)["numero"] for orden in ordenes
        ]
        assert numeros[0] != numeros[1]
        assert all(numero.startswith("HAB-2026-") for numero in numeros)
    finally:
        _limpiar(engine, [orden.id for orden in ordenes], evento_id)


def test_observar_una_contratista_exige_motivo_y_no_emite_habilita(
    repositorio, engine
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor="Servipat")
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        _registrar(repositorio, orden)

        with pytest.raises(ValueError, match="motivo"):
            repositorio.decidir_cierre_humano(
                orden_id=orden.id,
                decision="observar",
                actor="marta",
                rol="inspector",
            )

        assert repositorio.obtener_orden(orden.id)["estado"] == "Ejecutada"
        assert repositorio.obtener_habilitacion(orden.id) is None
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_una_decision_de_cierre_no_se_cambia(repositorio, engine) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor="Servipat")
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        _registrar(repositorio, orden)
        repositorio.decidir_cierre_humano(
            orden_id=orden.id,
            decision="observar",
            actor="marta",
            rol="inspector",
            motivo="falta justificar un repuesto",
        )

        with pytest.raises(ValueError, match="ya tiene"):
            repositorio.decidir_cierre_humano(
                orden_id=orden.id,
                decision="certificar",
                actor="marta",
                rol="inspector",
                monto_usd=1150.0,
            )

        assert repositorio.obtener_orden(orden.id)["estado"] == "Observada"
        assert repositorio.obtener_habilitacion(orden.id) is None
        with engine.connect() as conexion:
            decisiones = conexion.execute(
                text("SELECT count(*) FROM decision_cierre WHERE orden_id = :id"),
                {"id": orden.id},
            ).scalar()
        assert decisiones == 1
    finally:
        _limpiar(engine, [orden.id], evento_id)
