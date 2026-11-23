"""Tests de la Capability Matrix (Ítem 3.3 del Plan de implementación 3).

Verifica que la matriz de capacidades modele correctamente los modelos de la
lista cerrada y que los filtros de aptitud funcionen.
"""

from routing.matriz_capacidades import (
    MATRIZ_CAPACIDADES,
    MODELO_ASR,
    MODELO_EMBEDDINGS,
    MODELO_LLM,
    MODELO_VISION,
    CapacidadModelo,
    modelo_por_clave,
    modelos_aptos_para,
    modelos_baratos,
    modelos_rapidos,
)


def test_matriz_tiene_los_4_modelos_de_la_lista():
    """La matriz debe tener exactamente los 4 modelos de la lista cerrada."""
    assert set(MATRIZ_CAPACIDADES.keys()) == {
        MODELO_LLM,
        MODELO_VISION,
        MODELO_ASR,
        MODELO_EMBEDDINGS,
    }


def test_modelo_por_clave_devuelve_capacidades():
    """modelo_por_clave devuelve las capacidades correctas para cada clave."""
    llm = modelo_por_clave(MODELO_LLM)
    assert llm is not None
    assert llm.nombre == "Qwen2.5-3B-Instruct (Q4_K_M vía Ollama)"
    assert llm.ubicacion == "planta"

    vision = modelo_por_clave(MODELO_VISION)
    assert vision is not None
    assert vision.nombre == "Nemotron 3 Nano Omni 30B A3B (API de NVIDIA)"
    assert vision.ubicacion == "nube"

    assert modelo_por_clave("inexistente") is None


def test_llm_no_tiene_vision():
    """El LLM local no tiene capacidad de visión."""
    llm = modelo_por_clave(MODELO_LLM)
    assert llm is not None
    assert llm.vision == "nulo"


def test_vision_tiene_vision_alto():
    """El modelo de visión tiene capacidad de visión alta."""
    vision = modelo_por_clave(MODELO_VISION)
    assert vision is not None
    assert vision.vision == "alto"


def test_modelos_aptos_para_vision():
    """Solo el modelo de visión es apto para tareas de visión."""
    aptos = modelos_aptos_para("vision")
    assert MODELO_VISION in aptos
    assert MODELO_LLM not in aptos
    assert MODELO_ASR not in aptos
    assert MODELO_EMBEDDINGS not in aptos


def test_modelos_aptos_para_razonar():
    """El LLM y el modelo de visión son aptos para razonamiento (medio/bajo)."""
    aptos = modelos_aptos_para("razonar")
    assert MODELO_LLM in aptos
    assert MODELO_VISION in aptos
    assert MODELO_ASR not in aptos


def test_modelos_aptos_para_clasificar():
    """El LLM y el modelo de visión son aptos para clasificar (tienen structured output)."""
    aptos = modelos_aptos_para("clasificar")
    assert MODELO_LLM in aptos
    assert MODELO_VISION in aptos
    assert MODELO_ASR not in aptos


def test_modelos_aptos_para_audio():
    """Solo el ASR es apto para audio."""
    aptos = modelos_aptos_para("audio")
    assert MODELO_ASR in aptos
    assert MODELO_LLM not in aptos


def test_modelos_baratos():
    """Los modelos locales son baratos."""
    baratos = modelos_baratos()
    assert MODELO_LLM in baratos
    assert MODELO_ASR in baratos
    assert MODELO_EMBEDDINGS in baratos
    assert MODELO_VISION not in baratos  # nube, costo medio


def test_modelos_rapidos():
    """El ASR, embeddings y visión son rápidos."""
    rapidos = modelos_rapidos()
    assert MODELO_ASR in rapidos
    assert MODELO_EMBEDDINGS in rapidos
    assert MODELO_VISION in rapidos


def test_capacidad_modelo_apto_para():
    """CapacidadModelo.apto_para rechaza modelos sin visión para tareas de visión."""
    cap = CapacidadModelo(
        clave="test",
        nombre="Test",
        ubicacion="planta",
        tool_calling="bajo",
        structured_output="bajo",
        reasoning="bajo",
        vision="nulo",
        audio="nulo",
        contexto="2048",
        velocidad="bajo",
        costo="bajo",
    )
    assert cap.apto_para("vision") is False
    assert cap.apto_para("clasificar") is True


def test_capacidad_modelo_es_barato_y_es_rapido():
    """CapacidadModelo.es_barato y es_rapido funcionan según el campo costo/velocidad."""
    cap_barato_rapido = CapacidadModelo(
        clave="x",
        nombre="x",
        ubicacion="planta",
        tool_calling="bajo",
        structured_output="bajo",
        reasoning="bajo",
        vision="nulo",
        audio="nulo",
        contexto="2048",
        velocidad="alto",
        costo="bajo",
    )
    assert cap_barato_rapido.es_barato() is True
    assert cap_barato_rapido.es_rapido() is True

    cap_caro_lento = CapacidadModelo(
        clave="y",
        nombre="y",
        ubicacion="nube",
        tool_calling="alto",
        structured_output="alto",
        reasoning="alto",
        vision="alto",
        audio="nulo",
        contexto="128k",
        velocidad="bajo",
        costo="alto",
    )
    assert cap_caro_lento.es_barato() is False
    assert cap_caro_lento.es_rapido() is False
