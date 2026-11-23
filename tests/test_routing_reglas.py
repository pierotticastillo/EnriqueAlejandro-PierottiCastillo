"""Tests del router por reglas (Ítem 3.1 del Plan de implementación 3).

Verifica que el Router elija el modelo correcto de la lista cerrada según
las señales del pedido: imagen, ventana grande, tipo de tarea.
"""

from routing.pedido import Pedido
from routing.router import MODELO_LLM, MODELO_VISION, Router


def test_pedido_con_imagen_elige_vision():
    """Una foto de la planta debe ir al modelo de visión (NVIDIA Nemotron)."""
    pedido = Pedido(
        tipo_tarea="chat",
        tiene_imagen=True,
        tokens_estimados=500,
    )
    router = Router()
    assert router.elegir_modelo(pedido) == MODELO_VISION


def test_pedido_sin_imagen_elige_llm():
    """Un mensaje de texto sin imagen debe ir al modelo local (Qwen2.5:3b)."""
    pedido = Pedido(
        tipo_tarea="chat",
        tiene_imagen=False,
        tokens_estimados=500,
    )
    router = Router()
    assert router.elegir_modelo(pedido) == MODELO_LLM


def test_tarea_vision_elige_vision_incluso_sin_imagen():
    """Una tarea explícitamente de visión debe ir al modelo de visión."""
    pedido = Pedido(
        tipo_tarea="vision",
        tiene_imagen=False,
        tokens_estimados=500,
    )
    router = Router()
    assert router.elegir_modelo(pedido) == MODELO_VISION


def test_clasificar_o_extraer_elige_llm():
    """Tareas simples de clasificar/extraer van al modelo local."""
    for tipo in ("clasificar", "extraer"):
        pedido = Pedido(
            tipo_tarea=tipo,
            tiene_imagen=False,
            tokens_estimados=500,
        )
        router = Router()
        assert router.elegir_modelo(pedido) == MODELO_LLM, f"falló para {tipo}"


def test_razonar_sin_imagen_elige_llm():
    """Una tarea de razonamiento sin imagen va al modelo local (por ahora)."""
    pedido = Pedido(
        tipo_tarea="razonar",
        tiene_imagen=False,
        tokens_estimados=500,
    )
    router = Router()
    assert router.elegir_modelo(pedido) == MODELO_LLM


def test_ventana_grande_elige_llm_por_defecto():
    """Un input muy largo va al modelo local hasta que haya uno con ventana mayor."""
    pedido = Pedido(
        tipo_tarea="chat",
        tiene_imagen=False,
        tokens_estimados=150_000,
    )
    router = Router()
    assert router.elegir_modelo(pedido) == MODELO_LLM


def test_router_no_devuelve_claves_fuera_de_lista():
    """El router nunca devuelve una clave que no sea 'llm' o 'vision'."""
    claves_validas = {MODELO_LLM, MODELO_VISION}
    casos = [
        Pedido("chat", False, 500),
        Pedido("chat", True, 500),
        Pedido("clasificar", False, 500),
        Pedido("extraer", False, 500),
        Pedido("razonar", False, 500),
        Pedido("vision", False, 500),
        Pedido("chat", False, 150_000),
        Pedido("chat", True, 150_000),
    ]
    router = Router()
    for pedido in casos:
        assert router.elegir_modelo(pedido) in claves_validas
