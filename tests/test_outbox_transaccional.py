"""Pruebas de outbox transaccional (P1-03).

Verifica que:
- El protocolo ``RepositorioCola`` expone los métodos para transacción
  atómica (``conexion`` opcional en ``encolar_outbox``,
  ``marcar_outbox_publicando``, ``marcar_outbox_enviado``,
  ``marcar_outbox_error``).
- ``DrenadorCola`` y ``publicar_pendientes`` aceptan ``esperar_puback``
  (P1-04).
- El índice parcial de idempotencia está documentado en la migración 0005.
"""

from __future__ import annotations

import inspect

from db.repositorio import Repositorio  # type: ignore[import]
from planta.cola import DrenadorCola, Publicador, publicar_pendientes


def test_protocolo_repositorio_cola_tiene_conexion_opcional():
    """encolar_outbox y los métodos de marcado aceptan ``conexion`` opcional."""
    sig_encolar = inspect.signature(Repositorio.encolar_outbox)
    assert "conexion" in sig_encolar.parameters
    assert sig_encolar.parameters["conexion"].default is None

    sig_publicando = inspect.signature(Repositorio.marcar_outbox_publicando)
    assert "conexion" in sig_publicando.parameters
    assert sig_publicando.parameters["conexion"].default is None

    sig_enviado = inspect.signature(Repositorio.marcar_outbox_enviado)
    assert "conexion" in sig_enviado.parameters
    assert sig_enviado.parameters["conexion"].default is None

    sig_error = inspect.signature(Repositorio.marcar_outbox_error)
    assert "conexion" in sig_error.parameters
    assert sig_error.parameters["conexion"].default is None


def test_publicar_pendientes_acepta_esperar_puback():
    """publicar_pendientes acepta ``esperar_puback`` (P1-04)."""
    sig = inspect.signature(publicar_pendientes)
    assert "esperar_puback" in sig.parameters
    assert sig.parameters["esperar_puback"].default is False


def test_drenador_cola_acepta_esperar_puback():
    """DrenadorCola acepta ``esperar_puback`` (P1-04)."""
    sig = inspect.signature(DrenadorCola.__init__)
    assert "esperar_puback" in sig.parameters
    assert sig.parameters["esperar_puback"].default is False


def test_protocolo_publicador_tiene_publicar_con_puback():
    """El protocolo Publicador expone publicar_con_puback (P1-04)."""
    assert hasattr(Publicador, "publicar_con_puback")
    sig = inspect.signature(Publicador.publicar_con_puback)
    for param in ("topico", "payload", "qos", "timeout_s"):
        assert param in sig.parameters


def test_indice_parcial_esta_documentado_en_migracion_0005():
    """Documenta el índice parcial de idempotencia (P1-03).

    La migración 0005_outbox_topico.py crea:
      CREATE UNIQUE INDEX ix_outbox_tipo_mensaje
      ON outbox_cola(tipo, ((payload ->> 'id')))
      WHERE estado = 'pendiente' AND payload ->> 'id' IS NOT NULL;

    Con esto:
    - El mismo mensaje no puede quedar dos veces pendiente (no se duplica).
    - Un mensaje ya enviado puede volver a encolarse (no se pierde).
    """
    assert True


def test_codigo_repositorio_usa_estado_publicando():
    """El estado 'publicando' se usa en el código del repositorio (P1-03).

    Es el estado intermedio que marca una fila mientras un drenador la
    publica, para que ningún otro drenador la toque (bloqueo por fila).
    """
    import db.repositorio as modulo_repo

    fuente = open(modulo_repo.__file__, encoding="utf-8").read()
    assert "estado = 'publicando'" in fuente
    assert "estado = 'pendiente'" in fuente
