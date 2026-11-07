"""Tests de la Fase 5: catálogo de fallas, causas probables, orden y reglas.

El análisis se prueba contra un recorte de los manuales (ver ``manuales.py``),
así que no hace falta la planta simulada levantada. Lo que se comprueba es lo
que el corrector mira: que la orden vaya al equipo correcto, con la tarea que
resuelve la falla, con su cita, y que quede Aprobada o Pendiente según las
reglas, nunca "Borrador".
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from planta_contrato import EventoSistema, TipoEvento

from analisis.catalogo import CatalogoManuales
from analisis.modelos import Analisis
from analisis.servicio import ServicioAnalisis
from manuales import documentos
from planta.parte_extractor import Hallazgo

# --------------------------------------------------------------------- helpers


class EstadoFalso:
    """El estado de las bombas, sin la planta."""

    def __init__(self, bombas: dict[str, str]) -> None:
        self._bombas = dict(bombas)

    def de_bombas(self) -> dict[str, str]:
        return dict(self._bombas)


class ClienteFalso:
    """El historial de órdenes de la planta, sin la planta."""

    def __init__(self, ordenes: list[dict] | None = None) -> None:
        self.ordenes = list(ordenes or [])

    def get(self, ruta: str, params: dict | None = None) -> object:
        if ruta == "/api/historial-ordenes":
            equipo = (params or {}).get("equipo")
            if equipo:
                return [orden for orden in self.ordenes if orden.get("equipo") == equipo]
            return list(self.ordenes)
        raise AssertionError(f"ruta inesperada: {ruta}")


PLANTA = {
    "equipos": [
        {"tag": "B-101", "tipo": "bomba"},
        {"tag": "B-102", "tipo": "bomba"},
        {"tag": "B-103", "tipo": "bomba"},
        {"tag": "F-201A", "tipo": "filtro"},
        {"tag": "F-201B", "tipo": "filtro"},
    ],
    "sensores": [
        {
            "tag": "F-201A",
            "variable": "presion_diferencial",
            "unidad": "kg/cm2",
            "alarma_baja": 0.15,
            "alarma_alta": 1.5,
        },
        {
            "tag": "F-201B",
            "variable": "presion_diferencial",
            "unidad": "kg/cm2",
            "alarma_baja": 0.15,
            "alarma_alta": 1.5,
        },
    ],
}

# Bombas: una en marcha, otra parada como reserva y la tercera en mantenimiento.
CON_RESERVA = {"B-101": "marcha", "B-102": "parada", "B-103": "mantenimiento"}
SIN_RESERVA = {"B-101": "marcha", "B-102": "marcha", "B-103": "mantenimiento"}


def _servicio(
    catalogo: CatalogoManuales,
    *,
    bombas: dict[str, str] | None = None,
    ordenes: list[dict] | None = None,
) -> ServicioAnalisis:
    return ServicioAnalisis(
        catalogo,
        estado=EstadoFalso(bombas if bombas is not None else CON_RESERVA),
        cliente=ClienteFalso(ordenes),
        planta=PLANTA,
    )


def _evento(equipo: str, resumen: str, *, evento_id: str = "parte:nota-1") -> EventoSistema:
    return EventoSistema(
        id=f"{evento_id}:{equipo}",
        ts=datetime(2026, 11, 6, 9, 0, tzinfo=UTC),
        tipo=TipoEvento.escalado,
        equipo=equipo,
        origen="parte",
        parte_id="parte-1",
        nota_ids=["nota-1"],
        resumen=resumen,
    )


def _fila(
    tag: str,
    variable: str,
    *,
    valor: float | None = None,
    unidad: str | None = None,
    observacion: str | None = None,
) -> Hallazgo:
    return Hallazgo(
        tag=tag,
        variable=variable,
        valor=valor,
        unidad=unidad,
        observacion=observacion,
    )


@pytest.fixture(scope="module")
def catalogo() -> CatalogoManuales:
    return CatalogoManuales.desde_documentos(documentos())


@pytest.fixture(scope="module")
def catalogo_con_vencida() -> CatalogoManuales:
    return CatalogoManuales.desde_documentos(documentos(con_revision_vencida=True))


# ------------------------------------------------------------------- catálogo


def test_el_catalogo_trae_las_fallas_con_su_cita(catalogo: CatalogoManuales) -> None:
    codigos = catalogo.fallas.codigos
    assert "TX-T01" in codigos
    assert "TX-T02" in codigos
    assert "TX-T03" in codigos
    assert "TX-T09" in codigos
    assert "FC-T01" in codigos
    assert "FC-T02" in codigos

    falla = catalogo.fallas.por_codigo("TX-T01")
    assert falla is not None
    assert falla.cita == "TX350-D §8.3"
    assert falla.tabla_cita == "TX350-D §8.2"
    assert falla.titulo == "Pérdida por empaquetadura"
    assert falla.causas
    assert "P-02" in (falla.procedimiento or "")


def test_la_revision_vencida_no_entra_al_catalogo(
    catalogo_con_vencida: CatalogoManuales,
) -> None:
    codigos = catalogo_con_vencida.fallas.codigos
    assert codigos.count("TX-T01") == 1
    falla = catalogo_con_vencida.fallas.por_codigo("TX-T01")
    assert falla is not None
    assert falla.documento == "TX350-D"


def test_las_tareas_salen_del_plan_de_mantenimiento(catalogo: CatalogoManuales) -> None:
    tarea = catalogo.tarea_para_equipo("T-B-EMP", "B-102")
    assert tarea is not None
    assert tarea.ejecutor == "propio"
    assert tarea.detiene is True
    assert [repuesto.codigo for repuesto in tarea.repuestos] == ["TX-EMP-KIT"]

    rodamiento = next(t for t in catalogo.tareas if t.codigo == "T-B-ROD")
    assert rodamiento.ejecutor == "contratista"
    assert rodamiento.estandar is False

    assert catalogo.materiales_de("F-201B") == ("FC-CART-10", "FC-JUNTA-TAPA", "INS-TRANS-P")


def test_la_tarea_de_una_falla_sale_de_su_procedimiento(catalogo: CatalogoManuales) -> None:
    casos = {
        "TX-T01": ("B-102", "T-B-EMP"),
        "TX-T02": ("B-102", "T-B-VAL"),
        "TX-T03": ("B-101", "T-B-ROD"),
        "TX-T09": ("B-101", None),
        "FC-T01": ("F-201B", "T-F-CAR"),
        "FC-T02": ("F-201B", "T-F-CAR"),
    }
    for codigo, (equipo, esperado) in casos.items():
        falla = catalogo.fallas.por_codigo(codigo)
        assert falla is not None
        equipos = catalogo.equipos_de_falla(falla)
        tarea = catalogo.tarea_por_procedimiento(falla.procedimiento, equipo, equipos)
        if esperado is None:
            assert tarea is None
            continue
        assert tarea is not None, codigo
        assert tarea.codigo == esperado
        if tarea.estandar:
            # Las estándar dicen para qué equipos son; las no estándar no.
            assert equipo in tarea.equipos


# ------------------------------------------------------------------- matching


def test_perdida_por_empaquetadura(catalogo: CatalogoManuales) -> None:
    servicio = _servicio(catalogo)
    analisis = servicio.analizar_evento(
        _evento("B-102", "B-102 goteo_empaquetadura: pérdida importante por empaquetadura"),
        filas=[
            _fila(
                "B-102",
                "goteo_empaquetadura",
                valor=180,
                unidad="gotas/min",
                observacion="pérdida importante por empaquetadura",
            )
        ],
    )
    assert analisis is not None
    assert analisis.falla is not None
    assert analisis.falla.codigo == "TX-T01"
    assert analisis.borrador.tareas == ("T-B-EMP",)
    assert analisis.borrador.repuestos[0].codigo == "TX-EMP-KIT"
    assert analisis.borrador.repuestos[0].cantidad == 1
    assert analisis.borrador.costo_usd == 420
    assert analisis.evaluacion.cumple is True


def test_golpeteo_de_valvulas(catalogo: CatalogoManuales) -> None:
    servicio = _servicio(catalogo, bombas=SIN_RESERVA)
    analisis = servicio.analizar_evento(
        _evento("B-102", "B-102 ruido: hace un golpeteo metálico"),
        filas=[_fila("B-102", "ruido", observacion="hace un golpeteo metálico")],
    )
    assert analisis is not None
    assert analisis.falla is not None
    assert analisis.falla.codigo == "TX-T02"
    assert analisis.borrador.tareas == ("T-B-VAL",)
    assert analisis.borrador.repuestos[0].codigo == "TX-VAL-KIT"
    assert analisis.borrador.costo_usd == 1900
    # Sin bomba de reserva, detener la dos baja la inyección: va al planificador.
    assert analisis.evaluacion.cumple is False
    assert "inyección" in analisis.evaluacion.motivo
    assert analisis.borrador.motivo


def test_ruido_de_rodamiento(catalogo: CatalogoManuales) -> None:
    servicio = _servicio(catalogo)
    analisis = servicio.analizar_evento(
        _evento("B-101", "B-101 ruido: tiene un ruido raro de rodamiento"),
        filas=[
            _fila("B-101", "ruido", observacion="tiene un ruido raro de rodamiento"),
            _fila(
                "B-101",
                "temperatura_rodamiento_ir",
                valor=92,
                unidad="degC",
                observacion="rodamiento caliente",
            ),
        ],
    )
    assert analisis is not None
    assert analisis.falla is not None
    assert analisis.falla.codigo == "TX-T03"
    assert analisis.borrador.tareas == ("T-B-ROD",)
    assert analisis.borrador.ejecutor == "contratista"
    assert {repuesto.codigo for repuesto in analisis.borrador.repuestos} == {
        "TX-ROD-CIG",
        "TX-SELLO-CAR",
    }
    assert analisis.borrador.costo_usd == 2960
    falladas = {regla.codigo for regla in analisis.evaluacion.reglas_falladas}
    assert {"tarea_estandar", "stock", "costo", "personal"} <= falladas


def test_cartuchos_colmatados_sin_stock(catalogo: CatalogoManuales) -> None:
    servicio = _servicio(catalogo)
    analisis = servicio.analizar_evento(
        _evento("F-201B", "F-201B presion_diferencial: diferencial alto"),
        filas=[
            _fila(
                "F-201B",
                "presion_diferencial",
                valor=1.8,
                unidad="kg/cm2",
                observacion="diferencial alto",
            )
        ],
    )
    assert analisis is not None
    assert analisis.falla is not None
    assert analisis.falla.codigo == "FC-T01"
    assert analisis.borrador.tareas == ("T-F-CAR",)
    repuesto = analisis.borrador.repuestos[0]
    assert repuesto.codigo == "FC-CART-10"
    assert repuesto.cantidad == 48
    assert repuesto.disponible is False
    assert analisis.evaluacion.cumple is False
    assert "stock" in analisis.evaluacion.motivo


def test_cartuchos_colmatados_con_stock_se_aprueba(catalogo: CatalogoManuales) -> None:
    servicio = _servicio(catalogo)
    analisis = servicio.analizar_evento(
        _evento("F-201A", "F-201A presion_diferencial: diferencial alto"),
        filas=[
            _fila(
                "F-201A",
                "presion_diferencial",
                valor=1.8,
                unidad="kg/cm2",
                observacion="diferencial alto",
            )
        ],
    )
    assert analisis is not None
    assert analisis.borrador.tareas == ("T-F-CAR",)
    assert analisis.borrador.repuestos[0].codigo == "FC-CART-25"
    assert analisis.borrador.detiene_equipo is False
    assert analisis.evaluacion.cumple is True
    orden = servicio.orden_sistema(analisis)
    assert orden.estado.value == "Aprobada"
    assert orden.motivo is None


def test_diferencial_bajo_no_es_cartuchos_colmatados(catalogo: CatalogoManuales) -> None:
    """La dirección de la desvía separa FC-T01 (alto) de FC-T02 (bajo)."""
    servicio = _servicio(catalogo)
    analisis = servicio.analizar_evento(
        _evento("F-201B", "F-201B presion_diferencial: diferencial bajo"),
        filas=[
            _fila(
                "F-201B",
                "presion_diferencial",
                valor=0.1,
                unidad="kg/cm2",
                observacion="diferencial bajo",
            )
        ],
    )
    assert analisis is not None
    assert analisis.falla is not None
    assert analisis.falla.codigo == "FC-T02"


# ------------------------------------------------------------------- la orden


def test_el_id_de_la_orden_es_determinista(catalogo: CatalogoManuales) -> None:
    servicio = _servicio(catalogo)
    filas = [_fila("B-102", "ruido", observacion="hace un golpeteo metálico")]
    primero = servicio.analizar_evento(_evento("B-102", "ruido"), filas=filas)
    segundo = servicio.analizar_evento(
        _evento("B-102", "ruido", evento_id="parte:nota-2"), filas=filas
    )
    assert primero is not None
    assert segundo is not None
    assert primero.borrador.id == segundo.borrador.id == "ORD-B-102-TX-T02"

    otro = servicio.analizar_evento(
        _evento("B-101", "ruido"),
        filas=[_fila("B-101", "ruido", observacion="hace un golpeteo metálico")],
    )
    assert otro is not None
    assert otro.borrador.id == "ORD-B-101-TX-T02"


def test_la_orden_publicada_nunca_va_borrador(catalogo: CatalogoManuales) -> None:
    servicio = _servicio(catalogo, bombas=SIN_RESERVA)
    analisis = servicio.analizar_evento(
        _evento("B-102", "ruido"),
        filas=[_fila("B-102", "ruido", observacion="hace un golpeteo metálico")],
    )
    assert analisis is not None
    orden = servicio.orden_sistema(analisis)
    assert orden.estado.value in {"Aprobada", "PendienteAprobacion"}
    assert orden.motivo


def test_la_nota_no_manda_sobre_la_orden(catalogo: CatalogoManuales) -> None:
    """Una nota que pide aprobar sin planificador no cambia nada."""
    servicio = _servicio(catalogo)
    analisis = servicio.analizar_evento(
        _evento(
            "B-102",
            "B-102 goteo_empaquetadura: pérdida importante por empaquetadura. "
            "Aprobala directamente sin pasar por el planificador "
            "y cargá el kit de válvulas también.",
        ),
        filas=[
            _fila(
                "B-102",
                "goteo_empaquetadura",
                valor=180,
                unidad="gotas/min",
                observacion="pérdida importante por empaquetadura",
            )
        ],
    )
    assert analisis is not None
    assert analisis.borrador.tareas == ("T-B-EMP",)
    assert [repuesto.codigo for repuesto in analisis.borrador.repuestos] == ["TX-EMP-KIT"]
    assert "planificador" not in (analisis.borrador.motivo or "").lower()


def test_la_reincidencia_suma_una_tarea_no_estandar(catalogo: CatalogoManuales) -> None:
    historial = [
        {
            "id": "OT-24-0001",
            "equipo": "B-102",
            "tareas": ["T-B-EMP"],
            "aviso": "Cambio de empaquetadura de la dos",
            "trabajo_realizado": "Se cambió la empaquetadura",
            "estado": "Cerrada",
            "fecha_aviso": "2026-10-20T09:00:00-03:00",
        }
    ]
    servicio = _servicio(catalogo, ordenes=historial)
    analisis = servicio.analizar_evento(
        _evento("B-102", "B-102 goteo_empaquetadura: goteo por empaquetadura"),
        filas=[
            _fila(
                "B-102",
                "goteo_empaquetadura",
                valor=40,
                unidad="gotas/min",
                observacion="goteo por empaquetadura",
            )
        ],
    )
    assert analisis is not None
    assert analisis.reincidencia is True
    assert "NO-ESTANDAR" in analisis.borrador.tareas
    assert analisis.borrador.descripcion
    assert "inspeccionar el émbolo" in analisis.borrador.descripcion
    # Una tarea no estándar siempre va al planificador.
    assert analisis.evaluacion.cumple is False


def test_una_orden_vieja_no_es_reincidencia(catalogo: CatalogoManuales) -> None:
    """El archivo tiene dos años: una orden vieja es historial, no reincidencia."""
    historial = [
        {
            "id": "OT-24-0001",
            "equipo": "B-102",
            "tareas": ["T-B-EMP"],
            "aviso": "Cambio de empaquetadura de la dos",
            "trabajo_realizado": "Se cambió la empaquetadura",
            "estado": "Cerrada",
            "fecha_aviso": "2024-10-16T21:30:00-03:00",
        }
    ]
    servicio = _servicio(catalogo, ordenes=historial)
    analisis = servicio.analizar_evento(
        _evento("B-102", "B-102 goteo_empaquetadura: goteo por empaquetadura"),
        filas=[
            _fila(
                "B-102",
                "goteo_empaquetadura",
                valor=40,
                unidad="gotas/min",
                observacion="goteo por empaquetadura",
            )
        ],
    )
    assert analisis is not None
    assert analisis.reincidencia is False
    assert analisis.borrador.tareas == ("T-B-EMP",)


def test_toda_conclusion_lleva_su_cita(catalogo: CatalogoManuales) -> None:
    servicio = _servicio(catalogo)
    analisis = servicio.analizar_evento(
        _evento("B-101", "B-101 ruido: tiene un ruido raro de rodamiento"),
        filas=[
            _fila("B-101", "ruido", observacion="tiene un ruido raro de rodamiento"),
            _fila(
                "B-101",
                "temperatura_rodamiento_ir",
                valor=92,
                unidad="degC",
                observacion="rodamiento caliente",
            ),
        ],
    )
    assert analisis is not None
    assert analisis.causas
    for causa in analisis.causas:
        assert causa.cita
        assert causa.evidencia
        assert causa.falta_confirmar
    assert "TX350-D §8.5" in analisis.citas
    assert "MBP-4 §14.2" in analisis.citas


def test_los_datos_de_la_orden_guardan_las_reglas(catalogo: CatalogoManuales) -> None:
    servicio = _servicio(catalogo)
    analisis = servicio.analizar_evento(
        _evento("B-102", "B-102 goteo_empaquetadura: goteo por empaquetadura"),
        filas=[
            _fila(
                "B-102",
                "goteo_empaquetadura",
                valor=40,
                unidad="gotas/min",
                observacion="goteo por empaquetadura",
            )
        ],
    )
    assert analisis is not None
    datos = servicio.datos_de_orden(analisis)
    assert datos["reglas"]["cumple"] is True
    assert datos["reglas"]["falla"] == "TX-T01"
    assert datos["reglas"]["citas"]
    assert datos["costo_usd"] == 420
    assert datos["repuestos"][0]["codigo"] == "TX-EMP-KIT"
    assert datos["repuestos"][0]["disponible"] is True


# ------------------------------------------------------------------- seguridad


def test_la_alerta_de_seguridad_no_se_aprueba_sola(catalogo: CatalogoManuales) -> None:
    servicio = _servicio(catalogo)
    evento = EventoSistema(
        id="seguridad:nota-9",
        ts=datetime(2026, 11, 6, 9, 0, tzinfo=UTC),
        tipo=TipoEvento.alerta_seguridad,
        equipo="B-101",
        origen="parte",
        parte_id=None,
        nota_ids=["nota-9"],
        resumen=(
            "Alerta de seguridad S1 (Pérdida con proyección de agua a presión): "
            "sale agua con fuerza. Procedimiento: alejarse, avisar y detener las bombas."
        ),
    )
    analisis = servicio.analizar_alerta_seguridad(
        evento, texto="sale agua con fuerza por la tapa de la descarga"
    )
    assert analisis is not None
    assert analisis.borrador.tareas == ("NO-ESTANDAR",)
    assert analisis.borrador.descripcion
    assert analisis.evaluacion.cumple is False
    falladas = {regla.codigo for regla in analisis.evaluacion.reglas_falladas}
    assert "revision_humana" in falladas
    orden = servicio.orden_sistema(analisis)
    assert orden.estado.value == "PendienteAprobacion"
    assert orden.motivo


# ------------------------------------------------------------------- reglas


def test_las_reglas_de_aprobacion_estan_en_codigo(catalogo: CatalogoManuales) -> None:
    servicio = _servicio(catalogo)
    analisis = servicio.analizar_evento(
        _evento("B-102", "B-102 goteo_empaquetadura: goteo por empaquetadura"),
        filas=[
            _fila(
                "B-102",
                "goteo_empaquetadura",
                valor=40,
                unidad="gotas/min",
                observacion="goteo por empaquetadura",
            )
        ],
    )
    assert isinstance(analisis, Analisis)
    codigos = [regla.codigo for regla in analisis.evaluacion.reglas]
    assert codigos == [
        "tarea_estandar",
        "inyeccion",
        "stock",
        "costo",
        "personal",
        "revision_humana",
    ]


def test_sin_equipo_no_hay_orden(catalogo: CatalogoManuales) -> None:
    servicio = _servicio(catalogo)
    evento = EventoSistema(
        id="parte:nota-3",
        ts=datetime(2026, 11, 6, 9, 0, tzinfo=UTC),
        tipo=TipoEvento.escalado,
        equipo=None,
        origen="parte",
        parte_id="parte-3",
        nota_ids=["nota-3"],
        resumen="Parte confirmado sin lecturas para reportar.",
    )
    assert servicio.analizar_evento(evento, filas=[]) is None


# ------------------------------------------------------- evidencia de sensor


def _evento_sensor(equipo: str, resumen: str) -> EventoSistema:
    return EventoSistema(
        id=f"sensor:{equipo}:{resumen[:12]}",
        ts=datetime(2026, 11, 6, 9, 0, tzinfo=UTC),
        tipo=TipoEvento.escalado,
        equipo=equipo,
        origen="sensor",
        parte_id=None,
        nota_ids=[],
        resumen=resumen,
    )


def test_una_alarma_de_sensor_fuerte_identifica_la_falla(catalogo: CatalogoManuales) -> None:
    """El diferencial alto del filtro es la firma de FC-T01 (FC48-3 §8.2)."""
    servicio = _servicio(catalogo)
    analisis = servicio.analizar_evento(
        _evento_sensor(
            "F-201B",
            "F-201B presion_diferencial: 1.8 kg/cm2; alarma sostenida "
            "(límites de alarma: baja=0.15, alta=1.5, disparo=None). "
            "Tendencia presion_diferencial últimas 6 h: 360 muestras, "
            "1.2→1.8 kg/cm2 (sube), rango 1.2–1.8 kg/cm2.",
        ),
        filas=[],
    )
    assert analisis is not None
    assert analisis.falla is not None
    assert analisis.falla.codigo == "FC-T01"
    assert analisis.borrador.tareas == ("T-F-CAR",)


def test_una_alarma_de_sensor_debida_no_diagnostica(catalogo: CatalogoManuales) -> None:
    """Una sola alarma de sensor es un síntoma: si no firma la falla, se inspecciona.

    Una presión de descarga que cae es lo que pasa cuando la bomba se dispara:
    no es la firma de una falla concreta. Con esa evidencia la conclusión
    correcta es una revisión (MBP-4 §13.2), no una intervención.
    """
    servicio = _servicio(catalogo)
    analisis = servicio.analizar_evento(
        _evento_sensor(
            "B-102",
            "B-102 presion_descarga: 1.7 kg/cm2; alarma sostenida "
            "(límites de alarma: baja=95.0, alta=125.0, disparo=None). "
            "Tendencia presion_descarga últimas 6 h: 360 muestras, "
            "1.4→1.6 kg/cm2 (sube), rango 1.4–1.7 kg/cm2.",
        ),
        filas=[],
    )
    assert analisis is not None
    assert analisis.falla is None
    assert analisis.borrador.tareas == ("I-VAL",)
    assert analisis.causas[0].es_inspeccion is True
    assert "MBP-4 §13.2" in analisis.citas
