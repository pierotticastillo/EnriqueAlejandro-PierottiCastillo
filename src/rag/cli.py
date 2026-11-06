"""CLI de la Fase 4: indexar, evaluar y purgar memoria.

    python -m rag.cli indexar --embedder fastembed
    python -m rag.cli evaluar --corpus /ruta/a/datos/documentos --detalle
    python -m rag.cli evaluar-pgvector --suite retrieval.json
    python -m rag.cli purgar --dias 90            # dry-run

El indexado necesita PostgreSQL con la migración ``0004``. La evaluación
puede correr en memoria sobre una copia local del corpus de la planta, sin
base (``--corpus``), o contra el índice real (``evaluar-pgvector``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from config import Settings
from db.session import crear_engine
from rag.corpus import CorpusArchivos, CorpusPlanta
from rag.embedder import crear_embedder
from rag.evaluar import BASELINE_BM25, comparar_con_baseline, errores, evaluar
from rag.indexer import IndexadorRag, guardar_evaluacion, limpiar
from rag.indice import PESO_LEXICO_POR_DEFECTO, IndiceRag
from rag.memoria import contar_purgables, purgar_memoria
from rag.retriever import RecuperadorPgvector
from tools.cliente import ClientePlanta
from tools.modelos import ConsultaManuales


def _salida(texto: str = "") -> None:
    print(texto)


def _cliente(args: argparse.Namespace, settings: Settings) -> ClientePlanta:
    return ClientePlanta(
        args.api_url or settings.planta_api_url,
        timeout_s=settings.planta_api_timeout_s,
    )


def _embedder(args: argparse.Namespace, settings: Settings):
    return crear_embedder(
        args.embedder or settings.rag_embedder,
        modelo=args.modelo or settings.rag_modelo,
        dimension=settings.rag_dimension,
        cache_dir=settings.rag_cache_dir,
        estricto=True,
    )


def _motor(args: argparse.Namespace, settings: Settings):
    return crear_engine(args.database_url or settings.database_url)


def cmd_indexar(args: argparse.Namespace, settings: Settings) -> int:
    cliente = _cliente(args, settings)
    documentos = CorpusPlanta(cliente).cargar(incluir_historial=not args.sin_historial)
    if not documentos:
        _salida("La planta no devolvió documentos; no hay nada para indexar.")
        return 1
    embedder = _embedder(args, settings)
    motor = _motor(args, settings)
    if args.limpiar:
        limpiar(motor)
    resumen = IndexadorRag(motor, embedder).indexar(documentos, reemplazar=True)
    _salida(
        json.dumps(
            {
                "documentos": resumen.documentos,
                "chunks": resumen.chunks,
                "embedder": resumen.embedder,
                "dimension": resumen.dimension,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_evaluar(args: argparse.Namespace, settings: Settings) -> int:
    peso = args.peso_lexico if args.peso_lexico is not None else PESO_LEXICO_POR_DEFECTO
    if args.corpus:
        base = Path(args.corpus)
        documentos = CorpusArchivos(base).cargar(incluir_historial=True)
        suite = json.loads(
            (Path(args.suite) if args.suite else base / "evaluacion" / "retrieval.json")
            .read_text(encoding="utf-8")
        )
    else:
        cliente = _cliente(args, settings)
        documentos = CorpusPlanta(cliente).cargar(incluir_historial=True)
        if args.suite:
            suite = json.loads(Path(args.suite).read_text(encoding="utf-8"))
        else:
            suite = cliente.get("/documentos/evaluacion/retrieval.json")
    if not documentos:
        _salida("No hay corpus para evaluar.")
        return 1
    if not suite:
        _salida("No hay suite de evaluación. Indicá --suite o --corpus.")
        return 1

    embedder = _embedder(args, settings)
    indice = IndiceRag(documentos, embedder, peso_lexico=peso)
    busquedas = {
        "lexico": lambda pregunta, k: indice.buscar_citas(
            pregunta, k, solo_vigentes=False, peso_lexico=1.0
        ),
        "denso": lambda pregunta, k: indice.buscar_citas(
            pregunta, k, solo_vigentes=False, peso_lexico=0.0
        ),
        "hibrido": lambda pregunta, k: indice.buscar_citas(
            pregunta, k, solo_vigentes=False, peso_lexico=peso
        ),
        "hibrido_vigente": lambda pregunta, k: indice.buscar_citas(
            pregunta, k, solo_vigentes=True, peso_lexico=peso
        ),
    }
    resultados = {nombre: evaluar(buscar, suite) for nombre, buscar in busquedas.items()}
    resumen = {
        nombre: {
            "total": resultado["total"],
            "revision_vencida_antes": resultado["revision_vencida_antes"],
        }
        for nombre, resultado in resultados.items()
    }
    resumen["baseline_bm25"] = BASELINE_BM25
    resumen["hibrido_vs_baseline"] = comparar_con_baseline(
        resultados["hibrido_vigente"], BASELINE_BM25
    )
    _salida(json.dumps(resumen, ensure_ascii=False, indent=2))
    if args.detalle:
        _salida("\nFallos del híbrido (top-5):")
        for falla in errores(resultados["hibrido_vigente"], suite):
            _salida(json.dumps(falla, ensure_ascii=False))
    if args.guardar:
        destino = Path(args.guardar)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps(resumen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        _salida(f"\nResultados guardados en {destino}")
    if args.persistir:
        try:
            guardar_evaluacion(
                _motor(args, settings),
                suite=args.suite or "retrieval.json",
                motor="hibrido_vigente",
                resultado=resultados["hibrido_vigente"],
                detalle=resumen,
            )
            _salida("Evaluación persistida en rag_evaluacion.")
        except Exception as exc:  # noqa: BLE001 - la base es opcional
            _salida(f"No se pudo persistir la evaluación: {exc}")
    return 0


def cmd_evaluar_pgvector(args: argparse.Namespace, settings: Settings) -> int:
    """Mide la ruta de producción: pgvector para lo denso, BM25 para lo léxico."""
    suite = json.loads(Path(args.suite).read_text(encoding="utf-8"))
    embedder = _embedder(args, settings)
    motor = _motor(args, settings)
    peso = args.peso_lexico if args.peso_lexico is not None else settings.rag_peso_lexico
    recuperador = RecuperadorPgvector(motor, embedder, peso_lexico=peso)
    if not recuperador.disponible():
        _salida("El índice pgvector no está disponible.")
        return 1

    def buscar(pregunta: str, k: int) -> list[str]:
        return [
            fragmento.cita
            for fragmento in recuperador.buscar(
                ConsultaManuales(consulta=pregunta, top_k=k)
            )
        ]

    resultado = evaluar(buscar, suite)
    resumen = {
        "pgvector": {
            "total": resultado["total"],
            "revision_vencida_antes": resultado["revision_vencida_antes"],
        },
        "baseline_bm25": BASELINE_BM25,
        "pgvector_vs_baseline": comparar_con_baseline(resultado, BASELINE_BM25),
    }
    _salida(json.dumps(resumen, ensure_ascii=False, indent=2))
    if args.detalle:
        _salida("\nFallos del híbrido (top-5):")
        for falla in errores(resultado, suite):
            _salida(json.dumps(falla, ensure_ascii=False))
    if args.guardar:
        destino = Path(args.guardar)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps(resumen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        _salida(f"\nResultados guardados en {destino}")
    return 0


def cmd_purgar(args: argparse.Namespace, settings: Settings) -> int:
    motor = _motor(args, settings)
    dias = args.dias if args.dias is not None else settings.rag_memoria_dias
    if not args.aplicar:
        _salida(json.dumps(contar_purgables(motor, dias=dias), ensure_ascii=False))
        _salida("Dry-run: agregá --aplicar para borrar.")
        return 0
    _salida(json.dumps(purgar_memoria(motor, dias=dias), ensure_ascii=False))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)

    indexar = sub.add_parser("indexar", help="indexa manuales e historial en pgvector")
    indexar.add_argument("--api-url")
    indexar.add_argument("--database-url")
    indexar.add_argument("--embedder", choices=["fastembed", "hashing"])
    indexar.add_argument("--modelo")
    indexar.add_argument("--limpiar", action="store_true")
    indexar.add_argument("--sin-historial", action="store_true")
    indexar.set_defaults(func=cmd_indexar)

    evaluar_parser = sub.add_parser("evaluar", help="mide retrieval contra la suite")
    evaluar_parser.add_argument("--corpus", help="carpeta datos/documentos de la planta")
    evaluar_parser.add_argument("--suite", help="ruta a retrieval.json")
    evaluar_parser.add_argument("--api-url")
    evaluar_parser.add_argument("--database-url")
    evaluar_parser.add_argument("--embedder", choices=["fastembed", "hashing"])
    evaluar_parser.add_argument("--modelo")
    evaluar_parser.add_argument("--peso-lexico", type=float)
    evaluar_parser.add_argument("--detalle", action="store_true")
    evaluar_parser.add_argument("--guardar", help="archivo JSON de salida")
    evaluar_parser.add_argument("--persistir", action="store_true")
    evaluar_parser.set_defaults(func=cmd_evaluar)

    pgvector_parser = sub.add_parser(
        "evaluar-pgvector", help="mide retrieval contra el índice de pgvector"
    )
    pgvector_parser.add_argument("--suite", required=True, help="ruta a retrieval.json")
    pgvector_parser.add_argument("--database-url")
    pgvector_parser.add_argument("--embedder", choices=["fastembed", "hashing"])
    pgvector_parser.add_argument("--modelo")
    pgvector_parser.add_argument("--peso-lexico", type=float)
    pgvector_parser.add_argument("--detalle", action="store_true")
    pgvector_parser.add_argument("--guardar", help="archivo JSON de salida")
    pgvector_parser.set_defaults(func=cmd_evaluar_pgvector)

    purgar = sub.add_parser("purgar", help="borra estado de conversación vencido")
    purgar.add_argument("--database-url")
    purgar.add_argument("--dias", type=int)
    purgar.add_argument("--aplicar", action="store_true")
    purgar.set_defaults(func=cmd_purgar)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    settings = Settings()
    return int(args.func(args, settings))


if __name__ == "__main__":
    raise SystemExit(main())
