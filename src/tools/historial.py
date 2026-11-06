"""Tools de historial: sensores (``/api/historian``) y órdenes (``/api/historial-ordenes``)."""

from __future__ import annotations

from datetime import datetime

from tools.cliente import ClientePlanta
from tools.modelos import (
    ConsultaHistorialOrdenes,
    ConsultaHistorialSensores,
    HistorialSensores,
    MuestraSensor,
    OrdenHistorico,
)


def historial_sensores(
    cliente: ClientePlanta,
    consulta: ConsultaHistorialSensores,
) -> HistorialSensores:
    """Tool ``historial_sensores``: serie de un sensor con su resumen."""
    params: dict[str, str] = {}
    if consulta.desde:
        params["desde"] = consulta.desde.isoformat()
    if consulta.hasta:
        params["hasta"] = consulta.hasta.isoformat()
    datos = cliente.get(
        f"/api/historian/{consulta.tag}/{consulta.variable}", params=params or None
    )

    muestras: list[MuestraSensor] = []
    valores: list[float] = []
    for item in datos or []:
        if not isinstance(item, dict):
            continue
        ts = _fecha(item.get("ts"))
        if ts is None:
            continue
        valor = _numero(item.get("valor"))
        muestras.append(MuestraSensor(ts=ts, valor=valor))
        if valor is not None:
            valores.append(valor)

    muestras.sort(key=lambda muestra: muestra.ts)
    if len(muestras) > consulta.limite:
        muestras = muestras[-consulta.limite :]

    return HistorialSensores(
        tag=consulta.tag,
        variable=consulta.variable,
        unidad=None,
        cantidad=len(muestras),
        desde=muestras[0].ts if muestras else consulta.desde,
        hasta=muestras[-1].ts if muestras else consulta.hasta,
        minimo=min(valores) if valores else None,
        maximo=max(valores) if valores else None,
        promedio=round(sum(valores) / len(valores), 4) if valores else None,
        tendencia=_tendencia(valores),
        muestras=muestras,
    )


def historial_ordenes(
    cliente: ClientePlanta,
    consulta: ConsultaHistorialOrdenes,
) -> list[OrdenHistorico]:
    """Tool ``historial_ordenes``: órdenes de trabajo del archivo."""
    params: dict[str, str] = {}
    if consulta.equipo:
        params["equipo"] = consulta.equipo
    if consulta.desde:
        params["desde"] = consulta.desde.isoformat()
    if consulta.hasta:
        params["hasta"] = consulta.hasta.isoformat()
    datos = cliente.get("/api/historial-ordenes", params=params or None)

    salida: list[OrdenHistorico] = []
    for item in datos or []:
        if not isinstance(item, dict):
            continue
        salida.append(
            OrdenHistorico(
                orden_id=str(item.get("id") or item.get("orden_id") or ""),
                equipo=_texto(item.get("equipo")),
                fecha=_fecha(item.get("fecha") or item.get("fecha_aviso")),
                tareas=[str(t) for t in item.get("tareas") or []],
                ejecutor=_texto(item.get("ejecutor")),
                estado=_texto(item.get("estado")),
                repuestos=[
                    str(r.get("codigo"))
                    for r in item.get("repuestos") or []
                    if isinstance(r, dict) and r.get("codigo")
                ],
                texto=_texto(
                    item.get("trabajo_realizado") or item.get("aviso") or item.get("texto")
                ),
            )
        )
    salida.sort(key=lambda orden: orden.fecha or datetime.min, reverse=True)
    return salida[: consulta.limite]


def _tendencia(valores: list[float]) -> str | None:
    if len(valores) < 2:
        return None
    primero, ultimo = valores[0], valores[-1]
    rango = max(valores) - min(valores)
    margen = max(rango * 0.05, abs(primero) * 0.005, 0.01)
    if ultimo - primero > margen:
        return "sube"
    if primero - ultimo > margen:
        return "baja"
    return "estable"


def _fecha(valor: object) -> datetime | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        return datetime.fromisoformat(valor)
    except ValueError:
        return None


def _texto(valor: object) -> str | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def _numero(valor: object) -> float | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return float(valor)
