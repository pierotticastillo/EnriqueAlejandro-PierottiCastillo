"""Tests del control de compliance (Ítem 3.5 del Plan de implementación 3).

Verifica que las cuatro preguntas se respondan correctamente y que se bloquee
el envío cuando haya respuestas desconocidas o términos inseguros.
"""

from compliance.control import (
    ControlCompliance,
    DatoViaja,
    TerminosProveedor,
    crear_control_desde_config,
)


def test_datos_no_personales_siempre_se_envian():
    """Si los datos no son personales, se puede enviar sin restricciones."""
    datos = DatoViaja(
        tipo="métrica de temperatura del tanque",
        son_personales=False,
        descripcion="métrica de temperatura del tanque",
    )
    terminos = TerminosProveedor(
        retencion_dias=None,
        entrena_con_datos="no sé",
        region_procesamiento=None,
        jurisdiccion=None,
    )
    control = ControlCompliance(proveedor="NVIDIA", datos=datos, terminos=terminos)
    resultado = control.evaluar()
    assert resultado.puede_enviar is True


def test_datos_personales_con_todos_los_datos_conocidos_se_envian():
    """Datos personales con términos claros y región conocida se pueden enviar."""
    datos = DatoViaja(
        tipo="nombre y teléfono del cliente",
        son_personales=True,
        descripcion="nombre y teléfono del cliente",
    )
    terminos = TerminosProveedor(
        retencion_dias=30,
        entrena_con_datos="no",
        region_procesamiento="us-east-1",
        jurisdiccion="EE. UU.",
    )
    control = ControlCompliance(proveedor="NVIDIA", datos=datos, terminos=terminos)
    resultado = control.evaluar()
    assert resultado.puede_enviar is True


def test_datos_personales_con_entrenamiento_no_se_envian():
    """Si el proveedor entrena con los datos, no se envía."""
    datos = DatoViaja(
        tipo="teléfono del cliente",
        son_personales=True,
        descripcion="teléfono del cliente",
    )
    terminos = TerminosProveedor(
        retencion_dias=30,
        entrena_con_datos="sí",
        region_procesamiento="us-east-1",
        jurisdiccion="EE. UU.",
    )
    control = ControlCompliance(proveedor="NVIDIA", datos=datos, terminos=terminos)
    resultado = control.evaluar()
    assert resultado.puede_enviar is False
    assert "entrena con los datos" in resultado.motivo


def test_datos_personales_con_region_desconocida_no_se_envian():
    """Si no se sabe dónde se procesan, no se envía."""
    datos = DatoViaja(
        tipo="nombre del cliente",
        son_personales=True,
        descripcion="nombre del cliente",
    )
    terminos = TerminosProveedor(
        retencion_dias=30,
        entrena_con_datos="no",
        region_procesamiento=None,
        jurisdiccion=None,
    )
    control = ControlCompliance(proveedor="NVIDIA", datos=datos, terminos=terminos)
    resultado = control.evaluar()
    assert resultado.puede_enviar is False
    assert "región de procesamiento desconocida" in resultado.motivo


def test_datos_personales_con_retencion_desconocida_no_se_envian():
    """Si no se sabe cuánto se retienen los datos, no se envía."""
    datos = DatoViaja(
        tipo="teléfono del cliente",
        son_personales=True,
        descripcion="teléfono del cliente",
    )
    terminos = TerminosProveedor(
        retencion_dias=None,
        entrena_con_datos="no",
        region_procesamiento="us-east-1",
        jurisdiccion="EE. UU.",
    )
    control = ControlCompliance(proveedor="NVIDIA", datos=datos, terminos=terminos)
    resultado = control.evaluar()
    assert resultado.puede_enviar is False
    assert "retención desconocida" in resultado.motivo


def test_respuestas_desconocidas_se_registran():
    """Las preguntas respondidas con 'no sé' se listan en respuesta_unknown."""
    datos = DatoViaja(
        tipo="nombre del cliente",
        son_personales=True,
        descripcion="nombre del cliente",
    )
    terminos = TerminosProveedor(
        retencion_dias=None,
        entrena_con_datos="no sé",
        region_procesamiento=None,
        jurisdiccion=None,
    )
    control = ControlCompliance(proveedor="NVIDIA", datos=datos, terminos=terminos)
    resultado = control.evaluar()
    assert len(resultado.respuesta_unknown) > 0


def test_cuatro_preguntas_estan_presentes():
    """El resultado siempre contiene las cuatro preguntas de compliance."""
    datos = DatoViaja(
        tipo="dato de prueba",
        son_personales=False,
        descripcion="dato de prueba",
    )
    terminos = TerminosProveedor(
        retencion_dias=7,
        entrena_con_datos="no",
        region_procesamiento="sa-east-1",
        jurisdiccion="Argentina",
    )
    control = ControlCompliance(proveedor="NVIDIA", datos=datos, terminos=terminos)
    resultado = control.evaluar()
    assert "¿Qué datos viajan?" in resultado.preguntas
    assert "¿Qué hace el proveedor?" in resultado.preguntas
    assert "¿Dónde se procesan?" in resultado.preguntas
    assert "¿Bajo qué términos?" in resultado.preguntas


def test_crear_control_desde_config():
    """crear_control_desde_config construye un ControlCompliance válido."""
    control = crear_control_desde_config(
        proveedor="NVIDIA",
        datos_tipo="nombre y teléfono",
        datos_personales=True,
        retencion_dias=30,
        entrena_con_datos="no",
        region_procesamiento="sa-east-1",
        jurisdiccion="Argentina",
    )
    resultado = control.evaluar()
    assert resultado.puede_enviar is True
