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
from sqlalchemy.exc import DBAPIError, IntegrityError

from db.repositorio import AutorizacionInvalida, ConflictoEstado, Repositorio

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
    """Limpia lo que no es evidencia terminal.

    Una decisión humana es append-only y bloquea el borrado de sus padres.
    Las órdenes con decisión quedan en la base de prueba, identificadas por un
    UUID aleatorio; las que todavía no fueron decididas sí se eliminan.
    """
    with engine.begin() as conexion:
        for orden_id in orden_ids:
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
        assert resultado["monto_habilita_usd"] == repetido["monto_habilita_usd"]
        assert repetido["orden"] == resultado["orden"]
        assert repetido["orden"]["equipo"] == "F-201A"
        assert repetido["orden"]["tareas"] == ["T-F-CAR"]
        assert [repuesto["codigo"] for repuesto in repetido["orden"]["repuestos"]] == [
            "FC-CART-25"
        ]
        assert repetido["orden"]["ejecutor"] == "Servipat"
        assert repetido["orden"]["estado"] == "Certificada"
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

        with pytest.raises(ConflictoEstado, match="ya tiene"):
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


@pytest.mark.parametrize(
    ("decision", "rol", "ejecutor", "motivo", "monto"),
    [
        ("cerrar", "supervisor", "propio", "el trabajo quedó conforme", None),
        (
            "observar",
            "inspector",
            "Servipat",
            "falta justificar un repuesto",
            None,
        ),
        ("certificar", "inspector", "Servipat", None, 1150.0),
    ],
)
def test_reprocesar_una_orden_con_decision_no_degrada_estado_ni_contenido(
    repositorio,
    engine,
    decision,
    rol,
    ejecutor,
    motivo,
    monto,
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor=ejecutor)
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        _registrar(repositorio, orden, empresa=ejecutor)
        repositorio.decidir_cierre_humano(
            orden_id=orden.id,
            decision=decision,
            actor="marta" if rol == "inspector" else "luis",
            rol=rol,
            motivo=motivo,
            monto_usd=monto,
        )

        reprocesado = orden.model_copy(
            update={
                "estado": EstadoOrden.pendiente,
                "tareas": ["T-B-ALI"],
                "repuestos": [Repuesto(codigo="TX-VAL-KIT", cantidad=1)],
            }
        )
        repositorio.guardar_orden(
            reprocesado,
            reglas={"cumple": False, "falla": "TX-T01"},
            costo_usd=1,
        )

        guardada = repositorio.obtener_orden_completa(orden.id)
        assert guardada["estado"] == {
            "cerrar": "Cerrada",
            "observar": "Observada",
            "certificar": "Certificada",
        }[decision]
        assert float(repositorio.obtener_orden(orden.id)["costo_estimado"]) == 1150.0
        assert guardada["tareas"] == ["T-F-CAR"]
        assert [repuesto["codigo"] for repuesto in guardada["repuestos"]] == ["FC-CART-25"]
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_un_segundo_cierre_no_reabre_una_orden_certificada(repositorio, engine) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor="Servipat")
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        _registrar(repositorio, orden, empresa="Servipat")
        repositorio.decidir_cierre_humano(
            orden_id=orden.id,
            decision="certificar",
            actor="marta",
            rol="inspector",
            monto_usd=1150.0,
        )

        with pytest.raises(ConflictoEstado, match="no admite otro cierre"):
            _registrar(
                repositorio,
                orden,
                cierre_id=f"CIERRE-{uuid.uuid4().hex[:8]}",
                empresa="Servipat",
            )

        with engine.connect() as conexion:
            cierres = conexion.execute(
                text("SELECT count(*) FROM cierre WHERE orden_id = :id"),
                {"id": orden.id},
            ).scalar_one()
            decisiones = conexion.execute(
                text("SELECT count(*) FROM decision_cierre WHERE orden_id = :id"),
                {"id": orden.id},
            ).scalar_one()
            habilitaciones = conexion.execute(
                text("SELECT count(*) FROM habilitacion WHERE orden_id = :id"),
                {"id": orden.id},
            ).scalar_one()
        assert cierres == 1
        assert decisiones == 1
        assert habilitaciones == 1
        assert repositorio.obtener_orden(orden.id)["estado"] == "Certificada"
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_una_orden_observada_admite_una_aclaracion_y_luego_certificar(
    repositorio,
    engine,
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor="Servipat")
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        _registrar(repositorio, orden, empresa="Servipat")
        repositorio.decidir_cierre_humano(
            orden_id=orden.id,
            decision="observar",
            actor="marta",
            rol="inspector",
            motivo="falta justificar un repuesto",
        )

        _registrar(
            repositorio,
            orden,
            cierre_id=f"CIERRE-{uuid.uuid4().hex[:8]}",
            empresa="Servipat",
        )
        assert repositorio.obtener_orden(orden.id)["estado"] == "Ejecutada"

        resultado = repositorio.decidir_cierre_humano(
            orden_id=orden.id,
            decision="certificar",
            actor="marta",
            rol="inspector",
            monto_usd=1150.0,
        )
        assert resultado["habilita"].startswith("HAB-2026-")
        assert repositorio.obtener_orden(orden.id)["estado"] == "Certificada"
        with engine.connect() as conexion:
            cierres = conexion.execute(
                text("SELECT count(*) FROM cierre WHERE orden_id = :id"),
                {"id": orden.id},
            ).scalar_one()
            decisiones = conexion.execute(
                text("SELECT count(*) FROM decision_cierre WHERE orden_id = :id"),
                {"id": orden.id},
            ).scalar_one()
            habilitaciones = conexion.execute(
                text("SELECT count(*) FROM habilitacion WHERE orden_id = :id"),
                {"id": orden.id},
            ).scalar_one()
        assert cierres == 2
        assert decisiones == 2
        assert habilitaciones == 1
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_decision_cierre_es_append_only_para_update_delete_y_truncate(
    repositorio,
    engine,
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor="Servipat")
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        _registrar(repositorio, orden, empresa="Servipat")
        repositorio.decidir_cierre_humano(
            orden_id=orden.id,
            decision="certificar",
            actor="marta",
            rol="inspector",
            monto_usd=1150.0,
        )

        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conexion:
                conexion.execute(
                    text(
                        "UPDATE decision_cierre SET actor = 'alterado' "
                        "WHERE orden_id = :id"
                    ),
                    {"id": orden.id},
                )
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conexion:
                conexion.execute(
                    text("DELETE FROM decision_cierre WHERE orden_id = :id"),
                    {"id": orden.id},
                )
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conexion:
                conexion.execute(
                    text("TRUNCATE TABLE decision_cierre, habilitacion")
                )
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_la_decision_humana_impide_borrar_su_orden_padre(repositorio, engine) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor="Servipat")
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        _registrar(repositorio, orden, empresa="Servipat")
        repositorio.decidir_cierre_humano(
            orden_id=orden.id,
            decision="certificar",
            actor="marta",
            rol="inspector",
            monto_usd=1150.0,
        )

        with pytest.raises(DBAPIError):
            with engine.begin() as conexion:
                conexion.execute(text("DELETE FROM orden WHERE id = :id"), {"id": orden.id})

        assert repositorio.obtener_orden(orden.id)["estado"] == "Certificada"
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_no_se_puede_emitir_un_segundo_habilita_para_la_misma_orden(
    repositorio,
    engine,
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor="Servipat")
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        _registrar(repositorio, orden, empresa="Servipat")
        repositorio.decidir_cierre_humano(
            orden_id=orden.id,
            decision="certificar",
            actor="marta",
            rol="inspector",
            monto_usd=1150.0,
        )

        with engine.connect() as conexion:
            enlaces = conexion.execute(
                text(
                    """
                    SELECT d.id AS decision_id, c.id AS certificacion_id
                    FROM decision_cierre d
                    JOIN certificacion c
                      ON c.cierre_id = d.cierre_id
                     AND c.decision = 'certifica'
                    WHERE d.orden_id = :id
                    """
                ),
                {"id": orden.id},
            ).mappings().one()
        with pytest.raises(IntegrityError):
            with engine.begin() as conexion:
                conexion.execute(
                    text(
                        """
                        INSERT INTO habilitacion (
                            orden_id, numero, monto, moneda, fuente_monto,
                            decision_cierre_id, certificacion_id
                        )
                        VALUES (
                            :orden, :numero, 1250, 'USD', 'CT-SVP-2025 §2.1',
                            :decision, :certificacion
                        )
                        """
                    ),
                    {
                        "orden": orden.id,
                        "numero": f"HAB-DUP-{uuid.uuid4().hex[:10]}",
                        "decision": enlaces["decision_id"],
                        "certificacion": enlaces["certificacion_id"],
                    },
                )

        assert repositorio.obtener_habilitacion(orden.id)["monto"] == 1150
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_postgresql_rechaza_un_rol_incompatible_con_la_decision(
    repositorio,
    engine,
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor="Servipat")
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        _registrar(repositorio, orden, empresa="Servipat")
        cierre_id = repositorio.obtener_cierre_de_orden(orden.id)["id"]

        with pytest.raises(IntegrityError):
            with engine.begin() as conexion:
                conexion.execute(
                    text(
                        """
                        INSERT INTO decision_cierre (
                            cierre_id, orden_id, decision, estado_orden,
                            actor, rol, motivo, evidencia
                        )
                        VALUES (
                            :cierre, :orden, 'observar', 'Observada',
                            'marta', 'supervisor', 'motivo', '{}'::jsonb
                        )
                        """
                    ),
                    {"cierre": cierre_id, "orden": orden.id},
                )
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_postgresql_rechaza_una_decision_ligada_a_otra_orden(
    repositorio,
    engine,
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    primera = _orden(evento_id, ejecutor="Servipat")
    segunda = _orden(evento_id, ejecutor="Servipat")
    try:
        repositorio.guardar_orden(primera, reglas={"cumple": True}, costo_usd=1150)
        repositorio.guardar_orden(segunda, reglas={"cumple": True}, costo_usd=1150)
        _registrar(repositorio, primera, empresa="Servipat")
        cierre_id = repositorio.obtener_cierre_de_orden(primera.id)["id"]

        with pytest.raises(IntegrityError):
            with engine.begin() as conexion:
                conexion.execute(
                    text(
                        """
                        INSERT INTO decision_cierre (
                            cierre_id, orden_id, decision, estado_orden,
                            actor, rol, motivo, evidencia
                        )
                        VALUES (
                            :cierre, :orden, 'certificar', 'Certificada',
                            'marta', 'inspector', NULL, '{}'::jsonb
                        )
                        """
                    ),
                    {"cierre": cierre_id, "orden": segunda.id},
                )
    finally:
        _limpiar(engine, [primera.id, segunda.id], evento_id)


def test_el_cierre_duplicado_devuelve_el_habilita_ya_emitido(
    repositorio,
    engine,
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor="Servipat")
    cierre_id = f"CIERRE-{uuid.uuid4().hex[:8]}"
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        _registrar(repositorio, orden, cierre_id=cierre_id, empresa="Servipat")
        decided = repositorio.decidir_cierre_humano(
            orden_id=orden.id,
            decision="certificar",
            actor="marta",
            rol="inspector",
            monto_usd=1150.0,
        )

        repetido = _registrar(
            repositorio,
            orden,
            cierre_id=cierre_id,
            empresa="Servipat",
        )

        assert repetido["nuevo"] is False
        assert repetido["habilita"] == decided["habilita"]
        assert repetido["monto_habilita_usd"] == 1150.0
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_la_autorizacion_se_comprueba_antes_del_retorno_idempotente(
    repositorio,
    engine,
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor="Servipat")
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        _registrar(repositorio, orden, empresa="Servipat")
        repositorio.decidir_cierre_humano(
            orden_id=orden.id,
            decision="certificar",
            actor="marta",
            rol="inspector",
            monto_usd=1150.0,
        )

        with pytest.raises(AutorizacionInvalida, match="inspector"):
            repositorio.decidir_cierre_humano(
                orden_id=orden.id,
                decision="certificar",
                actor="luis",
                rol="supervisor",
                monto_usd=1150.0,
            )

        with engine.connect() as conexion:
            decisiones = conexion.execute(
                text("SELECT count(*) FROM decision_cierre WHERE orden_id = :id"),
                {"id": orden.id},
            ).scalar_one()
            habilitaciones = conexion.execute(
                text("SELECT count(*) FROM habilitacion WHERE orden_id = :id"),
                {"id": orden.id},
            ).scalar_one()
        assert decisiones == 1
        assert habilitaciones == 1
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_postgresql_rechaza_una_transicion_invalida_de_orden(
    repositorio,
    engine,
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id)
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=672)

        with pytest.raises(DBAPIError, match="Transición inválida"):
            with engine.begin() as conexion:
                conexion.execute(
                    text(
                        "UPDATE orden SET estado = CAST('Cerrada' AS estado_orden) "
                        "WHERE id = :id"
                    ),
                    {"id": orden.id},
                )

        assert repositorio.obtener_orden(orden.id)["estado"] == "Aprobada"
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_postgresql_protege_el_contenido_de_una_orden_certificada(
    repositorio,
    engine,
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor="Servipat")
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        _registrar(repositorio, orden, empresa="Servipat")
        repositorio.decidir_cierre_humano(
            orden_id=orden.id,
            decision="certificar",
            actor="marta",
            rol="inspector",
            monto_usd=1150.0,
        )

        with pytest.raises(DBAPIError, match="ya decidido"):
            with engine.begin() as conexion:
                conexion.execute(
                    text("UPDATE orden SET motivo = 'motivo alterado' WHERE id = :id"),
                    {"id": orden.id},
                )
        with pytest.raises(DBAPIError, match="ya decidida"):
            with engine.begin() as conexion:
                conexion.execute(
                    text(
                        "UPDATE orden_tarea SET tarea_id = 'T-ALTERADA' "
                        "WHERE orden_id = :id"
                    ),
                    {"id": orden.id},
                )

        guardada = repositorio.obtener_orden_completa(orden.id)
        assert guardada["motivo"] is None
        assert guardada["tareas"] == ["T-F-CAR"]
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_postgresql_rechaza_una_segunda_certificacion_final(
    repositorio,
    engine,
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor="Servipat")
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        _registrar(repositorio, orden, empresa="Servipat")
        repositorio.decidir_cierre_humano(
            orden_id=orden.id,
            decision="certificar",
            actor="marta",
            rol="inspector",
            monto_usd=1150.0,
        )

        with pytest.raises(IntegrityError):
            with engine.begin() as conexion:
                cierre_id = conexion.execute(
                    text(
                        """
                        INSERT INTO cierre (
                            externa_id, orden_id, tecnico, empresa, transcripcion,
                            tareas_hechas, repuestos_usados, horas, prueba_final,
                            diferencias
                        )
                        VALUES (
                            :externa, :orden, 'otro técnico', 'Servipat',
                            'segundo cierre', '[]'::jsonb, '[]'::jsonb,
                            1.0, 'prueba', '[]'::jsonb
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "externa": f"CIERRE-{uuid.uuid4().hex[:8]}",
                        "orden": orden.id,
                    },
                ).scalar_one()
                conexion.execute(
                    text(
                        """
                        INSERT INTO certificacion (
                            orden_id, cierre_id, inspector, decision, motivo, rol
                        )
                        VALUES (
                            :orden, :cierre, 'otro inspector', 'certifica',
                            'segunda certificación', 'inspector'
                        )
                        """
                    ),
                    {"orden": orden.id, "cierre": cierre_id},
                )

        with engine.connect() as conexion:
            certificaciones = conexion.execute(
                text(
                    "SELECT count(*) FROM certificacion "
                    "WHERE orden_id = :id AND decision = 'certifica'"
                ),
                {"id": orden.id},
            ).scalar_one()
        assert certificaciones == 1
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_el_cierre_con_motivo_tras_aprobacion_humana_puede_registrarse(
    repositorio,
    engine,
) -> None:
    """La aprobación humana no bloquea el motivo técnico de Aprobada a Ejecutada."""
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor="Servipat")
    pendiente = orden.model_copy(update={"estado": EstadoOrden.pendiente})
    try:
        repositorio.guardar_orden(
            pendiente,
            reglas={"cumple": False},
            costo_usd=1150,
        )
        aprobado = repositorio.decidir_orden(
            orden.id,
            "Aprobada",
            usuario="ana",
            rol="planificador",
        )
        assert aprobado is not None

        resultado = _registrar(
            repositorio,
            orden,
            empresa="Servipat",
            motivo="El cierre declara un repuesto que no estaba en la orden",
            diferencias=[
                {
                    "tipo": "repuesto_extra",
                    "detalle": "El cierre declara un juego de sellos",
                    "sustantiva": True,
                }
            ],
        )

        assert resultado["nuevo"] is True
        guardada = repositorio.obtener_orden(orden.id)
        assert guardada["estado"] == "Ejecutada"
        assert "no estaba en la orden" in guardada["motivo"]
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_postgresql_exige_decision_para_entrar_en_estado_terminal(
    repositorio,
    engine,
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor="Servipat")
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        _registrar(repositorio, orden, empresa="Servipat")

        with pytest.raises(DBAPIError, match="decisión humana"):
            with engine.begin() as conexion:
                conexion.execute(
                    text(
                        "UPDATE orden "
                        "SET estado = CAST('Certificada' AS estado_orden) "
                        "WHERE id = :id"
                    ),
                    {"id": orden.id},
                )

        assert repositorio.obtener_orden(orden.id)["estado"] == "Ejecutada"
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_postgresql_exige_habilita_para_entrar_en_certificada(
    repositorio,
    engine,
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor="Servipat")
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        _registrar(repositorio, orden, empresa="Servipat")
        cierre_id = repositorio.obtener_cierre_de_orden(orden.id)["id"]
        with engine.begin() as conexion:
            conexion.execute(
                text(
                    """
                    INSERT INTO decision_cierre (
                        cierre_id, orden_id, decision, estado_orden,
                        actor, rol, motivo, evidencia
                    )
                    VALUES (
                        :cierre, :orden, 'certificar', 'Certificada',
                        'marta', 'inspector', NULL, '{}'::jsonb
                    )
                    """
                ),
                {"cierre": cierre_id, "orden": orden.id},
            )
            conexion.execute(
                text(
                    """
                    INSERT INTO certificacion (
                        orden_id, cierre_id, inspector, decision, motivo, rol
                    )
                    VALUES (
                        :orden, :cierre, 'marta', 'certifica', NULL, 'inspector'
                    )
                    """
                ),
                {"orden": orden.id, "cierre": cierre_id},
            )

        with pytest.raises(DBAPIError, match="certificación y habilita"):
            with engine.begin() as conexion:
                conexion.execute(
                    text(
                        "UPDATE orden "
                        "SET estado = CAST('Certificada' AS estado_orden) "
                        "WHERE id = :id"
                    ),
                    {"id": orden.id},
                )

        assert repositorio.obtener_orden(orden.id)["estado"] == "Ejecutada"
        assert repositorio.obtener_habilitacion(orden.id) is None
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_habilitacion_es_append_only_para_update_delete_y_truncate(
    repositorio,
    engine,
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor="Servipat")
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        _registrar(repositorio, orden, empresa="Servipat")
        repositorio.decidir_cierre_humano(
            orden_id=orden.id,
            decision="certificar",
            actor="marta",
            rol="inspector",
            monto_usd=1150.0,
        )

        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conexion:
                conexion.execute(
                    text(
                        "UPDATE habilitacion SET monto = 999999 "
                        "WHERE orden_id = :id"
                    ),
                    {"id": orden.id},
                )
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conexion:
                conexion.execute(
                    text("DELETE FROM habilitacion WHERE orden_id = :id"),
                    {"id": orden.id},
                )
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conexion:
                conexion.execute(text("TRUNCATE TABLE habilitacion"))

        habilitacion = repositorio.obtener_habilitacion(orden.id)
        assert habilitacion is not None
        assert float(habilitacion["monto"]) == 1150.0
    finally:
        _limpiar(engine, [orden.id], evento_id)


@pytest.mark.parametrize(
    "monto",
    [None, 0.0, -1.0, "1150", True, float("nan"), float("inf"), float("-inf")],
)
def test_el_repositorio_rechaza_un_monto_de_habilita_invalido(
    repositorio,
    engine,
    monto,
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor="Servipat")
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        _registrar(repositorio, orden, empresa="Servipat")

        with pytest.raises(ValueError, match="monto"):
            repositorio.decidir_cierre_humano(
                orden_id=orden.id,
                decision="certificar",
                actor="marta",
                rol="inspector",
                monto_usd=monto,
            )

        assert repositorio.obtener_orden(orden.id)["estado"] == "Ejecutada"
        assert repositorio.obtener_habilitacion(orden.id) is None
        with engine.connect() as conexion:
            decisiones = conexion.execute(
                text("SELECT count(*) FROM decision_cierre WHERE orden_id = :id"),
                {"id": orden.id},
            ).scalar_one()
        assert decisiones == 0
    finally:
        _limpiar(engine, [orden.id], evento_id)


def test_no_se_pueden_insertar_dos_aclaraciones_para_la_misma_observacion(
    repositorio,
    engine,
) -> None:
    with engine.begin() as conexion:
        evento_id = _evento(conexion)
    orden = _orden(evento_id, ejecutor="Servipat")
    try:
        repositorio.guardar_orden(orden, reglas={"cumple": True}, costo_usd=1150)
        _registrar(repositorio, orden, empresa="Servipat")
        repositorio.decidir_cierre_humano(
            orden_id=orden.id,
            decision="observar",
            actor="marta",
            rol="inspector",
            motivo="falta justificar un repuesto",
        )
        observada = repositorio.obtener_cierre_de_orden(orden.id)
        _registrar(
            repositorio,
            orden,
            cierre_id=f"CIERRE-{uuid.uuid4().hex[:8]}",
            empresa="Servipat",
        )

        with pytest.raises(IntegrityError):
            with engine.begin() as conexion:
                conexion.execute(
                    text(
                        """
                        INSERT INTO cierre (
                            externa_id, orden_id, tecnico, empresa, transcripcion,
                            tareas_hechas, repuestos_usados, horas, prueba_final,
                            diferencias, aclara_cierre_id
                        )
                        VALUES (
                            :externa, :orden, 'otro técnico', 'Servipat',
                            'segunda aclaración', '[]'::jsonb, '[]'::jsonb,
                            1.0, 'prueba', '[]'::jsonb, :observada
                        )
                        """
                    ),
                    {
                        "externa": f"CIERRE-{uuid.uuid4().hex[:8]}",
                        "orden": orden.id,
                        "observada": observada["id"],
                    },
                )
    finally:
        _limpiar(engine, [orden.id], evento_id)
