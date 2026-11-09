FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./

# Dependencias de ASR, RAG y modelos locales: capa independiente del código
# fuente. Así un cambio en `src/` no vuelve a descargar modelos ni wheels.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install "faster-whisper>=1.0,<2" "av>=11,<16" "fastembed>=0.7,<1"

# Descargar el modelo ASR local en la build (CPU, int8) y dejarlo en el mismo
# directorio que usa el contenedor (ASR_DOWNLOAD_ROOT=/modelos). Así el runtime
# puede trabajar con local_files_only=True, sin salir a internet.
#
# El modelo es `base`: es el de la lista cerrada de modelos del curso
# (`docs/Lista_de_Modelos.md`) y el que fija la
# restricción 04. `scripts/verificar_modelos.py` lo comprueba.
ARG ASR_MODELO=base
ENV ASR_MODELO=${ASR_MODELO} \
    ASR_DOWNLOAD_ROOT=/modelos
RUN python -c "from faster_whisper.utils import download_model; download_model('${ASR_MODELO}', cache_dir='/modelos'); print('Modelo ASR listo')"

# Modelo de embeddings del RAG (384 dims, multilingüe). Se prefetchea en la
# build para que el contenedor no necesite internet en runtime.
ARG RAG_MODELO=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
ENV RAG_MODELO=${RAG_MODELO} \
    RAG_EMBEDDER=fastembed \
    RAG_CACHE_DIR=/modelos/rag \
    FASTEMBED_CACHE_PATH=/modelos/rag
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='${RAG_MODELO}', cache_dir='/modelos/rag'); print('Modelo RAG listo')"

COPY src ./src
COPY scripts ./scripts

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install .[asr,rag]

COPY alembic.ini ./
COPY migrations ./migrations

RUN useradd --create-home --uid 10001 pia3
RUN mkdir -p /datos /modelos && chown -R pia3:pia3 /datos /modelos

USER pia3

CMD ["python", "-m", "planta.main"]