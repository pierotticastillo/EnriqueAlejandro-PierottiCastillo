"""Manifiestos de evidencia y provenance de las decisiones.

Un manifiesto es una fotografia de la evidencia **en el momento en que se
decidio**.  No se actualiza: si la orden o el cierre vuelven a procesarse, la
primera version sigue intacta y la repeticion no crea otra decision.

El formato distingue tres cosas:

* lo que existe (filas, fotos, tendencia, fragmentos);
* el detalle de cada elemento (URL, hash, salida de vision, modelo, ventana);
* lo que no existia, marcado de forma explicita con ``no_disponible``.

La ultima parte es deliberada: un dato ausente no se transforma en una lista
vacia ambigua ni en un valor inventado.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

NO_DISPONIBLE = "no_disponible"
VERSION_MANIFIESTO = 1


def hash_sha256_archivo(ruta: str | Path | None) -> str | None:
    """SHA-256 del archivo, o ``None`` si no se pudo leer."""
    if not ruta:
        return None
    try:
        digest = hashlib.sha256()
        with Path(ruta).open("rb") as archivo:
            for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
                digest.update(bloque)
        return digest.hexdigest()
    except OSError:
        return None


def identificador_foto(
    url: str | None,
    ruta_local: str | None = None,
    hash_sha256: str | None = None,
) -> str:
    """Identificador estable de una foto.

    Es un UUID5 derivado de la URL, la ruta local y el hash.  Si la foto se
    vuelve a procesar, conserva el mismo id; si cambia el contenido, cambia
    el hash y, con el, la identidad de esa version.
    """
    if url or hash_sha256:
        semilla = f"{url or ''}|{hash_sha256 or ''}"
    else:
        semilla = str(ruta_local or "")
    return str(uuid5(NAMESPACE_URL, semilla))


def manifiesto_de_analisis(analisis: Any) -> dict[str, Any]:
    """Construye el manifiesto inmutable de evidencia de una orden."""
    evidencia = analisis.evidencia
    fotos = _fotos_de_analisis(analisis)
    tendencias = [dato.as_dict() for dato in evidencia.tendencias]
    parte_id = analisis.parte_id or evidencia.parte_id
    filas = [fila.as_dict() for fila in evidencia.filas_confirmadas]
    fragmentos, estado_fragmentos = _fragmentos_de_analisis(analisis)

    return {
        "version": VERSION_MANIFIESTO,
        "evento_id": str(analisis.evento_id),
        "orden_id": str(analisis.borrador.id),
        "parte": {
            "estado": "disponible" if parte_id else NO_DISPONIBLE,
            "id": str(parte_id) if parte_id else NO_DISPONIBLE,
            "filas": filas,
        },
        "fotos": {
            "estado": "disponible" if fotos else NO_DISPONIBLE,
            "elementos": fotos,
        },
        "tendencia": {
            "estado": "disponible" if tendencias else NO_DISPONIBLE,
            "elementos": [_dato_o_no_disponible(tendencia) for tendencia in tendencias],
        },
        "fragmentos": {
            "estado": estado_fragmentos,
            "elementos": fragmentos,
        },
        "citas": list(analisis.citas),
    }


def manifiesto_desde_reglas(
    *,
    evento_id: str,
    orden_id: str,
    reglas: dict[str, Any] | None,
) -> dict[str, Any]:
    """Manifiesto mínimo para persistencias que llaman al repositorio directo.

    El camino productivo usa :func:`manifiesto_de_analisis`, que conserva el
    parte, el hash de las fotos y la ventana de tendencia.  Este fallback
    garantiza que ninguna orden quede sin manifiesto y marca con
    ``no_disponible`` lo que la llamada anterior no recibió.
    """
    datos = reglas or {}
    fotos = _fotos_desde_evidencia_vision(datos.get("evidencia_vision") or [])
    citas = [str(cita) for cita in (datos.get("citas") or []) if cita]
    fragmentos = [_fragmento_legado(cita) for cita in citas]
    return {
        "version": VERSION_MANIFIESTO,
        "evento_id": str(evento_id),
        "orden_id": str(orden_id),
        "parte": {
            "estado": NO_DISPONIBLE,
            "id": NO_DISPONIBLE,
            "filas": [],
        },
        "fotos": {
            "estado": "disponible" if fotos else NO_DISPONIBLE,
            "elementos": fotos,
        },
        "tendencia": {
            "estado": NO_DISPONIBLE,
            "elementos": [],
        },
        "fragmentos": {
            "estado": "parcial" if fragmentos else NO_DISPONIBLE,
            "elementos": fragmentos,
        },
        "citas": citas,
        "origen": "repositorio",
    }


def manifiesto_de_cierre(
    *,
    evento_id: str,
    orden_id: str,
    cierre_id: str,
    tecnico: str,
    empresa: str,
    transcripcion: str,
    tareas_hechas: list[str],
    repuestos_usados: list[dict[str, Any]],
    horas: float | None,
    prueba_final: str | None,
    diferencias: list[dict[str, Any]],
    fotos: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Manifiesto de la evidencia tecnica de un cierre."""
    fotos = fotos or []
    return {
        "version": VERSION_MANIFIESTO,
        "evento_id": str(evento_id),
        "orden_id": str(orden_id),
        "cierre_id": str(cierre_id),
        "evidencia_cierre": {
            "tecnico": str(tecnico),
            "empresa": str(empresa),
            "transcripcion": str(transcripcion),
            "tareas": list(tareas_hechas),
            "repuestos": list(repuestos_usados),
            "horas": {
                "estado": "disponible" if horas is not None else NO_DISPONIBLE,
                "valor": float(horas) if horas is not None else NO_DISPONIBLE,
            },
            "prueba_final": {
                "estado": "disponible" if prueba_final else NO_DISPONIBLE,
                "valor": str(prueba_final) if prueba_final else NO_DISPONIBLE,
            },
            "diferencias": list(diferencias),
            "fotos": {
                "estado": "disponible" if fotos else NO_DISPONIBLE,
                "elementos": fotos,
            },
        },
    }


def manifiesto_de_decision_cierre(
    *,
    evento_id: str,
    orden_id: str,
    cierre_id: str,
    decision: str,
    estado: str,
    actor: str,
    rol: str,
    motivo: str | None,
    evidencia_cierre: dict[str, Any],
    habilita: str | None = None,
    monto_usd: float | None = None,
) -> dict[str, Any]:
    """Manifiesto de la decision humana y de su certificacion."""
    return {
        "version": VERSION_MANIFIESTO,
        "evento_id": str(evento_id),
        "orden_id": str(orden_id),
        "cierre_id": str(cierre_id),
        "evidencia_cierre": dict(evidencia_cierre),
        "decision": {
            "decision": str(decision),
            "estado": str(estado),
            "actor": str(actor),
            "rol": str(rol),
            "motivo": str(motivo) if motivo else NO_DISPONIBLE,
        },
        "certificacion": {
            "estado": (
                "certificada"
                if decision == "certificar"
                else "observada"
                if decision == "observar"
                else NO_DISPONIBLE
            ),
            "habilita": {
                "estado": "disponible" if habilita else NO_DISPONIBLE,
                "numero": str(habilita) if habilita else NO_DISPONIBLE,
                "monto_usd": float(monto_usd) if monto_usd is not None else NO_DISPONIBLE,
            },
        },
    }


def _fotos_de_analisis(analisis: Any) -> list[dict[str, Any]]:
    evidencia = analisis.evidencia
    urls = list(evidencia.fotos)
    vision = list(analisis.evidencia_vision)
    cantidad = max(len(urls), len(vision))
    fotos: list[dict[str, Any]] = []
    for indice in range(cantidad):
        item = vision[indice] if indice < len(vision) else None
        url = (getattr(item, "foto_url", None) if item is not None else None) or (
            urls[indice] if indice < len(urls) else None
        )
        ruta_local = getattr(item, "ruta_local", None) if item is not None else None
        hash_foto = getattr(item, "hash_sha256", None) if item is not None else None
        fotos.append(
            {
                "id": (getattr(item, "foto_id", None) if item is not None else None)
                or identificador_foto(url, ruta_local, hash_foto),
                "url": str(url) if url else NO_DISPONIBLE,
                "ruta_local": str(ruta_local) if ruta_local else NO_DISPONIBLE,
                "hash_sha256": str(hash_foto) if hash_foto else NO_DISPONIBLE,
                "vision": _salida_vision(item),
            }
        )
    return fotos


def _fotos_desde_evidencia_vision(evidencias: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fotos: list[dict[str, Any]] = []
    for item in evidencias:
        if not isinstance(item, dict):
            continue
        url = item.get("foto_url") or None
        ruta_local = item.get("ruta_local") or None
        hash_foto = item.get("hash_sha256") or None
        fotos.append(
            {
                "id": item.get("foto_id") or identificador_foto(url, ruta_local, hash_foto),
                "url": str(url) if url else NO_DISPONIBLE,
                "ruta_local": str(ruta_local) if ruta_local else NO_DISPONIBLE,
                "hash_sha256": str(hash_foto) if hash_foto else NO_DISPONIBLE,
                "vision": {
                    "estado": item.get("estado") or NO_DISPONIBLE,
                    "danio_estructural": (
                        item.get("danio_estructural")
                        if item.get("danio_estructural") is not None
                        else NO_DISPONIBLE
                    ),
                    "descripcion": item.get("descripcion") or NO_DISPONIBLE,
                    "detalle": item.get("detalle") or NO_DISPONIBLE,
                    "modelo": item.get("modelo") or NO_DISPONIBLE,
                    "tokens_prompt": int(item.get("tokens_prompt") or 0),
                    "tokens_completion": int(item.get("tokens_completion") or 0),
                    "segundos": float(item.get("segundos") or 0.0),
                },
            }
        )
    return fotos


def _salida_vision(item: Any) -> dict[str, Any]:
    if item is None:
        return {
            "estado": NO_DISPONIBLE,
            "danio_estructural": NO_DISPONIBLE,
            "descripcion": NO_DISPONIBLE,
            "detalle": NO_DISPONIBLE,
            "modelo": NO_DISPONIBLE,
            "tokens_prompt": 0,
            "tokens_completion": 0,
            "segundos": 0.0,
        }
    return {
        "estado": str(getattr(item, "estado", NO_DISPONIBLE)),
        "danio_estructural": (
            getattr(item, "danio_estructural", None)
            if getattr(item, "danio_estructural", None) is not None
            else NO_DISPONIBLE
        ),
        "descripcion": getattr(item, "descripcion", None) or NO_DISPONIBLE,
        "detalle": getattr(item, "detalle", None) or NO_DISPONIBLE,
        "modelo": getattr(item, "modelo", None) or NO_DISPONIBLE,
        "tokens_prompt": int(getattr(item, "tokens_prompt", 0) or 0),
        "tokens_completion": int(getattr(item, "tokens_completion", 0) or 0),
        "segundos": float(getattr(item, "segundos", 0.0) or 0.0),
    }


def _fragmentos_de_analisis(analisis: Any) -> tuple[list[dict[str, Any]], str]:
    contexto = getattr(analisis, "contexto", None)
    fragmentos = list(getattr(contexto, "fragmentos", None) or [])
    if fragmentos:
        return [fragmento.model_dump(mode="json") for fragmento in fragmentos], "disponible"
    citas = [str(cita) for cita in (analisis.citas or []) if cita]
    if citas:
        return [_fragmento_legado(cita) for cita in citas], "parcial"
    return [], NO_DISPONIBLE


def _fragmento_legado(cita: str) -> dict[str, Any]:
    documento, _, seccion = str(cita).partition("§")
    return {
        "cita": str(cita),
        "documento": documento.strip() or NO_DISPONIBLE,
        "seccion": seccion.strip() or NO_DISPONIBLE,
        "texto": NO_DISPONIBLE,
        "titulo": NO_DISPONIBLE,
        "vigente": True,
    }


def _dato_o_no_disponible(datos: dict[str, Any]) -> dict[str, Any]:
    return {clave: valor if valor is not None else NO_DISPONIBLE for clave, valor in datos.items()}
