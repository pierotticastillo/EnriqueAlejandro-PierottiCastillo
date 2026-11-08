"""El cierre de órdenes por voz: de la nota de cierre al estado de la orden.

La planta publica en ``planta/cierres`` una ``NotaCierre``: el técnico cerró
la orden y el audio con lo que dijo. El sistema baja el audio, lo transcribe
**local** (el mismo ASR de la planta, sin nube), entiende el texto y lo
compara contra la orden aprobada.

Con esa comparación decide, en código, el estado de la orden:

* ``Ejecutada``: el trabajo se hizo. Se publica siempre.
* ``Cerrada``: trabajo propio, con las diferencias a la vista.
* ``Observada``: contratista con diferencias; vuelve a la contratista con el
  motivo (CT-SVP-2025 §3.3).
* ``Certificada``: contratista sin diferencias; se emite el habilita con el
  monto que sale de la orden y de la lista de precios (§3.2).

El texto del técnico **no decide**: sólo se extraen datos de él. Las reglas
están en código, igual que las de aprobación.
"""

from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime
from typing import Any

from planta_contrato import NotaCierre, OrdenSistema, Repuesto

from analisis.catalogo import CatalogoManuales
from analisis.cierre import comparar_cierre, decidir_cierre, monto_de_la_orden, parsear_cierre
from db.repositorio import Repositorio
from planta.almacenamiento import Almacenamiento
from planta.mqtt_listener import ClientePlanta
from planta.transcriber import Transcriber
from tools.precios import ListaPrecios

logger = logging.getLogger("planta.cierre")

# El que certifica es el sistema aplicando las reglas del contrato. No es el
# texto del técnico: el texto se usa para extraer datos, no para decidir.
INSPECTOR = "PIA-3 (control de cierre)"

# El personal propio no factura: su cierre lo cierra el supervisor, sin
# habilita. El habilita es para el trabajo de la contratista (§3.4).
EJECUTOR_PROPIO = "propio"

# El estado con el que se arma la orden para comparar el cierre: el que tiene
# en la base (Aprobada, Pendiente de aprobación...).
ESTADO_INICIAL = "Aprobada"


class ServicioCierre:
    """Procesa las notas de cierre y decide el estado de la orden."""

    def __init__(
        self,
        repositorio: Repositorio,
        almacenamiento: Almacenamiento,
        transcriptor: Transcriber,
        cliente: ClientePlanta,
        catalogo: CatalogoManuales,
        precios: ListaPrecios,
    ) -> None:
        self._repo = repositorio
        self._almacen = almacenamiento
        self._transcriptor = transcriptor
        self._cliente = cliente
        self._catalogo = catalogo
        self._precios = precios
        self._cola: queue.Queue[NotaCierre | None] = queue.Queue()
        self._hilo: threading.Thread | None = None
        self._parar = threading.Event()

    # ------------------------------------------------------------------ ciclo

    def iniciar(self) -> None:
        self._hilo = threading.Thread(target=self._trabajar, name="cierre-orden", daemon=True)
        self._hilo.start()
        logger.info("Servicio de cierre de órdenes iniciado")

    def detener(self) -> None:
        self._parar.set()
        self._cola.put(None)
        if self._hilo is not None:
            self._hilo.join(timeout=10)
        logger.info("Servicio de cierre de órdenes detenido")

    def encolar_cierre(self, nota: NotaCierre) -> None:
        """Encola el cierre.

        Va por la cola y no por el callback de MQTT: el loop de MQTT es un solo
        hilo y no puede quedar esperando una transcripción mientras la planta
        le manda notas de voz.
        """
        self._cola.put(nota)

    def _trabajar(self) -> None:
        while not self._parar.is_set():
            nota = self._cola.get()
            if nota is None:
                break
            try:
                self.procesar_cierre(nota)
            except Exception:  # noqa: BLE001 - un cierre malo no frena la planta
                logger.exception("Error procesando el cierre %s", nota.id)

    # -------------------------------------------------------------- procesamiento

    def procesar_cierre(self, nota: NotaCierre) -> None:
        """El cierre, de punta a punta: transcribe, compara, decide y publica.

        La orden pasa a ``Ejecutada`` apenas llega el cierre y después se
        publica el estado que corresponde al control (Cerrada, Observada o
        Certificada).

        Es idempotente: la misma nota de cierre que vuelve a llegar no se
        procesa dos veces. Lo que sí hace es republicar el estado final de la
        orden, para que la planta quede al día aunque el sistema se haya
        reiniciado entre guardar y publicar.
        """
        if self._repo.obtener_cierre(nota.id) is not None:
            logger.info("El cierre %s ya estaba registrado; se republica el estado", nota.id)
            self._republicar(nota.orden_id)
            return

        fila = self._repo.obtener_orden_completa(nota.orden_id)
        if fila is None:
            logger.warning(
                "Cierre %s de la orden %s, que el sistema no conoce; se ignora",
                nota.id,
                nota.orden_id,
            )
            return

        orden = _orden_desde_fila(fila, ESTADO_INICIAL)

        # La orden pasa a **Ejecutada** en el momento en que llega el cierre:
        # el técnico dijo que terminó. Se publica antes de transcribir para
        # que la planta no quede esperando: la transcripción local puede
        # tardar, y que el trabajo se hizo no depende de ella.
        ejecutada = _orden_desde_fila(fila, "Ejecutada", motivo=None)
        self._cliente.publicar_orden(ejecutada)

        texto = self._transcribir(nota)
        cierre = parsear_cierre(texto, orden, self._catalogo)
        comparacion = comparar_cierre(orden, cierre, self._catalogo)
        decision = decidir_cierre(orden, comparacion, self._precios)

        monto = None
        if decision.estado == "Certificada":
            monto, detalle = monto_de_la_orden(orden, self._precios, horas=cierre.horas)
            logger.info(
                "Monto del habilita de %s: USD %s (%s)",
                orden.id,
                f"{monto:,.2f}",
                "; ".join(detalle) or "sin ítem de contrato",
            )

        resultado = self._repo.registrar_cierre(
            cierre_id=nota.id,
            orden_id=orden.id,
            estado=decision.estado,
            anio=orden.ts.year,
            tecnico=nota.tecnico,
            empresa=nota.empresa,
            transcripcion=texto,
            tareas_hechas=list(cierre.tareas),
            repuestos_usados=[
                {
                    "codigo": repuesto.codigo,
                    "cantidad": repuesto.cantidad,
                    "nombre": repuesto.nombre,
                }
                for repuesto in cierre.repuestos
            ],
            horas=cierre.horas,
            prueba_final=cierre.prueba_final,
            diferencias=[
                {
                    "tipo": diferencia.tipo,
                    "detalle": diferencia.detalle,
                    "sustantiva": diferencia.sustantiva,
                }
                for diferencia in comparacion.diferencias
            ],
            inspector=INSPECTOR,
            decision=_decision_de_certificacion(decision.estado),
            motivo=decision.motivo,
            monto_usd=monto,
        )

        # La orden se publica dos veces con el mismo id: primero como ejecutada
        # (el trabajo se hizo) y después con el estado que corresponde al
        # cierre. La planta ve la transición; el id sigue siendo idempotente.
        final = _orden_desde_fila(
            fila,
            decision.estado,
            motivo=decision.motivo,
            habilita=resultado.get("habilita"),
            monto=resultado.get("monto_habilita_usd"),
        )
        self._cliente.publicar_orden(final)
        logger.info(
            "Cierre %s de la orden %s: %s%s",
            nota.id,
            orden.id,
            decision.estado,
            f" · {decision.motivo}" if decision.motivo else "",
        )

    def _transcribir(self, nota: NotaCierre) -> str:
        """Baja el audio del cierre y lo transcribe localmente."""
        audio = self._almacen.guardar_audio(nota.id, nota.audio_url)
        if audio is None:
            logger.warning("El cierre %s no tiene audio; se cierra sin transcripción", nota.id)
            return ""
        try:
            return self._transcriptor.transcribir(audio)
        except Exception:  # noqa: BLE001 - se sigue con el cierre sin texto
            logger.exception("Falló la transcripción del cierre %s", nota.id)
            return ""

    def _republicar(self, orden_id: str) -> None:
        """Republica la orden con el estado que tiene en la base."""
        fila = self._repo.obtener_orden_completa(orden_id)
        if fila is None:
            return
        habilitacion = self._repo.obtener_habilitacion(orden_id)
        orden = _orden_desde_fila(
            fila,
            str(fila.get("estado") or "Ejecutada"),
            motivo=fila.get("motivo"),
            habilita=str(habilitacion["numero"]) if habilitacion else None,
            monto=float(habilitacion["monto"]) if habilitacion else None,
        )
        self._cliente.publicar_orden(orden)


# --------------------------------------------------------------------- helpers


def _decision_de_certificacion(estado: str) -> str | None:
    """La decisión que queda en la bitácora de certificación.

    El trabajo propio no se certifica: lo cierra el supervisor. La
    contratista se certifica u se observa (CT-SVP-2025 §3.3).
    """
    if estado == "Certificada":
        return "certifica"
    if estado == "Observada":
        return "observa"
    return None


def _orden_desde_fila(
    fila: dict[str, Any],
    estado: str,
    *,
    motivo: str | None = None,
    habilita: str | None = None,
    monto: float | None = None,
) -> OrdenSistema:
    """La orden tal como se publicó, con el estado nuevo.

    Sale de la base: mismo id, mismo equipo, mismas tareas y mismos repuestos.
    """
    return OrdenSistema(
        id=str(fila["id"]),
        ts=fila.get("creada_en") or datetime.now().astimezone(),
        estado=estado,
        equipo=str(fila.get("equipo") or ""),
        evento_ids=[str(fila["evento_id"])] if fila.get("evento_id") else [],
        tareas=[str(tarea) for tarea in (fila.get("tareas") or [])],
        detiene_equipo=bool(fila.get("detencion")),
        reduce_inyeccion=bool(fila.get("reduce_inyeccion")),
        ejecutor=str(fila.get("ejecutor") or EJECUTOR_PROPIO),
        repuestos=[
            Repuesto(
                codigo=str(repuesto.get("codigo")),
                cantidad=float(repuesto.get("cantidad") or 0),
            )
            for repuesto in (fila.get("repuestos") or [])
        ],
        motivo=motivo,
        habilita=habilita,
        monto_habilita_usd=monto,
    )
