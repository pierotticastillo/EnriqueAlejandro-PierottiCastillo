"""El embedder del RAG no puede abrir un hilo por núcleo.

La planta tiene 2 CPU. Si el runtime de ONNX abre un hilo por núcleo del host,
el indexado del manual (700 fragmentos) le roba la CPU al ASR: cada
transcripción pasa de 1,5 s a más de 30 s y la planta pierde horas de tiempo
simulado. El límite de hilos y la prioridad del hilo del RAG son la defensa.
"""

from __future__ import annotations

import os
import sys
import types

from planta.main import _bajar_prioridad_cpu
from rag.embedder import crear_embedder


def test_el_embedder_pasa_el_limite_de_hilos_a_onnx(monkeypatch) -> None:
    llamado: dict[str, object] = {}

    class TextEmbeddingFalso:
        def __init__(self, **kwargs) -> None:
            llamado.update(kwargs)

        def embed(self, textos, batch_size=32):
            return iter([[0.0] * 8 for _ in textos])

    modulo = types.ModuleType("fastembed")
    modulo.TextEmbedding = TextEmbeddingFalso  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", modulo)

    embedder = crear_embedder("fastembed", modelo="modelo-de-prueba", hilos=1)

    assert llamado["threads"] == 1
    assert embedder.dimension == 8


def test_el_embedder_usa_el_limite_de_hilos_por_defecto(monkeypatch) -> None:
    llamado: dict[str, object] = {}

    class TextEmbeddingFalso:
        def __init__(self, **kwargs) -> None:
            llamado.update(kwargs)

        def embed(self, textos, batch_size=32):
            return iter([[0.0] * 8 for _ in textos])

    modulo = types.ModuleType("fastembed")
    modulo.TextEmbedding = TextEmbeddingFalso  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", modulo)

    crear_embedder("fastembed", modelo="modelo-de-prueba")

    assert llamado["threads"] == 1


def test_la_prioridad_del_hilo_del_rag_se_baja() -> None:
    """Bajar la prioridad nunca puede romper el arranque."""
    prioridad = _bajar_prioridad_cpu()

    assert isinstance(prioridad, int)
    if hasattr(os, "nice"):  # pragma: no cover - Unix
        assert os.nice(0) >= prioridad
