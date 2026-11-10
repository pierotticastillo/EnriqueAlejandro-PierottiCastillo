"""El presupuesto de una etapa: pasos, tokens y tiempo.

El anexo de la Entrega 1 pide, para el flujo del parte, *"condiciones de
corte, presupuesto de pasos y tokens, y timeout global por nota"*.

Este módulo es ese presupuesto. Es un objeto chico, con tres límites y un
reloj:

* **pasos**: cuántas vueltas y llamadas a herramientas puede dar el agente;
* **tokens**: cuántos tokens puede gastar en total;
* **tiempo**: cuántos segundos puede tardar la etapa (el **timeout global**).

Cuando se agota uno de los tres, el presupuesto dice **por qué** se cortó
(``corte``) y quien lo consultó puede dejar esa razón escrita en la bitácora.
Cortar es parte del diseño: es mejor un corte escrito y auditado que un
proceso que se queda consultando o esperando.

El mismo tipo sirve para las dos cosas que hay que acotar:

* la **consulta del agente**: pasos, tokens y su propio timeout;
* el **proceso de una nota** (transcripción y parte): el timeout global por
  nota, que empieza cuando la nota entra al sistema.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


class PresupuestoAgotado(Exception):
    """Se agotó el presupuesto: la etapa se corta.

    No es un error del sistema: es una **condición de corte** prevista. El
    que la corta deja la razón escrita y sigue con lo que ya tenía.
    """


@dataclass
class Presupuesto:
    """Los tres límites de una etapa y el reloj que los mide.

    ``max_pasos`` y ``max_tokens`` son 0 (o negativos) cuando la etapa no
    usa modelo: en ese caso el presupuesto sólo hace de reloj.
    """

    max_pasos: int = 0
    max_tokens: int = 0
    timeout_s: float = 0.0
    pasos: int = 0
    tokens: int = 0
    corte: str | None = None
    inicio: float = field(default_factory=time.monotonic, repr=False)

    # ------------------------------------------------------------------ reloj

    @property
    def segundos(self) -> float:
        """Cuánto lleva la etapa."""
        return time.monotonic() - self.inicio

    @property
    def restante_s(self) -> float:
        """Cuánto le queda (0 si ya se agotó)."""
        if self.timeout_s <= 0:
            return float("inf")
        return max(0.0, self.timeout_s - self.segundos)

    # ------------------------------------------------------------------ corte

    def motivo_corte(self) -> str | None:
        """Por qué se cortó la etapa, o ``None`` si todavía puede seguir."""
        if self.corte:
            return self.corte
        if self.max_pasos > 0 and self.pasos > self.max_pasos:
            return f"se agotaron los {self.max_pasos} pasos"
        if self.max_tokens > 0 and self.tokens > self.max_tokens:
            return f"se agotaron los {self.max_tokens} tokens"
        if self.timeout_s > 0 and self.restante_s <= 0:
            return f"se agotaron los {self.timeout_s:g} s del timeout global"
        return None

    def agotado(self) -> bool:
        """Si la etapa ya no puede seguir."""
        return self.motivo_corte() is not None

    def anotar_corte(self, motivo: str) -> None:
        """Deja escrita la razón del corte (la primera que llega manda)."""
        if self.corte is None:
            self.corte = motivo

    def consumir_paso(
        self,
        tokens_prompt: int = 0,
        tokens_completion: int = 0,
    ) -> None:
        """Suma un paso y sus tokens, y corta si algo se agotó.

        Lanza ``PresupuestoAgotado`` para que la etapa se corte **en el
        momento** en que se pasó el límite, y no en el siguiente.
        """
        self.pasos += 1
        self.tokens += int(tokens_prompt or 0) + int(tokens_completion or 0)
        motivo = self.motivo_corte()
        if motivo is not None:
            self.corte = motivo
            raise PresupuestoAgotado(motivo)

    def resumen(self) -> dict[str, object]:
        """El presupuesto, para la bitácora."""
        return {
            "pasos": self.pasos,
            "max_pasos": self.max_pasos,
            "tokens": self.tokens,
            "max_tokens": self.max_tokens,
            "segundos": round(self.segundos, 3),
            "timeout_s": self.timeout_s,
            "corte": self.corte,
        }
