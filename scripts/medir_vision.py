"""Mide el modelo de nube con visión sobre las fotos de la planta.

Esto es lo que justifies la elección del modelo en el informe: cuánto tarda
cada llamada, qué responde y cuántos tokens consume. Usa **el mismo cliente
del sistema** (`analisis.vision.ClienteVision`), no un camino paralelo: lo que
se mide es lo que la planta hace.

Uso:

    # dentro del contenedor, con las fotos ya descargadas
    docker compose exec sistema python scripts/medir_vision.py --fotos /datos

    # desde el host, sobre un directorio de fotos
    python scripts/medir_vision.py --fotos /ruta/a/fotos --repetir 3

    # bajar las fotos de la planta y medirlas
    python scripts/medir_vision.py --descargar http://localhost:8100
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from analisis.vision import crear_cliente_vision  # noqa: E402
from config import Settings  # noqa: E402


def _fotos_de(directorio: Path) -> list[Path]:
    """Las fotos de un directorio, en orden."""
    extensiones = {".jpg", ".jpeg", ".png", ".webp"}
    return sorted(
        ruta
        for ruta in directorio.rglob("*")
        if ruta.is_file() and ruta.suffix.lower() in extensiones
    )


def _descargar(api: str, destino: Path) -> list[Path]:
    """Baja las fotos que publica la planta y las deja en ``destino``."""
    import httpx

    destino.mkdir(parents=True, exist_ok=True)
    bajadas: list[Path] = []
    with httpx.Client(timeout=30.0, trust_env=True) as cliente:
        verdad = cliente.get(f"{api}/api/verdad", timeout=30.0).json()
        for nota in verdad if isinstance(verdad, list) else []:
            for indice, url in enumerate(nota.get("fotos") or []):
                completa = url if url.startswith("http") else f"{api}{url}"
                nombre = destino / f"{nota.get('nota_id', 'nota')}_{indice:02d}.jpg"
                try:
                    respuesta = cliente.get(completa, timeout=30.0)
                    respuesta.raise_for_status()
                except httpx.HTTPError:
                    continue
                nombre.write_bytes(respuesta.content)
                bajadas.append(nombre)
    return bajadas


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fotos", type=Path, help="directorio con las fotos")
    parser.add_argument("--descargar", help="URL de la planta de la que bajar las fotos")
    parser.add_argument("--repetir", type=int, default=1, help="llamadas por foto")
    parser.add_argument("--salida", type=Path, help="archivo JSON con la medición")
    argumentos = parser.parse_args()

    settings = Settings()
    cliente = crear_cliente_vision(settings)
    if cliente is None:
        print(
            "El modelo de visión no está habilitado "
            "(falta la clave o el modelo no es de la lista)."
        )
        return 1

    fotos: list[Path] = []
    if argumentos.descargar:
        fotos = _descargar(argumentos.descargar, argumentos.fotos or Path("fotos"))
    elif argumentos.fotos:
        fotos = _fotos_de(argumentos.fotos)
    if not fotos:
        print("No hay fotos para medir.")
        return 1

    print(f"Modelo: {cliente.nombre}")
    print(f"max_tokens={settings.nvidia_max_tokens} · timeout={settings.nvidia_timeout_s:g} s")
    print(f"Fotos: {len(fotos)} · llamadas por foto: {argumentos.repetir}\n")

    mediciones: list[dict] = []
    for foto in fotos:
        for intento in range(argumentos.repetir):
            resultado = cliente.describir(foto)
            mediciones.append(
                {
                    "foto": str(foto),
                    "intento": intento + 1,
                    "estado": resultado.estado,
                    "danio_estructural": resultado.danio_estructural,
                    "descripcion": resultado.descripcion,
                    "detalle": resultado.detalle,
                    "segundos": round(resultado.segundos, 2),
                    "tokens_prompt": resultado.tokens_prompt,
                    "tokens_completion": resultado.tokens_completion,
                }
            )
            marca = "ok" if resultado.estado == "ok" else resultado.detalle
            print(
                f"{foto.name:28} #{intento + 1} {marca:12} "
                f"{resultado.segundos:5.2f} s · "
                f"{resultado.tokens_prompt:5d} tokens de entrada / "
                f"{resultado.tokens_completion:4d} de salida"
            )
            if resultado.descripcion:
                print(f"    → {resultado.descripcion}")

    tiempos = [m["segundos"] for m in mediciones if m["estado"] == "ok"]
    entradas = [m["tokens_prompt"] for m in mediciones if m["estado"] == "ok"]
    salidas = [m["tokens_completion"] for m in mediciones if m["estado"] == "ok"]
    print("\nResumen")
    print("-" * 70)
    if tiempos:
        print(f"llamadas ok: {len(tiempos)}/{len(mediciones)}")
        print(
            f"tiempo: mínimo {min(tiempos):.2f} s · mediana "
            f"{statistics.median(tiempos):.2f} s · máximo {max(tiempos):.2f} s"
        )
        print(
            f"tokens de entrada: {min(entradas)}-{max(entradas)} "
            f"(mediana {statistics.median(entradas):.0f})"
        )
        print(
            f"tokens de salida: {min(salidas)}-{max(salidas)} "
            f"(mediana {statistics.median(salidas):.0f})"
        )
    else:
        print("ninguna llamada terminó bien")
    con_danio = [m for m in mediciones if m["danio_estructural"] is True]
    print(f"respuestas con daño estructural: {len(con_danio)}/{len(mediciones)}")

    if argumentos.salida:
        import json

        argumentos.salida.write_text(
            json.dumps(
                {
                    "modelo": cliente.nombre,
                    "max_tokens": settings.nvidia_max_tokens,
                    "timeout_s": settings.nvidia_timeout_s,
                    "mediciones": mediciones,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"\nMedición guardada en {argumentos.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
