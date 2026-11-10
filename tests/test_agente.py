"""Tests del núcleo de agentes (LangChain) y del registro de la trayectoria.

Lo que se comprueba:

* las tools del agente usan el **mismo** contrato estricto que el MCP
  (``extra="forbid"``, ``additionalProperties: false``);
* el agente consulta con herramientas y devuelve una propuesta estructurada;
* lo que el agente propone se **contrasta con el catálogo**: una cita
  desconocida o una falla que no está en los manuales descarta la propuesta;
* las causas que el manual no respalda se omiten, pero no invalidan el resto;
* cada paso (modelo y herramienta) queda registrado con sus tokens y su
  tiempo;
* si el modelo falla, el agente no tumba nada: devuelve ``None``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agente.formato import formatear
from agente.modelos import PropuestaAgente, PropuestaValidada
from agente.nucleo import AgenteAnalisis
from agente.presupuesto import Presupuesto
from agente.tools import construir_tools
from agente.trayectoria import RegistradorTrayectoria, TrazaAgente
from analisis.catalogo import CatalogoManuales
from manuales import documentos
from tools.cliente import ClientePlanta
from tools.registro import REGISTRO

# --------------------------------------------------------------------- datos


def _catalogo() -> CatalogoManuales:
    return CatalogoManuales.desde_documentos(documentos())


class ClienteFalso(ClientePlanta):
    """Un cliente que no sale a la red: devuelve respuestas vacías."""

    def __init__(self) -> None:
        super().__init__("http://planta-falsa:8100")

    def get(self, ruta: str, *, params: dict[str, Any] | None = None) -> Any:
        if ruta.startswith("/api/documentos"):
            return {"documentos": []}
        if ruta.startswith("/api/planta"):
            return {"equipos": [], "sensores": []}
        return []


#: Los tokens que dice que usó el modelo falso. Permite que el presupuesto de
#: tokens se pueda probar sin un modelo de verdad.
_USO = {"input_tokens": 800, "output_tokens": 100, "total_tokens": 900}


class ModeloFalso(BaseChatModel):
    """Un modelo que primero pide una herramienta y después responde.

    Reproduce el ciclo del agente: el modelo pide consultar los manuales, la
    herramienta contesta y el modelo devuelve la propuesta estructurada.
    """

    propuesta: dict[str, Any] = {}
    error: bool = False
    turnos: list[Any] = []
    # El nombre del modelo, como en ChatOllama: es lo que queda escrito en
    # la bitácora.
    model: str = "modelo-falso"

    @property
    def _llm_type(self) -> str:
        return "falso"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self.bind(tools=list(tools), **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.error:
            raise RuntimeError("el modelo no respondió")
        self.turnos.append(messages)
        nombres = [getattr(tool, "name", "") for tool in (kwargs.get("tools") or [])]
        if len(self.turnos) == 1 and "busqueda_rag" in nombres:
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            usage_metadata=_USO,
                            tool_calls=[
                                {
                                    "name": "busqueda_rag",
                                    "args": {"consulta": "empaquetadura", "top_k": 3},
                                    "id": "llamada-1",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    )
                ]
            )
        if "PropuestaAgente" in nombres:
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            usage_metadata=_USO,
                            tool_calls=[
                                {
                                    "name": "PropuestaAgente",
                                    "args": self.propuesta,
                                    "id": "llamada-2",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    )
                ]
            )
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(content=json.dumps(self.propuesta), usage_metadata=_USO)
                )
            ]
        )


def _agente(
    *,
    propuesta: dict[str, Any] | None = None,
    error: bool = False,
    callbacks: list[Any] | None = None,
) -> AgenteAnalisis:
    return AgenteAnalisis(
        ModeloFalso(
            propuesta=propuesta
            or {
                "falla": "TX-T01",
                "causas": ["Desgaste normal de los anillos"],
                "citas": ["TX350-D §8.3"],
                "tareas": ["T-B-EMP"],
                "justificacion": "La empaquetadura está gastada.",
                "confianza": "alta",
            },
            error=error,
        ),
        cliente=ClienteFalso(),
        catalogo=_catalogo(),
        callbacks=callbacks,
    )


class ModeloEnProsa(ModeloFalso):
    """Un modelo que consulta con herramientas y después escribe prosa.

    Es lo que hacen los modelos pequeños que corren en la planta: consultan
    bien, pero al terminar **no llaman a la herramienta de salida** y escriben
    la propuesta en texto. La propuesta se cierra después, con la salida
    estructurada nativa del proveedor.
    """

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        """El cierre: el mismo modelo, con el esquema y en JSON."""
        from langchain_core.runnables import RunnableLambda

        def _cerrar(mensajes: Any) -> dict[str, Any]:
            # Se llama al modelo de verdad: así los callbacks ven el paso y la
            # trayectoria lo registra como lo que es.
            respuesta = self.invoke(mensajes)
            return {
                "raw": respuesta,
                "parsed": json.loads(respuesta.content),
                "parsing_error": None,
            }

        return RunnableLambda(_cerrar)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.error:
            raise RuntimeError("el modelo no respondió")
        self.turnos.append(messages)
        nombres = [getattr(tool, "name", "") for tool in (kwargs.get("tools") or [])]
        if len(self.turnos) == 1 and "busqueda_rag" in nombres:
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            usage_metadata=_USO,
                            tool_calls=[
                                {
                                    "name": "busqueda_rag",
                                    "args": {"consulta": "empaquetadura", "top_k": 3},
                                    "id": "llamada-1",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    )
                ]
            )
        if len(self.turnos) == 2:
            # El modelo escribe la propuesta en prosa y no llama a la
            # herramienta de salida.
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content=(
                                "La falla es TX-T01, la causa es el desgaste normal "
                                "de los anillos y la cita es TX350-D §8.3."
                            ),
                            usage_metadata=_USO,
                        )
                    )
                ]
            )
        # El cierre: la propuesta en JSON.
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(content=json.dumps(self.propuesta), usage_metadata=_USO)
                )
            ]
        )


def _agente_en_prosa(
    *,
    callbacks: list[Any] | None = None,
    propuesta: dict[str, Any] | None = None,
) -> AgenteAnalisis:
    return AgenteAnalisis(
        ModeloEnProsa(
            propuesta=propuesta
            or {
                "falla": "TX-T01",
                "causas": ["Desgaste normal de los anillos"],
                "citas": ["TX350-D §8.3"],
                "tareas": ["T-B-EMP"],
                "justificacion": "La empaquetadura está gastada.",
                "confianza": "alta",
            },
        ),
        cliente=ClienteFalso(),
        catalogo=_catalogo(),
        callbacks=callbacks,
    )


# ----------------------------------------------------------------------- tools


def test_las_tools_del_agente_son_las_del_registro() -> None:
    """No hay una segunda definición: son las mismas tools del MCP."""
    tools = construir_tools(ClienteFalso())
    assert {tool.name for tool in tools} == set(REGISTRO)


def test_el_esquema_de_la_tool_es_estricto() -> None:
    """El modelo ve el mismo contrato que ve un cliente MCP."""
    tools = {tool.name: tool for tool in construir_tools(ClienteFalso())}
    esquema = tools["busqueda_rag"].args_schema.model_json_schema()
    assert esquema["additionalProperties"] is False
    assert "consulta" in esquema["properties"]


def test_la_tool_rechaza_un_campo_desconocido() -> None:
    """Una tool que recibe un campo que no conoce falla, no lo ignora."""
    from pydantic import ValidationError

    tools = {tool.name: tool for tool in construir_tools(ClienteFalso())}
    with pytest.raises(ValidationError):
        tools["busqueda_rag"].invoke({"consulta": "empaquetadura", "inventado": 1})


def test_la_tool_devuelve_texto() -> None:
    """La tool devuelve un texto corto, no un objeto."""
    tools = {tool.name: tool for tool in construir_tools(ClienteFalso())}
    salida = tools["busqueda_manuales"].invoke({"consulta": "empaquetadura"})
    assert isinstance(salida, str)


# ------------------------------------------------------------------ validación


def test_una_cita_desconocida_descarta_la_propuesta() -> None:
    """Si el agente cita algo que no está en los manuales, no se usa nada."""
    agente = _agente()
    propuesta = PropuestaAgente(
        falla="TX-T01",
        citas=["TX350-D §99.9"],
        justificacion="porque sí",
    )
    assert agente.validar(propuesta, permitidas=("TX350-D §8.3",)) is None


def test_una_falla_que_no_esta_en_el_catalogo_descarta_la_propuesta() -> None:
    """El agente no puede inventar una falla del catálogo."""
    agente = _agente()
    propuesta = PropuestaAgente(
        falla="F99-INVENTADA",
        citas=["TX350-D §8.3"],
        justificacion="porque sí",
    )
    assert agente.validar(propuesta, permitidas=("TX350-D §8.3",)) is None


def test_las_causas_sin_respaldo_se_omiten_pero_no_invalidan() -> None:
    """Una causa que el manual no lista se omite; el resto sigue."""
    agente = _agente()
    propuesta = PropuestaAgente(
        falla="TX-T01",
        causas=["Desgaste normal de los anillos", "El operador paseó un perro"],
        citas=["TX350-D §8.3"],
        justificacion="porque sí",
    )
    validada = agente.validar(propuesta, permitidas=("TX350-D §8.3",))
    assert validada is not None
    assert len(validada.causas) == 1
    assert validada.causas_descartadas == ("El operador paseó un perro",)


def test_una_propuesta_valida_se_acepta() -> None:
    agente = _agente()
    propuesta = PropuestaAgente(
        falla="TX-T01",
        causas=["Desgaste normal de los anillos"],
        citas=["TX350-D §8.3"],
        justificacion="La empaquetadura está gastada.",
    )
    validada = agente.validar(propuesta, permitidas=("TX350-D §8.3",))
    assert validada is not None
    assert validada.falla == "TX-T01"
    assert validada.citas == ("TX350-D §8.3",)


# ----------------------------------------------------------------------- agente


def test_el_agente_consulta_y_propone() -> None:
    """El ciclo completo: el modelo pide una herramienta y propone después."""
    agente = _agente()
    propuesta = agente.consultar("probá", permitidas=("TX350-D §8.3",))
    assert propuesta is not None
    assert propuesta.falla == "TX-T01"
    assert propuesta.citas == ("TX350-D §8.3",)


def test_si_el_modelo_falla_no_rompe_nada() -> None:
    """Un modelo caído devuelve ``None``: la orden ya está publicada."""
    agente = _agente(error=True)
    assert agente.consultar("probá", permitidas=("TX350-D §8.3",)) is None


# ------------------------------------------------------------------- el cierre


def test_el_agente_cierra_la_propuesta_cuando_el_modelo_escribe_prosa() -> None:
    """El modelo pequeño no llama a la herramienta de salida: escribe prosa.

    Con el modelo local de 3B, el agente consultaba bien pero **nunca**
    devolvía una propuesta válida: la herramienta de salida no la llamaba y
    la respuesta quedaba en texto. El cierre es lo que arregla eso: la
    conversación se le vuelve a pasar al modelo con el esquema y la
    propuesta vuelve estructurada.
    """
    agente = _agente_en_prosa()
    propuesta = agente.consultar("probá", permitidas=("TX350-D §8.3",))
    assert propuesta is not None
    assert propuesta.falla == "TX-T01"
    assert propuesta.causas == ("Desgaste normal de los anillos",)
    assert propuesta.citas == ("TX350-D §8.3",)
    assert propuesta.tareas == ("T-B-EMP",)


def test_el_cierre_se_registra_en_la_trayectoria() -> None:
    """El cierre es un paso más del agente: queda registrado como tal."""
    registrador = RegistradorFalso()
    traza = TrazaAgente(registrador, orden_id="ORD-1", equipo="B-101")
    agente = _agente_en_prosa(callbacks=[traza])

    agente.consultar("probá", permitidas=("TX350-D §8.3",))

    modelos = [fila for fila in registrador.filas if fila["tipo"] == "modelo"]
    assert len(modelos) >= 3
    # El último es el cierre: la propuesta estructurada.
    assert "TX-T01" in str(modelos[-1].get("salida"))


def test_el_esquema_del_cierre_exige_todos_los_campos() -> None:
    """El modelo local omite los campos opcionales: el cierre los exige.

    Con el esquema de Pydantic (campos opcionales) la respuesta venía con la
    justificación y nada más: la falla, las causas y las citas faltaban.
    """
    from agente.nucleo import _esquema_cierre

    esquema = _esquema_cierre()
    assert set(esquema["required"]) == set(esquema["properties"])
    assert "falla" in esquema["required"]
    assert "causas" in esquema["required"]
    assert "citas" in esquema["required"]


def test_una_cita_que_el_agente_no_vio_descarta_la_propuesta() -> None:
    """Lo que el agente no consultó no lo puede citar.

    Una cita que no está en la lista de permitidas ni entre las que le
    mostraste las herramientas es inventada, y la propuesta se descarta
    entera.
    """
    agente = _agente()
    propuesta = PropuestaAgente(
        falla="TX-T01",
        citas=["TX350-D §7.5"],
        justificacion="porque sí",
    )
    assert agente.validar(propuesta, permitidas=("TX350-D §8.3",)) is None


def test_una_cita_que_mostro_la_herramienta_se_acepta() -> None:
    """Si el manual le mostró la sección, puede citarla."""
    agente = _agente()
    propuesta = PropuestaAgente(
        falla="TX-T01",
        citas=["TX350-D §7.5"],
        justificacion="porque sí",
    )
    validada = agente.validar(
        propuesta,
        permitidas=("TX350-D §8.3",),
        vistas=("TX350-D §7.5",),
    )
    assert validada is not None
    assert validada.citas == ("TX350-D §7.5",)


def test_el_agente_recoge_las_citas_de_las_herramientas() -> None:
    """Las citas que le devolvieron las tools son las que puede usar."""
    from langchain_core.messages import ToolMessage

    from agente.nucleo import _citas_vistas

    mensajes = [
        ToolMessage(
            content=json.dumps(
                [{"documento": "TX350-D", "cita": "TX350-D §8.3", "texto": "..."}]
            ),
            tool_call_id="llamada-1",
        ),
        ToolMessage(content="[]", tool_call_id="llamada-2"),
    ]
    assert _citas_vistas(mensajes) == ("TX350-D §8.3",)


def test_la_justificacion_del_agente_se_formatea() -> None:
    """La bitácora de la orden se lee igual venga del agente o del modelo."""
    propuesta = PropuestaValidada(
        falla="TX-T01",
        causas=("Desgaste normal de los anillos",),
        citas=("TX350-D §8.3",),
        tareas=("T-B-EMP",),
        justificacion="La empaquetadura está gastada.",
        confianza="alta",
    )
    texto = formatear(propuesta, ("TX350-D §8.3",))
    assert "agente LangChain" in texto
    assert "TX350-D §8.3" in texto
    assert "TX-T01" in texto


# ------------------------------------------------------------------ trayectoria


class RegistradorFalso(RegistradorTrayectoria):
    """Un registrador que guarda en memoria, sin base de datos."""

    def __init__(self) -> None:
        super().__init__(None)
        self.filas: list[dict[str, Any]] = []

    def registrar(self, **kwargs: Any) -> None:
        self.filas.append(kwargs)


def test_la_trayectoria_registra_el_modelo_y_las_tools() -> None:
    """Cada paso queda con su herramienta, sus argumentos y su tiempo."""
    registrador = RegistradorFalso()
    traza = TrazaAgente(registrador, orden_id="ORD-1", equipo="B-101")
    agente = _agente(callbacks=[traza])
    agente.consultar("probá", permitidas=("TX350-D §8.3",))

    tipos = [fila["tipo"] for fila in registrador.filas]
    assert "tool" in tipos
    assert "modelo" in tipos
    herramientas = [fila.get("herramienta") for fila in registrador.filas]
    assert "busqueda_rag" in herramientas
    for fila in registrador.filas:
        assert fila["orden_id"] == "ORD-1"
        assert fila["equipo"] == "B-101"


def test_la_trayectoria_no_rompe_sin_base_de_datos() -> None:
    """Sin base, el registrador no hace nada y no lanza."""
    registrador = RegistradorTrayectoria(None)
    assert registrador.activo is False
    registrador.registrar(etapa="agente", tipo="modelo")
    assert registrador.pasos("ORD-1") == []


# ------------------------------------------------------------------- presupuesto


def test_el_agente_se_corta_cuando_se_pasan_los_pasos() -> None:
    """El presupuesto corta al agente: la propuesta se pierde, la orden no.

    Es el caso más importante: el corte es una condición prevista. El agente
    devuelve ``None`` y la orden conserva su texto determinista.
    """
    registrador = RegistradorFalso()
    presupuesto = Presupuesto(max_pasos=1, max_tokens=100_000, timeout_s=60)
    traza = TrazaAgente(registrador, orden_id="ORD-1", presupuesto=presupuesto)
    agente = _agente(callbacks=[traza])

    propuesta = agente.consultar(
        "probá",
        permitidas=("TX350-D §8.3",),
        presupuesto=presupuesto,
    )

    assert propuesta is None
    assert presupuesto.agotado() is True
    assert "pasos" in (presupuesto.corte or "")


def test_el_agente_se_corta_cuando_se_pasan_los_tokens() -> None:
    """El presupuesto de tokens también corta, y queda dicho por qué."""
    registrador = RegistradorFalso()
    presupuesto = Presupuesto(max_pasos=10, max_tokens=1, timeout_s=60)
    traza = TrazaAgente(registrador, orden_id="ORD-1", presupuesto=presupuesto)
    agente = _agente(callbacks=[traza])

    propuesta = agente.consultar(
        "probá",
        permitidas=("TX350-D §8.3",),
        presupuesto=presupuesto,
    )

    assert propuesta is None
    assert "tokens" in (presupuesto.corte or "")


def test_el_corte_del_agente_queda_registrado() -> None:
    """El corte se escribe en la trayectoria: no se pierde en silencio."""
    registrador = RegistradorFalso()
    presupuesto = Presupuesto(max_pasos=1, max_tokens=100_000, timeout_s=60)
    traza = TrazaAgente(registrador, orden_id="ORD-1", presupuesto=presupuesto)
    agente = _agente(callbacks=[traza])

    agente.consultar("probá", permitidas=("TX350-D §8.3",), presupuesto=presupuesto)

    # Los pasos del modelo y de las tools se registered antes del corte.
    tipos = [fila["tipo"] for fila in registrador.filas]
    assert "modelo" in tipos
    assert "tool" in tipos


def test_la_trayectoria_registra_el_nombre_del_modelo() -> None:
    """Cada paso dice qué modelo lo hizo: sin eso, la bitácora no sirve."""
    registrador = RegistradorFalso()
    traza = TrazaAgente(registrador, orden_id="ORD-1", modelo="qwen2.5:3b-instruct")
    agente = _agente(callbacks=[traza])

    agente.consultar("probá", permitidas=("TX350-D §8.3",))

    modelos = [fila.get("modelo") for fila in registrador.filas if fila["tipo"] == "modelo"]
    assert modelos
    assert all(modelo == "qwen2.5:3b-instruct" for modelo in modelos)


def test_el_costo_de_un_paso_sale_de_los_tokens() -> None:
    """El costo no se estima a ojo: sale de los tokens y de la tarifa.

    Con el modelo local de la planta la tarifa es 0 (no hay nada que
    facturar); con un modelo de nube, el costo de cada paso se calcula con los
    tokens que midió el callback.
    """
    registrador = RegistradorFalso()
    traza = TrazaAgente(
        registrador,
        orden_id="ORD-1",
        modelo="qwen2.5:3b-instruct",
        precio_usd_por_millon=3.0,
    )
    agente = _agente(callbacks=[traza])

    agente.consultar("probá", permitidas=("TX350-D §8.3",))

    modelos = [fila for fila in registrador.filas if fila["tipo"] == "modelo"]
    assert modelos
    for fila in modelos:
        tokens = fila["tokens_prompt"] + fila["tokens_completion"]
        assert fila["costo_usd"] == pytest.approx(tokens * 3.0 / 1_000_000)


def test_con_el_modelo_local_el_costo_es_cero() -> None:
    """El modelo local no se factura: el costo queda en 0."""
    registrador = RegistradorFalso()
    traza = TrazaAgente(registrador, orden_id="ORD-1", modelo="qwen2.5:3b-instruct")
    agente = _agente(callbacks=[traza])

    agente.consultar("probá", permitidas=("TX350-D §8.3",))

    modelos = [fila for fila in registrador.filas if fila["tipo"] == "modelo"]
    assert modelos
    assert all(fila["costo_usd"] == 0.0 for fila in modelos)


# ------------------------------------------------------- la integración completa


def test_el_servicio_justifica_con_el_agente_y_deja_la_trayectoria(planta: dict) -> None:
    """El camino real: el servicio consulta al agente y deja su rastro.

    El agente **agrega** la justificación: la orden ya salió con su estado
    decidido por las reglas.
    """
    from datetime import UTC, datetime

    from planta_contrato import EventoSistema, TipoEvento

    from analisis.servicio import ServicioAnalisis
    from planta.parte_extractor import Hallazgo

    registrador = RegistradorFalso()
    agente = AgenteAnalisis(
        ModeloFalso(
            propuesta={
                "falla": "TX-T01",
                "causas": ["Desgaste normal de los anillos"],
                "citas": ["TX350-D §8.3"],
                "tareas": ["T-B-EMP"],
                "justificacion": "La empaquetadura está gastada.",
                "confianza": "alta",
            }
        ),
        cliente=ClienteFalso(),
        catalogo=_catalogo(),
    )
    servicio = ServicioAnalisis(
        _catalogo(),
        cliente=ClienteFalso(),
        planta=planta,
        agente=agente,
        registrador=registrador,
    )
    evento = EventoSistema(
        id="parte:nota-1:B-102",
        ts=datetime(2026, 11, 10, 9, 0, tzinfo=UTC),
        tipo=TipoEvento.escalado,
        equipo="B-102",
        origen="parte",
        parte_id="parte-1",
        nota_ids=["nota-1"],
        resumen="B-102 goteo_empaquetadura: pérdida importante por empaquetadura",
    )
    filas = [
        Hallazgo(
            tag="B-102",
            variable="goteo_empaquetadura",
            valor=180,
            unidad="gotas/min",
            observacion="pérdida importante por empaquetadura",
        )
    ]

    analisis = servicio.analizar_evento(evento, filas=filas)
    assert analisis is not None
    assert analisis.falla is not None
    assert analisis.falla.codigo == "TX-T01"
    texto = servicio.redactar(analisis)

    assert texto is not None
    assert "agente LangChain" in texto
    tipos = [fila["tipo"] for fila in registrador.filas]
    assert "modelo" in tipos
    assert "tool" in tipos
    # Y queda el resumen del presupuesto de la consulta.
    assert "consulta" in tipos


def test_el_resumen_de_la_consulta_lleva_modelo_tokens_y_tiempo(planta: dict) -> None:
    """La fila ``consulta`` dice con qué modelo se consultó y cuánto costó.

    Sin esto, la bitácora tiene los pasos sueltos pero no el resumen: no se
    puede responder "cuánto tardó la consulta ni con qué modelo" de una
    mirada.
    """
    from datetime import UTC, datetime

    from planta_contrato import EventoSistema, TipoEvento

    from analisis.servicio import ServicioAnalisis
    from planta.parte_extractor import Hallazgo

    registrador = RegistradorFalso()
    servicio = ServicioAnalisis(
        _catalogo(),
        cliente=ClienteFalso(),
        planta=planta,
        agente=_agente(),
        registrador=registrador,
        precio_usd_por_millon=3.0,
    )
    evento = EventoSistema(
        id="parte:nota-1:B-102",
        ts=datetime(2026, 11, 10, 9, 0, tzinfo=UTC),
        tipo=TipoEvento.escalado,
        equipo="B-102",
        origen="parte",
        parte_id="parte-1",
        nota_ids=["nota-1"],
        resumen="B-102 goteo_empaquetadura: pérdida importante por empaquetadura",
    )
    filas = [
        Hallazgo(
            tag="B-102",
            variable="goteo_empaquetadura",
            valor=180,
            unidad="gotas/min",
            observacion="pérdida importante por empaquetadura",
        )
    ]
    analisis = servicio.analizar_evento(evento, filas=filas)
    assert analisis is not None
    servicio.redactar(analisis)

    consultas = [fila for fila in registrador.filas if fila["tipo"] == "consulta"]
    assert consultas
    fila = consultas[0]
    assert fila["modelo"] == "modelo-falso"
    assert fila["tokens_prompt"] > 0
    assert fila["tokens_completion"] > 0
    assert fila["segundos"] > 0
    # El costo sale de los tokens medidos, no de una estimación.
    tokens = fila["tokens_prompt"] + fila["tokens_completion"]
    assert fila["costo_usd"] == pytest.approx(tokens * 3.0 / 1_000_000)
