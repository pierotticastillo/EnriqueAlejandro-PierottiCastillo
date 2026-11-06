"""Tests de la Fase 4: corpus, índice híbrido, citas y evaluación."""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path

import pytest

from rag.contexto import citas_en_texto, construir_contexto, validar_citas
from rag.corpus import ChunkRag, CorpusArchivos, CorpusPlanta, DocumentoRag
from rag.embedder import EmbedderHashing
from rag.evaluar import evaluar
from rag.indice import IndiceLexico, IndiceRag
from rag.memoria import corte
from rag.retriever import _fusionar
from tools.manuales import busqueda_manuales
from tools.modelos import ConsultaManuales, FragmentoManual
from tools.rag import busqueda_rag


class ClienteFalso:
    def __init__(self, respuestas: dict[str, object]) -> None:
        self.respuestas = respuestas

    def get(self, ruta: str, **_kwargs):
        if ruta not in self.respuestas:
            raise AssertionError(f"ruta inesperada: {ruta}")
        return self.respuestas[ruta]


DOCUMENTOS = {
    "documentos": [
        {
            "id": "MBP-4",
            "titulo": "Manual de buenas prácticas",
            "tipo": "manual_operadora",
            "emisor": "Operadora",
            "revision": "4",
            "vigente": True,
            "equipos": ["B-101", "F-201A"],
        },
        {
            "id": "TX350-C",
            "titulo": "Bomba TX-350 Rev. C",
            "tipo": "manual_fabricante",
            "emisor": "Trimax",
            "revision": "C",
            "vigente": False,
            "equipos": ["B-101"],
        },
    ]
}

SECCIONES = {
    "/api/documentos/MBP-4/secciones": [
        {
            "id": "MBP-4 §10.2",
            "doc": "MBP-4",
            "numero": "10.2",
            "titulo": "Pérdida con proyección",
            "texto": "Alejarse, avisar y detener las bombas. No acercarse a la pérdida.",
            "pagina": 31,
            "vigente": True,
        },
        {
            "id": "MBP-4 §5.1",
            "doc": "MBP-4",
            "numero": "5.1",
            "titulo": "Límites de alarma",
            "texto": "Los límites de vibración y temperatura salen del sistema de control.",
            "pagina": 15,
            "vigente": True,
        },
    ],
    "/api/documentos/TX350-C/secciones": [
        {
            "id": "TX350-C §3.2",
            "doc": "TX350-C",
            "numero": "3.2",
            "titulo": "Límites viejos",
            "texto": "Vibración de alarma 4,5 mm/s y disparo 7,1 mm/s.",
            "pagina": 12,
            "vigente": False,
        }
    ],
}

ORDENES = [
    {
        "id": "OT-25-0024",
        "fecha_aviso": "2025-05-02T10:00-03:00",
        "equipo": "B-101",
        "tipo": "correctiva",
        "tareas": ["T-B-ALIN"],
        "aviso": "vibracion moderada",
        "trabajo_realizado": "se alineo el acople, no se cambiaron valvulas",
        "repuestos": [{"codigo": "TX-ACOP", "cantidad": 1}],
        "codigo_falla": "F01",
    }
]


def _documentos() -> list[DocumentoRag]:
    return [
        DocumentoRag(
            clave="MBP-4",
            titulo="Manual de buenas prácticas",
            tipo="manual_operadora",
            fabricante="Operadora",
            equipos=("B-101",),
            version="4",
            chunks=(
                ChunkRag(
                    seccion="10.2",
                    cita="MBP-4 §10.2",
                    titulo="Pérdida con proyección",
                    texto="Alejarse, avisar y detener las bombas.",
                ),
                ChunkRag(
                    seccion="5.1",
                    cita="MBP-4 §5.1",
                    titulo="Límites de alarma",
                    texto="Los límites de vibración salen de la planta.",
                ),
            ),
        ),
        DocumentoRag(
            clave="TX350-C",
            titulo="Bomba TX-350 Rev. C",
            tipo="manual_fabricante",
            version="C",
            vigente=False,
            chunks=(
                ChunkRag(
                    seccion="3.2",
                    cita="TX350-C §3.2",
                    titulo="Límites viejos",
                    texto="Los límites de vibración de la revisión vieja.",
                    vigente=False,
                ),
            ),
        ),
    ]


# ------------------------------------------------------------------ embedder


def test_embedder_hashing_es_determinista_y_normalizado() -> None:
    embedder = EmbedderHashing(64)
    primero = embedder.embed(["presión de descarga de la bomba"])[0]
    segundo = embedder.embed(["presión de descarga de la bomba"])[0]
    assert primero == segundo
    assert len(primero) == 64
    assert abs(sum(valor * valor for valor in primero) - 1.0) < 1e-9


def test_embedder_hashing_distingue_textos() -> None:
    embedder = EmbedderHashing(64)
    bomba, filtro = embedder.embed(
        ["presión de descarga de la bomba", "cambio de cartucho del filtro"]
    )
    assert bomba != filtro


# ------------------------------------------------------------------ índice


def test_indice_devuelve_fragmentos_con_cita_y_excluye_vencidos() -> None:
    indice = IndiceRag(_documentos(), EmbedderHashing(64))
    resultado = indice.buscar("límites de vibración", top_k=5)
    assert resultado
    assert all(fragmento.cita for fragmento in resultado)
    assert all(fragmento.vigente for fragmento in resultado)
    assert all(fragmento.documento != "TX350-C" for fragmento in resultado)


def test_indice_incluye_vencidos_cuando_se_pide_para_evaluar() -> None:
    indice = IndiceRag(_documentos(), EmbedderHashing(64))
    citas = indice.buscar_citas("límites de vibración", 5, solo_vigentes=False)
    assert "TX350-C §3.2" in citas


def test_indice_filtra_por_documento() -> None:
    indice = IndiceRag(_documentos(), EmbedderHashing(64))
    resultado = indice.buscar("vibración", documento="MBP-4")
    assert {fragmento.documento for fragmento in resultado} == {"MBP-4"}


# ------------------------------------------------------------------ corpus


def test_corpus_planta_arma_manuales_e_historial() -> None:
    cliente = ClienteFalso(
        {
            "/api/documentos": DOCUMENTOS,
            "/api/historial-ordenes": ORDENES,
            **SECCIONES,
        }
    )
    documentos = CorpusPlanta(cliente).cargar()
    por_clave = {documento.clave: documento for documento in documentos}
    assert por_clave["MBP-4"].version == "4"
    assert por_clave["MBP-4"].chunks[0].cita == "MBP-4 §10.2"
    assert por_clave["TX350-C"].vigente is False
    historial = por_clave["historial"]
    assert historial.chunks[0].cita == "historial OT-25-0024"
    assert "acople" in historial.chunks[0].texto


def test_corpus_archivos_lee_la_copia_local(tmp_path: Path) -> None:
    base = tmp_path
    (base / "evaluacion").mkdir()
    (base / "historial").mkdir()
    (base / "indice.json").write_text(
        json.dumps(
            {
                "documentos": [
                    {
                        "id": "MBP-4",
                        "titulo": "Buenas prácticas",
                        "tipo": "manual_operadora",
                        "revision": "4",
                        "vigente": True,
                        "equipos": ["B-101"],
                        "secciones": [
                            {"numero": "10.2", "titulo": "Pérdida", "pagina": 31}
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (base / "fragmentos.jsonl").write_text(
        json.dumps(
            {
                "id": "MBP-4 §10.2",
                "doc": "MBP-4",
                "numero": "10.2",
                "titulo": "Pérdida",
                "pagina": 31,
                "vigente": True,
                "texto": "Alejarse y avisar.",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (base / "historial" / "ordenes.json").write_text(
        json.dumps(ORDENES, ensure_ascii=False), encoding="utf-8"
    )
    (base / "evaluacion" / "retrieval.json").write_text("[]", encoding="utf-8")
    documentos = CorpusArchivos(base).cargar()
    assert [documento.clave for documento in documentos] == ["MBP-4", "historial"]
    assert documentos[0].chunks[0].cita == "MBP-4 §10.2"


def test_corpus_planta_no_indexa_el_historial_dos_veces() -> None:
    """La planta sirve el historial como documento y como órdenes: va una vez."""
    cliente = ClienteFalso(
        {
            "/api/documentos": {
                "documentos": [
                    {"id": "MBP-4", "titulo": "Buenas prácticas", "vigente": True},
                    {"id": "historial", "titulo": "Historial", "tipo": "historial"},
                ]
            },
            "/api/documentos/MBP-4/secciones": [
                {"id": "MBP-4 §1", "numero": "1", "texto": "Texto."}
            ],
            "/api/documentos/historial/secciones": [
                {"id": "historial OT-24-0001", "numero": "OT-24-0001", "texto": "Orden."}
            ],
            "/api/historial-ordenes": [ORDENES[0], ORDENES[0]],
        }
    )
    documentos = CorpusPlanta(cliente).cargar()
    assert [documento.clave for documento in documentos] == ["MBP-4", "historial"]
    historial = documentos[-1]
    assert len(historial.chunks) == 1
    assert historial.chunks[0].cita == "historial OT-25-0024"


# ------------------------------------------------------------------ índice léxico


def test_indice_lexico_ordena_por_el_termino_que_aparece() -> None:
    indice = IndiceLexico(
        [
            "vibracion de la bomba triplex en mm/s",
            "cambio de aceite del carter cada 500 horas",
        ]
    )
    puntajes = indice.puntajes("vibracion mm/s")
    assert puntajes[0] > puntajes[1]


def test_indice_lexico_sin_terminos_devuelve_ceros() -> None:
    indice = IndiceLexico(["un texto", "otro texto"])
    assert indice.puntajes("de la") == [0.0, 0.0]
    assert indice.cantidad == 2


def test_fusion_de_senales_pondera_el_peso_lexico() -> None:
    lexicos = {"a": 10.0, "b": 5.0}
    densos = {"b": 0.9, "c": 0.8}
    fusionado = _fusionar(densos, lexicos, peso_lexico=1.0)
    # Con peso 1 el orden es el léxico; el candidato sólo denso queda al final.
    assert list(fusionado) == ["a", "b", "c"]
    assert fusionado["c"] == 0.0
    fusionado = _fusionar(densos, lexicos, peso_lexico=0.0)
    assert list(fusionado)[0] in {"b", "c"}


def test_el_indice_hibrido_ordena_por_el_cuerpo_de_la_seccion() -> None:
    """El título desambigua en el embedding, pero el BM25 mira el cuerpo."""
    indice = IndiceRag(_documentos(), EmbedderHashing(64))
    citas = indice.buscar_citas("vibracion", 5, peso_lexico=1.0)
    assert citas[0] == "MBP-4 §5.1"


# ------------------------------------------------------------------ evaluación


def test_evaluacion_mide_hit_recall_y_distractor() -> None:
    suite = [
        {
            "id": "q1",
            "tipo": "limites",
            "pregunta": "vibración",
            "relevantes": ["A §1", "A §2"],
            "distractores": ["B §1"],
        },
        {
            "id": "q2",
            "tipo": "seguridad",
            "pregunta": "agua",
            "relevantes": ["C §1"],
            "distractores": [],
        },
    ]

    def buscar(pregunta: str, k: int) -> list[str]:
        if pregunta == "vibración":
            return ["B §1", "A §1", "A §2"][:k]
        return ["C §1"][:k]

    resultado = evaluar(buscar, suite, ks=(1, 3, 5))
    assert resultado["n"] == 2
    assert resultado["total"]["hit@1"] == 0.5
    assert resultado["total"]["recall@3"] == 1.0
    assert resultado["total"]["distractor@1"] == 0.5
    assert resultado["preguntas"][0]["distractor@3"] == 1.0


# ------------------------------------------------------------------ contexto


def test_contexto_cita_los_fragmentos_y_valida_afirmaciones() -> None:
    class RecuperadorFalso:
        def buscar(self, consulta: ConsultaManuales) -> list[FragmentoManual]:
            return [
                FragmentoManual(
                    documento="MBP-4",
                    seccion="10.2",
                    cita="MBP-4 §10.2",
                    texto="Alejarse, avisar y detener las bombas.",
                    vigente=True,
                )
            ]

    contexto = construir_contexto(
        evento_id="parte:NOTA-1:B-101",
        equipo="B-101",
        estado="Escalado",
        resumen="Vibración sostenida",
        recuperador=RecuperadorFalso(),  # type: ignore[arg-type]
        consulta="vibración",
    )
    assert contexto.citas == ["MBP-4 §10.2"]
    assert "MBP-4 §10.2" in contexto.a_prompt()
    assert validar_citas("Según MBP-4 §10.2 hay que alejarse.", contexto) == []
    assert validar_citas("Según MBP-4 §99.9 hay que alejarse.", contexto) == ["MBP-4 §99.9"]


def test_contexto_recorta_el_presupuesto_de_caracteres() -> None:
    class RecuperadorFalso:
        def buscar(self, consulta: ConsultaManuales) -> list[FragmentoManual]:
            return [
                FragmentoManual(
                    documento="MBP-4",
                    seccion="1",
                    cita="MBP-4 §1",
                    texto="x" * 100,
                    vigente=True,
                ),
                FragmentoManual(
                    documento="MBP-4",
                    seccion="2",
                    cita="MBP-4 §2",
                    texto="y" * 100,
                    vigente=True,
                ),
            ]

    contexto = construir_contexto(
        evento_id="ev",
        estado="Escalado",
        resumen="resumen",
        recuperador=RecuperadorFalso(),  # type: ignore[arg-type]
        consulta="xx",
        max_caracteres=120,
    )
    assert len(contexto.fragmentos) == 1
    assert contexto.presupuesto_agotado is True


def test_citas_en_texto_reconoce_manuales_y_ordenes() -> None:
    assert citas_en_texto("Ver TX350-D §3.2 y historial OT-25-0024.") == {
        "TX350-D §3.2",
        "historial OT-25-0024",
    }


# ------------------------------------------------------------------ memoria y tool


def test_corte_de_memoria_respeta_los_dias() -> None:
    from datetime import datetime

    ahora = datetime(2026, 11, 5, tzinfo=UTC)
    assert corte(90, ahora).isoformat() == "2026-08-07T00:00:00+00:00"


def test_busqueda_rag_cae_a_lexica_si_el_indice_falla(monkeypatch) -> None:
    cliente = ClienteFalso(
        {
            "/api/documentos": DOCUMENTOS,
            **SECCIONES,
        }
    )

    def romper(_cliente):
        raise RuntimeError("sin índice")

    monkeypatch.setattr("tools.rag.crear_recuperador", romper)
    resultado = busqueda_rag(cliente, ConsultaManuales(consulta="pérdida de agua"))
    assert resultado
    assert resultado[0].cita.startswith("MBP-4 §")
    assert busqueda_manuales(cliente, ConsultaManuales(consulta="pérdida")) == resultado


def test_la_tool_rag_usa_el_indice_en_memoria() -> None:
    cliente = ClienteFalso(
        {
            "/api/documentos": DOCUMENTOS,
            "/api/historial-ordenes": ORDENES,
            **SECCIONES,
        }
    )
    resultado = busqueda_rag(cliente, ConsultaManuales(consulta="alejarse de la pérdida"))
    assert resultado
    assert resultado[0].cita == "MBP-4 §10.2"


@pytest.mark.parametrize(
    "consulta, esperado",
    [("vibración", "MBP-4 §5.1"), ("pérdida", "MBP-4 §10.2")],
)
def test_indice_ordena_por_consulta(consulta: str, esperado: str) -> None:
    indice = IndiceRag(_documentos(), EmbedderHashing(128))
    assert indice.buscar(consulta)[0].cita == esperado
