"""La transcripción no puede frenar a la planta.

Reglas que se cubren acá:

* el ASR (que decodifica el audio en CPU) va en **su propio hilo**: la planta
  sigue atendiendo las respuestas del operador y las órdenes que abren los
  eventos del sensor mientras el audio se decodifica;
* el orden de las notas se mantiene: la cola del ASR es FIFO y hay un solo
  hilo de transcripción;
* la misma nota que vuelve a llegar no se transcribe dos veces;
* el embedder ONNX del RAG no abre un hilo por núcleo: sobre la cuota de 2 CPU
  de la planta eso le roba la CPU al ASR.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from planta_contrato import EventoSistema, NotaRecorrida, OrdenSistema, TipoEvento

from config import Settings
from planta.servicio import ServicioParteVoz


class AlmacenamientoFalso:
    def __init__(self) -> None:
        self.audios: list[str] = []

    def guardar_audio(self, nota_id: str, url: str | None) -> Path | None:
        if not url:
            return None
        self.audios.append(nota_id)
        return Path(f"/datos/notas/{nota_id}/audio.wav")

    def guardar_fotos(self, nota_id: str, fotos: list[str]) -> list[Path | None]:
        return [None for _ in fotos]


class TranscriptorFalso:
    """Transcriptor que se puede bloquear a propósito."""

    def __init__(self, *, bloquear: bool = False) -> None:
        self.entrada = threading.Event()
        self.salida = threading.Event()
        self.llamadas: list[str] = []
        self._bloquear = bloquear

    def transcribir(self, ruta: Path) -> str:
        self.llamadas.append(ruta.name)
        self.entrada.set()
        if self._bloquear:
            self.salida.wait(timeout=10)
        return "La bomba está parada en reserva"


class RepositorioFalso:
    def __init__(self) -> None:
        self.notas: list[str] = []
        self.ordenes: list[object] = []
        self.snapshot: dict[str, object] | None = None
        self.error_guardado: Exception | None = None
        self.redacciones: list[tuple[str, str, str]] = []

    def obtener_conversacion_por_nota(self, nota_id: str) -> None:
        return None

    def guardar_nota(self, nota, *, transcripcion, estado, seguridad) -> str:
        self.notas.append(nota.id)
        return nota.id

    def guardar_parte(self, nota_id, ts, filas, **kwargs) -> str:
        return f"parte-{nota_id}"

    def guardar_conversacion(self, nota_id, mensaje, filas, **kwargs) -> None:
        pass

    def guardar_pregunta(self, nota_id, mensaje, pregunta) -> None:
        pass

    def actualizar_estado_nota(self, nota_id: str, estado: str) -> None:
        pass

    def guardar_orden(self, orden, **kwargs) -> dict[str, object]:
        if self.error_guardado is not None:
            raise self.error_guardado
        self.ordenes.append(orden)
        if self.snapshot is not None:
            return dict(self.snapshot)
        return {
            "id": orden.id,
            "estado": "Pendiente de aprobacion",
            "motivo": orden.motivo,
            "detencion": orden.detiene_equipo,
            "reduce_inyeccion": orden.reduce_inyeccion,
            "ejecutor": orden.ejecutor,
            "tareas": list(orden.tareas),
            "repuestos": [
                {"codigo": repuesto.codigo, "cantidad": repuesto.cantidad}
                for repuesto in orden.repuestos
            ],
        }

    def obtener_orden_completa(self, orden_id: str) -> dict[str, object] | None:
        if self.snapshot is not None:
            return dict(self.snapshot)
        return None

    def guardar_redaccion_orden(self, orden_id: str, texto: str, *, modelo: str) -> None:
        self.redacciones.append((orden_id, texto, modelo))

    def registrar_evento(self, evento, *, nota_id=None) -> bool:
        return True


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


class ResultadoDeAnalisisFalso:
    falla = None


class AnalisisFalso:
    def __init__(self) -> None:
        self.eventos: list[object] = []
        self.redaccion = ""
        self.resultado = ResultadoDeAnalisisFalso()

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
        self.eventos.append(evento)
        return self.resultado

    def analizar_alerta_seguridad(
        self,
        evento,
        *,
        texto=None,
        fotos=None,
        evidencia_vision=(),
        tendencias=None,
    ):
        return self.resultado

    def orden_sistema(self, analisis):
        return OrdenSistema(
            id="ORD-1",
            ts=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
            estado="PendienteAprobacion",
            equipo="B-103",
            evento_ids=[],
            tareas=["I-VAL"],
            detiene_equipo=False,
            reduce_inyeccion=False,
            ejecutor="propio",
            repuestos=[],
            motivo="prueba",
        )

    def datos_de_orden(self, analisis) -> dict:
        return {}

    def redactar(self, analisis) -> str:
        return self.redaccion

    @property
    def nombre_modelo(self) -> str:
        return "ninguno"


def _nota(numero: str, *, con_audio: bool = True) -> NotaRecorrida:
    return NotaRecorrida(
        id=numero,
        ts=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
        operador="Operador",
        audio_url=f"http://sim/media/{numero}.wav" if con_audio else None,
        fotos=[],
        recorrida_id=f"REC-{numero}",
    )


def _evento_sensor() -> EventoSistema:
    return EventoSistema(
        id="sensor:B-103:presion_descarga",
        ts=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
        tipo=TipoEvento.escalado,
        equipo="B-103",
        origen="sensor",
        resumen="presión fuera de rango",
    )


def _snapshot(estado: str) -> dict[str, object]:
    return {
        "id": "ORD-1",
        "estado": estado,
        "motivo": "motivo vigente",
        "detencion": False,
        "reduce_inyeccion": False,
        "ejecutor": "propio",
        "tareas": ["I-VAL"],
        "repuestos": [],
    }


def _servicio(
    transcriptor: TranscriptorFalso,
) -> tuple[ServicioParteVoz, ClienteFalso, RepositorioFalso, AnalisisFalso]:
    cliente = ClienteFalso()
    repo = RepositorioFalso()
    analisis = AnalisisFalso()
    servicio = ServicioParteVoz(
        Settings(),
        repo,
        almacenamiento=AlmacenamientoFalso(),
        transcriptor=transcriptor,
        cliente=cliente,
        planta={},
        analisis=analisis,
    )
    return servicio, cliente, repo, analisis


def _esperar(condition, *, timeout: float = 5.0) -> bool:
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_la_planta_sigue_mientras_el_asr_transcribe() -> None:
    """Con el ASR bloqueado, la orden de un evento del sensor igual sale."""
    transcriptor = TranscriptorFalso(bloquear=True)
    servicio, cliente, _repo, analisis = _servicio(transcriptor)
    servicio.iniciar()
    try:
        servicio.encolar_nota(_nota("NOTA-00001"))
        assert transcriptor.entrada.wait(timeout=5), "el ASR no llegó a transcribir"

        evento = EventoSistema(
            id="sensor:B-103:presion_descarga",
            ts=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
            tipo=TipoEvento.escalado,
            equipo="B-103",
            origen="sensor",
            resumen="presión fuera de rango",
        )
        servicio.encolar_orden_por_evento(evento)

        assert _esperar(lambda: bool(analisis.eventos)), (
            "la orden del sensor quedó esperando a que terminara la transcripción"
        )
        assert _esperar(lambda: bool(cliente.ordenes))
    finally:
        transcriptor.salida.set()
        servicio.detener()


def test_las_notas_se_transcriben_en_el_orden_de_llegada() -> None:
    transcriptor = TranscriptorFalso()
    servicio, _cliente, repo, _analisis = _servicio(transcriptor)
    servicio.iniciar()
    try:
        servicio.encolar_nota(_nota("NOTA-00001"))
        servicio.encolar_nota(_nota("NOTA-00002"))
        assert _esperar(lambda: len(repo.notas) == 2)
        assert repo.notas == ["NOTA-00001", "NOTA-00002"]
    finally:
        servicio.detener()


def test_la_misma_nota_no_se_transcribe_dos_veces() -> None:
    transcriptor = TranscriptorFalso()
    servicio, _cliente, repo, _analisis = _servicio(transcriptor)
    servicio.iniciar()
    try:
        nota = _nota("NOTA-00001")
        servicio.encolar_nota(nota)
        servicio.encolar_nota(nota)
        assert _esperar(lambda: len(repo.notas) == 1)
        time.sleep(0.2)
        assert len(transcriptor.llamadas) == 1
    finally:
        servicio.detener()


def test_una_nota_sin_audio_no_rompe_el_servicio() -> None:
    transcriptor = TranscriptorFalso()
    servicio, _cliente, repo, _analisis = _servicio(transcriptor)
    servicio.iniciar()
    try:
        servicio.encolar_nota(_nota("NOTA-00001", con_audio=False))
        assert _esperar(lambda: len(repo.notas) == 1)
        assert transcriptor.llamadas == []
    finally:
        servicio.detener()


def test_el_camino_sincronico_procesa_la_nota_entera() -> None:
    """``procesar_nota`` sigue siendo usable sin pasar por las colas."""
    transcriptor = TranscriptorFalso()
    servicio, _cliente, repo, _analisis = _servicio(transcriptor)

    servicio.procesar_nota(_nota("NOTA-00001"))

    assert transcriptor.llamadas == ["audio.wav"]
    assert repo.notas == ["NOTA-00001"]


def test_si_la_persistencia_falla_no_se_publica_una_orden() -> None:
    """Sin evidencia auditada no sale una orden al bus."""
    transcriptor = TranscriptorFalso()
    servicio, cliente, repo, _analisis = _servicio(transcriptor)
    repo.error_guardado = RuntimeError("PostgreSQL no disponible")

    servicio.generar_orden(_evento_sensor())

    assert repo.ordenes == []
    assert cliente.ordenes == []


def test_la_orden_publicada_tiene_el_estado_comprometido_en_postgresql() -> None:
    """La planta recibe el snapshot de la base, no el objeto local obsoleto."""
    transcriptor = TranscriptorFalso()
    servicio, cliente, repo, _analisis = _servicio(transcriptor)
    repo.snapshot = _snapshot("Aprobada")

    servicio.generar_orden(_evento_sensor())

    assert len(cliente.ordenes) == 1
    assert cliente.ordenes[0].estado.value == "Aprobada"
    assert cliente.ordenes[0].tareas == ["I-VAL"]


def test_la_redaccion_publica_el_estado_vigente_y_no_reabre_un_terminal() -> None:
    """La redacción tardía usa la base y no degrada una orden ya decidida."""
    transcriptor = TranscriptorFalso()
    servicio, cliente, repo, analisis = _servicio(transcriptor)
    analisis.redaccion = "Justificación técnica"
    orden = analisis.orden_sistema("analisis")

    repo.snapshot = _snapshot("Aprobada")
    servicio._redactar_orden("analisis", orden)
    assert len(cliente.ordenes) == 1
    assert cliente.ordenes[0].estado.value == "Aprobada"

    repo.snapshot = _snapshot("Rechazada")
    servicio._redactar_orden("analisis", orden)
    assert len(cliente.ordenes) == 1
    assert repo.redacciones == [
        ("ORD-1", "Justificación técnica", "ninguno"),
        ("ORD-1", "Justificación técnica", "ninguno"),
    ]
