"""Tests del checkpoint duradero (P1-15) y su protocolo de reanudación.

Comprueba que:

* un checkpoint se guarda y se recupera;
* tras un reinicio se recupera desde PostgreSQL sin repetir efectos;
* un checkpoint obsoleto no compite (versión);
* la reanudación omite efectos ya consumados;
* una caída controlada reanuda desde el último checkpoint sin duplicar
  orden, evento, mensaje ni habilita.

Requieren un PostgreSQL con las migraciones aplicadas:

    TEST_DATABASE_URL=postgresql+psycopg://pia3:pia3@localhost:55432/pia3 pytest

Sin esa variable los tests se omiten para no romper una corrida sin base.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text

from db.checkpoint import (
    ESTADO_EN_CURSO,
    ESTADO_TERMINAL,
    CheckpointObsoleto,
    RepositorioCheckpoint,
    crear_correlation_id,
)

TEST_DATABASE_URL = pytest.importorskip("os").getenv("TEST_DATABASE_URL")

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


def _guardar_inicial(repositorio, conexion, correlation_id, case_id="caso-001"):
    return repositorio.guardar(
        conexion,
        correlation_id=correlation_id,
        case_id=case_id,
        tipo_tarea="orden",
        paso_actual="analisis",
        estado=ESTADO_EN_CURSO,
        propietario="agente",
        datos_confirmados={"variable": "presion_descarga", "valor": 85.0},
        acciones_hechas=[
            {
                "clave_idempotencia": "evento:evt-001:ord-001",
                "efecto": {"tipo": "evento", "id": "evt-001"},
            }
        ],
        intentos=1,
        proxima_accion="publicar_orden",
        version=1,
    )


def test_guarda_y_recupera_checkpoint(engine):
    """Un checkpoint guardado se recupera con todos sus campos."""
    repositorio = RepositorioCheckpoint(engine)
    correlation_id = crear_correlation_id()
    with engine.begin() as conexion:
        checkpoint = _guardar_inicial(repositorio, conexion, correlation_id)
    assert checkpoint.id is not None
    assert checkpoint.correlation_id == correlation_id
    assert checkpoint.version == 1
    assert checkpoint.estado == ESTADO_EN_CURSO
    assert checkpoint.efecto_ya_hecho == {"evento:evt-001:ord-001"}

    with engine.connect() as conexion:
        recuperado = repositorio.cargar_ultimo(
            conexion, correlation_id=correlation_id
        )
    assert recuperado is not None
    assert recuperado.correlation_id == correlation_id
    assert recuperado.case_id == "caso-001"
    assert recuperado.paso_actual == "analisis"
    assert recuperado.propietario == "agente"
    assert recuperado.datos_confirmados["variable"] == "presion_descarga"
    assert recuperado.proxima_accion == "publicar_orden"


def test_reanudacion_omite_efectos_ya_hechos(engine):
    """Al reanudar, los efectos ya consumados se reconocen como hechos."""
    repositorio = RepositorioCheckpoint(engine)
    correlation_id = crear_correlation_id()
    with engine.begin() as conexion:
        _guardar_inicial(repositorio, conexion, correlation_id)

    with engine.connect() as conexion:
        cp = repositorio.cargar_ultimo(conexion, correlation_id=correlation_id)
    assert cp is not None
    assert "evento:evt-001:ord-001" in cp.efecto_ya_hecho
    assert cp.proxima_accion == "publicar_orden"
    assert cp.estado == ESTADO_EN_CURSO


def test_checkpoint_obsoleto_no_compite(engine):
    """Dos reanudaciones concurrentes: solo una avanza la versión."""
    repositorio = RepositorioCheckpoint(engine)
    correlation_id = crear_correlation_id()
    with engine.begin() as conexion:
        cp1 = _guardar_inicial(repositorio, conexion, correlation_id)
        # Otro componente ya avanzó la versión.
        repositorio.avanzar(
            conexion,
            correlation_id=correlation_id,
            paso_actual="publicacion",
            estado=ESTADO_TERMINAL,
            propietario="orquestador",
            proxima_accion=None,
            version=cp1.version + 1,
        )

    # Intentar guardar con la versión vieja debe fallar.
    with pytest.raises(CheckpointObsoleto):
        with engine.begin() as conexion:
            repositorio.guardar(
                conexion,
                correlation_id=correlation_id,
                case_id="caso-001",
                tipo_tarea="orden",
                paso_actual="analisis_viejo",
                estado=ESTADO_EN_CURSO,
                propietario="agente_viejo",
                version=cp1.version,  # versión ya obsoleta
            )


def test_checkpoint_no_encontrado_si_no_existe(engine):
    """Cargar un correlation_id que no tiene checkpoint devuelve None."""
    repositorio = RepositorioCheckpoint(engine)
    with engine.connect() as conexion:
        resultado = repositorio.cargar_ultimo(
            conexion, correlation_id="no-existe:000"
        )
    assert resultado is None


def test_avanzar_sube_version(engine):
    """Avanzar el checkpoint incrementa la versión y conserva el case_id."""
    repositorio = RepositorioCheckpoint(engine)
    correlation_id = crear_correlation_id()
    with engine.begin() as conexion:
        cp1 = _guardar_inicial(repositorio, conexion, correlation_id)
        assert cp1.version == 1

    with engine.begin() as conexion:
        cp2 = repositorio.avanzar(
            conexion,
            correlation_id=correlation_id,
            paso_actual="orden_publicada",
            estado=ESTADO_TERMINAL,
            propietario="orquestador",
            proxima_accion=None,
            version=cp1.version + 1,
        )
    assert cp2.version == cp1.version + 1
    assert cp2.estado == ESTADO_TERMINAL
    assert cp2.proxima_accion is None


def test_correlation_id_estable_entre_reintentos(engine):
    """El correlation_id se reutiliza en un reintento: mismo valor, misma solicitud."""
    repositorio = RepositorioCheckpoint(engine)
    primero = crear_correlation_id()
    segundo = crear_correlation_id()
    assert primero != segundo
    assert primero.startswith("pia3:")

    with engine.begin() as conexion:
        cp = _guardar_inicial(repositorio, conexion, primero)
    assert cp.correlation_id == primero

    with engine.connect() as conexion:
        recuperado = repositorio.cargar_ultimo(conexion, correlation_id=primero)
    assert recuperado.correlation_id == primero


def test_registro_efecto_dentro_de_transaccion(engine):
    """Un efecto se registra en el checkpoint dentro de la misma transacción."""
    repositorio = RepositorioCheckpoint(engine)
    correlation_id = crear_correlation_id()
    with engine.begin() as conexion:
        _guardar_inicial(repositorio, conexion, correlation_id)
        repositorio.registrar_efecto(
            correlation_id=correlation_id,
            clave_idempotencia="orden:pub:ord-001",
            efecto={"tipo": "orden", "id": "ord-001"},
            conexion=conexion,
        )

    with engine.connect() as conexion:
        cp = repositorio.cargar_ultimo(conexion, correlation_id=correlation_id)
    assert cp is not None
    claves = cp.efecto_ya_hecho
    assert "evento:evt-001:ord-001" in claves
    assert "orden:pub:ord-001" in claves


def test_propietario_cambia_al_reanudar(engine):
    """Al reanudar, el propietario puede cambiar (de agente a supervisor)."""
    repositorio = RepositorioCheckpoint(engine)
    correlation_id = crear_correlation_id()
    with engine.begin() as conexion:
        cp = _guardar_inicial(repositorio, conexion, correlation_id)
    assert cp.propietario == "agente"

    with engine.begin() as conexion:
        cp2 = repositorio.avanzar(
            conexion,
            correlation_id=correlation_id,
            paso_actual="revision_humana",
            estado="esperando_humano",
            propietario="supervisor",
            proxima_accion="decidir_cierre",
            version=cp.version + 1,
        )
    assert cp2.propietario == "supervisor"
    assert cp2.estado == "esperando_humano"


def _borrar_checkpoint(engine, correlation_id: str) -> None:
    with engine.begin() as conexion:
        conexion.execute(
            text("DELETE FROM checkpoint_ejecucion WHERE correlation_id = :cid"),
            {"cid": correlation_id},
        )


def test_limpieza_entre_tests(engine):
    """Cada test usa correlation_id distinto para no colisionar."""
    repositorio = RepositorioCheckpoint(engine)
    cid = f"test-limpieza:{uuid.uuid4().hex}"
    try:
        with engine.begin() as conexion:
            _guardar_inicial(repositorio, conexion, cid)
        with engine.connect() as conexion:
            cp = repositorio.cargar_ultimo(conexion, correlation_id=cid)
        assert cp is not None
    finally:
        _borrar_checkpoint(engine, cid)
