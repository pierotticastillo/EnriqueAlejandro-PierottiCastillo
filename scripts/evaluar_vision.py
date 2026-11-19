"""Evalúa la batería ``vision.json`` contra el modelo de visión real.

Procesa cada foto de la batería con `crear_cliente_vision` (`src/analisis/vision.py`),
compara el resultado contra la etiqueta esperada (daño estructural, fail-safe)
y emite un reporte reproducible con precisión, recall, tasa de indeterminado.

Uso (desde la raíz del repositorio):

    python scripts/evaluar_vision.py \\
        --entrada docs/evaluaciones/baterias/vision.json \\
        --salida docs/evaluaciones/reporte_vision.json

Requisitos:
- Las imágenes referenciadas en el JSON deben existir bajo las rutas indicadas.
- `NVIDIA_API_KEY` configurada en `.env` (o variable de entorno) para que el
  cliente de visión esté habilitado.
- Salida de red por el proxy del enlace (`enlace:3128`) para llegar a NVIDIA.
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

from analisis.vision import ResultadoVision, crear_cliente_vision  # noqa: E402
from config import Settings  # noqa: E402

BATERIA_DEFECTO = Path("docs/evaluaciones/baterias/vision.json")
REPORTE_DEFECTO = Path("docs/evaluaciones/reporte_vision.json")


def _cargar_bateria(ruta: Path) -> dict[str, Any]:
    """Carga el JSON de batería de evaluación de visión."""
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[error] no se pudo leer la batería {ruta}: {exc}")
        sys.exit(1)


def _evaluar_caso(
    cliente: Any,
    caso: dict[str, Any],
) -> dict[str, Any]:
    """Ejecuta un caso de visión y compara contra la etiqueta esperada."""
    caso_inicio = time.monotonic()
    case_id = caso.get("case_id")
    orden_id = caso.get("orden_id")
    imagen_ruta = caso.get("imagen", "")
    etiqueta_esperada = caso.get("etiqueta_esperada", "")
    resultado_esperado = caso.get("resultado_esperado", "")
    fail_safe = caso.get("fail_safe", "")

    resultado: dict[str, Any] = {
        "case_id": case_id,
        "orden_id": orden_id,
        "imagen": imagen_ruta,
        "etiqueta_esperada": etiqueta_esperada,
        "resultado_esperado": resultado_esperado,
        "fail_safe": fail_safe,
        "segundos": 0.0,
    }

    if cliente is None or not getattr(cliente, "habilitado", False):
        segundos = round(time.monotonic() - caso_inicio, 3)
        resultado["segundos"] = segundos
        resultado["estado"] = "pendiente"
        resultado["motivo"] = "cliente de visión no disponible (falta NVIDIA_API_KEY o modelo)"
        return resultado

    ruta_imagen = Path(imagen_ruta)
    if not ruta_imagen.exists():
        segundos = round(time.monotonic() - caso_inicio, 3)
        resultado["segundos"] = segundos
        resultado["estado"] = "pendiente"
        resultado["motivo"] = f"imagen no encontrada: {imagen_ruta}"
        return resultado

    try:
        resultado_vision: ResultadoVision = cliente.describir(ruta_imagen)
    except Exception as exc:  # noqa: BLE001
        segundos = round(time.monotonic() - caso_inicio, 3)
        resultado["segundos"] = segundos
        resultado["estado"] = "error"
        resultado["motivo"] = f"error ejecutando visión: {exc}"
        return resultado

    segundos = round(time.monotonic() - caso_inicio, 3)
    resultado["segundos"] = segundos

    danio_esperado = etiqueta_esperada == "danio_estructural=true"
    danio_detectado = bool(resultado_vision.hay_danio)
    estado_es_indeterminado = resultado_vision.estado == "indeterminado"

    danio_coincide = danio_esperado == danio_detectado

    fail_safe_correcto = (
        not estado_es_indeterminado
        or resultado_esperado == "revision_humana"
        or "sin_danio_nunca" in (fail_safe or "")
    )

    resultado["danio_esperado"] = danio_esperado
    resultado["danio_detectado"] = danio_detectado
    resultado["estado_vision"] = resultado_vision.estado
    resultado["modelo"] = resultado_vision.modelo
    resultado["tokens_prompt"] = resultado_vision.tokens_prompt
    resultado["tokens_completion"] = resultado_vision.tokens_completion
    resultado["segundos_modelo"] = resultado_vision.segundos

    estado = "aprobado" if danio_coincide and fail_safe_correcto else "fallido"
    resultado["estado"] = estado

    motivos: list[str] = []
    if not danio_coincide:
        motivos.append(f"daño esperado={danio_esperado}, detectado={danio_detectado}")
    if not fail_safe_correcto:
        motivos.append("fail-safe incorrecto")
    resultado["motivo"] = "; ".join(motivos) if motivos else "ok"

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

    verdaderos_positivos = sum(
        1
        for r in resultados
        if r.get("danio_esperado") is True and r.get("danio_detectado") is True
    )
    falsos_positivos = sum(
        1
        for r in resultados
        if r.get("danio_esperado") is False and r.get("danio_detectado") is True
    )
    falsos_negativos = sum(
        1
        for r in resultados
        if r.get("danio_esperado") is True and r.get("danio_detectado") is False
    )

    precision = (
        round(verdaderos_positivos / (verdaderos_positivos + falsos_positivos), 3)
        if (verdaderos_positivos + falsos_positivos) > 0
        else None
    )
    recall = (
        round(verdaderos_positivos / (verdaderos_positivos + falsos_negativos), 3)
        if (verdaderos_positivos + falsos_negativos) > 0
        else None
    )
    tasa_indeterminado = round(
        sum(1 for r in resultados if r.get("estado_vision") == "indeterminado")
        / total,
        3,
    ) if total > 0 else None

    return {
        "bateria": "vision",
        "version": bateria.get("version", 1),
        "dimensiones": bateria.get("dimensiones", []),
        "umbrales": umbrales,
        "total_casos": total,
        "aprobados": aprobados,
        "fallados": fallados,
        "errores": errores,
        "pendientes": pendientes,
        "segundos_totales": segundos_totales,
        "precision_danio_estructural": precision,
        "recall_danio_estructural": recall,
        "tasa_indeterminado": tasa_indeterminado,
        "falsos_positivos": falsos_positivos,
        "falsos_negativos": falsos_negativos,
        "resultado_global": "aprobado"
        if aprobados == total and errores == 0 and pendientes == 0
        else "fallido",
        "resultados": resultados,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evalúa la batería de visión con el modelo real."
    )
    parser.add_argument(
        "--entrada",
        default=str(BATERIA_DEFECTO),
        help="Ruta al JSON de batería de evaluación de visión.",
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
    cliente = crear_cliente_vision(settings)

    resultados: list[dict[str, Any]] = []
    for caso in bateria.get("casos", []):
        resultado = _evaluar_caso(cliente, caso)
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
        f"precisión={reporte['precision_danio_estructural']} | "
        f"recall={reporte['recall_danio_estructural']} | "
        f"indeterminado={reporte['tasa_indeterminado']}"
    )


if __name__ == "__main__":
    main()
