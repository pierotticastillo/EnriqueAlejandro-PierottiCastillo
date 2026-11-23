"""Tests de la cascada con validador (Ítem 3.2 del Plan de implementación 3).

Verifica que la cascada use el modelo barato cuando la respuesta es válida,
escale al modelo caro cuando el barato falle, y que sin validador no haya
cascada.
"""

from routing.cascada import Cascada
from routing.validador import valida_json, valida_monto, valida_no_vacio, valida_tool_existe

MODELO_BARATO_PRUEBA = "modelo_economico_prueba"
MODELO_CARO_PRUEBA = "modelo_caro_prueba"


def _consultar_fake(respuestas: dict[str, str]):
    """Crea un consultar_modelo falso que devuelve respuestas por modelo."""

    def consultar(modelo: str, pedido: str) -> str:
        return respuestas[modelo]

    return consultar


def test_cascada_modelo_barato_valido_una_llamada():
    """Si el modelo barato responde válido, solo se hace 1 llamada."""
    respuestas = {
        MODELO_BARATO_PRUEBA: '{"resultado": "ok"}',
        MODELO_CARO_PRUEBA: '{"resultado": "ok_caro"}',
    }
    cascada = Cascada(
        validador=valida_json,
        modelo_barato=MODELO_BARATO_PRUEBA,
        modelo_caro=MODELO_CARO_PRUEBA,
        consultar_modelo=_consultar_fake(respuestas),
    )
    resultado = cascada.consultar("pedido de prueba")
    assert resultado.llamadas == 1
    assert resultado.modelo_usado == MODELO_BARATO_PRUEBA
    assert resultado.valido is True
    assert resultado.respuesta == '{"resultado": "ok"}'


def test_cascada_modelo_barato_invalido_escala_al_caro():
    """Si el modelo barato falla el validador, se escala al modelo caro."""
    respuestas = {
        MODELO_BARATO_PRUEBA: "no es json",
        MODELO_CARO_PRUEBA: '{"resultado": "ok_caro"}',
    }
    cascada = Cascada(
        validador=valida_json,
        modelo_barato=MODELO_BARATO_PRUEBA,
        modelo_caro=MODELO_CARO_PRUEBA,
        consultar_modelo=_consultar_fake(respuestas),
    )
    resultado = cascada.consultar("pedido de prueba")
    assert resultado.llamadas == 2
    assert resultado.modelo_usado == MODELO_CARO_PRUEBA
    assert resultado.valido is True
    assert resultado.respuesta == '{"resultado": "ok_caro"}'


def test_cascada_ambos_fallan_devuelve_ultimo():
    """Si ambos modelos fallan el validador, se devuelve la respuesta del caro."""
    respuestas = {
        MODELO_BARATO_PRUEBA: "basura",
        MODELO_CARO_PRUEBA: "también basura",
    }
    cascada = Cascada(
        validador=valida_json,
        modelo_barato=MODELO_BARATO_PRUEBA,
        modelo_caro=MODELO_CARO_PRUEBA,
        consultar_modelo=_consultar_fake(respuestas),
    )
    resultado = cascada.consultar("pedido de prueba")
    assert resultado.llamadas == 2
    assert resultado.modelo_usado == MODELO_CARO_PRUEBA
    assert resultado.valido is False
    assert "rechazado también en modelo caro" in resultado.motivo


def test_cascada_validador_no_vacio():
    """El validador por defecto es valida_no_vacio."""
    respuestas = {
        MODELO_BARATO_PRUEBA: "",
        MODELO_CARO_PRUEBA: "respuesta válida",
    }
    cascada = Cascada(
        validador=valida_no_vacio,
        modelo_barato=MODELO_BARATO_PRUEBA,
        modelo_caro=MODELO_CARO_PRUEBA,
        consultar_modelo=_consultar_fake(respuestas),
    )
    resultado = cascada.consultar("pedido")
    assert resultado.llamadas == 2
    assert resultado.modelo_usado == MODELO_CARO_PRUEBA
    assert resultado.valido is True


def test_cascada_sin_validador_no_hay_cascada():
    """Sin validador explícito, la cascada usa valida_no_vacio por defecto."""
    respuestas = {
        MODELO_BARATO_PRUEBA: "",
        MODELO_CARO_PRUEBA: "algo",
    }
    cascada = Cascada(
        modelo_barato=MODELO_BARATO_PRUEBA,
        modelo_caro=MODELO_CARO_PRUEBA,
        consultar_modelo=_consultar_fake(respuestas),
    )
    resultado = cascada.consultar("pedido")
    assert resultado.llamadas == 2
    assert resultado.modelo_usado == MODELO_CARO_PRUEBA


def test_valida_json_rechaza_no_json():
    """valida_json rechaza texto que no es JSON."""
    resultado = valida_json("esto no es json")
    assert resultado.valido is False
    assert "no es JSON válido" in resultado.motivo


def test_valida_json_acepta_json_valido():
    """valida_json acepta JSON válido."""
    resultado = valida_json('{"clave": "valor"}')
    assert resultado.valido is True


def test_valida_json_con_esquema():
    """valida_json con esquema Pydantic rechaza JSON que no cumple."""
    from pydantic import BaseModel

    class Esquema(BaseModel):
        nombre: str

    resultado = valida_json('{"nombre": "ana"}', esquema=Esquema)
    assert resultado.valido is True

    resultado = valida_json('{"edad": 30}', esquema=Esquema)
    assert resultado.valido is False
    assert "no cumple el esquema" in resultado.motivo


def test_valida_monto_coincide():
    """valida_monto acepta cuando el monto está dentro de la tolerancia."""
    resultado = valida_monto("el total es 100.50", monto_esperado=100.5, tolerancia=0.01)
    assert resultado.valido is True


def test_valida_monto_no_coincide():
    """valida_monto rechaza cuando el monto difiere del esperado."""
    resultado = valida_monto("el total es 200.00", monto_esperado=100.5, tolerancia=0.01)
    assert resultado.valido is False


def test_valida_monto_sin_numeros():
    """valida_monto rechaza si no hay números en la respuesta."""
    resultado = valida_monto("no hay montos aquí", monto_esperado=100.0)
    assert resultado.valido is False


def test_valida_tool_existe_acepta_tool_conocida():
    """valida_tool_existe acepta cuando todas las tools son conocidas."""
    resultado = valida_tool_existe(
        "usá busqueda_rag para consultar",
        {"busqueda_rag", "historial_sensores"},
    )
    assert resultado.valido is True


def test_valida_tool_existe_rechaza_tool_desconocida():
    """valida_tool_existe rechaza si hay una tool desconocida."""
    resultado = valida_tool_existe(
        "usá herramienta_inexistente",
        {"busqueda_rag", "historial_sensores"},
    )
    assert resultado.valido is False
    assert "desconocidas" in resultado.motivo
