"""Transcripción local de las notas de voz.

La lista cerrada de modelos todavía no está confirmada, así que el motor queda
detrás de un protocolo. El adaptador incluido usa ``faster-whisper`` en CPU
(modelo local, sin nube); si no está instalado o el modelo no está disponible,
el sistema sigue funcionando con una transcripción vacía y lo deja en el log.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

logger = logging.getLogger("planta.transcriber")


class ErrorTranscripcion(RuntimeError):
    """No se pudo transcribir una nota."""


class Transcriber(Protocol):
    """Contrato mínimo de cualquier motor de transcripción local."""

    def transcribir(self, ruta: Path) -> str: ...


class TranscriberNulo:
    """Fallback explícito: no inventa texto."""

    def transcribir(self, ruta: Path) -> str:  # noqa: ARG002
        return ""


class FasterWhisperTranscriber:
    """Adaptador local con ``faster-whisper`` (CTranslate2, CPU)."""

    def __init__(
        self,
        modelo: str = "small",
        *,
        dispositivo: str = "cpu",
        tipo_computo: str = "int8",
        idioma: str = "es",
        hilos_cpu: int = 2,
        descarga_local: bool = False,
        directorio_descarga: str | None = None,
        vocabulario: Callable[[], str] | None = None,
    ) -> None:
        self._modelo = modelo
        self._dispositivo = dispositivo
        self._tipo_computo = tipo_computo
        self._idioma = idioma
        self._hilos_cpu = hilos_cpu
        self._descarga_local = descarga_local
        self._directorio_descarga = directorio_descarga
        # El vocabulario de la planta (tareas y repuestos del catálogo) se le
        # pasa al decodificador: sin él, el ASR escribe "Chofitrante" y no se
        # puede comparar el cierre contra la orden.
        self._vocabulario = vocabulario
        self._instancia = None
        # El motor se usa desde dos hilos (las notas y los cierres de orden).
        # CTranslate2 no garantiza que dos inferencias al mismo tiempo sobre
        # la misma instancia sean seguras: se turnan.
        self._candado = threading.Lock()

    def _cargar(self):
        if self._instancia is not None:
            return self._instancia
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise ErrorTranscripcion(
                "faster-whisper no está instalado; instalá el extra 'asr'"
            ) from exc

        logger.info(
            "Cargando modelo ASR local: %s (device=%s, compute=%s)",
            self._modelo,
            self._dispositivo,
            self._tipo_computo,
        )
        self._instancia = WhisperModel(
            self._modelo,
            device=self._dispositivo,
            compute_type=self._tipo_computo,
            cpu_threads=self._hilos_cpu,
            download_root=self._directorio_descarga,
            local_files_only=self._descarga_local,
        )
        return self._instancia

    def transcribir(self, ruta: Path) -> str:
        modelo = self._cargar()
        with self._candado:
            segmentos, _info = modelo.transcribe(
                str(ruta),
                language=self._idioma,
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=False,
                without_timestamps=True,
                temperature=0.0,
                initial_prompt=self._prompt(),
            )
            texto = " ".join(segmento.text.strip() for segmento in segmentos).strip()
        logger.info("Transcripción de %s: %s", ruta.name, texto[:160])
        return texto

    def _prompt(self) -> str | None:
        """El vocabulario de la planta, si lo hay.

        Se arma al transcribir (y no al construir) porque el catálogo de
        manuales se carga después que el motor: así el prompt tiene los
        nombres reales de las tareas y de los repuestos.
        """
        if self._vocabulario is None:
            return None
        try:
            prompt = self._vocabulario()
        except Exception:  # noqa: BLE001 - el prompt es una ayuda, no un requisito
            logger.exception("No se pudo armar el vocabulario del ASR")
            return None
        return prompt or None

    def precargar(self) -> None:
        """Descarga/carga el modelo. Se usa en la build para dejarlo cacheado."""
        self._cargar()


def crear_transcriptor(
    motor: str = "faster-whisper",
    modelo: str = "small",
    *,
    descarga_local: bool = False,
    directorio_descarga: str | None = None,
    hilos_cpu: int = 2,
    vocabulario: Callable[[], str] | None = None,
) -> Transcriber:
    """Crea el motor configurado; si no se puede, degrada a nulo."""
    if motor in {"ninguno", "nulo", "off", ""}:
        return TranscriberNulo()
    if motor in {"faster-whisper", "faster_whisper", "whisper"}:
        return FasterWhisperTranscriber(
            modelo,
            descarga_local=descarga_local,
            directorio_descarga=directorio_descarga,
            hilos_cpu=hilos_cpu,
            vocabulario=vocabulario,
        )
    logger.warning("Motor de transcripción desconocido %r; se usa el nulo", motor)
    return TranscriberNulo()


def precargar_modelo(
    motor: str,
    modelo: str,
    directorio_descarga: str,
) -> Transcriber:
    """Descarga el modelo en la build para que el contenedor arranque local."""
    transcriptor = crear_transcriptor(
        motor,
        modelo,
        descarga_local=False,
        directorio_descarga=directorio_descarga,
    )
    precargar = getattr(transcriptor, "precargar", None)
    if not callable(precargar):
        raise ErrorTranscripcion(
            f"El motor {motor!r} no permite precargar el modelo {modelo!r}"
        )
    precargar()
    return transcriptor
