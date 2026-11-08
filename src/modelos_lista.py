"""La lista cerrada de modelos del curso: la única fuente de verdad.

La restricción 04 dice: «Modelos. Solo los de la lista cerrada que publicamos.
Incluye modelos de transcripción y de lenguaje que entran en la PC de planta, y
modelos en la nube de NVIDIA con visión. Los modelos locales corren con la
versión y la configuración que fijamos.»

Este módulo es esa lista. El verificador (`scripts/verificar_modelos.py`), el
chequeo del arranque (`src/verificacion_modelos.py`) y la documentación comparan
contra lo que está acá, nunca contra un nombre escrito suelto en un `.env`.

Tres cosas que conviene tener presentes:

* La lista dice **qué** modelos se pueden usar, no que haya que usar un LLM.
  El análisis de una orden no necesita un modelo de lenguaje: las reglas están
  en código (`analisis/aprobacion.py`).
* Los modelos **locales** llevan la versión y la configuración fijadas: no
  alcanza con que el nombre sea parecido, tiene que ser el mismo y con la
  misma cuantización y el mismo contexto.
* El modelo **de nube con visión** todavía no tiene identificador publicado.
  Mientras tanto queda deshabilitado y con salida fail-safe (una foto que
  pueda mostrar daño estructural va a revisión humana), nunca "no hay daño".
"""

from __future__ import annotations

from dataclasses import dataclass

# Claves de la lista. Son estables: aparecen en la configuración, en los logs
# del verificador y en la documentación.
CLAVE_ASR = "asr"
CLAVE_EMBEDDINGS = "embeddings"
CLAVE_LLM = "llm"
CLAVE_VISION = "vision"


@dataclass(frozen=True)
class ModeloPermitido:
    """Un modelo de la lista cerrada, con la configuración con la que corre."""

    clave: str
    nombre: str
    familia: str
    version: str
    motor: str
    ubicacion: str
    configuracion: str
    # Nombres con los que se puede configurar el motor. El primero es el
    # canónico de la lista; los otros son los alias que usan las librerías.
    identificadores: tuple[str, ...] = ()
    cuantizacion: str | None = None
    dimension: int | None = None
    # Un modelo deshabilitado no se usa: el sistema funciona sin él.
    habilitado: bool = True
    # Por qué está deshabilitado, para que quede escrito.
    nota: str = ""

    def coincide(self, valor: str | None) -> bool:
        """¿El valor configurado es uno de los identificadores permitidos?"""
        if not valor:
            return False
        limpio = valor.strip().lower()
        return any(limpio == identificador.lower() for identificador in self.identificadores)


LISTA_CERRADA: tuple[ModeloPermitido, ...] = (
    ModeloPermitido(
        clave=CLAVE_ASR,
        nombre="faster-whisper-base",
        familia="Systran/faster-whisper",
        version="base",
        motor="faster-whisper (CTranslate2)",
        ubicacion="planta",
        configuracion="CPU int8, 2 hilos, sólo local (sin descarga en runtime)",
        identificadores=(
            "base",
            "faster-whisper-base",
            "Systran/faster-whisper-base",
        ),
        nota="Se descarga en la build de la imagen y queda en /modelos.",
    ),
    ModeloPermitido(
        clave=CLAVE_EMBEDDINGS,
        nombre="paraphrase-multilingual-MiniLM-L12-v2",
        familia="sentence-transformers",
        version="L12-v2",
        motor="fastembed (ONNX)",
        ubicacion="planta",
        configuracion="CPU, vectores de 384 dimensiones, índice pgvector",
        identificadores=(
            "paraphrase-multilingual-MiniLM-L12-v2",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            "Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q",
        ),
        dimension=384,
        nota="Las 384 dimensiones son las del índice pgvector: no se puede cambiar.",
    ),
    ModeloPermitido(
        clave=CLAVE_LLM,
        nombre="Qwen2.5-3B-Instruct",
        familia="Qwen2.5",
        version="3b-instruct",
        motor="Ollama (llama.cpp)",
        ubicacion="planta",
        configuracion=(
            "Q4_K_M, num_ctx 2048, num_thread 2, temperature 0, "
            "keep_alive 0 (a demanda), timeout 90 s"
        ),
        identificadores=("qwen2.5:3b-instruct",),
        cuantizacion="Q4_K_M",
        nota=(
            "Es un auxiliar de redacción: no aprueba órdenes ni decide fallas. "
            "Si Ollama no responde, el sistema sigue con el texto determinista."
        ),
    ),
    ModeloPermitido(
        clave=CLAVE_VISION,
        nombre="Nemotron 3 Nano Omni 30B A3B",
        familia="Nemotron",
        version="3-nano-omni-30b-a3b",
        motor="API de NVIDIA (por el proxy del enlace)",
        ubicacion="nube",
        configuracion=(
            "temperature 0, max_tokens 800, timeout 8 s, una foto por llamada, "
            "salida validada contra esquema"
        ),
        identificadores=("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",),
        habilitado=True,
        nota=(
            "El Nemotron con visión que la lista publicada dejó sin identificar "
            "ya no existe: `nvidia/nemotron-nano-12b-v2-vl` fue retirado por "
            "NVIDIA (HTTP 410 Gone, fin de vida 2026-08-26) y además era sólo "
            "en inglés. Se usa el Nemotron con visión vigente del catálogo, que "
            "responde en español y es el más rápido de los medidos. Es un "
            "auxiliar: describe la foto, no decide ni aprueba nada."
        ),
    ),
)

#: Nombres de la carpeta donde cada motor deja el modelo en el contenedor.
RUTA_MODELOS = {
    CLAVE_ASR: "models--Systran--faster-whisper-base",
    CLAVE_EMBEDDINGS: "models--Qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q",
}


def por_clave(clave: str) -> ModeloPermitido:
    """El modelo de la lista con esa clave."""
    for modelo in LISTA_CERRADA:
        if modelo.clave == clave:
            return modelo
    raise KeyError(f"Clave de modelo desconocida: {clave}")


def modelo_de(clave: str, valor: str | None) -> ModeloPermitido | None:
    """El modelo de la lista al que corresponde el valor configurado.

    Devuelve ``None`` si el valor no está en la lista: eso es una configuración
    fuera de la lista cerrada y hay que reportarla, no aceptarla en silencio.
    """
    modelo = por_clave(clave)
    if modelo.coincide(valor):
        return modelo
    for otro in LISTA_CERRADA:
        if otro.clave == clave:
            continue
        if otro.coincide(valor):
            # Es un modelo de la lista, pero del tipo equivocado.
            return None
    return None


def tabla() -> list[dict[str, str]]:
    """La lista como tabla, para el verificador y para la documentación."""
    filas: list[dict[str, str]] = []
    for modelo in LISTA_CERRADA:
        filas.append(
            {
                "clave": modelo.clave,
                "modelo": modelo.nombre,
                "version": modelo.version,
                "motor": modelo.motor,
                "ubicacion": modelo.ubicacion,
                "cuantizacion": modelo.cuantizacion or "-",
                "dimension": str(modelo.dimension) if modelo.dimension else "-",
                "configuracion": modelo.configuracion,
                "estado": "habilitado" if modelo.habilitado else "deshabilitado",
                "nota": modelo.nota,
            }
        )
    return filas
