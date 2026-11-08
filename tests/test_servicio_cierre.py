"""Tests del servicio de cierre (Fase 7): de la nota de cierre al estado.

El servicio recibe la ``NotaCierre`` que publica la planta, transcribe el
audio, compara el cierre contra la orden y publica el estado. Lo que se
comprueba acá es el **comportamiento**: qué se publica, en qué orden y qué
queda escrito. La base de datos se prueba aparte (``test_db_cierre.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from planta_contrato import NotaCierre, OrdenSistema

from analisis.catalogo import CatalogoManuales
from manuales import documentos
from planta.cierre import ServicioCierre
from tools.precios import ListaPrecios, precios_desde_texto

CIERRE = (
    "Cierro la orden ORD F 201A FC T01. Hice cambio de cartuchos. "
    "Usé cuarenta y ocho cartucho filtrante 25 µm. "
    "Quedó en servicio, el diferencial está normal. Fueron tres con dos horas."
)


# --------------------------------------------------------------------- dobles


class TranscriptorFalso:
    """El ASR, de mentira: devuelve un texto fijo."""

    def __init__(self, texto: str = CIERRE) -> None:
        self.texto = texto
        self.audios: list[Path] = []

    def transcribir(self, ruta: Path) -> str:
        self.audios.append(ruta)
        return self.texto


class AlmacenamientoFalso:
    """La descarga del audio, de mentira."""

    def __init__(self) -> None:
        self.audios: list[str] = []

    def guardar_audio(self, nota_id: str, audio_url: str | None) -> Path | None:
        self.audios.append(str(audio_url))
        if audio_url is None:
            return None
        return Path(f"/datos/notas/{nota_id}/audio.wav")


class ClienteFalso:
    """El cliente MQTT, de mentira: guarda lo publicado."""

    def __init__(self) -> None:
        self.ordenes: list[OrdenSistema] = []

    def publicar_orden(self, orden: OrdenSistema) -> None:
        self.ordenes.append(orden)


class RepositorioFalso:
    """La base, de memoria: alcanza para el comportamiento del servicio."""

    def __init__(self, orden: dict[str, Any]) -> None:
        self.orden = orden
        self.cierres: dict[str, dict[str, Any]] = {}
        self.registros: list[dict[str, Any]] = []

    def obtener_cierre(self, cierre_id: str) -> dict[str, Any] | None:
        return self.cierres.get(cierre_id)

    def obtener_orden_completa(self, orden_id: str) -> dict[str, Any] | None:
        return self.orden if self.orden["id"] == orden_id else None

    def obtener_habilitacion(self, orden_id: str) -> dict[str, Any] | None:
        for registro in self.registros:
            if registro["orden_id"] == orden_id and registro.get("habilita"):
                return {"numero": registro["habilita"], "monto": registro["monto"]}
        return None

    def registrar_cierre(self, **datos: Any) -> dict[str, Any]:
        self.cierres[datos["cierre_id"]] = dict(datos)
        # Como en la base real: el cierre deja la orden en su estado nuevo.
        self.orden["estado"] = datos["estado"]
        self.orden["motivo"] = datos["motivo"]
        habilita = None
        monto = None
        if datos.get("decision") == "certifica" and datos.get("monto_usd") is not None:
            habilita = f"HAB-{datos['anio']}-{len(self.registros) + 1:04d}"
            monto = datos["monto_usd"]
        self.registros.append(dict(datos) | {"habilita": habilita, "monto": monto})
        return {
            "cierre_id": datos["cierre_id"],
            "nuevo": True,
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


def _fila_orden(
    *,
    tareas: list[str] | None = None,
    repuestos: list[dict[str, Any]] | None = None,
    ejecutor: str = "propio",
) -> dict[str, Any]:
    return {
        "id": "ORD-F-201A-FC-T01",
        "evento_id": "parte:NOTA-00001:F-201A",
        "equipo": "F-201A",
        "estado": "Aprobada",
        "motivo": None,
        "detencion": False,
        "ejecutor": ejecutor,
        "reduce_inyeccion": False,
        "creada_en": datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
        "actualizada_en": datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
        "tareas": tareas if tareas is not None else ["T-F-CAR"],
        "repuestos": (
            repuestos
            if repuestos is not None
            else [{"codigo": "FC-CART-25", "cantidad": 48.0}]
        ),
    }


def _nota(**datos: Any) -> NotaCierre:
    base = {
        "id": "CIERRE-ORD-F-201A-FC-T01",
        "ts": datetime(2026, 11, 8, 16, 0, tzinfo=UTC),
        "orden_id": "ORD-F-201A-FC-T01",
        "tecnico": "Ramón Cayupán",
        "empresa": "propio",
        "audio_url": "/media/audios/CIERRE-ORD-F-201A-FC-T01.wav",
        "fotos": [],
    }
    return NotaCierre(**(base | datos))


def _servicio(
    repositorio: RepositorioFalso,
    cliente: ClienteFalso,
    catalogo: CatalogoManuales,
    precios: ListaPrecios,
    *,
    texto: str = CIERRE,
) -> tuple[ServicioCierre, TranscriptorFalso]:
    transcriptor = TranscriptorFalso(texto)
    servicio = ServicioCierre(
        repositorio,
        AlmacenamientoFalso(),
        transcriptor,
        cliente,
        catalogo,
        precios,
    )
    return servicio, transcriptor


# ------------------------------------------------------------------- los tests


def test_el_cierre_propio_deja_la_orden_cerrada(catalogo, precios) -> None:
    repositorio = RepositorioFalso(_fila_orden())
    cliente = ClienteFalso()
    servicio, _ = _servicio(repositorio, cliente, catalogo, precios)

    servicio.procesar_cierre(_nota())

    estados = [orden.estado.value for orden in cliente.ordenes]
    assert estados == ["Ejecutada", "Cerrada"]
    assert cliente.ordenes[-1].habilita is None
    assert cliente.ordenes[-1].monto_habilita_usd is None


def test_el_audio_del_cierre_se_transcribe_localmente(catalogo, precios) -> None:
    repositorio = RepositorioFalso(_fila_orden())
    cliente = ClienteFalso()
    servicio, transcriptor = _servicio(repositorio, cliente, catalogo, precios)

    servicio.procesar_cierre(_nota())

    assert len(transcriptor.audios) == 1
    assert "CIERRE-ORD-F-201A-FC-T01" in str(transcriptor.audios[0])


def test_el_cierre_queda_escrito_con_sus_diferencias(catalogo, precios) -> None:
    repositorio = RepositorioFalso(_fila_orden())
    cliente = ClienteFalso()
    servicio, _ = _servicio(repositorio, cliente, catalogo, precios)

    servicio.procesar_cierre(_nota())

    registro = repositorio.registros[0]
    assert registro["cierre_id"] == "CIERRE-ORD-F-201A-FC-T01"
    assert registro["tecnico"] == "Ramón Cayupán"
    assert registro["empresa"] == "propio"
    assert registro["tareas_hechas"] == ["T-F-CAR"]
    assert registro["repuestos_usados"][0]["codigo"] == "FC-CART-25"
    assert registro["horas"] == pytest.approx(3.2)
    assert registro["estado"] == "Cerrada"
    # El trabajo propio no se certifica: lo cierra el supervisor.
    assert registro["decision"] is None


def test_la_contratista_con_diferencias_queda_observada(catalogo, precios) -> None:
    repositorio = RepositorioFalso(
        _fila_orden(tareas=["T-B-ROD"], repuestos=[], ejecutor="Servipat")
    )
    cliente = ClienteFalso()
    texto = (
        "Hice cambio de rodamientos del cigüeñal. "
        "Usé un juego de sellos de cárter TX-350. Fueron dieciséis horas."
    )
    servicio, _ = _servicio(repositorio, cliente, catalogo, precios, texto=texto)

    servicio.procesar_cierre(_nota(empresa="Servipat", id="CIERRE-2"))

    estados = [orden.estado.value for orden in cliente.ordenes]
    assert estados == ["Ejecutada", "Observada"]
    assert cliente.ordenes[-1].motivo
    assert cliente.ordenes[-1].habilita is None
    assert repositorio.registros[0]["decision"] == "observa"


def test_la_contratista_sin_diferencias_queda_certificada_con_habilita(
    catalogo, precios
) -> None:
    repositorio = RepositorioFalso(
        _fila_orden(tareas=["T-B-ROD"], repuestos=[], ejecutor="Servipat")
    )
    cliente = ClienteFalso()
    texto = (
        "Hice cambio de rodamientos del cigüeñal. "
        "Probé a ciento diez kilos, sin pérdida. Fueron dieciséis horas."
    )
    servicio, _ = _servicio(repositorio, cliente, catalogo, precios, texto=texto)

    servicio.procesar_cierre(_nota(empresa="Servipat", id="CIERRE-3"))

    estados = [orden.estado.value for orden in cliente.ordenes]
    assert estados == ["Ejecutada", "Certificada"]
    assert cliente.ordenes[-1].habilita == "HAB-2026-0001"
    assert cliente.ordenes[-1].monto_habilita_usd == 1150.0
    assert repositorio.registros[0]["decision"] == "certifica"


def test_el_monto_no_sale_de_lo_que_declara_el_cierre(catalogo, precios) -> None:
    """El habilita se paga con el precio del contrato, no con lo que dice el
    técnico (CT-SVP-2025 §3.2)."""
    repositorio = RepositorioFalso(
        _fila_orden(tareas=["T-B-ROD"], repuestos=[], ejecutor="Servipat")
    )
    cliente = ClienteFalso()
    texto = (
        "Hice cambio de rodamientos del cigüeñal. "
        "Probé a ciento diez kilos, sin pérdida. Fueron cuarenta horas."
    )
    servicio, _ = _servicio(repositorio, cliente, catalogo, precios, texto=texto)

    servicio.procesar_cierre(_nota(empresa="Servipat", id="CIERRE-4"))

    assert cliente.ordenes[-1].monto_habilita_usd == 1150.0


def test_el_cierre_que_vuelve_no_se_procesa_dos_veces(catalogo, precios) -> None:
    repositorio = RepositorioFalso(_fila_orden())
    cliente = ClienteFalso()
    servicio, _ = _servicio(repositorio, cliente, catalogo, precios)

    servicio.procesar_cierre(_nota())
    servicio.procesar_cierre(_nota())

    # La segunda vez no se transcribe otra vez ni se vuelve a decidir: sólo se
    # republica el estado que ya estaba.
    assert len(repositorio.registros) == 1
    assert [orden.estado.value for orden in cliente.ordenes] == [
        "Ejecutada",
        "Cerrada",
        "Cerrada",
    ]


def test_un_cierre_de_una_orden_desconocida_no_inventa_nada(catalogo, precios) -> None:
    repositorio = RepositorioFalso(_fila_orden())
    cliente = ClienteFalso()
    servicio, _ = _servicio(repositorio, cliente, catalogo, precios)

    servicio.procesar_cierre(_nota(orden_id="ORD-QUE-NO-EXISTE"))

    assert cliente.ordenes == []
    assert repositorio.registros == []


def test_un_cierre_sin_audio_no_tumba_la_planta(catalogo, precios) -> None:
    repositorio = RepositorioFalso(_fila_orden())
    cliente = ClienteFalso()
    servicio, _ = _servicio(repositorio, cliente, catalogo, precios)

    servicio.procesar_cierre(_nota(audio_url=None))

    # Sin transcripción no hay tareas ni repuestos declarados: la diferencia
    # se ve, pero el cierre se registra igual.
    assert len(repositorio.registros) == 1
    assert repositorio.registros[0]["transcripcion"] == ""
    assert [orden.estado.value for orden in cliente.ordenes] == ["Ejecutada", "Cerrada"]
