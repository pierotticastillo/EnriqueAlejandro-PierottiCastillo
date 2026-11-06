"""Tool ``equipos``: equipos de la planta con nombres de campo y límites.

La fuente es ``GET /api/planta``: los límites de alarma salen de ahí, nunca de
un manual (MBP-4 §5: "los límites de alarma son los del sistema de control").
"""

from __future__ import annotations

from tools.cliente import ClientePlanta
from tools.modelos import ConsultaEquipos, Equipo, ManometroLocal, Sensor


def equipos(cliente: ClientePlanta, consulta: ConsultaEquipos) -> list[Equipo]:
    """Tool ``equipos``: catálogo de equipos, con sus sensores y límites."""
    datos = cliente.get("/api/planta") or {}
    sensores_por_equipo: dict[str, list[Sensor]] = {}
    for item in datos.get("sensores") or []:
        if not isinstance(item, dict) or not item.get("tag") or not item.get("variable"):
            continue
        sensor = Sensor(
            tag=str(item["tag"]),
            variable=str(item["variable"]),
            unidad=_texto(item.get("unidad")),
            alarma_baja=_numero(item.get("alarma_baja")),
            alarma_alta=_numero(item.get("alarma_alta")),
            disparo=_numero(item.get("disparo")),
        )
        sensores_por_equipo.setdefault(sensor.tag, []).append(sensor)

    salida: list[Equipo] = []
    for item in datos.get("equipos") or []:
        if not isinstance(item, dict) or not item.get("tag"):
            continue
        tag = str(item["tag"])
        if consulta.tag and tag != consulta.tag:
            continue
        tipo = _texto(item.get("tipo"))
        if consulta.tipo and (tipo or "").lower() != consulta.tipo.lower():
            continue
        manometro = item.get("manometro_local")
        salida.append(
            Equipo(
                tag=tag,
                tipo=tipo,
                descripcion=_texto(item.get("descripcion")),
                fabricante=_texto(item.get("fabricante")),
                modelo=_texto(item.get("modelo")),
                nombres=[str(n) for n in item.get("nombres") or []],
                caudal_nominal_m3d=_numero(item.get("caudal_nominal_m3d")),
                manometro_local=ManometroLocal(
                    unidad=_texto((manometro or {}).get("unidad"))
                )
                if isinstance(manometro, dict)
                else None,
                rol_inicial=_texto(item.get("rol_inicial")),
                sensores=sensores_por_equipo.get(tag, []),
            )
        )
    return salida


def _texto(valor: object) -> str | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def _numero(valor: object) -> float | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return float(valor)
