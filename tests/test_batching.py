"""Tests para el batching de requests no urgentes (Ítem 3.9 del Plan de implementación 3)."""

from __future__ import annotations

import time

import pytest

from src.routing.batching import BatchNoUrgente, ItemBatch, ResultadoBatch


def _envio_registrado() -> tuple[list, list]:
    """Devuelve (lista_de_envios, contador) para capturar qué se envió."""
    envios: list[list[ItemBatch[int]]] = []

    def callback(items: list[ItemBatch[int]]) -> None:
        envios.append(list(items))

    return envios, callback


def test_urgente_se_envia_inmediato():
    """Un request urgente se envía de inmediato, sin esperar al batch."""
    envios, callback = _envio_registrado()
    batch = BatchNoUrgente(callback_envio=callback, umbral_tamano=5, umbral_tiempo_s=30.0)

    batch.agregar(request_id="urg-1", datos="texto urgente", urgente=True)

    assert len(envios) == 1
    assert envios[0][0].request_id == "urg-1"
    assert envios[0][0].datos == "texto urgente"
    assert envios[0][0].urgente is True


def test_no_urgente_se_acumula():
    """Un request no urgente no se envía hasta alcanzar el umbral."""
    envios, callback = _envio_registrado()
    batch = BatchNoUrgente(callback_envio=callback, umbral_tamano=3, umbral_tiempo_s=30.0)

    batch.agregar(request_id="n1", datos="a", urgente=False)
    batch.agregar(request_id="n2", datos="b", urgente=False)

    assert len(envios) == 0  # aún no se envía
    assert batch.cantidad_pendientes() == 2


def test_envio_al_alcanzar_umbral_tamano():
    """Cuando se alcanza el umbral de tamaño, se envía el batch."""
    envios, callback = _envio_registrado()
    batch = BatchNoUrgente(callback_envio=callback, umbral_tamano=2, umbral_tiempo_s=30.0)

    batch.agregar(request_id="n1", datos="a", urgente=False)
    batch.agregar(request_id="n2", datos="b", urgente=False)

    assert len(envios) == 1
    assert envios[0][0].request_id == "n1"
    assert envios[0][1].request_id == "n2"
    assert batch.esta_vacio()


def test_envio_al_alcanzar_umbral_tiempo():
    """Cuando se alcanza el umbral de tiempo, se envía el batch aunque no llene el tamaño."""
    envios, callback = _envio_registrado()
    batch = BatchNoUrgente(
        callback_envio=callback, umbral_tamano=10, umbral_tiempo_s=0.05
    )

    batch.agregar(request_id="n1", datos="a", urgente=False)

    time.sleep(0.1)
    # Forzar una nueva verificación agregando otro elemento no urgente
    batch.agregar(request_id="n2", datos="b", urgente=False)

    assert len(envios) == 1
    assert envios[0][0].request_id == "n1"
    assert envios[0][1].request_id == "n2"


def test_enviar_ahora_envia_pendientes():
    """enviar_ahora() fuerza el envío de lo acumulado."""
    envios, callback = _envio_registrado()
    batch = BatchNoUrgente(callback_envio=callback, umbral_tamano=10, umbral_tiempo_s=30.0)

    batch.agregar(request_id="n1", datos="a", urgente=False)
    batch.agregar(request_id="n2", datos="b", urgente=False)

    resultado = batch.enviar_ahora()

    assert resultado.enviados == 2
    assert len(envios) == 1
    assert batch.esta_vacio()


def test_mezcla_urgente_y_no_urgente():
    """Los urgentes se envían inmediato y los no urgentes se acumulan por separado."""
    envios, callback = _envio_registrado()
    batch = BatchNoUrgente(callback_envio=callback, umbral_tamano=3, umbral_tiempo_s=30.0)

    batch.agregar(request_id="u1", datos="urgente", urgente=True)
    batch.agregar(request_id="n1", datos="a", urgente=False)
    batch.agregar(request_id="u2", datos="urgente 2", urgente=True)
    batch.agregar(request_id="n2", datos="b", urgente=False)

    # 2 envíos inmediatos (urgentes) + 0 de batch (aún no alcanza umbral)
    assert len(envios) == 2
    assert envios[0][0].request_id == "u1"
    assert envios[1][0].request_id == "u2"
    assert batch.cantidad_pendientes() == 2


def test_esta_vacio():
    """esta_vacio() refleja si hay pendientes."""
    envios, callback = _envio_registrado()
    batch = BatchNoUrgente(callback_envio=callback, umbral_tamano=5, umbral_tiempo_s=30.0)

    assert batch.esta_vacio() is True

    batch.agregar(request_id="n1", datos="a", urgente=False)
    assert batch.esta_vacio() is False

    batch.enviar_ahora()
    assert batch.esta_vacio() is True


def test_cantidad_pendientes():
    """cantidad_pendientes() cuenta los no urgentes acumulados."""
    envios, callback = _envio_registrado()
    batch = BatchNoUrgente(callback_envio=callback, umbral_tamano=5, umbral_tiempo_s=30.0)

    assert batch.cantidad_pendientes() == 0

    batch.agregar(request_id="n1", datos="a", urgente=False)
    batch.agregar(request_id="n2", datos="b", urgente=False)
    assert batch.cantidad_pendientes() == 2

    batch.enviar_ahora()
    assert batch.cantidad_pendientes() == 0


def test_resumen():
    """resumen() devuelve el estado del acumulador."""
    envios, callback = _envio_registrado()
    batch = BatchNoUrgente(callback_envio=callback, umbral_tamano=5, umbral_tiempo_s=30.0)

    batch.agregar(request_id="u1", datos="x", urgente=True)
    batch.agregar(request_id="n1", datos="a", urgente=False)
    batch.agregar(request_id="n2", datos="b", urgente=False)
    batch.enviar_ahora()

    resumen = batch.resumen()
    assert resumen["pendientes"] == 0
    assert resumen["enviados_inmediato"] == 1
    assert resumen["enviados_en_batch"] == 2
    assert resumen["total_procesados"] == 3
    assert resumen["umbral_tamano"] == 5
    assert resumen["umbral_tiempo_s"] == 30.0


def test_umbral_tamano_invalido():
    """umbral_tamano debe ser >= 1."""
    envios, callback = _envio_registrado()
    with pytest.raises(ValueError):
        BatchNoUrgente(callback_envio=callback, umbral_tamano=0, umbral_tiempo_s=30.0)


def test_umbral_tiempo_invalido():
    """umbral_tiempo_s debe ser > 0."""
    envios, callback = _envio_registrado()
    with pytest.raises(ValueError):
        BatchNoUrgente(callback_envio=callback, umbral_tamano=5, umbral_tiempo_s=0.0)


def test_resultado_batch_tipos():
    """ResultadoBatch tiene los campos correctos."""
    r = ResultadoBatch(enviados=5, urgentes_enviados_inmediato=3, motivo="umbral alcanzado")
    assert r.enviados == 5
    assert r.urgentes_enviados_inmediato == 3
    assert r.motivo == "umbral alcanzado"


def test_item_batch_campos():
    """ItemBatch tiene los campos correctos."""
    item = ItemBatch(request_id="r1", datos={"clave": "valor"}, urgente=False)
    assert item.request_id == "r1"
    assert item.datos == {"clave": "valor"}
    assert item.urgente is False
    assert item.creado_en > 0


def test_varios_batches_secuenciales():
    """Se pueden enviar varios batches uno tras otro."""
    envios, callback = _envio_registrado()
    batch = BatchNoUrgente(callback_envio=callback, umbral_tamano=2, umbral_tiempo_s=30.0)

    batch.agregar(request_id="n1", datos="a", urgente=False)
    batch.agregar(request_id="n2", datos="b", urgente=False)  # dispara batch 1

    batch.agregar(request_id="n3", datos="c", urgente=False)
    batch.agregar(request_id="n4", datos="d", urgente=False)  # dispara batch 2

    assert len(envios) == 2
    assert [i.request_id for i in envios[0]] == ["n1", "n2"]
    assert [i.request_id for i in envios[1]] == ["n3", "n4"]
