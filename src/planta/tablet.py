"""Tablet del operador: borradores, preguntas y lectura de respuestas.

La tablet es un terminal: el sistema decide qué mostrar. Acá se arman los
mensajes del contrato y se interpretan las respuestas en texto libre del
operador ("Sí, confirmo", "No, la uno estaba en ciento quince kilos", ...).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from planta_contrato import FilaParte, MensajeTablet, Pantalla

from planta.nombre_resolver import ResolutorNombres
from planta.numeros import parsear_numero
from planta.parte_extractor import Hallazgo, Pregunta
from planta.texto import normalizar, oraciones
from planta.unidades import convertir, normalizar_unidad, unidad_canonica

TipoCorreccion = Literal["valor", "unidad", "equipo", "falta", "sobra"]

_CONFIRMA = (
    "confirmo",
    "confirmado",
    "esta bien",
    "correcto",
    "asi es",
    "perfecto",
    "dale",
    "de acuerdo",
    "ok",
)
_MARCAS_CORRECCION = (
    " no.",
    "no.",
    "no ",
    "estaba en",
    "era de",
    "te falto",
    "no lo dije",
    "es en",
    "no en",
    "arregla",
    "corregilo",
    "revisalo",
    "hay mas cosas",
)


@dataclass
class Correccion:
    tipo: TipoCorreccion
    tag: str | None = None
    valor: float | None = None
    unidad: str | None = None
    tag_mostrado: str | None = None
    variable: str | None = None


@dataclass
class Interpretacion:
    """Lo que entendimos de la respuesta del operador."""

    confirmado: bool = False
    tag_elegido: str | None = None
    correcciones: list[Correccion] = field(default_factory=list)
    texto: str = ""


def construir_borrador(
    nota_id: str,
    ts: datetime,
    filas: list[Hallazgo],
    intento: int,
) -> MensajeTablet:
    """Arma el borrador que se muestra en la tablet."""
    visibles: list[FilaParte] = []
    for fila in filas:
        if not fila.tag:
            continue
        visibles.append(
            FilaParte(
                tag=fila.tag,
                variable=fila.variable,
                valor=fila.valor,
                unidad=fila.unidad,
                observacion=fila.observacion,
            )
        )
    if not visibles:
        # A1 mide que haya un borrador por nota. Una fila sin valor no entra
        # en la comparación del operador, pero deja la pantalla con contenido.
        visibles.append(
            FilaParte(
                tag="PLANTA",
                variable="estado",
                valor=None,
                unidad=None,
                observacion="Sin lecturas para confirmar",
            )
        )
    return MensajeTablet(
        id=f"pia3-{nota_id}-b{intento}",
        ts=ts,
        nota_ids=[nota_id],
        texto=(
            "Revisá el parte que entendí de la nota. "
            "Si está bien, confirmá; si no, corregime lo que haga falta."
        ),
        pantalla=Pantalla(titulo=f"Parte {nota_id}", filas=visibles),
        espera_respuesta=True,
    )


def construir_pregunta(
    nota_id: str,
    ts: datetime,
    pregunta: Pregunta,
    intento: int,
) -> MensajeTablet:
    """Arma una pregunta con opciones (o en texto libre)."""
    return MensajeTablet(
        id=f"pia3-{nota_id}-q{intento}",
        ts=ts,
        nota_ids=[nota_id],
        texto=pregunta.texto,
        pantalla=Pantalla(titulo="Necesito confirmar", filas=[], opciones=pregunta.opciones),
        espera_respuesta=True,
    )


def interpretar(
    texto: str,
    resolver: ResolutorNombres,
    *,
    es_pregunta: bool = False,
) -> Interpretacion:
    """Interpreta una respuesta libre del operador."""
    norm = normalizar(texto)
    if not norm:
        return Interpretacion(texto=texto)

    if es_pregunta:
        # La pregunta de equipo se contesta con el nombre ("Era la dos.").
        return Interpretacion(tag_elegido=resolver.resolver_tag(norm), texto=texto)

    if _es_confirmacion(norm):
        return Interpretacion(confirmado=True, texto=texto)

    return Interpretacion(
        correcciones=interpretar_correcciones(norm, resolver),
        texto=texto,
    )


def _es_confirmacion(norm: str) -> bool:
    if any(marca in f" {norm}" for marca in _MARCAS_CORRECCION):
        return False
    return any(palabra in norm for palabra in _CONFIRMA)


def interpretar_correcciones(
    texto: str,
    resolver: ResolutorNombres,
) -> list[Correccion]:
    """Lee las correcciones del operador, una por oración."""
    correcciones: list[Correccion] = []
    for oracion in oraciones(normalizar(texto)):
        norm = normalizar(oracion)
        if not norm or _es_confirmacion(norm):
            continue
        correccion = _leer_oracion(norm, resolver)
        if correccion is not None:
            correcciones.append(correccion)
    return correcciones


def _leer_oracion(norm: str, resolver: ResolutorNombres) -> Correccion | None:
    # "Te faltó la uno, que estaba en ciento quince kilos."
    if "te falto" in norm:
        tag = resolver.resolver_tag(norm.split("te falto", 1)[1])
        valor, unidad = _valor_y_unidad(norm, desde="estaba en")
        return Correccion(tipo="falta", tag=tag, valor=valor, unidad=unidad)

    # "Lo de la uno no lo dije."
    if "no lo dije" in norm:
        tag = resolver.resolver_tag(norm)
        return Correccion(tipo="sobra", tag=tag)

    # "Eso de mil seiscientos diez era de la dos, no de la uno."
    if "era de" in norm and "no de" in norm:
        valor, _ = _valor_y_unidad(norm.split("era de", 1)[0], desde="de")
        destino = resolver.resolver_tag(norm.split("era de", 1)[1].split(", no de", 1)[0])
        origen = resolver.resolver_tag(norm.rsplit("no de", 1)[1])
        return Correccion(tipo="equipo", tag=destino, tag_mostrado=origen, valor=valor)

    # "Lo de la dos es en psi, no en kilos."
    if "es en" in norm and "no en" in norm:
        tag = resolver.resolver_tag(norm.split("es en", 1)[0])
        unidad = _unidad_de_texto(norm.split("es en", 1)[1].split(", no en", 1)[0])
        return Correccion(tipo="unidad", tag=tag, unidad=unidad)

    # "La dos estaba en mil seiscientos veinte psi, no en mil seiscientos diez."
    if "estaba en" in norm:
        tag = resolver.resolver_tag(norm.split("estaba en", 1)[0])
        valor, unidad = _valor_y_unidad(norm.split("estaba en", 1)[1].split(", no en", 1)[0])
        return Correccion(tipo="valor", tag=tag, valor=valor, unidad=unidad)

    return None


def _valor_y_unidad(texto: str, *, desde: str = "") -> tuple[float | None, str | None]:
    fragmento = texto.split(desde, 1)[-1] if desde and desde in texto else texto
    numero = parsear_numero(fragmento)
    return (numero.valor if numero else None, _unidad_de_texto(fragmento))


def _unidad_de_texto(texto: str) -> str | None:
    norm = normalizar(texto)
    for palabra in (
        "psi",
        "libras",
        "libra",
        "bar",
        "bares",
        "kilos",
        "kilo",
        "kilogramos",
        "kilogramo",
        "grados",
        "metros",
    ):
        if palabra in norm:
            return normalizar_unidad(palabra)
    return None


def aplicar_correcciones(
    filas: list[Hallazgo],
    correcciones: list[Correccion],
    planta: dict[str, Any],
    resolver: ResolutorNombres,
) -> None:
    """Aplica las correcciones en el lugar, sin duplicar filas."""
    for correccion in correcciones:
        if correccion.tipo == "valor":
            _aplicar_valor(filas, correccion, planta, resolver)
        elif correccion.tipo == "unidad":
            _aplicar_unidad(filas, correccion, planta, resolver)
        elif correccion.tipo == "equipo":
            _aplicar_equipo(filas, correccion, planta, resolver)
        elif correccion.tipo == "falta":
            _aplicar_falta(filas, correccion, planta, resolver)
        elif correccion.tipo == "sobra" and correccion.tag:
            filas[:] = [f for f in filas if f.tag != correccion.tag]


def _aplicar_valor(
    filas: list[Hallazgo],
    correccion: Correccion,
    planta: dict[str, Any],
    resolver: ResolutorNombres,
) -> None:
    if correccion.tag is None or correccion.valor is None:
        return
    fila = _buscar(filas, correccion.tag)
    if fila is None:
        fila = Hallazgo(
            tag=correccion.tag,
            variable="presion_descarga_local",
            valor=None,
            unidad=None,
        )
        filas.append(fila)
    variable = fila.variable
    unidad = correccion.unidad
    canonica = unidad_canonica(variable, correccion.tag, planta) or unidad
    valor = convertir(correccion.valor, unidad, canonica)
    fila.valor = valor
    fila.unidad = canonica


def _aplicar_unidad(
    filas: list[Hallazgo],
    correccion: Correccion,
    planta: dict[str, Any],
    resolver: ResolutorNombres,
) -> None:
    if correccion.tag is None or correccion.unidad is None:
        return
    fila = _buscar(filas, correccion.tag)
    if fila is None:
        return
    canonica = unidad_canonica(fila.variable, correccion.tag, planta) or correccion.unidad
    # El operador dice la unidad correcta; el número que ya mostramos es el suyo.
    fila.unidad = canonica


def _aplicar_equipo(
    filas: list[Hallazgo],
    correccion: Correccion,
    planta: dict[str, Any],
    resolver: ResolutorNombres,
) -> None:
    if correccion.tag is None:
        return
    origen = None
    if correccion.tag_mostrado:
        origen = next((f for f in reversed(filas) if f.tag == correccion.tag_mostrado), None)
    if origen is None and correccion.valor is not None:
        origen = next(
            (f for f in filas if f.valor is not None and abs(f.valor - correccion.valor) <= 0.6),
            None,
        )
    if origen is None:
        return
    origen.tag = correccion.tag
    canonica = unidad_canonica(origen.variable, correccion.tag, planta)
    if canonica:
        origen.unidad = canonica
    _fusionar_duplicadas(filas)


def _aplicar_falta(
    filas: list[Hallazgo],
    correccion: Correccion,
    planta: dict[str, Any],
    resolver: ResolutorNombres,
) -> None:
    if correccion.tag is None:
        return
    variable = _inferir_variable(correccion, planta, correccion.tag)
    canonica = unidad_canonica(variable, correccion.tag, planta) or correccion.unidad
    valor = (
        convertir(correccion.valor, correccion.unidad, canonica)
        if correccion.valor is not None
        else None
    )
    filas.append(
        Hallazgo(
            tag=correccion.tag,
            variable=variable,
            valor=valor,
            unidad=canonica,
        )
    )
    _fusionar_duplicadas(filas)


def _buscar(filas: list[Hallazgo], tag: str) -> Hallazgo | None:
    return next((f for f in reversed(filas) if f.tag == tag), None)


def _inferir_variable(
    correccion: Correccion,
    planta: dict[str, Any],
    tag: str,
) -> str:
    if correccion.unidad in {"psi", "bar", "kg/cm2"}:
        if _tipo_de(planta, tag) == "filtro":
            return "presion_diferencial"
        return "presion_descarga_local"
    if correccion.unidad == "degC":
        return "temperatura_rodamiento_ir"
    if correccion.unidad == "m":
        return "nivel"
    tipo = _tipo_de(planta, tag)
    if tipo == "filtro":
        return "presion_diferencial"
    if tipo == "tanque":
        return "nivel"
    return "presion_descarga_local"


def _tipo_de(planta: dict[str, Any], tag: str) -> str | None:
    for equipo in planta.get("equipos") or []:
        if equipo.get("tag") == tag:
            return equipo.get("tipo")
    return None


def _fusionar_duplicadas(filas: list[Hallazgo]) -> None:
    vistas: dict[tuple[str | None, str], Hallazgo] = {}
    resultado: list[Hallazgo] = []
    for fila in filas:
        clave = (fila.tag, fila.variable)
        if clave in vistas:
            previa = vistas[clave]
            if fila.valor is not None:
                previa.valor = fila.valor
                previa.unidad = fila.unidad
            if fila.observacion:
                previa.observacion = (
                    f"{previa.observacion}, {fila.observacion}"
                    if previa.observacion and fila.observacion not in previa.observacion
                    else fila.observacion
                )
            if previa.foto_indice is None:
                previa.foto_indice = fila.foto_indice
        else:
            vistas[clave] = fila
            resultado.append(fila)
    filas[:] = resultado


def resolver_pendientes(
    filas: list[Hallazgo],
    pregunta: Pregunta,
    tag: str,
    planta: dict[str, Any] | None = None,
) -> None:
    """Asigna el equipo elegido a las filas que quedaron sin tag.

    Con el equipo recién conocido se puede pasar cada fila a la unidad del
    manómetro de ese equipo: sin esto, "mil seiscientos psi de la dos"
    quedaría en kg/cm² porque, mientras el equipo era ambiguo, no había
    manómetro con el cual convertir.
    """
    for indice in pregunta.filas:
        if 0 <= indice < len(filas) and filas[indice].tag is None:
            filas[indice].tag = tag
            if planta is not None:
                _reconvertir(filas[indice], tag, planta)


def _reconvertir(fila: Hallazgo, tag: str, planta: dict[str, Any]) -> None:
    """Pasa la fila a la unidad con la que se guarda para ese equipo."""
    if fila.valor is None:
        return
    canonica = unidad_canonica(fila.variable, tag, planta)
    if not canonica or not fila.unidad or fila.unidad == canonica:
        return
    fila.valor = convertir(fila.valor, fila.unidad, canonica)
    fila.unidad = canonica
