"""Evaluación de retrieval contra la suite de preguntas de la planta.

La suite viene del corpus de la planta (``evaluacion/retrieval.json``): 71
preguntas con las secciones relevantes y, en las preguntas con trampa de
versión, las secciones que no deberían aparecer antes que la vigente.

Se miden hit@k, recall@k, MRR y distractor@k. ``distractor@k`` cuenta la
pregunta cuando una sección prohibida aparece en el top-k **antes** de la
primera relevante. ``revision_vencida_antes`` cuenta el caso concreto de la
Rev. C del manual de la bomba, que la Fase 4 tiene que dejar detrás de la D.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

Buscador = Callable[[str, int], list[str]]


def cargar_suite(ruta: str | Path) -> list[dict[str, Any]]:
    return json.loads(Path(ruta).read_text(encoding="utf-8"))


def evaluar(
    buscar: Buscador,
    suite: Iterable[dict[str, Any]],
    *,
    ks: tuple[int, ...] = (1, 3, 5, 10),
) -> dict[str, Any]:
    """Mide un buscador que devuelve citas en orden."""
    preguntas = list(suite)
    if not preguntas:
        return {"n": 0, "total": {}, "por_tipo": {}, "preguntas": []}
    kmax = max(max(ks), 20)
    filas: list[dict[str, Any]] = []
    for pregunta in preguntas:
        resultado = buscar(pregunta["pregunta"], kmax)
        relevantes = set(pregunta["relevantes"])
        distractores = set(pregunta.get("distractores") or [])
        posicion_relevante = next(
            (indice for indice, cita in enumerate(resultado) if cita in relevantes), None
        )
        fila: dict[str, Any] = {
            "id": pregunta["id"],
            "tipo": pregunta["tipo"],
            "mrr": 1 / (posicion_relevante + 1) if posicion_relevante is not None else 0.0,
            "top": resultado[:5],
            "revision_vencida_antes": 0,
        }
        for k in ks:
            top = resultado[:k]
            fila[f"hit@{k}"] = float(any(cita in relevantes for cita in top))
            fila[f"recall@{k}"] = len(relevantes & set(top)) / len(relevantes)
            posicion_distractor = next(
                (indice for indice, cita in enumerate(top) if cita in distractores), None
            )
            fila[f"distractor@{k}"] = float(
                posicion_distractor is not None
                and (posicion_relevante is None or posicion_distractor < posicion_relevante)
            )
        top5 = resultado[:5]
        posicion_rev_c = next(
            (indice for indice, cita in enumerate(top5) if cita.startswith("TX350-C")), None
        )
        posicion_rev_d = next(
            (indice for indice, cita in enumerate(top5) if cita.startswith("TX350-D")), None
        )
        if posicion_rev_c is not None and (
            posicion_rev_d is None or posicion_rev_c < posicion_rev_d
        ):
            fila["revision_vencida_antes"] = 1
        filas.append(fila)

    metricas = [metrica for metrica in filas[0] if "@" in metrica or metrica == "mrr"]
    total = {
        metrica: round(sum(fila[metrica] for fila in filas) / len(filas), 3)
        for metrica in metricas
    }
    por_tipo: dict[str, dict[str, Any]] = {}
    for tipo in sorted({fila["tipo"] for fila in filas}):
        subconjunto = [fila for fila in filas if fila["tipo"] == tipo]
        por_tipo[tipo] = {
            "n": len(subconjunto),
            **{
                metrica: round(sum(fila[metrica] for fila in subconjunto) / len(subconjunto), 3)
                for metrica in metricas
                if metrica.startswith(("hit@", "recall@", "distractor@"))
            },
        }
    return {
        "n": len(filas),
        "total": total,
        "por_tipo": por_tipo,
        "revision_vencida_antes": sum(fila["revision_vencida_antes"] for fila in filas),
        "preguntas": filas,
    }


def comparar_con_baseline(resultado: dict[str, Any], baseline: dict[str, float]) -> dict[str, Any]:
    """Diferencia de las métricas principales contra la línea de base BM25."""
    return {
        metrica: round(resultado["total"][metrica] - valor, 3)
        for metrica, valor in baseline.items()
        if metrica in resultado["total"]
    }


def errores(
    resultado: dict[str, Any],
    suite: Iterable[dict[str, Any]],
    k: int = 5,
) -> list[dict[str, Any]]:
    """Preguntas sin acierto en el top-k o con distractor antes que la relevante."""
    por_id = {pregunta["id"]: pregunta for pregunta in suite}
    fallas = []
    for fila in resultado["preguntas"]:
        if not fila[f"hit@{k}"] or fila[f"distractor@{k}"]:
            pregunta = por_id.get(fila["id"], {})
            fallas.append(
                {
                    "id": fila["id"],
                    "pregunta": pregunta.get("pregunta"),
                    "esperado": pregunta.get("relevantes"),
                    "obtenido": fila["top"],
                }
            )
    return fallas


BASELINE_BM25 = {
    "hit@5": 0.845,
    "recall@5": 0.664,
    "distractor@5": 0.099,
}
