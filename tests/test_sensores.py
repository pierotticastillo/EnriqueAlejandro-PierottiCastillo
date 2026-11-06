"""Detector sostenido de alarmas según el catálogo MQTT/API de la planta."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from planta_contrato import Calidad, LecturaSensor, TipoEvento

from deteccion.sensores import DetectorSensores


def _planta() -> dict:
    return {
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
                "variable": "vibracion",
                "unidad": "mm/s",
                "alarma_alta": 7.1,
                "disparo": 11.2,
            },
        ]
    }


def _lectura(variable: str, valor: float | None, segundos: int, calidad=Calidad.buena):
    return LecturaSensor(
        tag="B-101",
        variable=variable,
        valor=valor,
        unidad="kg/cm2" if variable == "presion_descarga" else "mm/s",
        ts=datetime(2026, 11, 4, tzinfo=UTC) + timedelta(seconds=segundos),
        calidad=calidad,
    )


def test_alarma_alta_se_escala_tras_tres_muestras_y_no_repite_hasta_normalizar() -> None:
    detector = DetectorSensores(_planta())
    assert detector.evaluar(_lectura("presion_descarga", 125, 1)) is None
    assert detector.evaluar(_lectura("presion_descarga", 126, 2)) is None

    evento = detector.evaluar(_lectura("presion_descarga", 127, 3))
    assert evento is not None
    assert evento.tipo is TipoEvento.escalado
    assert evento.origen == "sensor"
    assert evento.equipo == "B-101"
    assert "presion_descarga" in evento.resumen
    assert detector.evaluar(_lectura("presion_descarga", 130, 4)) is None

    assert detector.evaluar(_lectura("presion_descarga", 110, 5)) is None
    assert detector.evaluar(_lectura("presion_descarga", 130, 6)) is None
    assert detector.evaluar(_lectura("presion_descarga", 130, 7)) is None
    assert detector.evaluar(_lectura("presion_descarga", 130, 8)) is not None


def test_disparo_es_inmediato_y_muestra_repetida_no_cuenta_dos_veces() -> None:
    detector = DetectorSensores(_planta())
    lectura = _lectura("vibracion", 11.2, 1)
    evento = detector.evaluar(lectura)
    assert evento is not None
    assert evento.tipo is TipoEvento.escalado
    assert detector.evaluar(lectura) is None


def test_muestra_sin_dato_no_dispara_ni_suma_racha() -> None:
    detector = DetectorSensores(_planta())
    assert detector.evaluar(_lectura("presion_descarga", 130, 1)) is None
    assert detector.evaluar(
        _lectura("presion_descarga", None, 2, calidad=Calidad.sin_dato)
    ) is None
    assert detector.evaluar(_lectura("presion_descarga", 130, 3)) is None
    assert detector.evaluar(_lectura("presion_descarga", 130, 4)) is None
    assert detector.evaluar(_lectura("presion_descarga", 130, 5)) is not None


def test_sensor_fuera_del_catalogo_se_ignora() -> None:
    detector = DetectorSensores(_planta())
    lectura = LecturaSensor(
        tag="B-999",
        variable="presion_descarga",
        valor=999,
        unidad="kg/cm2",
        ts=datetime(2026, 11, 4, tzinfo=UTC),
    )
    assert detector.evaluar(lectura) is None
