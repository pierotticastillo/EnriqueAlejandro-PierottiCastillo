"""Catálogos de la planta para el análisis: fallas, tareas, repuestos y materiales.

Todo sale del mismo corpus que usa el RAG (MBP-4 para el plan de
mantenimiento, los manuales de los fabricantes para las fallas), así que cada
dato viene con su cita. Las tareas se relacionan con las fallas por el
procedimiento del fabricante: la ficha dice "P-02" y MBP-4 §13.3 dice que
P-02 es el cambio de empaquetadura. No hay una tabla de fallas escrita a mano
en el código.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from analisis.fallas import CatalogoFallas
from analisis.modelos import FallaCatalogo
from rag.corpus import DocumentoRag
from tools.modelos import (
    ConsultaCatalogoTareas,
    ConsultaInventario,
    ConsultaListaMateriales,
    RepuestoStock,
    TareaCatalogo,
)

DOCUMENTO_BUENAS_PRACTICAS = "MBP-4"
# "P-02", "FP-02": el código del procedimiento del fabricante.
_CODIGO_PROCEDIMIENTO = re.compile(r"\bF?P-\d+\b")


class CorpusRag:
    """Adaptador del corpus del RAG a la interfaz de secciones de las tools.

    ``tools.tareas`` y ``tools.inventario`` ya saben leer el capítulo 13 y el
    14 del manual de buenas prácticas; este adaptador les pasa las secciones
    del corpus indexado en lugar de las que baja la API.
    """

    def __init__(self, documentos: list[DocumentoRag]) -> None:
        self._documentos = {documento.clave: documento for documento in documentos}

    @property
    def documentos(self) -> dict[str, DocumentoRag]:
        return self._documentos

    def seccion(self, documento: str, numero: str) -> dict | None:
        doc = self._documentos.get(documento)
        if doc is None:
            return None
        for chunk in doc.chunks:
            if chunk.seccion == numero:
                return {
                    "id": chunk.cita,
                    "numero": chunk.seccion,
                    "titulo": chunk.titulo,
                    "texto": chunk.texto,
                }
        return None


@dataclass(frozen=True)
class CatalogoManuales:
    """Todo lo que el manual dice, listo para decidir."""

    fallas: CatalogoFallas
    tareas: tuple[TareaCatalogo, ...]
    repuestos: dict[str, RepuestoStock]
    materiales: dict[str, tuple[str, ...]]
    equipos_por_documento: dict[str, tuple[str, ...]]
    nombres_equipos: tuple[str, ...] = ()

    # ------------------------------------------------------------------ tareas

    def vocabulario_asr(self) -> str:
        """El vocabulario de la planta, para el ASR local.

        El decodificador de voz escribe lo que oye; si no conoce los nombres
        de la planta, escribe "Chofitrante" en vez de "filtrante" y el cierre
        ya no se puede comparar contra la orden. Con estos nombres de
        referencia, el ASR local acierta.

        Se arma con lo que dice el manual (los nombres de las tareas del plan de
        mantenimiento y los de los repuestos de la lista de materiales) y con
        los nombres que la planta da para sus equipos ("la del medio", "el
        filtro A", "el pulmón"): son las palabras que el técnico dice en el
        parte por voz, y sin ellas el ASR las garbla ("el aboma" por "la
        bomba", "competeo metálico" por "golpeteo metálico") y el sistema no
        reconoce ni el equipo ni el síntoma.

        Los nombres de los equipos van **al final** del prompt: se midió que
        es donde el decodificador los toma, y es lo que decide el equipo y la
        fila del parte.
        """
        tareas: list[str] = []
        for tarea in self.tareas:
            nombre = _nombre_corto(tarea.nombre)
            if nombre and nombre not in tareas:
                tareas.append(nombre)
        repuestos: list[str] = []
        for repuesto in self.repuestos.values():
            nombre = _nombre_corto(repuesto.nombre)
            if nombre and nombre not in repuestos:
                repuestos.append(nombre)
        partes = [
            "Cierre de orden de trabajo de la planta de inyección de agua PIA-3.",
            f"Tareas: {', '.join(tareas)}." if tareas else "",
            f"Repuestos: {', '.join(repuestos)}." if repuestos else "",
            "El técnico dice qué hizo, qué repuestos usó, cuántas horas tardó "
            "y cómo quedó la prueba final.",
            f"Equipos de la planta: {', '.join(self.nombres_equipos)}."
            if self.nombres_equipos
            else "",
        ]
        return " ".join(parte for parte in partes if parte)

    def tarea_para_equipo(self, codigo: str, equipo: str) -> TareaCatalogo | None:
        """La tarea del código que aplica al equipo (T-F-CAR tiene dos variantes)."""
        for tarea in self.tareas:
            if tarea.codigo == codigo and equipo in tarea.equipos:
                return tarea
        return None

    def tarea_por_procedimiento(
        self,
        procedimiento: str | None,
        equipo: str,
        equipos_del_manual: tuple[str, ...],
    ) -> TareaCatalogo | None:
        """La tarea que ejecuta el procedimiento de la ficha de la falla.

        La ficha dice "P-03 (cambio) / P-09 (inspección)": gana la tarea del
        primer procedimiento, que es el cambio. Las tareas no estándar no
        traen lista de equipos, así que se admiten para los equipos del manual
        del fabricante que describe la falla.
        """
        codigos = _codigos_procedimiento(procedimiento)
        if not codigos:
            return None
        candidatas: list[tuple[int, str, TareaCatalogo]] = []
        for tarea in self.tareas:
            if not _aplica_a(tarea, equipo, equipos_del_manual):
                continue
            codigos_tarea = _codigos_procedimiento(tarea.procedimiento)
            if not codigos_tarea or codigos[0] not in codigos_tarea:
                continue
            candidatas.append((codigos_tarea.index(codigos[0]), tarea.codigo, tarea))
        if not candidatas:
            return None
        candidatas.sort(key=lambda item: (item[0], item[1]))
        return candidatas[0][2]

    def tareas_de_inspeccion(self, equipo: str) -> tuple[TareaCatalogo, ...]:
        """Las tareas de inspección del manual (válidas cuando la evidencia no alcanza)."""
        return tuple(
            tarea
            for tarea in self.tareas
            if tarea.codigo in {"I-VIB", "I-VAL"}
            and (equipo in tarea.equipos or not tarea.equipos)
        )

    def tarea_por_codigo(self, codigo: str, equipo: str) -> TareaCatalogo | None:
        """La tarea con ese código, si aplica al equipo.

        Las tareas no estándar no traen lista de equipos: valen para los
        equipos del manual del fabricante al que pertenece su procedimiento
        (``TX-350 P-09`` son las bombas del manual TX350-D).
        """
        for tarea in self.tareas:
            if tarea.codigo != codigo:
                continue
            if tarea.equipos:
                if equipo in tarea.equipos:
                    return tarea
                continue
            if equipo in self._equipos_del_procedimiento(tarea):
                return tarea
        return None

    def _equipos_del_procedimiento(self, tarea: TareaCatalogo) -> tuple[str, ...]:
        prefijo = _prefijo_procedimiento(tarea.procedimiento)
        if not prefijo:
            return ()
        for clave, equipos in self.equipos_por_documento.items():
            if clave.upper().replace("-", "").startswith(prefijo):
                return equipos
        return ()

    # -------------------------------------------------------------- repuestos

    def costo_unitario(self, codigo: str) -> float:
        repuesto = self.repuestos.get(codigo)
        return float(repuesto.costo_usd or 0.0) if repuesto else 0.0

    def stock(self, codigo: str, almacen: str = "yacimiento") -> float:
        repuesto = self.repuestos.get(codigo)
        if repuesto is None:
            return 0.0
        return float(repuesto.stock.get(almacen, 0.0))

    def materiales_de(self, equipo: str) -> tuple[str, ...]:
        return self.materiales.get(equipo, ())

    # ------------------------------------------------------------------ fallas

    def equipos_de_falla(self, falla: FallaCatalogo) -> tuple[str, ...]:
        return self.equipos_por_documento.get(falla.documento, ())

    @classmethod
    def desde_documentos(
        cls,
        documentos: list[DocumentoRag],
        *,
        equipos: list[str] | None = None,
        nombres_equipos: list[str] | None = None,
    ) -> CatalogoManuales:
        from tools.inventario import inventario, lista_materiales
        from tools.tareas import catalogo_tareas

        corpus = CorpusRag(documentos)
        tareas = tuple(
            catalogo_tareas(
                None,
                ConsultaCatalogoTareas(incluir_no_estandar=True),
                corpus=corpus,
            )
        )
        repuestos = {
            repuesto.codigo: repuesto
            for repuesto in inventario(None, ConsultaInventario(), corpus=corpus)
        }
        materiales: dict[str, tuple[str, ...]] = {}
        for equipo in equipos or _equipos_del_corpus(documentos):
            codigos = tuple(
                material.codigo
                for material in lista_materiales(
                    None,
                    ConsultaListaMateriales(equipo=equipo),
                    corpus=corpus,
                )
            )
            if codigos:
                materiales[equipo] = codigos
        equipos_por_documento = {
            documento.clave: tuple(documento.equipos)
            for documento in documentos
            if documento.equipos
        }
        return cls(
            fallas=CatalogoFallas.desde_documentos(documentos),
            tareas=tareas,
            repuestos=repuestos,
            materiales=materiales,
            equipos_por_documento=equipos_por_documento,
            nombres_equipos=tuple(nombres_equipos or ()),
        )


def _codigos_procedimiento(texto: str | None) -> tuple[str, ...]:
    if not texto:
        return ()
    return tuple(_CODIGO_PROCEDIMIENTO.findall(texto))


def _nombre_corto(nombre: str | None) -> str:
    """El nombre del manual, sin el agregado entre paréntesis.

    "kit de empaquetadura TX-350 (3 émbolos)" → "kit de empaquetadura TX-350":
    en campo el técnico no dice cuántos émbolos tiene el kit. Y sin la unidad
    "µm": el ASR la confunde con el final de un decimal ("fueron tres µm").
    """
    if not nombre:
        return ""
    texto = nombre.split(" (", 1)[0].strip()
    texto = texto.replace("µm", "").replace("µ", "")
    return " ".join(texto.split())


def _prefijo_procedimiento(texto: str | None) -> str:
    """``TX-350 P-09`` → ``TX350``: el fabricante del manual."""
    if not texto:
        return ""
    normal = texto.upper().replace("-", "").replace(" ", "")
    coincidencia = re.match(r"^([A-Z]{2,3}\d+)", normal)
    return coincidencia.group(1) if coincidencia else ""


def _aplica_a(
    tarea: TareaCatalogo,
    equipo: str,
    equipos_del_manual: tuple[str, ...],
) -> bool:
    if tarea.equipos:
        return equipo in tarea.equipos
    # Las tareas no estándar no traen equipos: valen para los equipos del
    # manual del fabricante que describe la falla (bombas para TX350-D).
    return equipo in equipos_del_manual


def _equipos_del_corpus(documentos: list[DocumentoRag]) -> list[str]:
    equipos: list[str] = []
    for documento in documentos:
        for equipo in documento.equipos:
            if equipo not in equipos:
                equipos.append(equipo)
    return equipos
