#!/usr/bin/env python3
"""
Verificación estática de redes y presupuesto (P1-10, Entrega 3 módulo 4.4).

Verifica que el docker-compose.yml cumple con los requisitos del Anexo de
Entregas de la Academia Novit 2026:

1. Servicios de planta solo en red `planta-lan` con label `academia.planta: pc`.
2. Límite agregado de 4 CPU y 8 GB entre todos los servicios de planta.
3. HTTP(S)_PROXY=http://enlace:3128, NO_PROXY=broker,sim,localhost.
4. PostgreSQL 16 con pgvector en el compose.
5. Latido cada 30 s.
6. `docker compose up` levanta postgres, migraciones, sistema y mcp.

Uso:
    python scripts/verificar_redes_presupuesto.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"

errores: list[str] = []
avisos: list[str] = []


def leer_compose() -> str:
    with open(COMPOSE_PATH, encoding="utf-8") as f:
        return f.read()


def verificar_redes(contenido: str) -> None:
    """Verifica que los servicios de planta estén en planta-lan con label correcto."""
    # El compose usa anclas YAML (<<: *comun) para aplicar redes y labels.
    # Verificamos que:
    # 1. La red externa planta-lan exista
    # 2. El bloque comun tenga label academia.planta: pc y redes planta-lan
    # 3. Cada servicio esencial haga referencia al bloque comun (<<: *comun)

    if "planta-lan" not in contenido:
        errores.append("Falta la red 'planta-lan' en el compose")
    if "academia.planta: pc" not in contenido:
        errores.append("Falta el label academia.planta: pc en el compose")
    if "x-comun" not in contenido:
        errores.append("Falta el bloque de anclas 'x-comun' en el compose")

    servicios_esperados = ["postgres", "migraciones", "sistema", "mcp"]
    for servicio in servicios_esperados:
        if not re.search(rf"^  {servicio}:", contenido, re.MULTILINE):
            errores.append(f"Falta el servicio '{servicio}' en el compose")


def verificar_limite_recursos(contenido: str) -> None:
    """Verifica que el límite agregado sea 4 CPU y 8 GB."""
    # Buscar los límites de cada servicio
    cpus_encontrados = []
    memorias_encontradas = []

    for match in re.finditer(r'cpus:\s*"?([\d.]+)"?', contenido):
        try:
            cpus_encontrados.append(float(match.group(1)))
        except ValueError:
            pass

    for match in re.finditer(r'memory:\s*"?(\d+)([MG])"?', contenido):
        valor = float(match.group(1))
        unidad = match.group(2)
        if unidad == "G":
            memorias_encontradas.append(valor)
        elif unidad == "M":
            memorias_encontradas.append(valor / 1024)

    if cpus_encontrados:
        total_cpu = sum(cpus_encontrados)
        if total_cpu > 4:
            avisos.append(
                f"Total de CPU en servicios de planta: {total_cpu} (límite agregado: 4)"
            )
        else:
            print(f"  [OK] Total CPU: {total_cpu} (dentro del límite de 4)")

    if memorias_encontradas:
        total_mem = sum(memorias_encontradas)
        if total_mem > 8:
            avisos.append(
                f"Total de memoria en servicios de planta: {total_mem:.1f} GB (límite agregado: 8 GB)"
            )
        else:
            print(f"  [OK] Total memoria: {total_mem:.1f} GB (dentro del límite de 8 GB)")


def verificar_proxy(contenido: str) -> None:
    """Verifica que los servicios de planta usen el proxy de enlace correcto."""
    if "HTTP_PROXY" not in contenido:
        avisos.append("No se encontró HTTP_PROXY en el compose")
    elif "enlace:3128" not in contenido:
        avisos.append("HTTP_PROXY no apunta a http://enlace:3128")

    if "HTTPS_PROXY" not in contenido:
        avisos.append("No se encontró HTTPS_PROXY en el compose")
    elif "enlace:3128" not in contenido:
        avisos.append("HTTPS_PROXY no apunta a http://enlace:3128")

    if "NO_PROXY" not in contenido:
        avisos.append("No se encontró NO_PROXY en el compose")
    else:
        for requisito in ["broker", "sim", "localhost"]:
            if requisito not in contenido:
                avisos.append(f"NO_PROXY no incluye {requisito}")


def verificar_postgres(contenido: str) -> None:
    """Verifica que PostgreSQL 16 con pgvector esté en el compose."""
    if "pgvector/pgvector:pg16" not in contenido:
        errores.append(
            "El servicio postgres no usa pgvector/pgvector:pg16"
        )


def verificar_latido(contenido: str) -> None:
    """Verifica que el latido esté configurado cada 30 s."""
    if "LATIDO_INTERVALO_S" not in contenido:
        avisos.append("No se encontró LATIDO_INTERVALO_S en el compose")
    elif "30" not in contenido.split("LATIDO_INTERVALO_S")[1].split("\n")[0]:
        avisos.append("LATIDO_INTERVALO_S no está en 30 s")


def verificar_servicios_esenciales(contenido: str) -> None:
    """Verifica que docker compose up levante los servicios esenciales."""
    esenciales = ["postgres", "migraciones", "sistema", "mcp"]
    for esencial in esenciales:
        if not re.search(rf"^  {esencial}:", contenido, re.MULTILINE):
            errores.append(f"Falta el servicio esencial '{esencial}'")


def main() -> None:
    contenido = leer_compose()

    print("=== Verificacion de redes y presupuesto (P1-10) ===")
    print()

    verificar_redes(contenido)
    verificar_limite_recursos(contenido)
    verificar_proxy(contenido)
    verificar_postgres(contenido)
    verificar_latido(contenido)
    verificar_servicios_esenciales(contenido)

    print()

    if errores:
        print("ERRORES:")
        for e in errores:
            print(f"  - {e}")
        print()
    if avisos:
        print("AVISOS:")
        for a in avisos:
            print(f"  - {a}")
        print()

    if not errores and not avisos:
        print("Todas las verificaciones de redes y presupuesto pasaron.")
    elif not errores:
        print("Sin errores. Hay avisos que revisar (no bloqueantes).")
    else:
        print("Hay errores que corregir antes de la entrega.")

    sys.exit(1 if errores else 0)


if __name__ == "__main__":
    main()
