"""Dataset oficial del historial y normalización estructurada de sus avisos.

La Academia publica 243 órdenes históricas en
``datos/documentos/historial/ordenes.json`` y la falla real de cada una en
``datos/documentos/evaluacion/historial_verdad.json``.  Ese segundo archivo es
 Ground Truth: se usa **sólo para puntuar** y nunca forma parte de la entrada
del agente.

El aviso histórico está escrito como lo escribe el personal de campo:
abreviaturas, errores de tipeo y frases muy resumidas.  Para que baseline y
agente reciban exactamente la misma entrada, este módulo lo convierte de forma
determinista a observaciones estructuradas.  La normalización reconoce
**variables y síntomas**; no reconoce códigos de falla ni consulta la verdad.

Una observación es *diagnóstica* cuando describe una firma de falla.  Un estado
operativo —una bomba disparada, una ruta preventiva— no alcanza para seleccionar
una falla y se conserva aparte.  Así el benchmark no convierte la falta de
evidencia en un acierto falso ni en un fallo inventado.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from analisis.catalogo import CatalogoManuales
from rag.corpus import CorpusArchivos, DocumentoRag

#: Commit oficial usado como ancla del corpus y de las etiquetas.
COMMIT_OFICIAL = "ab65b95e911731351dba395817a74a8cc60da2b2"

#: Correspondencia entre la falla del simulador y la ficha vigente del manual.
#: La tabla sale del catálogo de la Academia: F01/F02/F03/F04/F05 son las
#: bombas TX-350; F07/F08 son los filtros FC-48; F06, F09 y F10 tienen sus
#: fichas TX350-D §8.8, §8.9 y §8.10.  No se agrega ninguna falla extra.
MAPA_FALLAS_SIMULADOR: dict[str, str] = {
    "F01": "TX-T01",
    "F02": "TX-T02",
    "F03": "TX-T03",
    "F04": "TX-T04",
    "F05": "TX-T05",
    "F06": "TX-T06",
    "F07": "FC-T01",
    "F08": "FC-T02",
    "F09": "TX-T07",
    "F10": "TX-T08",
}


@dataclass(frozen=True)
class Observacion:
    """Una observación estructurada del aviso histórico.

    ``descripcion`` usa el vocabulario del manual para que el baseline pueda
    cruzar la evidencia y para que el agente reciba el mismo dato que el
    baseline, sin texto libre del operador.
    """

    variable: str
    descripcion: str
    valor: float | None = None
    unidad: str | None = None
    direccion: str | None = None
    diagnostica: bool = True

    @property
    def texto(self) -> str:
        valor = "" if self.valor is None else f": {self.valor:g}"
        unidad = f" {self.unidad}" if self.unidad else ""
        return f"{self.descripcion}{valor}{unidad}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "variable": self.variable,
            "descripcion": self.descripcion,
            "valor": self.valor,
            "unidad": self.unidad,
            "direccion": self.direccion,
            "diagnostica": self.diagnostica,
            "texto": self.texto,
        }


@dataclass(frozen=True)
class CasoHistorial:
    """Un caso oficial y la entrada compartida por los dos evaluados."""

    case_id: str
    orden_id: str
    fecha_aviso: str
    equipo: str
    tipo_equipo: str
    observaciones: tuple[Observacion, ...]
    #: Texto normalizado que se usa sólo para detectar plantillas duplicadas.
    #: Nunca forma parte de la entrada compartida ni del prompt del agente.
    texto_normalizado: str
    falla_simulador: str | None
    falla_esperada: str | None
    tarea_esperada: str | None
    tareas_de_la_orden: tuple[str, ...]

    @property
    def tiene_evidencia_diagnostica(self) -> bool:
        return any(observacion.diagnostica for observacion in self.observaciones)

    @property
    def es_abstencion(self) -> bool:
        """El Ground Truth no identifica una falla."""
        return self.falla_esperada is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "orden_id": self.orden_id,
            "fecha_aviso": self.fecha_aviso,
            "equipo": self.equipo,
            "tipo_equipo": self.tipo_equipo,
            "aviso_sha256": hashlib.sha256(self.texto_normalizado.encode("utf-8")).hexdigest(),
            "entrada_compartida": [observacion.as_dict() for observacion in self.observaciones],
            "tiene_evidencia_diagnostica": self.tiene_evidencia_diagnostica,
            "ground_truth": {
                "falla_simulador": self.falla_simulador,
                "falla_catalogo": self.falla_esperada,
                "tarea_esperada_por_catalogo": self.tarea_esperada,
                "tareas_reales_de_la_orden": list(self.tareas_de_la_orden),
            },
        }


@dataclass(frozen=True)
class DatasetHistorial:
    """Dataset cargado, con su corpus y sus hashes de procedencia."""

    casos: tuple[CasoHistorial, ...]
    documentos: tuple[DocumentoRag, ...]
    catalogo: CatalogoManuales
    planta: dict[str, Any]
    archivos: dict[str, dict[str, Any]]

    @property
    def casos_con_evidencia(self) -> tuple[CasoHistorial, ...]:
        return tuple(caso for caso in self.casos if caso.tiene_evidencia_diagnostica)

    def resumen(self) -> dict[str, Any]:
        fallas = Counter(caso.falla_simulador or "sin_falla" for caso in self.casos)
        equipos = Counter(caso.equipo for caso in self.casos)
        return {
            "version": 1,
            "origen": "historial oficial de órdenes de la Academia",
            "commit": COMMIT_OFICIAL,
            "casos": len(self.casos),
            "casos_con_evidencia_diagnostica": len(self.casos_con_evidencia),
            "casos_sin_evidencia_diagnostica": len(self.casos) - len(self.casos_con_evidencia),
            "distribucion_falla_simulador": dict(sorted(fallas.items())),
            "distribucion_equipo": dict(sorted(equipos.items())),
            "archivos": self.archivos,
        }


def cargar_dataset_historial(repo_oficial: str | Path) -> DatasetHistorial:
    """Carga el historial oficial y deja la verdad separada de la entrada."""
    repo = Path(repo_oficial).resolve()
    base = repo / "datos" / "documentos"
    archivo_ordenes = base / "historial" / "ordenes.json"
    archivo_verdad = base / "evaluacion" / "historial_verdad.json"
    archivo_planta = repo / "datos" / "planta.yaml"
    for archivo in (archivo_ordenes, archivo_verdad, archivo_planta):
        if not archivo.is_file():
            raise FileNotFoundError(f"No existe el archivo oficial requerido: {archivo}")

    ordenes = json.loads(archivo_ordenes.read_text(encoding="utf-8"))
    documento_verdad = json.loads(archivo_verdad.read_text(encoding="utf-8"))
    verdad = documento_verdad.get("fallas")
    if not isinstance(verdad, dict):
        raise ValueError("historial_verdad.json no contiene el objeto 'fallas'")
    planta = yaml.safe_load(archivo_planta.read_text(encoding="utf-8")) or {}

    documentos = tuple(CorpusArchivos(base).cargar(incluir_historial=False))
    catalogo = CatalogoManuales.desde_documentos(list(documentos))
    tipos = {
        str(equipo.get("tag")): str(equipo.get("tipo") or "equipo")
        for equipo in planta.get("equipos") or []
        if isinstance(equipo, dict) and equipo.get("tag")
    }

    ids_ordenes = {str(orden.get("id")) for orden in ordenes}
    ids_verdad = {str(identificador) for identificador in verdad}
    if ids_ordenes != ids_verdad:
        faltantes = sorted(ids_ordenes - ids_verdad)
        sobrantes = sorted(ids_verdad - ids_ordenes)
        raise ValueError(
            "Las órdenes y la verdad oficial no coinciden: "
            f"faltan={faltantes[:5]}, sobran={sobrantes[:5]}"
        )

    casos: list[CasoHistorial] = []
    for orden in ordenes:
        orden_id = str(orden.get("id") or "")
        equipo = str(orden.get("equipo") or "PLANTA")
        falla_simulador = verdad.get(orden_id)
        falla_simulador = str(falla_simulador) if falla_simulador is not None else None
        if falla_simulador is not None and falla_simulador not in MAPA_FALLAS_SIMULADOR:
            raise ValueError(f"Falla oficial sin correspondencia de catálogo: {falla_simulador}")
        falla_esperada = (
            MAPA_FALLAS_SIMULADOR[falla_simulador] if falla_simulador is not None else None
        )
        if falla_esperada is not None and catalogo.fallas.por_codigo(falla_esperada) is None:
            raise ValueError(f"La falla del catálogo no existe: {falla_esperada}")
        texto_aviso = str(orden.get("aviso") or "")
        observaciones = normalizar_aviso(
            texto_aviso,
            equipo=equipo,
            tipo_equipo=tipos.get(equipo, "equipo"),
        )
        tarea_esperada = tarea_para_falla(catalogo, falla_esperada, equipo)
        casos.append(
            CasoHistorial(
                case_id=orden_id,
                orden_id=orden_id,
                fecha_aviso=str(orden.get("fecha_aviso") or ""),
                equipo=equipo,
                tipo_equipo=tipos.get(equipo, "equipo"),
                observaciones=observaciones,
                texto_normalizado=_normalizar(texto_aviso),
                falla_simulador=falla_simulador,
                falla_esperada=falla_esperada,
                tarea_esperada=tarea_esperada,
                tareas_de_la_orden=tuple(str(tarea) for tarea in orden.get("tareas") or []),
            )
        )

    archivos = {
        "historial_ordenes.json": _hash_archivo(archivo_ordenes),
        "historial_verdad.json": _hash_archivo(archivo_verdad),
        "planta.yaml": _hash_archivo(archivo_planta),
        "indice.json": _hash_archivo(base / "indice.json"),
        "fragmentos.jsonl": _hash_archivo(base / "fragmentos.jsonl"),
    }
    return DatasetHistorial(
        casos=tuple(casos),
        documentos=documentos,
        catalogo=catalogo,
        planta=planta,
        archivos=archivos,
    )


def normalizar_aviso(
    aviso: str,
    *,
    equipo: str,
    tipo_equipo: str,
) -> tuple[Observacion, ...]:
    """Convierte un aviso histórico en observaciones estructuradas.

    La función no recibe la falla real.  Sólo reconoce síntomas y estados que
    están escritos en el aviso, con las reglas léxicas del historial oficial.
    """
    texto = _normalizar(aviso)
    tokens = set(re.findall(r"[a-z0-9]+", texto))
    observaciones: list[Observacion] = []

    def agregar(
        variable: str,
        descripcion: str,
        *,
        valor: float | None = None,
        unidad: str | None = None,
        direccion: str | None = None,
        diagnostica: bool = True,
        reemplazar_direccion: bool = False,
    ) -> None:
        for indice, actual in enumerate(observaciones):
            if actual.variable == variable:
                observaciones[indice] = Observacion(
                    variable=variable,
                    descripcion=descripcion,
                    valor=valor if valor is not None else actual.valor,
                    unidad=unidad if unidad is not None else actual.unidad,
                    direccion=(
                        direccion
                        if reemplazar_direccion or direccion is not None
                        else actual.direccion
                    ),
                    diagnostica=diagnostica or actual.diagnostica,
                )
                return
        observaciones.append(
            Observacion(
                variable=variable,
                descripcion=descripcion,
                valor=valor,
                unidad=unidad,
                direccion=direccion,
                diagnostica=diagnostica,
            )
        )

    preventivo = _es_preventivo(texto)
    disparada = _tiene_palabra(tokens, ("disparo", "dispro", "dipsaro"), 2)
    if preventivo:
        agregar(
            "estado",
            "Inspección o mantenimiento programado, sin firma diagnóstica",
            diagnostica=False,
        )
        return tuple(observaciones)
    if disparada:
        agregar(
            "estado",
            "Equipo disparado; no rearmar hasta revisión",
            diagnostica=False,
        )

    if _tiene_goteo(tokens):
        valor = _numero(texto, r"(?:gotas|g)\s*(?:/|x)?\s*min")
        agregar(
            "goteo_empaquetadura",
            "Goteo creciente por empaquetadura; charco de agua bajo el cabezal",
            valor=valor,
            unidad="gotas/min" if valor is not None else None,
        )

    ruido_valvula = _tiene_palabra(
        tokens,
        ("golpe", "golpeteo", "pulsacion", "plusacion"),
        1,
    )
    if (
        ruido_valvula
        or _tiene_palabra(tokens, ("oscila",), 1)
        or _tiene_palabra(tokens, ("metalico",), 1)
    ):
        agregar(
            "ruido",
            "Golpeteo metálico rítmico y pulsación alta en la descarga",
        )

    if _tiene_palabra(tokens, ("rodamiento",), 1) and _tiene_palabra(tokens, ("ruido", "rumor"), 1):
        agregar(
            "ruido",
            "Ruido raro de rodamiento, distinto del golpe de válvulas",
        )

    cavitacion = _tiene_palabra(tokens, ("cavitacion", "cavita", "piedras", "grava"), 2)
    if cavitacion:
        agregar(
            "ruido",
            "Ruido a piedras por cavitación en la succión",
        )
        agregar(
            "presion_succion",
            "Presión de succión baja",
            direccion="bajo",
        )

    correas = _tiene_palabra(tokens, ("correa", "correas", "patinan", "chirrido", "chiri"), 1)
    if correas or _tiene_palabra(tokens, ("chilla",), 1):
        agregar(
            "estado_correas",
            "Caudal bajo con motor caliente; chirrido de correas al arrancar y polea caliente",
        )

    aceite = _tiene_palabra(tokens, ("aceite", "acetie"), 1)
    aceite_bajo = bool(
        aceite
        and (
            re.search(
                r"(?:nivel|visor)\b[^,;]{0,40}\b(?:bajo|falta|minimo|dbeajo)\b",
                texto,
            )
            or re.search(
                r"\b(?:bajo|falta|minimo|dbeajo)\b[^,;]{0,25}\b(?:aceite|acetie)\b",
                texto,
            )
            or re.search(
                r"\b(?:aceite|acetie)\b[^,;]{0,25}\b(?:bajo|falta|minimo|dbeajo)\b",
                texto,
            )
        )
    )
    if aceite_bajo:
        agregar(
            "nivel_aceite_visor",
            "Nivel de aceite del cárter bajo el mínimo",
            direccion="bajo",
        )
    if aceite and _tiene_palabra(tokens, ("subiendo", "subiendo", "subiedno", "subendo"), 1):
        temperatura = _numero(texto, r"(?:temp(?:eratura)?\s*aceite|aceite)[^0-9]{0,20}(\d+)")
        agregar(
            "temperatura_aceite",
            "Temperatura de aceite en alza gradual",
            valor=temperatura,
            unidad="degC" if temperatura is not None else None,
            direccion="alto",
        )

    vibracion = any(token.startswith("vib") for token in tokens)
    if vibracion:
        valor = _numero(texto, r"\bvib\w*\s*(?:en\s*)?(\d+[.,]?\d*)")
        sin_golpe = _tiene_palabra(tokens, ("golpea",), 2) and _tiene_negacion(texto)
        descripcion = (
            "Vibración moderada en aumento sin golpe metálico"
            if sin_golpe
            else "Vibración anómala en aumento"
        )
        agregar(
            "vibracion",
            descripcion,
            valor=valor,
            unidad="mm/s" if valor is not None else None,
            direccion="alto",
            # Mencionar «vibración» como parte de un disparo no es una firma
            # diagnóstica.  En cambio, una medición, una tendencia o una
            # comparación con el rango normal sí lo son.
            diagnostica=(
                not disparada
                or valor is not None
                or bool(
                    re.search(
                        r"\b(?:subiendo|aument|más|mas|sigue|normal|anomal|moderad)\b",
                        texto,
                    )
                )
            ),
        )
        if _tiene_palabra(tokens, ("acople",), 1):
            agregar(
                "acople",
                "Acople caliente",
            )

    diferencial = any(token.startswith("dif") for token in tokens)
    if diferencial:
        # El tag puede contener números («dif de f-201b en 1,56»).  El valor
        # buscado es el que sigue a «en» o «de», nunca el número del tag.
        valor = _numero(texto, r"\bdif(?:erencial)?\b[^,;]{0,40}?\ben\s+(\d+[.,]?\d*)")
        if valor is None:
            valor = _numero(texto, r"\bdif(?:erencial)?\b[^,;]{0,40}?\bde\s+(\d+[.,]?\d*)")
        direccion = "alto"
        if valor is not None and valor <= 0.15:
            direccion = "bajo"
        elif valor is None and _tiene_palabra(tokens, ("bajo",), 1):
            direccion = "bajo"
        agregar(
            "presion_diferencial",
            "Presión diferencial alta y en aumento"
            if direccion == "alto"
            else "Caída de la presión diferencial",
            valor=valor,
            unidad="kg/cm2" if valor is not None else None,
            direccion=direccion,
        )

    succion = _tiene_palabra(tokens, ("succion",), 1)
    if succion:
        valor = _numero(texto, r"succion\s*(\d+[.,]?\d*)")
        agregar(
            "presion_succion",
            "Presión de succión baja",
            valor=valor,
            unidad="kg/cm2" if valor is not None else None,
            direccion="bajo",
            # Una succión baja citada dentro de un disparo es un estado
            # operativo.  La cavitación agrega su propia firma diagnóstica.
            diagnostica=not disparada,
        )

    if "caudal" in tokens and _tiene_palabra(tokens, ("bajo",), 1):
        agregar(
            "caudal",
            "Caudal bajo",
            direccion="bajo",
        )

    if _tiene_instrumento_inconsistente(texto, tokens):
        if "dif" in texto or "diferencial" in texto:
            agregar(
                "presion_diferencial",
                "Lectura diferencial clavada y distinta del manómetro local",
                direccion=None,
                reemplazar_direccion=True,
            )
        elif "caudalimetro" in texto:
            agregar(
                "caudal",
                "Caudalímetro no coincide con la suma de las bombas",
            )
        else:
            agregar(
                "presion_descarga_local",
                "Presión de descarga clavada y distinta del manómetro local",
            )

    if tipo_equipo not in {"bomba", "filtro"} and not observaciones:
        agregar(
            "estado",
            "Registro sin firma diagnóstica de una falla de equipo",
            diagnostica=False,
        )

    return tuple(observaciones)


def tarea_para_falla(
    catalogo: CatalogoManuales,
    falla: str | None,
    equipo: str,
) -> str | None:
    """La tarea del plan que el catálogo asocia a la falla verdadera."""
    if falla is None:
        return None
    ficha = catalogo.fallas.por_codigo(falla)
    if ficha is None:
        return None
    tarea = catalogo.tarea_por_procedimiento(
        ficha.procedimiento,
        equipo,
        catalogo.equipos_de_falla(ficha),
    )
    return tarea.codigo if tarea is not None else None


def _normalizar(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", texto)
    sin_tildes = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sin_tildes.lower()).strip()


def _distancia(uno: str, otro: str) -> int:
    anterior = list(range(len(otro) + 1))
    for i, letra in enumerate(uno, start=1):
        actual = [i]
        for j, otra in enumerate(otro, start=1):
            actual.append(
                min(
                    actual[-1] + 1,
                    anterior[j] + 1,
                    anterior[j - 1] + (letra != otra),
                )
            )
        anterior = actual
    return anterior[-1]


def _tiene_palabra(tokens: set[str], palabras: tuple[str, ...], max_distancia: int) -> bool:
    for palabra in palabras:
        for token in tokens:
            if palabra == token:
                return True
            # La aproximación por distancia se reserva para palabras de al
            # menos cuatro letras.  Si no, «la» parece «claa» y un texto
            # normal se vuelve una lectura de instrumento inconsistente.
            if (
                max_distancia > 0
                and len(token) >= 4
                and len(palabra) >= 4
                and abs(len(token) - len(palabra)) <= max_distancia
                and _distancia(token, palabra) <= max_distancia
            ):
                return True
            if len(palabra) >= 4 and palabra.endswith("a") and token.startswith(palabra[:-1]):
                return True
    return False


def _tiene_goteo(tokens: set[str]) -> bool:
    return (
        _tiene_palabra(tokens, ("empaquetadura", "empaq"), 2)
        or _tiene_palabra(tokens, ("goteo", "gotea", "goteando"), 2)
        or _tiene_palabra(tokens, ("charco", "prensa"), 1)
        or ("pierde" in tokens and "agua" in tokens)
    )


def _tiene_negacion(texto: str) -> bool:
    return bool(re.search(r"\b(?:no|nunca|sin)\b", texto))


def _es_preventivo(texto: str) -> bool:
    return bool(
        re.search(
            r"(?:medici[oó]n|meidcion|mediccion)\s+mens(?:ual|ul)|"
            r"ruta\s+mensual|programad|alinear\s+acople|"
            r"cambio\s+de\s+reten|cambio\s+de\s+embolo",
            texto,
        )
    )


def _tiene_instrumento_inconsistente(texto: str, tokens: set[str]) -> bool:
    if "caudalimetro" in tokens and "suma" in tokens:
        return True
    if _tiene_palabra(tokens, ("clavada", "claa", "clavado"), 2):
        return True
    if "plana" in tokens and "presion" in texto:
        return True
    if "no" in tokens and ("varia" in tokens or "mueve" in tokens):
        return True
    return "local" in tokens and "marca" in tokens and "dif" in texto


def _numero(texto: str, patron: str) -> float | None:
    coincidencia = re.search(patron, texto)
    if coincidencia is None:
        return None
    bruto = coincidencia.group(1) if coincidencia.groups() else coincidencia.group(0)
    bruto = bruto.replace(",", ".")
    try:
        return float(bruto)
    except ValueError:
        return None


def _hash_archivo(archivo: Path) -> dict[str, Any]:
    contenido = archivo.read_bytes()
    return {
        "ruta": str(archivo),
        "sha256": hashlib.sha256(contenido).hexdigest(),
        "bytes": len(contenido),
    }
