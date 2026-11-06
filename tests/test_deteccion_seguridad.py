
from datetime import datetime

from planta_contrato import Calidad, LecturaSensor, TipoEvento
from src.deteccion.sensores import DetectorSensores


# Definición de un escenario de prueba para S1-S4
def test_deteccion_alerta_seguridad_s1_fuga_gas():
    # Mock de planta con configuración de sensor
    planta_config = {
        "sensores": [
            {
                "tag": "SENS-001",
                "variable": "concentracion_gas",
                "unidad": "ppm",
                "alarma_alta": 100,
                "disparo": 200
            }
        ]
    }
    
    # Inicializamos el detector (usando la clase existente como base)
    # y planeamos extender o añadir el detector de seguridad
    detector = DetectorSensores(planta_config)
    
    # Lectura crítica simulando una fuga (S1 - Peligro alto)
    lectura = LecturaSensor(
        tag="SENS-001",
        variable="concentracion_gas",
        valor=250.0,
        unidad="ppm",
        ts=datetime.now(),
        calidad=Calidad.buena
    )
    
    evento = detector.evaluar(lectura)
    
    # En la fase actual, esperamos que detecte un disparo inmediato
    assert evento is not None
    assert evento.tipo == TipoEvento.escalado  # Por ahora escalado
    assert "disparo" in evento.resumen.lower()
