"""Verifica que el sistema use sólo modelos de la lista cerrada.

La restricción 04 del curso: «Modelos. Solo los de la lista cerrada que
publicamos. Incluye modelos de transcripción y de lenguaje que entran en la
PC de planta, y modelos en la nube de NVIDIA con visión. Los modelos locales
corren con la versión y la configuración que fijamos.»

Este script imprime la lista cerrada, compara contra lo que el sistema tiene
configurado y contra lo que está realmente disponible, y sale con código 1 si
algo está fuera de la lista.

Uso:

    python scripts/verificar_modelos.py            # con la configuración del entorno
    python scripts/verificar_modelos.py --tabla    # sólo la lista, sin comprobar nada

Dentro del contenedor:

    docker compose run --rm sistema python scripts/verificar_modelos.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import Settings  # noqa: E402
from modelos_lista import tabla  # noqa: E402
from verificacion_modelos import verificar  # noqa: E402


def _imprimir_lista() -> None:
    print("Lista cerrada de modelos del curso")
    print("=" * 100)
    print(f"{'clave':10s} {'modelo':42s} {'motor':28s} {'ubicación':10s} {'estado':13s}")
    print("-" * 100)
    for fila in tabla():
        print(
            f"{fila['clave']:10s} {fila['modelo']:42s} {fila['motor']:28s} "
            f"{fila['ubicacion']:10s} {fila['estado']:13s}"
        )
        print(f"{'':10s} configuración: {fila['configuracion']}")
        if fila["cuantizacion"] != "-":
            print(f"{'':10s} cuantización:  {fila['cuantizacion']}")
        if fila["dimension"] != "-":
            print(f"{'':10s} dimensión:     {fila['dimension']}")
        if fila["nota"]:
            print(f"{'':10s} nota:          {fila['nota']}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tabla",
        action="store_true",
        help="imprime la lista cerrada y sale, sin comprobar el entorno",
    )
    argumentos = parser.parse_args()

    _imprimir_lista()
    if argumentos.tabla:
        return 0

    settings = Settings()
    resultados = verificar(settings)

    print("Verificación del entorno")
    print("=" * 100)
    for resultado in resultados:
        marca = {
            "ok": "OK",
            "fuera-de-lista": "FUERA DE LISTA",
            "no-disponible": "NO DISPONIBLE",
            "deshabilitado": "deshabilitado",
            "advertencia": "advertencia",
        }.get(resultado.estado, resultado.estado)
        print(f"{resultado.clave:10s} {marca:16s} {resultado.detalle}")
    print()

    fuera = [r for r in resultados if r.estado == "fuera-de-lista"]
    if fuera:
        print("INCUMPLIMIENTO: hay modelos fuera de la lista cerrada.")
        return 1
    print("Todo lo que se está usando está en la lista cerrada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
