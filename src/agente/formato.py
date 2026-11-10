"""Cómo se escribe en la bitácora la propuesta del agente.

La justificación técnica de la orden tiene el mismo formato que la que
escribía el modelo sin herramientas: una línea de resumen, las causas, las
citas y la confianza. Así la bitácora de la orden se lee igual, venga de
donde venga.
"""

from __future__ import annotations

from agente.modelos import PropuestaValidada

#: Etiqueta que precede a la justificación en la bitácora de la orden.
ETIQUETA = "Justificación técnica (agente LangChain)"


def formatear(propuesta: PropuestaValidada, permitidas: tuple[str, ...]) -> str:
    """La propuesta, lista para guardarse en la bitácora de la orden."""
    partes: list[str] = [f"{ETIQUETA}: {propuesta.justificacion.strip()}"]
    if propuesta.causas:
        partes.append("Causas: " + "; ".join(causa.strip() for causa in propuesta.causas))
    if propuesta.falla:
        partes.append(f"Falla propuesta: {propuesta.falla}")
    citas = [cita for cita in propuesta.citas if cita.strip()] or list(permitidas)
    if citas:
        partes.append("Citas: " + "; ".join(citas))
    partes.append(f"Confianza del agente: {propuesta.confianza}")
    if propuesta.causas_descartadas:
        partes.append(
            "Causas del agente sin respaldo en el catálogo (se omitieron): "
            + "; ".join(propuesta.causas_descartadas)
        )
    return "\n".join(partes)
