"""Configuración compartida del sistema PIA-3."""

from __future__ import annotations

from planta_contrato import Topicos
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Variables de entorno del sistema.

    Los nombres se resuelven en mayúsculas: ``sistema_id`` -> ``SISTEMA_ID``.
    """

    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    sistema_id: str = "pia3"

    mqtt_host: str = "broker"
    mqtt_port: int = 1883
    mqtt_usuario: str | None = None
    mqtt_password: str | None = None

    latido_topico: str = Topicos.ESTADO
    latido_intervalo_s: float = 30.0
    # Cada cuánto se sondea el enlace. El sondeo es más seguido que el latido
    # para avisar apenas se cae o se restablece el radioenlace.
    latido_sondeo_s: float = 5.0

    database_url: str = "postgresql+psycopg://pia3:pia3@postgres:5432/pia3"

    planta_api_url: str = "http://sim:8100"
    planta_api_timeout_s: float = 10.0
    historial_timeout_s: float = 2.0
    enlace_check_url: str | None = None
    enlace_timeout_s: float = 3.0

    # Almacenamiento local de audios y fotos (volumen Docker).
    datos_dir: str = "/datos"

    # Transcripción local. La lista cerrada de modelos sigue pendiente; el
    # motor y el modelo se cambian por entorno sin tocar el código.
    asr_motor: str = "faster-whisper"
    asr_modelo: str = "small"
    asr_local_only: bool = False
    asr_download_root: str | None = None
    asr_hilos_cpu: int = 2

    # Conversación de tablet.
    parte_max_intentos: int = 3

    # RAG (Fase 4). El embedder por defecto es determinista para que los tests
    # no descarguen un modelo; Docker usa `fastembed` y el modelo local.
    rag_motor: str = "memoria"
    rag_embedder: str = "hashing"
    rag_modelo: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    rag_dimension: int = 384
    rag_cache_dir: str | None = None
    rag_peso_lexico: float = 0.8
    rag_memoria_dias: int = 90

    log_level: str = "INFO"
