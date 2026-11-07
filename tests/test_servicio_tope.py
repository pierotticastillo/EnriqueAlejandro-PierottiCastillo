"""La conversación de tablet no puede ser infinita.

Si el operador y el sistema no se ponen de acuerdo, cada corrección genera
un borrador nuevo y la nota se queda dando vueltas: se consume tiempo de
planta sin chances de cerrar. Con el tope, la nota queda para revisión
humana.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from planta_contrato import NotaRecorrida, RespuestaOperador

from config import Settings
from planta.servicio import ServicioParteVoz


class AlmacenamientoFalso:
    def guardar_audio(self, nota_id: str, url: str | None) -> Path | None:
        return Path(f"/datos/notas/{nota_id}/audio.wav") if url else None

    def guardar_fotos(self, nota_id: str, fotos: list[str]) -> list[Path | None]:
        return [None for _ in fotos]


class TranscriptorFalso:
    def transcribir(self, ruta: Path) -> str:
        return "La uno está en 102 kilogramos de descarga"


class RepositorioFalso:
    """Guarda la conversación como lo haría la base."""

    def __init__(self) -> None:
        self.conversaciones: dict[str, dict] = {}
        self.estados: list[str] = []

    def obtener_conversacion_por_nota(self, nota_id: str) -> None:
        return None

    def guardar_nota(self, nota, *, transcripcion, estado, seguridad) -> str:
        return nota.id

    def guardar_parte(self, nota_id, ts, filas, **kwargs) -> str:
        return f"parte-{nota_id}"

    def guardar_conversacion(
        self,
        nota_id,
        mensaje,
        filas,
        *,
        intento: int,
        estado: str,
        datos: dict | None = None,
    ) -> None:
        self.conversaciones[mensaje.id] = {
            "nota_id": nota_id,
            "mensaje_id": mensaje.id,
            "borrador": [
                {
                    "tag": fila.tag,
                    "variable": fila.variable,
                    "valor": fila.valor,
                    "unidad": fila.unidad,
                    "observacion": fila.observacion,
                    "texto_fuente": fila.texto_fuente,
                    "foto_indice": fila.foto_indice,
                }
                for fila in filas
            ],
            "intento": intento,
            "estado": estado,
            "datos": dict(datos or {}),
        }
        self.estados.append(estado)

    def obtener_conversacion(self, mensaje_id: str) -> dict | None:
        return self.conversaciones.get(mensaje_id)

    def actualizar_estado_nota(self, nota_id: str, estado: str) -> None:
        pass

    def confirmar_parte(self, nota_id, ts) -> str:
        return f"parte-{nota_id}"

    def marcar_conversacion(self, nota_id: str, estado: str) -> None:
        self.estados.append(estado)


class ClienteFalso:
    def __init__(self) -> None:
        self.tablets: list[object] = []
        self.ordenes: list[object] = []

    def publicar_tablet(self, mensaje) -> None:
        self.tablets.append(mensaje)

    def publicar_orden(self, orden) -> None:
        self.ordenes.append(orden)

    def publicar_evento(self, evento) -> None:
        pass


class AnalisisFalso:
    def analizar_evento(self, evento, *, filas=None):
        return "analisis"

    def analizar_alerta_seguridad(self, evento, *, texto=None):
        return "analisis"

    def orden_sistema(self, analisis):
        raise AssertionError("no debería haber orden en este test")

    def datos_de_orden(self, analisis) -> dict:
        return {}

    def redactar(self, analisis) -> str:
        return ""

    @property
    def nombre_modelo(self) -> str:
        return "ninguno"


def _nota() -> NotaRecorrida:
    return NotaRecorrida(
        id="NOTA-00001",
        ts=datetime(2026, 11, 7, 10, 0, tzinfo=UTC),
        operador="Operador",
        audio_url="http://sim/media/NOTA-00001.wav",
        fotos=[],
        recorrida_id="REC-1",
    )


def _respuesta(mensaje_id: str) -> RespuestaOperador:
    return RespuestaOperador(
        id=str(uuid4()),
        ts=datetime(2026, 11, 7, 10, 1, tzinfo=UTC),
        mensaje_id=mensaje_id,
        operador="Operador",
        texto="No. La uno estaba en ciento doce kilos, no en ciento dos.",
        audio_url=None,
    )


def _esperar(condition, *, timeout: float = 5.0) -> bool:
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_la_nota_no_puede_corregirse_para_siempre(planta: dict) -> None:
    settings = Settings()
    repo = RepositorioFalso()
    cliente = ClienteFalso()
    servicio = ServicioParteVoz(
        settings,
        repo,
        almacenamiento=AlmacenamientoFalso(),
        transcriptor=TranscriptorFalso(),
        cliente=cliente,
        planta=planta,
        analisis=AnalisisFalso(),
    )
    servicio.iniciar()
    try:
        servicio.encolar_nota(_nota())
        assert _esperar(lambda: len(cliente.tablets) == 1), "no llegó el borrador"

        # Cada respuesta del operador genera un borrador nuevo, hasta el tope.
        for _ in range(settings.parte_max_correcciones):
            antes = len(cliente.tablets)
            servicio.encolar_respuesta(_respuesta(cliente.tablets[-1].id))
            assert _esperar(lambda n=antes: len(cliente.tablets) > n), (
                "no llegó el borrador nuevo"
            )

        # La respuesta que supera el tope no genera otro borrador: la nota
        # queda para revisión humana.
        antes = len(cliente.tablets)
        servicio.encolar_respuesta(_respuesta(cliente.tablets[-1].id))
        assert _esperar(lambda: "revision_humana" in repo.estados), "no se marcó para revisión"
        assert len(cliente.tablets) == antes
        assert len(cliente.tablets) == settings.parte_max_correcciones + 1
    finally:
        servicio.detener()
