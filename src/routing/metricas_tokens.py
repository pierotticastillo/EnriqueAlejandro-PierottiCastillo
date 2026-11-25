"""Métrica de tokens por segundo (Ítem 3.13 del Plan de implementación 3).

La presentación de Semana_5 sugiere medir dos velocidades distintas de
inferencia:

* **Prompt eval rate**: cuánto lee el modelo la entrada (tokens/s) — pesa con
  contextos largos.
* **Eval rate**: cuánto escribe el modelo la salida (tokens/s) — token a token.

Esto permite saber si el modelo local está generando a una velocidad usable
para chat (más de 5 tok/s es aceptable, más de 10 fluido, más de 20 lo que
pide un agente), como sugiere la presentación.

La métrica se calcula a partir de los tokens medidos y los segundos
registrados en cada paso de la trayectoria.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricaTokensPorSegundo:
    """Una medición de velocidad de inferencia para un paso."""

    paso: int
    modelo: str
    tokens_prompt: int
    tokens_completion: int
    segundos_prompt: float
    segundos_completion: float
    prompt_eval_rate_tok_s: float  # tokens de entrada por segundo
    eval_rate_tok_s: float  # tokens de salida por segundo

    @property
    def segundos_totales(self) -> float:
        return self.segundos_prompt + self.segundos_completion

    @property
    def tokens_totales(self) -> int:
        return self.tokens_prompt + self.tokens_completion

    @property
    def velocidad_promedio_tok_s(self) -> float:
        """Tokens totales por segundo (prompt + salida)."""
        if self.segundos_totales <= 0:
            return 0.0
        return self.tokens_totales / self.segundos_totales

    def clasificacion_velocidad(self) -> str:
        """Clasifica la velocidad de generación según la presentación.

        * menos de 5 tok/s: incómodo para chat
        * 5 a 10: aceptable
        * 10 a 20: fluido para leer
        * más de 20: lo que pide un agente
        """
        tasa = self.eval_rate_tok_s
        if tasa < 5:
            return "incómodo para chat"
        if tasa < 10:
            return "aceptable"
        if tasa < 20:
            return "fluido para leer"
        return "lo que pide un agente"


def calcular_tokens_por_segundo(
    *,
    paso: int,
    modelo: str,
    tokens_prompt: int,
    tokens_completion: int,
    segundos_prompt: float,
    segundos_completion: float,
) -> MetricaTokensPorSegundo:
    """Calcula la métrica de tokens por segundo para un paso.

    Si los segundos de una fase son 0, su tasa se declara 0 (no se puede
    dividir por cero).
    """
    prompt_eval_rate = tokens_prompt / segundos_prompt if segundos_prompt > 0 else 0.0
    eval_rate = tokens_completion / segundos_completion if segundos_completion > 0 else 0.0
    return MetricaTokensPorSegundo(
        paso=paso,
        modelo=modelo,
        tokens_prompt=tokens_prompt,
        tokens_completion=tokens_completion,
        segundos_prompt=segundos_prompt,
        segundos_completion=segundos_completion,
        prompt_eval_rate_tok_s=round(prompt_eval_rate, 2),
        eval_rate_tok_s=round(eval_rate, 2),
    )


#: Instancia global simple (opcional). Se prefiere inyectar la calculadora.
metricas_tokens_por_segundo: list[MetricaTokensPorSegundo] = []


def registrar_metrica_tokens(
    *,
    paso: int,
    modelo: str,
    tokens_prompt: int,
    tokens_completion: int,
    segundos_prompt: float,
    segundos_completion: float,
) -> MetricaTokensPorSegundo:
    """Calcula y registra una métrica de tokens por segundo en la lista global."""
    metrica = calcular_tokens_por_segundo(
        paso=paso,
        modelo=modelo,
        tokens_prompt=tokens_prompt,
        tokens_completion=tokens_completion,
        segundos_prompt=segundos_prompt,
        segundos_completion=segundos_completion,
    )
    metricas_tokens_por_segundo.append(metrica)
    return metrica


def resumen_velocidad() -> dict[str, object]:
    """Devuelve un resumen de todas las mediciones registradas."""
    if not metricas_tokens_por_segundo:
        return {
            "total_pasos": 0,
            "prompt_eval_rate_promedio_tok_s": 0.0,
            "eval_rate_promedio_tok_s": 0.0,
            "velocidad_promedio_tok_s": 0.0,
            "clasificacion": "sin mediciones",
        }

    n = len(metricas_tokens_por_segundo)
    prompt_prom = sum(m.prompt_eval_rate_tok_s for m in metricas_tokens_por_segundo) / n
    eval_prom = sum(m.eval_rate_tok_s for m in metricas_tokens_por_segundo) / n
    vel_prom = sum(m.velocidad_promedio_tok_s for m in metricas_tokens_por_segundo) / n

    return {
        "total_pasos": n,
        "prompt_eval_rate_promedio_tok_s": round(prompt_prom, 2),
        "eval_rate_promedio_tok_s": round(eval_prom, 2),
        "velocidad_promedio_tok_s": round(vel_prom, 2),
        "clasificacion": metricas_tokens_por_segundo[-1].clasificacion_velocidad(),
    }
