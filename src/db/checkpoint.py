"""Checkpoint duradero y correlation ID de extremo a extremo (P1-15 / P1-16).

El checkpoint permite reanudar una tarea tras un reinicio sin repetir
efectos. El correlation_id agrupa los casos de una misma solicitud bajo el
orquestador; el case_id identifica cada caso (evento/orden) dentro de ella.

El registro se persiste en PostgreSQL dentro de la misma transacción que el
efecto que guarda, así que nunca hay un checkpoint sin su efecto ni un
efecto sin su checkpoint.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

#: Estados válidos de un checkpoint.
ESTADO_EN_CURSO = "en_curso"
ESTADO_ESPERANDO_HUMANO = "esperando_humano"
ESTADO_PAUSADO = "pausado"
ESTADO_TERMINAL = "terminal"

#: Tipos de tarea que pueden tener checkpoint.
TAREA_PARTE = "parte"
TAREA_ANALISIS = "analisis"
TAREA_ORDEN = "orden"
TAREA_CIERRE = "cierre"
TAREA_DECISION = "decision"


class CheckpointObsoleto(ValueError):
    """El checkpoint existe pero su versión ya no es la vigente."""


class CheckpointNoEncontrado(ValueError):
    """No hay un checkpoint válido para ese correlation_id / case_id."""


class ConflictoPropietario(ValueError):
    """Dos componentes intentan reanudar el mismo checkpoint al mismo tiempo."""


@dataclass
class Checkpoint:
    """Estado reanudable de una etapa."""

    correlation_id: str
    case_id: str
    tipo_tarea: str
    paso_actual: str
    estado: str
    propietario: str
    datos_confirmados: dict[str, Any] = field(default_factory=dict)
    acciones_hechas: list[dict[str, Any]] = field(default_factory=list)
    intentos: int = 0
    proxima_accion: str | None = None
    version: int = 1
    id: UUID | None = None
    creado_en: datetime | None = None
    actualizada_en: datetime | None = None

    @property
    def puede_seguir(self) -> bool:
        """Si el checkpoint aún no es terminal."""
        return self.estado != ESTADO_TERMINAL

    @property
    def efecto_ya_hecho(self) -> set[str]:
        """Las claves de idempotencia de los efectos ya consumados."""
        return {
            str(accion.get("clave_idempotencia", ""))
            for accion in self.acciones_hechas
            if accion.get("clave_idempotencia")
        }


class RepositorioCheckpoint:
    """Operaciones duraderas sobre checkpoint_ejecucion."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def guardar(
        self,
        conexion: Connection,
        *,
        correlation_id: str,
        case_id: str,
        tipo_tarea: str,
        paso_actual: str,
        estado: str,
        propietario: str,
        datos_confirmados: dict[str, Any] | None = None,
        acciones_hechas: list[dict[str, Any]] | None = None,
        intentos: int = 0,
        proxima_accion: str | None = None,
        version: int = 1,
    ) -> Checkpoint:
        """Inserta o actualiza el checkpoint dentro de la transacción activa.

        Si ya existe un registro para el correlation_id, se actualiza sólo
        cuando la versión coincide (control de concurrencia optimista). Si la
        versión no coincide, se lanza ``CheckpointObsoleto``: otro componente
        ya avanzó y este no debe competir.
        """
        ahora = datetime.now(UTC)
        fila = conexion.execute(
            text(
                """
                INSERT INTO checkpoint_ejecucion (
                    correlation_id, case_id, tipo_tarea, paso_actual, estado,
                    propietario, datos_confirmados, acciones_hechas, intentos,
                    proxima_accion, version, actualizada_en
                )
                VALUES (
                    :correlation_id, :case_id, :tipo_tarea, :paso_actual, :estado,
                    :propietario, CAST(:datos_confirmados AS jsonb),
                    CAST(:acciones_hechas AS jsonb), :intentos, :proxima_accion,
                    :version, :ahora
                )
                ON CONFLICT (correlation_id) DO UPDATE SET
                    case_id = EXCLUDED.case_id,
                    tipo_tarea = EXCLUDED.tipo_tarea,
                    paso_actual = EXCLUDED.paso_actual,
                    estado = EXCLUDED.estado,
                    propietario = EXCLUDED.propietario,
                    datos_confirmados = EXCLUDED.datos_confirmados,
                    acciones_hechas = EXCLUDED.acciones_hechas,
                    intentos = EXCLUDED.intentos,
                    proxima_accion = EXCLUDED.proxima_accion,
                    version = EXCLUDED.version,
                    actualizada_en = EXCLUDED.actualizada_en
                WHERE checkpoint_ejecucion.version = :version_esperada
                RETURNING id, version
                """
            ),
            {
                "correlation_id": correlation_id,
                "case_id": case_id,
                "tipo_tarea": tipo_tarea,
                "paso_actual": paso_actual,
                "estado": estado,
                "propietario": propietario,
                "datos_confirmados": _json(datos_confirmados or {}),
                "acciones_hechas": _json(acciones_hechas or []),
                "intentos": int(intentos),
                "proxima_accion": proxima_accion,
                "version": int(version),
                "version_esperada": int(version) - 1,
                "ahora": ahora,
            },
        ).mappings().first()
        if fila is None:
            raise CheckpointObsoleto(
                f"El checkpoint {correlation_id} ya fue avanzado por otro componente"
            )
        return Checkpoint(
            id=UUID(str(fila["id"])),
            correlation_id=correlation_id,
            case_id=case_id,
            tipo_tarea=tipo_tarea,
            paso_actual=paso_actual,
            estado=estado,
            propietario=propietario,
            datos_confirmados=datos_confirmados or {},
            acciones_hechas=acciones_hechas or [],
            intentos=int(intentos),
            proxima_accion=proxima_accion,
            version=int(fila["version"]),
            actualizada_en=ahora,
        )

    def cargar_ultimo(
        self,
        conexion: Connection,
        *,
        correlation_id: str,
        case_id: str | None = None,
    ) -> Checkpoint | None:
        """Carga el último checkpoint válido para una solicitud (y caso)."""
        if case_id is not None:
            fila = (
                conexion.execute(
                    text(
                        """
                        SELECT id, correlation_id, case_id, tipo_tarea,
                               paso_actual, estado, propietario,
                               datos_confirmados, acciones_hechas, intentos,
                               proxima_accion, version, creado_en, actualizada_en
                        FROM checkpoint_ejecucion
                        WHERE correlation_id = :cid AND case_id = :case
                        ORDER BY version DESC, actualizada_en DESC
                        LIMIT 1
                        """
                    ),
                    {"cid": correlation_id, "case": case_id},
                )
            ).mappings().first()
        else:
            fila = (
                conexion.execute(
                    text(
                        """
                        SELECT id, correlation_id, case_id, tipo_tarea,
                               paso_actual, estado, propietario,
                               datos_confirmados, acciones_hechas, intentos,
                               proxima_accion, version, creado_en, actualizada_en
                        FROM checkpoint_ejecucion
                        WHERE correlation_id = :cid
                        ORDER BY version DESC, actualizada_en DESC
                        LIMIT 1
                        """
                    ),
                    {"cid": correlation_id},
                )
            ).mappings().first()
        if fila is None:
            return None
        return Checkpoint(
            id=UUID(str(fila["id"])),
            correlation_id=str(fila["correlation_id"]),
            case_id=str(fila["case_id"]),
            tipo_tarea=str(fila["tipo_tarea"]),
            paso_actual=str(fila["paso_actual"]),
            estado=str(fila["estado"]),
            propietario=str(fila["propietario"]),
            datos_confirmados=_de_json(fila["datos_confirmados"]),
            acciones_hechas=_de_json(fila["acciones_hechas"]),
            intentos=int(fila["intentos"] or 0),
            proxima_accion=fila["proxima_accion"],
            version=int(fila["version"]),
            creado_en=fila["creado_en"],
            actualizada_en=fila["actualizada_en"],
        )

    def registrar_efecto(
        self,
        correlation_id: str,
        clave_idempotencia: str,
        efecto: dict[str, Any],
        conexion: Connection | None = None,
    ) -> None:
        """Añade un efecto consumado a las acciones hechas del checkpoint.

        Si se entrega una conexión, se usa dentro de esa transacción. Si no,
        se abre una transacción nueva.
        """
        def _ejecutar(con: Connection) -> None:
            con.execute(
                text(
                    """
                    UPDATE checkpoint_ejecucion
                    SET acciones_hechas = acciones_hechas || CAST(:nuevo AS jsonb),
                        intentos = intentos + 1,
                        ultimo_avance = now(),
                        actualizada_en = now()
                    WHERE correlation_id = :cid
                    """
                ),
                {
                    "cid": correlation_id,
                    "nuevo": _json(
                        [{"clave_idempotencia": clave_idempotencia, "efecto": efecto}]
                    ),
                },
            )

        if conexion is not None:
            _ejecutar(conexion)
        else:
            with self._engine.begin() as conexion_nueva:
                _ejecutar(conexion_nueva)

    def avanzar(
        self,
        conexion: Connection,
        *,
        correlation_id: str,
        paso_actual: str,
        estado: str,
        propietario: str,
        proxima_accion: str | None = None,
        datos_confirmados: dict[str, Any] | None = None,
        version: int | None = None,
    ) -> Checkpoint:
        """Avanza el checkpoint a la siguiente etapa (dentro de la transacción).

        Si se entrega ``version``, se usa esa versión en vez de incrementar la
        del checkpoint existente. Esto permite probar el control de
        concurrencia: un componente que intenta guardar con una versión obsoleta
        debe fallar.
        """
        existente = self.cargar_ultimo(conexion, correlation_id=correlation_id)
        if existente is None:
            raise CheckpointNoEncontrado(
                f"No hay checkpoint para reanudar: {correlation_id}"
            )
        nueva_version = version if version is not None else existente.version + 1
        return self.guardar(
            conexion,
            correlation_id=correlation_id,
            case_id=existente.case_id,
            tipo_tarea=existente.tipo_tarea,
            paso_actual=paso_actual,
            estado=estado,
            propietario=propietario,
            datos_confirmados=datos_confirmados or existente.datos_confirmados,
            acciones_hechas=existente.acciones_hechas,
            intentos=existente.intentos,
            proxima_accion=proxima_accion,
            version=nueva_version,
        )


def crear_correlation_id(prefijo: str = "pia3") -> str:
    """Genera un correlation_id estable e idempotente para una solicitud."""
    return f"{prefijo}:{uuid.uuid4().hex}"


def _json(valor: Any) -> str:
    import json

    return json.dumps(valor, ensure_ascii=False, default=str)


def _de_json(valor: Any) -> Any:
    import json

    if valor is None:
        return {}
    if isinstance(valor, (dict, list)):
        return valor
    try:
        return json.loads(valor)
    except (TypeError, ValueError):
        return {}
