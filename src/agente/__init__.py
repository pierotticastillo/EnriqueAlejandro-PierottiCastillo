"""Núcleo de agentes de PIA-3: el agente de análisis, en LangChain.

El enunciado pide *"Python 3.11 o superior, con LangChain para el núcleo de
agentes"*. Este paquete es ese núcleo: un agente de LangChain que, ante un
escalado, **consulta** los manuales, el historial y el plan de mantenimiento con
herramientas y devuelve una propuesta **con citas**.

Lo que el agente hace y lo que no hace:

* Hace: consultar las herramientas, contrastar el catálogo de fallas con el
  manual y el historial, y redactar la justificación técnica de la orden.
* No hace: decidir la falla, elegir la tarea, aprobar la orden ni escribir el
  motivo. Eso está en código (``analisis/aprobacion.py``) y no se toca.

La salida del agente es una **propuesta**: se valida contra el catálogo antes
de usarla, y una cita que no existe en los manuales se descarta entera.
"""

from agente.modelos import PropuestaAgente, PropuestaValidada
from agente.nucleo import AgenteAnalisis, crear_agente
from agente.presupuesto import Presupuesto, PresupuestoAgotado
from agente.tools import construir_tools
from agente.trayectoria import RegistradorTrayectoria, TrazaAgente

__all__ = [
    "AgenteAnalisis",
    "Presupuesto",
    "PresupuestoAgotado",
    "PropuestaAgente",
    "PropuestaValidada",
    "RegistradorTrayectoria",
    "TrazaAgente",
    "construir_tools",
    "crear_agente",
]
