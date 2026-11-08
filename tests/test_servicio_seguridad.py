"""La alerta de seguridad publica el evento, el aviso en tablet y el aviso por radio."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from planta_contrato import NotaRecorrida, TipoEvento

from analisis.vision import ResultadoVision
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

    def encolar_outbox(self, tipo, payload, topico="sistema/tablet"):
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


class VisionFalsa:
    """Un cliente de visión que devuelve lo que se le pide."""

    def __init__(self, resultado: ResultadoVision, *, habilitado: bool = True) -> None:
        self._resultado = resultado
        self.habilitado = habilitado
        self.fotos: list[Path] = []

    def describir(self, ruta: Path) -> ResultadoVision:
        self.fotos.append(ruta)
        return self._resultado


def _resultado(
    estado: str = "ok",
    danio: bool | None = True,
    descripcion: str = "una grieta en la línea de descarga",
) -> ResultadoVision:
    return ResultadoVision(
        estado=estado,
        danio_estructural=danio,
        descripcion=descripcion,
        detalle="ok" if estado == "ok" else "sin red",
        modelo="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        tokens_prompt=861,
        tokens_completion=191,
        segundos=2.9,
    )


def _servicio(
    cliente: ClienteFalso,
    repo: RepositorioFalso,
    *,
    enlace_ok: bool = True,
    vision=None,
) -> ServicioParteVoz:
    return ServicioParteVoz(
        Settings(),
        repo,
        almacenamiento=object(),
        transcriptor=object(),
        cliente=cliente,
        planta={},
        enlace_ok_fn=lambda: enlace_ok,
        vision=vision,
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


# ------------------------------------------------- la foto que ve el modelo


def test_el_aviso_incluye_lo_que_ve_el_modelo_de_vision() -> None:
    """La descripción de la foto va al aviso: es información para quien revise."""
    cliente = ClienteFalso()
    vision = VisionFalsa(_resultado())
    servicio = _servicio(cliente, RepositorioFalso(), vision=vision)

    servicio._publicar_alerta_seguridad(
        uuid4(), _nota(), _extraccion(), "sale agua a presión", [Path("/datos/foto.jpg")]
    )

    assert "una grieta en la línea de descarga" in cliente.tablets[0].texto


def test_sin_modelo_de_vision_el_aviso_sale_igual() -> None:
    """Sin modelo, el aviso de seguridad no cambia: es un auxiliar, no el sistema."""
    cliente = ClienteFalso()
    servicio = _servicio(cliente, RepositorioFalso())

    servicio._publicar_alerta_seguridad(
        uuid4(), _nota(), _extraccion(), "sale agua a presión", [Path("/datos/foto.jpg")]
    )

    assert "La foto adjunta muestra" not in cliente.tablets[0].texto
    assert "ALERTA DE SEGURIDAD S1" in cliente.tablets[0].texto


def test_si_el_modelo_no_puede_describir_el_aviso_sale_sin_descripcion() -> None:
    """Fail-safe: un fallo del modelo no es "no hay daño", es silence."""
    cliente = ClienteFalso()
    vision = VisionFalsa(_resultado(estado="indeterminado", danio=None, descripcion=""))
    servicio = _servicio(cliente, RepositorioFalso(), vision=vision)

    servicio._publicar_alerta_seguridad(
        uuid4(), _nota(), _extraccion(), "sale agua a presión", [Path("/datos/foto.jpg")]
    )

    assert "La foto adjunta muestra" not in cliente.tablets[0].texto
    assert "ALERTA DE SEGURIDAD S1" in cliente.tablets[0].texto


def test_el_modelo_de_vision_no_cambia_el_estado_de_la_alerta() -> None:
    """La decisión sigue en código: el modelo sólo describe."""
    cliente = ClienteFalso()
    vision = VisionFalsa(_resultado(danio=False, descripcion="todo en orden"))
    servicio = _servicio(cliente, RepositorioFalso(), vision=vision)

    servicio._publicar_alerta_seguridad(
        uuid4(), _nota(), _extraccion(), "sale agua a presión", [Path("/datos/foto.jpg")]
    )

    # El evento sigue siendo una alerta de seguridad (no un parte, no una orden
    # aprobada): el modelo no decide.
    assert cliente.eventos[0].tipo is TipoEvento.alerta_seguridad
    assert cliente.tablets[0].espera_respuesta is False
