"""Tests de constraints e idempotencia del esquema (Fase 0.5).

Requieren un PostgreSQL con las migraciones aplicadas:

    TEST_DATABASE_URL=postgresql+psycopg://pia3:pia3@localhost:5432/pia3 pytest

Sin esa variable los tests se omiten para no romper una corrida sin base.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

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


def _insertar_evento(conexion, evento_id: str | None = None) -> str:
    evento_id = evento_id or f"ev-{uuid.uuid4()}"
    conexion.execute(
        text(
            """
            INSERT INTO evento_sistema (id, origen, estado, payload)
            VALUES (:id, 'parte', 'Sin novedad', CAST(:payload AS jsonb))
            """
        ),
        {"id": evento_id, "payload": json.dumps({})},
    )
    return evento_id


def _insertar_orden(conexion, evento_id: str, orden_id: str | None = None) -> str:
    orden_id = orden_id or f"ord-{uuid.uuid4()}"
    conexion.execute(
        text(
            """
            INSERT INTO orden (id, evento_id, equipo, estado)
            VALUES (:id, :evento_id, 'B-101', 'Borrador')
            """
        ),
        {"id": orden_id, "evento_id": evento_id},
    )
    return orden_id


def _borrar(engine, tabla: str, columna: str, valor: str) -> None:
    with engine.begin() as conexion:
        conexion.execute(
            text(f"DELETE FROM {tabla} WHERE {columna} = :valor"),  # noqa: S608
            {"valor": valor},
        )


def test_evento_sistema_no_se_duplica(engine) -> None:
    with engine.begin() as conexion:
        evento_id = _insertar_evento(conexion)

    try:
        with pytest.raises(IntegrityError):
            with engine.begin() as conexion:
                _insertar_evento(conexion, evento_id)
    finally:
        _borrar(engine, "evento_sistema", "id", evento_id)


def test_orden_no_se_duplica(engine) -> None:
    with engine.begin() as conexion:
        evento_id = _insertar_evento(conexion)
        orden_id = _insertar_orden(conexion, evento_id)

    try:
        with pytest.raises(IntegrityError):
            with engine.begin() as conexion:
                _insertar_orden(conexion, evento_id, orden_id)
    finally:
        _borrar(engine, "orden", "id", orden_id)
        _borrar(engine, "evento_sistema", "id", evento_id)


def test_idempotency_key_no_se_duplica(engine) -> None:
    clave = f"clave-{uuid.uuid4()}"
    insertar = text(
        """
        INSERT INTO idempotency_key (clave, tipo, referencia_id)
        VALUES (:clave, 'evento', :referencia)
        """
    )

    with engine.begin() as conexion:
        conexion.execute(insertar, {"clave": clave, "referencia": "ref-1"})

    try:
        with pytest.raises(IntegrityError):
            with engine.begin() as conexion:
                conexion.execute(insertar, {"clave": clave, "referencia": "ref-2"})
    finally:
        _borrar(engine, "idempotency_key", "clave", clave)


def test_alerta_seguridad_rechaza_tipo_invalido(engine) -> None:
    with engine.begin() as conexion:
        evento_id = _insertar_evento(conexion)

    try:
        with pytest.raises(IntegrityError):
            with engine.begin() as conexion:
                conexion.execute(
                    text(
                        """
                        INSERT INTO alerta_seguridad
                            (evento_id, tipo, publicada_en)
                        VALUES (:evento_id, 'S9', now())
                        """
                    ),
                    {"evento_id": evento_id},
                )
    finally:
        _borrar(engine, "evento_sistema", "id", evento_id)


def test_certificacion_rechaza_decision_invalida(engine) -> None:
    with engine.begin() as conexion:
        evento_id = _insertar_evento(conexion)
        orden_id = _insertar_orden(conexion, evento_id)

    try:
        with pytest.raises(IntegrityError):
            with engine.begin() as conexion:
                conexion.execute(
                    text(
                        """
                        INSERT INTO certificacion (orden_id, inspector, decision)
                        VALUES (:orden_id, 'inspector', 'aprueba')
                        """
                    ),
                    {"orden_id": orden_id},
                )
    finally:
        _borrar(engine, "orden", "id", orden_id)
        _borrar(engine, "evento_sistema", "id", evento_id)


def test_orden_rechaza_estado_fuera_del_enum(engine) -> None:
    with engine.begin() as conexion:
        evento_id = _insertar_evento(conexion)

    try:
        with pytest.raises(DBAPIError):
            with engine.begin() as conexion:
                conexion.execute(
                    text(
                        """
                        INSERT INTO orden (id, evento_id, equipo, estado)
                        VALUES (:id, :evento_id, 'B-101', 'Inventado')
                        """
                    ),
                    {"id": f"ord-{uuid.uuid4()}", "evento_id": evento_id},
                )
    finally:
        _borrar(engine, "evento_sistema", "id", evento_id)
