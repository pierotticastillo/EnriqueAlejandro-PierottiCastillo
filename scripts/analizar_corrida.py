"""Analiza una corrida de corrección: verdad de campo vs. eventos publicados.

    python scripts/analizar_corrida.py <carpeta de corrida> [escenario]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    carpeta = Path(sys.argv[1])
    escenarios = sys.argv[2:] or [
        p.stem for p in sorted(carpeta.glob("*.json")) if p.stem != "meta"
    ]
    for nombre in escenarios:
        datos = json.loads((carpeta / f"{nombre}.json").read_text(encoding="utf-8"))
        print(f"\n{'=' * 78}\nESCENARIO {nombre}\n{'=' * 78}")

        eventos = [
            r["payload"]
            for r in datos["registro"]
            if r["topico"] == "sistema/eventos" and r.get("valido")
        ]
        por_nota: dict[str, list[dict]] = {}
        for evento in eventos:
            for nota_id in evento.get("nota_ids", []):
                por_nota.setdefault(nota_id, []).append(evento)

        for nota in datos["verdad"]:
            nota_id = nota["nota_id"]
            if nota.get("seguridad"):
                continue
            print(f"\n--- {nota_id}  seg={nota.get('seguridad')}  ambig={nota.get('ambiguedades')}")
            print(f"    texto: {nota.get('texto')}")
            for item in nota.get("items", []):
                print(
                    f"      item tag={item.get('tag')} var={item.get('variable')} "
                    f"valor={item.get('valor')} {item.get('unidad')} "
                    f"obs={item.get('observacion')!r}"
                )
            publicados = por_nota.get(nota_id, [])
            print(f"    eventos publicados: {len(publicados)}")
            for evento in publicados:
                print(
                    f"      · {evento.get('equipo')} → {evento.get('tipo')} "
                    f"id={evento.get('id')} resumen={evento.get('resumen')!r}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
