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

    # Transcripción local. El motor y el modelo se cambian por entorno sin
    # tocar el código, pero el modelo tiene que ser el de la lista cerrada
    # (`faster-whisper-base`); el verificador lo comprueba al arrancar.
    asr_motor: str = "faster-whisper"
    asr_modelo: str = "base"
    asr_local_only: bool = True
    asr_download_root: str | None = None
    asr_hilos_cpu: int = 2

    # Conversación de tablet.
    parte_max_intentos: int = 3
    # Cuántas correcciones del operador se aceptan para una misma nota. Con
    # el tope, una nota que no converge (el operador y el sistema no se
    # ponen de acuerdo) se deja para revisión humana en vez de seguir
    # consumiendo tiempo de planta en un bucle.
    parte_max_correcciones: int = 5

    # RAG (Fase 4). El embedder por defecto es determinista para que los tests
    # no descarguen un modelo; Docker usa `fastembed` y el modelo local.
    rag_motor: str = "memoria"
    rag_embedder: str = "hashing"
    rag_modelo: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    rag_dimension: int = 384
    rag_cache_dir: str | None = None
    rag_peso_lexico: float = 0.8
    rag_memoria_dias: int = 90
    # Hilos del embedder ONNX. El runtime de ONNX crea un hilo por núcleo si
    # no se le dice otra cosa: en una planta de 2 CPU eso deja al ASR sin
    # cuota y cada transcripción pasa de 1,5 s a más de 30 s. Con un hilo el
    # indexado del manual tarda lo mismo (es de fondo) y no frena la planta.
    rag_hilos: int = 1

    # LLM local (Ollama). Es un auxiliar de redacción: redacta la justificación
    # técnica de la orden, no decide. Si Ollama no está o no responde, el
    # sistema sigue con el texto determinista, así que el sistema no depende
    # de que este modelo esté disponible.
    llm_activo: bool = False
    ollama_url: str = "http://host.docker.internal:11434"
    # Proxy para la llamada al modelo local. En la simulación, el contenedor de
    # la planta está en redes internas y no tiene ruta directa al host: la
    # única salida sancionada es el proxy del enlace (`enlace:3128`). En la PC
    # de planta real el Ollama corre en localhost y el proxy va vacío.
    ollama_proxy: str | None = None
    ollama_modelo: str = "qwen2.5:3b-instruct"
    ollama_num_ctx: int = 2048
    ollama_num_thread: int = 2
    # Timeout de la llamada. Medido en la simulación (el Ollama del host se
    # alcanza por el proxy del enlace): ~40 s por vuelta con `keep_alive=0`,
    # porque el modelo se carga en cada llamada (~9 s) y genera a ~8 tok/s.
    # En la PC de planta real (Ollama en localhost) la vuelta es mucho más
    # corta. La redacción va en un hilo aparte, así que no frena la planta.
    ollama_timeout_s: float = 90.0
    ollama_seed: int = 42
    # 0 = el modelo se descarga de memoria en cuanto termina de responder.
    # Con 8 GB de RAM en la planta no puede quedar residente.
    ollama_keep_alive: int = 0

    # Modelo de nube con visión (NVIDIA). Es un auxiliar: **describe** la foto
    # para que quien la revise sepa qué está mirando. No decide, no aprueba y
    # nunca afirma que "no hay daño": si no puede responder, el resultado queda
    # indeterminado y la foto sigue yendo a revisión humana.
    #
    # La llamada sale por el proxy del enlace, que es la única salida
    # sancionada de la planta (las redes del contenedor son internas).
    nvidia_api_key: str = ""
    nvidia_modelo: str = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
    nvidia_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_proxy: str | None = None
    # El modelo es de razonamiento: con un tope chico se le va todo en
    # razonamiento y la respuesta final queda cortada. Medido: ~190 tokens de
    # razonamiento más la respuesta.
    nvidia_max_tokens: int = 800
    # Timeout corto a propósito: el aviso de seguridad no puede esperar al
    # modelo de visión. Medido: ~2 s por foto; si la red se cae, el aviso sale
    # igual (fail-safe) a los 8 s.
    nvidia_timeout_s: float = 8.0
    nvidia_temperature: float = 0.0

    log_level: str = "INFO"
