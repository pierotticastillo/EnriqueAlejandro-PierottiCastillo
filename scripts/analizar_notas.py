"""Compara el extractor con la verdad de campo de una corrida de corrección.

Lee las transcripciones reales del ASR de los logs, las pasa por el extractor
y las compara con los items de la verdad de campo.

    python scripts/analizar_notas.py <carpeta de corrida> [escenario]
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

from deteccion.partes import clasificar_partes  # noqa: E402
from planta.nombre_resolver import desde_api  # noqa: E402
from planta.parte_extractor import ExtractorParte  # noqa: E402

# Mismos parámetros que usa el corrector oficial (correccion/rubrica.yaml).
OBSERVACIONES_NORMALES = ("parada, en reserva", "parada por mantenimiento")
VARIABLES_CON_LIMITE = {
    "presion_descarga_local": "presion_descarga",
    "presion_diferencial": "presion_diferencial",
}

PLANTA = {
    "equipos": [
        {
            "tag": "B-101",
            "tipo": "bomba",
            "nombres": [
                "la uno",
                "la 1",
                "la de la punta",
                "la del portón",
                "bomba uno",
            ],
            "manometro_local": {"unidad": "kg/cm2"},
        },
        {
            "tag": "B-102",
            "tipo": "bomba",
            "nombres": ["la dos", "la 2", "la del medio", "bomba dos"],
            "manometro_local": {"unidad": "psi"},
        },
        {
            "tag": "B-103",
            "tipo": "bomba",
            "nombres": ["la tres", "la 3", "la del fondo", "la de reserva", "bomba tres"],
            "manometro_local": {"unidad": "bar"},
        },
        {
            "tag": "F-201A",
            "tipo": "filtro",
            "nombres": ["el filtro A", "el primario", "el de la izquierda", "el grueso"],
        },
        {
            "tag": "F-201B",
            "tipo": "filtro",
            "nombres": ["el filtro B", "el secundario", "el de la derecha", "el fino"],
        },
        {
            "tag": "TK-301",
            "tipo": "tanque",
            "nombres": ["el pulmón", "el tanque", "el tanque pulmón"],
        },
        {
            "tag": "FT-401",
            "tipo": "caudalimetro",
            "nombres": ["el caudalímetro", "el colector"],
        },
    ],
    "sensores": [
        {"tag": "B-101", "variable": "presion_succion", "unidad": "kg/cm2", "alarma_baja": 0.9},
        {
            "tag": "B-101",
            "variable": "presion_descarga",
            "unidad": "kg/cm2",
            "alarma_baja": 95,
            "alarma_alta": 125,
        },
        {
            "tag": "F-201A",
            "variable": "presion_diferencial",
            "unidad": "kg/cm2",
            "alarma_baja": 0.15,
            "alarma_alta": 1.5,
        },
        {
            "tag": "F-201B",
            "variable": "presion_diferencial",
            "unidad": "kg/cm2",
            "alarma_baja": 0.15,
            "alarma_alta": 1.5,
        },
        {"tag": "FT-401", "variable": "caudal", "unidad": "m3/d", "alarma_baja": 2200},
    ],
}

# Mismo criterio de comparación que el simulador de la planta.
A_KG = {"kg/cm2": 1.0, "psi": 1 / 14.2233, "bar": 1 / 0.980665}


def _cerca(a: float | None, b: float | None, ua: str | None, ub: str | None) -> bool:
    if a is None or b is None:
        return False
    if ua in A_KG and ub in A_KG:
        a, b = a * A_KG[ua], b * A_KG[ub]
        return abs(a - b) <= max(0.6, abs(b) * 0.02)
    return abs(a - b) <= max(0.051, abs(b) * 0.015)


def _esperados(nota: dict) -> dict[str, str]:
    """Tipo que el corrector espera para cada equipo de la nota."""
    limites = {
        (sensor["tag"], sensor["variable"]): sensor
        for sensor in PLANTA["sensores"]
    }
    salida: dict[str, bool] = {}
    for item in nota.get("items", []):
        anomalo = False
        observacion = item.get("observacion")
        if observacion is not None and observacion not in OBSERVACIONES_NORMALES:
            anomalo = True
        variable = VARIABLES_CON_LIMITE.get(item.get("variable"))
        if variable and item.get("valor") is not None:
            limite = limites.get((item.get("tag"), variable))
            if limite:
                valor = item["valor"] * A_KG.get(item.get("unidad") or "kg/cm2", 1.0)
                if (limite.get("alarma_alta") is not None and valor >= limite["alarma_alta"]) or (
                    limite.get("alarma_baja") is not None and valor <= limite["alarma_baja"]
                ):
                    anomalo = True
        salida[item["tag"]] = salida.get(item["tag"], False) or anomalo
    return {tag: ("Escalado" if anomalo else "SinNovedad") for tag, anomalo in salida.items()}


def _entradas(log: Path) -> list[str]:
    """Entradas del log, sin partir las líneas largas.

    Docker parte las líneas largas en varias: una entrada empieza con la
    marca de tiempo y las que siguen son su continuación. Sin juntarlas, la
    transcripción se lee truncada.
    """
    entradas: list[str] = []
    actual = ""
    for linea in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} ", linea):
            if actual:
                entradas.append(actual)
            actual = linea
        else:
            actual = f"{actual} {linea.strip()}"
    if actual:
        entradas.append(actual)
    return entradas


def _transcripciones(log: Path) -> dict[str, str]:
    salida: dict[str, str] = {}
    nota = None
    for entrada in _entradas(log):
        coincidencia = re.search(r"Procesando nota (NOTA-\d+)", entrada)
        if coincidencia:
            nota = coincidencia.group(1)
            continue
        if nota and "Transcripci" in entrada and "audio.wav:" in entrada:
            salida[nota] = entrada.split("audio.wav:", 1)[1].strip()
            nota = None
    return salida


def main() -> int:
    carpeta = Path(sys.argv[1])
    escenarios = sys.argv[2:] or [
        p.stem for p in sorted(carpeta.glob("*.json")) if p.stem not in {"meta", "resultado"}
    ]
    extractor = ExtractorParte(PLANTA, desde_api(PLANTA))
    for nombre in escenarios:
        datos = json.loads((carpeta / f"{nombre}.json").read_text(encoding="utf-8"))
        transcripciones = _transcripciones(carpeta / f"{nombre}.log")
        print(f"\n{'#' * 74}\n{nombre}")
        for nota in datos["verdad"]:
            if "recorrida_id" not in nota or nota.get("tipo") == "vivo":
                continue
            nota_id = nota["nota_id"]
            if nota.get("seguridad"):
                print(f"  {nota_id}  SEGURIDAD {nota['seguridad']} (no se mide el parte)")
                continue
            transcripcion = transcripciones.get(nota_id, nota.get("texto", ""))
            extraccion = extractor.extraer(transcripcion)
            ambig = next(
                (a["tag"] for a in nota.get("ambiguedades", []) if a.get("tipo") == "equipo"),
                None,
            )
            print(f"\n  {nota_id}  ambigüedad={ambig}")
            print(f"    texto: {transcripcion}")
            for item in nota.get("items", []):
                print(
                    f"      verdad: {item.get('tag')} {item.get('variable')} "
                    f"= {item.get('valor')} {item.get('unidad')} "
                    f"obs={item.get('observacion')!r}"
                )
            for fila in extraccion.filas:
                esperado_tag = ambig if (fila.tag is None and ambig) else fila.tag
                marca = "?"
                if fila.valor is not None:
                    candidatos = [
                        i
                        for i in nota.get("items", [])
                        if i.get("tag") == esperado_tag and i.get("valor") is not None
                    ]
                    item = next(
                        (i for i in candidatos if i.get("variable") == fila.variable),
                        candidatos[0] if candidatos else None,
                    )
                    if item and _cerca(
                        fila.valor, item.get("valor"), fila.unidad, item.get("unidad")
                    ):
                        marca = "ok"
                    elif item:
                        marca = "MAL"
                print(
                    f"      extraído: {fila.tag} {fila.variable} = {fila.valor} "
                    f"{fila.unidad} obs={fila.observacion!r} [{marca}]"
                )
            for pregunta in extraccion.preguntas:
                print(
                    f"      PREGUNTA: {pregunta.texto} tags={pregunta.tags} filas={pregunta.filas}"
                )

            # --- B1: un evento por equipo, con el tipo que espera la verdad
            filas_resueltas = [
                replace(fila, tag=ambig) if (fila.tag is None and ambig) else fila
                for fila in extraccion.filas
            ]
            eventos = clasificar_partes(
                nota_id=nota_id,
                parte_id="parte-x",
                ts=datetime(2026, 11, 5, tzinfo=UTC),
                filas=filas_resueltas,
                planta=PLANTA,
            )
            publicado = {evento.equipo: evento.tipo.value for evento in eventos}
            esperado = _esperados(nota)
            ok = all(publicado.get(tag) == tipo for tag, tipo in esperado.items())
            print(
                f"      B1: {'ok' if ok else 'MAL'}  esperado={esperado} "
                f"publicado={publicado}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
