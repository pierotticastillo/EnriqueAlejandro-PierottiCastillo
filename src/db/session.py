"""Sesión y consultas de infraestructura a PostgreSQL."""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


def crear_engine(database_url: str) -> Engine:
    """Crea un engine con reconexión tolerante a caídas transitorias.

    El ``connect_timeout`` es corto a propósito: si la base no está (o el
    enlace se cae), la conexión falla en pocos segundos en vez de quedarse
    esperando. Un sistema de planta no puede quedar bloqueado por una base
    que no responde: lo que no puede esperar, sigue por el camino léxico.
    """
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=2,
        connect_args=_connect_args(database_url),
    )


def _connect_args(database_url: str) -> dict[str, object]:
    """Los argumentos de conexión, según el driver.

    El ``connect_timeout`` es corto a propósito: si la base no está (o el
    enlace se cae), la conexión falla en pocos segundos en vez de quedarse
    esperando. Un sistema de planta no puede quedar bloqueado por una base
    que no responde: lo que no puede esperar, sigue por el camino léxico.
    """
    if database_url.startswith("postgresql"):
        return {"connect_timeout": 5}
    return {}


def contar_pendientes(engine: Engine) -> int:
    """Cantidad de mensajes pendientes de sincronizar en ``outbox_cola``.

    Antes de aplicar las migraciones la tabla no existe: se devuelve 0 sin
    generar errores repetidos en el log de PostgreSQL.
    """
    with engine.connect() as conexion:
        existe = conexion.execute(text("SELECT to_regclass('public.outbox_cola')")).scalar_one()
        if existe is None:
            return 0

        resultado = conexion.execute(
            text("SELECT count(*) FROM outbox_cola WHERE estado = 'pendiente'")
        )
        return int(resultado.scalar_one())
