"""Orquestación de la Fase 1: nota de voz -> transcripción -> parte -> tablet."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from planta_contrato import (
    EventoSistema,
    FilaParte,
    MensajeTablet,
    NotaRecorrida,
    Pantalla,
    RespuestaOperador,
    TipoEvento,
)

from analisis.servicio import ServicioAnalisis
from analisis.vision import ClienteVision
from config import Settings
from db.repositorio import Repositorio, filas_desde_json
from deteccion import seguridad
from deteccion.partes import clasificar_partes, filas_anomalas, fuentes_tendencia_parte
from planta.almacenamiento import Almacenamiento
from planta.cola import encolar_aviso
from planta.heartbeat import comprobar_enlace
from planta.historial import Historiador
from planta.mqtt_listener import ClientePlanta
from planta.nombre_resolver import ResolutorNombres, desde_api
from planta.parte_extractor import Extraccion, ExtractorParte, Hallazgo, Pregunta
from planta.tablet import (
    aplicar_correcciones,
    construir_borrador,
    construir_pregunta,
    interpretar,
    resolver_pendientes,
)
from planta.transcriber import Transcriber

logger = logging.getLogger("planta.servicio")

# Los estados en los que una orden ya no se abre más: se ejecutó, se cerró,
# se observó o se certificó. Una republicación posterior (por ejemplo, la
# redacción técnica que llega tarde) no puede rebajar el estado.
ESTADOS_CERRADOS = {"Ejecutada", "Cerrada", "Observada", "Certificada"}


class ServicioParteVoz:
    """Procesa las notas y mantiene la conversación con la tablet."""

    def __init__(
        self,
        settings: Settings,
        repositorio: Repositorio,
        almacenamiento: Almacenamiento,
        transcriptor: Transcriber,
        cliente: ClientePlanta,
        planta: dict[str, Any],
        *,
        extractor: ExtractorParte | None = None,
        resolver: ResolutorNombres | None = None,
        historiador: Historiador | None = None,
        enlace_ok_fn: Callable[[], bool] | None = None,
        analisis: ServicioAnalisis | None = None,
        vision: ClienteVision | None = None,
    ) -> None:
        self._settings = settings
        self._repo = repositorio
        self._almacen = almacenamiento
        self._transcriptor = transcriptor
        self._cliente = cliente
        self._planta = planta
        self._historiador = historiador
        self._analisis = analisis
        # Modelo de nube con visión: sólo describe las fotos. Es opcional:
        # sin clave o sin modelo, el aviso de seguridad sale igual que siempre.
        self._vision = vision
        self._resolver = resolver or desde_api(planta)
        self._extractor = extractor or ExtractorParte(planta, self._resolver)
        self._enlace_ok = enlace_ok_fn or (lambda: comprobar_enlace(settings))
        self._cola: queue.Queue[tuple[str, object] | None] = queue.Queue()
        self._hilo: threading.Thread | None = None
        # Cola aparte para la transcripción: el ASR es la parte lenta de la
        # nota (decodifica el audio en CPU). Si se hiciera en el hilo de las
        # notas, la planta no respondería ni las respuestas del operador ni
        # los eventos del sensor mientras decodifica.
        self._cola_asr: queue.Queue[object | None] = queue.Queue()
        self._hilo_asr: threading.Thread | None = None
        # Notas que ya están en transcripción: la misma nota puede volver a
        # llegar por MQTT y no se transcribe dos veces.
        self._notas_en_curso: set[str] = set()
        self._candado_notas = threading.Lock()
        # Cola aparte para la redacción con el modelo local: la cola de notas
        # no puede esperar a que un modelo de 3B termine de responder.
        self._cola_redaccion: queue.Queue[tuple[object, object] | None] = queue.Queue()
        self._hilo_redaccion: threading.Thread | None = None
        self._parar = threading.Event()

    # ------------------------------------------------------------------ ciclo

    def iniciar(self) -> None:
        self._hilo = threading.Thread(target=self._trabajar, name="parte-voz", daemon=True)
        self._hilo.start()
        self._hilo_asr = threading.Thread(
            target=self._trabajar_asr, name="transcripcion", daemon=True
        )
        self._hilo_asr.start()
        self._hilo_redaccion = threading.Thread(
            target=self._trabajar_redaccion, name="redaccion-llm", daemon=True
        )
        self._hilo_redaccion.start()
        logger.info("Servicio de parte por voz iniciado")

    def detener(self) -> None:
        self._parar.set()
        self._cola.put(None)
        self._cola_asr.put(None)
        self._cola_redaccion.put(None)
        if self._hilo is not None:
            self._hilo.join(timeout=10)
        if self._hilo_asr is not None:
            self._hilo_asr.join(timeout=10)
        if self._hilo_redaccion is not None:
            self._hilo_redaccion.join(timeout=10)
        logger.info("Servicio de parte por voz detenido")

    def encolar_nota(self, nota: NotaRecorrida) -> None:
        self._cola.put(("nota", nota))

    def encolar_respuesta(self, respuesta: RespuestaOperador) -> None:
        self._cola.put(("respuesta", respuesta))

    def encolar_orden_por_evento(self, evento) -> None:
        """Encola la orden que abre un evento de sensor.

        Va por la cola y no por el callback de MQTT: el loop de MQTT es un
        solo hilo para todos los mensajes y no puede quedar esperando un
        análisis mientras la planta manda notas de voz.
        """
        self._cola.put(("orden", evento))

    def _trabajar(self) -> None:
        while not self._parar.is_set():
            item = self._cola.get()
            if item is None:
                break
            tipo, carga = item
            try:
                if tipo == "nota":
                    self._preparar_nota(carga)  # type: ignore[arg-type]
                elif tipo == "respuesta":
                    self.procesar_respuesta(carga)  # type: ignore[arg-type]
                elif tipo == "nota-lista":
                    nota, texto, fotos_locales = carga  # type: ignore[misc]
                    self._completar_nota(nota, texto, fotos_locales)  # type: ignore[arg-type]
                elif tipo == "orden":
                    self.generar_orden(carga)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001 - una nota mala no frena la cola
                logger.exception("Error procesando %s", tipo)

    # ------------------------------------------------------------- transcripción

    def _trabajar_asr(self) -> None:
        """Transcribe las notas en su propio hilo.

        La cola es FIFO y hay un solo hilo: el ASR no se paraliza y el orden de
        las notas se mantiene. Lo que se gana es que el hilo de la planta
        sigue atendiendo las respuestas del operador y los eventos del sensor
        mientras el audio se decodifica.
        """
        while not self._parar.is_set():
            nota = self._cola_asr.get()
            if nota is None:
                break
            try:
                texto, fotos_locales = self._transcribir(nota)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001 - una nota mala no frena la cola
                logger.exception("Falló la transcripción de %s", nota.id)  # type: ignore[attr-defined]
                texto, fotos_locales = "", []
            self._cola.put(("nota-lista", (nota, texto, fotos_locales)))

    # ------------------------------------------------------------------ redacción

    def _trabajar_redaccion(self) -> None:
        """Redacta la justificación de las órdenes ya publicadas.

        Va en su propio hilo: la orden ya salió con su estado y su motivo
        (decididos por las reglas, en código). La redacción es un agregado
        posterior y no puede frenar la cola de notas ni la publicación.
        """
        while not self._parar.is_set():
            item = self._cola_redaccion.get()
            if item is None:
                break
            analisis, orden = item
            try:
                self._redactar_orden(analisis, orden)
            except Exception:  # noqa: BLE001 - la redacción no frena la planta
                logger.exception("Falló la redacción de la orden %s", orden.id)

    def _redactar_orden(self, analisis, orden) -> None:
        if self._analisis is None:
            return
        texto = self._analisis.redactar(analisis)
        if not texto:
            return
        self._repo.guardar_redaccion_orden(
            orden.id,
            texto,
            modelo=self._analisis.nombre_modelo,
        )
        # Una orden que ya se ejecutó o se cerró no se vuelve a abrir: la
        # redacción llega después y no puede rebajar el estado.
        actual = self._repo.obtener_orden(orden.id)
        if actual is not None and str(actual.get("estado")) in ESTADOS_CERRADOS:
            logger.info(
                "Orden %s ya está en %s; no se vuelve a publicar",
                orden.id,
                actual.get("estado"),
            )
            return
        # La orden se vuelve a publicar con el mismo id y el mismo estado: la
        # planta no ve una orden nueva y el id sigue siendo idempotente.
        self._cliente.publicar_orden(orden)
        logger.info("Orden %s con justificación técnica redactada", orden.id)

    # ------------------------------------------------------------------ notas

    def _chequear_nota(self, nota: NotaRecorrida) -> bool:
        """¿La nota hay que procesarla? Devuelve False si ya está en curso.

        La misma nota puede volver a llegar por MQTT (o el sistema puede
        reiniciar): el id de la nota es el que manda y no se abre una segunda
        conversación para la misma nota.
        """
        with self._candado_notas:
            if nota.id in self._notas_en_curso:
                logger.info("Nota %s ya está en transcripción; se espera", nota.id)
                return False
        existente = self._repo.obtener_conversacion_por_nota(nota.id)
        if existente is not None:
            if existente["estado"] == "confirmada":
                logger.info("Nota %s ya confirmada; se ignora", nota.id)
                return False
            logger.info("Nota %s ya estaba en curso; se reenvía el último mensaje", nota.id)
            self._reenviar(existente)
            return False
        with self._candado_notas:
            self._notas_en_curso.add(nota.id)
        return True

    def _preparar_nota(self, nota: NotaRecorrida) -> None:
        """Mete la nota en la cola de transcripción."""
        if not self._chequear_nota(nota):
            return
        logger.info("Procesando nota %s de %s", nota.id, nota.operador)
        self._cola_asr.put(nota)

    def _transcribir(self, nota: NotaRecorrida) -> tuple[str, list[Path | None]]:
        """Descarga el audio y las fotos y transcribe la nota.

        Va en el hilo de transcripción: la descarga y la decodificación son
        las partes lentas y no pueden frenar al hilo de la planta.
        """
        audio = self._almacen.guardar_audio(nota.id, nota.audio_url)
        fotos_locales = self._almacen.guardar_fotos(nota.id, nota.fotos)

        texto = ""
        if audio is not None:
            try:
                texto = self._transcriptor.transcribir(audio)
            except Exception:  # noqa: BLE001 - se sigue con borrador vacío
                logger.exception("Falló la transcripción de %s", nota.id)

        if not texto and nota.audio_url:
            logger.warning("Nota %s sin transcripción; se muestra un borrador vacío", nota.id)
        return texto, fotos_locales

    def _completar_nota(
        self,
        nota: NotaRecorrida,
        texto: str,
        fotos_locales: list[Path | None],
    ) -> None:
        """Con la transcripción lista, arma el parte y lo publica."""
        with self._candado_notas:
            self._notas_en_curso.discard(nota.id)
        extraccion = self._extractor.extraer(texto, nota.fotos)
        nota_id = self._repo.guardar_nota(
            nota,
            transcripcion=texto,
            estado="extraida",
            seguridad=extraccion.seguridad,
        )
        if extraccion.seguridad:
            self._publicar_alerta_seguridad(nota_id, nota, extraccion, texto, fotos_locales)
            return
        self._iniciar_conversacion(nota_id, nota, extraccion, fotos_locales)

    def procesar_nota(self, nota: NotaRecorrida) -> None:
        """Procesa la nota de punta a punta, sin pasar por las colas.

        Es el camino síncrono: lo usan los tests y el arranque manual. El
        servicio usa las colas, que es lo que permite que la planta siga
        respondiendo mientras el audio se decodifica.
        """
        if not self._chequear_nota(nota):
            return
        logger.info("Procesando nota %s de %s", nota.id, nota.operador)
        texto, fotos_locales = self._transcribir(nota)
        self._completar_nota(nota, texto, fotos_locales)

    def _publicar_alerta_seguridad(
        self,
        nota_id,
        nota: NotaRecorrida,
        extraccion: Extraccion,
        evidencia_texto: str,
        fotos_locales: list[Path | None] | None = None,
    ) -> None:
        """Alerta de seguridad: evento, aviso en tablet y aviso por radio.

        El procedimiento sale del manual MBP-4 (capítulo 10) y el aviso por
        radio al supervisor va por el canal 1 (Anexo D). Los dos avisos no
        esperan respuesta: la alerta no espera aprobación.
        """
        tipo = extraccion.seguridad
        assert tipo is not None
        proc = seguridad.procedimiento(tipo)
        equipo = next((fila.tag for fila in extraccion.filas if fila.tag), None)
        evidencia = seguridad.evidencia(
            tipo,
            nota_id=nota.id,
            texto=evidencia_texto,
            ts=nota.ts,
            equipo=equipo,
        )
        evento_id = self._repo.registrar_alerta_seguridad(
            nota_id, nota.id, tipo, evidencia
        )
        evento_seguridad = EventoSistema(
            id=evento_id,
            ts=nota.ts,
            tipo=TipoEvento.alerta_seguridad,
            equipo=equipo,
            origen="parte",
            parte_id=None,
            nota_ids=[nota.id],
            resumen=(
                f"Alerta de seguridad {tipo} ({proc.titulo}): "
                f"{evidencia_texto[:240]}. Procedimiento: {proc.que_hacer}"
            ),
        )
        self._cliente.publicar_evento(
            evento_seguridad
        )
        # La alerta de seguridad abre también una orden: no se aprueba sola
        # (MBP-4 §10.2), la decide una persona.
        self.generar_orden(
            evento_seguridad,
            texto=evidencia_texto,
            seguridad=True,
        )

        # Aviso en la tablet del operador: qué hacer, sin esperar respuesta.
        # La foto adjunta, si el modelo de visión pudo describirla, va en el
        # texto: es información para quien revise, no una decisión.
        descripcion_foto = self._describir_foto(fotos_locales or [])
        fila = FilaParte(
            tag=equipo or "PLANTA",
            variable="condicion_insegura",
            observacion=evidencia_texto[:240],
        )
        mensaje = MensajeTablet(
            id=f"pia3-{evento_id}-alerta",
            ts=nota.ts,
            nota_ids=[nota.id],
            texto=seguridad.texto_aviso_tablet(tipo, equipo, descripcion_foto),
            pantalla=Pantalla(
                titulo=f"Alerta de seguridad {tipo} · {proc.titulo}",
                filas=[fila],
            ),
            espera_respuesta=False,
        )
        self._cliente.publicar_tablet(mensaje)

        # Aviso por radio al supervisor de turno (canal 1). El contrato no
        # tiene un tópico de radio: el aviso sale por la tablet, que es el
        # canal de avisos del sistema. No espera respuesta.
        radio = MensajeTablet(
            id=f"pia3-{evento_id}-radio",
            ts=nota.ts,
            nota_ids=[nota.id],
            texto=seguridad.texto_aviso_radio(tipo, equipo),
            pantalla=Pantalla(
                titulo=f"Radio canal {seguridad.RADIO_SUPERVISOR} · Supervisor de turno",
                filas=[fila],
            ),
            espera_respuesta=False,
        )
        if self._enlace_ok():
            self._cliente.publicar_tablet(radio)
        else:
            # El aviso por radio sale de la planta: sin radioenlace no se puede
            # enviar, pero tampoco se pierde. Queda en la cola y sale cuando
            # el enlace vuelve.
            encolar_aviso(self._repo, radio)
        logger.critical(
            "Alerta %s publicada para nota %s (equipo %s) con aviso por radio canal %s",
            tipo,
            nota.id,
            equipo,
            seguridad.RADIO_SUPERVISOR,
        )

    def _describir_foto(self, fotos_locales: list[Path | None]) -> str | None:
        """Lo que el modelo de visión ve en la foto, si lo puede describir.

        Es un **auxiliar**: la descripción va al aviso para que quien revise
        la foto sepa qué está mirando. **No decide nada**: si el modelo no
        está, no responde o su salida no valida, el aviso sale sin
        descripción y la foto sigue yendo a revisión humana. Nunca se
        interpreta un fallo como "no hay daño".
        """
        if self._vision is None or not self._vision.habilitado:
            return None
        for foto in fotos_locales:
            if foto is None:
                continue
            resultado = self._vision.describir(foto)
            logger.info(
                "Foto %s: %s (%s, %.1f s, %d tokens de entrada / %d de salida)",
                foto.name,
                resultado.estado,
                resultado.detalle,
                resultado.segundos,
                resultado.tokens_prompt,
                resultado.tokens_completion,
            )
            if resultado.utilizable:
                return resultado.descripcion
        return None

    def _iniciar_conversacion(
        self,
        nota_id,
        nota: NotaRecorrida,
        extraccion: Extraccion,
        fotos_locales: list[Path | None],
    ) -> None:
        datos = _datos_base(nota, extraccion.preguntas, fotos_locales)
        if extraccion.preguntas:
            self._enviar_pregunta(
                nota_id,
                nota,
                extraccion.filas,
                extraccion.preguntas,
                indice=0,
                intento=0,
                datos=datos,
            )
        else:
            self._enviar_borrador(
                nota_id,
                nota,
                extraccion.filas,
                intento=0,
                datos=datos,
            )

    def _enviar_pregunta(
        self,
        nota_id,
        nota: NotaRecorrida,
        filas: list[Hallazgo],
        preguntas: list[Pregunta],
        *,
        indice: int,
        intento: int,
        datos: dict[str, Any],
    ) -> None:
        pregunta = preguntas[indice]
        mensaje = construir_pregunta(nota.id, nota.ts, pregunta, intento)
        self._repo.guardar_pregunta(nota_id, mensaje, pregunta)
        self._repo.guardar_parte(
            nota_id,
            nota.ts,
            filas,
            fotos_url=nota.fotos,
            fotos_locales=datos.get("fotos_locales") or [],
            mensaje_id=mensaje.id,
            intento=intento,
        )
        datos = {k: v for k, v in datos.items() if not k.startswith("_")}
        datos["indice_pregunta"] = indice
        self._repo.guardar_conversacion(
            nota_id,
            mensaje,
            filas,
            intento=intento,
            estado="pregunta",
            datos=datos,
        )
        self._repo.actualizar_estado_nota(nota_id, "esperando_respuesta")
        self._cliente.publicar_tablet(mensaje)
        logger.info("Pregunta %s enviada para %s: %s", mensaje.id, nota.id, pregunta.texto)

    def _enviar_borrador(
        self,
        nota_id,
        nota: NotaRecorrida,
        filas: list[Hallazgo],
        *,
        intento: int,
        datos: dict[str, Any],
    ) -> None:
        mensaje = construir_borrador(nota.id, nota.ts, filas, intento)
        parte_id = self._repo.guardar_parte(
            nota_id,
            nota.ts,
            filas,
            fotos_url=nota.fotos,
            fotos_locales=datos.get("fotos_locales") or [],
            mensaje_id=mensaje.id,
            intento=intento,
        )
        datos = {k: v for k, v in datos.items() if not k.startswith("_")}
        self._repo.guardar_conversacion(
            nota_id,
            mensaje,
            filas,
            intento=intento,
            estado="borrador",
            datos=datos,
        )
        self._repo.actualizar_estado_nota(nota_id, "esperando_confirmacion")
        self._cliente.publicar_tablet(mensaje)
        logger.info(
            "Borrador %s enviado para %s con %s filas",
            mensaje.id,
            nota.id,
            len(mensaje.pantalla.filas if mensaje.pantalla else []),
        )

        if not _tiene_lecturas(filas):
            # Un parte sin lecturas no tiene nada que corregir: lo único que
            # podía estar abierto (el equipo) ya se preguntó y el operador
            # respondió. Por eso el evento del equipo se publica ahora y no
            # esperando una confirmación que no puede aportar información.
            self._publicar_eventos(
                nota_id,
                nota,
                filas,
                parte_id=parte_id,
                ts=nota.ts,
                fotos=len(datos.get("fotos_locales") or []),
            )

    def _reenviar(self, conversacion: dict[str, Any]) -> None:
        # El id es idempotente: si la planta ya lo recibió lo ignora; si no,
        # lo procesa. No se crea un mensaje nuevo sin actualizar la conversación.
        mensaje = MensajeTablet.model_validate(conversacion["mensaje_tablet"])
        self._cliente.publicar_tablet(mensaje)
        logger.info("Mensaje %s republicado", mensaje.id)

    # ------------------------------------------------------------------ respuestas

    def _publicar_eventos(
        self,
        nota_id,
        nota: NotaRecorrida,
        filas: list[Hallazgo],
        *,
        parte_id,
        ts,
        fotos: int,
    ) -> list[EventoSistema]:
        """Publica **un evento por equipo** del parte y lo deja registrado.

        Los eventos son idempotentes por id (``parte:<nota>:<equipo>``): si el
        mismo parte se publica dos veces no se duplica nada.
        """
        eventos = clasificar_partes(
            nota_id=nota.id,
            parte_id=str(parte_id),
            ts=ts,
            filas=filas,
            planta=self._planta,
            fotos=fotos,
        )
        fuentes = fuentes_tendencia_parte(filas, self._planta)
        for evento in eventos:
            fuente = fuentes.get(evento.equipo or "")
            if self._historiador is not None and fuente is not None:
                variable, unidad = fuente
                evento = self._historiador.enriquecer_evento(
                    evento, evento.equipo or "", variable, unidad=unidad
                )
            self._repo.registrar_evento(evento, nota_id=nota_id)
            self._cliente.publicar_evento(evento)
            if evento.tipo is TipoEvento.escalado:
                # Un escalado abre una orden de trabajo: el análisis del manual
                # dice qué falla es, qué tarea la resuelve y si la orden se
                # puede aprobar sola.
                self.generar_orden(evento, filas=filas)
        return eventos

    def generar_orden(
        self,
        evento: EventoSistema,
        *,
        filas: list[Hallazgo] | None = None,
        texto: str | None = None,
        seguridad: bool = False,
    ) -> None:
        """Analiza el escalado y publica la orden que sale de ese análisis.

        El análisis no puede tumbar la planta: si algo sale mal, el evento ya
        está publicado y queda registrado en la base.
        """
        if self._analisis is None:
            return
        try:
            if seguridad:
                analisis = self._analisis.analizar_alerta_seguridad(
                    evento, texto=texto or evento.resumen
                )
            else:
                # La evidencia es lo que motivó el escalado: las filas
                # anómalas del parte de ese equipo.
                anomalas = filas_anomalas(
                    filas or [], self._planta, evento.equipo or ""
                )
                analisis = self._analisis.analizar_evento(evento, filas=anomalas)
        except Exception:  # noqa: BLE001 - el análisis no frena la planta
            logger.exception("Falló el análisis del evento %s", evento.id)
            return
        if analisis is None:
            return
        orden = self._analisis.orden_sistema(analisis)
        try:
            self._repo.guardar_orden(orden, **self._analisis.datos_de_orden(analisis))
        except Exception:  # noqa: BLE001 - la orden se publica igual
            logger.exception("No se pudo guardar la orden %s", orden.id)
        self._cliente.publicar_orden(orden)
        logger.info(
            "Orden %s para %s: %s · %s · %s",
            orden.id,
            orden.equipo,
            ", ".join(orden.tareas),
            orden.estado.value,
            analisis.falla.codigo if analisis.falla else "sin falla identificada",
        )
        # La justificación técnica se redacta después, en su propio hilo: la
        # orden ya está publicada y su estado no depende del modelo.
        self._cola_redaccion.put((analisis, orden))

    def procesar_respuesta(self, respuesta: RespuestaOperador) -> None:
        conversacion = self._repo.obtener_conversacion(respuesta.mensaje_id)
        if conversacion is None:
            logger.info("Respuesta %s sin conversación vigente; se ignora", respuesta.mensaje_id)
            return

        estado = conversacion["estado"]
        if estado == "confirmada":
            logger.info("Respuesta %s sobre nota ya confirmada", respuesta.mensaje_id)
            return

        filas = filas_desde_json(conversacion.get("borrador") or [])
        datos = conversacion.get("datos") or {}
        nota = _nota_desde_datos(datos)
        preguntas = _preguntas_desde_json(datos.get("preguntas") or [])
        intento = int(conversacion.get("intento") or 0)

        if estado == "pregunta":
            self._procesar_respuesta_pregunta(
                conversacion,
                respuesta,
                filas,
                datos,
                nota,
                preguntas,
                intento,
            )
            return

        if estado == "borrador":
            self._procesar_respuesta_borrador(
                conversacion,
                respuesta,
                filas,
                datos,
                nota,
                preguntas,
                intento,
            )

    def _procesar_respuesta_pregunta(
        self,
        conversacion: dict[str, Any],
        respuesta: RespuestaOperador,
        filas: list[Hallazgo],
        datos: dict[str, Any],
        nota: NotaRecorrida,
        preguntas: list[Pregunta],
        intento: int,
    ) -> None:
        indice = int(datos.get("indice_pregunta") or 0)
        if not preguntas:
            self._enviar_borrador(
                conversacion["nota_id"],
                nota,
                filas,
                intento=intento + 1,
                datos=datos,
            )
            return

        pregunta = preguntas[min(indice, len(preguntas) - 1)]
        interpretacion = interpretar(respuesta.texto, self._resolver, es_pregunta=True)
        self._repo.responder_pregunta(respuesta.mensaje_id, respuesta.texto)

        if pregunta.campo == "equipo":
            tag = interpretacion.tag_elegido
            if tag and tag in pregunta.tags:
                resolver_pendientes(filas, pregunta, tag, self._planta)
            else:
                logger.warning(
                    "No se entendió el equipo en la respuesta a %s: %r",
                    respuesta.mensaje_id,
                    respuesta.texto,
                )

        siguiente = indice + 1
        datos = dict(datos)
        datos["indice_pregunta"] = siguiente
        if siguiente < len(preguntas):
            self._enviar_pregunta(
                conversacion["nota_id"],
                nota,
                filas,
                preguntas,
                indice=siguiente,
                intento=intento + 1,
                datos=datos,
            )
        else:
            self._enviar_borrador(
                conversacion["nota_id"],
                nota,
                filas,
                intento=intento + 1,
                datos=datos,
            )

    def _procesar_respuesta_borrador(
        self,
        conversacion: dict[str, Any],
        respuesta: RespuestaOperador,
        filas: list[Hallazgo],
        datos: dict[str, Any],
        nota: NotaRecorrida,
        preguntas: list[Pregunta],
        intento: int,
    ) -> None:
        interpretacion = interpretar(respuesta.texto, self._resolver, planta=self._planta)
        if interpretacion.confirmado:
            parte_id = self._repo.confirmar_parte(conversacion["nota_id"], respuesta.ts)
            fotos = len(datos.get("fotos_locales") or [])
            eventos = self._publicar_eventos(
                conversacion["nota_id"],
                nota,
                filas,
                parte_id=parte_id,
                ts=respuesta.ts,
                fotos=fotos,
            )
            self._repo.marcar_conversacion(conversacion["nota_id"], "confirmada")
            logger.info(
                "Parte de %s confirmado; %s evento(s) publicado(s): %s",
                nota.id,
                len(eventos),
                ", ".join(f"{e.equipo or '-'}:{e.tipo.value}" for e in eventos),
            )
            return

        if interpretacion.correcciones:
            aplicadas = int(datos.get("correcciones") or 0)
            if aplicadas >= self._settings.parte_max_correcciones:
                # La nota no converge: se deja el último borrador para que lo
                # revise una persona. Seguir mandando borradores consume
                # tiempo de planta sin chances de cerrar la nota.
                logger.warning(
                    "La nota %s llega al tope de %s correcciones; el borrador "
                    "queda para revisión humana",
                    nota.id,
                    self._settings.parte_max_correcciones,
                )
                mensaje = construir_borrador(nota.id, nota.ts, filas, intento)
                datos = dict(datos)
                datos["revision_humana"] = True
                self._repo.guardar_conversacion(
                    conversacion["nota_id"],
                    mensaje,
                    filas,
                    intento=intento,
                    estado="revision_humana",
                    datos=datos,
                )
                return
            afirmados = {
                (str(item[0]), str(item[1]))
                for item in (datos.get("afirmados") or [])
                if isinstance(item, (list, tuple)) and len(item) == 2
            }
            tocadas = aplicar_correcciones(
                filas,
                interpretacion.correcciones,
                self._planta,
                self._resolver,
                afirmados=afirmados,
            )
            datos = dict(datos)
            datos["correcciones"] = aplicadas + 1
            datos["afirmados"] = sorted([list(item) for item in (afirmados | set(tocadas))])
            self._enviar_borrador(
                conversacion["nota_id"],
                nota,
                filas,
                intento=intento + 1,
                datos=datos,
            )
            return

        if interpretacion.texto:
            logger.warning(
                "No se entendió la respuesta a %s: %r",
                respuesta.mensaje_id,
                respuesta.texto,
            )
        reintentos = int(datos.get("reintentos_sin_entender") or 0)
        if reintentos < self._settings.parte_max_intentos:
            datos = dict(datos)
            datos["reintentos_sin_entender"] = reintentos + 1
            self._enviar_borrador(
                conversacion["nota_id"],
                nota,
                filas,
                intento=intento + 1,
                datos=datos,
            )


# ---------------------------------------------------------------------- helpers


def _tiene_lecturas(filas: list[Hallazgo]) -> bool:
    """¿El parte tiene alguna medición (no solo observaciones)?"""
    return any(fila.valor is not None for fila in filas)


def _datos_base(
    nota: NotaRecorrida,
    preguntas: list[Pregunta],
    fotos_locales: list[Path | None],
) -> dict[str, Any]:
    return {
        "nota": nota.model_dump(mode="json"),
        "preguntas": [asdict(pregunta) for pregunta in preguntas],
        "indice_pregunta": 0,
        "reintentos_sin_entender": 0,
        "correcciones": 0,
        "afirmados": [],
        "fotos_locales": [str(p) if p else None for p in fotos_locales],
    }


def _nota_desde_datos(datos: dict[str, Any]) -> NotaRecorrida:
    return NotaRecorrida.model_validate(datos["nota"])


def _preguntas_desde_json(datos: list[dict[str, Any]]) -> list[Pregunta]:
    return [
        Pregunta(
            campo=item.get("campo") or "equipo",
            texto=item.get("texto") or "",
            opciones=list(item.get("opciones") or []),
            filas=list(item.get("filas") or []),
            variable=item.get("variable"),
            valor_dicho=item.get("valor_dicho"),
            tags=list(item.get("tags") or []),
        )
        for item in datos
    ]
