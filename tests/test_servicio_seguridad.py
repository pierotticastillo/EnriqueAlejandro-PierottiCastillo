"""La alerta de seguridad publica el evento, el aviso en tablet y el aviso por radio."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from planta_contrato import NotaRecorrida, TipoEvento

from config import Settings
from planta.parte_extractor import Extraccion, Hallazgo
from planta.servicio import ServicioParteVoz


class RepositorioFalso:
    def __init__(self) -> None:
        self.evidencias = []
        self.encolados = []

    def registrar_alerta_seguridad(self, nota_id, externa, tipo, evidencia):
        assert tipo == "S1"
        assert evidencia["texto"]
        self.evidencias.append(evidencia)
        return "evento-1"

    def encolar_outbox(self, tipo, payload):
        self.encolados.append((tipo, payload))
        return "out-1"


class ClienteFalso:
    def __init__(self) -> None:
        self.eventos = []
        self.tablets = []

    def publicar_evento(self, evento) -> None:
        self.eventos.append(evento)

    def publicar_tablet(self, mensaje) -> None:
        self.tablets.append(mensaje)


def _servicio(
    cliente: ClienteFalso,
    repo: RepositorioFalso,
    *,
    enlace_ok: bool = True,
) -> ServicioParteVoz:
    return ServicioParteVoz(
        Settings(),
        repo,
        almacenamiento=object(),
        transcriptor=object(),
        cliente=cliente,
        planta={},
        enlace_ok_fn=lambda: enlace_ok,
    )


def _nota() -> NotaRecorrida:
    return NotaRecorrida(
        id="NOTA-00099",
        ts=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
        operador="Operador",
        audio_url=None,
        fotos=[],
        recorrida_id="REC-00099",
    )


def _extraccion() -> Extraccion:
    return Extraccion(
        filas=[
            Hallazgo(
                tag="B-101",
                variable="condicion_insegura",
                valor=None,
                unidad=None,
                observacion="se rompió un niple y sale agua a presión",
            )
        ],
        seguridad="S1",
    )


def test_alerta_seguridad_muestra_fila_en_tablet() -> None:
    cliente = ClienteFalso()
    servicio = _servicio(cliente, RepositorioFalso())

    servicio._publicar_alerta_seguridad(uuid4(), _nota(), _extraccion(), "sale agua a presión")

    assert len(cliente.eventos) == 1
    evento = cliente.eventos[0]
    assert evento.tipo is TipoEvento.alerta_seguridad
    assert evento.equipo == "B-101"
    assert evento.nota_ids == ["NOTA-00099"]

    aviso = cliente.tablets[0]
    assert aviso.espera_respuesta is False
    assert aviso.pantalla.filas
    fila = aviso.pantalla.filas[0]
    assert fila.tag == "B-101"
    assert fila.variable == "condicion_insegura"
    assert fila.observacion


def test_alerta_seguridad_publica_el_aviso_por_radio() -> None:
    cliente = ClienteFalso()
    servicio = _servicio(cliente, RepositorioFalso())

    servicio._publicar_alerta_seguridad(uuid4(), _nota(), _extraccion(), "sale agua a presión")

    assert len(cliente.tablets) == 2
    radio = cliente.tablets[1]
    assert radio.espera_respuesta is False
    assert radio.texto.startswith("Radio canal 1 - Supervisor de turno:")
    assert "Alejarse, avisar, detener las bombas" in radio.texto


def test_el_aviso_en_tablet_trae_el_procedimiento_de_emergencia() -> None:
    cliente = ClienteFalso()
    servicio = _servicio(cliente, RepositorioFalso())

    servicio._publicar_alerta_seguridad(uuid4(), _nota(), _extraccion(), "sale agua a presión")

    aviso = cliente.tablets[0]
    assert "ALERTA DE SEGURIDAD S1" in aviso.texto
    assert "Alejarse, avisar, detener las bombas" in aviso.texto
    assert "Nadie se acerca a la pérdida" in aviso.texto


def test_la_evidencia_guarda_el_procedimiento_y_el_canal_de_radio() -> None:
    cliente = ClienteFalso()
    repo = RepositorioFalso()
    servicio = _servicio(cliente, repo)

    servicio._publicar_alerta_seguridad(uuid4(), _nota(), _extraccion(), "sale agua a presión")

    evidencia = repo.evidencias[0]
    assert evidencia["tipo"] == "S1"
    assert evidencia["radio_canal"] == "1"
    assert evidencia["que_hacer"]


def test_sin_enlace_el_aviso_por_radio_queda_en_la_cola() -> None:
    """El aviso por radio sale de la planta: sin radioenlace va a la cola."""
    cliente = ClienteFalso()
    repo = RepositorioFalso()
    servicio = _servicio(cliente, repo, enlace_ok=False)

    servicio._publicar_alerta_seguridad(uuid4(), _nota(), _extraccion(), "sale agua a presión")

    # El aviso en tablet es local y sale siempre.
    assert len(cliente.tablets) == 1
    assert "ALERTA DE SEGURIDAD S1" in cliente.tablets[0].texto
    # El aviso por radio queda en la cola.
    assert len(repo.encolados) == 1
    tipo, payload = repo.encolados[0]
    assert tipo == "radio"
    assert payload["id"].endswith("-radio")
    assert cliente.tablets[0].id != payload["id"]
