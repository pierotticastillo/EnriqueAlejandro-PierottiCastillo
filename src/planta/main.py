"""Arranque del sistema PIA-3: latido + parte por voz + análisis, órdenes y cierre."""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from typing import Any

import httpx
from planta_contrato import LecturaSensor, OrdenSistema, Repuesto
from sqlalchemy.engine import Engine

from analisis.catalogo import CatalogoManuales
from analisis.estado import EstadoBombas
from analisis.llm import crear_modelo
from analisis.servicio import RecuperadorDiferido, ServicioAnalisis
from analisis.vision import crear_cliente_vision
from config import Settings
from db.repositorio import Repositorio
from db.session import crear_engine
from deteccion.sensores import DetectorSensores
from modelos_lista import LISTA_CERRADA
from planta.almacenamiento import Almacenamiento
from planta.cierre import ServicioCierre
from planta.cola import DrenadorCola
from planta.heartbeat import Heartbeat, comprobar_enlace
from planta.heartbeat import crear_cliente as crear_cliente_latido
from planta.historial import Historiador
from planta.mqtt_listener import ClientePlanta
from planta.servicio import ServicioParteVoz
from planta.transcriber import crear_transcriptor
from rag.corpus import CorpusPlanta
from tools.cliente import ClientePlanta as ClienteApi
from tools.manuales import CorpusManuales
from tools.precios import ListaPrecios, precios_contrato
from verificacion_modelos import verificar as verificar_modelos

logger = logging.getLogger("planta.main")


def cargar_planta(settings: Settings, intentos: int = 20, espera_s: float = 2.0):
    """Espera a que la API de la planta esté disponible y devuelve su catálogo."""
    url = f"{settings.planta_api_url.rstrip('/')}/api/planta"
    for intento in range(1, intentos + 1):
        try:
            with httpx.Client(timeout=settings.planta_api_timeout_s, trust_env=True) as cliente:
                respuesta = cliente.get(url)
                respuesta.raise_for_status()
                datos = respuesta.json()
                logger.info(
                    "Catálogo de planta cargado: %s equipos, %s sensores",
                    len(datos.get("equipos") or []),
                    len(datos.get("sensores") or []),
                )
                return datos
        except httpx.HTTPError as exc:
            logger.warning("Planta no disponible (%s/%s): %s", intento, intentos, exc)
            time.sleep(espera_s)
    logger.error("No se pudo cargar /api/planta; se usan los alias predeterminados")
    return {}


def cargar_catalogo(cliente: ClienteApi, planta: dict[str, Any]) -> CatalogoManuales:
    """El catálogo de fallas, tareas y repuestos, leído de los manuales.

    Sale del mismo corpus que usa el RAG: por lo tanto cada falla, tarea y
    repuesto viene con su cita, sin ninguna tabla escrita a mano en el código.
    """
    documentos = CorpusPlanta(cliente).cargar(incluir_historial=False)
    equipos = [
        str(equipo.get("tag"))
        for equipo in planta.get("equipos") or []
        if isinstance(equipo, dict) and equipo.get("tag")
    ]
    catalogo = CatalogoManuales.desde_documentos(documentos, equipos=equipos)
    logger.info(
        "Catálogo de manuales cargado: %s fallas, %s tareas",
        len(catalogo.fallas),
        len(catalogo.tareas),
    )
    return catalogo


def _bajar_prioridad_cpu() -> int:
    """Baja la prioridad de CPU del hilo actual (solo en Unix).

    El índice del manual se arma en segundo plano: son 700 fragmentos que
    pasar por el embedder y puede tardar un minuto. Con la misma prioridad que
    la transcripción, el indexado le roba la CPU al ASR y cada nota se
    transcribe veinte veces más lento. Es un trabajo de fondo: la planta
    primero.
    """
    ajustar = getattr(os, "nice", None)
    if ajustar is None:  # pragma: no cover - Windows
        return 0
    try:
        return int(ajustar(10))
    except OSError:  # pragma: no cover - sin permisos para bajar prioridad
        return 0


def _cargar_recuperador(
    recuperador: RecuperadorDiferido,
    cliente: ClienteApi,
    settings: Settings,
    engine: Engine,
) -> None:
    """Arma el índice del manual en un hilo aparte.

    El arranque del sistema no espera al índice: si el RAG no se puede
    preparar, el análisis sigue con el cruce léxico del catálogo.
    """
    try:
        from rag.retriever import crear_recuperador

        prioridad = _bajar_prioridad_cpu()
        logger.info("Indexando el manual en segundo plano (prioridad CPU %s)", prioridad)
        recuperador.definir(
            crear_recuperador(cliente, settings=settings, engine=engine)
        )
        logger.info("Recuperador del manual listo")
    except Exception:  # noqa: BLE001 - el RAG no puede impedir el arranque
        logger.warning("No se pudo preparar el índice del manual", exc_info=True)


def _orden_recuperada(
    fila: dict[str, Any],
    catalogo: CatalogoManuales,
    estado_bombas: dict[str, str],
) -> OrdenSistema:
    """La orden pendiente que se vuelve a publicar después de un corte.

    Sale de la base: mismo id, mismo estado, mismas tareas y repuestos. Lo que
    no se guarda en la base (el ejecutor, si la orden baja la inyección) se
    vuelve a calcular con el catálogo y con el estado de las bombas.
    """
    equipo = str(fila.get("equipo") or "")
    tareas = [str(tarea) for tarea in (fila.get("tareas") or [])]
    ejecutor = "propio"
    for tarea in tareas:
        catalogo_tarea = catalogo.tarea_por_codigo(tarea, equipo)
        if catalogo_tarea is not None and (catalogo_tarea.ejecutor or "propio") != "propio":
            ejecutor = (catalogo_tarea.ejecutor or "propio").strip().lower()
    detiene = bool(fila.get("detencion"))
    reduce = False
    if detiene and estado_bombas.get(equipo) == "marcha":
        reduce = not any(
            tag != equipo and estado == "parada"
            for tag, estado in estado_bombas.items()
        )
    return OrdenSistema(
        id=str(fila.get("id")),
        ts=fila.get("creada_en") or fila.get("actualizada_en"),
        estado="PendienteAprobacion",
        equipo=equipo,
        evento_ids=[str(fila.get("evento_id"))] if fila.get("evento_id") else [],
        tareas=tareas,
        detiene_equipo=detiene,
        reduce_inyeccion=reduce,
        ejecutor=ejecutor,
        repuestos=[
            Repuesto(
                codigo=str(repuesto.get("codigo")),
                cantidad=float(repuesto.get("cantidad") or 0),
            )
            for repuesto in fila.get("repuestos") or []
        ],
        motivo=fila.get("motivo"),
        habilita=None,
        monto_habilita_usd=None,
    )


def _verificar_lista_cerrada(settings: Settings) -> None:
    """Deja escrito, al arrancar, qué modelos se están usando.

    La restricción 04 pide que los modelos sean los de la lista cerrada, con
    la versión y la configuración fijadas. Esto no puede tumbar el sistema:
    si algo no está, la planta sigue funcionando y el incumplimiento queda
    en el log.
    """
    try:
        resultados = verificar_modelos(settings)
    except Exception:  # noqa: BLE001 - la verificación no puede impedir el arranque
        logger.warning("No se pudo verificar la lista cerrada de modelos", exc_info=True)
        return
    fuera = [resultado for resultado in resultados if resultado.estado == "fuera-de-lista"]
    if fuera:
        logger.error(
            "Hay %s modelo(s) fuera de la lista cerrada: %s",
            len(fuera),
            ", ".join(f"{r.clave}={r.detalle}" for r in fuera),
        )
    logger.info(
        "Modelos en uso (lista cerrada de %s modelos):\n%s",
        len(LISTA_CERRADA),
        "\n".join(
            f"  {modelo.clave}: {modelo.nombre} ({modelo.motor})"
            for modelo in LISTA_CERRADA
        ),
    )


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    _verificar_lista_cerrada(settings)

    planta = cargar_planta(settings)

    engine: Engine = crear_engine(settings.database_url)
    repositorio = Repositorio(engine)
    historiador = Historiador(
        settings.planta_api_url,
        timeout_s=settings.historial_timeout_s,
    )
    almacenamiento = Almacenamiento(settings.datos_dir, settings.planta_api_url)
    # El vocabulario del ASR (los nombres de las tareas y de los repuestos de
    # la planta) se pide al transcribir: para entonces el catálogo ya está
    # cargado. Sin esto, el ASR escribe los nombres de la planta como los oye
    # ("Chofitrante") y el cierre no se puede comparar contra la orden.
    catalogo_ref: dict[str, CatalogoManuales] = {}
    transcriptor = crear_transcriptor(
        settings.asr_motor,
        settings.asr_modelo,
        descarga_local=settings.asr_local_only,
        directorio_descarga=settings.asr_download_root,
        hilos_cpu=settings.asr_hilos_cpu,
        vocabulario=lambda: (
            catalogo_ref["catalogo"].vocabulario_asr() if "catalogo" in catalogo_ref else ""
        ),
    )

    # ------------------------------------------------- análisis y órdenes (Fase 5)
    cliente_api = ClienteApi(
        settings.planta_api_url,
        timeout_s=settings.planta_api_timeout_s,
    )
    try:
        catalogo = cargar_catalogo(cliente_api, planta)
    except Exception:  # noqa: BLE001 - sin catálogo no hay análisis, pero la planta sigue
        logger.exception("No se pudo cargar el catálogo de manuales")
        catalogo = CatalogoManuales.desde_documentos([])
    catalogo_ref["catalogo"] = catalogo
    # La lista de precios del contrato (CT-SVP-2025 §2.1). El monto del
    # habilita sale de la orden y de esta lista, nunca de lo que declara el
    # cierre de la contratista (§3.2).
    try:
        precios = ListaPrecios(
            precios_contrato(cliente_api, corpus=CorpusManuales(cliente_api))
        )
    except Exception:  # noqa: BLE001 - sin precios no se certifica, pero la planta sigue
        logger.exception("No se pudo cargar la lista de precios del contrato")
        precios = ListaPrecios()
    logger.info("Lista de precios del contrato: %s ítem(s)", len(precios))
    estado_bombas = EstadoBombas(settings.planta_api_url)
    recuperador = RecuperadorDiferido()
    # El modelo de lenguaje local es un auxiliar de redacción. Si no está
    # activo o no responde, el sistema sigue con el texto determinista.
    modelo = crear_modelo(settings)
    analisis = ServicioAnalisis(
        catalogo,
        estado=estado_bombas,
        cliente=cliente_api,
        recuperador=recuperador,
        planta=planta,
        modelo=modelo,
    )
    if modelo is not None:
        logger.info("Modelo local de redacción activo: %s", modelo.nombre)
    # El modelo de nube con visión describe las fotos; no decide nada. Si no
    # hay clave, o el modelo no es el de la lista cerrada, no se llama a la nube.
    vision = crear_cliente_vision(settings)
    if vision is not None:
        logger.info("Modelo de visión activo: %s", vision.nombre)
    hilo_rag = threading.Thread(
        target=_cargar_recuperador,
        args=(recuperador, cliente_api, settings, engine),
        name="rag",
        daemon=True,
    )
    hilo_rag.start()

    referencia: dict[str, ServicioParteVoz] = {}
    cliente_ref: dict[str, ClientePlanta] = {}
    detector_sensores = DetectorSensores(planta)

    def al_nota(nota) -> None:
        referencia["servicio"].encolar_nota(nota)

    def al_respuesta(respuesta) -> None:
        referencia["servicio"].encolar_respuesta(respuesta)

    def al_sensor(lectura: LecturaSensor) -> None:
        evento = detector_sensores.evaluar(lectura)
        if evento is None:
            return
        evento = historiador.enriquecer_evento(
            evento,
            lectura.tag,
            lectura.variable,
            unidad=lectura.unidad,
        )
        if repositorio.registrar_evento_sensor(evento):
            cliente_ref["cliente"].publicar_evento(evento)
            logger.warning("Evento de sensor publicado: %s", evento.id)
            # Una alarma de sensor también abre una orden: el manual dice qué
            # hacer con esa desviación. La orden se arma en la cola del
            # servicio, no en el callback: el loop de MQTT es uno solo y no
            # puede quedarse esperando un análisis mientras la planta le
            # manda notas de voz.
            referencia["servicio"].encolar_orden_por_evento(evento)

    def al_cierre(nota) -> None:
        """Cierre de orden por voz (Fase 7).

        Va por la cola del servicio de cierre: el loop de MQTT es uno solo y
        no puede quedarse transcribiendo el audio mientras la planta manda
        notas de voz.
        """
        referencia["cierre"].encolar_cierre(nota)

    def al_conectar() -> None:
        """Al conectar (o al volver de un corte) se recupera lo pendiente.

        Las órdenes que estaban esperando aprobación se vuelven a publicar con
        el mismo id y el mismo estado: la planta no las pierde y no se abre
        una orden nueva. Y sale lo que quedó en la cola de salida.
        """
        try:
            pendientes = repositorio.ordenes_pendientes()
        except Exception:  # noqa: BLE001 - recuperar no puede tumbar el sistema
            logger.exception("No se pudieron leer las órdenes pendientes")
            return
        for fila in pendientes:
            orden = _orden_recuperada(fila, catalogo, estado_bombas.de_bombas())
            cliente_ref["cliente"].publicar_orden(orden)
            logger.info("Orden pendiente recuperada: %s", orden.id)
        # La cola de salida se vacía con el bus ya conectado.
        drenador = referencia.get("drenador")
        if drenador is not None:
            enviados = drenador.drenar()
            if enviados:
                logger.info("Cola de salida vaciada al conectar: %s mensajes", enviados)

    cliente = ClientePlanta(
        settings,
        al_nota=al_nota,
        al_respuesta=al_respuesta,
        al_sensor=al_sensor,
        al_cierre=al_cierre,
        al_conectar=al_conectar,
        cola=repositorio,
    )
    cliente_ref["cliente"] = cliente
    # El drenador publica por el mismo cliente: lo que quedó en la cola sale
    # por el bus, en orden, sin duplicarse.
    drenador = DrenadorCola(
        repositorio,
        cliente,
        enlace_ok_fn=lambda: comprobar_enlace(settings),
    )
    referencia["drenador"] = drenador
    servicio = ServicioParteVoz(
        settings,
        repositorio,
        almacenamiento,
        transcriptor,
        cliente,
        planta,
        historiador=historiador,
        analisis=analisis,
        vision=vision,
    )
    referencia["servicio"] = servicio
    # Cierre de órdenes por voz (Fase 7): el técnico cierra la orden hablando
    # y el sistema decide, contra la orden, si queda cerrada, observada o
    # certificada.
    servicio_cierre = ServicioCierre(
        repositorio,
        almacenamiento,
        transcriptor,
        cliente,
        catalogo,
        precios,
    )
    referencia["cierre"] = servicio_cierre

    parar = threading.Event()

    def _detener(_signum: int, _frame: object) -> None:
        parar.set()

    for senal in (signal.SIGTERM, signal.SIGINT):
        signal.signal(senal, _detener)

    latido = Heartbeat(
        settings,
        engine=engine,
        mqtt_client=crear_cliente_latido(settings),
        # Con el enlace de vuelta salen los mensajes que quedaron en la cola
        # (por ejemplo, el aviso por radio al supervisor).
        vaciar_cola_fn=drenador.drenar,
    )
    hilo_latido = threading.Thread(
        target=latido.ejecutar,
        kwargs={"parar": parar},
        name="latido",
        daemon=True,
    )

    def _viciar_cola() -> None:
        """Vacia la cola de salida mientras el sistema esté vivo.

        Es el camino de salida de **todo** lo que la planta publica: eventos,
        órdenes y avisos. El latido también la vacía cuando vuelve el enlace,
        pero el enlace y el bus son dos cosas distintas: un evento no tiene
        que esperar a que vuelva el radioenlace.
        """
        while not parar.is_set():
            try:
                drenador.drenar()
            except Exception:  # noqa: BLE001 - la cola no puede tumbar el sistema
                logger.exception("Falló el drenaje de la cola de salida")
            parar.wait(settings.cola_sondeo_s)

    hilo_cola = threading.Thread(
        target=_viciar_cola,
        name="cola-salida",
        daemon=True,
    )

    hilo_latido.start()
    hilo_cola.start()
    servicio.iniciar()
    servicio_cierre.iniciar()
    cliente.iniciar()

    logger.info("PIA-3 en marcha")
    parar.wait()

    logger.info("Deteniendo PIA-3")
    servicio.detener()
    servicio_cierre.detener()
    cliente.detener()
    historiador.cerrar()
    estado_bombas.cerrar()
    cliente_api.cerrar()
    hilo_latido.join(timeout=10)
    hilo_cola.join(timeout=10)
    engine.dispose()


if __name__ == "__main__":
    main()
