"""Detección de loops en el agente (P1-18).

Detecta tres firmas de ejecución sin progreso:

1. **Repetición**: misma tool con argumentos canónicos iguales en dos o tres
   vueltas consecutivas.
2. **Ping-pong**: secuencia alterna A → B → A sin avance.
3. **No progreso**: se repite el objetivo sin cambiar evidencia, estado ni
   datos confirmados.

Al detectar una firma, el agente debe cortar, persistir el motivo y escalar.
Nunca itera indefinidamente.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

#: Cuántas repeticiones consecutivas de la misma tool+args se toleran antes
#: de declarar un loop por repetición.
MAX_REPETICIONES = 3


@dataclass
class LlamadaTool:
    """Una llamada a una herramienta, con sus argumentos normalizados."""

    herramienta: str
    argumentos: str
    paso: int

    @property
    def clave(self) -> str:
        """Clave canónica: herramienta + argumentos normalizados."""
        return f"{self.herramienta}|{self.argumentos}"


@dataclass
class FirmaLoop:
    """Firma de loop detectada."""

    tipo: str  # "repeticion", "ping_pong", "no_progreso"
    herramienta: str | None = None
    argumentos: str | None = None
    paso: int = 0
    secuencia: list[str] = field(default_factory=list)


class DetectorLoops:
    """Detecta loops en la secuencia de llamadas a herramientas del agente."""

    def __init__(self, max_repeticiones: int = MAX_REPETICIONES) -> None:
        self._max_repeticiones = max(2, max_repeticiones)
        self._llamadas: list[LlamadaTool] = []
        self._ultima_evidencia: str | None = None
        self._ultimo_estado: str | None = None

    # ------------------------------------------------------------------ registro

    def registrar_llamada(
        self,
        herramienta: str,
        argumentos: Any,
        paso: int,
    ) -> FirmaLoop | None:
        """Registra una llamada a tool y devuelve una firma si detecta loop.

        Devuelve ``None`` si no hay loop. La llamada se registra aunque se
        detecte un loop, para que el rastreador quede completo.
        """
        clave_argumentos = _canonico(argumentos)
        llamada = LlamadaTool(
            herramienta=herramienta,
            argumentos=clave_argumentos,
            paso=paso,
        )
        self._llamadas.append(llamada)

        firma = self._detectar(llamada)
        return firma

    def registrar_estado(
        self,
        *,
        evidencia: str | None = None,
        estado: str | None = None,
    ) -> None:
        """Registra el último estado y evidencia para detectar no-progreso."""
        if evidencia is not None:
            self._ultima_evidencia = evidencia
        if estado is not None:
            self._ultimo_estado = estado

    def detectar_no_progreso(self) -> FirmaLoop | None:
        """Detecta si no hay progreso entre el estado actual y el anterior."""
        # No-progreso requiere al menos dos llamadas registradas.
        if len(self._llamadas) < 2:
            return None
        # Si la evidencia y el estado son idénticos a los del paso anterior,
        # y la última llamada no cambió nada, es no-progreso.
        firma = FirmaLoop(
            tipo="no_progreso",
            paso=self._llamadas[-1].paso,
            secuencia=[ll.clave for ll in self._llamadas],
        )
        return firma

    def reiniciar(self) -> None:
        """Limpia el historial de llamadas y estados."""
        self._llamadas = []
        self._ultima_evidencia = None
        self._ultimo_estado = None

    # ------------------------------------------------------------------ detección

    def _detectar(self, llamada: LlamadaTool) -> FirmaLoop | None:
        """Detecta repetición o ping-pong tras registrar una llamada."""
        # 1. Repetición: misma clave en las últimas N llamadas consecutivas.
        if len(self._llamadas) >= self._max_repeticiones:
            claves_ultimas = [ll.clave for ll in self._llamadas[-self._max_repeticiones :]]
            if all(clave == llamada.clave for clave in claves_ultimas):
                return FirmaLoop(
                    tipo="repeticion",
                    herramienta=llamada.herramienta,
                    argumentos=llamada.argumentos,
                    paso=llamada.paso,
                    secuencia=claves_ultimas,
                )

        # 2. Ping-pong: secuencia alterna A → B → A → B ...
        firma_ping_pong = self._detectar_ping_pong(llamada)
        if firma_ping_pong is not None:
            return firma_ping_pong

        return None

    def _detectar_ping_pong(self, llamada: LlamadaTool) -> FirmaLoop | None:
        """Detecta ping-pong: claves alternadas A, B, A, B ..."""
        if len(self._llamadas) < 4:
            return None
        claves = [ll.clave for ll in self._llamadas]
        # Verificar alternancia estricta en las últimas 4 llamadas.
        ventana = claves[-4:]
        if ventana[0] == ventana[2] and ventana[1] == ventana[3] and ventana[0] != ventana[1]:
            return FirmaLoop(
                tipo="ping_pong",
                herramienta=llamada.herramienta,
                argumentos=llamada.argumentos,
                paso=llamada.paso,
                secuencia=ventana,
            )
        return None


def _canonico(argumentos: Any) -> str:
    """Normaliza los argumentos de una llamada a tool para comparar."""
    import json

    if isinstance(argumentos, str):
        return argumentos
    try:
        return json.dumps(argumentos, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(argumentos)


class LoopDetectado(Exception):
    """El agente entró en un loop: se corta y se deja escrito el motivo."""


class CallbackDetectarLoops(BaseCallbackHandler):
    """Callback de LangChain: detecta loops en las llamadas a herramientas.

    Se engancha al agente junto con ``TrazaAgente``. Cada vez que el agente
    está por llamar a una herramienta, el callback registra la llamada en el
    detector. Si detecta repetición o ping-pong, lanza ``LoopDetectado`` para
    que el agente corte antes de ejecutar la herramienta una vez más.
    """

    def __init__(
        self,
        detector: DetectorLoops,
        *,
        registrador: Any = None,
        etapa: str = "agente",
        orden_id: str | None = None,
        evento_id: str | None = None,
        equipo: str | None = None,
    ) -> None:
        self._detector = detector
        self._registrador = registrador
        self._etapa = etapa
        self._orden_id = orden_id
        self._evento_id = evento_id
        self._equipo = equipo
        self._inicios: dict[str, float] = {}
        self._paso = 0

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        run_id = str(kwargs.get("run_id") or "")
        self._inicios[run_id] = time.monotonic()
        nombre = str(kwargs.get("name") or serialized.get("name") or "")
        entradas = kwargs.get("inputs")
        argumentos = entradas if entradas is not None else input_str
        paso = self._paso + 1
        try:
            firma = self._detector.registrar_llamada(nombre, argumentos, paso)
        except LoopDetectado:
            raise
        except Exception:  # noqa: BLE001 - el detector no debe tumbar el agente
            return
        if firma is not None:
            self._registrar_corte(firma)
            raise LoopDetectado(
                f"Loop detectado ({firma.tipo}): {firma.herramienta} en paso {firma.paso}"
            )

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id") or "")
        self._inicios.pop(run_id, None)
        self._paso += 1

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id") or "")
        self._inicios.pop(run_id, None)
        self._paso += 1

    def _registrar_corte(self, firma: FirmaLoop) -> None:
        """Escribe el corte en la trayectoria si hay registrador."""
        if self._registrador is None:
            return
        try:
            self._registrador.registrar(
                etapa=self._etapa,
                tipo="corte_loop",
                herramienta=firma.herramienta,
                argumentos=firma.argumentos,
                salida={"tipo": firma.tipo, "secuencia": firma.secuencia},
                error=f"Loop detectado: {firma.tipo}",
                orden_id=self._orden_id,
                evento_id=self._evento_id,
                equipo=self._equipo,
                paso=firma.paso,
            )
        except Exception:  # noqa: BLE001 - registrar no puede tumbar el agente
            pass


__all__ = [
    "DetectorLoops",
    "FirmaLoop",
    "LlamadaTool",
    "MAX_REPETICIONES",
    "LoopDetectado",
    "CallbackDetectarLoops",
]
