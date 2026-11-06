"""Tools de solo lectura sobre la planta: equipos, historial, stock y manuales.

Las tools no escriben nada: leen la API de la planta y el corpus de manuales
que ella sirve. Cada tool valida su entrada y su salida con Pydantic estricto
(``extra="forbid"``), y todas manejan timeout, reintentos y errores.
"""

from tools.registro import REGISTRO, Tool, registrar

__all__ = ["REGISTRO", "Tool", "registrar"]
