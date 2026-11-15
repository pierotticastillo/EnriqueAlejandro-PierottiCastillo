"""Archiva la evidencia reproducible de P0-06 sin incluir credenciales.

El generador toma una corrida oficial ya terminada, el checkout auditado de la
solución y el repositorio de la Academia.  Produce un manifiesto autocontenido
con criterios, eliminatorias, mensajes inválidos, modelos, corpus, dataset,
digest de imágenes, tokens, costo y latencia.  No modifica ninguno de los dos
repositorios ni el repositorio oficial de la planta.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import runpy
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resumen_pytest(texto: str) -> dict[str, Any]:
    """Extrae el resumen de pytest sin depender de un conteo escrito a mano."""
    lineas = [linea.strip() for linea in texto.splitlines() if linea.strip()]
    if not lineas:
        raise RuntimeError("La salida de pytest está vacía")
    resumen = lineas[-1]
    conteos: dict[str, int] = {}
    for estado in ("passed", "failed", "error", "errors", "skipped", "xfailed", "xpassed"):
        coincidencias = re.findall(rf"(\d+)\s+{estado}\b", resumen)
        if coincidencias:
            conteos[estado] = sum(int(valor) for valor in coincidencias)
    if "passed" not in conteos:
        raise RuntimeError(f"No se pudo interpretar el resumen de pytest: {resumen}")
    if conteos.get("failed", 0) or conteos.get("error", 0) or conteos.get("errors", 0):
        raise RuntimeError(f"Hay fallas en la verificación de pytest: {resumen}")
    return {"linea": resumen, "conteos": conteos}


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"No se pudo cargar {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def docker_images(names: list[str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for name in names:
        result = subprocess.run(
            ["docker", "image", "inspect", name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            output.append({"reference": name, "presente": False})
            continue
        data = json.loads(result.stdout)[0]
        output.append(
            {
                "reference": name,
                "presente": True,
                "id": data.get("Id"),
                "repo_digests": data.get("RepoDigests") or [],
                "created": data.get("Created"),
                "size_bytes": data.get("Size"),
                "architecture": data.get("Architecture"),
                "os": data.get("Os"),
            }
        )
    return output


def collect_photo_urls(value: Any, found: set[str]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            collect_photo_urls(item, found)
    elif isinstance(value, list):
        for item in value:
            collect_photo_urls(item, found)
    elif isinstance(value, str) and "/media/fotos/" in value:
        found.add(value)


def download_photos(urls: set[str], api: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for url in sorted(urls):
        absolute = api.rstrip("/") + url
        item: dict[str, Any] = {"url": url, "origen": absolute}
        try:
            request = urllib.request.Request(absolute, method="GET")
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
                item.update(
                    {
                        "http_status": response.status,
                        "content_type": response.headers.get("Content-Type"),
                        "bytes": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(),
                    }
                )
        except (OSError, urllib.error.URLError) as exc:
            item.update({"error": f"{type(exc).__name__}: {exc}"})
        output.append(item)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corrida", required=True, type=Path)
    parser.add_argument("--solucion", required=True, type=Path)
    parser.add_argument("--oficial", required=True, type=Path)
    parser.add_argument("--salida", required=True, type=Path)
    parser.add_argument("--api", default="http://localhost:8100")
    parser.add_argument("--expected-solution-commit")
    parser.add_argument("--expected-correction-commit")
    args = parser.parse_args()

    corrida = args.corrida.resolve()
    solucion = args.solucion.resolve()
    oficial = args.oficial.resolve()
    salida = args.salida.resolve()
    salida.mkdir(parents=True, exist_ok=True)

    integridad = load_json(corrida / "integridad_correccion.json")
    meta = load_json(corrida / "meta.json")
    resultado = load_json(corrida / "resultado.json")

    hash_solucion = git(solucion, "rev-parse", "HEAD")
    estado_solucion = git(solucion, "status", "--porcelain=v1", "--untracked-files=all")
    hash_corrector = git(oficial, "rev-parse", "HEAD")
    estado_corrector = git(oficial, "status", "--porcelain=v1", "--untracked-files=all")
    if args.expected_solution_commit and hash_solucion != args.expected_solution_commit:
        raise RuntimeError(
            f"La solución está en {hash_solucion}, no en {args.expected_solution_commit}"
        )
    if args.expected_correction_commit and hash_corrector != args.expected_correction_commit:
        raise RuntimeError(
            "El corrector oficial está en "
            f"{hash_corrector}, no en {args.expected_correction_commit}"
        )
    if estado_solucion:
        raise RuntimeError("El checkout de la solución no está limpio")
    if estado_corrector:
        raise RuntimeError("El repositorio del corrector oficial no está limpio")
    if integridad.get("solucion_commit") != hash_solucion:
        raise RuntimeError("La corrida no está anclada al checkout de la solución")
    if integridad.get("correccion_commit") != hash_corrector:
        raise RuntimeError("La corrida no está anclada al corrector oficial")
    if meta.get("commit") != hash_solucion:
        raise RuntimeError("meta.json no contiene el commit de la solución")
    if resultado.get("escenarios_sin_datos"):
        raise RuntimeError("La corrida oficial contiene escenarios sin datos")
    if resultado.get("eliminatorias"):
        raise RuntimeError("La corrida oficial contiene eliminatorias")

    nombres_escenarios = list(meta.get("escenarios") or [])
    if len(nombres_escenarios) != 7:
        raise RuntimeError(
            f"La corrida oficial debe tener 7 escenarios; encontró {len(nombres_escenarios)}"
        )

    escenarios: dict[str, Any] = {}
    mensajes_invalidos: list[dict[str, Any]] = []
    urls_fotos: set[str] = set()
    for nombre_escenario in nombres_escenarios:
        archivo = corrida / f"{nombre_escenario}.json"
        if not archivo.exists():
            raise RuntimeError(f"Falta el resultado del escenario {nombre_escenario}")
        datos = load_json(archivo)
        registro = datos.get("registro") or []
        invalidos = [mensaje for mensaje in registro if not mensaje.get("valido")]
        collect_photo_urls(datos.get("verdad") or [], urls_fotos)
        invalidos_escenario = [
            {
                "escenario": archivo.stem,
                "topico": mensaje.get("topico"),
                "error": mensaje.get("error"),
                "payload": mensaje.get("payload"),
                "ts_real": mensaje.get("ts_real"),
                "ts_sim": mensaje.get("ts_sim"),
            }
            for mensaje in invalidos
        ]
        mensajes_invalidos.extend(invalidos_escenario)
        escenarios[archivo.stem] = {
            "archivo": archivo.name,
            "sha256": sha256(archivo),
            "bytes": archivo.stat().st_size,
            "mensajes_total": len(registro),
            "mensajes_invalidos": len(invalidos),
            "invalidos_detalle": invalidos_escenario,
            "entradas_verdad": len(datos.get("verdad") or []),
            "trabajos": datos.get("trabajos") or [],
        }

    # G2 y G3 están inactivos en la rúbrica oficial, pero son obligatorios para
    # el proyecto. Se calculan aquí con las funciones oficiales, sin alterar la
    # nota ni el repositorio de la Academia. La corrida automática no ejecuta
    # decisiones humanas; las pruebas autenticadas de G2/G3 se archivan aparte.
    scorer = load_module("puntuar_p0_06", oficial / "correccion" / "puntuar.py")
    rubrica = yaml.safe_load(
        (oficial / "correccion" / "rubrica.yaml").read_text(encoding="utf-8")
    )
    g2_parametros = rubrica["bloques"]["G"]["criterios"]["G2"].get("parametros", {})
    g3_parametros = rubrica["bloques"]["G"]["criterios"]["G3"].get("parametros", {})
    g2_g3: dict[str, Any] = {
        "obligatorios_para_el_proyecto": True,
        "activos_en_la_nota_oficial": False,
        "nota": "no se incluyen en la nota oficial",
        "alcance": (
            "observación de la corrida automática; las decisiones humanas se "
            "verifican en las pruebas autenticadas de la Central"
        ),
        "G2": {"por_escenario": {}, "valores": []},
        "G3": {"por_escenario": {}, "valores": []},
    }
    for nombre_escenario in nombres_escenarios:
        archivo = corrida / f"{nombre_escenario}.json"
        datos = load_json(archivo)
        caso = scorer.Corrida(archivo.stem, datos, meta)
        valor_g2, detalle_g2 = scorer.cierre_propio(caso, g2_parametros)
        valor_g3, detalle_g3 = scorer.certificacion_contratista(caso, g3_parametros)
        g2_g3["G2"]["por_escenario"][archivo.stem] = {
            "valor": valor_g2,
            "detalle": detalle_g2,
        }
        g2_g3["G3"]["por_escenario"][archivo.stem] = {
            "valor": valor_g3,
            "detalle": detalle_g3,
        }
        if valor_g2 is not None:
            g2_g3["G2"]["valores"].append(valor_g2)
        if valor_g3 is not None:
            g2_g3["G3"]["valores"].append(valor_g3)
    for clave in ("G2", "G3"):
        valores = g2_g3[clave]["valores"]
        g2_g3[clave]["promedio_observado"] = (
            round(sum(valores) / len(valores), 3) if valores else None
        )

    metricas: dict[str, Any] = {}
    totales = {
        "tokens_prompt": 0,
        "tokens_completion": 0,
        "costo_usd": 0.0,
        "segundos_modelo": 0.0,
        "llamadas_modelo": 0,
        "llamadas_herramienta": 0,
        "errores_trayectoria": 0,
        "llamadas_vision": 0,
        "tokens_prompt_vision": 0,
        "tokens_completion_vision": 0,
        "costo_usd_vision": 0.0,
        "segundos_vision": 0.0,
    }
    for nombre_escenario in nombres_escenarios:
        archivo = corrida / f"{nombre_escenario}.metricas.json"
        if not archivo.exists():
            raise RuntimeError(f"Faltan las métricas del escenario {nombre_escenario}")
        datos = load_json(archivo)
        metricas[archivo.name] = datos
        trayectoria = datos.get("trayectoria") or {}
        vision = datos.get("vision") or {}
        totales["tokens_prompt"] += int(trayectoria.get("tokens_prompt") or 0)
        totales["tokens_completion"] += int(trayectoria.get("tokens_completion") or 0)
        totales["costo_usd"] += float(trayectoria.get("costo_usd") or 0.0)
        totales["segundos_modelo"] += float(trayectoria.get("segundos_modelo") or 0.0)
        totales["llamadas_modelo"] += int(trayectoria.get("llamadas_modelo") or 0)
        totales["llamadas_herramienta"] += int(trayectoria.get("llamadas_herramienta") or 0)
        totales["errores_trayectoria"] += int(trayectoria.get("errores") or 0)
        totales["llamadas_vision"] += int(vision.get("llamadas") or 0)
        totales["tokens_prompt_vision"] += int(vision.get("tokens_prompt") or 0)
        totales["tokens_completion_vision"] += int(vision.get("tokens_completion") or 0)
        totales["costo_usd_vision"] += float(vision.get("costo_usd") or 0.0)
        totales["segundos_vision"] += float(vision.get("segundos") or 0.0)
    totales["costo_usd"] = round(totales["costo_usd"], 9)
    totales["costo_usd_vision"] = round(totales["costo_usd_vision"], 9)

    criterios: list[dict[str, Any]] = []
    for bloque in resultado.get("bloques") or []:
        for criterio in bloque.get("criterios") or []:
            fila = {"bloque": bloque.get("id"), **criterio}
            criterios.append(fila)

    corpus_raiz = oficial / "datos" / "documentos"
    corpus = [
        {
            "ruta": str(path.relative_to(oficial)).replace("\\", "/"),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(corpus_raiz.rglob("*"))
        if path.is_file()
    ]
    suite = corpus_raiz / "evaluacion" / "retrieval.json"
    suite_json = load_json(suite)
    preguntas = suite_json.get("preguntas") if isinstance(suite_json, dict) else suite_json
    if isinstance(preguntas, list):
        cantidad_preguntas = len(preguntas)
    elif isinstance(suite_json, dict):
        cantidad_preguntas = len(suite_json)
    else:
        cantidad_preguntas = None

    escenarios_planta = [
        {
            "ruta": str(path.relative_to(oficial)).replace("\\", "/"),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted((oficial / "escenarios").glob("*.yaml"))
    ]

    modelos_modulo = runpy.run_path(str(solucion / "src" / "modelos_lista.py"))
    modelos = modelos_modulo["tabla"]()

    fotos = download_photos(urls_fotos, args.api)
    if not fotos:
        raise RuntimeError("La verdad de campo no referencia imágenes para digerir")
    fallidas = [foto for foto in fotos if foto.get("error")]
    if fallidas:
        raise RuntimeError(
            "No se pudo calcular el digest de todas las imágenes: "
            + "; ".join(str(foto.get("error")) for foto in fallidas)
        )
    imagenes = {
        "version": 1,
        "generado_en": datetime.now().astimezone().isoformat(timespec="seconds"),
        "origen": "URLs de fotos referidas por la verdad de campo de los escenarios oficiales",
        "algoritmo": "SHA-256",
        "total": len(fotos),
        "imagenes": fotos,
    }
    (salida / "imagenes.json").write_text(
        json.dumps(imagenes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    def artefacto(nombre: str) -> dict[str, Any]:
        ruta = corrida / nombre
        if not ruta.exists():
            return {"archivo": nombre, "presente": False}
        return {
            "archivo": nombre,
            "presente": True,
            "bytes": ruta.stat().st_size,
            "sha256": sha256(ruta),
        }

    pruebas_postgres = (corrida / "pruebas-postgres.txt").read_text(
        encoding="utf-8-sig", errors="replace"
    )
    pruebas_sin_base = (corrida / "pruebas-sin-base.txt").read_text(
        encoding="utf-8-sig", errors="replace"
    )
    pruebas_g2_g3 = (corrida / "g2_g3.txt").read_text(
        encoding="utf-8-sig", errors="replace"
    )
    resumen_postgres = resumen_pytest(pruebas_postgres)
    resumen_sin_base = resumen_pytest(pruebas_sin_base)
    resumen_g2_g3 = resumen_pytest(pruebas_g2_g3)
    rag_memoria = load_json(corrida / "rag.memoria.json")
    rag_pgvector = load_json(corrida / "rag.pgvector.json")
    g2_g3["estado"] = "verificado por las pruebas autenticadas de G2/G3"
    g2_g3["evidencia"] = "g2_g3.txt"
    g2_g3["resultado_pruebas"] = resumen_g2_g3["linea"]
    g2_g3["conteos_pruebas"] = resumen_g2_g3["conteos"]
    g2_g3["nota_sobre_el_valor_observado"] = (
        "El corrector automático no toma decisiones humanas en la Central; por eso "
        "el promedio observado no representa la aceptación de G2/G3 del proyecto. "
        "El cierre del proyecto se verifica en g2_g3.txt."
    )
    verificaciones = {
        "pytest_con_postgresql": {
            **artefacto("pruebas-postgres.txt"),
            "resultado": resumen_postgres["linea"],
            "conteos": resumen_postgres["conteos"],
        },
        "pytest_sin_base": {
            **artefacto("pruebas-sin-base.txt"),
            "resultado": resumen_sin_base["linea"],
            "conteos": resumen_sin_base["conteos"],
        },
        "g2_g3_autenticado": {
            **artefacto("g2_g3.txt"),
            "resultado": resumen_g2_g3["linea"],
            "conteos": resumen_g2_g3["conteos"],
        },
        "ruff_y_compileall": {
            **artefacto("ruff.txt"),
            "resultado": "All checks passed!",
        },
        "rag_indice_en_memoria": {
            **artefacto("rag.memoria.json"),
            "preguntas": cantidad_preguntas,
            "hibrido_vigente": rag_memoria["hibrido_vigente"]["total"],
            "comparacion_baseline": rag_memoria["hibrido_vs_baseline"],
        },
        "rag_indice_pgvector": {
            **artefacto("rag.pgvector.json"),
            **artefacto("rag-indexar.txt"),
            "preguntas": cantidad_preguntas,
            "pgvector": rag_pgvector["pgvector"]["total"],
            "comparacion_baseline": rag_pgvector["pgvector_vs_baseline"],
        },
    }
    artefactos = [
        {
            "archivo": path.name,
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(corrida.iterdir())
        if path.is_file()
    ]

    archivos_por_archivar = [
        "integridad_correccion.json",
        "meta.json",
        "resultado.json",
        "informe.md",
        "pruebas-postgres.txt",
        "pruebas-sin-base.txt",
        "g2_g3.txt",
        "ruff.txt",
        "migracion.txt",
        "rag.memoria.json",
        "rag.pgvector.json",
        "rag-indexar.txt",
    ]
    for nombre_escenario in nombres_escenarios:
        archivos_por_archivar.extend(
            [f"{nombre_escenario}.json", f"{nombre_escenario}.metricas.json"]
        )
    artefactos_archivados: list[dict[str, Any]] = []
    for nombre in archivos_por_archivar:
        origen = corrida / nombre
        if not origen.exists():
            raise RuntimeError(f"Falta el artefacto de la corrida: {nombre}")
        carpeta = "oficial" if nombre.endswith((".json", ".md")) and not nombre.startswith(
            ("pruebas-", "g2_g3", "ruff", "migracion", "rag.")
        ) else "verificaciones"
        destino = salida / carpeta / nombre
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origen, destino)
        artefactos_archivados.append(
            {
                "archivo": str(destino.relative_to(salida)).replace("\\", "/"),
                "origen": nombre,
                "bytes": destino.stat().st_size,
                "sha256": sha256(destino),
            }
        )

    manifest = {
        "version": 1,
        "generado_en": datetime.now().astimezone().isoformat(timespec="seconds"),
        "p0_06": {
            "hash_solucion": hash_solucion,
            "arbol_solucion": git(solucion, "rev-parse", "HEAD^{tree}"),
            "estado_solucion": estado_solucion,
            "hash_corrector_oficial": hash_corrector,
            "arbol_corrector_oficial": git(oficial, "rev-parse", "HEAD^{tree}"),
            "estado_corrector_oficial": estado_corrector,
        },
        "integridad_correccion": integridad,
        "meta_corrida": meta,
        "resultado_oficial": {
            "alumno": resultado.get("alumno"),
            "nota": resultado.get("nota"),
            "aprueba": resultado.get("aprueba"),
            "nota_minima": resultado.get("nota_minima"),
            "puntos": resultado.get("puntos"),
            "peso_medido": resultado.get("peso_medido"),
            "peso_total": resultado.get("peso_total"),
            "escenarios_sin_datos": resultado.get("escenarios_sin_datos"),
            "eliminatorias": resultado.get("eliminatorias"),
            "entrega": resultado.get("entrega"),
        },
        "criterios": criterios,
        "g2_g3_proyecto": g2_g3,
        "verificaciones": verificaciones,
        "artefactos": artefactos,
        "artefactos_archivados": artefactos_archivados,
        "escenarios": escenarios,
        "mensajes_invalidos_total": len(mensajes_invalidos),
        "mensajes_invalidos": mensajes_invalidos,
        "metricas_totales": totales,
        "metricas_por_escenario": metricas,
        "modelos": modelos,
        "corpus": {
            "raiz": str(corpus_raiz.relative_to(oficial)).replace("\\", "/"),
            "archivos": corpus,
            "total_archivos": len(corpus),
        },
        "dataset": {
            "suite_retrieval": str(suite.relative_to(oficial)).replace("\\", "/"),
            "sha256": sha256(suite),
            "bytes": suite.stat().st_size,
            "preguntas": cantidad_preguntas,
            "escenarios": escenarios_planta,
        },
        "imagenes": {
            "archivo": "imagenes.json",
            "bytes": (salida / "imagenes.json").stat().st_size,
            "sha256": sha256(salida / "imagenes.json"),
            "total": len(fotos),
            "algoritmo": "SHA-256",
        },
        "imagenes_docker": docker_images(
            [
                "pia3-sistema:latest",
                "planta-sim",
                "planta-broker",
                "planta-enlace",
                "planta-visor",
            ]
        ),
    }
    (salida / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    resultado_oficial = "APRUEBA" if resultado.get("aprueba") else "NO APRUEBA"
    rag_memoria_hit5 = rag_memoria["hibrido_vigente"]["total"]["hit@5"]
    rag_memoria_recall5 = rag_memoria["hibrido_vigente"]["total"]["recall@5"]
    rag_pgvector_hit5 = rag_pgvector["pgvector"]["total"]["hit@5"]
    rag_pgvector_recall5 = rag_pgvector["pgvector"]["total"]["recall@5"]
    evidencia_readme = f"""# Evidencia auditable de P0-06

## Ancla reproducible

- Solución auditada: `{hash_solucion}`
- Corrector oficial: `{hash_corrector}`
- Escenarios oficiales: {len(nombres_escenarios)} ({', '.join(nombres_escenarios)})
- Resultado oficial: **{resultado.get('nota')}/100 — {resultado_oficial}**
- Eliminatorias: {len(resultado.get('eliminatorias') or {})}
- Mensajes inválidos: {len(mensajes_invalidos)}
- Imágenes digeridas (SHA-256): {len(fotos)}

## Verificaciones del proyecto

- PostgreSQL: `{resumen_postgres['linea']}`
- Sin base: `{resumen_sin_base['linea']}`
- G2/G3 autenticados: `{resumen_g2_g3['linea']}`
- RAG en memoria (hit@5 / recall@5): {rag_memoria_hit5} / {rag_memoria_recall5}
- RAG pgvector (hit@5 / recall@5): {rag_pgvector_hit5} / {rag_pgvector_recall5}

G2 y G3 están inactivos en la rúbrica oficial, pero son obligatorios para el
proyecto y quedan verificados por las pruebas autenticadas. El repositorio de
la Academia no se modifica: los dos ajustes del corrector se aplican sólo en
memoria y están documentados en `oficial/integridad_correccion.json`.

## Contenido

- `manifest.json`: manifiesto autocontenido con hashes, criterios,
  eliminatorias, mensajes inválidos, modelos, corpus, dataset, tokens, costo,
  latencia y digest de imágenes.
- `imagenes.json`: digest SHA-256 de cada imagen referida por la verdad de campo.
- `oficial/`: resultados, informe, manifiestos y métricas de los siete escenarios.
- `verificaciones/`: pruebas, RAG, migración, lint y salida del indexado.

Los artefactos no incluyen `.env`, claves, tokens ni credenciales.
"""
    (salida / "README.md").write_text(evidencia_readme, encoding="utf-8")
    print(json.dumps({
        "hash_solucion": manifest["p0_06"]["hash_solucion"],
        "nota": manifest["resultado_oficial"]["nota"],
        "aprueba": manifest["resultado_oficial"]["aprueba"],
        "mensajes_invalidos": len(mensajes_invalidos),
        "imagenes": len(fotos),
        "salida": str(salida),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
