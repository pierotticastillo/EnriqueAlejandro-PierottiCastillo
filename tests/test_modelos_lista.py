"""Tests de la lista cerrada de modelos (restricción 04 del curso).

Lo que se comprueba acá es que el sistema no pueda usar un modelo que no esté
en la lista, y que la lista sea la que el curso publicó: ASR
``faster-whisper-base``, embeddings ``paraphrase-multilingual-MiniLM-L12-v2``
(384 dims) y LLM ``Qwen2.5-3B-Instruct`` Q4_K_M. El modelo de nube con visión
no tiene identificador publicado: queda deshabilitado.
"""

from __future__ import annotations

from pathlib import Path

from config import Settings
from modelos_lista import (
    CLAVE_ASR,
    CLAVE_EMBEDDINGS,
    CLAVE_LLM,
    CLAVE_VISION,
    LISTA_CERRADA,
    por_clave,
    tabla,
)
from verificacion_modelos import (
    ADVERTENCIA,
    DESHABILITADO,
    FUERA_DE_LISTA,
    NO_DISPONIBLE,
    OK,
    verificar_configuracion,
    verificar_runtime,
)


class RespuestaFalsa:
    """Una respuesta HTTP de mentira."""

    def __init__(self, datos: dict, status_code: int = 200) -> None:
        self._datos = datos
        self.status_code = status_code

    def json(self) -> dict:
        return self._datos

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class ClienteFalso:
    """Un cliente HTTP que responde como Ollama."""

    def __init__(self, *, cuantizacion: str = "Q4_K_M", modelos: list[str] | None = None) -> None:
        self._cuantizacion = cuantizacion
        self._modelos = modelos if modelos is not None else ["qwen2.5:3b-instruct"]
        self.consultas: list[str] = []

    def get(self, url: str, **kwargs) -> RespuestaFalsa:
        self.consultas.append(url)
        return RespuestaFalsa({"models": [{"name": nombre} for nombre in self._modelos]})

    def post(self, url: str, json: dict | None = None, **kwargs) -> RespuestaFalsa:
        self.consultas.append(url)
        nombre = (json or {}).get("model")
        if nombre not in self._modelos:
            return RespuestaFalsa({}, status_code=404)
        return RespuestaFalsa(
            {
                "details": {
                    "quantization_level": self._cuantizacion,
                    "parameter_size": "3.1B",
                }
            }
        )


def _settings(**cambios) -> Settings:
    base = {
        "asr_modelo": "base",
        "asr_local_only": True,
        "asr_download_root": None,
        "rag_modelo": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "rag_dimension": 384,
        "rag_cache_dir": None,
        "llm_activo": True,
        "ollama_modelo": "qwen2.5:3b-instruct",
        "ollama_url": "http://ollama:11434",
        "nvidia_api_key": "",
        "nvidia_modelo": "",
    }
    base.update(cambios)
    # `_env_file=None`: los tests no dependen del `.env` local de quien los corre.
    return Settings(_env_file=None, **base)


def _estado(resultados, clave: str) -> str:
    return next(resultado.estado for resultado in resultados if resultado.clave == clave)


# ------------------------------------------------------------------- la lista


def test_la_lista_tiene_los_cuatro_modelos_del_curso() -> None:
    claves = [modelo.clave for modelo in LISTA_CERRADA]
    assert claves == [CLAVE_ASR, CLAVE_EMBEDDINGS, CLAVE_LLM, CLAVE_VISION]


def test_los_modelos_locales_son_los_de_la_lista_publicada() -> None:
    assert por_clave(CLAVE_ASR).nombre == "faster-whisper-base"
    assert por_clave(CLAVE_EMBEDDINGS).nombre == "paraphrase-multilingual-MiniLM-L12-v2"
    assert por_clave(CLAVE_EMBEDDINGS).dimension == 384
    assert por_clave(CLAVE_LLM).nombre == "Qwen2.5-3B-Instruct"
    assert por_clave(CLAVE_LLM).cuantizacion == "Q4_K_M"


def test_el_modelo_de_vision_es_el_nemotron_vigente() -> None:
    """El modelo de visión es el Nemotron con visión que sigue en el catálogo.

    El que la lista publicada dejó sin identificar
    (`nvidia/nemotron-nano-12b-v2-vl`) fue retirado por NVIDIA: la API responde
    410 Gone (fin de vida 2026-08-26). Se usa el Nemotron con visión vigente.
    """
    vision = por_clave(CLAVE_VISION)
    assert vision.habilitado is True
    assert vision.ubicacion == "nube"
    assert vision.identificadores == ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",)
    assert "410" in vision.nota or "retirado" in vision.nota


def test_la_tabla_alcanza_para_documentar() -> None:
    filas = tabla()
    assert len(filas) == len(LISTA_CERRADA)
    assert {fila["clave"] for fila in filas} == {
        CLAVE_ASR,
        CLAVE_EMBEDDINGS,
        CLAVE_LLM,
        CLAVE_VISION,
    }


# -------------------------------------------------------- configuración vs lista


def test_el_asr_de_la_lista_pasa() -> None:
    resultados = verificar_configuracion(_settings())
    assert _estado(resultados, CLAVE_ASR) == OK


def test_un_asr_que_no_es_de_la_lista_se_reporta() -> None:
    resultados = verificar_configuracion(_settings(asr_modelo="small"))
    assert _estado(resultados, CLAVE_ASR) == FUERA_DE_LISTA


def test_el_asr_de_la_lista_pero_que_puede_bajar_se_advierte() -> None:
    resultados = verificar_configuracion(_settings(asr_local_only=False))
    assert _estado(resultados, CLAVE_ASR) == ADVERTENCIA


def test_un_embedder_con_otras_dimensions_no_pasa() -> None:
    resultados = verificar_configuracion(_settings(rag_dimension=768))
    assert _estado(resultados, CLAVE_EMBEDDINGS) == FUERA_DE_LISTA


def test_un_llm_que_no_es_de_la_lista_se_reporta() -> None:
    resultados = verificar_configuracion(_settings(ollama_modelo="llama3:8b"))
    assert _estado(resultados, CLAVE_LLM) == FUERA_DE_LISTA


def test_sin_llm_activo_el_sistema_no_depende_del_modelo() -> None:
    resultados = verificar_configuracion(_settings(llm_activo=False))
    assert _estado(resultados, CLAVE_LLM) == DESHABILITADO


# ------------------------------------------------------- el modelo de visión


def test_sin_clave_la_vision_sigue_deshabilitada() -> None:
    resultados = verificar_configuracion(_settings())
    assert _estado(resultados, CLAVE_VISION) == DESHABILITADO


def test_la_clave_sola_no_habilita_el_modelo() -> None:
    """Con la clave puesta pero sin identificador, el modelo sigue sin usarse.

    La lista publicada no dice cuál es el modelo de visión: hasta que el curso
    lo diga, la clave no habilita nada.
    """
    resultados = verificar_configuracion(_settings(nvidia_api_key="nvapi-secreto"))
    assert _estado(resultados, CLAVE_VISION) == ADVERTENCIA


def test_un_modelo_de_vision_fuera_de_la_lista_se_reporta() -> None:
    """Si se configura un modelo de visión, tiene que estar en la lista."""
    resultados = verificar_configuracion(
        _settings(nvidia_api_key="nvapi-secreto", nvidia_modelo="nvidia/llama-3.2-90b-vision")
    )
    assert _estado(resultados, CLAVE_VISION) == FUERA_DE_LISTA


def test_el_modelo_de_vision_de_la_lista_pasa() -> None:
    resultados = verificar_configuracion(
        _settings(
            nvidia_api_key="nvapi-secreto",
            nvidia_modelo="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        )
    )
    assert _estado(resultados, CLAVE_VISION) == OK


def test_el_modelo_retirado_por_nvidia_no_pasa() -> None:
    """`nvidia/nemotron-nano-12b-v2-vl` dio 410 Gone: ya no es un modelo."""
    resultados = verificar_configuracion(
        _settings(nvidia_api_key="nvapi-secreto", nvidia_modelo="nvidia/nemotron-nano-12b-v2-vl")
    )
    assert _estado(resultados, CLAVE_VISION) == FUERA_DE_LISTA


def test_la_clave_de_nvidia_no_aparece_en_el_log() -> None:
    """La clave no se imprime nunca: sólo se dice si hay o no."""
    clave = "nvapi-secreto"
    resultados = verificar_configuracion(
        _settings(nvidia_api_key=clave, nvidia_modelo="nvidia/llama-3.2-90b-vision")
    )
    for resultado in resultados:
        assert clave not in resultado.detalle


# -------------------------------------------------------------- runtime vs lista


def test_el_runtime_ok_con_el_modelo_y_la_cuantizacion_de_la_lista(tmp_path: Path) -> None:
    (tmp_path / "models--Systran--faster-whisper-base").mkdir()
    (tmp_path / "models--Qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q").mkdir()
    resultados = verificar_runtime(
        _settings(
            asr_download_root=str(tmp_path),
            rag_cache_dir=str(tmp_path),
        ),
        ClienteFalso(),
    )
    assert _estado(resultados, CLAVE_ASR) == OK
    assert _estado(resultados, CLAVE_EMBEDDINGS) == OK
    assert _estado(resultados, CLAVE_LLM) == OK


def test_una_cuantizacion_distinta_no_pasa(tmp_path: Path) -> None:
    (tmp_path / "models--Systran--faster-whisper-base").mkdir()
    (tmp_path / "models--Qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q").mkdir()
    resultados = verificar_runtime(
        _settings(
            asr_download_root=str(tmp_path),
            rag_cache_dir=str(tmp_path),
        ),
        ClienteFalso(cuantizacion="Q8_0"),
    )
    assert _estado(resultados, CLAVE_LLM) == NO_DISPONIBLE


def test_un_modelo_que_no_esta_en_ollama_no_pasa(tmp_path: Path) -> None:
    (tmp_path / "models--Systran--faster-whisper-base").mkdir()
    (tmp_path / "models--Qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q").mkdir()
    resultados = verificar_runtime(
        _settings(
            asr_download_root=str(tmp_path),
            rag_cache_dir=str(tmp_path),
        ),
        ClienteFalso(modelos=["phi3:latest"]),
    )
    assert _estado(resultados, CLAVE_LLM) == NO_DISPONIBLE


def test_un_modelo_no_descargado_no_pasa(tmp_path: Path) -> None:
    resultados = verificar_runtime(
        _settings(asr_download_root=str(tmp_path), rag_cache_dir=str(tmp_path)),
        ClienteFalso(),
    )
    assert _estado(resultados, CLAVE_ASR) == NO_DISPONIBLE
    assert _estado(resultados, CLAVE_EMBEDDINGS) == NO_DISPONIBLE
