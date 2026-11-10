"""El agente de análisis: consulta los manuales y propone, con citas.

Es el **núcleo de agentes** del sistema, y está en LangChain (``create_agent``
de ``langchain.agents``). Ante un escalado, el agente:

1. **consulta** las herramientas: los manuales por RAG y por búsqueda léxica,
   el historial de órdenes del equipo, el catálogo de tareas, la lista de
   materiales y el inventario;
2. **propone** una falla del catálogo con sus causas y las citas de las que
   salen, en un esquema estricto.

Lo que el agente **no** hace: decidir la falla, elegir la tarea, aprobar la
orden ni escribir el motivo. La propuesta se contrasta con el catálogo y, si
no se puede respaldar, se descarta entera. Las reglas de aprobación siguen
en código (``analisis/aprobacion.py``).

**El prompt no lleva el texto libre del operador.** Sólo material confiable:
las mediciones estructuradas, la ficha de la falla, las causas del catálogo,
las tareas ya decididas y el resultado de las reglas. Por construcción el
agente no puede obedecer lo que dice una nota, porque no la ve.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel

from agente.modelos import PropuestaAgente, PropuestaValidada
from agente.presupuesto import Presupuesto, PresupuestoAgotado
from agente.tools import construir_tools

logger = logging.getLogger("agente.nucleo")

#: Cuántas **vueltas del modelo** puede dar el agente como máximo. Es el
#: "presupuesto de pasos" del anexo: el agente no puede seguir consultando
#: indefinidamente. Las llamadas a herramientas van dentro de la vuelta, y el
#: ``recursion_limit`` de LangChain las acota aparte.
MAX_PASOS = 6

#: La instrucción de sistema: qué hacer y qué no hacer.
INSTRUCCION = (
    "Sos un asistente técnico de una planta de inyección de agua. "
    "Tu tarea es CONSULTAR y PROPONER, no decidir.\n"
    "Cómo trabajar:\n"
    "1. Usá las herramientas para consultar los manuales del fabricante "
    "(busqueda_rag y busqueda_manuales), el historial de órdenes del equipo "
    "(historial_ordenes), el plan de mantenimiento (catalogo_tareas), los "
    "materiales del equipo (lista_materiales) y el stock (inventario).\n"
    "2. Contrastá lo que diga el manual con las mediciones que te pasan.\n"
    "3. Respondé con el esquema: la falla (código del catálogo), las causas "
    "probables en orden, las citas de las secciones del manual de las que "
    "sale, las tareas del plan y una justificación técnica para el técnico "
    "de mantenimiento.\n"
    "Reglas: no aprobás ni rechazás órdenes, no mencionás estados de "
    "aprobación, no cambiás las tareas ni los repuestos que ya están decididos, "
    "no agregás información que no esté en los datos ni en los manuales, no "
    "citás documentos que no estén en la lista de citas permitidas, no "
    "escribís números de documento o de sección dentro del texto (las "
    "referencias van sólo en el campo citas) y no repetís el texto del "
    "operador, que no te fue dado."
)


class AgenteAnalisis:
    """El agente: consulta con herramientas y propone con citas."""

    def __init__(
        self,
        modelo: BaseChatModel,
        *,
        cliente: Any,
        corpus: Any | None = None,
        recuperador: Any | None = None,
        catalogo: Any | None = None,
        max_pasos: int = MAX_PASOS,
        callbacks: list[Any] | None = None,
    ) -> None:
        self._modelo = modelo
        self._catalogo = catalogo
        self._max_pasos = max_pasos
        self._agente = create_agent(
            modelo,
            tools=construir_tools(cliente, corpus=corpus, recuperador=recuperador),
            system_prompt=INSTRUCCION,
            response_format=PropuestaAgente,
            name="agente-analisis",
        )
        self._callbacks = callbacks or []

    @property
    def nombre_modelo(self) -> str | None:
        """El nombre del modelo, para dejarlo escrito en la bitácora."""
        nombre = getattr(self._modelo, "model", None)
        return str(nombre) if nombre else None

    @property
    def max_pasos(self) -> int:
        """El presupuesto de pasos: cuántas vueltas puede dar el agente."""
        return self._max_pasos

    def consultar(
        self,
        prompt: str,
        *,
        permitidas: tuple[str, ...],
        callbacks: list[Any] | None = None,
        presupuesto: Presupuesto | None = None,
    ) -> PropuestaValidada | None:
        """Consulta al agente y valida lo que devuelve.

        ``None`` si el agente no respondió, si su salida no validó o si propuso
        algo que no se puede respaldar. La propuesta **nunca** cambia una
        decisión: sólo enriquece la justificación y deja su rastro.

        ``presupuesto`` acota los pasos, los tokens y el tiempo: cuando se
        agota, el corte es una condición prevista (no un fallo) y la
        justificación determinista sigue estando.
        """
        handlers = list(self._callbacks) + list(callbacks or [])
        try:
            resultado = self._agente.invoke(
                {"messages": [{"role": "user", "content": prompt}]},
                config={
                    "recursion_limit": self._max_pasos * 2 + 2,
                    "callbacks": handlers,
                },
            )
        except PresupuestoAgotado as exc:
            # Condición de corte: se anota y se sigue con lo que había.
            logger.warning("La consulta al agente se cortó: %s", exc)
            if presupuesto is not None:
                presupuesto.anotar_corte(str(exc))
            return None
        except Exception as exc:  # noqa: BLE001 - el agente no puede tumbar el análisis
            logger.warning("El agente no pudo consultarse: %s", exc)
            if presupuesto is not None:
                presupuesto.anotar_corte(f"el modelo falló: {exc}")
            return None
        propuesta = resultado.get("structured_response")
        if not isinstance(propuesta, PropuestaAgente):
            logger.warning("El agente no devolvió una propuesta válida")
            return None
        return self.validar(propuesta, permitidas=permitidas)

    # ------------------------------------------------------------------ validación

    def validar(
        self,
        propuesta: PropuestaAgente,
        *,
        permitidas: tuple[str, ...],
    ) -> PropuestaValidada | None:
        """Contrasta la propuesta con el catálogo y con las citas conocidas.

        Una cita que no está en la lista de permitidas descarta la propuesta
        entera: es el mismo control que hay en la redacción.
        """
        conocidas = {_normalizar(cita) for cita in permitidas}
        citas_ok: list[str] = []
        descartadas: list[str] = []
        for cita in propuesta.citas:
            (citas_ok if _normalizar(cita) in conocidas else descartadas).append(cita)
        if descartadas:
            logger.warning(
                "El agente citó documentos desconocidos (%s): se descarta su propuesta",
                ", ".join(descartadas),
            )
            return None

        falla = None
        if propuesta.falla:
            falla = self._falla_de(propuesta.falla)
            if falla is None:
                logger.warning(
                    "El agente propuso una falla que no está en el catálogo (%r): "
                    "se descarta su propuesta",
                    propuesta.falla,
                )
                return None

        causas_ok: list[str] = []
        causas_mal: list[str] = []
        permitidas_causa = self._causas_de(falla)
        for causa in propuesta.causas:
            if not permitidas_causa or _coincide(causa, permitidas_causa):
                causas_ok.append(causa)
            else:
                causas_mal.append(causa)

        # Las tareas que propone el agente son un dato de control: las tareas
        # que salen en la orden son las que ya decidió el código.
        return PropuestaValidada(
            falla=propuesta.falla,
            causas=tuple(causas_ok),
            citas=tuple(citas_ok),
            tareas=tuple(propuesta.tareas),
            justificacion=propuesta.justificacion,
            confianza=propuesta.confianza,
            citas_descartadas=tuple(descartadas),
            causas_descartadas=tuple(causas_mal),
        )

    def _falla_de(self, codigo: str) -> Any:
        if self._catalogo is None:
            return None
        return self._catalogo.fallas.por_codigo(codigo)

    def _causas_de(self, falla: Any) -> tuple[str, ...]:
        if falla is None:
            return ()
        return tuple(falla.causas or ())


def crear_agente(
    settings: Any,
    *,
    cliente: Any,
    corpus: Any | None = None,
    recuperador: Any | None = None,
    catalogo: Any | None = None,
    callbacks: list[Any] | None = None,
) -> AgenteAnalisis | None:
    """El agente de análisis, o ``None`` si no hay modelo activo.

    ``None`` significa "seguí sin agente": es un estado normal, no un error.
    El sistema no necesita el modelo para funcionar.
    """
    if not getattr(settings, "llm_activo", False):
        return None
    from langchain_ollama import ChatOllama

    cliente_kwargs: dict[str, Any] = {"timeout": settings.ollama_timeout_s}
    proxy = getattr(settings, "ollama_proxy", None) or None
    if proxy:
        # `trust_env=False`: el tráfico no sale por el proxy del entorno. Si
        # se configuró un proxy (en la simulación, el del enlace), se usa ése
        # y sólo ése.
        cliente_kwargs["proxy"] = proxy
        cliente_kwargs["trust_env"] = False
    modelo = ChatOllama(
        model=settings.ollama_modelo,
        base_url=settings.ollama_url,
        temperature=0,
        seed=settings.ollama_seed,
        num_ctx=settings.ollama_num_ctx,
        num_thread=settings.ollama_num_thread,
        num_predict=settings.ollama_max_tokens,
        keep_alive=settings.ollama_keep_alive,
        client_kwargs=cliente_kwargs,
    )
    return AgenteAnalisis(
        modelo,
        cliente=cliente,
        corpus=corpus,
        recuperador=recuperador,
        catalogo=catalogo,
        max_pasos=getattr(settings, "agente_max_pasos", MAX_PASOS),
        callbacks=callbacks,
    )


def _normalizar(texto: str) -> str:
    return " ".join(texto.strip().lower().split())


def _coincide(causa: str, permitidas: tuple[str, ...]) -> bool:
    """Si una causa del agente está respaldada por el catálogo.

    La comparación es por tokens significantes: el agente puede parafrasear
    («la empaquetadura está gastada» por «empaquetadura»), pero no puede
    inventar una causa que el manual no lista.
    """
    from rag.texto import tokenizar

    tokens = set(tokenizar(causa))
    if not tokens:
        return False
    for referencia in permitidas:
        otros = set(tokenizar(referencia))
        if otros and len(tokens & otros) / len(tokens) >= 0.5:
            return True
    return False
