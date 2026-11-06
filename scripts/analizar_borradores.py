"""Analiza los borradores de la corrida: qué corrigió el operador y por qué.

    python scripts/analizar_borradores.py <carpeta de corrida> [escenario]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    carpeta = Path(sys.argv[1])
    escenarios = sys.argv[2:] or [
        p.stem for p in sorted(carpeta.glob("*.json")) if p.stem not in {"meta", "resultado"}
    ]
    for nombre in escenarios:
        datos = json.loads((carpeta / f"{nombre}.json").read_text(encoding="utf-8"))
        print(f"\n{'#' * 70}\n{nombre}")
        notas = {
            n["nota_id"]: n
            for n in datos["verdad"]
            if "recorrida_id" in n and n.get("tipo") != "vivo"
        }
        for dialogo in datos["verdad"]:
            if dialogo.get("tipo") != "dialogo":
                continue
            if not dialogo.get("filas_mostradas"):
                continue
            nota_ids = dialogo.get("nota_ids") or []
            nota_id = nota_ids[0] if nota_ids else "?"
            nota = notas.get(nota_id, {})
            print(
                f"\n  {nota_id} filas={dialogo.get('filas_mostradas')} "
                f"confirmo={dialogo.get('confirmo')}"
            )
            print(f"    texto: {nota.get('texto')}")
            for item in nota.get("items", []):
                print(
                    f"      verdad: tag={item.get('tag')} var={item.get('variable')} "
                    f"valor={item.get('valor')} {item.get('unidad')}"
                )
            problemas = dialogo.get("problemas") or []
            for problema in problemas:
                print(f"      PROBLEMA: {json.dumps(problema, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
