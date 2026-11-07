"""Catálogo de fallas del manual, con cita (Fase 5).

Las fichas de falla viven en los manuales de los fabricantes (TX350-D para las
bombas, FC48-3 para los filtros). Cada ficha es una tabla de "Aspecto |
Detalle" con el síntoma, la firma en sensores, la firma en la recorrida, las
causas probables, cómo confirmar, la acción recomendada, el procedimiento, los
repuestos y si detiene el equipo.

El catálogo se arma del corpus que ya usa el RAG, así que cada falla viene con
la cita de su sección (``TX350-D §8.3``) y con la de la tabla de fallas
(``TX350-D §8.2``). Las revisiones vencidas (TX350-C) no entran: no son la
fuente.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from analisis.modelos import FallaCatalogo, RepuestoFicha
from analisis.sintomas import direcciones_de_texto
from rag.corpus import ChunkRag, DocumentoRag
from tools.manuales import normalizar, numero, tabla

# "TX-T01 Pérdida por empaquetadura" → código y título de la ficha.
_CODIGO_FICHA = re.compile(r"^(?P<codigo>[A-Z]{2,3}-T\d{2})\s+(?P<titulo>.+)$")
# "TX-EMP-KIT (1 kit)", "48 x FC-CART-25 (primario)", "TX-SELLO-CAR + ACE-ISO220 (38 L)".
_REPuesto = re.compile(
    r"(?:(?P<cantidad>[\d.,]+)\s*x\s*)?"
    r"(?P<codigo>[A-Z]{2,4}-[A-Z0-9]+(?:-[A-Z0-9]+)*)"
    r"\s*(?:\(\s*(?P<detalle>[^)]*)\))?"
)
# Etiquetas de la tabla "Aspecto | Detalle" de cada ficha.
_CAMPOS = {
    "sintoma": "sintoma",
    "firma en sensores": "firma_sensores",
    "firma en la recorrida": "firma_recorrida",
    "causas probables": "causas",
    "como confirmar": "como_confirmar",
    "accion recomendada": "accion",
    "procedimiento": "procedimiento",
    "repuestos": "repuestos",
}
# Tipo de equipo por manual: el prefijo del documento dice de qué equipo es.
_TIPO_POR_PREFIJO = {"TX": "bomba", "FC": "filtro"}


@dataclass(frozen=True)
class CatalogoFallas:
    """Las fallas que el manual describe, con su cita."""

    fallas: tuple[FallaCatalogo, ...]

    def __iter__(self):
        return iter(self.fallas)

    def __len__(self) -> int:
        return len(self.fallas)

    @property
    def codigos(self) -> tuple[str, ...]:
        return tuple(falla.codigo for falla in self.fallas)

    def por_codigo(self, codigo: str) -> FallaCatalogo | None:
        return next((falla for falla in self.fallas if falla.codigo == codigo), None)

    def por_cita(self, cita: str) -> FallaCatalogo | None:
        return next((falla for falla in self.fallas if falla.cita == cita), None)

    def para_tipo(self, tipo: str) -> tuple[FallaCatalogo, ...]:
        """Fallas del manual del tipo de equipo indicado (bomba, filtro...)."""
        return tuple(falla for falla in self.fallas if falla.tipo_equipo == tipo)

    @classmethod
    def desde_documentos(cls, documentos: list[DocumentoRag]) -> CatalogoFallas:
        return cls(tuple(_armar_catalogo(documentos)))


def _armar_catalogo(documentos: list[DocumentoRag]) -> list[FallaCatalogo]:
    fallas: list[FallaCatalogo] = []
    for documento in documentos:
        if not documento.vigente or documento.tipo != "manual_fabricante":
            continue
        tablas = _tablas_de_fallas(documento)
        for chunk in documento.chunks:
            if not chunk.vigente:
                continue
            falla = _parsear_ficha(documento, chunk, tablas)
            if falla is not None:
                fallas.append(falla)
    fallas.sort(key=lambda falla: falla.codigo)
    return fallas


def _tablas_de_fallas(documento: DocumentoRag) -> dict[str, tuple[str, str, str, str]]:
    """Filas de la tabla de fallas del documento: código → sintoma, causa, acción."""
    filas: dict[str, tuple[str, str, str, str]] = {}
    for chunk in documento.chunks:
        if not chunk.vigente:
            continue
        if normalizar(chunk.titulo or "") != "tabla de fallas":
            continue
        for fila in tabla(chunk.texto)[1:]:
            if len(fila) < 4 or not fila[0]:
                continue
            filas[fila[0].strip()] = (fila[1], fila[2], fila[3], chunk.cita)
    return filas


def _parsear_ficha(
    documento: DocumentoRag,
    chunk: ChunkRag,
    tablas: dict[str, tuple[str, str, str, str]],
) -> FallaCatalogo | None:
    coincidencia = _CODIGO_FICHA.match((chunk.titulo or "").strip())
    if not coincidencia:
        return None
    codigo = coincidencia.group("codigo")
    campos = _campos(chunk.texto)
    if not campos:
        return None
    sintoma = campos.get("sintoma", "")
    tabla_fila = tablas.get(codigo)
    return FallaCatalogo(
        codigo=codigo,
        titulo=coincidencia.group("titulo").strip(),
        documento=documento.clave,
        cita=chunk.cita or f"{documento.clave} §{chunk.seccion}",
        tipo_equipo=_tipo_de_documento(documento),
        sintoma=sintoma,
        firma_sensores=campos.get("firma_sensores", ""),
        firma_recorrida=campos.get("firma_recorrida", ""),
        causas=_causas(campos.get("causas", "")),
        como_confirmar=campos.get("como_confirmar", ""),
        accion=campos.get("accion", ""),
        procedimiento=campos.get("procedimiento") or None,
        repuestos=_repuestos(campos.get("repuestos", "")),
        detiene=_detiene(campos),
        es_seguridad="aviso de emergencia" in normalizar(campos.get("accion", "")),
        tabla_cita=tabla_fila[3] if tabla_fila else None,
        tabla_sintoma=tabla_fila[0] if tabla_fila else None,
        tabla_causa=tabla_fila[1] if tabla_fila else None,
        tabla_accion=tabla_fila[2] if tabla_fila else None,
        direcciones=tuple(_direcciones(sintoma, campos)),
    )


def _campos(texto: str) -> dict[str, str]:
    """Filas de la tabla "Aspecto | Detalle" de una ficha."""
    campos: dict[str, str] = {}
    for fila in tabla(texto):
        if len(fila) < 2:
            continue
        clave = normalizar(fila[0])
        if clave in _CAMPOS:
            campos[_CAMPOS[clave]] = fila[1].strip()
            continue
        if "detiene" in clave or "saca" in clave:
            campos["detiene"] = fila[1].strip()
    return campos


def _detiene(campos: dict[str, str]) -> bool | None:
    texto = normalizar(campos.get("detiene", ""))
    if not texto:
        return None
    if texto.startswith("no"):
        return False
    if texto.startswith("si"):
        return True
    return None


def _causas(texto: str) -> tuple[str, ...]:
    """``1) ...; 2) ...`` → las causas, en el orden del manual."""
    if not texto:
        return ()
    partes = re.split(r";\s*(?=\d+\))", texto)
    salida: list[str] = []
    for parte in partes:
        limpia = re.sub(r"^\d+\)\s*", "", parte.strip())
        if limpia:
            salida.append(limpia)
    return tuple(salida)


def _repuestos(texto: str) -> tuple[RepuestoFicha, ...]:
    """``TX-EMP-KIT (1 kit)`` → repuesto con su cantidad."""
    if not texto:
        return ()
    salida: list[RepuestoFicha] = []
    for coincidencia in _REPuesto.finditer(texto):
        codigo = coincidencia.group("codigo")
        detalle = (coincidencia.group("detalle") or "").strip()
        cantidad = numero(coincidencia.group("cantidad"))
        if cantidad is None:
            # La cantidad puede venir dentro del paréntesis:
            # "ACE-ISO220 (aprox. 8 L)" son 8 litros.
            indoor = re.match(r"(?:aprox\.?\s*)?([\d.,]+)\s*(.*)", detalle)
            if indoor:
                cantidad = numero(indoor.group(1))
                detalle = indoor.group(2).strip() or detalle
        if cantidad is None or cantidad <= 0:
            # Sin número explícito el manual deja la cantidad a criterio.
            cantidad = 1.0
        salida.append(
            RepuestoFicha(codigo=codigo, cantidad=cantidad, texto=detalle or codigo)
        )
    return tuple(salida)


def _tipo_de_documento(documento: DocumentoRag) -> str:
    prefijo = documento.clave[:2].upper()
    return _TIPO_POR_PREFIJO.get(prefijo, "equipo")


def _direcciones(sintoma: str, campos: dict[str, str]) -> list[tuple[str, str]]:
    """En qué sentido el manual dice que se desvía cada variable.

    Se lee cláusula por cláusula: "Presión diferencial alta y en aumento" es una
    desviación alta del diferencial, mientras que "Caída brusca de la presión
    diferencial" es una baja. Sin esto, "diferencial alto" y "diferencial
    bajo" serían indistinguibles.
    """
    texto = " ".join(
        parte
        for parte in (
            sintoma,
            campos.get("firma_sensores", ""),
            campos.get("firma_recorrida", ""),
        )
        if parte
    )
    return direcciones_de_texto(texto)
