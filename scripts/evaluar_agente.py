"""Evalúa la batería ``agente_vs_baseline.json`` contra el agente real.

Conecta cada caso de la batería al núcleo de agentes (`src/agente/nucleo.py`)
a través de ``crear_agente`` y ``consultar_detallado``, compara la propuesta
del agente con el ``ground_truth`` del Enunciado y emite un reporte
reproducible con aciertos, costo y latencia por caso.

Uso (desde la raíz del repositorio):

    python scripts/evaluar_agente.py [--entrada docs/evaluaciones/baterias/agente_vs_baseline.json] [--salida docs/evaluaciones/reporte_agente_vs_baseline.json]

Requisitos:
- Docker Compose levantado (postgres, sistema, mcp).
- Ollama corriendo con `qwen2.5:3b-instruct` (modelo local de la lista cerrada).
- La API de la planta accesible (configurada en `Settings`).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

from agente.nucleo import crear_agente  # noqa: E402
from analisis.catalogo import CatalogoManuales  # noqa: E402
from analisis.llm import crear_modelo  # noqa: E402
from analisis.servicio import RecuperadorDiferido  # noqa: E402
from config import Settings  # noqa: E402
from tools.cliente import ClientePlanta as ClienteApi  # noqa: E402
from tools.manuales import CorpusManuales  # noqa: E402

BATERIA_DEFECTO = Path("docs/evaluaciones/baterias/agente_vs_baseline.json")
REPORTE_DEFECTO = Path("docs/evaluaciones/reporte_agente_vs_baseline.json")


def _cargar_bateria(ruta: Path) -> dict[str, Any]:
    """Carga el JSON de batería de evaluación."""
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[error] no se pudo leer la batería {ruta}: {exc}")
        sys.exit(1)


def _construir_agente(settings: Settings) -> Any:
    """Construye el agente usando el factory real del sistema."""
    cliente_api = ClienteApi(
        settings.planta_api_url,
        timeout_s=settings.planta_api_timeout_s,
    )
    try:
        catalogo = CatalogoManuales.desde_documentos([])
    except Exception:  # noqa: BLE001
        catalogo = CatalogoManuales.desde_documentos([])
    recuperador = RecuperadorDiferido()
    modelo = crear_modelo(settings)
    return crear_agente(
        settings,
        cliente=cliente_api,
        corpus=CorpusManuales(cliente_api),
        recuperador=recuperador,
        catalogo=catalogo,
    )


def _evaluar_caso(
    agente: Any,
    caso: dict[str, Any],
) -> dict[str, Any]:
    """Ejecuta un caso y compara la propuesta del agente con ground_truth."""
    caso_inicio = time.monotonic()
    case_id = caso.get("case_id")
    orden_id = caso.get("orden_id")
    entrada = list(caso.get("entrada_compartida", []))
    ground_truth = caso.get("ground_truth", {})

    falla_esperada = ground_truth.get("falla_catalogo")
    tarea_esperada = ground_truth.get("tarea_esperada")
    tareas_reales = list(ground_truth.get("tareas_reales", []))

    resultado: dict[str, Any] = {
        "case_id": case_id,
        "orden_id": orden_id,
        "falla_esperada": falla_esperada,
        "tarea_esperada": tarea_esperada,
        "tareas_reales": tareas_reales,
        "entrada_compartida": entrada,
        "segundos": 0.0,
    }

    if agente is None:
        segundos = round(time.monotonic() - caso_inicio, 3)
        resultado["segundos"] = segundos
        resultado["estado"] = "pendiente"
        resultado["motivo"] = "agente no disponible (Ollama no responde o llm_activo=False)"
        return resultado

    # Armar el prompt para el agente a partir de la entrada compartida
    prompt_partes: list[str] = []
    for fila in entrada:
        variable = fila.get("variable", "?")
        valor = fila.get("valor", "?")
        unidad = fila.get("unidad", "")
        direccion = fila.get("direccion", "")
        desc = fila.get("descripcion", "")
        diagnostica = fila.get("diagnostica", False)
        prompt_partes.append(
            f"- {variable}: {valor} {unidad} ({direccion}) — {desc}"
            + (" [diagnostica]" if diagnostica else "")
        )
    prompt = (
        "Analiza las siguientes mediciones de una parte de recorrida y propone "
        "la falla del catálogo, causas probables, tareas y citas correspondientes:\n"
        + "\n".join(prompt_partes)
        + "\n\nResponde con una propuesta estructurada: falla, causas, citas, tareas y justificación."
    )

    try:
        from analisis.aprobacion import CONTEXTO_CITAS_PERMITIDAS  # type: ignore
        permitidas = tuple(CONTEXTO_CITAS_PERMITIDAS)
    except Exception:
        permitidas = ()

    desde = time.monotonic()
    detalle = agente.consultar_detallado(
        prompt,
        permitidas=permitidas,
    )
    segundos = round(time.monotonic() - desde, 3)
    resultado["segundos"] = segundos

    estado = detalle.estado
    resultado["estado_agente"] = estado
    resultado["motivo_agente"] = detalle.motivo

    propuesta = detalle.propuesta
    if propuesta is not None:
        resultado["falla_obtenida"] = propuesta.falla
        resultado["causas_obtenidas"] = list(propuesta.causas)
        resultado["citas_obtenidas"] = list(propuesta.citas)
        resultado["tareas_obtenidas"] = list(propuesta.tareas)
        resultado["justificacion"] = propuesta.justificacion
        resultado["confianza"] = propuesta.confianza
        resultado["citas_descartadas"] = list(propuesta.citas_descartadas)
        resultado["causas_descartadas"] = list(propuesta.causas_descartadas)

    # Comparar contra ground_truth
    falla_correcta = (
        propuesta.falla == falla_esperada if propuesta is not None else falla_esperada is None
    )
    tarea_correcta = (
        list(propuesta.tareas)[0] == tarea_esperada
        if propuesta is not None and propuesta.tareas
        else tarea_esperada is None
    )
    tareas_coinciden = (
        set(list(propuesta.tareas)) == set(tareas_reales)
        if propuesta is not None
        else len(tareas_reales) == 0
    )
    # Las citas deben estar todas permitidas
    citas_validas = True
    if propuesta is not None:
        for cita in propuesta.citas:
            if cita not in permitidas and permitidas:
                citas_validas = False
                break

    resultado["falla_correcta"] = falla_correcta
    resultado["tarea_correcta"] = tarea_correcta
    resultado["tareas_coinciden"] = tareas_coinciden
    resultado["citas_validas"] = citas_validas

    aprobado = (
        estado == "aceptada"
        and falla_correcta
        and tarea_correcta
        and tareas_coinciden
        and citas_validas
    )
    resultado["estado"] = "aprobado" if aprobado else "fallido"
    resultado["motivo"] = (
        "ok"
        if aprobado
        else f"estado={estado}, falla_ok={falla_correcta}, tarea_ok={tarea_correcta}, tareas_ok={tareas_coinciden}, citas_ok={citas_validas}"
    )

    return resultado


def _generar_reporte(
    bateria: dict[str, Any],
    resultados: list[dict[str, Any]],
) -> dict[str, Any]:
    """Agrega los resultados por caso en un reporte final."""
    umbrales = bateria.get("umbrales", {})
    total = len(resultados)
    aprobados = sum(1 for r in resultados if r.get("estado") == "aprobado")
    fallados = sum(1 for r in resultados if r.get("estado") == "fallido")
    errores = sum(1 for r in resultados if r.get("estado") == "error")
    pendientes = sum(1 for r in resultados if r.get("estado") == "pendiente")

    segundos_totales = round(sum(r.get("segundos", 0) for r in resultados), 3)
    latencias = [r["segundos"] for r in resultados if isinstance(r.get("segundos"), (int, float))]
    latencia_p95 = _percentil(latencias, 95) if latencias else None

    return {
        "bateria": "agente_vs_baseline",
        "version": bateria.get("version", 1),
        "dimensiones": bateria.get("dimensiones", []),
        "umbrales": umbrales,
        "total_casos": total,
        "aprobados": aprobados,
        "fallados": fallados,
        "errores": errores,
        "pendientes": pendientes,
        "segundos_totales": segundos_totales,
        "latencia_p95_s": latencia_p95,
        "resultado_global": "aprobado"
        if aprobados == total and errores == 0 and pendientes == 0
        else "fallido",
        "resultados": resultados,
    }


def _percentil(valores: list[float], p: int) -> float | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    k = (len(ordenados) - 1) * (p / 100)
    f = int(k)
    c = min(f + 1, len(ordenados) - 1)
    if f == c:
        return round(ordenados[f], 3)
    d0 = ordenados[f] * (c - k)
    d1 = ordenados[c] * (k - f)
    return round(d0 + d1, 3)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evalúa agente vs baseline con la batería oficial."
    )
    parser.add_argument(
        "--entrada",
        default=str(BATERIA_DEFECTO),
        help="Ruta al JSON de batería de evaluación.",
    )
    parser.add_argument(
        "--salida",
        default=str(REPORTE_DEFECTO),
        help="Ruta donde guardar el reporte JSON.",
    )
    args = parser.parse_args()

    ruta_entrada = Path(args.entrada)
    ruta_salida = Path(args.salida)

    if not ruta_entrada.exists():
        print(f"[error] no existe la batería: {ruta_entrada}")
        sys.exit(1)

    bateria = _cargar_bateria(ruta_entrada)
    settings = Settings()
    agente = _construir_agente(settings)

    resultados: list[dict[str, Any]] = []
    for caso in bateria.get("casos", []):
        resultado = _evaluar_caso(agente, caso)
        resultados.append(resultado)
        print(
            f"  [{resultado['case_id']}] {resultado['estado']} - {resultado['motivo']}"
        )

    reporte = _generar_reporte(bateria, resultados)
    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    ruta_salida.write_text(
        json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\nReporte guardado en: {ruta_salida}")
    print(
        f"Resultado: {reporte['resultado_global']} | "
        f"{reporte['aprobados']}/{reporte['total_casos']} aprobados | "
        f"pendientes={reporte['pendientes']} | p95={reporte['latencia_p95_s']}s"
    )


if __name__ == "__main__":
    main()
