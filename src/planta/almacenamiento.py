"""Descarga y almacenamiento local de audios y fotos de la planta."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx

logger = logging.getLogger("planta.almacenamiento")


def _seguro(texto: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", texto)[:120] or "nota"


class Almacenamiento:
    """Guarda los binarios bajo ``datos/notas/<nota>/`` (volumen Docker)."""

    def __init__(
        self,
        base_dir: str | Path,
        api_url: str,
        *,
        cliente: httpx.Client | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.api_url = api_url.rstrip("/")
        self._cliente = cliente
        self._timeout_s = timeout_s

    def carpeta_nota(self, nota_id: str) -> Path:
        return self.base_dir / "notas" / _seguro(nota_id)

    def guardar_audio(self, nota_id: str, audio_url: str | None) -> Path | None:
        if not audio_url:
            return None
        destino = self.carpeta_nota(nota_id) / "audio.wav"
        return self._descargar(audio_url, destino)

    def guardar_fotos(self, nota_id: str, fotos: list[str]) -> list[Path | None]:
        salida: list[Path | None] = []
        for indice, url in enumerate(fotos):
            sufijo = Path(url.split("?", 1)[0]).suffix or ".jpg"
            destino = self.carpeta_nota(nota_id) / f"foto_{indice:02d}{sufijo}"
            salida.append(self._descargar(url, destino))
        return salida

    def _descargar(self, url: str, destino: Path) -> Path | None:
        destino.parent.mkdir(parents=True, exist_ok=True)
        if destino.exists() and destino.stat().st_size > 0:
            return destino

        completa = url if url.startswith(("http://", "https://")) else f"{self.api_url}{url}"
        try:
            if self._cliente is not None:
                respuesta = self._cliente.get(completa, timeout=self._timeout_s)
            else:
                with httpx.Client(timeout=self._timeout_s, trust_env=True) as cliente:
                    respuesta = cliente.get(completa)
            respuesta.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("No se pudo descargar %s: %s", completa, exc)
            return None

        temporal = destino.with_suffix(destino.suffix + ".tmp")
        temporal.write_bytes(respuesta.content)
        temporal.replace(destino)
        logger.info("Guardado %s (%s bytes)", destino, len(respuesta.content))
        return destino
