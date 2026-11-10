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

import json
import logging
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from agente.modelos import MAX_CARACTERES, MAX_ITEMS, PropuestaAgente, PropuestaValidada
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
    "(historial_ordenes), el historial de los sensores del equipo "
    "(historial_sensores, con el tag del equipo y la variable), el plan de "
    "mantenimiento (catalogo_tareas), los "
    "materiales del equipo (lista_materiales) y el stock (inventario).\n"
    "2. Contrastá lo que diga el manual con las mediciones que te pasan.\n"
    "3. Respondé con el esquema: la falla (código del catálogo), las causas "
    "probables en orden, las citas de las secciones del manual de las que "
    "sale, las tareas del plan y una justificación técnica para el técnico "
    "de mantenimiento.\n"
    "Reglas: no aprobás ni rechazás órdenes, no mencionás estados de "
    "aprobación, no cambiás las tareas ni los repuestos que ya están decididos, "
    "no agregás información que no esté en los datos ni en los manuales, no "
    "citás documentos que no estén en la lista de citas permitidas ni en las "
    "secciones que te muestren las herramientas, no "
    "escribís números de documento o de sección dentro del texto (las "
    "referencias van sólo en el campo citas) y no repetís el texto del "
    "operador, que no te fue dado."
)

#: La instrucción de cierre: el modelo ya consultó y ahora tiene que llenar
#: el esquema. Es el camino que funciona con los modelos pequeños.
#:
#: Los modelos de 3B que corren en la planta **no llaman a la herramienta de
#: salida**: consultan bien (llaman a las herramientas de consulta), pero al
#: terminar escriben prosa en vez de llenar el esquema. Por eso, si la
#: herramienta de salida no fue llamada, la conversación se le vuelve a pasar
#: al modelo con el esquema estricto y se le pide que complete la propuesta.
#: No es un camino distinto: es el mismo modelo, la misma conversación y los
#: mismos materiales; sólo cambia la forma de pedirle la respuesta.
INSTRUCCION_CIERRE = (
    "Completá el esquema con lo que consultaste. Cada campo va por separado:\n"
    "- falla: sólo el código de la falla, el mismo que dice la ficha de la "
    "falla (por ejemplo TX-T01);\n"
    "- causas: una causa por elemento, copiada del manual (por ejemplo "
    "Desgaste normal de los anillos);\n"
    "- citas: una cita por elemento, tal como la escribió la herramienta "
    "(por ejemplo TX350-D §8.3);\n"
    "- tareas: los códigos de las tareas del plan (por ejemplo T-B-EMP);\n"
    "- justificacion: dos o tres frases con el por qué;\n"
    "- confianza: alta, media o baja.\n"
    "No repitas la falla, las causas ni las citas dentro de la justificación: "
    "van en sus propios campos."
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
        mensajes = resultado.get("messages") or []
        if not isinstance(propuesta, PropuestaAgente):
            # Los modelos pequeños no llaman a la herramienta de salida:
            # escriben prosa. La propuesta se cierra con la salida
            # estructurada nativa, sobre la misma conversación.
            propuesta = self._cerrar(mensajes, handlers)
        if not isinstance(propuesta, PropuestaAgente):
            logger.warning("El agente no devolvió una propuesta válida")
            return None
        return self.validar(
            propuesta,
            permitidas=permitidas,
            vistas=_citas_vistas(mensajes),
        )

    # ------------------------------------------------------------------- cierre

    def _cerrar(
        self,
        mensajes: list[Any],
        handlers: list[Any],
    ) -> PropuestaAgente | None:
        """Cierra la propuesta en un esquema estricto.

        La conversación que armó el agente (el prompt, lo que consultó y lo que
        le devolvieron las herramientas) se le vuelve a pasar al modelo con el
        esquema de la propuesta y se le pide que lo complete. La salida
        estructurada **nativa** del proveedor es la que funciona con Ollama:
        pedirle al modelo que llame a la herramienta de salida no lo hace, y
        por eso esta es la vía de cierre.

        Es un paso más del agente: queda registrado en la trayectoria con su
        modelo, sus tokens y su tiempo, y consume presupuesto como cualquier
        otro.
        """
        if not mensajes:
            return None
        cerrador = self._cerrador()
        if cerrador is None:
            return None
        conversacion = [
            SystemMessage(content=INSTRUCCION),
            *mensajes,
            HumanMessage(content=INSTRUCCION_CIERRE),
        ]
        try:
            salida = cerrador.invoke(conversacion, config={"callbacks": handlers})
        except PresupuestoAgotado:
            # Condición de corte del presupuesto: sube y se anota.
            raise
        except Exception as exc:  # noqa: BLE001 - el cierre no puede tumbar el análisis
            logger.warning("El cierre estructurado falló: %s", exc)
            return None
        return _propuesta_de(salida)

    def _cerrador(self) -> Any | None:
        """El modelo con el esquema de la propuesta, o ``None`` si no puede.

        Primero se pide la salida estructurada **nativa** del proveedor (en
        Ollama es el ``format`` con el JSON schema). Si el modelo no la
        soporta, se cae a la salida por llamada a herramienta, que es lo que
        usan los modelos grandes.

        Se pide con ``include_raw=True`` para no perder la respuesta cuando el
        modelo se desvía un poco del esquema: el rescate lo hace
        ``_propuesta_de``.
        """
        try:
            return self._modelo.with_structured_output(
                _esquema_cierre(),
                method="json_schema",
                include_raw=True,
            )
        except Exception as exc:  # noqa: BLE001 - se prueba el otro camino
            logger.info("El modelo no usa salida estructurada nativa: %s", exc)
        try:
            return self._modelo.with_structured_output(
                PropuestaAgente,
                include_raw=True,
            )
        except Exception as exc:  # noqa: BLE001 - sin cierre estructurado no hay propuesta
            logger.warning("El modelo no soporta salida estructurada: %s", exc)
            return None

    # ------------------------------------------------------------------ validación

    def validar(
        self,
        propuesta: PropuestaAgente,
        *,
        permitidas: tuple[str, ...],
        vistas: tuple[str, ...] = (),
    ) -> PropuestaValidada | None:
        """Contrasta la propuesta con el catálogo y con las citas conocidas.

        Una cita que no está en la lista de permitidas descarta la propuesta
        entera: es el mismo control que hay en la redacción.

        Las citas **vistas** son las que le devolvieron las herramientas al
        agente: si el manual le mostró una sección, puede citarla. Lo que no
        está ni en la lista de permitidas ni entre lo que consultó es
        inventado, y la propuesta se descarta entera.
        """
        conocidas = {_normalizar(cita) for cita in permitidas}
        conocidas |= {_normalizar(cita) for cita in vistas}
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
        # El agente necesita más contexto que el redactor: el prompt lleva la
        # ficha de la falla, las mediciones y las reglas, y cada vuelta de
        # herramientas vuelve a mandarlo. Con el contexto del redactor
        # (2048) no le queda lugar ni para la respuesta.
        num_ctx=getattr(settings, "agente_num_ctx", settings.ollama_num_ctx),
        num_thread=settings.ollama_num_thread,
        # Y necesita escribir más: la propuesta completa (falla, causas, citas,
        # tareas y justificación) no entra en 250 tokens.
        num_predict=getattr(
            settings, "agente_max_tokens_salida", settings.ollama_max_tokens
        ),
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


def _esquema_cierre() -> dict[str, Any]:
    """El esquema de la propuesta, con **todos** los campos obligatorios.

    El modelo local **omite** los campos que el esquema marca como
    opcionales: con la falla, las causas y las citas opcionales, la respuesta
    venía con la justificación y nada más. Con todos los campos obligatorios
    el proveedor los completa y la propuesta llega entera.

    El modelo de Pydantic no cambia: sigue siendo tolerante (los campos que
    falten se completan con su valor por defecto) y el rescate de ``_validar``
    sigue aplicando.
    """
    esquema = dict(PropuestaAgente.model_json_schema())
    propiedades = esquema.get("properties") or {}
    esquema["required"] = list(propiedades)
    return esquema


def _citas_vistas(mensajes: list[Any]) -> tuple[str, ...]:
    """Las citas que le devolvieron las herramientas al agente.

    Cada tool del manual responde con su cita (``"cita": "TX350-D §8.3"``).
    Si el agente consultó el manual y le mostró una sección, puede citarla:
    la propuesta se contrasta contra lo que **vio**, no contra una lista
    escrita a mano. Lo que no está ni en la lista de permitidas ni entre lo
    que consultó es inventado.
    """
    vistas: list[str] = []
    for mensaje in mensajes:
        if type(mensaje).__name__ != "ToolMessage":
            continue
        contenido = getattr(mensaje, "content", None)
        if not isinstance(contenido, str) or not contenido.strip():
            continue
        try:
            datos = json.loads(contenido)
        except (TypeError, ValueError):
            continue
        for cita in _citas_de(datos):
            if cita not in vistas:
                vistas.append(cita)
    return tuple(vistas)


def _citas_de(datos: Any) -> list[str]:
    """Las citas que aparecen en una estructura de datos, a cualquier nivel."""
    if isinstance(datos, dict):
        salida: list[str] = []
        for clave, valor in datos.items():
            if clave == "cita" and isinstance(valor, str) and valor.strip():
                salida.append(valor.strip())
            else:
                salida.extend(_citas_de(valor))
        return salida
    if isinstance(datos, list):
        salida = []
        for item in datos:
            salida.extend(_citas_de(item))
        return salida
    return []


def _propuesta_de(salida: Any) -> PropuestaAgente | None:
    """La propuesta que devolvió el cierre, o ``None``.

    El cierre puede devolver tres cosas: la propuesta ya validada, el
    ``dict`` de ``include_raw`` (con ``raw``, ``parsed`` y ``parsing_error``)
    o el ``dict`` con los campos. En los dos últimos casos se intenta
    validar; si el modelo se desvió un poco del esquema (un campo de más, una
    justificación larguísima) se recorta lo que sobra en vez de tirar la
    propuesta entera: la propuesta igual se contrasta con el catálogo
    después, así que lo que no se puede respaldar se descarta ahí.
    """
    if isinstance(salida, PropuestaAgente):
        return salida
    if not isinstance(salida, dict):
        logger.warning("El cierre estructurado no devolvió una propuesta")
        return None
    if "parsed" in salida:
        parseado = salida.get("parsed")
        if isinstance(parseado, PropuestaAgente):
            return parseado
        if isinstance(parseado, dict):
            return _validar(parseado)
        datos = _json_de(salida.get("raw"))
        if datos is None:
            logger.warning(
                "La propuesta del cierre no validó: %s",
                salida.get("parsing_error") or "el modelo no devolvió JSON",
            )
            return None
    else:
        datos = salida
    return _validar(datos)


def _json_de(mensaje: Any) -> dict[str, Any] | None:
    """El JSON del contenido de un mensaje, si lo hay."""
    contenido = getattr(mensaje, "content", None)
    if contenido is None and isinstance(mensaje, dict):
        contenido = mensaje.get("content")
    if not isinstance(contenido, str) or not contenido.strip():
        return None
    try:
        datos = json.loads(contenido)
    except (TypeError, ValueError):
        return None
    return datos if isinstance(datos, dict) else None


def _validar(datos: dict[str, Any]) -> PropuestaAgente | None:
    """Valida los datos de la propuesta; recorta lo que sobra si hace falta."""
    try:
        return PropuestaAgente.model_validate(datos)
    except ValidationError as exc:
        logger.info("La propuesta del cierre no validó (%s): se recorta", exc)
    try:
        return PropuestaAgente.model_validate(_recortar(datos))
    except ValidationError as exc:
        logger.warning("La propuesta del cierre no validó: %s", exc)
        return None


def _recortar(datos: dict[str, Any]) -> dict[str, Any]:
    """La propuesta sin los campos inventados y con los textos acotados.

    El modelo puede agregar un campo (``extra="forbid"``) o escribir una
    justificación más larga de lo que entra. Ninguna de las dos cosas cambia
    lo que el agente propone: se recortan y el resto se contrasta con el
    catálogo como siempre.
    """
    limpio: dict[str, Any] = {}
    for clave in ("falla", "causas", "citas", "tareas", "justificacion", "confianza"):
        if clave not in datos:
            continue
        valor = datos[clave]
        if clave == "justificacion" and isinstance(valor, str):
            limpio[clave] = valor[:MAX_CARACTERES]
        elif clave == "falla" and isinstance(valor, str):
            limpio[clave] = valor[:64]
        elif isinstance(valor, list):
            limpio[clave] = [item for item in valor if isinstance(item, str)][:MAX_ITEMS]
        elif clave == "confianza" and valor not in ("alta", "media", "baja"):
            limpio[clave] = "media"
        else:
            limpio[clave] = valor
    return limpio


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
