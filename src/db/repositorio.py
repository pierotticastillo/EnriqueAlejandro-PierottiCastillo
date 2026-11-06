"""Persistencia de notas, partes, fotos y conversaciones de tablet."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from planta_contrato import EventoSistema, MensajeTablet, NotaRecorrida
from sqlalchemy import text
from sqlalchemy.engine import Engine

from planta.parte_extractor import Hallazgo, Pregunta


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
    ) -> bool:
        """Persiste cualquier evento de forma idempotente y lo vincula a una nota."""
        estados_db = {
            "SinNovedad": "Sin novedad",
            "Escalado": "Escalado",
            "AlertaSeguridad": "AlertaSeguridad",
        }
        payload = evento.model_dump(mode="json")
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

    # ------------------------------------------------------------------ cola

    def encolar_outbox(self, tipo: str, payload: dict[str, Any]) -> str:
        """Encola un mensaje para publicar cuando vuelva el enlace.

        La idempotencia la da el id del mensaje: si el mismo aviso se encola
        dos veces no queda duplicado en la cola.
        """
        with self._engine.begin() as conexion:
            id_outbox = conexion.execute(
                text(
                    """
                    INSERT INTO outbox_cola (tipo, payload, estado)
                    VALUES (:tipo, CAST(:payload AS jsonb), 'pendiente')
                    ON CONFLICT DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "tipo": tipo,
                    "payload": json.dumps(payload, ensure_ascii=False),
                },
            ).scalar_one_or_none()
            if id_outbox is None:
                id_outbox = conexion.execute(
                    text(
                        """
                        SELECT id FROM outbox_cola
                        WHERE tipo = :tipo AND payload ->> 'id' = :mensaje_id
                        LIMIT 1
                        """
                    ),
                    {"tipo": tipo, "mensaje_id": payload.get("id", "")},
                ).scalar_one()
        return str(id_outbox)

    def pendientes_outbox(self, limite: int = 50) -> list[tuple[str, dict[str, Any]]]:
        """Mensajes en cola, en el orden en que se encolaron."""
        with self._engine.connect() as conexion:
            filas = (
                conexion.execute(
                    text(
                        """
                        SELECT id, payload
                        FROM outbox_cola
                        WHERE estado = 'pendiente'
                          AND (proximo_intento IS NULL OR proximo_intento <= now())
                        ORDER BY creado_en, id
                        LIMIT :limite
                        """
                    ),
                    {"limite": limite},
                )
                .mappings()
                .all()
            )
        return [(str(fila["id"]), dict(fila["payload"])) for fila in filas]

    def marcar_outbox_enviado(self, id_outbox: str) -> None:
        with self._engine.begin() as conexion:
            conexion.execute(
                text(
                    """
                    UPDATE outbox_cola
                    SET estado = 'enviado'
                    WHERE id = :id
                    """
                ),
                {"id": id_outbox},
            )

    def marcar_outbox_error(self, id_outbox: str) -> None:
        """Suma un intento fallido. El mensaje sigue pendiente: se reintenta."""
        with self._engine.begin() as conexion:
            conexion.execute(
                text(
                    """
                    UPDATE outbox_cola
                    SET intentos = intentos + 1, proximo_intento = now() + interval '30 seconds'
                    WHERE id = :id
                    """
                ),
                {"id": id_outbox},
            )


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
