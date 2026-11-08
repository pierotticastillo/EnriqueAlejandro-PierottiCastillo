"""Persistencia de notas, partes, fotos, conversaciones y órdenes."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from planta_contrato import EventoSistema, MensajeTablet, NotaRecorrida, OrdenSistema
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

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

    # ------------------------------------------------------------------ órdenes

    def guardar_orden(
        self,
        orden: OrdenSistema,
        *,
        reglas: dict[str, Any] | None = None,
        descripcion: str | None = None,
        costo_usd: float | None = None,
        repuestos: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Persiste la orden y su evaluación de reglas, de forma idempotente.

        El id de la orden es determinista (equipo + falla): si el mismo equipo
        vuelve a fallar, la orden se actualiza en vez de abrir una segunda.
        """
        estado_db = _ESTADO_ORDEN_DB.get(orden.estado.value)
        if estado_db is None:
            raise ValueError(f"Estado de orden desconocido: {orden.estado.value}")
        evento_id = orden.evento_ids[0] if orden.evento_ids else None
        if evento_id is None:
            raise ValueError("La orden tiene que referenciar el evento que la originó")
        reglas_json = json.dumps(reglas or {}, ensure_ascii=False)
        detalles = {
            str(detalle.get("codigo")): detalle for detalle in (repuestos or [])
        }
        with self._engine.begin() as conexion:
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
                        estado = EXCLUDED.estado,
                        motivo = EXCLUDED.motivo,
                        detencion = EXCLUDED.detencion,
                        costo_estimado = EXCLUDED.costo_estimado,
                        ejecutor = EXCLUDED.ejecutor,
                        reduce_inyeccion = EXCLUDED.reduce_inyeccion,
                        actualizada_en = now()
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
                },
            )
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
        return True

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
                               o.detencion, o.ejecutor, o.reduce_inyeccion,
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

    def registrar_cierre(
        self,
        *,
        cierre_id: str,
        orden_id: str,
        estado: str,
        anio: int,
        tecnico: str,
        empresa: str,
        transcripcion: str,
        tareas_hechas: list[str],
        repuestos_usados: list[dict[str, Any]],
        horas: float | None,
        prueba_final: str | None,
        diferencias: list[dict[str, Any]],
        inspector: str,
        decision: str | None,
        motivo: str | None,
        monto_usd: float | None = None,
        fuente_monto: str | None = None,
    ) -> dict[str, Any]:
        """Guarda el cierre, la certificación y el habilita, y actualiza la orden.

        Todo en una transacción: o queda escrito el cierre con su certificación
        y el estado nuevo de la orden, o no queda nada.

        Es **idempotente** por el id externo del cierre: la misma nota de
        cierre puede volver a llegar (o el sistema puede reiniciar) y no se
        duplica ni el cierre ni la certificación ni el habilita.

        Devuelve el número del habilita y su monto: el número se emite acá,
        dentro de la transacción, para que no haya dos habilita con el mismo
        número.

        ``decision`` es ``None`` cuando el cierre no se certifica: el trabajo
        propio lo cierra el supervisor, sin habilita ni certificación.
        """
        if decision is not None and decision not in {"certifica", "observa"}:
            raise ValueError(f"Decisión de certificación inválida: {decision}")
        estado_db = _ESTADO_ORDEN_DB.get(estado)
        if estado_db is None:
            raise ValueError(f"Estado de orden desconocido: {estado}")
        with self._engine.begin() as conexion:
            id_cierre = conexion.execute(
                text(
                    """
                    INSERT INTO cierre (
                        externa_id, orden_id, tecnico, empresa, transcripcion,
                        tareas_hechas, repuestos_usados, horas, prueba_final,
                        diferencias
                    )
                    VALUES (
                        :externa_id, :orden_id, :tecnico, :empresa, :transcripcion,
                        CAST(:tareas AS jsonb), CAST(:repuestos AS jsonb), :horas,
                        :prueba_final, CAST(:diferencias AS jsonb)
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
                },
            ).scalar_one_or_none()
            nuevo = id_cierre is not None
            if id_cierre is None:
                id_cierre = conexion.execute(
                    text("SELECT id FROM cierre WHERE externa_id = :externa_id"),
                    {"externa_id": cierre_id},
                ).scalar_one()

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
                {"estado": estado_db, "motivo": motivo, "id": orden_id},
            )

            conexion.execute(
                text(
                    """
                    INSERT INTO certificacion (
                        orden_id, cierre_id, inspector, decision, motivo
                    )
                    SELECT :orden_id, :cierre_id, :inspector, :decision, :motivo
                    WHERE CAST(:decision AS text) IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM certificacion WHERE cierre_id = :cierre_id
                      )
                    """
                ),
                {
                    "orden_id": orden_id,
                    "cierre_id": id_cierre,
                    "inspector": inspector,
                    "decision": decision,
                    "motivo": motivo,
                },
            )

            habilita: str | None = None
            monto: float | None = None
            if decision == "certifica" and monto_usd is not None:
                existente = conexion.execute(
                    text(
                        """
                        SELECT numero, monto FROM habilitacion
                        WHERE orden_id = :orden_id
                        ORDER BY emitida_en LIMIT 1
                        """
                    ),
                    {"orden_id": orden_id},
                ).mappings().first()
                if existente is not None:
                    habilita = str(existente["numero"])
                    monto = float(existente["monto"])
                else:
                    habilita, monto = self._emitir_habilita(
                        conexion,
                        orden_id=orden_id,
                        monto_usd=monto_usd,
                        anio=anio,
                        fuente=fuente_monto or "CT-SVP-2025 §2.1",
                    )
        return {
            "cierre_id": str(id_cierre),
            "nuevo": nuevo,
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
                        orden_id, numero, monto, moneda, fuente_monto
                    )
                    VALUES (:orden_id, :numero, :monto, 'USD', :fuente)
                    ON CONFLICT (numero) DO NOTHING
                    RETURNING numero
                    """
                ),
                {
                    "orden_id": orden_id,
                    "numero": numero,
                    "monto": monto_usd,
                    "fuente": fuente,
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
                               horas, prueba_final, diferencias, creado_en
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

    # ------------------------------------------------------------------ cola

    def encolar_outbox(
        self,
        tipo: str,
        payload: dict[str, Any],
        topico: str = "sistema/tablet",
    ) -> str:
        """Encola un mensaje para publicar cuando vuelva el enlace.

        La idempotencia la da el id del mensaje: si el mismo aviso, evento u
        orden se encola dos veces **mientras está pendiente**, no queda
        duplicado en la cola. Un mensaje que ya salió puede volver a encolarse
        (si el bus se cae otra vez): lo que importa es que no haya dos copias
        del mismo mensaje esperando a la vez.

        Además queda escrito en ``idempotency_key``, que es el registro
        auditable de qué se encoló.
        """
        with self._engine.begin() as conexion:
            id_outbox = conexion.execute(
                text(
                    """
                    INSERT INTO outbox_cola (tipo, payload, topico, estado)
                    VALUES (:tipo, CAST(:payload AS jsonb), :topico, 'pendiente')
                    ON CONFLICT DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "tipo": tipo,
                    "payload": json.dumps(payload, ensure_ascii=False),
                    "topico": topico,
                },
            ).scalar_one_or_none()
            if id_outbox is None:
                # El mensaje ya estaba en la cola: se devuelve su fila, no se
                # encola otra vez. Sólo se buscan los pendientes: una fila
                # ya enviada es historia, no una copia de este mensaje.
                id_outbox = conexion.execute(
                    text(
                        """
                        SELECT id FROM outbox_cola
                        WHERE tipo = :tipo
                          AND payload ->> 'id' = :mensaje_id
                          AND estado = 'pendiente'
                        LIMIT 1
                        """
                    ),
                    {"tipo": tipo, "mensaje_id": payload.get("id", "")},
                ).scalar_one()
            else:
                # El registro de idempotencia deja escrito qué mensaje se
                # encoló y en qué fila: así no se encola dos veces y se puede
                # auditar qué salió de la planta.
                conexion.execute(
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

    def pendientes_outbox(
        self, limite: int = 50
    ) -> list[tuple[str, str, str, dict[str, Any]]]:
        """Mensajes en cola, en el orden en que se encolaron.

        Cada fila trae ``(id, tipo, topico, payload)``: el tipo dice qué
        contrato hay que validar y el tópico, por dónde sale.
        """
        with self._engine.connect() as conexion:
            filas = (
                conexion.execute(
                    text(
                        """
                        SELECT id, tipo, topico, payload
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
        return [
            (str(fila["id"]), str(fila["tipo"]), str(fila["topico"]), dict(fila["payload"]))
            for fila in filas
        ]

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
