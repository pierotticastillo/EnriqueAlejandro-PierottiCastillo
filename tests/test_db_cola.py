"""Tests de la cola de salida contra PostgreSQL (Fase 6).

La cola es el camino de salida de la planta: lo que no se puede publicar no
se pierde. Estos tests comprueban contra la base real que:

* la fila guarda **a qué tópico** va el mensaje;
* el mismo mensaje no se encola dos veces (idempotencia);
* queda escrito en ``idempotency_key`` qué se encoló;
* los pendientes salen **en orden**;
* un mensaje enviado o con error sale de los pendientes.

Requieren un PostgreSQL con las migraciones aplicadas:

    TEST_DATABASE_URL=postgresql+psycopg://pia3:pia3@localhost:55432/pia3 pytest

Sin esa variable los tests se omiten para no romper una corrida sin base.
"""

from __future__ import annotations

import os

import pytest
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


def _limpiar(engine) -> None:
    with engine.begin() as conexion:
        conexion.execute(text("DELETE FROM outbox_cola"))
        conexion.execute(text("DELETE FROM idempotency_key WHERE clave LIKE 'outbox:%'"))


def _mensaje(id_: str) -> dict:
    return {
        "id": id_,
        "ts": "2026-11-08T10:00:00+00:00",
        "nota_ids": ["NOTA-00001"],
        "texto": "aviso",
        "pantalla": {"titulo": "Radio canal 1", "filas": [], "opciones": []},
        "audio_url": None,
        "espera_respuesta": False,
    }


def test_el_mensaje_encolado_guarda_su_topico(engine, repositorio) -> None:
    _limpiar(engine)

    id_outbox = repositorio.encolar_outbox("radio", _mensaje("radio-1"), "sistema/tablet")

    with engine.connect() as conexion:
        fila = conexion.execute(
            text("SELECT tipo, topico, estado FROM outbox_cola WHERE id = :id"),
            {"id": id_outbox},
        ).mappings().one()
    assert fila["tipo"] == "radio"
    assert fila["topico"] == "sistema/tablet"
    assert fila["estado"] == "pendiente"


def test_el_mismo_mensaje_no_se_encola_dos_veces(engine, repositorio) -> None:
    _limpiar(engine)

    primero = repositorio.encolar_outbox("evento", {"id": "parte:N1:A1"}, "sistema/eventos")
    segundo = repositorio.encolar_outbox("evento", {"id": "parte:N1:A1"}, "sistema/eventos")

    assert primero == segundo
    with engine.connect() as conexion:
        total = conexion.execute(
            text("SELECT count(*) FROM outbox_cola WHERE payload ->> 'id' = 'parte:N1:A1'")
        ).scalar_one()
    assert total == 1


def test_un_mensaje_ya_enviado_puede_volver_a_encolarse(engine, repositorio) -> None:
    """El mensaje puede salir otra vez si el bus se cae de nuevo.

    La idempotencia de la cola es "no encolar dos veces **mientras está
    pendiente**". Si el mensaje ya salió, la fila vieja es historia: para
    una segunda caída hace falta una fila nueva.
    """
    _limpiar(engine)

    primero = repositorio.encolar_outbox("evento", {"id": "parte:N1:A1"}, "sistema/eventos")
    repositorio.marcar_outbox_enviado(primero)

    segundo = repositorio.encolar_outbox("evento", {"id": "parte:N1:A1"}, "sistema/eventos")

    assert segundo != primero
    with engine.connect() as conexion:
        filas = conexion.execute(
            text(
                "SELECT estado FROM outbox_cola WHERE payload ->> 'id' = 'parte:N1:A1' "
                "ORDER BY creado_en, id"
            )
        ).scalars().all()
    assert filas == ["enviado", "pendiente"]
    # Y el pendiente vuelve a salir, una sola vez.
    pendientes = repositorio.pendientes_outbox()
    assert [payload["id"] for _, _, _, payload in pendientes] == ["parte:N1:A1"]


def test_queda_escrito_que_se_encolo(engine, repositorio) -> None:
    """El registro de idempotencia deja auditable qué salió de la planta."""
    _limpiar(engine)

    id_outbox = repositorio.encolar_outbox("orden", {"id": "ORD-1"}, "sistema/ordenes")

    with engine.connect() as conexion:
        fila = conexion.execute(
            text("SELECT tipo, referencia_id FROM idempotency_key WHERE clave = :clave"),
            {"clave": "outbox:orden:ORD-1"},
        ).mappings().one()
    assert fila["tipo"] == "orden"
    assert fila["referencia_id"] == id_outbox


def test_los_pendientes_salen_en_orden(engine, repositorio) -> None:
    _limpiar(engine)

    for numero in range(3):
        repositorio.encolar_outbox(
            "evento", {"id": f"parte:N1:A{numero}"}, "sistema/eventos"
        )

    pendientes = repositorio.pendientes_outbox()

    assert [payload["id"] for _, _, _, payload in pendientes] == [
        "parte:N1:A0",
        "parte:N1:A1",
        "parte:N1:A2",
    ]
    assert {tipo for _, tipo, _, _ in pendientes} == {"evento"}
    assert {topico for _, _, topico, _ in pendientes} == {"sistema/eventos"}


def test_un_mensaje_enviado_sale_de_los_pendientes(engine, repositorio) -> None:
    _limpiar(engine)

    id_outbox = repositorio.encolar_outbox("radio", _mensaje("radio-1"), "sistema/tablet")
    repositorio.marcar_outbox_enviado(id_outbox)

    assert repositorio.pendientes_outbox() == []
    with engine.connect() as conexion:
        estado = conexion.execute(
            text("SELECT estado FROM outbox_cola WHERE id = :id"), {"id": id_outbox}
        ).scalar_one()
    assert estado == "enviado"


def test_un_mensaje_con_error_sigue_pendiente_pero_espera(engine, repositorio) -> None:
    """Un fallo no pierde el mensaje: queda para reintentar, más tarde."""
    _limpiar(engine)

    id_outbox = repositorio.encolar_outbox("radio", _mensaje("radio-1"), "sistema/tablet")
    repositorio.marcar_outbox_error(id_outbox)

    with engine.connect() as conexion:
        fila = conexion.execute(
            text("SELECT estado, intentos, proximo_intento FROM outbox_cola WHERE id = :id"),
            {"id": id_outbox},
        ).mappings().one()
    assert fila["estado"] == "pendiente"
    assert fila["intentos"] == 1
    assert fila["proximo_intento"] is not None
    # Todavía no sale: espera el próximo intento.
    assert repositorio.pendientes_outbox() == []
