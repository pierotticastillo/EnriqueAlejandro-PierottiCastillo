"""Ejecuta el corrector oficial y archiva las métricas de PostgreSQL.

El repositorio oficial de la planta no se modifica.  Este envoltorio carga su
``puntuar.py`` y ``corregir.py`` como módulos y hace dos ajustes **en memoria**:

1. corrige el bug del scorer oficial, que suma un texto a un entero cuando una
   orden pendiente trae motivo (``TypeError: int + str``);
2. antes de que el corrector borre el volumen de PostgreSQL de cada escenario,
   consulta la trayectoria y los manifiestos y guarda tokens, costo y latencia.

Los dos ajustes quedan documentados en ``integridad_correccion.json`` dentro de
la carpeta de salida.  La lógica de la planta, los escenarios, la rúbrica y el
cálculo del puntaje no se modifican en disco.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import inspect
import json
import math
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text


def _sha256(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _cargar_modulo(nombre: str, ruta: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"No se pudo cargar {ruta}")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def _corregir_scorer_en_memoria(scorer: ModuleType) -> str:
    """Corrige ``bien += ok`` sin tocar el archivo oficial.

    ``ok`` puede ser el texto del motivo de una orden pendiente.  La expresión
    booleana del scorer devuelve ese texto cuando la orden está correctamente
    pendiente y trae motivo; al sumarlo a un entero se produce un ``TypeError``.
    """
    funcion = scorer.reglas_aprobacion
    fuente = inspect.getsource(funcion)
    corregida = fuente.replace("bien += ok", "bien += bool(ok)")
    if corregida == fuente:
        raise RuntimeError("El scorer oficial ya no contiene el patrón que se esperaba corregir")
    codigo = compile(corregida, f"{scorer.__file__}#reglas_aprobacion", "exec")
    exec(codigo, scorer.__dict__)  # noqa: S102 - carga controlada de la función oficial
    return "reglas_aprobacion: `bien += ok` -> `bien += bool(ok)`"


def _git(carpeta: Path, *args: str) -> str:
    resultado = subprocess.run(
        ["git", "-C", str(carpeta), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return resultado.stdout.strip() if resultado.returncode == 0 else ""


def _valor_env(ruta: Path, nombre: str, defecto: str) -> str:
    if not ruta.exists():
        return defecto
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if not linea or linea.lstrip().startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        if clave.strip() != nombre:
            continue
        return valor.strip().strip("'\"")
    return defecto


def _url_postgres(repo: Path, explicita: str | None) -> str:
    if explicita:
        return explicita
    env = repo / ".env"
    usuario = _valor_env(env, "POSTGRES_USER", "pia3")
    password = _valor_env(env, "POSTGRES_PASSWORD", "pia3")
    puerto = _valor_env(env, "POSTGRES_PORT", "55432")
    base = _valor_env(env, "POSTGRES_DB", "pia3")
    return (
        f"postgresql+psycopg://{quote_plus(usuario)}:{quote_plus(password)}"
        f"@127.0.0.1:{puerto}/{base}"
    )


def _percentil(valores: list[float], percentil: float) -> float | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    indice = max(0, math.ceil(percentil * len(ordenados)) - 1)
    return round(float(ordenados[indice]), 6)


def _capturar_metricas(database_url: str, destino: Path, escenario: str) -> None:
    """Guarda la evidencia de trayectoria y visión antes de borrar el volumen."""
    motor = create_engine(database_url, pool_pre_ping=True)
    try:
        with motor.connect() as conexion:
            version = conexion.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            trayectoria = [
                dict(fila)
                for fila in conexion.execute(
                    text(
                        """
                        SELECT id::text AS id, ts, etapa, tipo, modelo, herramienta,
                               tokens_prompt, tokens_completion, costo_usd, segundos,
                               error, orden_id, evento_id, paso
                        FROM registro_trayectoria
                        ORDER BY ts, paso, id
                        """
                    )
                ).mappings()
            ]
            manifiestos = [
                dict(fila)
                for fila in conexion.execute(
                    text(
                        """
                        SELECT id::text AS id, tipo, referencia_id, contenido,
                               hash_sha256, creado_en
                        FROM manifiesto_evidencia
                        WHERE tipo = 'orden'
                        ORDER BY creado_en, id
                        """
                    )
                ).mappings()
            ]
            ledger = [
                dict(fila)
                for fila in conexion.execute(
                    text(
                        """
                        SELECT tipo, count(*) AS entradas,
                               sum(tokens_prompt) AS tokens_prompt,
                               sum(tokens_completion) AS tokens_completion,
                               sum(costo_usd) AS costo_usd,
                               sum(segundos) AS segundos
                        FROM ledger_decisiones
                        GROUP BY tipo
                        ORDER BY tipo
                        """
                    )
                ).mappings()
            ]
            conteos = {
                tabla: conexion.execute(
                    text(f"SELECT count(*) FROM {tabla}")  # noqa: S608 - lista fija
                ).scalar_one()
                for tabla in (
                    "nota",
                    "evento_sistema",
                    "orden",
                    "cierre",
                    "decision_cierre",
                    "habilitacion",
                    "manifiesto_evidencia",
                    "ledger_decisiones",
                )
            }
    finally:
        motor.dispose()

    modelos = [fila for fila in trayectoria if fila["tipo"] == "modelo"]
    herramientas = [fila for fila in trayectoria if fila["tipo"] == "tool"]
    latencias = [float(fila["segundos"] or 0.0) for fila in modelos]
    grouped: dict[str, dict[str, Any]] = {}
    for fila in modelos:
        modelo = str(fila["modelo"] or "no_disponible")
        grupo = grouped.setdefault(
            modelo,
            {
                "llamadas": 0,
                "tokens_prompt": 0,
                "tokens_completion": 0,
                "costo_usd": 0.0,
                "segundos": 0.0,
                "errores": 0,
            },
        )
        grupo["llamadas"] += 1
        grupo["tokens_prompt"] += int(fila["tokens_prompt"] or 0)
        grupo["tokens_completion"] += int(fila["tokens_completion"] or 0)
        grupo["costo_usd"] += float(fila["costo_usd"] or 0.0)
        grupo["segundos"] += float(fila["segundos"] or 0.0)
        grupo["errores"] += int(fila["error"] is not None)

    vision: list[dict[str, Any]] = []
    for manifiesto in manifiestos:
        contenido = manifiesto.get("contenido") or {}
        fotos = (contenido.get("fotos") or {}).get("elementos") or []
        for foto in fotos:
            if not isinstance(foto, dict):
                continue
            salida = foto.get("vision") or {}
            if not isinstance(salida, dict):
                continue
            modelo = str(salida.get("modelo") or "no_disponible")
            if modelo == "no_disponible":
                continue
            vision.append(
                {
                    "manifiesto_id": manifiesto["id"],
                    "orden_id": manifiesto["referencia_id"],
                    "foto_id": foto.get("id"),
                    "modelo": modelo,
                    "estado": salida.get("estado"),
                    "tokens_prompt": int(salida.get("tokens_prompt") or 0),
                    "tokens_completion": int(salida.get("tokens_completion") or 0),
                    "costo_usd": 0.0,
                    "segundos": float(salida.get("segundos") or 0.0),
                }
            )

    metricas = {
        "version": 1,
        "escenario": escenario,
        "capturado_en": datetime.now().astimezone().isoformat(timespec="seconds"),
        "alembic_version": version,
        "conteos": conteos,
        "trayectoria": {
            "filas": len(trayectoria),
            "llamadas_modelo": len(modelos),
            "llamadas_herramienta": len(herramientas),
            "errores": sum(1 for fila in trayectoria if fila["error"] is not None),
            "tokens_prompt": sum(int(fila["tokens_prompt"] or 0) for fila in modelos),
            "tokens_completion": sum(int(fila["tokens_completion"] or 0) for fila in modelos),
            "costo_usd": round(sum(float(fila["costo_usd"] or 0.0) for fila in modelos), 9),
            "segundos_modelo": round(sum(latencias), 6),
            "latencia_p50_s": _percentil(latencias, 0.50),
            "latencia_p95_s": _percentil(latencias, 0.95),
            "latencia_max_s": round(max(latencias), 6) if latencias else None,
            "segundos_herramientas": round(
                sum(float(fila["segundos"] or 0.0) for fila in herramientas), 6
            ),
            "por_modelo": grouped,
        },
        "vision": {
            "llamadas": len(vision),
            "tokens_prompt": sum(fila["tokens_prompt"] for fila in vision),
            "tokens_completion": sum(fila["tokens_completion"] for fila in vision),
            "costo_usd": round(sum(fila["costo_usd"] for fila in vision), 9),
            "segundos": round(sum(fila["segundos"] for fila in vision), 6),
            "llamadas_por_modelo": vision,
        },
        "ledger_por_tipo": ledger,
        "alcance": {
            "modelo": "filas de registro_trayectoria con tipo=modelo",
            "vision": "manifiestos de orden y bloque fotos.elementos[].vision",
            "costo": (
                "valor registrado con la tarifa configurada; la visión no tiene tarifa configurada"
            ),
            "redaccion": (
                "la llamada de redacción no tiene registro de tokens propio; no se cuenta"
            ),
        },
    }
    destino.write_text(
        json.dumps(metricas, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _verificar_ancla(
    correccion: Path,
    repo: Path,
    database_url: str,
    *,
    expected_commit: str | None,
    expected_alembic: str | None,
    expected_correccion_commit: str | None,
    require_clean: bool,
) -> dict[str, str | bool]:
    """Verifica commit, árbol limpio y revisión antes de iniciar la corrida.

    Exigir el commit esperado activa también la exigencia de árbol limpio:
    una evidencia reproducible no puede salir de un checkout alterado.
    """
    commit = _git(repo, "rev-parse", "HEAD")
    if not commit:
        raise RuntimeError(f"El checkout de la solución no es un repositorio Git: {repo}")
    if expected_commit and commit != expected_commit:
        raise RuntimeError(f"El checkout de la solución está en {commit}, no en {expected_commit}")
    estado = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if (expected_commit or require_clean) and estado:
        raise RuntimeError(
            "El checkout de la solución tiene cambios sin confirmar; la corrida se detiene"
        )

    correccion_commit = ""
    correccion_estado = ""
    if expected_correccion_commit:
        correccion_commit = _git(correccion.parent, "rev-parse", "HEAD")
        if not correccion_commit:
            raise RuntimeError("No se pudo determinar el commit del repositorio de la Academia")
        if correccion_commit != expected_correccion_commit:
            raise RuntimeError(
                "El repositorio de la Academia está en "
                f"{correccion_commit}, no en {expected_correccion_commit}"
            )
        correccion_estado = _git(
            correccion.parent, "status", "--porcelain=v1", "--untracked-files=all"
        )
        if correccion_estado:
            raise RuntimeError("El repositorio de la Academia tiene cambios sin confirmar")

    motor = create_engine(database_url, pool_pre_ping=True)
    try:
        ultimo_error = None
        for intento in range(30):
            try:
                with motor.connect() as conexion:
                    version = conexion.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                break
            except Exception as exc:  # noqa: BLE001 - reintentamos hasta que PostgreSQL esté listo
                ultimo_error = exc
                time.sleep(2)
        else:
            raise RuntimeError(f"No se pudo conectar a PostgreSQL tras 30 intentos: {ultimo_error}")
    finally:
        motor.dispose()
    if expected_alembic and version != expected_alembic:
        raise RuntimeError(f"La base está en Alembic {version}, no en {expected_alembic}")
    return {
        "commit": commit,
        "arbol_limpio": not bool(estado),
        "alembic_version": str(version),
    }


def _integridad(
    correccion: Path,
    repo: Path,
    salida: Path,
    parche: str,
    verificacion: dict[str, str | bool],
) -> None:
    rubrica = correccion / "rubrica.yaml"
    suite = correccion.parent / "datos" / "documentos" / "evaluacion" / "retrieval.json"
    nota = {
        "version": 1,
        "generado_en": datetime.now().astimezone().isoformat(timespec="seconds"),
        "correccion_repo": str(correccion.parent),
        "correccion_commit": _git(correccion.parent, "rev-parse", "HEAD"),
        "correccion_status": _git(correccion.parent, "status", "--porcelain"),
        "solucion_repo": str(repo),
        "solucion_commit": _git(repo, "rev-parse", "HEAD"),
        "solucion_status": _git(repo, "status", "--porcelain"),
        "verificacion_ancla": verificacion,
        "archivos": {
            "corregir.py": _sha256(correccion / "corregir.py"),
            "puntuar.py": _sha256(correccion / "puntuar.py"),
            "rubrica.yaml": _sha256(rubrica),
            "retrieval.json": _sha256(suite),
        },
        "ajustes_en_memoria": [
            {
                "archivo": "puntuar.py",
                "funcion": "reglas_aprobacion",
                "cambio": parche,
                "motivo": "el valor ok puede ser el texto del motivo y provoca TypeError int + str",
            },
            {
                "archivo": "corregir.py",
                "funcion": "sh",
                "cambio": "captura métricas de PostgreSQL antes del down -v de cada escenario",
                "motivo": "el corrector oficial elimina el volumen al terminar cada escenario",
            },
        ],
        "modificaciones_en_disco": "ninguna",
    }
    (salida / "integridad_correccion.json").write_text(
        json.dumps(nota, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--correccion-dir", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--salida", required=True, type=Path)
    parser.add_argument("--alumno")
    parser.add_argument("--escenarios", nargs="*")
    parser.add_argument("--database-url")
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-alembic")
    parser.add_argument("--expected-correccion-commit")
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--api", default="http://localhost:8100")
    parser.add_argument("--rubrica", type=Path)
    parser.add_argument("--velocidad", type=float)
    parser.add_argument("--rapida", type=float)
    parser.add_argument("--gracia", type=int)
    parser.add_argument("--sin-construir", action="store_true")
    args = parser.parse_args()

    correccion = args.correccion_dir.resolve()
    repo = args.repo.resolve()
    salida = args.salida.resolve()
    database_url = _url_postgres(repo, args.database_url)
    verificacion = _verificar_ancla(
        correccion,
        repo,
        database_url,
        expected_commit=args.expected_commit,
        expected_alembic=args.expected_alembic,
        expected_correccion_commit=args.expected_correccion_commit,
        require_clean=args.require_clean,
    )
    salida.mkdir(parents=True, exist_ok=True)

    scorer = _cargar_modulo("puntuar", correccion / "puntuar.py")
    parche = _corregir_scorer_en_memoria(scorer)
    _integridad(correccion, repo, salida, parche, verificacion)

    corrector = _cargar_modulo("corregir_oficial", correccion / "corregir.py")
    original_sh = corrector.sh

    # El parámetro ``salida`` del corrector tiene que coincidir con el nombre
    # usado por ``sh``; Python no permite alias de argumentos en una función.
    def sh_con_captura(
        *args: str,
        env: dict[str, str] | None = None,
        check: bool = True,
        salida: Path | None = None,
    ) -> str:
        if "logs" in args and salida is not None:
            escenario = Path(salida).stem
            destino = salida.parent / f"{escenario}.metricas.json"
            try:
                _capturar_metricas(database_url, destino, escenario)
                print(f"  métricas de PostgreSQL guardadas en {destino}", flush=True)
            except Exception as exc:  # noqa: BLE001 - no se pierde la corrida oficial
                print(f"  no se pudieron capturar las métricas: {exc}", flush=True)
        return original_sh(*args, env=env, check=check, salida=salida)

    corrector.sh = sh_con_captura

    argumentos = [
        str(correccion / "corregir.py"),
        "--repo",
        str(repo),
        "--salida",
        str(salida),
        "--api",
        args.api,
    ]
    if args.alumno:
        argumentos.extend(["--alumno", args.alumno])
    if args.escenarios is not None:
        argumentos.append("--escenarios")
        argumentos.extend(args.escenarios)
    if args.rubrica:
        argumentos.extend(["--rubrica", str(args.rubrica.resolve())])
    if args.velocidad is not None:
        argumentos.extend(["--velocidad", str(args.velocidad)])
    if args.rapida is not None:
        argumentos.extend(["--rapida", str(args.rapida)])
    if args.gracia is not None:
        argumentos.extend(["--gracia", str(args.gracia)])
    if args.sin_construir:
        argumentos.append("--sin-construir")

    sys.argv = argumentos
    corrector.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
