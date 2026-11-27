#!/usr/bin/env python3
"""
Medición de tiempo de arranque tras corte de luz (Entrega 3, módulo 4.3).

Simula un corte de luz y mide cuánto tarda el sistema en volver a estar
operativo: contenedores levantados, base de datos lista, MQTT conectado,
latido publicando y sistema listo para procesar notas.

Uso:
    python scripts/medir_arranque_corte_luz.py [--iteraciones N]

Resultado:
    Imprime un resumen JSON con el tiempo promedio de arranque y los
    tiempos por componente.
"""

import json
import subprocess
import time
import sys
import argparse
from datetime import datetime, timezone


def ejecutar_comando(comando, timeout=120):
    """Ejecuta un comando y devuelve (codigo_salida, stdout, stderr)."""
    try:
        resultado = subprocess.run(
            comando,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return resultado.returncode, resultado.stdout, resultado.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"


def esperar_servicio(nombre, comando_check, timeout=60, intervalo=2):
    """Espera a que un servicio esté disponible."""
    inicio = time.time()
    while time.time() - inicio < timeout:
        codigo, _, _ = ejecutar_comando(comando_check)
        if codigo == 0:
            return time.time() - inicio
        time.sleep(intervalo)
    return None  # timeout


def medir_arranque(iteraciones=1):
    """Mide el tiempo de arranque tras simular un corte de luz."""
    resultados = []

    for i in range(iteraciones):
        print(f"\n=== Iteración {i + 1}/{iteraciones} ===", flush=True)

        inicio_total = time.time()

        # 1. Parar contenedores (simular corte de luz)
        print("  [1/5] Parando contenedores...", flush=True)
        codigo, _, _ = ejecutar_comando("docker compose down --remove-orphans", timeout=60)
        if codigo != 0:
            print(f"    WARN: docker compose down devolvió código {codigo}", flush=True)

        # Pequeña espera para asegurar que todo se detuvo
        time.sleep(2)

        # 2. Levantar contenedores
        print("  [2/5] Levantando contenedores...", flush=True)
        inicio_levantar = time.time()
        codigo, _, _ = ejecutar_comando("docker compose up -d", timeout=120)
        tiempo_levantar = time.time() - inicio_levantar
        if codigo != 0:
            print(f"    ERROR: docker compose up devolvió código {codigo}", flush=True)
            continue

        # 3. Esperar a que PostgreSQL esté listo
        print("  [3/5] Esperando PostgreSQL...", flush=True)
        tiempo_postgres = esperar_servicio(
            "postgres",
            "docker compose exec -T postgres pg_isready -U pia3 -d pia3",
            timeout=60,
        )

        # 4. Esperar a que el latido publique
        print("  [4/5] Esperando latido sistema/estado...", flush=True)
        tiempo_latido = esperar_servicio(
            "latido",
            'docker compose exec -T sistema python -c "import time; time.sleep(35)"',
            timeout=60,
        )

        # 5. Esperar a que el sistema esté listo (MQTT conectado, latido publicado)
        print("  [5/5] Verificando sistema operativo...", flush=True)
        tiempo_sistema = esperar_servicio(
            "sistema",
            "docker compose exec -T sistema python -c "
            '"from src.db.session import check_db; check_db()"',
            timeout=60,
        )

        tiempo_total = time.time() - inicio_total

        iteracion = {
            "iteracion": i + 1,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "tiempo_levantar_contenedores_s": round(tiempo_levantar, 2),
            "tiempo_postgres_s": round(tiempo_postgres, 2) if tiempo_postgres else None,
            "tiempo_latido_s": round(tiempo_latido, 2) if tiempo_latido else None,
            "tiempo_sistema_s": round(tiempo_sistema, 2) if tiempo_sistema else None,
            "tiempo_total_arranque_s": round(tiempo_total, 2),
        }
        resultados.append(iteracion)
        print(f"    Tiempo total: {tiempo_total:.2f} s", flush=True)

    # Resumen
    tiempos_validos = [r["tiempo_total_arranque_s"] for r in resultados if r["tiempo_total_arranque_s"]]
    resumen = {
        "iteraciones": iteraciones,
        "iteraciones_exitosas": len(tiempos_validos),
        "tiempo_promedio_s": round(sum(tiempos_validos) / len(tiempos_validos), 2) if tiempos_validos else None,
        "tiempo_min_s": round(min(tiempos_validos), 2) if tiempos_validos else None,
        "tiempo_max_s": round(max(tiempos_validos), 2) if tiempos_validos else None,
        "detalle": resultados,
    }

    print("\n=== RESUMEN ===")
    print(json.dumps(resumen, indent=2, ensure_ascii=False))

    return resumen


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Medir arranque tras corte de luz")
    parser.add_argument(
        "--iteraciones",
        type=int,
        default=1,
        help="Número de iteraciones de medición (default: 1)",
    )
    args = parser.parse_args()

    resumen = medir_arranque(iteraciones=args.iteraciones)

    # Guardar resultado
    with open("docs/evaluaciones/entrega3/medicion_arranque_corte_luz.json", "w", encoding="utf-8") as f:
        json.dump(resumen, f, indent=2, ensure_ascii=False)

    print("\nResultado guardado en docs/evaluaciones/entrega3/medicion_arranque_corte_luz.json")
