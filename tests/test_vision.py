"""Tests del modelo de nube con visión (NVIDIA).

Lo que se comprueba acá es lo importante: el modelo **describe** la foto y no
decide nada. Si falta la clave, si la red no responde, si la salida no valida
o si el modelo no es el de la lista cerrada, el resultado queda
**indeterminado** — nunca "no hay daño".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from analisis.vision import (
    ClienteVision,
    ResultadoVision,
    SalidaVision,
    crear_cliente_vision,
)
from config import Settings


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
    """Un cliente HTTP que responde como la API de NVIDIA."""

    def __init__(
        self,
        *,
        contenido: str | None = '{"danio_estructural": true, "descripcion": "grieta"}',
        status_code: int = 200,
        razonamiento: str | None = None,
    ) -> None:
        self._contenido = contenido
        self._status_code = status_code
        self._razonamiento = razonamiento
        self.llamadas: list[dict] = []
        self.cabeceras: list[dict] = []

    def post(self, url: str, json: dict | None = None, headers: dict | None = None, **kwargs):
        self.llamadas.append({"url": url, "json": json or {}})
        self.cabeceras.append(headers or {})
        if self._status_code != 200:
            return RespuestaFalsa({}, status_code=self._status_code)
        mensaje: dict[str, Any] = {"role": "assistant", "content": self._contenido}
        if self._razonamiento is not None:
            mensaje["reasoning_content"] = self._razonamiento
        return RespuestaFalsa(
            {
                "choices": [{"message": mensaje, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 857, "completion_tokens": 210},
            }
        )


def _cliente(cliente_http: Any = None, **cambios) -> ClienteVision:
    base = {
        "url": "https://integrate.api.nvidia.com/v1",
        "modelo": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "api_key": "nvapi-secreto",
        "max_tokens": 800,
        "timeout_s": 8.0,
        "temperature": 0.0,
        "proxy": "http://enlace:3128",
    }
    base.update(cambios)
    return ClienteVision(cliente=cliente_http, **base)


def _foto(tmp_path: Path) -> Path:
    ruta = tmp_path / "foto.jpg"
    ruta.write_bytes(b"\xff\xd8\xff\xe0 datos de foto")
    return ruta


def _settings(**cambios) -> Settings:
    base = {
        "nvidia_api_key": "nvapi-secreto",
        "nvidia_modelo": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "nvidia_url": "https://integrate.api.nvidia.com/v1",
        "nvidia_proxy": "http://enlace:3128",
        "nvidia_max_tokens": 800,
        "nvidia_timeout_s": 8.0,
        "nvidia_temperature": 0.0,
    }
    base.update(cambios)
    return Settings(_env_file=None, **base)


# ------------------------------------------------------------------- el cliente


def test_describe_la_foto(tmp_path: Path) -> None:
    cliente_http = ClienteFalso()
    resultado = _cliente(cliente_http).describir(_foto(tmp_path))

    assert resultado.estado == "ok"
    assert resultado.danio_estructural is True
    assert resultado.descripcion == "grieta"
    assert resultado.hay_danio is True
    assert resultado.tokens_prompt == 857
    assert resultado.tokens_completion == 210


def test_la_foto_viaja_como_data_url(tmp_path: Path) -> None:
    cliente_http = ClienteFalso()
    _cliente(cliente_http).describir(_foto(tmp_path))

    contenido = cliente_http.llamadas[0]["json"]["messages"][0]["content"]
    assert contenido[1]["type"] == "image_url"
    assert contenido[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_el_prompt_no_lleva_el_texto_del_operador(tmp_path: Path) -> None:
    """El modelo ve la foto y una pregunta cerrada, nada más."""
    cliente_http = ClienteFalso()
    _cliente(cliente_http).describir(_foto(tmp_path))

    cuerpo = cliente_http.llamadas[0]["json"]
    assert cuerpo["model"] == "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
    assert cuerpo["temperature"] == 0
    assert cuerpo["max_tokens"] == 800
    texto = cuerpo["messages"][0]["content"][0]["text"]
    assert "estructural" in texto


def test_acepta_un_json_en_un_bloque_de_codigo(tmp_path: Path) -> None:
    cliente_http = ClienteFalso(
        contenido='```json\n{"danio_estructural": false, "descripcion": "pintura"}\n```'
    )
    resultado = _cliente(cliente_http).describir(_foto(tmp_path))

    assert resultado.estado == "ok"
    assert resultado.danio_estructural is False
    assert resultado.hay_danio is False


# ------------------------------------------------------------------- fail-safe


def test_sin_clave_no_se_llama_a_la_nube(tmp_path: Path) -> None:
    cliente_http = ClienteFalso()
    cliente = _cliente(cliente_http, api_key="")

    resultado = cliente.describir(_foto(tmp_path))

    assert cliente_http.llamadas == []
    assert resultado.estado == "indeterminado"
    assert resultado.danio_estructural is None
    assert resultado.hay_danio is False


def test_sin_contenido_el_resultado_es_indeterminado(tmp_path: Path) -> None:
    """Los modelos de razonamiento a veces devuelven `content: null`."""
    cliente_http = ClienteFalso(contenido=None, razonamiento="estoy pensando...")
    resultado = _cliente(cliente_http).describir(_foto(tmp_path))

    assert resultado.estado == "indeterminado"
    assert resultado.danio_estructural is None


def test_una_salida_invalida_no_se_usa(tmp_path: Path) -> None:
    cliente_http = ClienteFalso(contenido="no soy un json")
    resultado = _cliente(cliente_http).describir(_foto(tmp_path))

    assert resultado.estado == "indeterminado"
    assert resultado.danio_estructural is None


def test_una_salida_con_campos_de_mas_se_rechaza(tmp_path: Path) -> None:
    cliente_http = ClienteFalso(
        contenido='{"danio_estructural": true, "descripcion": "grieta", "inventado": 1}'
    )
    resultado = _cliente(cliente_http).describir(_foto(tmp_path))

    assert resultado.estado == "indeterminado"


def test_un_error_http_queda_indeterminado(tmp_path: Path) -> None:
    cliente_http = ClienteFalso(status_code=500)
    resultado = _cliente(cliente_http).describir(_foto(tmp_path))

    assert resultado.estado == "indeterminado"
    assert "500" in resultado.detalle


def test_una_foto_que_no_existe_no_levanta(tmp_path: Path) -> None:
    cliente_http = ClienteFalso()
    resultado = _cliente(cliente_http).describir(tmp_path / "no-esta.jpg")

    assert resultado.estado == "indeterminado"
    assert cliente_http.llamadas == []


def test_un_fallo_de_red_no_levanta(tmp_path: Path) -> None:
    class ClienteQueFalla:
        def post(self, *args, **kwargs):
            raise ConnectionError("no hay ruta")

    resultado = _cliente(ClienteQueFalla()).describir(_foto(tmp_path))

    assert resultado.estado == "indeterminado"
    assert "no hay ruta" in resultado.detalle


def test_la_clave_no_aparece_en_el_log_ni_en_el_detalle(tmp_path: Path) -> None:
    cliente_http = ClienteFalso(status_code=500)
    resultado = _cliente(cliente_http).describir(_foto(tmp_path))

    assert "nvapi-secreto" not in resultado.detalle
    assert "nvapi-secreto" not in str(resultado)


# ------------------------------------------------------------ la lista cerrada


def test_con_clave_y_modelo_de_la_lista_hay_cliente() -> None:
    cliente = crear_cliente_vision(_settings())
    assert cliente is not None
    assert cliente.nombre == "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"


def test_sin_clave_no_hay_cliente() -> None:
    assert crear_cliente_vision(_settings(nvidia_api_key="")) is None


def test_un_modelo_fuera_de_la_lista_no_hay_cliente() -> None:
    """Un modelo que no está en la lista cerrada no se llama: fail-safe."""
    assert crear_cliente_vision(_settings(nvidia_modelo="nvidia/otro-modelo")) is None


def test_el_modelo_retirado_no_hay_cliente() -> None:
    """El Nemotron Nano 12B v2 VL fue retirado por NVIDIA (HTTP 410 Gone)."""
    assert crear_cliente_vision(_settings(nvidia_modelo="nvidia/nemotron-nano-12b-v2-vl")) is None


# ------------------------------------------------------------------- el esquema


def test_el_esquema_rechaza_campos_de_mas() -> None:
    with pytest.raises(ValidationError):
        SalidaVision.model_validate(
            {"danio_estructural": True, "descripcion": "x", "extra": 1}
        )


def test_el_esquema_exige_la_descripcion() -> None:
    with pytest.raises(ValidationError):
        SalidaVision.model_validate({"danio_estructural": True})


def test_el_resultado_indeterminado_no_da_danio() -> None:
    resultado = ResultadoVision(
        estado="indeterminado",
        danio_estructural=None,
        descripcion="",
        detalle="sin red",
        modelo="m",
    )
    assert resultado.hay_danio is False
    assert resultado.utilizable is False
