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
from db.repositorio import AutorizacionInvalida, ConflictoEstado
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
        # Como en la base real: el cierre técnico deja la orden ejecutada y
        # espera la decisión humana de G2/G3.
        self.orden["estado"] = "Ejecutada"
        self.orden["motivo"] = datos.get("motivo")
        self.registros.append(dict(datos))
        return {
            "cierre_id": datos["cierre_id"],
            "nuevo": True,
            "habilita": None,
            "monto_habilita_usd": None,
        }

    def obtener_cierre_de_orden(self, orden_id: str) -> dict[str, Any] | None:
        return next(
            (
                cierre
                for cierre in self.cierres.values()
                if cierre["orden_id"] == orden_id
            ),
            None,
        )

    def decidir_cierre_humano(self, **datos: Any) -> dict[str, Any]:
        estado = {
            "cerrar": "Cerrada",
            "certificar": "Certificada",
            "observar": "Observada",
        }[datos["decision"]]
        habilita = None
        monto = None
        if estado == "Certificada":
            certificaciones = sum(
                1 for registro in self.registros if registro.get("decision") == "certificar"
            )
            habilita = f"HAB-{self.orden['creada_en'].year}-{certificaciones + 1:04d}"
            monto = datos.get("monto_usd")
        self.orden["estado"] = estado
        self.orden["motivo"] = datos.get("motivo")
        self.registros.append(
            dict(datos) | {"estado": estado, "habilita": habilita, "monto": monto}
        )
        return {
            "decision": {
                "decision": datos["decision"],
                "estado_orden": estado,
                "actor": datos["actor"],
                "rol": datos["rol"],
                "motivo": datos.get("motivo"),
            },
            "orden": dict(self.orden),
            "habilita": habilita,
            "monto_habilita_usd": monto,
        }


class RepositorioConError(RepositorioFalso):
    def __init__(self, orden: dict[str, Any], error: Exception) -> None:
        super().__init__(orden)
        self.error = error

    def decidir_cierre_humano(self, **datos: Any) -> dict[str, Any]:
        raise self.error


class RepositorioEnCarrera(RepositorioFalso):
    """Simula que otra decisión certificó la orden entre precheck y escritura."""

    def registrar_cierre(self, **datos: Any) -> dict[str, Any]:
        self.orden["estado"] = "Certificada"
        self.orden["motivo"] = "certificada por otra persona"
        return {
            "cierre_id": datos["cierre_id"],
            "nuevo": False,
            "habilita": "HAB-2026-0009",
            "monto_habilita_usd": 1150.0,
        }

    def obtener_habilitacion(self, orden_id: str) -> dict[str, Any] | None:
        return {"numero": "HAB-2026-0009", "monto": 1150.0}


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


def test_el_cierre_propio_deja_la_orden_esperando_al_supervisor(
    catalogo, precios
) -> None:
    repositorio = RepositorioFalso(_fila_orden())
    cliente = ClienteFalso()
    servicio, _ = _servicio(repositorio, cliente, catalogo, precios)

    servicio.procesar_cierre(_nota())

    estados = [orden.estado.value for orden in cliente.ordenes]
    assert estados == ["Ejecutada"]
    assert repositorio.orden["estado"] == "Ejecutada"
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
    # El sistema no certifica ni cierra: deja la decisión al supervisor.
    assert repositorio.orden["estado"] == "Ejecutada"
    assert "decision" not in registro


def test_la_contratista_con_diferencias_queda_esperando_al_inspector(
    catalogo, precios
) -> None:
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
    assert estados == ["Ejecutada"]
    assert cliente.ordenes[-1].motivo
    assert cliente.ordenes[-1].habilita is None
    assert repositorio.registros[0]["diferencias"][0]["tipo"] == "repuesto_extra"


def test_la_contratista_sin_diferencias_se_certifica_con_habilita_humano(
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
    assert cliente.ordenes[-1].estado.value == "Ejecutada"
    assert cliente.ordenes[-1].habilita is None

    resultado = servicio.decidir_cierre(
        "ORD-F-201A-FC-T01",
        "certificar",
        actor="marta",
        rol="inspector",
    )

    assert resultado["ok"] is True
    estados = [orden.estado.value for orden in cliente.ordenes]
    assert estados == ["Ejecutada", "Certificada"]
    assert cliente.ordenes[-1].habilita == "HAB-2026-0001"
    assert cliente.ordenes[-1].monto_habilita_usd == 1150.0
    assert repositorio.registros[-1]["actor"] == "marta"
    assert repositorio.registros[-1]["rol"] == "inspector"


def test_la_certificacion_sin_precios_no_decide_ni_emite_habilita(catalogo) -> None:
    repositorio = RepositorioFalso(
        _fila_orden(tareas=["T-B-ROD"], repuestos=[], ejecutor="Servipat")
    )
    cliente = ClienteFalso()
    texto = (
        "Hice cambio de rodamientos del cigüeñal. "
        "Probé a ciento diez kilos, sin pérdida. Fueron dieciséis horas."
    )
    servicio, _ = _servicio(
        repositorio,
        cliente,
        catalogo,
        ListaPrecios(),
        texto=texto,
    )
    servicio.procesar_cierre(_nota(empresa="Servipat", id="CIERRE-SIN-PRECIO"))

    resultado = servicio.decidir_cierre(
        "ORD-F-201A-FC-T01",
        "certificar",
        actor="marta",
        rol="inspector",
    )

    assert resultado["ok"] is False
    assert resultado["codigo"] == 409
    assert "precio contractual" in resultado["error"]
    assert repositorio.orden["estado"] == "Ejecutada"
    assert all("decision" not in registro for registro in repositorio.registros)
    assert all(orden.estado.value != "Certificada" for orden in cliente.ordenes)


def test_observar_no_depende_de_la_lista_de_precios(catalogo) -> None:
    repositorio = RepositorioFalso(
        _fila_orden(tareas=["T-B-ROD"], repuestos=[], ejecutor="Servipat")
    )
    cliente = ClienteFalso()
    texto = (
        "Hice cambio de rodamientos del cigüeñal. "
        "Usé un juego de sellos de cárter TX-350. Fueron dieciséis horas."
    )
    servicio, _ = _servicio(
        repositorio,
        cliente,
        catalogo,
        ListaPrecios(),
        texto=texto,
    )
    servicio.procesar_cierre(_nota(empresa="Servipat", id="CIERRE-OBSERVA-SIN-PRECIO"))

    resultado = servicio.decidir_cierre(
        "ORD-F-201A-FC-T01",
        "observar",
        actor="marta",
        rol="inspector",
        motivo="falta justificar un repuesto",
    )

    assert resultado["ok"] is True
    assert repositorio.orden["estado"] == "Observada"
    assert repositorio.obtener_habilitacion("ORD-F-201A-FC-T01") is None


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
    servicio.decidir_cierre(
        "ORD-F-201A-FC-T01",
        "certificar",
        actor="marta",
        rol="inspector",
    )

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
        "Ejecutada",
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
    assert [orden.estado.value for orden in cliente.ordenes] == ["Ejecutada"]


def test_el_precheck_de_ejecutor_precede_a_la_existencia_del_cierre(
    catalogo,
    precios,
) -> None:
    """Un inspector no puede certificar trabajo propio, haya cierre o no."""
    repositorio = RepositorioFalso(_fila_orden(ejecutor="propio"))
    cliente = ClienteFalso()
    servicio, _ = _servicio(repositorio, cliente, catalogo, precios)

    resultado = servicio.decidir_cierre(
        "ORD-F-201A-FC-T01",
        "certificar",
        actor="marta",
        rol="inspector",
    )

    assert resultado["ok"] is False
    assert resultado["codigo"] == 403
    assert repositorio.registros == []
    assert cliente.ordenes == []


def test_un_conflicto_del_repositorio_se_traduce_a_409(catalogo, precios) -> None:
    repositorio = RepositorioConError(
        _fila_orden(tareas=["T-B-ROD"], repuestos=[], ejecutor="Servipat"),
        ConflictoEstado("la orden ya fue decidida"),
    )
    cliente = ClienteFalso()
    servicio, _ = _servicio(repositorio, cliente, catalogo, precios)
    servicio.procesar_cierre(_nota(empresa="Servipat", id="CIERRE-CONFLICTO"))
    cliente.ordenes.clear()

    resultado = servicio.decidir_cierre(
        "ORD-F-201A-FC-T01",
        "certificar",
        actor="marta",
        rol="inspector",
    )

    assert resultado["ok"] is False
    assert resultado["codigo"] == 409
    assert cliente.ordenes == []


def test_un_error_de_autorizacion_se_traduce_a_403(catalogo, precios) -> None:
    repositorio = RepositorioConError(
        _fila_orden(tareas=["T-B-ROD"], repuestos=[], ejecutor="Servipat"),
        AutorizacionInvalida("el ejecutor no corresponde"),
    )
    cliente = ClienteFalso()
    servicio, _ = _servicio(repositorio, cliente, catalogo, precios)
    servicio.procesar_cierre(_nota(empresa="Servipat", id="CIERRE-AUTORIZACION"))
    cliente.ordenes.clear()

    resultado = servicio.decidir_cierre(
        "ORD-F-201A-FC-T01",
        "certificar",
        actor="marta",
        rol="inspector",
    )

    assert resultado["ok"] is False
    assert resultado["codigo"] == 403
    assert cliente.ordenes == []


def test_un_cierre_duplicado_no_republica_un_estado_obsoleto(catalogo, precios) -> None:
    """Si el cierre se-certificó en una carrera, sale ese estado, no Ejecutada."""
    repositorio = RepositorioEnCarrera(_fila_orden(ejecutor="Servipat"))
    cliente = ClienteFalso()
    servicio, _ = _servicio(repositorio, cliente, catalogo, precios)

    servicio.procesar_cierre(_nota(empresa="Servipat", id="CIERRE-CARRERA"))

    assert [orden.estado.value for orden in cliente.ordenes] == ["Certificada"]
    assert cliente.ordenes[0].habilita == "HAB-2026-0009"
    assert cliente.ordenes[0].monto_habilita_usd == 1150.0
