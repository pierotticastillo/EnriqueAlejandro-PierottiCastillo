"""Control de compliance antes de enviar datos a proveedores de nube (Ítem 3.5 del Plan 3).

Antes de enviar cualquier dato personal (nombre, teléfono, etc.) a un proveedor
de nube (NVIDIA, o cualquier otro que se agregue a la lista cerrada), el sistema
debe responder y registrar cuatro preguntas:

1. ¿Qué datos viajan?
2. ¿Qué hace el proveedor? (¿los guarda? ¿cuánto tiempo? ¿entrena con ellos?)
3. ¿Dónde se procesan? (región y jurisdicción)
4. ¿Bajo qué términos?

Si alguna respuesta es "no sé", el dato no sale.
"""

from .control import ControlCompliance, DatoViaja, ResultadoCompliance, TerminosProveedor

__all__ = [
    "ControlCompliance",
    "ResultadoCompliance",
    "DatoViaja",
    "TerminosProveedor",
]
