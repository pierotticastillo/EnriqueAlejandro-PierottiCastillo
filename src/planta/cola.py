"""La salida de la planta: lo que no se puede mandar ahora, no se pierde.

La cola (``outbox_cola``) es la salida de la planta. Todo lo que el sistema
publica al bus de la planta pasa por acá:

* **Eventos** (``sistema/eventos``) y **órdenes** (``sistema/ordenes``): van al
  bus local. Si el bus no está, el mensaje queda en la cola en vez de perderse
  en la memoria del cliente MQTT (que se pierde al reiniciar el proceso).
* **Avisos por radio** (``sistema/tablet``): el contrato no tiene un tópico de
  radio, así que el aviso al supervisor de turno sale por la tablet, que es el
  canal de avisos del sistema. El radio **necesita el enlace**: si el
  radioenlace está caído, el aviso no se puede enviar y queda en la cola.

**Orden.** Los mensajes salen en el orden en que se encolaron (FIFO). Pero el
orden es **por destino**: un aviso por radio no puede salir después del aviso
por radio que lo sigue, y un evento no se queda esperando a un aviso de radio
que todavía no puede salir (el radio va por otro lado). Por eso:

* los avisos por radio salen en orden entre ellos, y sólo cuando el enlace
  volvió;
* los eventos y las órdenes salen en cuanto el bus está, sin esperar al radio.

**Fail-safe.** Si un mensaje no se puede publicar, queda pendiente y se
reintenta con espera (no se pierde y no se publica dos veces). La cola es
idempotente: el id del mensaje es la clave, así que el mismo aviso, evento u
orden no queda dos veces.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any, Protocol

from planta_contrato import EventoSistema, MensajeTablet, OrdenSistema, Topicos
from pydantic import ValidationError

logger = logging.getLogger("planta.cola")

#: Qué contrato hay que validar según el tipo de mensaje de la cola.
MODELOS: dict[str, Any] = {
    "radio": MensajeTablet,
    "evento": EventoSistema,
    "orden": OrdenSistema,
}


class RepositorioCola(Protocol):
    """Operaciones de cola que usa este módulo."""

    def encolar_outbox(
        self, tipo: str, payload: dict[str, Any], topico: str = "sistema/tablet"
    ) -> str: ...

    def pendientes_outbox(
        self, limite: int = 50
    ) -> list[tuple[str, str, str, dict[str, Any]]]: ...

    def marcar_outbox_enviado(self, id_outbox: str) -> None: ...

    def marcar_outbox_error(self, id_outbox: str) -> None: ...


class Publicador(Protocol):
    """Publica un mensaje en el bus de la planta."""

    def publicar(self, topico: str, payload: str, *, qos: int = 1) -> None: ...


def encolar_aviso(repo: RepositorioCola, mensaje: MensajeTablet, tipo: str = "radio") -> str:
    """Encola un aviso para enviarlo cuando vuelva el enlace."""
    id_outbox = repo.encolar_outbox(tipo, mensaje.model_dump(mode="json"), Topicos.TABLET)
    logger.warning(
        "Enlace caído: el aviso %s queda en la cola (%s)",
        mensaje.id,
        id_outbox,
    )
    return id_outbox


def publicar_pendientes(
    repo: RepositorioCola,
    publicador: Publicador,
    *,
    enlace_ok: bool = True,
    limite: int = 50,
) -> int:
    """Publica los mensajes en cola, en orden, y marca los enviados.

    Se llama cuando el bus está disponible. Un mensaje que no se puede
    publicar queda en la cola y se vuelve a intentar en la próxima vuelta: el
    corte se hace en el primero que falla para **no cambiar el orden** de lo
    que sale.
    """
    enviados = 0
    for id_outbox, tipo, topico, payload in repo.pendientes_outbox(limite):
        if tipo == "radio" and not enlace_ok:
            # El radio necesita el enlace: sigue en la cola, en su lugar.
            # Los eventos y las órdenes no dependen del radio y sí salen.
            continue

        modelo = MODELOS.get(tipo)
        if modelo is None:
            logger.warning(
                "Mensaje en cola %s de tipo desconocido (%r): se descarta",
                id_outbox,
                tipo,
            )
            repo.marcar_outbox_enviado(id_outbox)
            continue

        try:
            mensaje = modelo.model_validate(payload)
        except ValidationError as exc:
            # Un mensaje que no valida no se puede publicar nunca: se marca
            # con error y se deja de reintentar (queda para revisarlo).
            logger.warning("Mensaje en cola %s inválido: %s", id_outbox, exc.errors()[:2])
            repo.marcar_outbox_error(id_outbox)
            continue

        try:
            publicador.publicar(topico, mensaje.model_dump_json())
        except Exception as exc:  # noqa: BLE001 - el bus puede estar caído
            logger.warning("No se pudo publicar el mensaje en cola %s: %s", id_outbox, exc)
            repo.marcar_outbox_error(id_outbox)
            break
        repo.marcar_outbox_enviado(id_outbox)
        enviados += 1
        logger.info("Mensaje en cola publicado en %s: %s", topico, payload.get("id"))
    return enviados


class DrenadorCola:
    """Vacia la cola de salida, sin publicar dos veces lo mismo.

    Lo usan dos cosas a la vez (el latido y el hilo de la cola), así que
    lleva un candado: si entran dos vueltas al mismo tiempo, la segunda
    espera.
    """

    def __init__(
        self,
        repo: RepositorioCola,
        publicador: Publicador,
        *,
        enlace_ok_fn: Callable[[], bool] | None = None,
    ) -> None:
        self._repo = repo
        self._publicador = publicador
        self._enlace_ok = enlace_ok_fn or (lambda: True)
        self._candado = threading.Lock()

    def drenar(self, limite: int = 50) -> int:
        """Publica lo que quedó en la cola y devuelve cuántos salieron."""
        with self._candado:
            try:
                enlace_ok = self._enlace_ok()
            except Exception:  # noqa: BLE001 - sin estado de enlace, se asume disponible
                logger.exception("No se pudo comprobar el enlace para vaciar la cola")
                enlace_ok = True
            return publicar_pendientes(
                self._repo,
                self._publicador,
                enlace_ok=enlace_ok,
                limite=limite,
            )
