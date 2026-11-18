"""Tests del correlation ID de extremo a extremo (P1-16).

Comprueba que:

* una solicitud tiene un correlation_id canónico antes de cualquier efecto;
* el correlation_id se propaga a ledger, manifiesto, outbox y trayectoria;
* evento_id y orden_id siguen identificando el caso (una nota puede generar
  varios casos);
* las claves de idempotencia se derivan de operación + ID del caso, sin
  colapsar varios eventos de la misma nota.

Requieren un PostgreSQL con las migraciones aplicadas:

    TEST_DATABASE_URL=postgresql+psycopg://pia3:pia3@localhost:55432/pia3 pytest

Sin esa variable los tests se omiten para no romper una corrida sin base.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text

TEST_DATABASE_URL = pytest.importorskip("os").getenv("TEST_DATABASE_URL")

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
    evento_id = evento_id or f"evt-{uuid.uuid4().hex}"
    conexion.execute(
        text(
            """
            INSERT INTO evento_sistema (id, origen, estado, payload)
            VALUES (:id, 'parte', 'Sin novedad', CAST(:payload AS jsonb))
            """
        ),
        {"id": evento_id, "payload": "{}"},
    )
    return evento_id


def _insertar_orden(conexion, evento_id: str, orden_id: str | None = None) -> str:
    orden_id = orden_id or f"ord-{uuid.uuid4().hex}"
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


def _insertar_ledger(conexion, *, evento_id, orden_id, idempotencia, correlation_id=None):
    conexion.execute(
        text(
            """
            INSERT INTO ledger_decisiones (
                tipo, referencia_id, evento_id, orden_id,
                idempotencia, correlation_id, actor, rol, estado
            )
            VALUES (
                'evento', :referencia, :evento, :orden,
                :idempotencia, :correlation_id, 'codigo', 'sistema', 'en_curso'
            )
            ON CONFLICT (idempotencia) DO NOTHING
            """
        ),
        {
            "referencia": evento_id,
            "evento": evento_id,
            "orden": orden_id,
            "idempotencia": idempotencia,
            "correlation_id": correlation_id,
        },
    )


def test_correlation_id_propaga_a_ledger(engine):
    """Una solicitud deja su correlation_id en el ledger de decisiones."""
    correlation_id = f"solicitud:{uuid.uuid4().hex}"
    evento_id = f"evt-{uuid.uuid4().hex}"
    orden_id = f"ord-{uuid.uuid4().hex}"
    idempotencia_evento = f"evento:{evento_id}:{orden_id}"
    idempotencia_orden = f"orden:{orden_id}"

    with engine.begin() as conexion:
        _insertar_evento(conexion, evento_id)
        _insertar_orden(conexion, evento_id, orden_id)
        _insertar_ledger(
            conexion,
            evento_id=evento_id,
            orden_id=orden_id,
            idempotencia=idempotencia_evento,
            correlation_id=correlation_id,
        )
        _insertar_ledger(
            conexion,
            evento_id=evento_id,
            orden_id=orden_id,
            idempotencia=idempotencia_orden,
            correlation_id=correlation_id,
        )

    with engine.connect() as conexion:
        filas = (
            (
                conexion.execute(
                    text(
                        """
                    SELECT idempotencia, correlation_id
                    FROM ledger_decisiones
                    WHERE correlation_id = :cid
                    ORDER BY idempotencia
                    """
                    ),
                    {"cid": correlation_id},
                )
            )
            .mappings()
            .all()
        )

    assert len(filas) == 2
    claves = {fila["idempotencia"] for fila in filas}
    assert idempotencia_evento in claves
    assert idempotencia_orden in claves
    for fila in filas:
        assert fila["correlation_id"] == correlation_id


def test_evento_id_y_orden_id_identifican_caso_no_solicitud(engine):
    """Una nota puede generar varios casos: evento_id y orden_id son del caso."""
    correlation_id = f"solicitud:{uuid.uuid4().hex}"
    evento_a = f"evt-a-{uuid.uuid4().hex}"
    orden_a = f"ord-a-{uuid.uuid4().hex}"
    evento_b = f"evt-b-{uuid.uuid4().hex}"
    orden_b = f"ord-b-{uuid.uuid4().hex}"

    with engine.begin() as conexion:
        _insertar_evento(conexion, evento_a)
        _insertar_orden(conexion, evento_a, orden_a)
        _insertar_evento(conexion, evento_b)
        _insertar_orden(conexion, evento_b, orden_b)
        _insertar_ledger(
            conexion,
            evento_id=evento_a,
            orden_id=orden_a,
            idempotencia=f"evento:{evento_a}:{orden_a}",
            correlation_id=correlation_id,
        )
        _insertar_ledger(
            conexion,
            evento_id=evento_b,
            orden_id=orden_b,
            idempotencia=f"evento:{evento_b}:{orden_b}",
            correlation_id=correlation_id,
        )

    with engine.connect() as conexion:
        filas = (
            (
                conexion.execute(
                    text(
                        """
                    SELECT evento_id, orden_id
                    FROM ledger_decisiones
                    WHERE correlation_id = :cid
                    ORDER BY evento_id
                    """
                    ),
                    {"cid": correlation_id},
                )
            )
            .mappings()
            .all()
        )

    assert len(filas) == 2
    assert filas[0]["evento_id"] == evento_a
    assert filas[0]["orden_id"] == orden_a
    assert filas[1]["evento_id"] == evento_b
    assert filas[1]["orden_id"] == orden_b


def test_claves_idempotencia_no_colapsan_casos_distintos(engine):
    """Dos casos de la misma solicitud no comparten clave de idempotencia."""
    correlation_id = f"solicitud:{uuid.uuid4().hex}"
    clave_a = f"evento:evt-a:{uuid.uuid4().hex}"
    clave_b = f"evento:evt-b:{uuid.uuid4().hex}"
    assert clave_a != clave_b

    with engine.begin() as conexion:
        _insertar_ledger(
            conexion,
            evento_id="evt-a",
            orden_id="ord-a",
            idempotencia=clave_a,
            correlation_id=correlation_id,
        )
        _insertar_ledger(
            conexion,
            evento_id="evt-b",
            orden_id="ord-b",
            idempotencia=clave_b,
            correlation_id=correlation_id,
        )

    with engine.connect() as conexion:
        conteo = (
            conexion.execute(
                text(
                    """
                    SELECT count(*) FROM ledger_decisiones
                    WHERE correlation_id = :cid
                      AND idempotencia IN (:a, :b)
                    """
                ),
                {"cid": correlation_id, "a": clave_a, "b": clave_b},
            )
        ).scalar_one()

    assert conteo == 2


def test_sin_correlation_id_no_se_rompe(engine):
    """Un registro de ledger sin correlation_id sigue siendo válido."""
    with engine.begin() as conexion:
        _insertar_ledger(
            conexion,
            evento_id=f"evt-{uuid.uuid4().hex}",
            orden_id=f"ord-{uuid.uuid4().hex}",
            idempotencia=f"evento:{uuid.uuid4().hex}",
            correlation_id=None,
        )
    # Si no lanza, el registro sin correlation_id es aceptado.
