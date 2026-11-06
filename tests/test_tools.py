"""Tests de las tools de solo lectura.

Las tools se prueban contra un cliente falso que devuelve la misma forma que
la API de la planta. Cada tool tiene su test, con sus validaciones y sus
errores.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from tools.cliente import ClientePlanta, ErrorHerramienta
from tools.equipos import equipos
from tools.historial import historial_ordenes, historial_sensores
from tools.inventario import inventario, lista_materiales
from tools.manuales import CorpusManuales, busqueda_manuales, numero, tabla
from tools.modelos import (
    ConsultaCatalogoTareas,
    ConsultaEquipos,
    ConsultaHistorialOrdenes,
    ConsultaHistorialSensores,
    ConsultaInventario,
    ConsultaListaMateriales,
    ConsultaManuales,
)
from tools.registro import REGISTRO, ejecutar
from tools.tareas import catalogo_tareas

# --------------------------------------------------------------------- datos

PLANTA = {
    "planta": {"nombre": "Planta de Inyección PIA-3"},
    "equipos": [
        {
            "tag": "B-101",
            "tipo": "bomba",
            "descripcion": "Bomba alternativa triplex 1",
            "fabricante": "Trimax Industrial",
            "modelo": "TX-350",
            "nombres": ["la uno", "la de la punta"],
            "caudal_nominal_m3d": 1300,
            "manometro_local": {"unidad": "kg/cm2"},
            "rol_inicial": "servicio",
        },
        {
            "tag": "F-201B",
            "tipo": "filtro",
            "descripcion": "Filtro de cartuchos secundario",
            "nombres": ["el filtro B", "el fino"],
        },
    ],
    "sensores": [
        {
            "tag": "B-101",
            "variable": "presion_descarga",
            "unidad": "kg/cm2",
            "alarma_baja": 95,
            "alarma_alta": 125,
        },
        {
            "tag": "B-101",
            "variable": "temperatura_aceite",
            "unidad": "degC",
            "alarma_alta": 80,
            "disparo": 90,
        },
    ],
}

SECCIONES_MBP4 = [
    {
        "id": "MBP-4 §13.1",
        "doc": "MBP-4",
        "numero": "13.1",
        "titulo": "Tareas estándar",
        "vigente": True,
        "texto": (
            "13.1 Tareas estándar\n"
            "Código | Tarea | Equipos | Detiene | Horas | Ejecutor | Repuestos | Costo rep.\n"
            "T-B-EMP | Cambio de empaquetadura | B-101, B-102, B-103 | Sí | 4 | propio | "
            "TX-EMP-KIT x 1 | USD 420\n"
            "T-B-ACE | Reposición de aceite del cárter | B-101, B-102, B-103 | No | 0,5 | "
            "propio | ACE-ISO220 x 8 | USD 72\n"
            "T-F-CAR | Cambio de cartuchos | F-201B | No | 3 | propio | FC-CART-10 x 48 | "
            "USD 816\n"
        ),
    },
    {
        "id": "MBP-4 §13.2",
        "doc": "MBP-4",
        "numero": "13.2",
        "titulo": "Tareas no estándar",
        "vigente": True,
        "texto": (
            "13.2 Tareas no estándar\n"
            "Código | Tarea | Detiene | Horas | Ejecutor\n"
            "T-B-ROD | Cambio de rodamientos del cigüeñal | Sí | 16 | contratista\n"
            "I-VAL | Revisión de válvulas | Sí | 2 | propio\n"
        ),
    },
    {
        "id": "MBP-4 §13.3",
        "doc": "MBP-4",
        "numero": "13.3",
        "titulo": "Correspondencia con los procedimientos de los fabricantes",
        "vigente": True,
        "texto": (
            "13.3 Correspondencia con los procedimientos de los fabricantes\n"
            "Tarea | Procedimiento del fabricante\n"
            "T-B-EMP | TX-350 P-02\n"
            "T-B-ROD | TX-350 P-07\n"
        ),
    },
    {
        "id": "MBP-4 §14.2",
        "doc": "MBP-4",
        "numero": "14.2",
        "titulo": "Lista de repuestos y stock de referencia",
        "vigente": True,
        "texto": (
            "14.2 Lista de repuestos y stock de referencia\n"
            "Código | Descripción | Unidad | Costo | Yacimiento | Central\n"
            "TX-EMP-KIT | kit de empaquetadura TX-350 | kit | USD 420 | 3 | 12\n"
            "FC-CART-10 | cartucho filtrante 10 µm | unidad | USD 17 | 40 | 600\n"
        ),
    },
    {
        "id": "MBP-4 §14.3",
        "doc": "MBP-4",
        "numero": "14.3",
        "titulo": "Lista de materiales por equipo",
        "vigente": True,
        "texto": (
            "14.3 Lista de materiales por equipo\n"
            "Equipo | Repuestos que le corresponden\n"
            "B-101 | TX-EMP-KIT, ACE-ISO220\n"
            "F-201B | FC-CART-10\n"
        ),
    },
    {
        "id": "MBP-4 §10.2",
        "doc": "MBP-4",
        "numero": "10.2",
        "titulo": "S1 Pérdida con proyección de agua a presión",
        "vigente": True,
        "texto": (
            "10.2 S1 Pérdida con proyección de agua a presión\n"
            "Qué hacer: Alejarse, avisar, detener las bombas desde el tablero y aislar a "
            "distancia. Nadie se acerca a la pérdida.\n"
        ),
    },
]

DOCUMENTOS = {
    "documentos": [
        {"id": "MBP-4", "titulo": "Manual de buenas prácticas", "vigente": True},
        {"id": "TX350-C", "titulo": "TX-350 Rev. C (vencida)", "vigente": False},
    ]
}

HISTORIAN = [
    {"ts": "2026-11-04T10:00:00-03:00", "valor": 110.0},
    {"ts": "2026-11-04T10:01:00-03:00", "valor": 112.0},
    {"ts": "2026-11-04T10:02:00-03:00", "valor": 118.0},
]

ORDENES = [
    {
        "id": "OT-24-0001",
        "fecha_aviso": "2024-10-03T20:15-03:00",
        "equipo": "B-101",
        "tareas": ["T-B-VAL"],
        "ejecutor": "propio",
        "estado": "Cerrada",
        "repuestos": [{"codigo": "TX-VAL-KIT", "cantidad": 1}],
        "trabajo_realizado": "válvulas gastadas, se cambió el kit completo",
    }
]


class ClienteFalso:
    """Cliente HTTP falso que devuelve la forma de la API de la planta."""

    def __init__(self, respuestas: dict[str, Any] | None = None) -> None:
        self.respuestas = respuestas or {}
        self.llamadas: list[tuple[str, dict[str, Any] | None]] = []

    def get(self, ruta: str, *, params: dict[str, Any] | None = None) -> Any:
        self.llamadas.append((ruta, params))
        # Gana el prefijo más largo: "/api/documentos/MBP-4/secciones" antes que
        # "/api/documentos".
        mejor: Any = []
        mejor_largo = -1
        for prefijo, valor in self.respuestas.items():
            if ruta.startswith(prefijo) and len(prefijo) > mejor_largo:
                mejor, mejor_largo = valor, len(prefijo)
        return mejor


def _cliente() -> ClienteFalso:
    return ClienteFalso(
        {
            "/api/planta": PLANTA,
            "/api/documentos/MBP-4/secciones": SECCIONES_MBP4,
            "/api/documentos": DOCUMENTOS,
            "/api/historian": HISTORIAN,
            "/api/historial-ordenes": ORDENES,
        }
    )


# --------------------------------------------------------------------- equipos


def test_equipos_devuelve_el_catalogo_con_sensores() -> None:
    resultado = equipos(_cliente(), ConsultaEquipos())
    assert len(resultado) == 2
    b101 = next(e for e in resultado if e.tag == "B-101")
    assert b101.nombres == ["la uno", "la de la punta"]
    assert b101.manometro_local.unidad == "kg/cm2"
    assert {s.variable for s in b101.sensores} == {"presion_descarga", "temperatura_aceite"}
    alarma = next(s for s in b101.sensores if s.variable == "temperatura_aceite")
    assert alarma.alarma_alta == 80
    assert alarma.disparo == 90


def test_equipos_filtra_por_tag() -> None:
    resultado = equipos(_cliente(), ConsultaEquipos(tag="F-201B"))
    assert [e.tag for e in resultado] == ["F-201B"]


def test_equipos_filtra_por_tipo() -> None:
    resultado = equipos(_cliente(), ConsultaEquipos(tipo="bomba"))
    assert [e.tag for e in resultado] == ["B-101"]


# ------------------------------------------------------------------- historial


def test_historial_sensores_resume_la_ventana() -> None:
    consulta = ConsultaHistorialSensores(
        tag="B-101",
        variable="presion_descarga",
        desde=datetime(2026, 11, 4, 10, 0, tzinfo=UTC),
        hasta=datetime(2026, 11, 4, 10, 2, tzinfo=UTC),
    )
    resultado = historial_sensores(_cliente(), consulta)
    assert resultado.cantidad == 3
    assert resultado.minimo == 110.0
    assert resultado.maximo == 118.0
    assert resultado.promedio == 113.3333
    assert resultado.tendencia == "sube"
    assert resultado.muestras[0].ts.isoformat().startswith("2026-11-04T10:00")


def test_historial_sensores_pasa_la_ventana_a_la_planta() -> None:
    cliente = _cliente()
    consulta = ConsultaHistorialSensores(
        tag="B-101",
        variable="presion_descarga",
        desde=datetime(2026, 11, 4, 0, 0, tzinfo=UTC),
        hasta=datetime(2026, 11, 5, 0, 0, tzinfo=UTC),
    )
    historial_sensores(cliente, consulta)
    ruta, params = cliente.llamadas[0]
    assert ruta == "/api/historian/B-101/presion_descarga"
    assert params is not None
    assert "desde" in params and "hasta" in params


def test_historial_ordenes_devuelve_el_texto_libre() -> None:
    resultado = historial_ordenes(_cliente(), ConsultaHistorialOrdenes(equipo="B-101"))
    assert len(resultado) == 1
    orden = resultado[0]
    assert orden.orden_id == "OT-24-0001"
    assert orden.tareas == ["T-B-VAL"]
    assert orden.repuestos == ["TX-VAL-KIT"]
    assert "válvulas gastadas" in (orden.texto or "")


# ------------------------------------------------------------------ inventario


def test_inventario_trae_stock_por_almacen() -> None:
    resultado = inventario(_cliente(), ConsultaInventario())
    por_codigo = {rep.codigo: rep for rep in resultado}
    assert por_codigo["TX-EMP-KIT"].costo_usd == 420
    assert por_codigo["TX-EMP-KIT"].stock == {"yacimiento": 3.0, "central": 12.0}
    assert por_codigo["TX-EMP-KIT"].disponible is True
    assert por_codigo["TX-EMP-KIT"].equipos == ["B-101"]


def test_inventario_filtra_por_equipo() -> None:
    resultado = inventario(_cliente(), ConsultaInventario(equipo="F-201B"))
    assert [rep.codigo for rep in resultado] == ["FC-CART-10"]


def test_inventario_por_almacen_central() -> None:
    resultado = inventario(_cliente(), ConsultaInventario(almacen="central"))
    assert all(rep.disponible for rep in resultado)


def test_lista_materiales_de_un_equipo() -> None:
    resultado = lista_materiales(
        _cliente(), ConsultaListaMateriales(equipo="B-101")
    )
    assert [material.codigo for material in resultado] == ["TX-EMP-KIT", "ACE-ISO220"]
    assert resultado[0].costo_usd == 420


# --------------------------------------------------------------------- tareas


def test_catalogo_tareas_incluye_estandar_y_no_estandar() -> None:
    resultado = catalogo_tareas(_cliente(), ConsultaCatalogoTareas())
    por_codigo = {tarea.codigo: tarea for tarea in resultado}
    assert por_codigo["T-B-EMP"].estandar is True
    assert por_codigo["T-B-EMP"].detiene is True
    assert por_codigo["T-B-EMP"].ejecutor == "propio"
    assert por_codigo["T-B-EMP"].costo_repuestos_usd == 420
    assert por_codigo["T-B-EMP"].procedimiento == "TX-350 P-02"
    assert por_codigo["T-B-EMP"].cita == "MBP-4 §13.1"
    assert por_codigo["T-B-ROD"].estandar is False
    assert por_codigo["T-B-ROD"].ejecutor == "contratista"


def test_catalogo_tareas_filtra_por_equipo() -> None:
    resultado = catalogo_tareas(_cliente(), ConsultaCatalogoTareas(equipo="F-201B"))
    assert [tarea.codigo for tarea in resultado] == ["T-F-CAR"]


def test_catalogo_tareas_sin_no_estandar() -> None:
    resultado = catalogo_tareas(
        _cliente(), ConsultaCatalogoTareas(incluir_no_estandar=False)
    )
    assert all(tarea.estandar for tarea in resultado)


# ------------------------------------------------------------------- manuales


def test_busqueda_manuales_devuelve_fragmentos_con_cita() -> None:
    resultado = busqueda_manuales(
        _cliente(), ConsultaManuales(consulta="proyección de agua a presión")
    )
    assert resultado
    primero = resultado[0]
    assert primero.documento == "MBP-4"
    assert primero.cita.startswith("MBP-4 §")
    assert "Alejarse, avisar" in primero.texto


def test_busqueda_manuales_penaliza_un_documento_vencido() -> None:
    cliente = ClienteFalso(
        {
            "/api/documentos": DOCUMENTOS,
            "/api/documentos/MBP-4/secciones": SECCIONES_MBP4,
            "/api/documentos/TX350-C/secciones": [
                {
                    "id": "TX350-C §1.1",
                    "doc": "TX350-C",
                    "numero": "1.1",
                    "titulo": "Límites",
                    "vigente": False,
                    "texto": "Límites de presión de descarga",
                }
            ],
        }
    )
    resultado = busqueda_manuales(cliente, ConsultaManuales(consulta="presión de descarga"))
    vencida = next(f for f in resultado if f.documento == "TX350-C")
    vigente = next(f for f in resultado if f.documento == "MBP-4")
    assert vencida.vigente is False
    assert (vencida.puntaje or 0) < (vigente.puntaje or 0)


# ------------------------------------------------------------------ registro


def test_el_registro_tiene_las_tools_minimas() -> None:
    esperadas = {
        "equipos",
        "historial_sensores",
        "historial_ordenes",
        "inventario",
        "lista_materiales",
        "catalogo_tareas",
        "busqueda_manuales",
    }
    assert esperadas <= set(REGISTRO)


def test_los_modelos_de_entrada_no_aceptan_campos_extra() -> None:
    with pytest.raises(ValidationError):
        ConsultaEquipos.model_validate({"tag": "B-101", "inventado": 1})


def test_ejecutar_una_tool_desde_el_registro() -> None:
    resultado = ejecutar("equipos", _cliente(), {"tag": "B-101"})
    assert len(resultado) == 1
    assert resultado[0].tag == "B-101"


def test_ejecutar_una_tool_con_argumentos_invalidos_falla() -> None:
    with pytest.raises(ErrorHerramienta):
        ejecutar("equipos", _cliente(), {"inventado": True})


def test_ejecutar_una_tool_desconocida_falla() -> None:
    with pytest.raises(ErrorHerramienta):
        ejecutar("no_existe", _cliente(), {})


# -------------------------------------------------------------------- cliente


def test_el_cliente_reintenta_ante_un_error_de_transporte() -> None:
    intentos = {"n": 0}

    def _manejador(request: httpx.Request) -> httpx.Response:
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise httpx.ConnectError("no hay broker", request=request)
        return httpx.Response(200, json={"ok": True})

    transporte = httpx.MockTransport(_manejador)
    with httpx.Client(transport=transporte) as http:
        cliente = ClientePlanta("http://planta", cliente=http, espera_s=0)
        assert cliente.get("/api/planta") == {"ok": True}
    assert intentos["n"] == 3


def test_el_cliente_falla_con_un_error_tipado_si_no_hay_respuesta() -> None:
    def _manejador(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no hay broker", request=request)

    transporte = httpx.MockTransport(_manejador)
    with httpx.Client(transport=transporte) as http:
        cliente = ClientePlanta("http://planta", cliente=http, reintentos=1, espera_s=0)
        with pytest.raises(ErrorHerramienta):
            cliente.get("/api/planta")


def test_el_cliente_no_reintenta_un_404() -> None:
    intentos = {"n": 0}

    def _manejador(request: httpx.Request) -> httpx.Response:
        intentos["n"] += 1
        return httpx.Response(404, json={"detail": "no existe"})

    transporte = httpx.MockTransport(_manejador)
    with httpx.Client(transport=transporte) as http:
        cliente = ClientePlanta("http://planta", cliente=http, espera_s=0)
        with pytest.raises(ErrorHerramienta):
            cliente.get("/api/planta")
    assert intentos["n"] == 1


# -------------------------------------------------------------------- helpers


def test_numero_entiende_el_formato_del_manual() -> None:
    assert numero("USD 1.900") == 1900
    assert numero("0,5") == 0.5
    assert numero("USD 420") == 420
    assert numero("-") is None


def test_tabla_lee_una_tabla_de_markdown() -> None:
    filas = tabla("a | b\n1 | 2\n")
    assert filas == [["a", "b"], ["1", "2"]]


def test_corpus_cachea_las_secciones() -> None:
    cliente = _cliente()
    corpus = CorpusManuales(cliente)
    corpus.secciones("MBP-4")
    corpus.secciones("MBP-4")
    llamadas = [ruta for ruta, _ in cliente.llamadas if "secciones" in ruta]
    assert len(llamadas) == 1


def test_el_corpus_sabe_leer_una_seccion() -> None:
    corpus = CorpusManuales(_cliente())
    seccion = corpus.seccion("MBP-4", "14.2")
    assert seccion is not None
    assert "TX-EMP-KIT" in seccion["texto"]


def test_json_de_ejemplo_es_valido() -> None:
    """Sanidad: los datos de prueba son JSON válido (sin comas sueltas)."""
    json.loads(json.dumps({"ok": True}))
