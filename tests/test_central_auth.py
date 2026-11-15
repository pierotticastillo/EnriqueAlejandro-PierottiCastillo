"""Autenticación y RBAC de la Central.

La identidad sale de un token Bearer configurado en el servidor. El cuerpo
HTTP nunca puede cambiar el actor ni elevar el rol.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

import pytest

from config import CredencialCentral, Settings
from planta.central import Central
from planta.identidad import AutenticadorToken

TOKEN_PLANIFICADOR = "p" * 40
TOKEN_SUPERVISOR = "s" * 40
TOKEN_INSPECTOR = "i" * 40
TOKEN_LECTURA = "l" * 40


class RepositorioFalso:
    def __init__(self) -> None:
        self.ordenes = {
            "ORD-1": {
                "id": "ORD-1",
                "evento_id": "EVT-1",
                "equipo": "B-101",
                "estado": "Pendiente de aprobacion",
                "motivo": "bomba con pérdida",
                "tareas": ["T-B-EMP"],
                "repuestos": [{"codigo": "TX-EMP-KIT", "cantidad": 1}],
            }
        }
        self.decisiones: list[dict[str, Any]] = []

    def decidir_orden(
        self,
        orden_id: str,
        decision: str,
        *,
        motivo: str | None = None,
        usuario: str | None = None,
        rol: str | None = None,
    ) -> dict[str, Any] | None:
        self.decisiones.append(
            {
                "orden_id": orden_id,
                "decision": decision,
                "motivo": motivo,
                "usuario": usuario,
                "rol": rol,
            }
        )
        fila = self.ordenes.get(orden_id)
        if fila is None or fila["estado"] != "Pendiente de aprobacion":
            return None
        resultado = dict(fila)
        resultado["estado"] = decision
        self.ordenes[orden_id] = resultado
        return resultado

    def ordenes_para_central(self, limite: int = 50) -> list[dict[str, Any]]:
        return list(self.ordenes.values())[:limite]

    def cierres_para_central(self, limite: int = 50) -> list[dict[str, Any]]:
        return []

    def reconstruir_orden(self, orden_id: str) -> dict[str, Any] | None:
        orden = self.ordenes.get(orden_id)
        if orden is None:
            return None
        return {
            "orden": orden,
            "evento": {"id": orden["evento_id"]},
            "manifiestos": [
                {
                    "tipo": "orden",
                    "referencia_id": orden_id,
                    "hash_sha256": "a" * 64,
                    "hash_verificado": True,
                    "contenido": {"parte": {"estado": "no_disponible"}},
                }
            ],
            "ledger": [
                {
                    "tipo": "evento",
                    "evento_id": orden["evento_id"],
                    "orden_id": orden_id,
                    "estado": orden["estado"],
                }
            ],
            "trayectoria": [],
            "integridad": {
                "manifiestos_verificados": True,
                "entradas_ledger": 1,
            },
        }


def _autenticador() -> AutenticadorToken:
    return AutenticadorToken(
        [
            CredencialCentral(
                token=TOKEN_PLANIFICADOR,
                usuario="ana",
                rol="planificador",
            ),
            CredencialCentral(
                token=TOKEN_SUPERVISOR,
                usuario="luis",
                rol="supervisor",
            ),
            CredencialCentral(
                token=TOKEN_INSPECTOR,
                usuario="marta",
                rol="inspector",
            ),
            CredencialCentral(
                token=TOKEN_LECTURA,
                usuario="roberto",
                rol="lectura",
            ),
        ]
    )


def _puerto_libre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def central_viva() -> Any:
    repositorio = RepositorioFalso()
    cierres: list[dict[str, Any]] = []

    def decidir_cierre(orden_id, decision, *, actor, rol, motivo=None):
        cierres.append(
            {
                "orden_id": orden_id,
                "decision": decision,
                "actor": actor,
                "rol": rol,
                "motivo": motivo,
            }
        )
        return {
            "ok": True,
            "orden": {"id": orden_id, "estado": "Cerrada"},
            "habilita": None,
            "monto_habilita_usd": None,
        }

    central = Central(
        repositorio,
        decidir_cierre=decidir_cierre,
        autenticador=_autenticador(),
        host="127.0.0.1",
        puerto=_puerto_libre(),
    )
    central.iniciar()
    yield central, repositorio, cierres
    central.detener()


def _url(central: Central, camino: str) -> str:
    return f"http://127.0.0.1:{central._puerto}{camino}"  # noqa: SLF001 - es del test


def _peticion(
    central: Central,
    camino: str,
    *,
    token: str | None = None,
    cuerpo: dict[str, Any] | None = None,
    method: str = "GET",
) -> tuple[int, dict[str, Any], Any]:
    data = None if cuerpo is None else json.dumps(cuerpo).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    peticion = urllib.request.Request(
        _url(central, camino),
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(peticion, timeout=5) as respuesta:
            return (
                respuesta.status,
                json.loads(respuesta.read().decode("utf-8")),
                respuesta.headers,
            )
    except urllib.error.HTTPError as error:
        cuerpo_error = error.read().decode("utf-8")
        return error.code, json.loads(cuerpo_error), error.headers


def test_sin_credencial_la_api_responde_401(central_viva: Any) -> None:
    central = central_viva[0]

    codigo, datos, cabeceras = _peticion(central, "/api/ordenes")

    assert codigo == 401
    assert "credencial" in datos["error"].lower()
    assert cabeceras["WWW-Authenticate"] == 'Bearer realm="pia3-central"'


def test_un_token_desconocido_no_permite_operar(central_viva: Any) -> None:
    central, repositorio, _ = central_viva

    codigo, _, _ = _peticion(
        central,
        "/api/ordenes/ORD-1/decision",
        token="x" * 40,
        cuerpo={"decision": "aprobar"},
        method="POST",
    )

    assert codigo == 401
    assert repositorio.decisiones == []


def test_lectura_puede_consultar_pero_no_decidir(central_viva: Any) -> None:
    central, repositorio, _ = central_viva

    codigo, _, _ = _peticion(central, "/api/ordenes", token=TOKEN_LECTURA)
    assert codigo == 200

    codigo, _, _ = _peticion(
        central,
        "/api/ordenes/ORD-1/decision",
        token=TOKEN_LECTURA,
        cuerpo={"decision": "aprobar"},
        method="POST",
    )
    assert codigo == 403
    assert repositorio.decisiones == []


def test_la_evidencia_exige_autenticacion_y_permite_consulta(central_viva: Any) -> None:
    central = central_viva[0]

    codigo, _, cabeceras = _peticion(central, "/api/ordenes/ORD-1/evidencia")
    assert codigo == 401
    assert cabeceras["WWW-Authenticate"] == 'Bearer realm="pia3-central"'

    codigo, datos, _ = _peticion(
        central,
        "/api/ordenes/ORD-1/evidencia",
        token=TOKEN_LECTURA,
    )
    assert codigo == 200
    assert datos["manifiestos"][0]["hash_verificado"] is True
    assert datos["ledger"][0]["tipo"] == "evento"


def test_la_reconstruccion_de_una_orden_inexistente_devuelve_404(central_viva: Any) -> None:
    central = central_viva[0]

    codigo, datos, _ = _peticion(
        central,
        "/api/ordenes/ORD-NO-EXISTE/reconstruccion",
        token=TOKEN_LECTURA,
    )

    assert codigo == 404
    assert datos["error"] == "La orden no existe"


def test_el_planificador_puede_decidir(central_viva: Any) -> None:
    central, repositorio, _ = central_viva

    codigo, datos, _ = _peticion(
        central,
        "/api/ordenes/ORD-1/decision",
        token=TOKEN_PLANIFICADOR,
        cuerpo={"decision": "aprobar"},
        method="POST",
    )

    assert codigo == 200
    assert datos["ok"] is True
    assert repositorio.decisiones[0]["usuario"] == "ana"
    assert repositorio.decisiones[0]["rol"] == "planificador"


def test_el_usuario_del_cuerpo_no_puede_suplantar_la_identidad(
    central_viva: Any,
) -> None:
    central, repositorio, _ = central_viva

    codigo, _, _ = _peticion(
        central,
        "/api/ordenes/ORD-1/decision",
        token=TOKEN_PLANIFICADOR,
        cuerpo={"decision": "aprobar", "usuario": "atacante", "rol": "inspector"},
        method="POST",
    )

    assert codigo == 200
    assert repositorio.decisiones[0]["usuario"] == "ana"
    assert repositorio.decisiones[0]["rol"] == "planificador"


def test_supervisor_e_inspector_no_pueden_aprobar_ordenes(central_viva: Any) -> None:
    central, repositorio, _ = central_viva

    for token in (TOKEN_SUPERVISOR, TOKEN_INSPECTOR):
        codigo, _, _ = _peticion(
            central,
            "/api/ordenes/ORD-1/decision",
            token=token,
            cuerpo={"decision": "aprobar"},
            method="POST",
        )
        assert codigo == 403

    assert repositorio.decisiones == []


def test_el_supervisor_puede_cerrar_un_cierre(central_viva: Any) -> None:
    central, _, cierres = central_viva

    codigo, _, _ = _peticion(
        central,
        "/api/cierres/ORD-1/decision",
        token=TOKEN_SUPERVISOR,
        cuerpo={"decision": "cerrar", "motivo": "trabajo conforme"},
        method="POST",
    )

    assert codigo == 200
    assert cierres[0]["actor"] == "luis"
    assert cierres[0]["rol"] == "supervisor"


def test_el_inspector_puede_certificar_un_cierre(central_viva: Any) -> None:
    central, _, cierres = central_viva

    codigo, _, _ = _peticion(
        central,
        "/api/cierres/ORD-1/decision",
        token=TOKEN_INSPECTOR,
        cuerpo={"decision": "certificar"},
        method="POST",
    )

    assert codigo == 200
    assert cierres[0]["actor"] == "marta"
    assert cierres[0]["rol"] == "inspector"


def test_el_inspector_no_puede_cerrar_trabajo_propio(central_viva: Any) -> None:
    central, _, cierres = central_viva

    codigo, _, _ = _peticion(
        central,
        "/api/cierres/ORD-1/decision",
        token=TOKEN_INSPECTOR,
        cuerpo={"decision": "cerrar"},
        method="POST",
    )

    assert codigo == 403
    assert cierres == []


def test_la_sesion_devuelve_el_actor_autenticado(central_viva: Any) -> None:
    central = central_viva[0]

    codigo, datos, _ = _peticion(central, "/api/sesion", token=TOKEN_INSPECTOR)

    assert codigo == 200
    assert datos == {"usuario": "marta", "rol": "inspector"}


def test_la_central_nativa_escucha_en_loopback_por_defecto() -> None:
    settings = Settings(_env_file=None)

    assert settings.central_host == "127.0.0.1"
    assert settings.central_credenciales == []


def test_las_credenciales_se_leen_del_entorno(monkeypatch: Any) -> None:
    token = "e" * 40
    monkeypatch.setenv(
        "CENTRAL_CREDENCIALES",
        f'[{{"token":"{token}","usuario":"elena","rol":"inspector"}}]',
    )

    settings = Settings(_env_file=None)

    assert len(settings.central_credenciales) == 1
    credencial = settings.central_credenciales[0]
    assert credencial.usuario == "elena"
    assert credencial.rol == "inspector"
    assert credencial.token.get_secret_value() == token
    assert token not in repr(settings.central_credenciales)


def test_no_se_admiten_dos_tokens_para_la_misma_persona() -> None:
    with pytest.raises(ValueError, match="un solo token"):
        AutenticadorToken(
            [
                CredencialCentral(
                    token="a" * 40,
                    usuario="ana",
                    rol="planificador",
                ),
                CredencialCentral(
                    token="b" * 40,
                    usuario="ana",
                    rol="supervisor",
                ),
            ]
        )


def test_no_se_admiten_el_mismo_token_para_dos_personas() -> None:
    with pytest.raises(ValueError, match="una sola persona"):
        AutenticadorToken(
            [
                CredencialCentral(
                    token="a" * 40,
                    usuario="ana",
                    rol="planificador",
                ),
                CredencialCentral(
                    token="a" * 40,
                    usuario="beto",
                    rol="supervisor",
                ),
            ]
        )
