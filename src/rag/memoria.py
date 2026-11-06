"""Política de memoria de conversación y limpieza de datos efímeros.

Qué se guarda y por cuánto tiempo:

* **Conversación de tablet** (``conversacion_nota``): mensaje mostrado,
  borrador, intento y estado. Se conserva mientras la nota está abierta y
  ``RAG_MEMORIA_DIAS`` días después de cerrarse. Sirve para reintentar una
  respuesta y para auditar qué se le mostró al operador.
* **Preguntas de tablet** (``pregunta_tablet``): campo, opciones, respuesta.
  Se conservan ``RAG_MEMORIA_DIAS`` días desde que se resolvieron.
* **Parte confirmado** (``parte`` y ``fila_parte``): no se purga por esta
  política; es el registro operativo y la evidencia de la detección.
* **Eventos y alertas**: no se purgan; son la trazabilidad de seguridad.
* **Índice RAG y evaluaciones**: se reconstruyen desde el corpus de la planta;
  no contienen datos personales.

La limpieza **no** toca la planta ni los mensajes ya publicados. Sólo borra
estado de conversación vencido. Se ejecuta con ``python -m rag.cli purgar`` y
por defecto es un ``--dry-run``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

DIAS_POR_DEFECTO = 90


def corte(dias: int = DIAS_POR_DEFECTO, ahora: datetime | None = None) -> datetime:
    ahora = ahora or datetime.now(UTC)
    return ahora - timedelta(days=dias)


def contar_purgables(engine: Engine, *, dias: int = DIAS_POR_DEFECTO) -> dict[str, int]:
    """Cuenta filas que la política borraría, sin borrar nada."""
    limite = corte(dias)
    with engine.connect() as conexion:
        conversaciones = conexion.execute(
            text(
                """
                SELECT count(*)
                FROM conversacion_nota
                WHERE estado IN ('confirmada', 'cerrada', 'error')
                  AND actualizada_en < :corte
                """
            ),
            {"corte": limite},
        ).scalar_one()
        preguntas = conexion.execute(
            text(
                """
                SELECT count(*)
                FROM pregunta_tablet
                WHERE resuelta AND creada_en < :corte
                """
            ),
            {"corte": limite},
        ).scalar_one()
    return {"conversaciones": int(conversaciones), "preguntas": int(preguntas)}


def purgar_memoria(engine: Engine, *, dias: int = DIAS_POR_DEFECTO) -> dict[str, int]:
    """Borra el estado de conversación vencido y devuelve cuántas filas borró."""
    limite = corte(dias)
    with engine.begin() as conexion:
        conversaciones = conexion.execute(
            text(
                """
                DELETE FROM conversacion_nota
                WHERE estado IN ('confirmada', 'cerrada', 'error')
                  AND actualizada_en < :corte
                """
            ),
            {"corte": limite},
        ).rowcount
        preguntas = conexion.execute(
            text(
                """
                DELETE FROM pregunta_tablet
                WHERE resuelta AND creada_en < :corte
                """
            ),
            {"corte": limite},
        ).rowcount
    return {
        "conversaciones": max(conversaciones or 0, 0),
        "preguntas": max(preguntas or 0, 0),
    }
