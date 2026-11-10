"""Tests de la Central de mantenimiento.

La Central es el canal de decisión del planificador (enunciado, sección D:
la orden pendiente *"se reanuda cuando el planificador la aprueba o la
rechaza"*).

Lo que se comprueba:

* sólo se pueden decidir las órdenes que están **pendientes**;
* aprobar no necesita motivo; **rechazar sí**;
* la decisión se publica por el bus con el estado nuevo;
* la decisión queda registrada en la trayectoria;
* la Central **no es una tool**: no está en el registro de tools del MCP;
* las rutas HTTP responden lo que corresponde.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

import pytest

from planta.central import Central
from tools.registro import REGISTRO

# --------------------------------------------------------------------- dobles


class RepositorioFalso:
    """Un repositorio en memoria: sólo las órdenes que le cargan."""

    def __init__(self) -> None:
        self.ordenes: dict[str, dict[str, Any]] = {}
        self.llamadas: list[tuple[str, str, str | None, str | None]] = []

    def _agregar(self, orden_id: str, estado: str) -> dict[str, Any]:
        fila = {
            "id": orden_id,
            "evento_id": "EVT-1",
            "equipo": "B-101",
            "estado": estado,
            "motivo": "bomba con pérdida",
            "costo_estimado": 420,
            "detencion": False,
            "creada_en": "2026-11-10T10:00:00Z",
            "actualizada_en": "2026-11-10T10:00:00Z",
            "tareas": ["T-B-EMP"],
            "repuestos": [{"codigo": "TX-EMP-KIT", "cantidad": 1}],
        }
        self.ordenes[orden_id] = fila
        return fila

    def decidir_orden(
        self,
        orden_id: str,
        decision: str,
        *,
        motivo: str | None = None,
        usuario: str | None = None,
    ) -> dict[str, Any] | None:
        self.llamadas.append((orden_id, decision, motivo, usuario))
        fila = self.ordenes.get(orden_id)
        if fila is None or fila["estado"] != "Pendiente de aprobacion":
            return None
        if decision == "Rechazada" and not (motivo or "").strip():
            raise ValueError("El rechazo de una orden necesita un motivo")
        fila = dict(fila)
        fila["estado"] = decision
        if decision == "Rechazada":
            fila["motivo"] = motivo
        self.ordenes[orden_id] = fila
        return fila

    def ordenes_para_central(self, limite: int = 50) -> list[dict[str, Any]]:
        return list(self.ordenes.values())[:limite]


def _central(**kwargs: Any) -> tuple[Central, RepositorioFalso, list[Any], list[Any]]:
    repositorio = RepositorioFalso()
    publicadas: list[Any] = []
    registradas: list[Any] = []
    central = Central(
        repositorio,
        publicar_orden=lambda fila, estado: publicadas.append((fila, estado)),
        registrar_paso=lambda **campos: registradas.append(campos),
        **kwargs,
    )
    return central, repositorio, publicadas, registradas


# ------------------------------------------------------------------- decisiones


def test_aprobar_una_orden_pendiente_la_publica() -> None:
    """El planificador aprueba: la orden sale por el bus con el estado nuevo."""
    central, repositorio, publicadas, registradas = _central()
    repositorio._agregar("ORD-1", "Pendiente de aprobacion")

    resultado = central.decidir("ORD-1", "aprobar", usuario="planificador")

    assert resultado["ok"] is True
    assert resultado["orden"]["estado"] == "Aprobada"
    assert len(publicadas) == 1
    assert publicadas[0][1] == "Aprobada"
    assert registradas and registradas[0]["etapa"] == "planificador"
    assert registradas[0]["orden_id"] == "ORD-1"


def test_rechazar_una_orden_pendiente_necesita_motivo() -> None:
    """Un rechazo sin motivo no se acepta: el sistema no adivina."""
    central, repositorio, publicadas, _ = _central()
    repositorio._agregar("ORD-1", "Pendiente de aprobacion")

    resultado = central.decidir("ORD-1", "rechazar")

    assert resultado["ok"] is False
    assert "motivo" in resultado["error"].lower()
    assert publicadas == []


def test_rechazar_con_motivo_deja_el_motivo_escrito() -> None:
    """El motivo queda en la orden: es la razón del rechazo."""
    central, repositorio, _, _ = _central()
    repositorio._agregar("ORD-1", "Pendiente de aprobacion")

    resultado = central.decidir("ORD-1", "rechazar", motivo="falta el repuesto")

    assert resultado["ok"] is True
    assert resultado["orden"]["motivo"] == "falta el repuesto"


def test_una_orden_que_no_esta_pendiente_no_se_decide() -> None:
    """Una orden ya decidida no se toca: la decisión es de una sola vez."""
    central, repositorio, publicadas, _ = _central()
    repositorio._agregar("ORD-1", "Aprobada")

    resultado = central.decidir("ORD-1", "rechazar", motivo="cambié de opinión")

    assert resultado["ok"] is False
    assert "pendiente" in resultado["error"].lower()
    assert publicadas == []


def test_una_orden_inexistente_no_se_decide() -> None:
    central, _, _, _ = _central()
    resultado = central.decidir("ORD-9", "aprobar")
    assert resultado["ok"] is False


def test_una_decision_invalida_no_se_acepta() -> None:
    """Sólo aprobar y rechazar: el resto no es una decisión."""
    central, repositorio, _, _ = _central()
    repositorio._agregar("ORD-1", "Pendiente de aprobacion")

    resultado = central.decidir("ORD-1", "ejecutar")

    assert resultado["ok"] is False
    assert repositorio.llamadas == []


def test_la_decision_se_registra_con_usuario_y_motivo() -> None:
    """En la trayectoria queda quién decidió, qué decidió y por qué."""
    central, repositorio, _, registradas = _central()
    repositorio._agregar("ORD-1", "Pendiente de aprobacion")

    central.decidir("ORD-1", "rechazar", motivo="falta el repuesto", usuario="juan")

    campos = registradas[0]
    assert campos["tipo"] == "decision"
    assert campos["argumentos"]["decision"] == "Rechazada"
    assert campos["argumentos"]["motivo"] == "falta el repuesto"
    assert campos["argumentos"]["usuario"] == "juan"


def test_la_central_no_es_una_tool() -> None:
    """El agente no puede aprobar nada: la Central no está en el registro."""
    assert "central" not in REGISTRO
    assert all("central" not in nombre for nombre in REGISTRO)


# ------------------------------------------------------------------------ HTTP


def _puerto_libre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def central_viva() -> Any:
    """Una Central levantada en un puerto libre."""
    central, repositorio, publicadas, registradas = _central(
        host="127.0.0.1",
        puerto=_puerto_libre(),
    )
    repositorio._agregar("ORD-1", "Pendiente de aprobacion")
    central.iniciar()
    yield central, repositorio, publicadas, registradas
    central.detener()


def _url(central: Central, camino: str) -> str:
    return f"http://127.0.0.1:{central._puerto}{camino}"  # noqa: SLF001 - es del test


def test_la_pagina_de_la_central_responde(central_viva: Any) -> None:
    central = central_viva[0]
    with urllib.request.urlopen(_url(central, "/"), timeout=5) as respuesta:
        cuerpo = respuesta.read().decode("utf-8")
    assert respuesta.status == 200
    assert "Central de mantenimiento" in cuerpo


def test_la_api_devuelve_las_ordenes(central_viva: Any) -> None:
    central = central_viva[0]
    with urllib.request.urlopen(_url(central, "/api/ordenes"), timeout=5) as respuesta:
        datos = json.loads(respuesta.read().decode("utf-8"))
    assert datos["ordenes"][0]["id"] == "ORD-1"


def test_la_api_decide_una_orden(central_viva: Any) -> None:
    central, repositorio, publicadas, _ = central_viva
    cuerpo = json.dumps({"decision": "aprobar", "usuario": "planificador"}).encode("utf-8")
    peticion = urllib.request.Request(
        _url(central, "/api/ordenes/ORD-1/decision"),
        data=cuerpo,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(peticion, timeout=5) as respuesta:
        datos = json.loads(respuesta.read().decode("utf-8"))
    assert datos["ok"] is True
    assert datos["orden"]["estado"] == "Aprobada"
    assert len(publicadas) == 1


def test_la_api_rechaza_un_rechazo_sin_motivo(central_viva: Any) -> None:
    central = central_viva[0]
    cuerpo = json.dumps({"decision": "rechazar"}).encode("utf-8")
    peticion = urllib.request.Request(
        _url(central, "/api/ordenes/ORD-1/decision"),
        data=cuerpo,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(peticion, timeout=5)
    assert error.value.code == 400


def test_una_ruta_desconocida_da_404(central_viva: Any) -> None:
    central = central_viva[0]
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(_url(central, "/api/no-existe"), timeout=5)
    assert error.value.code == 404


def test_la_central_se_puede_detenener() -> None:
    """El apagado es limpio: no queda el puerto tomado."""
    central, _, _, _ = _central(host="127.0.0.1", puerto=_puerto_libre())
    central.iniciar()
    central.detener()
    central.detener()  # idempotente
