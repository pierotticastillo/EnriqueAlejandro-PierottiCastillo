"""G2/G3: el cierre técnico deja la decisión final a una persona.

La nota del técnico prepara la evidencia y deja la orden en ``Ejecutada``.
Después:

* el ``supervisor`` cierra el trabajo propio;
* el ``inspector`` certifica u observa el trabajo de la contratista;
* una observación exige motivo;
* el habilita se emite únicamente después de una certificación humana.

El rol y el actor se validan en el servidor: no vienen del texto del técnico
ni del cuerpo de la petición.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from planta_contrato import NotaCierre, OrdenSistema

from analisis.catalogo import CatalogoManuales
from analisis.cierre import validar_decision_cierre
from manuales import documentos
from planta.cierre import ServicioCierre
from tools.precios import ListaPrecios, precios_desde_texto

CIERRE_PROPIO = (
    "Cierro la orden ORD F 201A FC T01. Hice cambio de cartuchos. "
    "Usé cuarenta y ocho cartucho filtrante 25 µm. "
    "Quedó en servicio, el diferencial está normal. Fueron tres con dos horas."
)
CIERRE_CONTRATISTA = (
    "Hice cambio de rodamientos del cigüeñal. "
    "Probé a ciento diez kilos, sin pérdida. Fueron dieciséis horas."
)


# --------------------------------------------------------------------- dominio


def test_el_supervisor_puede_cerrar_trabajo_propio() -> None:
    decision = validar_decision_cierre(
        decision="cerrar",
        rol="supervisor",
        ejecutor="propio",
        motivo="el trabajo terminó",
    )

    assert decision.decision == "cerrar"
    assert decision.estado == "Cerrada"
    assert decision.rol == "supervisor"


def test_el_inspector_puede_certificar_trabajo_de_contratista() -> None:
    decision = validar_decision_cierre(
        decision="certificar",
        rol="inspector",
        ejecutor="Servipat",
    )

    assert decision.decision == "certificar"
    assert decision.estado == "Certificada"
    assert decision.rol == "inspector"


def test_observar_una_contratista_exige_motivo() -> None:
    with pytest.raises(ValueError, match="motivo"):
        validar_decision_cierre(
            decision="observar",
            rol="inspector",
            ejecutor="Servipat",
        )


def test_el_supervisor_no_certifica_y_el_inspector_no_cierra() -> None:
    with pytest.raises(ValueError, match="inspector"):
        validar_decision_cierre(
            decision="certificar",
            rol="supervisor",
            ejecutor="Servipat",
        )

    with pytest.raises(ValueError, match="supervisor"):
        validar_decision_cierre(
            decision="cerrar",
            rol="inspector",
            ejecutor="propio",
        )


# --------------------------------------------------------------------- dobles


class TranscriptorFalso:
    def __init__(self, texto: str) -> None:
        self.texto = texto

    def transcribir(self, ruta: Path) -> str:
        return self.texto


class AlmacenamientoFalso:
    def guardar_audio(self, nota_id: str, audio_url: str | None) -> Path | None:
        return Path(f"/datos/{nota_id}.wav") if audio_url else None


class ClienteFalso:
    def __init__(self) -> None:
        self.ordenes: list[OrdenSistema] = []

    def publicar_orden(self, orden: OrdenSistema) -> None:
        self.ordenes.append(orden)


class RepositorioFalso:
    def __init__(self, orden: dict[str, Any]) -> None:
        self.orden = orden
        self.cierres: dict[str, dict[str, Any]] = {}
        self.decisiones: list[dict[str, Any]] = []

    def obtener_cierre(self, cierre_id: str) -> dict[str, Any] | None:
        return self.cierres.get(cierre_id)

    def obtener_cierre_de_orden(self, orden_id: str) -> dict[str, Any] | None:
        return next(
            (cierre for cierre in self.cierres.values() if cierre["orden_id"] == orden_id),
            None,
        )

    def obtener_orden_completa(self, orden_id: str) -> dict[str, Any] | None:
        return self.orden if self.orden["id"] == orden_id else None

    def registrar_cierre(self, **datos: Any) -> dict[str, Any]:
        cierre = dict(datos)
        cierre["id"] = datos["cierre_id"]
        self.cierres[datos["cierre_id"]] = cierre
        self.orden["estado"] = "Ejecutada"
        self.orden["motivo"] = datos.get("motivo")
        return {"cierre_id": datos["cierre_id"], "nuevo": True}

    def obtener_habilitacion(self, orden_id: str) -> dict[str, Any] | None:
        return None

    def decidir_cierre_humano(self, **datos: Any) -> dict[str, Any]:
        decision = validar_decision_cierre(
            decision=datos["decision"],
            rol=datos["rol"],
            ejecutor=self.orden["ejecutor"],
            motivo=datos.get("motivo"),
        )
        habilita = None
        monto = None
        if decision.estado == "Certificada":
            habilita = "HAB-2026-0001"
            monto = datos.get("monto_usd")
        self.orden["estado"] = decision.estado
        self.orden["motivo"] = datos.get("motivo")
        registro = dict(datos) | {
            "estado": decision.estado,
            "habilita": habilita,
            "monto_habilita_usd": monto,
        }
        self.decisiones.append(registro)
        return {
            "ok": True,
            "orden": dict(self.orden),
            "habilita": habilita,
            "monto_habilita_usd": monto,
        }


# ------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def catalogo() -> CatalogoManuales:
    return CatalogoManuales.desde_documentos(documentos())


@pytest.fixture(scope="module")
def precios() -> ListaPrecios:
    for documento in documentos():
        if documento.clave != "CT-SVP-2025":
            continue
        for chunk in documento.chunks:
            if chunk.seccion == "2.1":
                return ListaPrecios(precios_desde_texto(chunk.texto))
    raise AssertionError("el recorte no tiene la lista de precios")


def _fila_orden(*, ejecutor: str = "propio") -> dict[str, Any]:
    return {
        "id": "ORD-F-201A-FC-T01",
        "evento_id": "parte:NOTA-00001:F-201A",
        "equipo": "F-201A",
        "estado": "Aprobada",
        "motivo": None,
        "detencion": False,
        "ejecutor": ejecutor,
        "reduce_inyeccion": False,
        "creada_en": datetime(2026, 11, 14, 12, 0, tzinfo=UTC),
        "actualizada_en": datetime(2026, 11, 14, 12, 0, tzinfo=UTC),
        "tareas": ["T-B-ROD"] if ejecutor != "propio" else ["T-F-CAR"],
        "repuestos": [] if ejecutor != "propio" else [{"codigo": "FC-CART-25", "cantidad": 48.0}],
    }


def _nota(*, empresa: str = "propio", cierre_id: str = "CIERRE-1") -> NotaCierre:
    return NotaCierre(
        id=cierre_id,
        ts=datetime(2026, 11, 14, 16, 0, tzinfo=UTC),
        orden_id="ORD-F-201A-FC-T01",
        tecnico="Ramón Cayupán",
        empresa=empresa,
        audio_url=f"/media/{cierre_id}.wav",
        fotos=[],
    )


def _servicio(
    repositorio: RepositorioFalso,
    cliente: ClienteFalso,
    catalogo: CatalogoManuales,
    precios: ListaPrecios,
    texto: str,
) -> ServicioCierre:
    return ServicioCierre(
        repositorio,
        AlmacenamientoFalso(),
        TranscriptorFalso(texto),
        cliente,
        catalogo,
        precios,
    )


# --------------------------------------------------------------------- tests


def test_el_cierre_automatico_solo_deja_la_orden_ejecutada(
    catalogo: CatalogoManuales,
    precios: ListaPrecios,
) -> None:
    repositorio = RepositorioFalso(_fila_orden())
    cliente = ClienteFalso()
    servicio = _servicio(repositorio, cliente, catalogo, precios, CIERRE_PROPIO)

    servicio.procesar_cierre(_nota())

    assert [orden.estado.value for orden in cliente.ordenes] == ["Ejecutada"]
    assert repositorio.orden["estado"] == "Ejecutada"
    assert repositorio.decisiones == []
    assert cliente.ordenes[-1].habilita is None


def test_el_supervisor_cierra_el_trabajo_propio(
    catalogo: CatalogoManuales,
    precios: ListaPrecios,
) -> None:
    repositorio = RepositorioFalso(_fila_orden())
    cliente = ClienteFalso()
    servicio = _servicio(repositorio, cliente, catalogo, precios, CIERRE_PROPIO)
    servicio.procesar_cierre(_nota())

    resultado = servicio.decidir_cierre(
        "ORD-F-201A-FC-T01",
        "cerrar",
        actor="luis",
        rol="supervisor",
        motivo="el trabajo quedó conforme",
    )

    assert resultado["ok"] is True
    assert repositorio.orden["estado"] == "Cerrada"
    assert repositorio.decisiones[0]["actor"] == "luis"
    assert repositorio.decisiones[0]["rol"] == "supervisor"
    assert cliente.ordenes[-1].estado.value == "Cerrada"
    assert cliente.ordenes[-1].habilita is None


def test_el_inspector_certifica_y_solo_ahi_se_emite_el_habilita(
    catalogo: CatalogoManuales,
    precios: ListaPrecios,
) -> None:
    repositorio = RepositorioFalso(_fila_orden(ejecutor="Servipat"))
    cliente = ClienteFalso()
    servicio = _servicio(repositorio, cliente, catalogo, precios, CIERRE_CONTRATISTA)
    servicio.procesar_cierre(_nota(empresa="Servipat", cierre_id="CIERRE-2"))

    resultado = servicio.decidir_cierre(
        "ORD-F-201A-FC-T01",
        "certificar",
        actor="marta",
        rol="inspector",
    )

    assert resultado["ok"] is True
    assert repositorio.orden["estado"] == "Certificada"
    assert repositorio.decisiones[0]["actor"] == "marta"
    assert cliente.ordenes[-1].estado.value == "Certificada"
    assert cliente.ordenes[-1].habilita == "HAB-2026-0001"
    assert cliente.ordenes[-1].monto_habilita_usd == 1150.0


def test_una_observacion_sin_motivo_no_cambia_el_estado(
    catalogo: CatalogoManuales,
    precios: ListaPrecios,
) -> None:
    repositorio = RepositorioFalso(_fila_orden(ejecutor="Servipat"))
    cliente = ClienteFalso()
    servicio = _servicio(repositorio, cliente, catalogo, precios, CIERRE_CONTRATISTA)
    servicio.procesar_cierre(_nota(empresa="Servipat", cierre_id="CIERRE-3"))

    resultado = servicio.decidir_cierre(
        "ORD-F-201A-FC-T01",
        "observar",
        actor="marta",
        rol="inspector",
    )

    assert resultado["ok"] is False
    assert "motivo" in resultado["error"].lower()
    assert repositorio.orden["estado"] == "Ejecutada"
    assert repositorio.decisiones == []
    assert cliente.ordenes[-1].estado.value == "Ejecutada"


def test_un_supervisor_no_puede_cerrar_trabajo_de_contratista(
    catalogo: CatalogoManuales,
    precios: ListaPrecios,
) -> None:
    repositorio = RepositorioFalso(_fila_orden(ejecutor="Servipat"))
    cliente = ClienteFalso()
    servicio = _servicio(repositorio, cliente, catalogo, precios, CIERRE_CONTRATISTA)
    servicio.procesar_cierre(_nota(empresa="Servipat", cierre_id="CIERRE-4"))

    resultado = servicio.decidir_cierre(
        "ORD-F-201A-FC-T01",
        "cerrar",
        actor="luis",
        rol="supervisor",
    )

    assert resultado["ok"] is False
    assert repositorio.orden["estado"] == "Ejecutada"
    assert repositorio.decisiones == []
