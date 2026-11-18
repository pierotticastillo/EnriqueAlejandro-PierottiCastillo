"""Manifiesto de evidencia y provenance del análisis productivo (P0-03)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from planta_contrato import EventoSistema, TipoEvento

from analisis.evidencia import Evidencia, FilaConfirmada, TendenciaEvidencia
from analisis.modelos import EvidenciaVision
from analisis.provenance import identificador_foto, manifiesto_de_analisis
from analisis.servicio import ServicioAnalisis
from tools.modelos import FragmentoManual


class RecuperadorFalso:
    disponible = True

    def buscar(self, consulta: Any) -> list[FragmentoManual]:
        return [
            FragmentoManual(
                documento="MBP-4",
                seccion="13.2",
                cita="MBP-4 §13.2",
                titulo="Inspección",
                texto="Si la falla no puede identificarse, inspeccionar el equipo.",
            )
        ]


def _evento() -> EventoSistema:
    return EventoSistema(
        id="parte:nota-1:B-102",
        ts=datetime(2026, 11, 14, 9, 0, tzinfo=UTC),
        tipo=TipoEvento.escalado,
        equipo="B-102",
        origen="parte",
        parte_id="parte-confirmado-1",
        nota_ids=["nota-1"],
        resumen="B-102 goteo: 8.3 mm/s",
    )


def _evidencia(
    *,
    con_datos: bool = True,
) -> Evidencia:
    if not con_datos:
        return Evidencia(
            equipo="B-102",
            fragmentos=(),
            fotos=(),
            tendencia=(),
            resumen="sin evidencia",
            tokens=frozenset(),
            direcciones={},
        )
    return Evidencia(
        equipo="B-102",
        fragmentos=(),
        fotos=("https://planta.example/fotos/B-102.jpg",),
        tendencia=("Tendencia vibracion últimas 6 h",),
        resumen="B-102 goteo: 8.3 mm/s",
        tokens=frozenset({"goteo"}),
        direcciones={"vibracion": "sube"},
        parte_id="parte-confirmado-1",
        filas_confirmadas=(
            FilaConfirmada(
                tag="B-102",
                variable="vibracion",
                valor=8.3,
                unidad="mm/s",
                observacion="goteo audible",
            ),
        ),
        tendencias=(
            TendenciaEvidencia(
                tag="B-102",
                variable="vibracion",
                unidad="mm/s",
                desde="2026-11-14T03:00:00+00:00",
                hasta="2026-11-14T09:00:00+00:00",
                ventana="6 h",
                fuente="historiador",
                muestras=24,
                minimo=4.1,
                maximo=8.3,
                promedio=6.2,
                tendencia="sube",
            ),
        ),
    )


def _analisis(con_datos: bool = True) -> SimpleNamespace:
    evidencia = _evidencia(con_datos=con_datos)
    vision = (
        (
            EvidenciaVision(
                foto_url="https://planta.example/fotos/B-102.jpg",
                ruta_local="/datos/NOTA-1/B-102.jpg",
                estado="ok",
                danio_estructural=True,
                descripcion="grieta visible en la carcasa",
                detalle="ok",
                modelo="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
                foto_id="foto-1",
                hash_sha256="a" * 64,
                tokens_prompt=861,
                tokens_completion=191,
                segundos=2.9,
            ),
        )
        if con_datos
        else ()
    )
    fragmentos = (
        (
            FragmentoManual(
                documento="MBP-4",
                seccion="13.2",
                cita="MBP-4 §13.2",
                titulo="Inspección",
                texto="Si la falla no puede identificarse, inspeccionar el equipo.",
            ),
        )
        if con_datos
        else ()
    )
    return SimpleNamespace(
        evento_id="parte:nota-1:B-102",
        borrador=SimpleNamespace(id="ORD-B-102-TX-T01-abc"),
        evidencia=evidencia,
        evidencia_vision=vision,
        contexto=SimpleNamespace(fragmentos=fragmentos),
        citas=("MBP-4 §13.2",) if con_datos else (),
        parte_id=evidencia.parte_id,
    )


def test_el_manifiesto_responde_parte_foto_tendencia_y_fragmento() -> None:
    manifiesto = manifiesto_de_analisis(_analisis())

    assert manifiesto["parte"]["estado"] == "disponible"
    assert manifiesto["parte"]["id"] == "parte-confirmado-1"
    assert manifiesto["parte"]["filas"][0]["variable"] == "vibracion"
    assert manifiesto["parte"]["filas"][0]["valor"] == 8.3

    foto = manifiesto["fotos"]["elementos"][0]
    assert manifiesto["fotos"]["estado"] == "disponible"
    assert foto["id"] == "foto-1"
    assert foto["url"].endswith("B-102.jpg")
    assert foto["hash_sha256"] == "a" * 64
    assert foto["vision"]["danio_estructural"] is True
    assert foto["vision"]["modelo"].startswith("nvidia/")

    tendencia = manifiesto["tendencia"]["elementos"][0]
    assert manifiesto["tendencia"]["estado"] == "disponible"
    assert tendencia["ventana"] == "6 h"
    assert tendencia["fuente"] == "historiador"
    assert tendencia["muestras"] == 24
    assert tendencia["minimo"] == 4.1
    assert tendencia["maximo"] == 8.3
    assert tendencia["promedio"] == 6.2
    assert tendencia["tendencia"] == "sube"

    fragmento = manifiesto["fragmentos"]["elementos"][0]
    assert manifiesto["fragmentos"]["estado"] == "disponible"
    assert fragmento["cita"] == "MBP-4 §13.2"
    assert fragmento["documento"] == "MBP-4"
    assert fragmento["seccion"] == "13.2"
    assert fragmento["texto"]
    assert manifiesto["citas"] == ["MBP-4 §13.2"]


def test_lo_que_no_existe_queda_marcado_no_disponible() -> None:
    manifiesto = manifiesto_de_analisis(_analisis(con_datos=False))

    assert manifiesto["parte"]["estado"] == "no_disponible"
    assert manifiesto["parte"]["id"] == "no_disponible"
    assert manifiesto["fotos"]["estado"] == "no_disponible"
    assert manifiesto["fotos"]["elementos"] == []
    assert manifiesto["tendencia"]["estado"] == "no_disponible"
    assert manifiesto["tendencia"]["elementos"] == []
    assert manifiesto["fragmentos"]["estado"] == "no_disponible"
    assert manifiesto["fragmentos"]["elementos"] == []
    assert manifiesto["citas"] == []


def test_el_id_de_foto_es_estable_y_cambia_con_el_contenido() -> None:
    primero = identificador_foto(
        "https://planta.example/fotos/B-102.jpg",
        "/datos/primer/B-102.jpg",
        "a" * 64,
    )
    mismo_contenido = identificador_foto(
        "https://planta.example/fotos/B-102.jpg",
        "/datos/otro/B-102.jpg",
        "a" * 64,
    )
    contenido_distinto = identificador_foto(
        "https://planta.example/fotos/B-102.jpg",
        "/datos/otro/B-102.jpg",
        "b" * 64,
    )

    assert primero == mismo_contenido
    assert primero != contenido_distinto


def test_el_contexto_productivo_conserva_los_fragmentos_consultados() -> None:
    servicio = ServicioAnalisis(
        catalogo=None,  # type: ignore[arg-type] - sólo se prueba el context builder
        recuperador=RecuperadorFalso(),
    )

    contexto = servicio._construir_contexto(_evento(), _evidencia())  # noqa: SLF001

    assert contexto is not None
    assert contexto.parte_id == "parte-confirmado-1"
    assert contexto.filas[0]["variable"] == "vibracion"
    assert contexto.fotos == ["https://planta.example/fotos/B-102.jpg"]
    assert contexto.tendencia[0].ventana == "6 h"
    assert contexto.tendencia[0].fuente == "historiador"
    assert contexto.tendencia[0].muestras == 24
    assert contexto.fragmentos[0].cita == "MBP-4 §13.2"
    assert contexto.citas == ["MBP-4 §13.2"]
