"""La cola de salida: lo que la planta no puede mandar ahora, no se pierde.

La cola es el camino de salida de eventos, órdenes y avisos. Un mensaje sale
en el orden en que se encoló, y **por destino**: un aviso por radio espera al
radioenlace, pero un evento no se queda esperando a un aviso de radio.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from planta_contrato import (
    EstadoOrden,
    EventoSistema,
    MensajeTablet,
    OrdenSistema,
    Pantalla,
    TipoEvento,
    Topicos,
)

from planta.cola import DrenadorCola, encolar_aviso, publicar_pendientes


class RepoFalso:
    """Una cola de verdad, en memoria."""

    def __init__(self) -> None:
        self.pendientes: list[tuple[str, str, str, dict[str, Any]]] = []
        self.enviados: list[str] = []
        self.errores: list[str] = []
        self.contador = 0

    def encolar_outbox(
        self, tipo: str, payload: dict[str, Any], topico: str = "sistema/tablet"
    ) -> str:
        self.contador += 1
        id_outbox = f"out-{self.contador}"
        self.pendientes.append((id_outbox, tipo, topico, payload))
        return id_outbox

    def pendientes_outbox(
        self, limite: int = 50
    ) -> list[tuple[str, str, str, dict[str, Any]]]:
        return self.pendientes[:limite]

    def marcar_outbox_enviado(self, id_outbox: str) -> None:
        self.enviados.append(id_outbox)
        self.pendientes = [p for p in self.pendientes if p[0] != id_outbox]

    def marcar_outbox_error(self, id_outbox: str) -> None:
        self.errores.append(id_outbox)


class PublicadorFalso:
    """Un bus de planta que publica o falla."""

    def __init__(self, falla: bool = False) -> None:
        self.publicados: list[tuple[str, str]] = []
        self.falla = falla

    def publicar(self, topico: str, payload: str, *, qos: int = 1) -> None:
        if self.falla:
            raise RuntimeError("no se pudo publicar")
        self.publicados.append((topico, payload))


def _mensaje(id_: str) -> MensajeTablet:
    return MensajeTablet(
        id=id_,
        ts=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
        nota_ids=["NOTA-00001"],
        texto="Radio canal 1 - Supervisor de turno: alerta S1",
        pantalla=Pantalla(titulo="Radio canal 1", filas=[]),
        espera_respuesta=False,
    )


def _evento(id_: str = "parte:NOTA-00001:A1") -> EventoSistema:
    return EventoSistema(
        id=id_,
        ts=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
        tipo=TipoEvento.alerta_seguridad,
        equipo="A1",
        origen="parte",
        parte_id="NOTA-00001",
        nota_ids=["NOTA-00001"],
        resumen="fuga en la descarga",
    )


def _orden(id_: str = "ORD-A1-1") -> OrdenSistema:
    return OrdenSistema(
        id=id_,
        ts=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
        evento_ids=["parte:NOTA-00001:A1"],
        equipo="A1",
        estado=EstadoOrden.pendiente,
        tareas=["T-B-EMP"],
        detiene_equipo=False,
        reduce_inyeccion=False,
        ejecutor="propio",
        repuestos=[],
        motivo="falta repuesto",
    )


# ------------------------------------------------------------------- el aviso


def test_el_aviso_encolado_queda_pendiente() -> None:
    repo = RepoFalso()

    encolar_aviso(repo, _mensaje("radio-1"))

    assert len(repo.pendientes) == 1
    id_outbox, tipo, topico, payload = repo.pendientes[0]
    assert tipo == "radio"
    assert topico == Topicos.TABLET
    assert payload["id"] == "radio-1"
    assert repo.enviados == []


def test_la_cola_publica_en_orden_y_marca_enviado() -> None:
    repo = RepoFalso()
    publicador = PublicadorFalso()
    encolar_aviso(repo, _mensaje("radio-1"))
    encolar_aviso(repo, _mensaje("radio-2"))

    enviados = publicar_pendientes(repo, publicador)

    assert enviados == 2
    assert [topico for topico, _ in publicador.publicados] == [Topicos.TABLET] * 2
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


# ------------------------------------------------------- eventos y órdenes


def test_un_evento_sale_por_su_topico() -> None:
    repo = RepoFalso()
    publicador = PublicadorFalso()
    repo.encolar_outbox("evento", _evento().model_dump(mode="json"), Topicos.EVENTOS)

    enviados = publicar_pendientes(repo, publicador)

    assert enviados == 1
    topico, payload = publicador.publicados[0]
    assert topico == Topicos.EVENTOS
    assert '"parte:NOTA-00001:A1"' in payload


def test_una_orden_sale_por_su_topico() -> None:
    repo = RepoFalso()
    publicador = PublicadorFalso()
    repo.encolar_outbox("orden", _orden().model_dump(mode="json"), Topicos.ORDENES)

    enviados = publicar_pendientes(repo, publicador)

    assert enviados == 1
    topico, _ = publicador.publicados[0]
    assert topico == Topicos.ORDENES


# ------------------------------------------------------------ el orden por destino


def test_el_radio_espera_al_enlace_pero_los_eventos_no() -> None:
    """El enlace caído frena al radio, no frena a los eventos.

    El evento y la orden van por el bus de la planta: no tienen por qué
    esperar a que vuelva el radioenlace.
    """
    repo = RepoFalso()
    publicador = PublicadorFalso()
    encolar_aviso(repo, _mensaje("radio-1"))
    repo.encolar_outbox("evento", _evento().model_dump(mode="json"), Topicos.EVENTOS)
    repo.encolar_outbox("orden", _orden().model_dump(mode="json"), Topicos.ORDENES)

    enviados = publicar_pendientes(repo, publicador, enlace_ok=False)

    assert enviados == 2
    topicos = [topico for topico, _ in publicador.publicados]
    assert topicos == [Topicos.EVENTOS, Topicos.ORDENES]
    # El aviso por radio sigue en la cola, en su lugar.
    assert len(repo.pendientes) == 1
    assert repo.pendientes[0][1] == "radio"


def test_los_avisos_por_radio_salen_en_orden_entre_ellos() -> None:
    repo = RepoFalso()
    publicador = PublicadorFalso()
    encolar_aviso(repo, _mensaje("radio-1"))
    encolar_aviso(repo, _mensaje("radio-2"))

    publicar_pendientes(repo, publicador, enlace_ok=False)
    assert publicador.publicados == []

    publicar_pendientes(repo, publicador, enlace_ok=True)

    assert len(publicador.publicados) == 2
    assert '"radio-1"' in publicador.publicados[0][1]
    assert '"radio-2"' in publicador.publicados[1][1]


def test_un_mensaje_invalido_no_bloquea_a_los_demas() -> None:
    """Un mensaje que no valida no se publica, pero tampoco frena la cola."""
    repo = RepoFalso()
    publicador = PublicadorFalso()
    repo.encolar_outbox("evento", {"id": "sin-campos"}, Topicos.EVENTOS)
    repo.encolar_outbox("evento", _evento().model_dump(mode="json"), Topicos.EVENTOS)

    enviados = publicar_pendientes(repo, publicador)

    assert enviados == 1
    assert repo.errores == ["out-1"]
    assert len(publicador.publicados) == 1


def test_un_tipo_desconocido_se_descarta() -> None:
    repo = RepoFalso()
    publicador = PublicadorFalso()
    repo.encolar_outbox("inventado", {"id": "x"}, "sistema/inventado")

    enviados = publicar_pendientes(repo, publicador)

    assert enviados == 0
    assert repo.enviados == ["out-1"]
    assert repo.pendientes == []


# ----------------------------------------------------------------- el drenador


def test_el_drenador_solo_vacia_cuando_el_enlace_esta_para_el_radio() -> None:
    repo = RepoFalso()
    publicador = PublicadorFalso()
    encolar_aviso(repo, _mensaje("radio-1"))
    repo.encolar_outbox("evento", _evento().model_dump(mode="json"), Topicos.EVENTOS)

    drenador = DrenadorCola(repo, publicador, enlace_ok_fn=lambda: False)
    assert drenador.drenar() == 1  # sale el evento
    assert len(repo.pendientes) == 1  # el radio sigue esperando

    drenador = DrenadorCola(repo, publicador, enlace_ok_fn=lambda: True)
    assert drenador.drenar() == 1  # sale el radio
    assert repo.pendientes == []


def test_el_drenador_no_publica_dos_veces_lo_mismo() -> None:
    """El latido y el hilo de la cola drenan a la vez: no se duplica nada."""
    repo = RepoFalso()
    publicador = PublicadorFalso()
    encolar_aviso(repo, _mensaje("radio-1"))

    drenador = DrenadorCola(repo, publicador)

    enviados = drenador.drenar()

    assert enviados == 1
    assert len(publicador.publicados) == 1


def test_un_error_del_enlace_no_tumba_el_drenado() -> None:
    repo = RepoFalso()
    publicador = PublicadorFalso()
    encolar_aviso(repo, _mensaje("radio-1"))

    def enlace_falla() -> bool:
        raise ConnectionError("no hay enlace")

    drenador = DrenadorCola(repo, publicador, enlace_ok_fn=enlace_falla)

    assert drenador.drenar() == 1
