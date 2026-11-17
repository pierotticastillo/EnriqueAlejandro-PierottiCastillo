"""Suite unificada de evaluación del Plan de Implementación 2.

Ejecuta todas las baterías de ``docs/evals/`` y emite un reporte reproducible
con aciertos, costo y latencia por dimensión y por caso.

Uso (desde la raíz del repositorio):

    python scripts/evaluar_todo.py [--eval nombre] [--salida reporte.json]

Si no se pasa ``--eval``, se ejecutan todas las baterías disponibles.
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

from agente.modelos import PropuestaAgente  # noqa: E402
from agente.nucleo import _cita_conocida, _normalizar  # noqa: E402

EVALS_DIR = Path("docs/evals")


def _cargar_evals(nombre: str | None = None) -> list[tuple[str, dict[str, Any]]]:
    """Carga los JSON de evaluación disponibles."""
    if not EVALS_DIR.is_dir():
        print(f"No existe el directorio de evals: {EVALS_DIR}")
        return []
    archivos = sorted(EVALS_DIR.glob("*.json"))
    if nombre:
        archivos = [p for p in archivos if p.stem == nombre]
    resultado: list[tuple[str, dict[str, Any]]] = []
    for ruta in archivos:
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
            resultado.append((ruta.stem, datos))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  [skip] {ruta.name}: no se puede leer ({exc})")
    return resultado


# ------------------------------------------------------------------ grounding

def _validar_grounding(
    citas_propuesta: list[str],
    citas_permitidas: list[str],
    citas_vistas_tool: list[str],
) -> tuple[bool, str | None]:
    """Valida las citas de una propuesta contra las permitidas y las vistas.

    Reutiliza la misma lógica de ``_validar_detallado`` de
    ``agente.nucleo``: una cita que no está ni en las permitidas ni entre las
    vistas por las herramientas descarta la propuesta entera.

    Devuelve ``(aceptada, motivo)``.
    """
    conocidas = {_normalizar(cita) for cita in citas_permitidas}
    conocidas |= {_normalizar(cita) for cita in citas_vistas_tool}
    descartadas: list[str] = []
    for cita in citas_propuesta:
        if not _cita_conocida(cita, conocidas):
            descartadas.append(cita)
    if descartadas:
        return False, "cita desconocida: " + ", ".join(descartadas)
    return True, None


def _ejecutar_grounding(datos: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta la batería de grounding usando el validador real del agente."""
    casos = datos.get("casos", [])
    umbrales = datos.get("umbrales", {})
    dimensiones = datos.get("dimensiones", [])

    inicio = time.monotonic()
    resultados: list[dict[str, Any]] = []
    aprobados = 0
    citas_inventadas = 0

    for caso in casos:
        caso_inicio = time.monotonic()
        case_id = caso.get("case_id")
        orden_id = caso.get("orden_id")
        esperado = caso.get("esperado", {})

        citas_propuesta = list(caso.get("citas_propuesta", []))
        citas_permitidas = list(caso.get("citas_permitidas", []))
        citas_vistas = list(caso.get("citas_vistas_tool", []))

        try:
            PropuestaAgente(
                falla=None,
                causas=[],
                citas=citas_propuesta,
                tareas=[],
                justificacion="validación de grounding",
                confianza="media",
            )
        except Exception as exc:  # noqa: BLE001
            resultados.append(
                {
                    "case_id": case_id,
                    "orden_id": orden_id,
                    "estado": "error",
                    "motivo": f"no se pudo construir la propuesta: {exc}",
                    "segundos": round(time.monotonic() - caso_inicio, 3),
                }
            )
            continue

        aceptada, motivo = _validar_grounding(citas_propuesta, citas_permitidas, citas_vistas)
        segundos = round(time.monotonic() - caso_inicio, 3)

        esperado_descartada = bool(esperado.get("descartada", False))
        estado = "aprobado" if (aceptada == (not esperado_descartada)) else "fallido"

        if estado == "aprobado":
            aprobados += 1
        if not aceptada:
            citas_inventadas += len(citas_propuesta)

        resultados.append(
            {
                "case_id": case_id,
                "orden_id": orden_id,
                "estado": estado,
                "motivo": motivo or "ok",
                "citas_propuesta": citas_propuesta,
                "citas_descartadas": (
                    [c for c in citas_propuesta if not _cita_conocida(
                        c, {_normalizar(x) for x in citas_permitidas}
                        | {_normalizar(x) for x in citas_vistas}
                    )]
                    if not aceptada
                    else []
                ),
                "segundos": segundos,
            }
        )

    total_segundos = round(time.monotonic() - inicio, 3)
    return {
        "bateria": "grounding",
        "version": datos.get("version", 1),
        "dimensiones": dimensiones,
        "umbrales": umbrales,
        "total_casos": len(casos),
        "aprobados": aprobados,
        "pendientes": len(casos) - aprobados,
        "citas_inventadas": citas_inventadas,
        "segundos_totales": total_segundos,
        "resultados": resultados,
    }


def _ejecutar_bateria_estructural(nombre: str, datos: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta una batería sin implementación real (validación estructural)."""
    casos = datos.get("casos", [])
    umbrales = datos.get("umbrales", {})
    dimensiones = datos.get("dimensiones", [])

    inicio = time.monotonic()
    resultados: list[dict[str, Any]] = []
    aprobados = 0

    for caso in casos:
        caso_inicio = time.monotonic()
        estado = "pendiente"
        motivo = "implementación pendiente de la fase correspondiente"
        resultados.append(
            {
                "case_id": caso.get("case_id"),
                "orden_id": caso.get("orden_id"),
                "estado": estado,
                "motivo": motivo,
                "segundos": round(time.monotonic() - caso_inicio, 3),
            }
        )
        if estado == "aprobado":
            aprobados += 1

    total_segundos = round(time.monotonic() - inicio, 3)
    return {
        "bateria": nombre,
        "version": datos.get("version", 1),
        "dimensiones": dimensiones,
        "umbrales": umbrales,
        "total_casos": len(casos),
        "aprobados": aprobados,
        "pendientes": len(casos) - aprobados,
        "segundos_totales": total_segundos,
        "resultados": resultados,
    }


def _ejecutar_bateria(nombre: str, datos: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta una batería de evaluación y devuelve su reporte."""
    if nombre == "grounding":
        return _ejecutar_grounding(datos)
    return _ejecutar_bateria_estructural(nombre, datos)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ejecuta la suite de evaluación del Plan 2."
    )
    parser.add_argument(
        "--eval",
        help="Nombre de la batería a ejecutar (sin .json). Si se omite, todas.",
    )
    parser.add_argument(
        "--salida",
        default="reporte_evaluacion.json",
        help="Ruta del reporte JSON de salida.",
    )
    args = parser.parse_args()

    baterias = _cargar_evals(args.eval)
    if not baterias:
        print("No se encontraron baterías de evaluación.")
        return 1

    reporte: dict[str, Any] = {
        "generado_en": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "commit": _commit_actual(),
        "baterias": [],
        "resumen": {
            "total_baterias": len(baterias),
            "total_casos": 0,
            "aprobados": 0,
            "pendientes": 0,
        },
    }

    for nombre, datos in baterias:
        print(f"\n=== Batería: {nombre} ===")
        resultado = _ejecutar_bateria(nombre, datos)
        reporte["baterias"].append(resultado)
        reporte["resumen"]["total_casos"] += resultado["total_casos"]
        reporte["resumen"]["aprobados"] += resultado["aprobados"]
        reporte["resumen"]["pendientes"] += resultado["pendientes"]
        extra = ""
        if nombre == "grounding":
            extra = f" | citas inventadas: {resultado.get('citas_inventadas', 0)}"
        print(
            f"  Casos: {resultado['total_casos']} | "
            f"Aprobados: {resultado['aprobados']} | "
            f"Pendientes: {resultado['pendientes']} | "
            f"Tiempo: {resultado['segundos_totales']} s{extra}"
        )

    salida = Path(args.salida)
    salida.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReporte escrito en: {salida}")
    return 0


def _commit_actual() -> str | None:
    """El commit HEAD del repositorio, si git está disponible."""
    import subprocess

    try:
        resultado = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return resultado.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
