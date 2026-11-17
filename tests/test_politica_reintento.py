"""Tests de la política única de retry/backoff (P1-17).

Comprueba que:

* se reintenta 429 (rate limit) y 5xx (error del servidor);
* no se reintenta 400, 401, 403, 422 (errores del cliente);
* se reintenta errores de red transitorios;
* se respeta el deadline global (restante_s <= 0 no reintenta);
* el backoff es exponencial con jitter;
* una operación no idempotente no se reintenta tras una primera falla.
"""

from __future__ import annotations

import httpx
import pytest

from tools.politica_reintento import (
    CODIGOS_NO_REINTENTAR,
    CODIGOS_REINTENTABLES,
    PoliticaReintento,
    ejecutar_con_reintento,
)

# ------------------------------------------------------------------ helpers


def _respuesta_falsa(status_code: int) -> httpx.Response:
    """Construye una respuesta HTTP falsa para probar códigos de estado."""
    request = httpx.Request("GET", "http://planta/api/test")
    return httpx.Response(status_code, request=request)


def _error_http(status_code: int) -> httpx.HTTPStatusError:
    """Construye un HTTPStatusError falso."""
    return httpx.HTTPStatusError(
        f"HTTP {status_code}",
        request=_respuesta_falsa(status_code).request,
        response=_respuesta_falsa(status_code),
    )


# ------------------------------------------------------------------ clasificación


@pytest.mark.parametrize("codigo", CODIGOS_REINTENTABLES)
def test_codigo_reintento_http(codigo: int):
    """Los códigos 429 y 5xx se clasifican como reintentables."""
    politica = PoliticaReintento()
    assert politica.es_reintento_http(codigo)


@pytest.mark.parametrize("codigo", CODIGOS_NO_REINTENTAR)
def test_codigo_no_reintento_http(codigo: int):
    """Los códigos 4xx del cliente no se reintentan."""
    politica = PoliticaReintento()
    assert not politica.es_reintento_http(codigo)


def test_otro_codigo_no_es_reintento():
    """Un código que no está en ninguna lista no se reintenta."""
    politica = PoliticaReintento()
    assert not politica.es_reintento_http(301)
    assert not politica.es_reintento_http(404)


# ------------------------------------------------------------------ ejecución


def test_ejecucion_exitosa_sin_reintentos():
    """Una operación que funciona no reintenta."""
    politica = PoliticaReintento(max_intentos=3)
    llamadas = 0

    def accion() -> str:
        nonlocal llamadas
        llamadas += 1
        return "ok"

    assert politica.ejecutar(accion) == "ok"
    assert llamadas == 1


def test_reintenta_429():
    """HTTP 429 se reintenta hasta tener éxito."""
    politica = PoliticaReintento(max_intentos=3, espera_base_s=0.0, jitter=False)
    llamadas = 0

    def accion() -> str:
        nonlocal llamadas
        llamadas += 1
        if llamadas < 3:
            raise _error_http(429)
        return "ok"

    assert politica.ejecutar(accion) == "ok"
    assert llamadas == 3


def test_reintenta_5xx():
    """HTTP 503 se reintenta hasta tener éxito."""
    politica = PoliticaReintento(max_intentos=3, espera_base_s=0.0, jitter=False)
    llamadas = 0

    def accion() -> str:
        nonlocal llamadas
        llamadas += 1
        if llamadas < 2:
            raise _error_http(503)
        return "ok"

    assert politica.ejecutar(accion) == "ok"
    assert llamadas == 2


def test_no_reintenta_400():
    """HTTP 400 no se reintenta: se propaga inmediatamente."""
    politica = PoliticaReintento(max_intentos=3, espera_base_s=0.0)
    llamadas = 0

    def accion() -> str:
        nonlocal llamadas
        llamadas += 1
        raise _error_http(400)

    with pytest.raises(httpx.HTTPStatusError):
        politica.ejecutar(accion)
    assert llamadas == 1


def test_no_reintenta_401():
    """HTTP 401 no se reintenta."""
    politica = PoliticaReintento(max_intentos=3, espera_base_s=0.0)
    llamadas = 0

    def accion() -> str:
        nonlocal llamadas
        llamadas += 1
        raise _error_http(401)

    with pytest.raises(httpx.HTTPStatusError):
        politica.ejecutar(accion)
    assert llamadas == 1


def test_no_reintenta_403():
    """HTTP 403 no se reintenta."""
    politica = PoliticaReintento(max_intentos=3, espera_base_s=0.0)
    llamadas = 0

    def accion() -> str:
        nonlocal llamadas
        llamadas += 1
        raise _error_http(403)

    with pytest.raises(httpx.HTTPStatusError):
        politica.ejecutar(accion)
    assert llamadas == 1


def test_no_reintenta_422():
    """HTTP 422 no se reintenta."""
    politica = PoliticaReintento(max_intentos=3, espera_base_s=0.0)
    llamadas = 0

    def accion() -> str:
        nonlocal llamadas
        llamadas += 1
        raise _error_http(422)

    with pytest.raises(httpx.HTTPStatusError):
        politica.ejecutar(accion)
    assert llamadas == 1


def test_error_de_red_transitorio_se_reintenta():
    """Un TransportError se reintenta."""
    politica = PoliticaReintento(max_intentos=3, espera_base_s=0.0, jitter=False)
    llamadas = 0

    def accion() -> str:
        nonlocal llamadas
        llamadas += 1
        if llamadas < 2:
            raise httpx.TransportError("connection reset")
        return "ok"

    assert politica.ejecutar(accion) == "ok"
    assert llamadas == 2


def test_respeta_deadline_agotado():
    """Si el deadline ya se agotó, no se ejecuta ni se reintenta."""
    politica = PoliticaReintento(max_intentos=5, espera_base_s=0.0)

    def accion() -> str:
        return "ok"

    with pytest.raises(TimeoutError):
        politica.ejecutar(accion, restante_s=lambda: 0.0)


def test_respeta_deadline_entre_reintentos():
    """Si el deadline se agota entre reintentos, se corta."""
    politica = PoliticaReintento(max_intentos=5, espera_base_s=0.0, jitter=False)
    llamadas = 0
    valores = [1.0, 1.0, 1.0, 0.0]
    indice = [0]

    def restante_s() -> float:
        if indice[0] >= len(valores):
            return 0.0
        valor = valores[indice[0]]
        indice[0] += 1
        return valor

    def accion() -> str:
        nonlocal llamadas
        llamadas += 1
        raise _error_http(503)

    with pytest.raises(TimeoutError):
        politica.ejecutar(accion, restante_s=restante_s)
    # Se ejecutó 2 veces: la primera falló, la segunda falló, el tercer
    # intento vio restante_s == 0 y levantó TimeoutError antes de ejecutar.
    assert llamadas == 2


def test_no_idempotente_no_se_reintenta():
    """Una operación no idempotente no se reintenta tras una falla de red."""
    politica = PoliticaReintento(max_intentos=3, espera_base_s=0.0, jitter=False)
    llamadas = 0

    def accion() -> str:
        nonlocal llamadas
        llamadas += 1
        raise httpx.TransportError("connection reset")

    with pytest.raises(httpx.TransportError):
        politica.ejecutar(accion, idempotente=False)
    assert llamadas == 1


def test_max_intentos_configurable():
    """max_intentos limita la cantidad de reintentos."""
    politica = PoliticaReintento(max_intentos=2, espera_base_s=0.0, jitter=False)
    llamadas = 0

    def accion() -> str:
        nonlocal llamadas
        llamadas += 1
        raise _error_http(503)

    with pytest.raises(httpx.HTTPStatusError):
        politica.ejecutar(accion)
    assert llamadas == 2


def test_backoff_exponencial_creciente():
    """El tiempo de espera crece exponencialmente con cada intento."""
    politica = PoliticaReintento(max_intentos=4, espera_base_s=1.0, jitter=False)

    def accion() -> str:
        raise _error_http(503)

    try:
        politica.ejecutar(accion)
    except httpx.HTTPStatusError:
        pass

    # Las esperas se registran vía _debe_reintento -> espera_para, pero como
    # no podemos interceptar time.sleep fácilmente, verificamos el cálculo
    # directo de espera_para.
    e0 = politica.espera_para(0)
    e1 = politica.espera_para(1)
    e2 = politica.espera_para(2)
    assert e0 == 1.0
    assert e1 == 2.0
    assert e2 == 4.0


def test_jitter_establece_espera_diferente():
    """Con jitter activado, dos esperas del mismo intento pueden diferir."""
    politica = PoliticaReintento(max_intentos=2, espera_base_s=1.0, jitter=True)
    e1 = politica.espera_para(0)
    e2 = politica.espera_para(0)
    # Con jitter, es muy probable que difieran (aunque no garantizado).
    # Al menos verificamos que están dentro del rango esperado.
    assert 1.0 <= e1 <= 1.25
    assert 1.0 <= e2 <= 1.25


def test_sin_jitter_espera_determinista():
    """Sin jitter, la espera es siempre la misma para el mismo intento."""
    politica = PoliticaReintento(max_intentos=2, espera_base_s=1.0, jitter=False)
    assert politica.espera_para(0) == politica.espera_para(0) == 1.0


def test_ejecutar_con_reintento_atajo():
    """El atajo ejecutar_con_reintento funciona con valores por defecto."""
    llamadas = 0

    def accion() -> str:
        nonlocal llamadas
        llamadas += 1
        if llamadas < 2:
            raise _error_http(429)
        return "ok"

    assert ejecutar_con_reintento(accion) == "ok"
    assert llamadas == 2


# ------------------------------------------------------------------ constantes


def test_codigos_reintentables_contiene_429_y_5xx():
    """429 y 5xx están en la lista de reintentables."""
    assert 429 in CODIGOS_REINTENTABLES
    assert 500 in CODIGOS_REINTENTABLES
    assert 503 in CODIGOS_REINTENTABLES


def test_codigos_no_reintentar_contiene_4xx():
    """400, 401, 403, 422 están en la lista de no reintentar."""
    assert 400 in CODIGOS_NO_REINTENTAR
    assert 401 in CODIGOS_NO_REINTENTAR
    assert 403 in CODIGOS_NO_REINTENTAR
    assert 422 in CODIGOS_NO_REINTENTAR
