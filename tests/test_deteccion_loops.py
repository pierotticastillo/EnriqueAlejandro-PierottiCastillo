"""Tests de detección de loops (P1-18).

Comprueba que el detector identifica:

* repetición: misma tool con mismos argumentos en N vueltas consecutivas;
* ping-pong: secuencia alterna A → B → A → B sin avance;
* no progreso: misma evidencia y estado sin cambiar.

Y que el callback de LangChain lanza ``LoopDetectado`` ante la primera firma.
"""

from __future__ import annotations

from agente.deteccion_loops import (
    MAX_REPETICIONES,
    CallbackDetectarLoops,
    DetectorLoops,
    FirmaLoop,
    LoopDetectado,
)

# ------------------------------------------------------------------ detector


def test_repeticion_detectada():
    """Misma tool con mismos argumentos 3 veces seguidas: loop por repetición."""
    detector = DetectorLoops()
    assert detector.registrar_llamada("busqueda_rag", {"tag": "B-101"}, 1) is None
    assert detector.registrar_llamada("busqueda_rag", {"tag": "B-101"}, 2) is None
    firma = detector.registrar_llamada("busqueda_rag", {"tag": "B-101"}, 3)
    assert firma is not None
    assert firma.tipo == "repeticion"
    assert firma.herramienta == "busqueda_rag"
    assert firma.paso == 3


def test_repeticion_no_se_detecta_con_menos_de_tres():
    """Dos repeticiones no alcanzan el tope por defecto (3)."""
    detector = DetectorLoops()
    assert detector.registrar_llamada("busqueda_rag", {"tag": "B-101"}, 1) is None
    assert detector.registrar_llamada("busqueda_rag", {"tag": "B-101"}, 2) is None


def test_max_repeticiones_configurable():
    """Con max_repeticiones=2, dos repeticiones bastan para declarar loop."""
    detector = DetectorLoops(max_repeticiones=2)
    assert detector.registrar_llamada("busqueda_rag", {"tag": "B-101"}, 1) is None
    firma = detector.registrar_llamada("busqueda_rag", {"tag": "B-101"}, 2)
    assert firma is not None
    assert firma.tipo == "repeticion"


def test_ping_pong_detectado():
    """A → B → A → B: loop por ping-pong."""
    detector = DetectorLoops()
    assert detector.registrar_llamada("tool_A", {}, 1) is None
    assert detector.registrar_llamada("tool_B", {}, 2) is None
    assert detector.registrar_llamada("tool_A", {}, 3) is None
    firma = detector.registrar_llamada("tool_B", {}, 4)
    assert firma is not None
    assert firma.tipo == "ping_pong"
    assert firma.paso == 4


def test_ping_pong_no_se_detecta_con_menos_de_cuatro():
    """A → B → A no es aún ping-pong."""
    detector = DetectorLoops()
    assert detector.registrar_llamada("tool_A", {}, 1) is None
    assert detector.registrar_llamada("tool_B", {}, 2) is None
    assert detector.registrar_llamada("tool_A", {}, 3) is None


def test_argumentos_distintos_no_son_repeticion():
    """Misma tool pero argumentos distintos no es loop."""
    detector = DetectorLoops()
    assert detector.registrar_llamada("busqueda_rag", {"tag": "B-101"}, 1) is None
    assert detector.registrar_llamada("busqueda_rag", {"tag": "B-102"}, 2) is None
    assert detector.registrar_llamada("busqueda_rag", {"tag": "B-101"}, 3) is None


def test_no_progreso():
    """Sin cambio de evidencia ni estado entre pasos: no progreso."""
    detector = DetectorLoops()
    detector.registrar_llamada("busqueda_rag", {"tag": "B-101"}, 1)
    detector.registrar_llamada("busqueda_manuales", {"tag": "B-101"}, 2)
    detector.registrar_estado(evidencia="misma", estado="en_curso")
    firma = detector.detectar_no_progreso()
    assert firma is not None
    assert firma.tipo == "no_progreso"


def test_no_progreso_sin_estado_previo():
    """Con una sola llamada no hay no-progreso detectable."""
    detector = DetectorLoops()
    detector.registrar_llamada("busqueda_rag", {"tag": "B-101"}, 1)
    detector.registrar_estado(evidencia="misma", estado="en_curso")
    assert detector.detectar_no_progreso() is None


def test_reiniciar_limpia_historial():
    """Reiniciar borra las llamadas registradas."""
    detector = DetectorLoops()
    detector.registrar_llamada("busqueda_rag", {"tag": "B-101"}, 1)
    detector.registrar_llamada("busqueda_rag", {"tag": "B-101"}, 2)
    detector.reiniciar()
    assert detector.registrar_llamada("busqueda_rag", {"tag": "B-101"}, 1) is None
    assert detector.registrar_llamada("busqueda_rag", {"tag": "B-101"}, 2) is None


# ------------------------------------------------------------------ callback


class RegistradorFalso:
    """Registrador de trayectoria falso para probar el callback."""

    def __init__(self) -> None:
        self.eventos = []

    def registrar(
        self,
        *,
        etapa,
        tipo,
        herramienta,
        argumentos,
        salida,
        error,
        orden_id=None,
        evento_id=None,
        equipo=None,
        paso=None,
    ):
        self.eventos.append(
            {
                "etapa": etapa,
                "tipo": tipo,
                "herramienta": herramienta,
                "argumentos": argumentos,
                "salida": salida,
                "error": error,
                "orden_id": orden_id,
                "evento_id": evento_id,
                "equipo": equipo,
                "paso": paso,
            }
        )


def test_callback_lanza_loop_detectado():
    """El callback lanza LoopDetectado ante repetición y registra el corte."""
    detector = DetectorLoops()
    registrador = RegistradorFalso()
    callback = CallbackDetectarLoops(
        detector,
        registrador=registrador,
        etapa="agente",
        orden_id="ord-001",
        evento_id="evt-001",
        equipo="B-101",
    )

    # Dos llamadas previas para armar la repetición en la tercera.
    detector.registrar_llamada("busqueda_rag", {"tag": "B-101"}, 1)
    detector.registrar_llamada("busqueda_rag", {"tag": "B-101"}, 2)

    try:
        callback.on_tool_start(
            {"name": "busqueda_rag"},
            '{"tag": "B-101"}',
            run_id="run-1",
            name="busqueda_rag",
            inputs={"tag": "B-101"},
        )
        raise AssertionError("Se esperaba LoopDetectado")
    except LoopDetectado as exc:
        assert "repeticion" in str(exc)

    assert len(registrador.eventos) == 1
    evento = registrador.eventos[0]
    assert evento["tipo"] == "corte_loop"
    assert evento["herramienta"] == "busqueda_rag"
    assert evento["orden_id"] == "ord-001"
    # El paso del callback es su propio contador interno (primera llamada).
    assert evento["paso"] == 1


def test_callback_sin_loop_no_lanza():
    """Llamadas distintas no lanzan nada."""
    detector = DetectorLoops()
    callback = CallbackDetectarLoops(detector)

    assert (
        callback.on_tool_start(
            {"name": "busqueda_rag"},
            '{"tag": "B-101"}',
            run_id="run-1",
            name="busqueda_rag",
            inputs={"tag": "B-101"},
        )
        is None
    )
    assert (
        callback.on_tool_start(
            {"name": "busqueda_manuales"},
            '{"tag": "F-201A"}',
            run_id="run-2",
            name="busqueda_manuales",
            inputs={"tag": "F-201A"},
        )
        is None
    )


def test_callback_ping_pong_lanza():
    """El callback lanza LoopDetectado ante ping-pong."""
    detector = DetectorLoops()
    callback = CallbackDetectarLoops(detector)

    detector.registrar_llamada("tool_A", {}, 1)
    detector.registrar_llamada("tool_B", {}, 2)
    detector.registrar_llamada("tool_A", {}, 3)

    try:
        callback.on_tool_start(
            {"name": "tool_B"},
            {},
            run_id="run-4",
            name="tool_B",
            inputs={},
        )
        raise AssertionError("Se esperaba LoopDetectado")
    except LoopDetectado as exc:
        assert "ping_pong" in str(exc)


def test_max_repeticiones_es_al_menos_dos():
    """MAX_REPETICIONES no puede ser menor que 2."""
    assert MAX_REPETICIONES >= 2


def test_firma_loop_tiene_campos_esperados():
    """Una firma de loop tiene tipo, paso y secuencia."""
    firma = FirmaLoop(
        tipo="repeticion",
        herramienta="busqueda_rag",
        argumentos='{"tag": "B-101"}',
        paso=3,
        secuencia=[
            'busqueda_rag|{"tag": "B-101"}',
            'busqueda_rag|{"tag": "B-101"}',
            'busqueda_rag|{"tag": "B-101"}',
        ],
    )
    assert firma.tipo == "repeticion"
    assert firma.paso == 3
    assert len(firma.secuencia) == 3
