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

from config import Settings
from db.repositorio import Repositorio, filas_desde_json
from deteccion import seguridad
from deteccion.partes import clasificar_partes, fuentes_tendencia_parte
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
    ) -> None:
        self._settings = settings
        self._repo = repositorio
        self._almacen = almacenamiento
        self._transcriptor = transcriptor
        self._cliente = cliente
        self._planta = planta
        self._historiador = historiador
        self._resolver = resolver or desde_api(planta)
        self._extractor = extractor or ExtractorParte(planta, self._resolver)
        self._enlace_ok = enlace_ok_fn or (lambda: comprobar_enlace(settings))
        self._cola: queue.Queue[tuple[str, object] | None] = queue.Queue()
        self._hilo: threading.Thread | None = None
        self._parar = threading.Event()

    # ------------------------------------------------------------------ ciclo

    def iniciar(self) -> None:
        self._hilo = threading.Thread(target=self._trabajar, name="parte-voz", daemon=True)
        self._hilo.start()
        logger.info("Servicio de parte por voz iniciado")

    def detener(self) -> None:
        self._parar.set()
        self._cola.put(None)
        if self._hilo is not None:
            self._hilo.join(timeout=10)
        logger.info("Servicio de parte por voz detenido")

    def encolar_nota(self, nota: NotaRecorrida) -> None:
        self._cola.put(("nota", nota))

    def encolar_respuesta(self, respuesta: RespuestaOperador) -> None:
        self._cola.put(("respuesta", respuesta))

    def _trabajar(self) -> None:
        while not self._parar.is_set():
            item = self._cola.get()
            if item is None:
                break
            tipo, carga = item
            try:
                if tipo == "nota":
                    self.procesar_nota(carga)  # type: ignore[arg-type]
                elif tipo == "respuesta":
                    self.procesar_respuesta(carga)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001 - una nota mala no frena la cola
                logger.exception("Error procesando %s", tipo)

    # ------------------------------------------------------------------ notas

    def procesar_nota(self, nota: NotaRecorrida) -> None:
        existente = self._repo.obtener_conversacion_por_nota(nota.id)
        if existente is not None:
            if existente["estado"] == "confirmada":
                logger.info("Nota %s ya confirmada; se ignora", nota.id)
                return
            logger.info("Nota %s ya estaba en curso; se reenvía el último mensaje", nota.id)
            self._reenviar(existente)
            return

        logger.info("Procesando nota %s de %s", nota.id, nota.operador)
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

        extraccion = self._extractor.extraer(texto, nota.fotos)
        nota_id = self._repo.guardar_nota(
            nota,
            transcripcion=texto,
            estado="extraida",
            seguridad=extraccion.seguridad,
        )
        if extraccion.seguridad:
            self._publicar_alerta_seguridad(nota_id, nota, extraccion, texto)
            return
        self._iniciar_conversacion(nota_id, nota, extraccion, fotos_locales)

    def _publicar_alerta_seguridad(
        self,
        nota_id,
        nota: NotaRecorrida,
        extraccion: Extraccion,
        evidencia_texto: str,
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
        self._cliente.publicar_evento(
            EventoSistema(
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
        )

        # Aviso en la tablet del operador: qué hacer, sin esperar respuesta.
        fila = FilaParte(
            tag=equipo or "PLANTA",
            variable="condicion_insegura",
            observacion=evidencia_texto[:240],
        )
        mensaje = MensajeTablet(
            id=f"pia3-{evento_id}-alerta",
            ts=nota.ts,
            nota_ids=[nota.id],
            texto=seguridad.texto_aviso_tablet(tipo, equipo),
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
        return eventos

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
        interpretacion = interpretar(respuesta.texto, self._resolver)
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
            aplicar_correcciones(filas, interpretacion.correcciones, self._planta, self._resolver)
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
