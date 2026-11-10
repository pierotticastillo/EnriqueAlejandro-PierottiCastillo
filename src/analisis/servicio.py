"""Servicio de análisis: del escalado a la orden de trabajo.

Es el hilo de la Fase 5:

1. se junta la evidencia (parte confirmado, fotos, tendencia de sensores);
2. se consulta el manual por RAG y se cruza con el catálogo de fallas;
3. se ordenan las causas probables, cada una con su evidencia y lo que falta
   confirmar;
4. se arma el borrador de orden con tareas, repuestos y costo;
5. se pasa la orden por las reglas de aprobación, que están en código.

El mismo equipo con la misma falla no abre dos órdenes: el identificador es
determinista y la orden que ya existe se actualiza.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from datetime import datetime
from typing import Any

from planta_contrato import EstadoOrden, EventoSistema, OrdenSistema, Repuesto

from agente.formato import formatear as formatear_propuesta
from agente.nucleo import AgenteAnalisis
from agente.presupuesto import Presupuesto
from analisis.aprobacion import Contexto, evaluar
from analisis.catalogo import CatalogoManuales
from analisis.causas import causalizar
from analisis.estado import EstadoBombas
from analisis.evidencia import Evidencia, reunir_parte, reunir_texto
from analisis.historial import es_reincidencia
from analisis.llm import AnalysisModel
from analisis.modelos import Analisis, FallaCatalogo, Medicion
from analisis.orden import TAREA_NO_ESTANDAR, armar_borrador, descripcion_de_reincidencia
from analisis.redaccion import redactar
from tools.cliente import ClientePlanta, ErrorHerramienta
from tools.modelos import ConsultaManuales

logger = logging.getLogger("analisis.servicio")


class RecuperadorDiferido:
    """Un recuperador que todavía no está listo.

    El índice del RAG se arma en un hilo aparte: el sistema tiene que arrancar
    igual si el índice no se puede construir. Mientras tanto, este objeto
    devuelve un error y el análisis sigue con el cruce léxico del manual.
    """

    def __init__(self) -> None:
        self._valor: Any = None
        self._lock = threading.Lock()

    def definir(self, recuperador: Any) -> None:
        with self._lock:
            self._valor = recuperador

    @property
    def disponible(self) -> bool:
        with self._lock:
            return self._valor is not None

    def buscar(self, consulta: Any) -> Any:
        with self._lock:
            recuperador = self._valor
        if recuperador is None:
            raise RuntimeError("El índice del manual todavía no está listo")
        return recuperador.buscar(consulta)


class ServicioAnalisis:
    """Produce el análisis y la orden de un escalado."""

    def __init__(
        self,
        catalogo: CatalogoManuales,
        *,
        estado: EstadoBombas | None = None,
        cliente: ClientePlanta | None = None,
        recuperador: Any = None,
        planta: dict[str, Any] | None = None,
        modelo: AnalysisModel | None = None,
        agente: AgenteAnalisis | None = None,
        registrador: Any = None,
        agente_max_tokens: int = 12000,
        agente_timeout_s: float = 180.0,
    ) -> None:
        self._catalogo = catalogo
        self._estado = estado
        self._cliente = cliente
        self._recuperador = recuperador
        self._planta = planta or {}
        # El modelo de lenguaje es un auxiliar de redacción. Si no hay, el
        # sistema funciona igual: las reglas están en código.
        self._modelo = modelo
        # El núcleo de agentes (LangChain) consulta los manuales y el
        # historial con herramientas y propone con citas. Como el modelo, es
        # un auxiliar: la decisión la toman las reglas, en código.
        self._agente = agente
        self._registrador = registrador
        # El presupuesto de la consulta al agente: pasos, tokens y tiempo.
        self._agente_max_tokens = agente_max_tokens
        self._agente_timeout_s = agente_timeout_s

    # ------------------------------------------------------------------ análisis

    def analizar_evento(
        self,
        evento: EventoSistema,
        *,
        filas: list[Any] | None = None,
        fotos: list[str] | None = None,
    ) -> Analisis | None:
        """Analiza un evento escalado y devuelve el análisis con su borrador."""
        equipo = evento.equipo
        if not equipo:
            return None
        evidencia = reunir_parte(
            equipo=equipo,
            filas=filas or [],
            fotos=fotos,
            resumen=evento.resumen,
            planta=self._planta,
        )
        return self._analizar(evento, evidencia)

    def analizar_alerta_seguridad(
        self,
        evento: EventoSistema,
        *,
        texto: str,
        fotos: list[str] | None = None,
    ) -> Analisis | None:
        """Analiza una alerta de seguridad: la orden va siempre a una persona.

        Una condición insegura no se aprueba sola (MBP-4 §10.2): la orden que
        sale de una alerta de seguridad queda para revisión humana.
        """
        equipo = evento.equipo or "PLANTA"
        evidencia = reunir_texto(
            equipo=equipo,
            texto=texto,
            fotos=fotos,
            resumen=evento.resumen,
            planta=self._planta,
        )
        return self._analizar(evento, evidencia, revision_humana=True, incluir_seguridad=True)

    def _analizar(
        self,
        evento: EventoSistema,
        evidencia: Evidencia,
        *,
        revision_humana: bool = False,
        incluir_seguridad: bool = False,
    ) -> Analisis:
        equipo = evento.equipo or evidencia.equipo
        tipo_equipo = self._tipo_de(equipo)
        citas_rag = self._consultar_manual(evidencia)
        causas = causalizar(
            evidencia,
            self._catalogo,
            tipo_equipo=tipo_equipo,
            citas_rag=citas_rag,
            incluir_seguridad=incluir_seguridad,
        )
        falla = self._falla_de(causas)
        # Una condición insegura no es una tarea de mantenimiento del plan: la
        # orden sale como NO-ESTANDAR con la descripción del procedimiento
        # (MBP-4 §10.2 y §13.2), y la decide una persona.
        tarea = None if incluir_seguridad else self._tarea_de(falla, equipo, causas)
        reincidencia = es_reincidencia(
            falla,
            self._ordenes_previas(equipo),
            tarea_codigo=tarea.codigo if tarea else None,
            referencia=evento.ts,
        )
        estado_bombas = self._estado.de_bombas() if self._estado else {}
        borrador = armar_borrador(
            equipo=equipo,
            falla=falla,
            tarea=tarea,
            catalogo=self._catalogo,
            estado_bombas=estado_bombas,
            evidencia=evidencia,
            causas=causas,
            reincidencia=reincidencia,
        )
        evaluacion = evaluar(
            borrador,
            Contexto(
                catalogo=self._catalogo,
                estado_bombas=estado_bombas,
                revision_humana=revision_humana,
            ),
        )
        borrador = replace(borrador, motivo=None if evaluacion.cumple else evaluacion.motivo)
        if not borrador.descripcion and TAREA_NO_ESTANDAR in borrador.tareas:
            # La tarea que no está en el catálogo va con la descripción de lo
            # que hay que hacer (MBP-4 §13.2).
            borrador = replace(borrador, descripcion=evento.resumen or None)
        notas: list[str] = []
        if reincidencia:
            descripcion = descripcion_de_reincidencia(falla)
            if descripcion:
                notas.append(
                    "La falla ya se atendió antes en este equipo: "
                    f"{descripcion.lower()}. Se suma la tarea {TAREA_NO_ESTANDAR}."
                )
        if revision_humana:
            notas.append(
                "La evidencia muestra una condición insegura o daño estructural: "
                "la orden no se aprueba sola."
            )
        return Analisis(
            evento_id=evento.id,
            equipo=equipo,
            falla=falla,
            causas=causas,
            borrador=borrador,
            evaluacion=evaluacion,
            citas=borrador.citas,
            ts=evento.ts,
            reincidencia=reincidencia,
            notas=tuple(notas),
            mediciones=_mediciones(evidencia),
        )

    # -------------------------------------------------------------------- orden

    def orden_sistema(self, analisis: Analisis) -> OrdenSistema:
        """El borrador, en el formato que viaja por MQTT."""
        borrador = analisis.borrador
        estado = (
            EstadoOrden.aprobada
            if analisis.evaluacion.cumple
            else EstadoOrden.pendiente
        )
        return OrdenSistema(
            id=borrador.id,
            ts=analisis.ts or datetime.now().astimezone(),
            estado=estado,
            equipo=borrador.equipo,
            evento_ids=[analisis.evento_id],
            tareas=list(borrador.tareas),
            detiene_equipo=borrador.detiene_equipo,
            reduce_inyeccion=borrador.reduce_inyeccion,
            ejecutor=borrador.ejecutor,
            repuestos=[
                Repuesto(codigo=repuesto.codigo, cantidad=repuesto.cantidad)
                for repuesto in borrador.repuestos
            ],
            motivo=borrador.motivo,
            habilita=None,
            monto_habilita_usd=None,
        )

    def datos_de_orden(self, analisis: Analisis) -> dict[str, Any]:
        """Lo que se guarda de la orden junto con ella.

        La orden no se guarda sola: se guarda **por qué** se decidió así. Las
        reglas evaluadas, la evidencia y las citas quedan en la misma fila, de
        modo que la decisión se puede auditar sin volver a correr el análisis.
        """
        borrador = analisis.borrador
        evaluacion = analisis.evaluacion
        return {
            "reglas": {
                "cumple": evaluacion.cumple,
                "motivo": evaluacion.motivo,
                "reglas": [
                    {
                        "codigo": regla.codigo,
                        "nombre": regla.nombre,
                        "cumple": regla.cumple,
                        "detalle": regla.detalle,
                    }
                    for regla in evaluacion.reglas
                ],
                "falla": borrador.falla,
                "causas": list(borrador.causas),
                "evidencia": list(borrador.evidencia),
                "citas": list(borrador.citas),
                "reincidencia": analisis.reincidencia,
            },
            "descripcion": borrador.descripcion,
            "costo_usd": borrador.costo_usd,
            "repuestos": [
                {
                    "codigo": repuesto.codigo,
                    "costo_unitario": repuesto.costo_unitario,
                    "disponible": repuesto.disponible,
                }
                for repuesto in borrador.repuestos
            ],
        }

    # ------------------------------------------------------------------ redacción

    @property
    def nombre_modelo(self) -> str | None:
        """El nombre del modelo: el del agente si hay, si no el del redactor."""
        if self._agente is not None:
            return self._agente.nombre_modelo
        return getattr(self._modelo, "nombre", None)

    def redactar(self, analisis: Analisis) -> str | None:
        """La justificación técnica de la orden.

        El camino normal es el **agente** (LangChain): consulta los manuales
        y el historial con herramientas y propone con citas. Si no hay agente,
        o si no respondió, se cae al modelo sin herramientas; y si tampoco
        hay modelo, no hay justificación. En los tres casos la orden ya está
        publicada con su estado decidido: esto es un agregado, no una
        decisión.
        """
        texto = self._justificar_con_agente(analisis)
        if texto:
            return texto
        if self._modelo is None:
            return None
        tareas = self._nombres_de_tareas(analisis.borrador.tareas, analisis.borrador.equipo)
        return redactar(self._modelo, analisis, tareas=tareas)

    def _justificar_con_agente(self, analisis: Analisis) -> str | None:
        """La justificación que propone el agente de LangChain.

        El agente **propone**; el código **dispone**. Lo que devuelve se
        contrasta con el catálogo y con las citas conocidas antes de usarse: una
        cita inventada descarta la propuesta entera.
        """
        if self._agente is None:
            return None
        permitidas = tuple(analisis.citas)
        callbacks: list[Any] = []
        # El presupuesto de la consulta: pasos, tokens y timeout. Cuando se
        # agota, el corte queda escrito y la orden conserva su texto
        # determinista.
        presupuesto = Presupuesto(
            max_pasos=self._agente.max_pasos,
            max_tokens=self._agente_max_tokens,
            timeout_s=self._agente_timeout_s,
        )
        if self._registrador is not None:
            from agente.trayectoria import TrazaAgente

            callbacks.append(
                TrazaAgente(
                    self._registrador,
                    etapa="agente",
                    orden_id=analisis.borrador.id,
                    evento_id=analisis.evento_id,
                    equipo=analisis.equipo,
                    presupuesto=presupuesto,
                    modelo=self._agente.nombre_modelo,
                )
            )
        tareas = self._nombres_de_tareas(analisis.borrador.tareas, analisis.borrador.equipo)
        try:
            propuesta = self._agente.consultar(
                self._prompt_agente(analisis, tareas),
                permitidas=permitidas,
                callbacks=callbacks,
                presupuesto=presupuesto,
            )
        except Exception:  # noqa: BLE001 - el agente no puede tumbar el análisis
            logger.warning("Falló la consulta al agente", exc_info=True)
            return None
        if self._registrador is not None:
            # El corte queda en la bitácora: no se borra en silencio. Si la
            # consulta llegó bien, lo que queda es el resumen del presupuesto.
            self._registrador.registrar(
                etapa="agente",
                tipo="corte" if presupuesto.corte else "consulta",
                argumentos=presupuesto.resumen(),
                salida={"corte": presupuesto.corte},
                error=presupuesto.corte,
                orden_id=analisis.borrador.id,
                evento_id=analisis.evento_id,
                equipo=analisis.equipo,
            )
        if propuesta is None or propuesta.vacia:
            return None
        return formatear_propuesta(propuesta, permitidas)

    def _prompt_agente(self, analisis: Analisis, tareas: dict[str, str]) -> str:
        """El prompt del agente: material confiable, sin texto del operador.

        Es el mismo material que se le da al modelo de redacción (la ficha de
        la falla, las mediciones, las tareas y el resultado de las reglas) con
        un pedido distinto: en vez de redactar, el agente **consulta** con
        herramientas y **propone**.
        """
        borrador = analisis.borrador
        falla = analisis.falla
        lineas: list[str] = []
        lineas.append(f"Equipo: {borrador.equipo}")
        lineas.append(
            "Tipo de orden: "
            + ("detiene el equipo" if borrador.detiene_equipo else "no detiene el equipo")
        )
        if falla is not None:
            lineas.append("")
            lineas.append("FICHA DE LA FALLA (catálogo de manuales)")
            lineas.append(f"- Código: {falla.codigo}")
            lineas.append(f"- Título: {falla.titulo}")
            lineas.append(f"- Síntoma: {falla.sintoma}")
            if falla.causas:
                lineas.append("- Causas que lista el manual: " + "; ".join(falla.causas))
            if falla.accion:
                lineas.append("- Acción del manual: " + falla.accion)
            lineas.append(f"- Documento: {falla.documento}")
            lineas.append(f"- Cita: {falla.cita}")
        else:
            lineas.append("")
            lineas.append(
                "FICHA DE LA FALLA: no se identificó una falla del catálogo; "
                "la orden es una inspección."
            )
        if analisis.mediciones:
            lineas.append("")
            lineas.append("MEDICIONES DE PLANTA (sólo valores; no hay texto del operador)")
            for medicion in analisis.mediciones:
                valor = "-" if medicion.valor is None else f"{medicion.valor:g}"
                desvio = f" · desvío: {medicion.direccion}" if medicion.direccion else ""
                lineas.append(
                    f"- {medicion.variable}: {valor} {medicion.unidad or ''}{desvio}".rstrip()
                )
        if analisis.causas:
            lineas.append("")
            lineas.append("CAUSAS QUE YA PROPUSO EL CRUCE CON EL CATÁLOGO")
            for causa in analisis.causas:
                lineas.append(f"- {causa.causa} (cita: {causa.cita})")
        lineas.append("")
        lineas.append("TAREAS DEL PLAN (ya decididas)")
        for tarea in borrador.tareas:
            detalle = tareas.get(tarea)
            lineas.append(f"- {tarea}" + (f": {detalle}" if detalle else ""))
        lineas.append("")
        lineas.append("REGLAS DE APROBACIÓN (evaluadas en código; el resultado no se cambia)")
        for regla in analisis.evaluacion.reglas:
            lineas.append(f"- {regla.nombre}: {'cumple' if regla.cumple else 'no cumple'}")
        permitidas = tuple(analisis.citas)
        lineas.append("")
        lineas.append("CITAS PERMITIDAS (sólo podés usar estas): " + ", ".join(permitidas))
        lineas.append("")
        lineas.append(
            "Consultá los manuales y el historial con las herramientas y después "
            "proponé la falla, las causas y las citas de las que sale. "
            "Podés citar las secciones que te muestren las herramientas, tal "
            "como están escritas. "
            "No cambies las tareas, los repuestos, los costos ni el resultado de "
            "las reglas."
        )
        return "\n".join(lineas)

    def _nombres_de_tareas(self, tareas: tuple[str, ...], equipo: str) -> dict[str, str]:
        """El nombre y el procedimiento de cada tarea, del catálogo del plan."""
        salida: dict[str, str] = {}
        for codigo in tareas:
            tarea = self._catalogo.tarea_por_codigo(codigo, equipo)
            if tarea is None:
                if codigo == TAREA_NO_ESTANDAR:
                    salida[codigo] = "Tarea no prevista en el plan de mantenimiento"
                continue
            detalle = tarea.nombre
            if tarea.procedimiento:
                detalle = f"{detalle} ({tarea.procedimiento})"
            salida[codigo] = detalle
        return salida

    # ------------------------------------------------------------------ internos

    def _tipo_de(self, equipo: str) -> str:
        for item in self._planta.get("equipos") or []:
            if isinstance(item, dict) and item.get("tag") == equipo:
                return str(item.get("tipo") or "equipo")
        return "equipo"

    def _falla_de(self, causas: tuple) -> FallaCatalogo | None:
        for causa in causas:
            if causa.falla:
                falla = self._catalogo.fallas.por_codigo(causa.falla)
                if falla is not None:
                    return falla
        return None

    def _tarea_de(self, falla: FallaCatalogo | None, equipo: str, causas: tuple):
        """La tarea que hay que hacer para esta falla.

        Si la evidencia no alcanza para identificar la falla, la conclusión es
        una inspección (MBP-4 §13.2) y la tarea es la de inspección que
        corresponde: análisis de vibraciones o revisión de válvulas.
        """
        if falla is None:
            for causa in causas:
                if causa.tarea:
                    tarea = self._catalogo.tarea_por_codigo(causa.tarea, equipo)
                    if tarea is not None:
                        return tarea
            return None
        equipos = self._catalogo.equipos_de_falla(falla)
        tarea = self._catalogo.tarea_por_procedimiento(
            falla.procedimiento,
            equipo,
            equipos,
        )
        if tarea is not None:
            return tarea
        return None

    def _consultar_manual(self, evidencia: Evidencia) -> tuple[str, ...]:
        """Las citas que el RAG propone para esta evidencia."""
        if self._recuperador is None:
            return ()
        if not getattr(self._recuperador, "disponible", True):
            # El índice del manual todavía se está armando: el cruce léxico
            # sigue funcionando solo.
            return ()
        consulta = evidencia.texto or evidencia.resumen
        if not consulta.strip():
            return ()
        try:
            fragmentos = self._recuperador.buscar(
                ConsultaManuales(consulta=consulta[:500], top_k=6)
            )
        except Exception:  # noqa: BLE001 - el RAG no puede tumbar el análisis
            logger.warning("Falló la consulta al manual por RAG", exc_info=True)
            return ()
        return tuple(fragmento.cita for fragmento in fragmentos if fragmento.cita)

    def _ordenes_previas(self, equipo: str) -> list[dict]:
        if self._cliente is None:
            return []
        try:
            datos = self._cliente.get("/api/historial-ordenes", params={"equipo": equipo})
        except ErrorHerramienta as exc:
            logger.warning("No se pudo leer el historial de órdenes: %s", exc)
            return []
        if not isinstance(datos, list):
            return []
        return [orden for orden in datos if isinstance(orden, dict)]


def _mediciones(evidencia: Evidencia) -> tuple[Medicion, ...]:
    """Las mediciones estructuradas de la evidencia.

    Se descarta todo fragmento que venga del texto del operador: lo que dice
    una nota no es una medición, y no se le muestra a ningún modelo.
    """
    return tuple(
        Medicion(
            variable=fragmento.variable,
            valor=fragmento.valor,
            unidad=fragmento.unidad,
            direccion=evidencia.direcciones.get(fragmento.variable),
        )
        for fragmento in evidencia.fragmentos
        if fragmento.origen != "texto"
    )
