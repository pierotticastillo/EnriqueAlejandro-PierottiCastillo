"""El servicio publica un evento por equipo del parte.

Reglas que se cubren acá:

* un parte confirmado publica **un evento por equipo**;
* un parte **sin lecturas** (solo observaciones) publica el evento del equipo
  en cuanto queda resuelto lo único que estaba abierto: el equipo. No hay
  nada que el operador pueda corregir en un parte sin mediciones.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from planta_contrato import NotaRecorrida, TipoEvento

from config import Settings
from planta.parte_extractor import Extraccion, Hallazgo
from planta.servicio import ServicioParteVoz

PLANTA = {
    "equipos": [
        {
            "tag": "B-103",
            "tipo": "bomba",
            "nombres": ["la tres", "la 3", "la de reserva", "bomba tres"],
            "manometro_local": {"unidad": "bar"},
        }
    ],
    "sensores": [],
}


class RepositorioFalso:
    def __init__(self) -> None:
        self.eventos: list[tuple[object, str | None]] = []
        self.partes: list[object] = []

    def guardar_parte(self, nota_id, ts, filas, **kwargs) -> str:
        self.partes.append((nota_id, filas))
        return "parte-1"

    def guardar_conversacion(self, nota_id, mensaje, filas, **kwargs) -> None:
        pass

    def actualizar_estado_nota(self, nota_id, estado: str) -> None:
        pass

    def registrar_evento(self, evento, *, nota_id=None) -> bool:
        self.eventos.append((evento, nota_id))
        return True


class ClienteFalso:
    def __init__(self) -> None:
        self.eventos: list[object] = []
        self.tablets: list[object] = []

    def publicar_evento(self, evento) -> None:
        self.eventos.append(evento)

    def publicar_tablet(self, mensaje) -> None:
        self.tablets.append(mensaje)


def _servicio(cliente: ClienteFalso, repo: RepositorioFalso) -> ServicioParteVoz:
    return ServicioParteVoz(
        Settings(),
        repo,
        almacenamiento=object(),
        transcriptor=object(),
        cliente=cliente,
        planta=PLANTA,
    )


def _nota() -> NotaRecorrida:
    return NotaRecorrida(
        id="NOTA-00008",
        ts=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
        operador="Operador",
        audio_url=None,
        fotos=[],
        recorrida_id="REC-00008",
    )


def test_parte_sin_lecturas_publica_el_evento_del_equipo() -> None:
    cliente = ClienteFalso()
    repo = RepositorioFalso()
    servicio = _servicio(cliente, repo)

    filas = [
        Hallazgo(
            tag="B-103",
            variable="estado",
            valor=None,
            unidad=None,
            observacion="parada, en reserva",
        )
    ]
    servicio._iniciar_conversacion(
        uuid4(),
        _nota(),
        Extraccion(filas=filas),
        [],
    )

    assert len(cliente.eventos) == 1
    evento = cliente.eventos[0]
    assert evento.tipo is TipoEvento.sin_novedad
    assert evento.equipo == "B-103"
    assert evento.id == "parte:NOTA-00008:B-103"
    assert evento.nota_ids == ["NOTA-00008"]
    # El borrador igual se muestra en la tablet.
    assert len(cliente.tablets) == 1


def test_parte_con_lecturas_no_publica_eventos_hasta_la_confirmacion() -> None:
    cliente = ClienteFalso()
    repo = RepositorioFalso()
    servicio = _servicio(cliente, repo)

    filas = [
        Hallazgo(
            tag="B-103",
            variable="presion_descarga_local",
            valor=100.0,
            unidad="bar",
            observacion=None,
        )
    ]
    servicio._iniciar_conversacion(
        uuid4(),
        _nota(),
        Extraccion(filas=filas),
        [],
    )

    # Con mediciones, el evento espera a que el operador confirme el borrador.
    assert cliente.eventos == []
    assert len(cliente.tablets) == 1
