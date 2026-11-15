"""El gate fotográfico de escalados ordinarios, en el camino productivo.

El servicio de parte entrega al análisis el resultado estructurado de las
fotos. El modelo no aprueba la orden: sólo puede activar el gate de revisión
humana por daño estructural o por un resultado indeterminado.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from planta_contrato import EventoSistema, TipoEvento

from analisis.vision import ResultadoVision
from config import Settings
from planta.servicio import ServicioParteVoz


class AnalisisFalso:
    """Captura la evidencia fotográfica que recibe el análisis."""

    def __init__(self) -> None:
        self.evidencia = ()

    def analizar_evento(
        self,
        evento,
        *,
        filas=None,
        fotos=None,
        evidencia_vision=(),
        filas_confirmadas=None,
        tendencias=None,
    ):
        self.evidencia = evidencia_vision
        return None


class VisionFalsa:
    def __init__(self, resultado: ResultadoVision) -> None:
        self.resultado = resultado
        self.fotos: list[Path] = []

    def describir(self, ruta: Path) -> ResultadoVision:
        self.fotos.append(ruta)
        return self.resultado


class RepositorioFalso:
    pass


class ClienteFalso:
    pass


def _evento() -> EventoSistema:
    return EventoSistema(
        id="parte:NOTA-1:B-102",
        ts=datetime(2026, 11, 14, 9, 0, tzinfo=UTC),
        tipo=TipoEvento.escalado,
        equipo="B-102",
        origen="parte",
        parte_id="parte-1",
        nota_ids=["NOTA-1"],
        resumen="B-102 goteo_empaquetadura: pérdida por empaquetadura",
    )


def _resultado(
    *,
    estado: str = "ok",
    danio: bool | None = True,
) -> ResultadoVision:
    return ResultadoVision(
        estado=estado,
        danio_estructural=danio,
        descripcion="grieta en la carcasa" if estado == "ok" else "",
        detalle="ok" if estado == "ok" else "la API no respondió",
        modelo="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    )


def _servicio(analisis: AnalisisFalso, vision=None) -> ServicioParteVoz:
    return ServicioParteVoz(
        Settings(),
        RepositorioFalso(),
        almacenamiento=object(),
        transcriptor=object(),
        cliente=ClienteFalso(),
        planta={},
        analisis=analisis,
        vision=vision,
    )


def test_el_escalado_ordinario_entrega_la_evidencia_fotografica_al_analisis() -> None:
    analisis = AnalisisFalso()
    vision = VisionFalsa(_resultado())
    servicio = _servicio(analisis, vision)

    servicio.generar_orden(
        _evento(),
        filas=[],
        fotos_locales=[Path("/datos/NOTA-1/foto.jpg")],
        fotos_urls=["https://planta.example/NOTA-1/foto.jpg"],
    )

    assert len(vision.fotos) == 1
    assert len(analisis.evidencia) == 1
    evidencia = analisis.evidencia[0]
    assert evidencia.foto_url == "https://planta.example/NOTA-1/foto.jpg"
    assert evidencia.ruta_local == str(Path("/datos/NOTA-1/foto.jpg"))
    assert evidencia.danio_estructural is True
    assert evidencia.requiere_revision_humana is True


def test_sin_modelo_la_foto_no_se_interpreta_como_sin_danio() -> None:
    analisis = AnalisisFalso()
    servicio = _servicio(analisis)

    servicio.generar_orden(
        _evento(),
        filas=[],
        fotos_locales=[Path("/datos/NOTA-1/foto.jpg")],
        fotos_urls=["https://planta.example/NOTA-1/foto.jpg"],
    )

    assert len(analisis.evidencia) == 1
    evidencia = analisis.evidencia[0]
    assert evidencia.estado == "indeterminado"
    assert evidencia.danio_estructural is None
    assert evidencia.requiere_revision_humana is True
