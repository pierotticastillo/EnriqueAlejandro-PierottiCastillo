"""El cierre de órdenes por voz: de la nota de cierre al estado de la orden.

La planta publica en ``planta/cierres`` una ``NotaCierre``: el técnico cerró
la orden y el audio con lo que dijo. El sistema baja el audio, lo transcribe
**local** (el mismo ASR de la planta, sin nube), entiende el texto y lo
compara contra la orden aprobada.

La comparación produce evidencia técnica, pero el estado final lo decide una
persona autenticada:

* ``Ejecutada``: el cierre técnico se registró y la orden espera revisión.
* ``Cerrada``: el ``supervisor`` cerró trabajo propio.
* ``Observada``: el ``inspector`` observó trabajo de contratista; vuelve con
  el motivo y sin habilita (CT-SVP-2025 §3.3).
* ``Certificada``: el ``inspector`` certificó trabajo de contratista; sólo
  entonces se emite el habilita con el monto de la orden y de la lista de
  precios (§3.2).

El texto del técnico **no decide**: sólo se extraen datos de él. Las reglas
de transición siguen en código y la identidad y el rol salen del token.
"""

from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime
from typing import Any

from planta_contrato import NotaCierre, OrdenSistema, Repuesto

from analisis.catalogo import CatalogoManuales
from analisis.cierre import (
    MontoContractualInvalido,
    comparar_cierre,
    monto_de_la_orden,
    parsear_cierre,
    validar_decision_cierre,
)
from analisis.provenance import (
    NO_DISPONIBLE,
    hash_sha256_archivo,
    identificador_foto,
)
from db.repositorio import AutorizacionInvalida, ConflictoEstado, Repositorio
from planta.almacenamiento import Almacenamiento
from planta.mqtt_listener import ClientePlanta
from planta.transcriber import Transcriber
from tools.precios import ListaPrecios

logger = logging.getLogger("planta.cierre")

# El personal propio no factura: su cierre lo cierra el supervisor, sin
# habilita. El habilita es para el trabajo de la contratista (§3.4).
EJECUTOR_PROPIO = "propio"

# El estado con el que se arma la orden para comparar el cierre: el que tiene
# en la base (Aprobada, Pendiente de aprobación...).
ESTADO_INICIAL = "Aprobada"

_ESTADO_CONTRACTO = {
    "Borrador": "Borrador",
    "Pendiente de aprobacion": "PendienteAprobacion",
    "Aprobada": "Aprobada",
    "Rechazada": "Rechazada",
    "Ejecutada": "Ejecutada",
    "Observada": "Observada",
    "Cerrada": "Cerrada",
    "Certificada": "Certificada",
}


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
        """Prepara el cierre técnico y deja la orden esperando decisión humana.

        El texto del técnico se transcribe y se compara contra la orden. La
        comparación produce evidencia y diferencias, pero **no decide el
        estado final**: la orden queda ``Ejecutada`` hasta que un supervisor o
        un inspector revisen el cierre en la Central.

        Es idempotente: la misma nota de cierre que vuelve a llegar no se
        procesa dos veces. Lo que sí hace es republicar el estado que tiene en
        la base, para que la planta quede al día aunque el sistema se haya
        reiniciado entre guardar y publicar.
        """
        existente = self._repo.obtener_cierre(nota.id)
        if existente is not None:
            if str(existente.get("orden_id")) != str(nota.orden_id):
                logger.error(
                    "El cierre %s pertenece a %s, no a %s; no se republica",
                    nota.id,
                    existente.get("orden_id"),
                    nota.orden_id,
                )
                return
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
        texto = self._transcribir(nota)
        cierre = parsear_cierre(texto, orden, self._catalogo)
        comparacion = comparar_cierre(orden, cierre, self._catalogo)
        motivo = comparacion.resumen or None
        fotos = self._fotos_de_cierre(nota)

        resultado = self._repo.registrar_cierre(
            cierre_id=nota.id,
            orden_id=orden.id,
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
            fotos=fotos,
            motivo=motivo,
        )
        if not resultado.get("nuevo"):
            # Una carrera puede haber registrado el cierre entre el precheck y
            # la escritura. Se publica el estado actual, nunca el "Ejecutada"
            # calculado desde una lectura anterior.
            self._republicar(nota.orden_id)
            return

        actual = self._repo.obtener_orden_completa(orden.id)
        if actual is None:
            return
        ejecutada = _orden_desde_fila(actual, "Ejecutada", motivo=actual.get("motivo"))
        self._cliente.publicar_orden(ejecutada)
        rol = "supervisor" if (orden.ejecutor or "propio").lower() == "propio" else "inspector"
        logger.info(
            "Cierre %s de la orden %s preparado; espera decisión de %s%s",
            nota.id,
            orden.id,
            rol,
            f" · {motivo}" if motivo else "",
        )

    def decidir_cierre(
        self,
        orden_id: str,
        decision: str,
        *,
        actor: str,
        rol: str,
        motivo: str | None = None,
    ) -> dict[str, Any]:
        """Aplica la decisión humana de G2/G3 sobre un cierre ya preparado.

        El servidor ya autenticó al actor y comprobó su rol. Acá se vuelve a
        validar la transición contra el ejecutor de la orden y se calcula el
        monto del habilita a partir de la orden y la lista de precios.
        """
        fila = self._repo.obtener_orden_completa(orden_id)
        if fila is None:
            return {"ok": False, "error": "La orden no existe", "codigo": 404}
        orden = _orden_desde_fila(fila, "Ejecutada")
        accion = str(decision or "").strip().lower()
        rol = str(rol or "").strip().lower()
        ejecutor = str(orden.ejecutor or "propio").strip().lower()
        # La autorización se comprueba antes que la existencia del cierre:
        # un actor sin permiso recibe 403 y no puede usar la ausencia del
        # cierre para distinguir un caso que no puede operar.
        if accion == "cerrar":
            if rol != "supervisor" or ejecutor != "propio":
                return {
                    "ok": False,
                    "error": "El supervisor sólo puede cerrar trabajo propio",
                    "codigo": 403,
                }
        elif accion in {"certificar", "observar"}:
            if rol != "inspector" or ejecutor == "propio":
                return {
                    "ok": False,
                    "error": (
                        "El inspector sólo puede certificar u observar "
                        "trabajo de contratista"
                    ),
                    "codigo": 403,
                }
        else:
            return {"ok": False, "error": f"Decisión inválida: {decision!r}", "codigo": 400}
        cierre = self._repo.obtener_cierre_de_orden(orden_id)
        if cierre is None:
            return {"ok": False, "error": "La orden no tiene un cierre registrado", "codigo": 409}
        try:
            transicion = validar_decision_cierre(
                decision=decision,
                rol=rol,
                ejecutor=orden.ejecutor or "propio",
                motivo=motivo,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "codigo": 400}

        monto: float | None = None
        if transicion.estado == "Certificada" and orden.estado.value != "Certificada":
            # La certificación es fail-closed: cada tarea debe tener un ítem
            # contractual y el total debe ser positivo. Una lista vacía o una
            # tarea sin precio nunca puede producir un habilita por USD 0.
            try:
                monto, detalle = monto_de_la_orden(
                    orden,
                    self._precios,
                    horas=(
                        float(cierre["horas"])
                        if cierre.get("horas") is not None
                        else None
                    ),
                )
            except MontoContractualInvalido as exc:
                return {
                    "ok": False,
                    "error": f"No se puede emitir el habilita: {exc}",
                    "codigo": 409,
                }
            logger.info(
                "Monto del habilita de %s: USD %s (%s)",
                orden.id,
                f"{monto:,.2f}",
                "; ".join(detalle) or "sin detalle de precio",
            )

        try:
            resultado = self._repo.decidir_cierre_humano(
                orden_id=orden.id,
                decision=transicion.decision,
                actor=actor,
                rol=transicion.rol,
                motivo=motivo,
                monto_usd=monto,
                fuente_monto="CT-SVP-2025 §2.1",
            )
        except ConflictoEstado as exc:
            return {"ok": False, "error": str(exc), "codigo": 409}
        except AutorizacionInvalida as exc:
            return {"ok": False, "error": str(exc), "codigo": 403}
        except MontoContractualInvalido as exc:
            return {"ok": False, "error": str(exc), "codigo": 409}
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "codigo": 400}

        orden_final = self._repo.obtener_orden_completa(orden.id)
        if orden_final is None:
            return {"ok": False, "error": "La orden ya no existe", "codigo": 404}
        decision_registrada = resultado.get("decision") or {}
        if "estado_orden" in decision_registrada:
            estado_final = str(decision_registrada["estado_orden"])
            motivo_final = decision_registrada.get("motivo")
        else:
            estado_final = transicion.estado
            motivo_final = motivo
        publicada = _orden_desde_fila(
            orden_final,
            estado_final,
            motivo=motivo_final,
            habilita=resultado.get("habilita"),
            monto=resultado.get("monto_habilita_usd"),
        )
        self._cliente.publicar_orden(publicada)
        logger.info(
            "Cierre humano de %s: %s (%s, %s)",
            orden.id,
            transicion.estado,
            actor,
            transicion.rol,
        )
        return {
            "ok": True,
            "orden": orden_final,
            "decision": resultado.get("decision"),
            "habilita": resultado.get("habilita"),
            "monto_habilita_usd": resultado.get("monto_habilita_usd"),
        }

    def _fotos_de_cierre(self, nota: NotaCierre) -> list[dict[str, Any]]:
        """Descarga e identifica las fotos que respaldan el cierre.

        El cierre no usa visión para decidir.  Las fotos nevertheless quedan
        identificadas por URL y hash para que quien certifique pueda volver
        al binario exacto.
        """
        urls = list(nota.fotos or [])
        if not urls:
            return []
        guardar = getattr(self._almacen, "guardar_fotos", None)
        if callable(guardar):
            locales = guardar(nota.id, urls)
        else:
            locales = [None] * len(urls)
        fotos: list[dict[str, Any]] = []
        for indice, url in enumerate(urls):
            local = locales[indice] if indice < len(locales) else None
            hash_foto = hash_sha256_archivo(local) if local is not None else None
            fotos.append(
                {
                    "id": identificador_foto(url, str(local) if local else None, hash_foto),
                    "url": url,
                    "ruta_local": str(local) if local else NO_DISPONIBLE,
                    "hash_sha256": hash_foto or NO_DISPONIBLE,
                    "vision": {
                        "estado": NO_DISPONIBLE,
                        "danio_estructural": NO_DISPONIBLE,
                        "descripcion": NO_DISPONIBLE,
                        "detalle": NO_DISPONIBLE,
                        "modelo": NO_DISPONIBLE,
                        "tokens_prompt": 0,
                        "tokens_completion": 0,
                        "segundos": 0.0,
                    },
                }
            )
        return fotos

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
        estado_db = str(fila.get("estado") or "Ejecutada")
        orden = _orden_desde_fila(
            fila,
            _ESTADO_CONTRACTO.get(estado_db, "Ejecutada"),
            motivo=fila.get("motivo"),
            habilita=str(habilitacion["numero"]) if habilitacion else None,
            monto=float(habilitacion["monto"]) if habilitacion else None,
        )
        self._cliente.publicar_orden(orden)


# --------------------------------------------------------------------- helpers


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
