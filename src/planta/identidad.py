"""Identidad autenticada y roles de la Central de mantenimiento.

La Central no acepta un usuario desde el cuerpo HTTP. Cada persona tiene un
token opaco, configurado en el servidor, y ese token se resuelve a un actor y
un rol. La comparación es de tiempo constante y el token nunca se registra.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from config import CredencialCentral

Rol = Literal["planificador", "supervisor", "inspector", "lectura"]

ROLES: frozenset[str] = frozenset({"planificador", "supervisor", "inspector", "lectura"})

#: Un token más largo que esto no es una credencial válida de la Central.
MAX_TOKEN_CARACTERES = 512


@dataclass(frozen=True)
class Actor:
    """La persona autenticada y su rol."""

    usuario: str
    rol: Rol


class AutenticadorToken:
    """Resuelve un header Bearer a un actor con rol."""

    def __init__(self, credenciales: Iterable[CredencialCentral] = ()) -> None:
        self._credenciales: list[tuple[str, Actor]] = []
        tokens_vistos: set[str] = set()
        personas_vistas: set[str] = set()
        for credencial in credenciales:
            token = credencial.token.get_secret_value()
            if not token or len(token) > MAX_TOKEN_CARACTERES:
                raise ValueError("Token de la Central inválido")
            if credencial.rol not in ROLES:
                raise ValueError(f"Rol inválido para la Central: {credencial.rol!r}")
            if token in tokens_vistos:
                raise ValueError("Cada token de la Central pertenece a una sola persona")
            if credencial.usuario in personas_vistas:
                raise ValueError("Cada persona tiene un solo token en la Central")
            tokens_vistos.add(token)
            personas_vistas.add(credencial.usuario)
            actor = Actor(usuario=credencial.usuario, rol=credencial.rol)
            self._credenciales.append((token, actor))

    @property
    def habilitado(self) -> bool:
        """¿Hay alguna credencial configurada?"""
        return bool(self._credenciales)

    def autenticar(self, cabeceras: Sequence[str] | None) -> Actor | None:
        """Devuelve el actor del único header Bearer válido, o ``None``.

        Un header ausente, duplicado, mal formado o desconocido falla igual:
        la respuesta no revela qué parte de la credencial fue incorrecta.
        """
        valores = list(cabeceras or [])
        if len(valores) != 1:
            return None
        valor = valores[0].strip()
        esquema, separador, token = valor.partition(" ")
        if not separador or esquema.lower() != "bearer":
            return None
        token = token.strip()
        if not token or len(token) > MAX_TOKEN_CARACTERES:
            return None
        for esperado, actor in self._credenciales:
            if secrets.compare_digest(token, esperado):
                return actor
        return None
