"""Cola de mensajes que necesitan el radioenlace.

El aviso por radio al supervisor de turno sale de la planta: si el radioenlace
está caído, el aviso no se puede enviar y **no se pierde**: queda en la cola
(``outbox_cola``) y sale, en orden, cuando el enlace vuelve.

El aviso en tablet es local (la tablet está en la planta) y se publica siempre,
con o sin enlace.

La cola es idempotente: el id del mensaje es la clave, así que el mismo aviso
no queda dos veces.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from planta_contrato import MensajeTablet

logger = logging.getLogger("planta.cola")


class RepositorioCola(Protocol):
    """Operaciones de cola que usa este módulo."""

    def encolar_outbox(self, tipo: str, payload: dict[str, Any]) -> str: ...

    def pendientes_outbox(self, limite: int = 50) -> list[tuple[str, dict[str, Any]]]: ...

    def marcar_outbox_enviado(self, id_outbox: str) -> None: ...

    def marcar_outbox_error(self, id_outbox: str) -> None: ...


class Publicador(Protocol):
    """Publica un mensaje de tablet en el bus de la planta."""

    def publicar_tablet(self, mensaje: MensajeTablet) -> None: ...


def encolar_aviso(repo: RepositorioCola, mensaje: MensajeTablet, tipo: str = "radio") -> str:
    """Encola un aviso para enviarlo cuando vuelva el enlace."""
    id_outbox = repo.encolar_outbox(tipo, mensaje.model_dump(mode="json"))
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
    limite: int = 50,
) -> int:
    """Publica los mensajes en cola, en orden, y marca los enviados.

    Se llama solo cuando el enlace está disponible. Un mensaje que no se puede
    publicar queda en la cola y se vuelve a intentar en la próxima vuelta.
    """
    enviados = 0
    for id_outbox, payload in repo.pendientes_outbox(limite):
        try:
            mensaje = MensajeTablet.model_validate(payload)
            publicador.publicar_tablet(mensaje)
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo publicar el mensaje en cola %s: %s", id_outbox, exc)
            repo.marcar_outbox_error(id_outbox)
            break
        repo.marcar_outbox_enviado(id_outbox)
        enviados += 1
        logger.info("Mensaje en cola publicado: %s", payload.get("id"))
    return enviados
