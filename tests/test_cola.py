"""Cola de mensajes que esperan el radioenlace.

El aviso por radio al supervisor sale de la planta: sin radioenlace no se
puede enviar, pero tampoco se pierde. Queda en la cola y sale, en orden,
cuando el enlace vuelve.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from planta_contrato import MensajeTablet, Pantalla

from planta.cola import encolar_aviso, publicar_pendientes


class RepoFalso:
    def __init__(self) -> None:
        self.pendientes: list[tuple[str, dict[str, Any]]] = []
        self.enviados: list[str] = []
        self.errores: list[str] = []
        self.contador = 0

    def encolar_outbox(self, tipo: str, payload: dict[str, Any]) -> str:
        self.contador += 1
        id_outbox = f"out-{self.contador}"
        self.pendientes.append((id_outbox, payload))
        return id_outbox

    def pendientes_outbox(self, limite: int = 50) -> list[tuple[str, dict[str, Any]]]:
        return self.pendientes[:limite]

    def marcar_outbox_enviado(self, id_outbox: str) -> None:
        self.enviados.append(id_outbox)
        self.pendientes = [p for p in self.pendientes if p[0] != id_outbox]

    def marcar_outbox_error(self, id_outbox: str) -> None:
        self.errores.append(id_outbox)


class PublicadorFalso:
    def __init__(self, falla: bool = False) -> None:
        self.publicados: list[MensajeTablet] = []
        self.falla = falla

    def publicar_tablet(self, mensaje: MensajeTablet) -> None:
        if self.falla:
            raise RuntimeError("no se pudo publicar")
        self.publicados.append(mensaje)


def _mensaje(id_: str) -> MensajeTablet:
    return MensajeTablet(
        id=id_,
        ts=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
        nota_ids=["NOTA-00001"],
        texto="Radio canal 1 - Supervisor de turno: alerta S1",
        pantalla=Pantalla(titulo="Radio canal 1", filas=[]),
        espera_respuesta=False,
    )


def test_el_aviso_encolado_queda_pendiente() -> None:
    repo = RepoFalso()

    encolar_aviso(repo, _mensaje("radio-1"))

    assert len(repo.pendientes) == 1
    id_outbox, payload = repo.pendientes[0]
    assert payload["id"] == "radio-1"
    assert repo.enviados == []


def test_la_cola_publica_en_orden_y_marca_enviado() -> None:
    repo = RepoFalso()
    publicador = PublicadorFalso()
    encolar_aviso(repo, _mensaje("radio-1"))
    encolar_aviso(repo, _mensaje("radio-2"))

    enviados = publicar_pendientes(repo, publicador)

    assert enviados == 2
    assert [m.id for m in publicador.publicados] == ["radio-1", "radio-2"]
    assert repo.enviados == ["out-1", "out-2"]
    assert repo.pendientes == []


def test_un_mensaje_que_falla_queda_pendiente_para_reintentar() -> None:
    repo = RepoFalso()
    publicador = PublicadorFalso(falla=True)
    encolar_aviso(repo, _mensaje("radio-1"))

    enviados = publicar_pendientes(repo, publicador)

    assert enviados == 0
    assert repo.errores == ["out-1"]
    assert len(repo.pendientes) == 1
