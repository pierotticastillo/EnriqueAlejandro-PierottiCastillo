"""Persistencia de notas, partes, fotos, conversaciones y órdenes."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from planta_contrato import (
    EstadoOrden,
    EventoSistema,
    MensajeTablet,
    NotaRecorrida,
    OrdenSistema,
    Repuesto,
)
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from analisis.cierre import MontoContractualInvalido
from analisis.provenance import (
    manifiesto_de_cierre,
    manifiesto_de_decision_cierre,
    manifiesto_desde_reglas,
)
from planta.parte_extractor import Hallazgo, Pregunta

# El enum de la base y el del contrato no se escriben igual.
_ESTADO_ORDEN_DB = {
    "Borrador": "Borrador",
    "PendienteAprobacion": "Pendiente de aprobacion",
    "Aprobada": "Aprobada",
    "Rechazada": "Rechazada",
    "Ejecutada": "Ejecutada",
    "Observada": "Observada",
    "Cerrada": "Cerrada",
    "Certificada": "Certificada",
}

#: Estados que no se pueden degradar ni cambiar de contenido por un
#: reproceso técnico.  ``Ejecutada`` espera una decisión humana; ``Observada``
#: puede volver a ``Ejecutada`` únicamente mediante una nueva aclaración.
#: ``Cerrada``, ``Certificada`` y ``Rechazada`` son terminales.
_ESTADOS_ORDEN_PROTEGIDOS = frozenset(
    {"Rechazada", "Ejecutada", "Observada", "Cerrada", "Certificada"}
)

#: Estados desde los que puede recibirse un cierre técnico.  ``Observada``
#: corresponde al segundo cierre de una aclaración de la contratista.
_ESTADOS_ORDEN_CIERRE_ADMISIBLES = frozenset({"Aprobada", "Observada"})

_ESTADO_CONTRACTO = {
    estado_db: EstadoOrden(contrato) for contrato, estado_db in _ESTADO_ORDEN_DB.items()
}


class ConflictoEstado(ValueError):
    """El recurso existe, pero su estado no admite la operación solicitada."""


class AutorizacionInvalida(ValueError):
    """El rol autenticado no puede decidir sobre el ejecutor de la orden."""


def orden_desde_fila(orden: OrdenSistema, fila: dict[str, Any]) -> OrdenSistema:
    """Construye la publicación desde el snapshot ya comprometido en PostgreSQL.

    La orden local puede haber quedado obsoleta mientras se analizaba o se
    redactaba.  Estado, motivo, tareas y repuestos salen siempre de la base;
    el resto del contrato se conserva de la orden original.
    """
    estado_db = str(fila.get("estado") or "")
    if estado_db not in _ESTADO_CONTRACTO:
        raise ValueError(f"Estado de orden desconocido en PostgreSQL: {estado_db!r}")
    repuestos = [
        Repuesto(codigo=str(repuesto["codigo"]), cantidad=float(repuesto["cantidad"]))
        for repuesto in (fila.get("repuestos") or [])
    ]
    return orden.model_copy(
        update={
            "estado": _ESTADO_CONTRACTO[estado_db],
            "motivo": fila.get("motivo"),
            "detiene_equipo": bool(fila.get("detencion")),
            "reduce_inyeccion": bool(fila.get("reduce_inyeccion")),
            "ejecutor": str(fila.get("ejecutor") or "propio"),
            "tareas": [str(tarea) for tarea in (fila.get("tareas") or [])],
            "repuestos": repuestos,
        }
    )


class Repositorio:
    """Operaciones idempotentes sobre el esquema PIA-3."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ------------------------------------------------------------------ notas

    def guardar_nota(
        self,
        nota: NotaRecorrida,
        *,
        transcripcion: str,
        estado: str,
        seguridad: str | None = None,
    ) -> UUID:
        origen = "vivo" if nota.id.upper().startswith("VIVO-") else "normal"
        with self._engine.begin() as conexion:
            fila = conexion.execute(
                text(
                    """
                    INSERT INTO nota (
                        externa_id, ts, audio_url, transcripcion, origen, estado,
                        operador, recorrida_id, seguridad, actualizada_en
                    )
                    VALUES (
                        :externa_id, :ts, :audio_url, :transcripcion, :origen, :estado,
                        :operador, :recorrida_id, :seguridad, now()
                    )
                    ON CONFLICT (externa_id) DO UPDATE SET
                        ts = EXCLUDED.ts,
                        audio_url = EXCLUDED.audio_url,
                        transcripcion = EXCLUDED.transcripcion,
                        estado = EXCLUDED.estado,
                        operador = EXCLUDED.operador,
                        recorrida_id = EXCLUDED.recorrida_id,
                        seguridad = EXCLUDED.seguridad,
                        actualizada_en = now()
                    RETURNING id
                    """
                ),
                {
                    "externa_id": nota.id,
                    "ts": nota.ts,
                    "audio_url": nota.audio_url or "",
                    "transcripcion": transcripcion,
                    "origen": origen,
                    "estado": estado,
                    "operador": nota.operador,
                    "recorrida_id": nota.recorrida_id,
                    "seguridad": seguridad,
                },
            ).scalar_one()
        return fila

    def actualizar_estado_nota(self, nota_id: UUID, estado: str) -> None:
        with self._engine.begin() as conexion:
            conexion.execute(
                text("UPDATE nota SET estado = :estado, actualizada_en = now() WHERE id = :id"),
                {"estado": estado, "id": nota_id},
            )

    def registrar_alerta_seguridad(
        self,
        nota_id: UUID,
        externa_id: str,
        tipo: str,
        evidencia: dict[str, Any],
    ) -> str:
        """Registra un evento de seguridad idempotente y su evidencia."""
        if tipo not in {"S1", "S2", "S3", "S4"}:
            raise ValueError(f"Tipo de seguridad inválido: {tipo}")
        evento_id = f"seguridad:{externa_id}"
        with self._engine.begin() as conexion:
            conexion.execute(
                text(
                    """
                    INSERT INTO evento_sistema (id, origen, estado, payload)
                    VALUES (:id, 'parte', 'AlertaSeguridad', CAST(:payload AS jsonb))
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {"id": evento_id, "payload": json.dumps(evidencia, ensure_ascii=False)},
            )
            conexion.execute(
                text(
                    "INSERT INTO evento_nota (evento_id, nota_id) "
                    "VALUES (:evento, :nota) ON CONFLICT DO NOTHING"
                ),
                {"evento": evento_id, "nota": nota_id},
            )
            conexion.execute(
                text(
                    """
                    INSERT INTO alerta_seguridad (
                        evento_id, tipo, publicada_en, espera_respuesta, evidencia
                    )
                    SELECT :evento, :tipo, now(), false, CAST(:evidencia AS jsonb)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM alerta_seguridad WHERE evento_id = :evento
                    )
                    """
                ),
                {
                    "evento": evento_id,
                    "tipo": tipo,
                    "evidencia": json.dumps(evidencia, ensure_ascii=False),
                },
            )
            conexion.execute(
                text(
                    "UPDATE nota SET estado = 'alerta_seguridad', "
                    "actualizada_en = now() WHERE id = :id"
                ),
                {"id": nota_id},
            )
        return evento_id

    def registrar_evento_sensor(self, evento: EventoSistema) -> bool:
        """Persiste un evento MQTT de sensor una sola vez."""
        if evento.origen != "sensor":
            raise ValueError("El evento debe tener origen sensor")
        return self.registrar_evento(evento)

    def registrar_evento(
        self,
        evento: EventoSistema,
        *,
        nota_id: UUID | None = None,
        correlation_id: str | None = None,
    ) -> bool:
        """Persiste cualquier evento de forma idempotente y lo vincula a una nota.

        Si se entrega un ``correlation_id``, se incluye en el payload del
        evento para que cualquier consulta pueda localizar la solicitud de
        origen sin descargar el JSON completo de otras tablas.
        """
        estados_db = {
            "SinNovedad": "Sin novedad",
            "Escalado": "Escalado",
            "AlertaSeguridad": "AlertaSeguridad",
        }
        payload = evento.model_dump(mode="json")
        if correlation_id is not None:
            payload["correlation_id"] = correlation_id
        with self._engine.begin() as conexion:
            id_insertado = conexion.execute(
                text(
                    """
                    INSERT INTO evento_sistema (id, origen, estado, payload, creado_en)
                    VALUES (
                        :id, CAST(:origen AS origen_evento),
                        CAST(:estado AS estado_evento), CAST(:payload AS jsonb), :ts
                    )
                    ON CONFLICT (id) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "id": evento.id,
                    "origen": evento.origen,
                    "estado": estados_db[evento.tipo.value],
                    "payload": json.dumps(payload, ensure_ascii=False),
                    "ts": evento.ts,
                },
            ).scalar_one_or_none()
            if nota_id is not None:
                conexion.execute(
                    text(
                        "INSERT INTO evento_nota (evento_id, nota_id) "
                        "VALUES (:evento, :nota) ON CONFLICT DO NOTHING"
                    ),
                    {"evento": evento.id, "nota": nota_id},
                )
        return id_insertado is not None

    # ------------------------------------------------------------------ parte

    def guardar_parte(
        self,
        nota_id: UUID,
        ts: datetime,
        filas: list[Hallazgo],
        *,
        fotos_url: list[str],
        fotos_locales: list[Path | None],
        mensaje_id: str,
        intento: int,
        estado: str = "Borrador",
    ) -> UUID:
        with self._engine.begin() as conexion:
            parte_id = conexion.execute(
                text(
                    """
                    INSERT INTO parte (nota_id, estado, mensaje_id, intento)
                    VALUES (:nota_id, CAST(:estado AS estado_parte), :mensaje_id, :intento)
                    ON CONFLICT (nota_id) DO UPDATE SET
                        estado = EXCLUDED.estado,
                        mensaje_id = EXCLUDED.mensaje_id,
                        intento = EXCLUDED.intento
                    RETURNING id
                    """
                ),
                {
                    "nota_id": nota_id,
                    "estado": estado,
                    "mensaje_id": mensaje_id,
                    "intento": intento,
                },
            ).scalar_one()

            conexion.execute(
                text("DELETE FROM foto WHERE nota_id = :nota_id"),
                {"nota_id": nota_id},
            )
            conexion.execute(
                text("DELETE FROM fila_parte WHERE parte_id = :parte_id"),
                {"parte_id": parte_id},
            )

            ids_por_indice: dict[int, UUID] = {}
            for fila in filas:
                if not fila.tag:
                    continue
                fila_id = conexion.execute(
                    text(
                        """
                        INSERT INTO fila_parte (
                            parte_id, tag, variable, valor, unidad, hora_iso,
                            observacion, confianza
                        )
                        VALUES (
                            :parte_id, :tag, :variable, :valor, :unidad, :hora_iso,
                            :observacion, :confianza
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "parte_id": parte_id,
                        "tag": fila.tag,
                        "variable": fila.variable,
                        "valor": fila.valor,
                        "unidad": fila.unidad,
                        "hora_iso": ts,
                        "observacion": fila.observacion,
                        "confianza": 1.0,
                    },
                ).scalar_one()
                if fila.foto_indice is not None:
                    ids_por_indice[fila.foto_indice] = fila_id

            for indice, url in enumerate(fotos_url):
                local = fotos_locales[indice] if indice < len(fotos_locales) else None
                conexion.execute(
                    text(
                        """
                        INSERT INTO foto (nota_id, url, ruta_local, fila_parte_id, indice)
                        VALUES (:nota_id, :url, :ruta_local, :fila_parte_id, :indice)
                        """
                    ),
                    {
                        "nota_id": nota_id,
                        "url": url,
                        "ruta_local": str(local) if local else None,
                        "fila_parte_id": ids_por_indice.get(indice),
                        "indice": indice,
                    },
                )
        return parte_id

    def confirmar_parte(self, nota_id: UUID, ts: datetime) -> UUID:
        with self._engine.begin() as conexion:
            parte_id = conexion.execute(
                text(
                    """
                    UPDATE parte
                    SET estado = 'Confirmado', confirmado_en = :ts
                    WHERE nota_id = :nota_id
                    RETURNING id
                    """
                ),
                {"ts": ts, "nota_id": nota_id},
            ).scalar_one()
            conexion.execute(
                text(
                    """
                    UPDATE nota
                    SET estado = 'confirmada', actualizada_en = now()
                    WHERE id = :id
                    """
                ),
                {"id": nota_id},
            )
        return parte_id

    # ------------------------------------------------------------------ conversación

    def guardar_conversacion(
        self,
        nota_id: UUID,
        mensaje: MensajeTablet,
        filas: list[Hallazgo],
        *,
        intento: int,
        estado: str,
        datos: dict[str, Any] | None = None,
    ) -> None:
        mensaje_json = json.dumps(mensaje.model_dump(mode="json"), ensure_ascii=False)
        borrador_json = json.dumps(_filas_json(filas), ensure_ascii=False)
        datos_json = json.dumps(datos or {}, ensure_ascii=False)
        with self._engine.begin() as conexion:
            conexion.execute(
                text(
                    """
                    INSERT INTO conversacion_nota (
                        nota_id, mensaje_tablet, borrador, intento, estado,
                        actualizada_en, mensaje_id, datos
                    )
                    VALUES (
                        :nota_id, CAST(:mensaje AS jsonb), CAST(:borrador AS jsonb),
                        :intento, :estado, now(), :mensaje_id, CAST(:datos AS jsonb)
                    )
                    ON CONFLICT (nota_id) DO UPDATE SET
                        mensaje_tablet = EXCLUDED.mensaje_tablet,
                        borrador = EXCLUDED.borrador,
                        intento = EXCLUDED.intento,
                        estado = EXCLUDED.estado,
                        actualizada_en = now(),
                        mensaje_id = EXCLUDED.mensaje_id,
                        datos = EXCLUDED.datos
                    """
                ),
                {
                    "nota_id": nota_id,
                    "mensaje": mensaje_json,
                    "borrador": borrador_json,
                    "intento": intento,
                    "estado": estado,
                    "mensaje_id": mensaje.id,
                    "datos": datos_json,
                },
            )

    def obtener_conversacion(self, mensaje_id: str) -> dict[str, Any] | None:
        with self._engine.connect() as conexion:
            fila = (
                conexion.execute(
                    text(
                        """
                    SELECT c.nota_id, n.externa_id, c.mensaje_tablet, c.borrador,
                           c.intento, c.estado, c.datos
                    FROM conversacion_nota c
                    JOIN nota n ON n.id = c.nota_id
                    WHERE c.mensaje_id = :mensaje_id
                    """
                    ),
                    {"mensaje_id": mensaje_id},
                )
                .mappings()
                .first()
            )
        return dict(fila) if fila else None

    def obtener_conversacion_por_nota(self, externa_id: str) -> dict[str, Any] | None:
        with self._engine.connect() as conexion:
            fila = (
                conexion.execute(
                    text(
                        """
                    SELECT c.nota_id, n.externa_id, c.mensaje_tablet, c.borrador,
                           c.intento, c.estado, c.datos
                    FROM conversacion_nota c
                    JOIN nota n ON n.id = c.nota_id
                    WHERE n.externa_id = :externa_id
                    """
                    ),
                    {"externa_id": externa_id},
                )
                .mappings()
                .first()
            )
        return dict(fila) if fila else None

    def marcar_conversacion(self, nota_id: UUID, estado: str) -> None:
        with self._engine.begin() as conexion:
            conexion.execute(
                text(
                    """
                    UPDATE conversacion_nota
                    SET estado = :estado, actualizada_en = now()
                    WHERE nota_id = :nota_id
                    """
                ),
                {"estado": estado, "nota_id": nota_id},
            )

    def guardar_pregunta(
        self,
        nota_id: UUID,
        mensaje: MensajeTablet,
        pregunta: Pregunta,
    ) -> UUID:
        with self._engine.begin() as conexion:
            fila = conexion.execute(
                text(
                    """
                    INSERT INTO pregunta_tablet (
                        nota_id, campo, opciones, respuesta, resuelta,
                        mensaje_id, tags, variable, valor_dicho
                    )
                    VALUES (
                        :nota_id, :campo, CAST(:opciones AS jsonb), NULL, false,
                        :mensaje_id, CAST(:tags AS jsonb), :variable, :valor_dicho
                    )
                    RETURNING id
                    """
                ),
                {
                    "nota_id": nota_id,
                    "campo": pregunta.campo,
                    "opciones": json.dumps(pregunta.opciones, ensure_ascii=False),
                    "mensaje_id": mensaje.id,
                    "tags": json.dumps(pregunta.tags, ensure_ascii=False),
                    "variable": pregunta.variable,
                    "valor_dicho": pregunta.valor_dicho,
                },
            ).scalar_one()
        return fila

    def responder_pregunta(self, mensaje_id: str, respuesta: str) -> None:
        with self._engine.begin() as conexion:
            conexion.execute(
                text(
                    """
                    UPDATE pregunta_tablet
                    SET respuesta = :respuesta, resuelta = true
                    WHERE mensaje_id = :mensaje_id
                    """
                ),
                {"respuesta": respuesta, "mensaje_id": mensaje_id},
            )

    # ------------------------------------------------------------------ órdenes

    def guardar_orden(
        self,
        orden: OrdenSistema,
        *,
        reglas: dict[str, Any] | None = None,
        descripcion: str | None = None,
        costo_usd: float | None = None,
        repuestos: list[dict[str, Any]] | None = None,
        manifiesto: dict[str, Any] | None = None,
        analisis: dict[str, Any] | None = None,
        propuesta: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Persiste la orden y devuelve el snapshot realmente comprometido.

        El id de la orden es determinista (equipo + falla): si el mismo equipo
        vuelve a fallar, la orden se actualiza en vez de abrir una segunda.
        Cuando el estado ya estaba protegido, el snapshot devuelto conserva el
        contenido y el estado de la base, no los del objeto reprocesado.
        """
        estado_db = _ESTADO_ORDEN_DB.get(orden.estado.value)
        if estado_db is None:
            raise ValueError(f"Estado de orden desconocido: {orden.estado.value}")
        evento_id = orden.evento_ids[0] if orden.evento_ids else None
        if evento_id is None:
            raise ValueError("La orden tiene que referenciar el evento que la originó")
        reglas_json = json.dumps(reglas or {}, ensure_ascii=False)
        detalles = {str(detalle.get("codigo")): detalle for detalle in (repuestos or [])}
        with self._engine.begin() as conexion:
            # El bloqueo serializa dos reprocesos de la misma orden.  Si la
            # orden ya está en un estado del flujo humano, el reproceso sólo
            # puede sumar evidencia al ledger: no cambia estado, motivo,
            # costo, tareas ni repuestos.
            estado_previo = conexion.execute(
                text("SELECT estado FROM orden WHERE id = :id FOR UPDATE"),
                {"id": orden.id},
            ).scalar_one_or_none()
            decision_humana = conexion.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM aprobacion
                        WHERE orden_id = :id
                          AND usuario IS NOT NULL
                          AND btrim(usuario) <> ''
                    )
                    """
                ),
                {"id": orden.id},
            ).scalar_one()
            preservar = estado_previo in _ESTADOS_ORDEN_PROTEGIDOS or bool(decision_humana)
            estado_efectivo = estado_previo if preservar else estado_db

            conexion.execute(
                text(
                    """
                    INSERT INTO orden (
                        id, evento_id, equipo, estado, motivo, detencion,
                        costo_estimado, ejecutor, reduce_inyeccion,
                        creada_en, actualizada_en
                    )
                    VALUES (
                        :id, :evento_id, :equipo, CAST(:estado AS estado_orden), :motivo,
                        :detencion, :costo, :ejecutor, :reduce,
                        now(), now()
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        estado = CASE
                            WHEN :preservar THEN orden.estado ELSE EXCLUDED.estado END,
                        motivo = CASE
                            WHEN :preservar THEN orden.motivo ELSE EXCLUDED.motivo END,
                        detencion = CASE
                            WHEN :preservar THEN orden.detencion ELSE EXCLUDED.detencion END,
                        costo_estimado = CASE
                            WHEN :preservar THEN orden.costo_estimado
                            ELSE EXCLUDED.costo_estimado END,
                        ejecutor = CASE
                            WHEN :preservar THEN orden.ejecutor ELSE EXCLUDED.ejecutor END,
                        reduce_inyeccion = CASE
                            WHEN :preservar THEN orden.reduce_inyeccion
                            ELSE EXCLUDED.reduce_inyeccion END,
                        actualizada_en = CASE
                            WHEN :preservar THEN orden.actualizada_en ELSE now() END
                    """
                ),
                {
                    "id": orden.id,
                    "evento_id": evento_id,
                    "equipo": orden.equipo,
                    "estado": estado_db,
                    "motivo": orden.motivo,
                    "detencion": orden.detiene_equipo,
                    "costo": costo_usd,
                    "ejecutor": orden.ejecutor or "propio",
                    "reduce": orden.reduce_inyeccion,
                    "preservar": preservar,
                },
            )
            if not preservar:
                conexion.execute(
                    text("DELETE FROM orden_tarea WHERE orden_id = :id"),
                    {"id": orden.id},
                )
                conexion.execute(
                    text("DELETE FROM orden_repuesto WHERE orden_id = :id"),
                    {"id": orden.id},
                )
                for tarea in orden.tareas:
                    conexion.execute(
                        text(
                            """
                            INSERT INTO orden_tarea (orden_id, tarea_id, descripcion, origen)
                            VALUES (:orden, :tarea, :descripcion, 'catalogo')
                            """
                        ),
                        {
                            "orden": orden.id,
                            "tarea": tarea,
                            "descripcion": descripcion if tarea == "NO-ESTANDAR" else None,
                        },
                    )
                for repuesto in orden.repuestos:
                    detalle = detalles.get(repuesto.codigo, {})
                    conexion.execute(
                        text(
                            """
                            INSERT INTO orden_repuesto (
                                orden_id, repuesto_id, cantidad, costo_unitario, disponible
                            )
                            VALUES (
                                :orden, :repuesto, :cantidad, :costo, :disponible
                            )
                            """
                        ),
                        {
                            "orden": orden.id,
                            "repuesto": repuesto.codigo,
                            "cantidad": repuesto.cantidad,
                            "costo": float(detalle.get("costo_unitario") or 0.0),
                            "disponible": bool(detalle.get("disponible", False)),
                        },
                    )
                conexion.execute(
                    text(
                        """
                        INSERT INTO aprobacion (orden_id, decision, motivo, reglas_evaluadas)
                        SELECT :orden, :decision, :motivo, CAST(:reglas AS jsonb)
                        WHERE NOT EXISTS (
                            SELECT 1 FROM aprobacion
                            WHERE orden_id = :orden AND decision = :decision
                              AND COALESCE(motivo, '') = COALESCE(:motivo, '')
                        )
                        """
                    ),
                    {
                        "orden": orden.id,
                        "decision": orden.estado.value,
                        "motivo": orden.motivo,
                        "reglas": reglas_json,
                    },
                )

            # El manifiesto y el ledger se escriben en la misma transacción
            # que la orden.  Si la evidencia no se puede dejar escrita, la
            # orden tampoco queda como si estuviera auditada.
            payload_evento = conexion.execute(
                text("SELECT payload FROM evento_sistema WHERE id = :id"),
                {"id": evento_id},
            ).scalar_one_or_none()
            contenido_manifiesto = manifiesto or manifiesto_desde_reglas(
                evento_id=evento_id,
                orden_id=orden.id,
                reglas=reglas,
            )
            manifiesto_id, hash_manifiesto = self._insertar_manifiesto(
                conexion,
                tipo="orden",
                referencia_id=orden.id,
                evento_id=evento_id,
                orden_id=orden.id,
                parte_id=_parte_id_manifiesto(contenido_manifiesto),
                contenido=contenido_manifiesto,
                correlation_id=correlation_id,
            )
            metricas = _metricas_manifiesto(contenido_manifiesto)
            herramientas_contexto = (
                ["rag.contexto.construir_contexto"]
                if contenido_manifiesto.get("fragmentos", {}).get("estado") == "disponible"
                else []
            )
            self._insertar_ledger(
                conexion,
                tipo="evento",
                referencia_id=evento_id,
                evento_id=evento_id,
                orden_id=orden.id,
                datos=payload_evento or {},
                idempotencia=f"evento:{evento_id}:{orden.id}",
                correlation_id=correlation_id,
            )
            self._insertar_ledger(
                conexion,
                tipo="evidencia",
                referencia_id=evento_id,
                evento_id=evento_id,
                orden_id=orden.id,
                evidencia={
                    "manifiesto_id": manifiesto_id,
                    "hash_sha256": hash_manifiesto,
                },
                modelo=metricas["modelo"],
                herramientas=metricas["herramientas"],
                tokens_prompt=metricas["tokens_prompt"],
                tokens_completion=metricas["tokens_completion"],
                costo_usd=metricas["costo_usd"],
                segundos=metricas["segundos"],
                idempotencia=f"evidencia:{evento_id}:{orden.id}",
                correlation_id=correlation_id,
            )
            self._insertar_ledger(
                conexion,
                tipo="analisis",
                referencia_id=evento_id,
                evento_id=evento_id,
                orden_id=orden.id,
                analisis=analisis or _analisis_desde_reglas(reglas),
                herramientas=herramientas_contexto,
                idempotencia=f"analisis:{evento_id}:{orden.id}",
                correlation_id=correlation_id,
            )
            self._insertar_ledger(
                conexion,
                tipo="propuesta",
                referencia_id=evento_id,
                evento_id=evento_id,
                orden_id=orden.id,
                propuesta=propuesta or _propuesta_desde_orden(orden, repuestos, costo_usd),
                idempotencia=f"propuesta:{evento_id}:{orden.id}",
                correlation_id=correlation_id,
            )
            self._insertar_ledger(
                conexion,
                tipo="reglas",
                referencia_id=evento_id,
                evento_id=evento_id,
                orden_id=orden.id,
                reglas=reglas or {},
                actor="codigo",
                rol="sistema",
                estado=estado_efectivo,
                herramientas=herramientas_contexto,
                idempotencia=f"reglas:{evento_id}:{orden.id}",
                correlation_id=correlation_id,
            )
        snapshot = self.obtener_orden_completa(orden.id)
        if snapshot is None:
            raise RuntimeError(f"La orden {orden.id} no quedó persistida")
        return snapshot

    def _insertar_manifiesto(
        self,
        conexion: Connection,
        *,
        tipo: str,
        referencia_id: str,
        evento_id: str,
        orden_id: str,
        parte_id: str | None,
        contenido: dict[str, Any],
        correlation_id: str | None = None,
    ) -> tuple[str, str]:
        """Inserta un manifiesto sin permitir que una repeticion lo cambie."""
        serializado = json.dumps(contenido, ensure_ascii=False, default=str)
        fila = (
            conexion.execute(
                text(
                    """
                INSERT INTO manifiesto_evidencia (
                    tipo, referencia_id, evento_id, orden_id, parte_id,
                    contenido, hash_sha256, correlation_id
                )
                SELECT
                    :tipo, :referencia, :evento, :orden, :parte,
                    CAST(:contenido AS jsonb),
                    encode(
                        sha256(convert_to(CAST(:contenido AS jsonb)::text, 'UTF8')),
                        'hex'
                    ),
                    :correlation_id
                ON CONFLICT (tipo, referencia_id) DO NOTHING
                RETURNING id, hash_sha256
                """
                ),
                {
                    "tipo": tipo,
                    "referencia": referencia_id,
                    "evento": evento_id,
                    "orden": orden_id,
                    "parte": parte_id,
                    "contenido": serializado,
                    "correlation_id": correlation_id,
                },
            )
            .mappings()
            .first()
        )
        if fila is None:
            fila = (
                conexion.execute(
                    text(
                        """
                    SELECT id, hash_sha256 FROM manifiesto_evidencia
                    WHERE tipo = :tipo AND referencia_id = :referencia
                    """
                    ),
                    {"tipo": tipo, "referencia": referencia_id},
                )
                .mappings()
                .one()
            )
        return str(fila["id"]), str(fila["hash_sha256"])

    def _insertar_ledger(
        self,
        conexion: Connection,
        *,
        tipo: str,
        referencia_id: str | None,
        evento_id: str,
        orden_id: str,
        idempotencia: str,
        evidencia: dict[str, Any] | None = None,
        analisis: dict[str, Any] | None = None,
        propuesta: dict[str, Any] | None = None,
        reglas: dict[str, Any] | None = None,
        actor: str | None = None,
        rol: str | None = None,
        estado: str | None = None,
        modelo: str | None = None,
        herramientas: list[str] | None = None,
        tokens_prompt: int = 0,
        tokens_completion: int = 0,
        costo_usd: float = 0.0,
        segundos: float = 0.0,
        datos: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Agrega una entrada al ledger; la idempotencia evita duplicados."""
        conexion.execute(
            text(
                """
                INSERT INTO ledger_decisiones (
                    tipo, referencia_id, evento_id, orden_id,
                    evidencia, analisis, propuesta, reglas,
                    actor, rol, estado, modelo, herramientas,
                    tokens_prompt, tokens_completion, costo_usd, segundos,
                    datos, idempotencia, correlation_id
                )
                VALUES (
                    :tipo, :referencia, :evento, :orden,
                    CAST(:evidencia AS jsonb), CAST(:analisis AS jsonb),
                    CAST(:propuesta AS jsonb), CAST(:reglas AS jsonb),
                    :actor, :rol, :estado, :modelo, CAST(:herramientas AS jsonb),
                    :tokens_prompt, :tokens_completion, :costo, :segundos,
                    CAST(:datos AS jsonb), :idempotencia, :correlation_id
                )
                ON CONFLICT (idempotencia) DO NOTHING
                """
            ),
            {
                "tipo": tipo,
                "referencia": referencia_id,
                "evento": evento_id,
                "orden": orden_id,
                "evidencia": _json(contenido=evidencia or {}),
                "analisis": _json(contenido=analisis or {}),
                "propuesta": _json(contenido=propuesta or {}),
                "reglas": _json(contenido=reglas or {}),
                "actor": actor,
                "rol": rol,
                "estado": estado,
                "modelo": modelo,
                "herramientas": _json(contenido=herramientas or []),
                "tokens_prompt": int(tokens_prompt or 0),
                "tokens_completion": int(tokens_completion or 0),
                "costo": float(costo_usd or 0.0),
                "segundos": float(segundos or 0.0),
                "datos": _json(contenido=datos or {}),
                "idempotencia": idempotencia,
                "correlation_id": correlation_id,
            },
        )

    def guardar_redaccion_orden(
        self,
        orden_id: str,
        redaccion: str,
        *,
        modelo: str | None = None,
    ) -> bool:
        """Guarda la justificación técnica escrita por el modelo local.

        Va en la bitácora de la aprobación (``aprobacion.reglas_evaluadas``),
        junto con las reglas que se evaluaron: es el lugar donde ya se guarda
        por qué se decidió así. **No** cambia el estado de la orden ni el
        motivo: la redacción es un agregado, la decisión ya estaba tomada.
        """
        if not redaccion.strip():
            return False
        with self._engine.begin() as conexion:
            resultado = conexion.execute(
                text(
                    """
                    UPDATE aprobacion
                    SET reglas_evaluadas = jsonb_set(
                        reglas_evaluadas,
                        '{redaccion}',
                        jsonb_build_object(
                            'texto', CAST(:texto AS text),
                            'modelo', CAST(:modelo AS text),
                            'redactado_en', now()
                        ),
                        true
                    )
                    WHERE id = (
                        SELECT id FROM aprobacion
                        WHERE orden_id = :orden
                        ORDER BY creada_en DESC, id DESC
                        LIMIT 1
                    )
                    """
                ),
                {
                    "orden": orden_id,
                    "texto": redaccion,
                    "modelo": modelo or "",
                },
            )
            if resultado.rowcount:
                orden = (
                    conexion.execute(
                        text(
                            """
                        SELECT evento_id, equipo, estado::text AS estado
                        FROM orden WHERE id = :id
                        """
                        ),
                        {"id": orden_id},
                    )
                    .mappings()
                    .one()
                )
                pasos = (
                    conexion.execute(
                        text(
                            """
                        SELECT modelo, herramienta, tokens_prompt,
                               tokens_completion, costo_usd, segundos
                        FROM registro_trayectoria
                        WHERE orden_id = :orden
                        ORDER BY ts, paso
                        """
                        ),
                        {"orden": orden_id},
                    )
                    .mappings()
                    .all()
                )
                modelos = {
                    str(paso["modelo"])
                    for paso in pasos
                    if paso["modelo"] and str(paso["modelo"]) != "no_disponible"
                }
                if modelo:
                    modelos.add(modelo)
                herramientas = sorted(
                    {str(paso["herramienta"]) for paso in pasos if paso["herramienta"]}
                )
                tokens_prompt = sum(int(paso["tokens_prompt"] or 0) for paso in pasos)
                tokens_completion = sum(int(paso["tokens_completion"] or 0) for paso in pasos)
                costo_usd = sum(float(paso["costo_usd"] or 0.0) for paso in pasos)
                segundos = sum(float(paso["segundos"] or 0.0) for paso in pasos)
                digest = hashlib.sha256(redaccion.encode("utf-8")).hexdigest()[:16]
                self._insertar_ledger(
                    conexion,
                    tipo="trayectoria",
                    referencia_id=orden_id,
                    evento_id=str(orden["evento_id"]),
                    orden_id=orden_id,
                    propuesta={"redaccion": redaccion},
                    estado=str(orden["estado"]),
                    modelo=", ".join(sorted(modelos)) or None,
                    herramientas=herramientas,
                    tokens_prompt=tokens_prompt,
                    tokens_completion=tokens_completion,
                    costo_usd=costo_usd,
                    segundos=segundos,
                    datos={"tipo": "redaccion_tecnica"},
                    idempotencia=f"trayectoria:redaccion:{orden_id}:{digest}",
                )
        return bool(resultado.rowcount)

    def obtener_redaccion_orden(self, orden_id: str) -> dict[str, Any] | None:
        """La justificación técnica de la orden, si se redactó."""
        with self._engine.connect() as conexion:
            fila = (
                conexion.execute(
                    text(
                        """
                        SELECT reglas_evaluadas -> 'redaccion' AS redaccion
                        FROM aprobacion
                        WHERE orden_id = :orden
                        ORDER BY creada_en DESC, id DESC
                        LIMIT 1
                        """
                    ),
                    {"orden": orden_id},
                )
                .mappings()
                .first()
            )
        if not fila:
            return None
        redaccion = fila["redaccion"]
        return dict(redaccion) if isinstance(redaccion, dict) else None

    def obtener_orden(self, orden_id: str) -> dict[str, Any] | None:
        with self._engine.connect() as conexion:
            fila = (
                conexion.execute(
                    text(
                        """
                        SELECT id, evento_id, equipo, estado, motivo, detencion,
                               costo_estimado, creada_en, actualizada_en
                        FROM orden WHERE id = :id
                        """
                    ),
                    {"id": orden_id},
                )
                .mappings()
                .first()
            )
        return dict(fila) if fila else None

    def obtener_orden_completa(self, orden_id: str) -> dict[str, Any] | None:
        """La orden con sus tareas y repuestos, tal como se publicó.

        El cierre necesita la orden completa para compararse contra ella y
        republicarla con el estado nuevo: con el mismo id, las mismas tareas y
        los mismos repuestos.
        """
        with self._engine.connect() as conexion:
            fila = (
                conexion.execute(
                    text(
                        """
                        SELECT o.id, o.evento_id, o.equipo, o.estado, o.motivo,
                               o.detencion, o.costo_estimado, o.ejecutor,
                               o.reduce_inyeccion, o.creada_en, o.actualizada_en,
                               COALESCE(
                                   (SELECT json_agg(t.tarea_id ORDER BY t.tarea_id)
                                    FROM orden_tarea t WHERE t.orden_id = o.id),
                                   '[]'::json
                               ) AS tareas,
                               COALESCE(
                                   (SELECT json_agg(
                                       json_build_object(
                                           'codigo', r.repuesto_id,
                                           'cantidad', r.cantidad
                                       ) ORDER BY r.repuesto_id
                                   )
                                    FROM orden_repuesto r WHERE r.orden_id = o.id),
                                   '[]'::json
                               ) AS repuestos
                        FROM orden o
                        WHERE o.id = :id
                        """
                    ),
                    {"id": orden_id},
                )
                .mappings()
                .first()
            )
        return dict(fila) if fila else None

    # ------------------------------------------------------------------ cierre

    def _habilitacion_de_orden(
        self,
        conexion: Connection,
        orden_id: str,
    ) -> dict[str, Any] | None:
        """Habilita vigente de una orden, si ya fue certificado."""
        fila = (
            conexion.execute(
                text(
                    """
                SELECT numero, monto
                FROM habilitacion
                WHERE orden_id = :orden_id
                ORDER BY emitida_en, id
                LIMIT 1
                """
                ),
                {"orden_id": orden_id},
            )
            .mappings()
            .first()
        )
        if fila is None:
            return None
        return {"numero": str(fila["numero"]), "monto": float(fila["monto"])}

    def registrar_cierre(
        self,
        *,
        cierre_id: str,
        orden_id: str,
        anio: int,
        tecnico: str,
        empresa: str,
        transcripcion: str,
        tareas_hechas: list[str],
        repuestos_usados: list[dict[str, Any]],
        horas: float | None,
        prueba_final: str | None,
        diferencias: list[dict[str, Any]],
        fotos: list[dict[str, Any]] | None = None,
        motivo: str | None = None,
    ) -> dict[str, Any]:
        """Guarda el cierre técnico y deja la orden esperando decisión humana.

        El procesamiento automático **no** decide el estado final. La orden pasa
        a ``Ejecutada`` y queda en la bandeja de la Central hasta que un
        ``supervisor`` cierre el trabajo propio o un ``inspector`` certifique u
        observe el trabajo de la contratista.

        Es idempotente por ``cierre.externa_id``: si la misma nota vuelve a
        llegar, no se duplica el cierre ni se vuelve a mover una orden que ya
        tenga una decisión humana.
        """
        with self._engine.begin() as conexion:
            # Primero se consulta la idempotencia.  Después se bloquea la orden
            # y se vuelve a consultar: ese segundo control cubre dos cierres
            # iguales que llegan al mismo tiempo sin esperar un lock adicional
            # sobre PostgreSQL.
            existente = (
                conexion.execute(
                    text("SELECT id, orden_id FROM cierre WHERE externa_id = :externa_id"),
                    {"externa_id": cierre_id},
                )
                .mappings()
                .first()
            )
            if existente is not None:
                if existente["orden_id"] != orden_id:
                    raise ConflictoEstado(
                        f"El identificador de cierre {cierre_id} ya pertenece a otra orden"
                    )
                habilitacion = self._habilitacion_de_orden(conexion, orden_id)
                return {
                    "cierre_id": str(existente["id"]),
                    "nuevo": False,
                    "habilita": (habilitacion["numero"] if habilitacion is not None else None),
                    "monto_habilita_usd": (
                        habilitacion["monto"] if habilitacion is not None else None
                    ),
                }

            # El bloqueo serializa cierres distintos de la misma orden.  La
            # única transición admitida desde un estado con decisión es
            # ``Observada -> Ejecutada`` para una nueva aclaración.
            estado_actual = conexion.execute(
                text("SELECT estado FROM orden WHERE id = :id FOR UPDATE"),
                {"id": orden_id},
            ).scalar_one_or_none()
            if estado_actual is None:
                raise ValueError(f"La orden no existe: {orden_id}")

            existente = (
                conexion.execute(
                    text("SELECT id, orden_id FROM cierre WHERE externa_id = :externa_id"),
                    {"externa_id": cierre_id},
                )
                .mappings()
                .first()
            )
            if existente is not None:
                if existente["orden_id"] != orden_id:
                    raise ConflictoEstado(
                        f"El identificador de cierre {cierre_id} ya pertenece a otra orden"
                    )
                habilitacion = self._habilitacion_de_orden(conexion, orden_id)
                return {
                    "cierre_id": str(existente["id"]),
                    "nuevo": False,
                    "habilita": (habilitacion["numero"] if habilitacion is not None else None),
                    "monto_habilita_usd": (
                        habilitacion["monto"] if habilitacion is not None else None
                    ),
                }

            if estado_actual in {"Cerrada", "Certificada"}:
                raise ConflictoEstado(
                    f"La orden {orden_id} ya tiene una decisión de cierre y no admite otro cierre"
                )
            if estado_actual not in _ESTADOS_ORDEN_CIERRE_ADMISIBLES:
                raise ConflictoEstado(
                    f"La orden {orden_id} está en estado {estado_actual} y no admite un cierre"
                )

            aclara_cierre_id: str | None = None
            if estado_actual == "Observada":
                observaciones = (
                    conexion.execute(
                        text(
                            """
                        SELECT c.id
                        FROM cierre c
                        JOIN decision_cierre d ON d.cierre_id = c.id
                        WHERE c.orden_id = :orden_id
                          AND d.decision = 'observar'
                          AND NOT EXISTS (
                              SELECT 1 FROM cierre aclaracion
                              WHERE aclaracion.aclara_cierre_id = c.id
                          )
                        ORDER BY c.creado_en DESC, c.id DESC
                        FOR UPDATE OF c
                        """
                        ),
                        {"orden_id": orden_id},
                    )
                    .scalars()
                    .all()
                )
                if len(observaciones) != 1:
                    raise ConflictoEstado(
                        f"La orden {orden_id} no tiene una observación pendiente de aclaración"
                    )
                aclara_cierre_id = str(observaciones[0])

            id_cierre = conexion.execute(
                text(
                    """
                    INSERT INTO cierre (
                        externa_id, orden_id, tecnico, empresa, transcripcion,
                        tareas_hechas, repuestos_usados, horas, prueba_final,
                        diferencias, aclara_cierre_id
                    )
                    VALUES (
                        :externa_id, :orden_id, :tecnico, :empresa, :transcripcion,
                        CAST(:tareas AS jsonb), CAST(:repuestos AS jsonb), :horas,
                        :prueba_final, CAST(:diferencias AS jsonb), :aclara_cierre_id
                    )
                    ON CONFLICT (externa_id) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "externa_id": cierre_id,
                    "orden_id": orden_id,
                    "tecnico": tecnico,
                    "empresa": empresa,
                    "transcripcion": transcripcion,
                    "tareas": json.dumps(tareas_hechas, ensure_ascii=False),
                    "repuestos": json.dumps(repuestos_usados, ensure_ascii=False),
                    "horas": horas,
                    "prueba_final": prueba_final,
                    "diferencias": json.dumps(diferencias, ensure_ascii=False),
                    "aclara_cierre_id": aclara_cierre_id,
                },
            ).scalar_one_or_none()
            if id_cierre is None:
                existente = (
                    conexion.execute(
                        text("SELECT id, orden_id FROM cierre WHERE externa_id = :externa_id"),
                        {"externa_id": cierre_id},
                    )
                    .mappings()
                    .one()
                )
                if existente["orden_id"] != orden_id:
                    raise ConflictoEstado(
                        f"El identificador de cierre {cierre_id} ya pertenece a otra orden"
                    )
                habilitacion = self._habilitacion_de_orden(conexion, orden_id)
                return {
                    "cierre_id": str(existente["id"]),
                    "nuevo": False,
                    "habilita": (habilitacion["numero"] if habilitacion is not None else None),
                    "monto_habilita_usd": (
                        habilitacion["monto"] if habilitacion is not None else None
                    ),
                }
            nuevo = True

            if nuevo:
                # El estado ya fue bloqueado y validado antes de insertar el
                # cierre.  La condición explícita mantiene la transición
                # segura incluso si una ruta externa modifica la fila.
                actualizado = conexion.execute(
                    text(
                        """
                        UPDATE orden
                        SET estado = CAST('Ejecutada' AS estado_orden),
                            motivo = :motivo,
                            actualizada_en = now()
                        WHERE id = :id AND estado = :estado
                        """
                    ),
                    {
                        "motivo": motivo,
                        "id": orden_id,
                        "estado": estado_actual,
                    },
                )
                if actualizado.rowcount != 1:
                    raise ValueError(
                        f"La orden {orden_id} cambió de estado mientras se registraba el cierre"
                    )
                orden = (
                    conexion.execute(
                        text(
                            """
                        SELECT evento_id, equipo FROM orden WHERE id = :id
                        """
                        ),
                        {"id": orden_id},
                    )
                    .mappings()
                    .one()
                )
                contenido_manifiesto = manifiesto_de_cierre(
                    evento_id=str(orden["evento_id"]),
                    orden_id=orden_id,
                    cierre_id=cierre_id,
                    tecnico=tecnico,
                    empresa=empresa,
                    transcripcion=transcripcion,
                    tareas_hechas=list(tareas_hechas),
                    repuestos_usados=list(repuestos_usados),
                    horas=horas,
                    prueba_final=prueba_final,
                    diferencias=list(diferencias),
                    fotos=list(fotos or []),
                )
                manifiesto_id, hash_manifiesto = self._insertar_manifiesto(
                    conexion,
                    tipo="cierre",
                    referencia_id=cierre_id,
                    evento_id=str(orden["evento_id"]),
                    orden_id=orden_id,
                    parte_id=None,
                    contenido=contenido_manifiesto,
                )
                self._insertar_ledger(
                    conexion,
                    tipo="cierre",
                    referencia_id=cierre_id,
                    evento_id=str(orden["evento_id"]),
                    orden_id=orden_id,
                    evidencia={
                        "manifiesto_id": manifiesto_id,
                        "hash_sha256": hash_manifiesto,
                    },
                    propuesta={
                        "tareas": list(tareas_hechas),
                        "repuestos": list(repuestos_usados),
                        "horas": horas,
                        "prueba_final": prueba_final,
                    },
                    actor="codigo",
                    rol="sistema",
                    estado="Ejecutada",
                    datos={"diferencias": list(diferencias)},
                    idempotencia=f"cierre:{cierre_id}",
                )

        return {
            "cierre_id": str(id_cierre),
            "nuevo": nuevo,
            "habilita": None,
            "monto_habilita_usd": None,
        }

    def obtener_cierre_de_orden(self, orden_id: str) -> dict[str, Any] | None:
        """El último cierre técnico registrado para una orden."""
        with self._engine.connect() as conexion:
            fila = (
                conexion.execute(
                    text(
                        """
                        SELECT id, externa_id, orden_id, tecnico, empresa,
                               transcripcion, tareas_hechas, repuestos_usados,
                               horas, prueba_final, diferencias, aclara_cierre_id,
                               creado_en
                        FROM cierre
                        WHERE orden_id = :orden_id
                        ORDER BY creado_en DESC, id DESC
                        LIMIT 1
                        """
                    ),
                    {"orden_id": orden_id},
                )
                .mappings()
                .first()
            )
        return dict(fila) if fila else None

    def obtener_manifiesto(
        self,
        tipo: str,
        referencia_id: str,
    ) -> dict[str, Any] | None:
        """El manifiesto inmutable de una referencia, si existe."""
        with self._engine.connect() as conexion:
            fila = (
                conexion.execute(
                    text(
                        """
                        SELECT id::text AS id, creado_en, tipo, referencia_id,
                               evento_id, orden_id, parte_id, contenido,
                               hash_sha256,
                               encode(
                                   sha256(convert_to(contenido::text, 'UTF8')),
                                   'hex'
                               ) AS hash_calculado
                        FROM manifiesto_evidencia
                        WHERE tipo = :tipo AND referencia_id = :referencia
                        """
                    ),
                    {"tipo": tipo, "referencia": referencia_id},
                )
                .mappings()
                .first()
            )
        return _manifiesto_desde_fila(fila) if fila is not None else None

    def obtener_manifiestos_de_orden(self, orden_id: str) -> list[dict[str, Any]]:
        """Todos los manifiestos de evidencia de una orden."""
        with self._engine.connect() as conexion:
            return self._manifiestos_de_orden(conexion, orden_id)

    def obtener_ledger_de_orden(self, orden_id: str) -> list[dict[str, Any]]:
        """La cadena append-only de decisiones de una orden, en orden."""
        with self._engine.connect() as conexion:
            return self._ledger_de_orden(conexion, orden_id)

    def reconstruir_orden(self, orden_id: str) -> dict[str, Any] | None:
        """Reconstruye una orden desde su evento hasta la decisión humana.

        La respuesta incluye el estado operativo actual, el evento que la
        originó, los manifiestos inmutables, el ledger y la trayectoria
        detallada.  Los manifiestos vuelven con ``hash_verificado`` para que
        el consumidor pueda comprobar que la evidencia no fue alterada.
        """
        with self._engine.connect() as conexion:
            orden = (
                conexion.execute(
                    text(
                        """
                        SELECT o.id, o.evento_id, o.equipo, o.estado, o.motivo,
                               o.detencion, o.ejecutor, o.reduce_inyeccion,
                               o.costo_estimado, o.creada_en, o.actualizada_en,
                               COALESCE(
                                   (SELECT json_agg(t.tarea_id ORDER BY t.tarea_id)
                                    FROM orden_tarea t WHERE t.orden_id = o.id),
                                   '[]'::json
                               ) AS tareas,
                               COALESCE(
                                   (SELECT json_agg(
                                       json_build_object(
                                           'codigo', r.repuesto_id,
                                           'cantidad', r.cantidad
                                       ) ORDER BY r.repuesto_id
                                   )
                                    FROM orden_repuesto r WHERE r.orden_id = o.id),
                                   '[]'::json
                               ) AS repuestos
                        FROM orden o
                        WHERE o.id = :id
                        """
                    ),
                    {"id": orden_id},
                )
                .mappings()
                .first()
            )
            if orden is None:
                return None
            evento = (
                conexion.execute(
                    text(
                        """
                        SELECT id, origen, estado, payload, creado_en
                        FROM evento_sistema WHERE id = :id
                        """
                    ),
                    {"id": orden["evento_id"]},
                )
                .mappings()
                .first()
            )
            manifiestos = self._manifiestos_de_orden(conexion, orden_id)
            ledger = self._ledger_de_orden(conexion, orden_id)
            trayectoria = (
                conexion.execute(
                    text(
                        """
                        SELECT id::text AS id, ts, etapa, tipo, modelo, herramienta,
                               argumentos, salida, tokens_prompt, tokens_completion,
                               costo_usd, segundos, error, orden_id, evento_id,
                               equipo, paso
                        FROM registro_trayectoria
                        WHERE orden_id = :orden
                        ORDER BY ts, paso, id
                        """
                    ),
                    {"orden": orden_id},
                )
                .mappings()
                .all()
            )
        return {
            "orden": dict(orden),
            "evento": dict(evento) if evento is not None else None,
            "manifiestos": manifiestos,
            "ledger": ledger,
            "trayectoria": [dict(fila) for fila in trayectoria],
            "integridad": {
                "manifiestos_verificados": all(
                    bool(manifiesto["hash_verificado"]) for manifiesto in manifiestos
                ),
                "entradas_ledger": len(ledger),
            },
        }

    def _manifiestos_de_orden(
        self,
        conexion: Connection,
        orden_id: str,
    ) -> list[dict[str, Any]]:
        filas = (
            conexion.execute(
                text(
                    """
                    SELECT id::text AS id, creado_en, tipo, referencia_id,
                           evento_id, orden_id, parte_id, contenido,
                           hash_sha256,
                           encode(
                               sha256(convert_to(contenido::text, 'UTF8')),
                               'hex'
                           ) AS hash_calculado
                    FROM manifiesto_evidencia
                    WHERE orden_id = :orden
                    ORDER BY creado_en, id
                    """
                ),
                {"orden": orden_id},
            )
            .mappings()
            .all()
        )
        return [_manifiesto_desde_fila(fila) for fila in filas]

    def _ledger_de_orden(
        self,
        conexion: Connection,
        orden_id: str,
    ) -> list[dict[str, Any]]:
        filas = (
            conexion.execute(
                text(
                    """
                    SELECT id, creado_en, tipo, referencia_id, evento_id, orden_id,
                           evidencia, analisis, propuesta, reglas, actor, rol,
                           estado, modelo, herramientas, tokens_prompt,
                           tokens_completion, costo_usd, segundos, datos,
                           idempotencia
                    FROM ledger_decisiones
                    WHERE orden_id = :orden
                    ORDER BY creado_en, id
                    """
                ),
                {"orden": orden_id},
            )
            .mappings()
            .all()
        )
        return [_ledger_desde_fila(fila) for fila in filas]

    def decidir_cierre_humano(
        self,
        *,
        orden_id: str,
        decision: str,
        actor: str,
        rol: str,
        motivo: str | None = None,
        monto_usd: float | None = None,
        fuente_monto: str | None = None,
    ) -> dict[str, Any]:
        """Registra la decisión humana de G2/G3 y sus efectos, en una transacción.

        * ``supervisor`` + ``cerrar`` deja la orden ``Cerrada``;
        * ``inspector`` + ``certificar`` la deja ``Certificada`` y emite el
          habilita;
        * ``inspector`` + ``observar`` la deja ``Observada``, sin habilita.

        La decisión, su actor, su rol, el motivo y la evidencia del cierre
        quedan en ``decision_cierre``. La certificación y el habilita sólo se
        escriben cuando corresponde.
        """
        accion = str(decision or "").strip().lower()
        actor = str(actor or "").strip()
        rol = str(rol or "").strip().lower()
        motivo = str(motivo or "").strip() or None
        if accion not in {"cerrar", "certificar", "observar"}:
            raise ValueError(f"Decisión de cierre inválida: {decision!r}")
        if not actor:
            raise ValueError("La decisión de cierre necesita un actor")
        if rol not in {"supervisor", "inspector"}:
            raise ValueError(f"Rol inválido para decidir un cierre: {rol!r}")
        if accion == "observar" and not motivo:
            raise ValueError("La observación necesita un motivo")

        estado = {"cerrar": "Cerrada", "certificar": "Certificada", "observar": "Observada"}[accion]
        with self._engine.begin() as conexion:
            fila = (
                conexion.execute(
                    text(
                        """
                        SELECT o.id, o.evento_id, o.equipo, o.estado, o.motivo,
                               o.detencion, o.ejecutor, o.reduce_inyeccion,
                               o.creada_en, o.actualizada_en,
                               c.id AS cierre_id, c.externa_id, c.tecnico, c.empresa,
                               c.transcripcion, c.tareas_hechas, c.repuestos_usados,
                               c.horas, c.prueba_final, c.diferencias, c.creado_en
                        FROM orden o
                        LEFT JOIN cierre c ON c.orden_id = o.id
                        WHERE o.id = :orden_id
                        ORDER BY c.creado_en DESC, c.id DESC
                        LIMIT 1
                        FOR UPDATE OF o
                        """
                    ),
                    {"orden_id": orden_id},
                )
                .mappings()
                .first()
            )
            if fila is None:
                raise ValueError(f"La orden no existe: {orden_id}")

            ejecutor = str(fila["ejecutor"] or "propio").strip().lower()
            if accion == "cerrar":
                if rol != "supervisor":
                    raise AutorizacionInvalida("Sólo el supervisor puede cerrar el trabajo")
                if ejecutor != "propio":
                    raise AutorizacionInvalida("El supervisor sólo puede cerrar trabajo propio")
            else:
                if rol != "inspector":
                    raise AutorizacionInvalida("Sólo el inspector puede certificar u observar")
                if ejecutor == "propio":
                    raise AutorizacionInvalida(
                        "El inspector sólo puede certificar u observar trabajo de contratista"
                    )

            if fila["cierre_id"] is None:
                raise ValueError("La orden no tiene un cierre técnico registrado")

            existente = (
                conexion.execute(
                    text(
                        """
                        SELECT decision, estado_orden, actor, rol, motivo, evidencia,
                               creada_en
                        FROM decision_cierre
                        WHERE cierre_id = :cierre_id
                        """
                    ),
                    {"cierre_id": fila["cierre_id"]},
                )
                .mappings()
                .first()
            )
            if existente is not None:
                if existente["decision"] != accion:
                    raise ConflictoEstado("La orden ya tiene una decisión de cierre")
                habilitacion = (
                    conexion.execute(
                        text(
                            """
                            SELECT numero, monto FROM habilitacion
                            WHERE orden_id = :orden_id
                            ORDER BY emitida_en LIMIT 1
                            """
                        ),
                        {"orden_id": orden_id},
                    )
                    .mappings()
                    .first()
                )
                orden = self.obtener_orden_completa(orden_id)
                return {
                    "decision": dict(existente),
                    "orden": orden,
                    "habilita": habilitacion["numero"] if habilitacion else None,
                    "monto_habilita_usd": (float(habilitacion["monto"]) if habilitacion else None),
                }

            if fila["estado"] != "Ejecutada":
                raise ConflictoEstado("La orden ya tiene una decisión de cierre")
            if accion == "certificar":
                if (
                    monto_usd is None
                    or isinstance(monto_usd, bool)
                    or not isinstance(monto_usd, (int, float, Decimal))
                ):
                    raise MontoContractualInvalido(
                        "El habilita necesita un monto USD positivo y finito"
                    )
                try:
                    monto_usd = float(monto_usd)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise MontoContractualInvalido(
                        "El habilita necesita un monto USD positivo y finito"
                    ) from exc
                if not math.isfinite(monto_usd) or monto_usd <= 0:
                    raise MontoContractualInvalido(
                        "El habilita necesita un monto USD positivo y finito"
                    )
            diferencias = list(fila["diferencias"] or [])
            cierre_referencia = str(fila["externa_id"] or fila["cierre_id"])
            evidencia = {
                "cierre_id": cierre_referencia,
                "tecnico": fila["tecnico"],
                "empresa": fila["empresa"],
                "transcripcion": fila["transcripcion"],
                "tareas_hechas": list(fila["tareas_hechas"] or []),
                "repuestos_usados": list(fila["repuestos_usados"] or []),
                "horas": fila["horas"],
                "prueba_final": fila["prueba_final"],
                "diferencias": diferencias,
                "cierre_registrado_en": fila["creado_en"].isoformat(),
            }
            decision_id = conexion.execute(
                text(
                    """
                    INSERT INTO decision_cierre (
                        cierre_id, orden_id, decision, estado_orden,
                        actor, rol, motivo, evidencia
                    )
                    VALUES (
                        :cierre_id, :orden_id, :decision, CAST(:estado AS estado_orden),
                        :actor, :rol, :motivo, CAST(:evidencia AS jsonb)
                    )
                    RETURNING id
                    """
                ),
                {
                    "cierre_id": fila["cierre_id"],
                    "orden_id": orden_id,
                    "decision": accion,
                    "estado": estado,
                    "actor": actor,
                    "rol": rol,
                    "motivo": motivo,
                    "evidencia": json.dumps(evidencia, ensure_ascii=False, default=str),
                },
            ).scalar_one()

            certificacion_id: str | None = None
            if accion in {"certificar", "observar"}:
                certificacion_id = conexion.execute(
                    text(
                        """
                        INSERT INTO certificacion (
                            orden_id, cierre_id, inspector, decision, motivo, rol
                        )
                        VALUES (
                            :orden_id, :cierre_id, :actor, :decision, :motivo, :rol
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "orden_id": orden_id,
                        "cierre_id": fila["cierre_id"],
                        "actor": actor,
                        "decision": "certifica" if accion == "certificar" else "observa",
                        "motivo": motivo,
                        "rol": rol,
                    },
                ).scalar_one()

            reglas = {
                "tipo": "cierre_humano",
                "decision": accion,
                "rol": rol,
                "cierre_id": cierre_referencia,
                "evidencia": evidencia,
            }
            conexion.execute(
                text(
                    """
                    INSERT INTO aprobacion (
                        orden_id, decision, motivo, reglas_evaluadas, usuario, rol
                    )
                    VALUES (
                        :orden_id, :decision, :motivo, CAST(:reglas AS jsonb),
                        :actor, :rol
                    )
                    """
                ),
                {
                    "orden_id": orden_id,
                    "decision": estado,
                    "motivo": motivo,
                    "reglas": json.dumps(reglas, ensure_ascii=False, default=str),
                    "actor": actor,
                    "rol": rol,
                },
            )

            motivo_final = motivo
            if not motivo_final:
                detalle = [
                    str(diferencia.get("detalle"))
                    for diferencia in diferencias
                    if isinstance(diferencia, dict) and diferencia.get("detalle")
                ]
                motivo_final = "; ".join(detalle) or None
            # PostgreSQL exige que el estado terminal tenga su habilita válido.
            # Se emite dentro de la misma transacción antes del UPDATE; si la
            # orden no cambia, el rollback también deshace la autorización.
            habilita: str | None = None
            monto: float | None = None
            if accion == "certificar":
                if monto_usd is None:
                    raise ValueError("La certificación necesita el monto del habilita")
                if certificacion_id is None:
                    raise RuntimeError("La certificación no quedó registrada")
                habilita, monto = self._emitir_habilita(
                    conexion,
                    orden_id=orden_id,
                    monto_usd=monto_usd,
                    anio=int(fila["creada_en"].year),
                    fuente=fuente_monto or "CT-SVP-2025 §2.1",
                    decision_id=str(decision_id),
                    certificacion_id=str(certificacion_id),
                )

            conexion.execute(
                text(
                    """
                    UPDATE orden
                    SET estado = CAST(:estado AS estado_orden),
                        motivo = :motivo,
                        actualizada_en = now()
                    WHERE id = :id
                    """
                ),
                {"estado": estado, "motivo": motivo_final, "id": orden_id},
            )

            manifiesto_cierre = (
                conexion.execute(
                    text(
                        """
                    SELECT id, hash_sha256, contenido
                    FROM manifiesto_evidencia
                    WHERE tipo = 'cierre' AND referencia_id = :referencia
                    """
                    ),
                    {"referencia": cierre_referencia},
                )
                .mappings()
                .first()
            )
            contenido_cierre = (
                dict(manifiesto_cierre["contenido"]) if manifiesto_cierre is not None else {}
            )
            evidencia_cierre = dict(contenido_cierre.get("evidencia_cierre") or evidencia)
            contenido_decision = manifiesto_de_decision_cierre(
                evento_id=str(fila["evento_id"]),
                orden_id=orden_id,
                cierre_id=cierre_referencia,
                decision=accion,
                estado=estado,
                actor=actor,
                rol=rol,
                motivo=motivo,
                evidencia_cierre=evidencia_cierre,
                habilita=habilita,
                monto_usd=monto,
            )
            manifiesto_id, hash_manifiesto = self._insertar_manifiesto(
                conexion,
                tipo="decision_cierre",
                referencia_id=cierre_referencia,
                evento_id=str(fila["evento_id"]),
                orden_id=orden_id,
                parte_id=None,
                contenido=contenido_decision,
            )
            self._insertar_ledger(
                conexion,
                tipo="decision_cierre",
                referencia_id=cierre_referencia,
                evento_id=str(fila["evento_id"]),
                orden_id=orden_id,
                evidencia={
                    "manifiesto_id": manifiesto_id,
                    "hash_sha256": hash_manifiesto,
                    "cierre_manifiesto_id": (
                        str(manifiesto_cierre["id"]) if manifiesto_cierre is not None else None
                    ),
                },
                reglas=reglas,
                actor=actor,
                rol=rol,
                estado=estado,
                datos={
                    "decision": accion,
                    "motivo": motivo,
                    "habilita": habilita,
                    "monto_usd": monto,
                },
                idempotencia=f"decision_cierre:{cierre_referencia}",
            )

        orden = self.obtener_orden_completa(orden_id)
        return {
            "decision": {
                "decision": accion,
                "estado_orden": estado,
                "actor": actor,
                "rol": rol,
                "motivo": motivo,
                "evidencia": evidencia,
            },
            "orden": orden,
            "habilita": habilita,
            "monto_habilita_usd": monto,
        }

    def _emitir_habilita(
        self,
        conexion: Connection,
        *,
        orden_id: str,
        monto_usd: float,
        anio: int,
        fuente: str,
        decision_id: str,
        certificacion_id: str,
    ) -> tuple[str, float]:
        """Emite el habilita con su número HAB-AAAA-NNNN (CT-SVP-2025 §3.4).

        El número es correlativo dentro del año. Si dos cierres llegan juntos,
        el índice único del número hace que uno reintente con el siguiente.
        """
        for intento in range(10):
            emitidas = conexion.execute(
                text(
                    """
                    SELECT count(*) FROM habilitacion
                    WHERE numero LIKE :prefijo
                    """
                ),
                {"prefijo": f"HAB-{anio}-%"},
            ).scalar_one()
            numero = f"HAB-{anio}-{int(emitidas) + 1 + intento:04d}"
            insertado = conexion.execute(
                text(
                    """
                    INSERT INTO habilitacion (
                        orden_id, numero, monto, moneda, fuente_monto,
                        decision_cierre_id, certificacion_id
                    )
                    VALUES (
                        :orden_id, :numero, :monto, 'USD', :fuente,
                        :decision_id, :certificacion_id
                    )
                    ON CONFLICT (numero) DO NOTHING
                    RETURNING numero
                    """
                ),
                {
                    "orden_id": orden_id,
                    "numero": numero,
                    "monto": monto_usd,
                    "fuente": fuente,
                    "decision_id": decision_id,
                    "certificacion_id": certificacion_id,
                },
            ).scalar_one_or_none()
            if insertado is not None:
                return str(insertado), float(monto_usd)
        raise RuntimeError("No se pudo emitir el número de habilita")

    def obtener_cierre(self, cierre_id: str) -> dict[str, Any] | None:
        """El cierre registrado, por su id externo."""
        with self._engine.connect() as conexion:
            fila = (
                conexion.execute(
                    text(
                        """
                        SELECT id, externa_id, orden_id, tecnico, empresa,
                               transcripcion, tareas_hechas, repuestos_usados,
                               horas, prueba_final, diferencias, aclara_cierre_id,
                               creado_en
                        FROM cierre WHERE externa_id = :externa_id
                        """
                    ),
                    {"externa_id": cierre_id},
                )
                .mappings()
                .first()
            )
        return dict(fila) if fila else None

    def obtener_habilitacion(self, orden_id: str) -> dict[str, Any] | None:
        """El habilita de la orden, si lo tiene."""
        with self._engine.connect() as conexion:
            fila = (
                conexion.execute(
                    text(
                        """
                        SELECT id, orden_id, numero, monto, moneda,
                               fuente_monto, emitida_en
                        FROM habilitacion WHERE orden_id = :orden_id
                        ORDER BY emitida_en LIMIT 1
                        """
                    ),
                    {"orden_id": orden_id},
                )
                .mappings()
                .first()
            )
        return dict(fila) if fila else None

    def ordenes_por_equipo(self, equipo: str) -> list[dict[str, Any]]:
        """Las órdenes del equipo, con su estado actual."""
        with self._engine.connect() as conexion:
            filas = (
                conexion.execute(
                    text(
                        """
                        SELECT id, equipo, estado, motivo, costo_estimado, actualizada_en
                        FROM orden WHERE equipo = :equipo ORDER BY actualizada_en DESC
                        """
                    ),
                    {"equipo": equipo},
                )
                .mappings()
                .all()
            )
        return [dict(fila) for fila in filas]

    def ordenes_pendientes(self) -> list[dict[str, Any]]:
        """Las órdenes que esperan aprobación, con lo que hay que republicar.

        Cuando se corta la luz, la PC de planta se apaga de golpe. Al volver,
        el sistema recupera de la base las órdenes que quedaron pendientes y
        las vuelve a publicar **con el mismo id y el mismo estado**: la planta
        no las pierde y no se abre una orden nueva.
        """
        with self._engine.connect() as conexion:
            filas = (
                conexion.execute(
                    text(
                        """
                        SELECT o.id, o.evento_id, o.equipo, o.estado, o.motivo,
                               o.detencion, o.creada_en, o.actualizada_en,
                               COALESCE(
                                   (SELECT json_agg(
                                       json_build_object(
                                           'codigo', r.repuesto_id,
                                           'cantidad', r.cantidad
                                       ) ORDER BY r.repuesto_id
                                   )
                                    FROM orden_repuesto r WHERE r.orden_id = o.id),
                                   '[]'::json
                               ) AS repuestos,
                               COALESCE(
                                   (SELECT json_agg(t.tarea_id ORDER BY t.tarea_id)
                                    FROM orden_tarea t WHERE t.orden_id = o.id),
                                   '[]'::json
                               ) AS tareas
                        FROM orden o
                        WHERE o.estado = 'Pendiente de aprobacion'
                        ORDER BY o.creada_en
                        """
                    )
                )
                .mappings()
                .all()
            )
        return [dict(fila) for fila in filas]

    def decidir_orden(
        self,
        orden_id: str,
        decision: str,
        *,
        motivo: str | None = None,
        usuario: str | None = None,
        rol: str | None = None,
    ) -> dict[str, Any] | None:
        """La decisión del planificador sobre una orden pendiente.

        Es la única escritura sobre órdenes que **no** viene de las reglas:
        la decisión de una persona (el enunciado, sección D: la orden pendiente
        *"se reanuda cuando el planificador la aprueba o la rechaza"*).

        * Sólo toca órdenes que están ``Pendiente de aprobacion``: una orden
          ya decidida no se cambia.
        * El rechazo necesita motivo.
        * La decisión queda en la tabla ``aprobacion`` con su usuario y su
          motivo, así que se puede auditar quién decidió qué.
        """
        estado = _ESTADO_ORDEN_DB.get(decision)
        if estado not in ("Aprobada", "Rechazada"):
            raise ValueError(f"Decisión inválida: {decision!r}")
        motivo = (motivo or "").strip() or None
        if estado == "Rechazada" and not motivo:
            raise ValueError("El rechazo de una orden necesita un motivo")
        with self._engine.begin() as conexion:
            fila = (
                conexion.execute(
                    text(
                        """
                        UPDATE orden
                        SET estado = :estado,
                            motivo = :motivo,
                            actualizada_en = now()
                        WHERE id = :id AND estado = 'Pendiente de aprobacion'
                        RETURNING id, evento_id, equipo, estado, motivo,
                                  costo_estimado, detencion, creada_en, actualizada_en,
                                  COALESCE(
                                      (SELECT json_agg(t.tarea_id ORDER BY t.tarea_id)
                                       FROM orden_tarea t WHERE t.orden_id = orden.id),
                                      '[]'::json
                                  ) AS tareas,
                                  COALESCE(
                                      (SELECT json_agg(
                                          json_build_object(
                                              'codigo', r.repuesto_id,
                                              'cantidad', r.cantidad
                                          ) ORDER BY r.repuesto_id
                                       )
                                       FROM orden_repuesto r WHERE r.orden_id = orden.id),
                                      '[]'::json
                                  ) AS repuestos
                        """
                    ),
                    {
                        "id": orden_id,
                        "estado": estado,
                        "motivo": motivo if estado == "Rechazada" else None,
                    },
                )
                .mappings()
                .first()
            )
            if fila is None:
                return None
            conexion.execute(
                text(
                    """
                    INSERT INTO aprobacion (
                        orden_id, decision, motivo, reglas_evaluadas, usuario, rol
                    )
                    SELECT :orden, :decision, :motivo,
                           COALESCE(
                               (SELECT reglas_evaluadas FROM aprobacion
                                WHERE orden_id = :orden ORDER BY creada_en DESC LIMIT 1),
                               '{}'::jsonb
                           ),
                           :usuario, :rol
                    WHERE NOT EXISTS (
                        SELECT 1 FROM aprobacion
                        WHERE orden_id = :orden AND decision = :decision
                          AND COALESCE(usuario, '') = COALESCE(:usuario, '')
                    )
                    """
                ),
                {
                    "orden": orden_id,
                    "decision": decision,
                    "motivo": motivo,
                    "usuario": usuario,
                    "rol": rol,
                },
            )
            reglas = conexion.execute(
                text(
                    "SELECT reglas_evaluadas FROM aprobacion "
                    "WHERE orden_id = :orden ORDER BY creada_en DESC, id DESC LIMIT 1"
                ),
                {"orden": orden_id},
            ).scalar_one()
            manifiesto = (
                conexion.execute(
                    text(
                        """
                    SELECT id, hash_sha256 FROM manifiesto_evidencia
                    WHERE orden_id = :orden AND tipo = 'orden'
                    ORDER BY creado_en DESC, id DESC LIMIT 1
                    """
                    ),
                    {"orden": orden_id},
                )
                .mappings()
                .first()
            )
            self._insertar_ledger(
                conexion,
                tipo="aprobacion",
                referencia_id=orden_id,
                evento_id=str(fila["evento_id"]),
                orden_id=orden_id,
                evidencia=(
                    {
                        "manifiesto_id": str(manifiesto["id"]),
                        "hash_sha256": str(manifiesto["hash_sha256"]),
                    }
                    if manifiesto is not None
                    else {}
                ),
                propuesta={
                    "tareas": list(fila["tareas"] or []),
                    "repuestos": list(fila["repuestos"] or []),
                    "costo_usd": fila["costo_estimado"],
                },
                reglas=reglas or {},
                actor=usuario,
                rol=rol,
                estado=estado,
                datos={"decision": decision, "motivo": motivo},
                idempotencia=(f"aprobacion:{orden_id}:{decision}:{usuario or 'sin_usuario'}"),
            )
        return dict(fila)

    def ordenes_para_central(self, limite: int = 50) -> list[dict[str, Any]]:
        """Las órdenes que el planificador tiene a la vista.

        Primero las que esperan decisión y después las más recientes: es lo
        que muestra la Central de mantenimiento.
        """
        with self._engine.connect() as conexion:
            filas = (
                conexion.execute(
                    text(
                        """
                        SELECT o.id, o.equipo, o.estado, o.motivo, o.costo_estimado,
                               o.creada_en, o.actualizada_en,
                               COALESCE(
                                   (SELECT json_agg(t.tarea_id ORDER BY t.tarea_id)
                                     FROM orden_tarea t WHERE t.orden_id = o.id),
                                   '[]'::json
                               ) AS tareas,
                               COALESCE(
                                   (SELECT json_agg(
                                       json_build_object(
                                           'codigo', r.repuesto_id,
                                           'cantidad', r.cantidad
                                       ) ORDER BY r.repuesto_id
                                   )
                                    FROM orden_repuesto r WHERE r.orden_id = o.id),
                                   '[]'::json
                               ) AS repuestos
                        FROM orden o
                        ORDER BY (o.estado = 'Pendiente de aprobacion') DESC,
                                 o.actualizada_en DESC
                        LIMIT :limite
                        """
                    ),
                    {"limite": max(1, int(limite))},
                )
                .mappings()
                .all()
            )
        return [dict(fila) for fila in filas]

    def cierres_para_central(self, limite: int = 50) -> list[dict[str, Any]]:
        """Los cierres técnicos y su decisión humana para la Central.

        Primero quedan los cierres en estado ``Ejecutada`` que todavía no
        tienen decisión. Cada fila incluye la evidencia técnica del cierre y,
        cuando existe, el actor, rol, motivo y evidencia de la decisión.
        """
        with self._engine.connect() as conexion:
            filas = (
                conexion.execute(
                    text(
                        """
                        WITH ultimo AS (
                            SELECT DISTINCT ON (orden_id)
                                   id, externa_id, orden_id, tecnico, empresa,
                                   transcripcion, tareas_hechas, repuestos_usados,
                                   horas, prueba_final, diferencias, creado_en
                            FROM cierre
                            ORDER BY orden_id, creado_en DESC, id DESC
                        ), habilita AS (
                            SELECT DISTINCT ON (orden_id)
                                   orden_id, numero, monto
                            FROM habilitacion
                            ORDER BY orden_id, emitida_en, id
                        )
                        SELECT o.id AS orden_id, o.equipo, o.estado, o.motivo,
                               o.ejecutor, o.creada_en, o.actualizada_en,
                               c.externa_id AS cierre_id, c.tecnico, c.empresa,
                               c.transcripcion, c.tareas_hechas, c.repuestos_usados,
                               c.horas, c.prueba_final, c.diferencias,
                               c.creado_en AS cierre_registrado_en,
                               dc.id AS decision_id, dc.decision,
                               dc.estado_orden, dc.actor, dc.rol,
                               dc.motivo AS decision_motivo, dc.evidencia,
                               dc.creada_en AS decidido_en,
                               h.numero AS habilita, h.monto AS monto_habilita_usd,
                               COALESCE(
                                   (SELECT json_agg(t.tarea_id ORDER BY t.tarea_id)
                                    FROM orden_tarea t WHERE t.orden_id = o.id),
                                   '[]'::json
                               ) AS tareas,
                               COALESCE(
                                   (SELECT json_agg(
                                       json_build_object(
                                           'codigo', r.repuesto_id,
                                           'cantidad', r.cantidad
                                       ) ORDER BY r.repuesto_id
                                    )
                                    FROM orden_repuesto r WHERE r.orden_id = o.id),
                                   '[]'::json
                               ) AS repuestos
                        FROM orden o
                        JOIN ultimo c ON c.orden_id = o.id
                        LEFT JOIN decision_cierre dc ON dc.cierre_id = c.id
                        LEFT JOIN habilita h ON h.orden_id = o.id
                        ORDER BY (o.estado = 'Ejecutada' AND dc.id IS NULL) DESC,
                                 c.creado_en DESC
                        LIMIT :limite
                        """
                    ),
                    {"limite": max(1, int(limite))},
                )
                .mappings()
                .all()
            )
        return [dict(fila) for fila in filas]

    # ------------------------------------------------------------------ cola

    def encolar_outbox(
        self,
        tipo: str,
        payload: dict[str, Any],
        topico: str = "sistema/tablet",
        *,
        conexion: Any | None = None,
    ) -> str:
        """Encola un mensaje para publicar cuando vuelva el enlace.

        La idempotencia la da el id del mensaje: si el mismo aviso, evento u
        orden se encola dos veces **mientras está pendiente**, no queda
        duplicado en la cola. Un mensaje que ya salió puede volver a encolarse
        (si el bus se cae otra vez): lo que importa es que no haya dos copias
        del mismo mensaje esperando a la vez.

        Además queda escrito en ``idempotency_key``, que es el registro
        auditable de qué se encoló.

        **Outbox transaccional (P1-03):** si se entrega ``conexion``, la
        inserción se ejecuta dentro de esa transacción (por ejemplo, la misma
        que guarda la orden o el evento), de modo que el mensaje y su efecto
        de negocio se comprometen juntos. Si no se entrega, se abre una
        transacción propia.
        """
        payload_json = json.dumps(payload, ensure_ascii=False)

        def _ejecutar(con: Connection) -> str:
            id_outbox = None
            for _ in range(3):
                id_outbox = con.execute(
                    text(
                        """
                        INSERT INTO outbox_cola (tipo, payload, topico, estado)
                        VALUES (:tipo, CAST(:payload AS jsonb), :topico, 'pendiente')
                        ON CONFLICT DO NOTHING
                        RETURNING id
                        """
                    ),
                    {"tipo": tipo, "payload": payload_json, "topico": topico},
                ).scalar_one_or_none()
                if id_outbox is not None:
                    break
                # La fila pendiente es la misma orden, no una copia: se
                # actualiza con el snapshot más reciente para no publicar un
                # estado viejo cuando vuelva el bus.
                id_outbox = con.execute(
                    text(
                        """
                        UPDATE outbox_cola
                        SET payload = CAST(:payload AS jsonb)
                        WHERE id = (
                            SELECT id FROM outbox_cola
                            WHERE tipo = :tipo
                              AND payload ->> 'id' = :mensaje_id
                              AND estado = 'pendiente'
                            ORDER BY creado_en, id
                            LIMIT 1
                            FOR UPDATE
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "tipo": tipo,
                        "payload": payload_json,
                        "mensaje_id": payload.get("id", ""),
                    },
                ).scalar_one_or_none()
                if id_outbox is not None:
                    break
            if id_outbox is None:
                raise RuntimeError(f"No se pudo encolar el mensaje {payload.get('id', '')!r}")
            # El registro de idempotencia deja escrito qué mensaje se
            # encoló y en qué fila: así no se encola dos veces y se puede
            # auditar qué salió de la planta.
            con.execute(
                text(
                    """
                    INSERT INTO idempotency_key (clave, tipo, referencia_id)
                    VALUES (:clave, :tipo, :referencia)
                    ON CONFLICT (clave) DO UPDATE
                    SET referencia_id = EXCLUDED.referencia_id
                    """
                ),
                {
                    "clave": f"outbox:{tipo}:{payload.get('id', '')}",
                    "tipo": tipo,
                    "referencia": str(id_outbox),
                },
            )
            return str(id_outbox)

        if conexion is not None:
            return _ejecutar(conexion)
        with self._engine.begin() as conexion_nueva:
            return _ejecutar(conexion_nueva)

    def marcar_outbox_publicando(
        self, id_outbox: str, *, conexion: Any | None = None
    ) -> None:
        """Marca una fila de la cola como "publicando" (bloqueo de drenador).

        Se usa dentro de ``publicar_pendientes`` para que ningún otro
        drenador toque la misma fila mientras este la publica (P1-03).
        Si se entrega ``conexion``, se ejecuta dentro de esa transacción.
        """

        def _ejecutar(con: Connection) -> None:
            con.execute(
                text(
                    """
                    UPDATE outbox_cola
                    SET estado = 'publicando'
                    WHERE id = :id AND estado = 'pendiente'
                    """
                ),
                {"id": id_outbox},
            )

        if conexion is not None:
            _ejecutar(conexion)
        elif self._engine is not None:
            with self._engine.begin() as conexion_nueva:
                _ejecutar(conexion_nueva)

    def marcar_outbox_enviado(
        self, id_outbox: str, *, conexion: Any | None = None
    ) -> None:
        """Marca una fila de la cola como enviada.

        Si se entrega ``conexion``, se ejecuta dentro de esa transacción
        (outbox transaccional: publicar + marcar enviado en una sola
        transacción, P1-03).
        """

        def _ejecutar(con: Connection) -> None:
            con.execute(
                text(
                    """
                    UPDATE outbox_cola
                    SET estado = 'enviado'
                    WHERE id = :id
                    """
                ),
                {"id": id_outbox},
            )

        if conexion is not None:
            _ejecutar(conexion)
        elif self._engine is not None:
            with self._engine.begin() as conexion_nueva:
                _ejecutar(conexion_nueva)

    def marcar_outbox_error(
        self, id_outbox: str, *, conexion: Any | None = None
    ) -> None:
        """Suma un intento fallido. El mensaje sigue pendiente: se reintenta.

        Si se entrega ``conexion``, se ejecuta dentro de esa transacción.
        """

        def _ejecutar(con: Connection) -> None:
            con.execute(
                text(
                    """
                    UPDATE outbox_cola
                    SET intentos = intentos + 1, proximo_intento = now() + interval '30 seconds'
                    WHERE id = :id
                    """
                ),
                {"id": id_outbox},
            )

        if conexion is not None:
            _ejecutar(conexion)
        elif self._engine is not None:
            with self._engine.begin() as conexion_nueva:
                _ejecutar(conexion_nueva)

    def pendientes_outbox(self, limite: int = 50) -> list[tuple[str, str, str, dict[str, Any]]]:
        """Mensajes en cola, en el orden en que se encolaron.

        Cada fila trae ``(id, tipo, topico, payload)``: el tipo dice qué
        contrato hay que validar y el tópico, por dónde sale.
        """
        with self._engine.connect() as conexion:
            filas = (
                conexion.execute(
                    text(
                        """
                        SELECT q.id, q.tipo, q.topico,
                               CASE
                                   WHEN q.tipo = 'orden' AND o.id IS NOT NULL THEN
                                       q.payload || jsonb_build_object(
                                           'estado', CASE o.estado
                                               WHEN 'Pendiente de aprobacion'
                                                   THEN 'PendienteAprobacion'
                                               ELSE o.estado::text
                                           END,
                                           'motivo', o.motivo,
                                           'detiene_equipo', o.detencion,
                                           'reduce_inyeccion', o.reduce_inyeccion,
                                           'ejecutor', o.ejecutor,
                                           'tareas', COALESCE(
                                               (SELECT json_agg(t.tarea_id ORDER BY t.tarea_id)
                                                FROM orden_tarea t WHERE t.orden_id = o.id),
                                               '[]'::json
                                           ),
                                           'repuestos', COALESCE(
                                               (SELECT json_agg(
                                                   json_build_object(
                                                       'codigo', r.repuesto_id,
                                                       'cantidad', r.cantidad
                                                   ) ORDER BY r.repuesto_id
                                               )
                                                FROM orden_repuesto r
                                                WHERE r.orden_id = o.id),
                                               '[]'::json
                                           ),
                                           'habilita', h.numero,
                                           'monto_habilita_usd', h.monto
                                       )
                                   ELSE q.payload
                               END AS payload
                        FROM outbox_cola q
                        LEFT JOIN orden o
                          ON q.tipo = 'orden' AND o.id = q.payload ->> 'id'
                        LEFT JOIN LATERAL (
                            SELECT numero, monto
                            FROM habilitacion
                            WHERE orden_id = o.id
                            ORDER BY emitida_en
                            LIMIT 1
                        ) h ON q.tipo = 'orden'
                        WHERE q.estado = 'pendiente'
                          AND (
                              q.proximo_intento IS NULL OR q.proximo_intento <= now()
                          )
                        ORDER BY q.creado_en, q.id
                        LIMIT :limite
                        """
                    ),
                    {"limite": limite},
                )
                .mappings()
                .all()
            )
        return [
            (str(fila["id"]), str(fila["tipo"]), str(fila["topico"]), dict(fila["payload"]))
            for fila in filas
        ]

def _parte_id_manifiesto(manifiesto: dict[str, Any]) -> str | None:
    """El id real del parte; ``no_disponible`` no es un id."""
    valor = (manifiesto.get("parte") or {}).get("id")
    if valor is None or str(valor) in {"", "no_disponible"}:
        return None
    return str(valor)


def _manifiesto_desde_fila(fila: Any) -> dict[str, Any]:
    """Normaliza un manifiesto y verifica su hash contra el contenido."""
    datos = dict(fila)
    hash_guardado = str(datos.get("hash_sha256") or "")
    hash_calculado = str(datos.get("hash_calculado") or "")
    datos["hash_verificado"] = bool(hash_guardado and hash_guardado == hash_calculado)
    return datos


def _ledger_desde_fila(fila: Any) -> dict[str, Any]:
    """Normaliza una entrada del ledger para la API y las pruebas."""
    datos = dict(fila)
    datos["id"] = int(datos["id"])
    datos["tokens_prompt"] = int(datos.get("tokens_prompt") or 0)
    datos["tokens_completion"] = int(datos.get("tokens_completion") or 0)
    datos["costo_usd"] = float(datos.get("costo_usd") or 0.0)
    datos["segundos"] = float(datos.get("segundos") or 0.0)
    return datos


def _json(contenido: Any) -> str:
    """Serializa JSON para PostgreSQL sin perder acentos ni datos simples."""
    return json.dumps(contenido, ensure_ascii=False, default=str)


def _metricas_manifiesto(manifiesto: dict[str, Any]) -> dict[str, Any]:
    """Modelo, tools, tokens, costo y tiempo que dejó la evidencia."""
    modelos: set[str] = set()
    herramientas: set[str] = set()
    tokens_prompt = 0
    tokens_completion = 0
    costo_usd = 0.0
    segundos = 0.0
    fotos = manifiesto.get("fotos", {}).get("elementos") or []
    for foto in fotos:
        if not isinstance(foto, dict):
            continue
        vision = foto.get("vision") or {}
        if not isinstance(vision, dict):
            continue
        modelo = str(vision.get("modelo") or "").strip()
        if modelo and modelo != "no_disponible":
            modelos.add(modelo)
            herramientas.add("vision.describir")
        tokens_prompt += int(vision.get("tokens_prompt") or 0)
        tokens_completion += int(vision.get("tokens_completion") or 0)
        segundos += float(vision.get("segundos") or 0.0)
    if manifiesto.get("fragmentos", {}).get("estado") == "disponible":
        herramientas.add("rag.contexto.construir_contexto")
    return {
        "modelo": ", ".join(sorted(modelos)) or None,
        "herramientas": sorted(herramientas),
        "tokens_prompt": tokens_prompt,
        "tokens_completion": tokens_completion,
        "costo_usd": costo_usd,
        "segundos": segundos,
    }


def _analisis_desde_reglas(reglas: dict[str, Any] | None) -> dict[str, Any]:
    """Snapshot mínimo para llamadas directas al repositorio."""
    datos = reglas or {}
    return {
        "falla": datos.get("falla"),
        "causas": list(datos.get("causas") or []),
        "citas": list(datos.get("citas") or []),
        "reincidencia": bool(datos.get("reincidencia")),
    }


def _propuesta_desde_orden(
    orden: OrdenSistema,
    repuestos: list[dict[str, Any]] | None,
    costo_usd: float | None,
) -> dict[str, Any]:
    """Propuesta persistida para llamadas directas al repositorio."""
    return {
        "equipo": orden.equipo,
        "tareas": list(orden.tareas),
        "repuestos": list(repuestos or []),
        "costo_usd": costo_usd,
        "detiene_equipo": orden.detiene_equipo,
        "reduce_inyeccion": orden.reduce_inyeccion,
        "ejecutor": orden.ejecutor,
    }


def _filas_json(filas: list[Hallazgo]) -> list[dict[str, Any]]:
    return [
        {
            "tag": fila.tag,
            "variable": fila.variable,
            "valor": fila.valor,
            "unidad": fila.unidad,
            "observacion": fila.observacion,
            "texto_fuente": fila.texto_fuente,
            "foto_indice": fila.foto_indice,
        }
        for fila in filas
    ]


def filas_desde_json(datos: list[dict[str, Any]]) -> list[Hallazgo]:
    return [
        Hallazgo(
            tag=fila.get("tag"),
            variable=fila.get("variable") or "",
            valor=fila.get("valor"),
            unidad=fila.get("unidad"),
            observacion=fila.get("observacion"),
            texto_fuente=fila.get("texto_fuente"),
            foto_indice=fila.get("foto_indice"),
        )
        for fila in datos
    ]
