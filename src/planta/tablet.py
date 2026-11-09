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
from planta.parte_extractor import Hallazgo, Pregunta, recalcular_observacion
from planta.texto import normalizar, oraciones
from planta.unidades import convertir, unidad_canonica, unidad_de_frase

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
    planta: dict[str, Any] | None = None,
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
        correcciones=interpretar_correcciones(norm, resolver, planta=planta),
        texto=texto,
    )


def _es_confirmacion(norm: str) -> bool:
    if any(marca in f" {norm}" for marca in _MARCAS_CORRECCION):
        return False
    return any(palabra in norm for palabra in _CONFIRMA)


def interpretar_correcciones(
    texto: str,
    resolver: ResolutorNombres,
    *,
    planta: dict[str, Any] | None = None,
) -> list[Correccion]:
    """Lee las correcciones del operador, una por oración."""
    correcciones: list[Correccion] = []
    for oracion in oraciones(normalizar(texto)):
        norm = normalizar(oracion)
        if not norm or _es_confirmacion(norm):
            continue
        correccion = _leer_oracion(norm, resolver, planta)
        if correccion is not None:
            correcciones.append(correccion)
    return correcciones


def _leer_oracion(
    norm: str,
    resolver: ResolutorNombres,
    planta: dict[str, Any] | None = None,
) -> Correccion | None:
    # "Te faltó la uno, que estaba en ciento quince kilos."
    if "te falto" in norm:
        tag = resolver.resolver_tag(norm.split("te falto", 1)[1])
        valor, unidad = _valor_y_unidad(norm, desde="estaba en")
        correccion = Correccion(tipo="falta", tag=tag, valor=valor, unidad=unidad)
        return _con_variable(correccion, planta)

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
        # La unidad que mostramos (la que está después de "no en") es la que
        # dice qué fila hay que corregir.
        mostrada = _unidad_de_texto(norm.rsplit("no en", 1)[1])
        correccion = Correccion(tipo="unidad", tag=tag, unidad=unidad)
        correccion.variable = _variable_de_unidad(mostrada, planta, tag)
        return correccion

    # "La dos estaba en mil seiscientos veinte psi, no en mil seiscientos diez."
    if "estaba en" in norm:
        tag = resolver.resolver_tag(norm.split("estaba en", 1)[0])
        valor, unidad = _valor_y_unidad(norm.split("estaba en", 1)[1].split(", no en", 1)[0])
        correccion = Correccion(tipo="valor", tag=tag, valor=valor, unidad=unidad)
        return _con_variable(correccion, planta)

    return None


def _con_variable(
    correccion: Correccion,
    planta: dict[str, Any] | None,
) -> Correccion:
    """Le pone a la corrección la variable que el operador está corrigiendo."""
    if correccion.tag is None:
        return correccion
    correccion.variable = _variable_de_unidad(correccion.unidad, planta, correccion.tag)
    return correccion


def _variable_de_unidad(
    unidad: str | None,
    planta: dict[str, Any] | None,
    tag: str | None,
) -> str:
    """Variable que una unidad corresponde, para un equipo."""
    if unidad in {"psi", "bar", "kg/cm2"}:
        if planta is not None and tag and _tipo_de(planta, tag) == "filtro":
            return "presion_diferencial"
        return "presion_descarga_local"
    if unidad == "degC":
        return "temperatura_rodamiento_ir"
    if unidad == "m":
        return "nivel"
    if unidad == "gotas/min":
        return "goteo_empaquetadura"
    if unidad == "m3/d":
        return "caudal"
    if planta is None or not tag:
        return "presion_descarga_local"
    return _inferir_variable(Correccion(tipo="valor", tag=tag, unidad=unidad), planta, tag)


def _valor_y_unidad(texto: str, *, desde: str = "") -> tuple[float | None, str | None]:
    fragmento = texto.split(desde, 1)[-1] if desde and desde in texto else texto
    numero = parsear_numero(fragmento)
    return (numero.valor if numero else None, _unidad_de_texto(fragmento))


def _unidad_de_texto(texto: str) -> str | None:
    """La unidad que el operador dijo en el fragmento.

    Se leen todas las unidades de la planta, no sólo las de presión: si una
    corrección en gotas por minuto llega sin unidad, el sistema no sabe qué
    fila corregir y la escribe sobre la presión de descarga.
    """
    return unidad_de_frase(texto)


def aplicar_correcciones(
    filas: list[Hallazgo],
    correcciones: list[Correccion],
    planta: dict[str, Any],
    resolver: ResolutorNombres,
    *,
    afirmados: set[tuple[str | None, str]] | None = None,
) -> list[tuple[str | None, str]]:
    """Aplica las correcciones en el lugar, sin duplicar filas.

    Devuelve las lecturas que el operador confirmó o corrigió (equipo y
    variable). Con esa lista se sabe a qué lectura se refiere una negación:
    "lo de la uno no lo dije" niega una lectura, no todas las del equipo.
    """
    tocadas: list[tuple[str | None, str]] = []
    for correccion in correcciones:
        if correccion.tipo == "valor":
            fila = _aplicar_valor(filas, correccion, planta, resolver)
        elif correccion.tipo == "unidad":
            fila = _aplicar_unidad(filas, correccion, planta, resolver)
        elif correccion.tipo == "equipo":
            fila = _aplicar_equipo(filas, correccion, planta, resolver)
        elif correccion.tipo == "falta":
            fila = _aplicar_falta(filas, correccion, planta, resolver)
        elif correccion.tipo == "sobra" and correccion.tag:
            fila = _aplicar_sobra(filas, correccion, planta, afirmados or set())
        else:
            fila = None
        if fila is not None:
            tocadas.append((fila.tag, fila.variable))
    return tocadas


def _aplicar_valor(
    filas: list[Hallazgo],
    correccion: Correccion,
    planta: dict[str, Any],
    resolver: ResolutorNombres,
) -> Hallazgo | None:
    if correccion.tag is None or correccion.valor is None:
        return None
    fila = _buscar_para_correccion(filas, correccion)
    if fila is None:
        fila = Hallazgo(
            tag=correccion.tag,
            variable=correccion.variable or "presion_descarga_local",
            valor=None,
            unidad=None,
        )
        filas.append(fila)
    variable = fila.variable
    canonica = unidad_canonica(variable, correccion.tag, planta) or correccion.unidad
    valor = convertir(correccion.valor, correccion.unidad, canonica)
    fila.valor = valor
    fila.unidad = canonica
    # La observación que se había derivado del valor viejo ya no corresponde:
    # un diferencial que el operador corrigió de 4 a 0,4 no es "diferencial
    # alto" y la bomba no se tiene que escalar.
    recalcular_observacion(fila)
    return fila


def _aplicar_unidad(
    filas: list[Hallazgo],
    correccion: Correccion,
    planta: dict[str, Any],
    resolver: ResolutorNombres,
) -> Hallazgo | None:
    if correccion.tag is None or correccion.unidad is None:
        return None
    fila = _buscar_para_correccion(filas, correccion)
    if fila is None:
        return None
    canonica = unidad_canonica(fila.variable, correccion.tag, planta) or correccion.unidad
    # El operador dice la unidad correcta; el número que ya mostramos es el suyo.
    fila.unidad = canonica
    return fila


def _aplicar_equipo(
    filas: list[Hallazgo],
    correccion: Correccion,
    planta: dict[str, Any],
    resolver: ResolutorNombres,
) -> Hallazgo | None:
    if correccion.tag is None:
        return None
    origen = _buscar_para_mover(filas, correccion)
    if origen is None:
        return None
    # El equipo cambia: también cambian la variable que se está midiendo y la
    # unidad con la que se guarda. Sin convertir el valor, "1600 psi" que
    # pasa de la dos (psi) a la uno (kg/cm2) queda como "1600 kg/cm2", que no
    # es lo mismo: el operador vuelve a corregir y el borrador nunca converge.
    variable = _variable_del_equipo(origen.variable, correccion.tag, planta)
    canonica = unidad_canonica(variable, correccion.tag, planta)
    if canonica and origen.valor is not None and origen.unidad:
        origen.valor = convertir(origen.valor, origen.unidad, canonica)
    origen.variable = variable
    origen.tag = correccion.tag
    if canonica:
        origen.unidad = canonica
    # El valor y la variable cambiaron: la observación se vuelve a derivar.
    recalcular_observacion(origen)
    _fusionar_duplicadas(filas)
    return next(
        (f for f in filas if f.tag == correccion.tag and f.variable == variable),
        origen,
    )


def _aplicar_falta(
    filas: list[Hallazgo],
    correccion: Correccion,
    planta: dict[str, Any],
    resolver: ResolutorNombres,
) -> Hallazgo | None:
    if correccion.tag is None:
        return None
    # Si la corrección ya trae la variable (la inferida de la unidad que dijo
    # el operador), esa manda: inferirla otra vez desde el equipo caía la
    # lectura en la fila de la presión de descarga.
    variable = correccion.variable or _inferir_variable(correccion, planta, correccion.tag)
    canonica = unidad_canonica(variable, correccion.tag, planta) or correccion.unidad
    valor = (
        convertir(correccion.valor, correccion.unidad, canonica)
        if correccion.valor is not None
        else None
    )
    fila = Hallazgo(
        tag=correccion.tag,
        variable=variable,
        valor=valor,
        unidad=canonica,
    )
    filas.append(fila)
    _fusionar_duplicadas(filas)
    return next((f for f in filas if f.tag == fila.tag and f.variable == fila.variable), fila)


def _aplicar_sobra(
    filas: list[Hallazgo],
    correccion: Correccion,
    planta: dict[str, Any],
    afirmados: set[tuple[str | None, str]],
) -> Hallazgo | None:
    """Saca **una** lectura del equipo, no todas.

    El operador niega una lectura ("Lo de la uno no lo dije"), no el equipo
    entero. Si el equipo tiene varias lecturas, se saca la que el operador no
    afirmó en la conversación; si todas están afirmadas, la última.
    """
    candidatas = [f for f in filas if f.tag == correccion.tag]
    if not candidatas:
        return None
    if len(candidatas) == 1:
        filas.remove(candidatas[0])
        return candidatas[0]
    no_afirmadas = [f for f in candidatas if (f.tag, f.variable) not in afirmados]
    removida = (no_afirmadas or candidatas)[-1]
    filas.remove(removida)
    return removida


def _buscar_para_correccion(
    filas: list[Hallazgo],
    correccion: Correccion,
) -> Hallazgo | None:
    """La fila que una corrección del operador está hablando.

    Se busca primero por variable (la lectura exacta), después por unidad
    (una lectura del mismo tipo) y sólo al final, si no se sabe qué variable
    es, por equipo. Buscar sólo por equipo mezcla las lecturas: un equipo
    puede tener la presión de descarga y la temperatura del rodamiento, y
    corregir una no es corregir la otra.
    """
    if correccion.tag is None:
        return None
    candidatas = [f for f in filas if f.tag == correccion.tag]
    if not candidatas:
        return None
    if correccion.variable:
        exacta = next(
            (f for f in reversed(candidatas) if f.variable == correccion.variable),
            None,
        )
        if exacta is not None:
            return exacta
    if correccion.unidad:
        misma = next((f for f in reversed(candidatas) if f.unidad == correccion.unidad), None)
        if misma is not None:
            return misma
        familia = _familia_unidad(correccion.unidad)
        if familia:
            misma = next(
                (f for f in reversed(candidatas) if _familia_unidad(f.unidad) == familia),
                None,
            )
            if misma is not None:
                return misma
    if correccion.variable:
        # Se sabe qué variable se corrige y ese equipo no la tiene: la
        # lectura se crea, no se pisa la de otra variable.
        return None
    return candidatas[-1]


def _buscar_para_mover(
    filas: list[Hallazgo],
    correccion: Correccion,
) -> Hallazgo | None:
    """La lectura que el operador dice que es de otro equipo."""
    candidatas: list[Hallazgo] = []
    if correccion.tag_mostrado:
        candidatas = [f for f in filas if f.tag == correccion.tag_mostrado]
    if correccion.valor is not None:
        con_valor = next(
            (
                f
                for f in candidatas
                if _mismo_valor(f.valor, f.unidad, correccion.valor, correccion.unidad)
            ),
            None,
        )
        if con_valor is not None:
            return con_valor
        con_valor = next(
            (
                f
                for f in filas
                if _mismo_valor(f.valor, f.unidad, correccion.valor, correccion.unidad)
            ),
            None,
        )
        if con_valor is not None:
            return con_valor
    if candidatas:
        return candidatas[-1]
    if correccion.valor is not None:
        return next(
            (f for f in filas if f.valor is not None and abs(f.valor - correccion.valor) <= 0.6),
            None,
        )
    return None


def _familia_unidad(unidad: str | None) -> str | None:
    """Familia de una unidad: presión, temperatura, longitud, caudal."""
    if unidad in {"kg/cm2", "psi", "bar"}:
        return "presion"
    if unidad == "degC":
        return "temperatura"
    if unidad == "m":
        return "longitud"
    if unidad == "m3/d":
        return "caudal"
    if unidad == "gotas/min":
        return "goteo"
    if unidad == "mm/s":
        return "vibracion"
    return None


def _mismo_valor(
    valor_a: float | None,
    unidad_a: str | None,
    valor_b: float | None,
    unidad_b: str | None,
) -> bool:
    """¿Son la misma lectura, aunque estén en unidades distintas?

    Es la misma tolerancia que usa el operador de la planta: 2 % para las
    presiones (1600 psi y 112 kg/cm² son lo mismo) y 1,5 % para el resto.
    """
    if valor_a is None or valor_b is None:
        return False
    if _familia_unidad(unidad_a) == "presion" and _familia_unidad(unidad_b) == "presion":
        a = valor_a * convertir(1.0, unidad_a, "kg/cm2")
        b = valor_b * convertir(1.0, unidad_b, "kg/cm2")
        return abs(a - b) <= max(0.6, abs(b) * 0.02)
    if unidad_a and unidad_b and unidad_a != unidad_b:
        return False
    return abs(valor_a - valor_b) <= max(0.051, abs(valor_b) * 0.015)


def _variable_del_equipo(
    variable: str,
    tag: str,
    planta: dict[str, Any],
) -> str:
    """La variable que se mide, vista desde el equipo al que se mueve."""
    tipo = _tipo_de(planta, tag)
    if variable == "presion_descarga_local" and tipo == "filtro":
        return "presion_diferencial"
    if variable == "presion_diferencial" and tipo == "bomba":
        return "presion_descarga_local"
    return variable


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
    if correccion.unidad == "gotas/min":
        return "goteo_empaquetadura"
    if correccion.unidad == "m3/d":
        return "caudal"
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
            # La fila que se fusiona trae el valor que manda: la observación
            # derivada se vuelve a calcular con ese valor.
            recalcular_observacion(previa)
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
    # El valor cambió de unidad: la observación que dependía de él también.
    recalcular_observacion(fila)
