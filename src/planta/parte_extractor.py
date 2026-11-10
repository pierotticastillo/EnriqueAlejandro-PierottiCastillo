"""Extractor determinista de ``FilaParte`` a partir de una nota hablada.

No usa LLM: reconoce las formas en que el operador dicta en esta planta
("la del medio está en mil seiscientos diez de descarga", "el rodamiento me
dio cincuenta y un grados con la pistola", "el filtro B me dio uno coma tres
de diferencial", ...). Cuando una mención es ambigua ("la bomba") o un valor
no es físicamente posible ("dieciocho" de diferencial), deja una pregunta
para la tablet en lugar de inventar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from deteccion.seguridad import detectar as _detectar_seguridad
from planta.nombre_resolver import Mencion, ResolutorNombres
from planta.numeros import NumeroDicho, parsear_numero
from planta.texto import clausulas, normalizar, oraciones
from planta.unidades import convertir, normalizar_unidad, unidad_canonica

CampoPregunta = Literal["equipo", "valor"]

# Los síntomas que el sistema entiende, escritos como los dice el técnico.
#
# Van al prompt del ASR: el decodificador garbla lo que no conoce ("golpe
# teometálico" por "golpeteo metálico", "competeo metálico" por lo mismo) y
# una observación garblada no es una anomalía: el equipo no se escala y el
# evento sale como "no hay novedad" aunque la verdad de campo diga otra
# cosa. Cada síntoma de esta lista es el que produce ``_observacion``.
SINTOMAS_HABLADOS = (
    "golpeteo metálico",
    "piedras adentro",
    "ruido raro de rodamiento",
    "ruido de rodamiento",
    "correas flojas",
    "correas rotas",
    "correas desalineadas",
    "correas en mal estado",
    "nivel de aceite bajo",
    "rebalse a pileta",
    "disparada no arranca",
    "pérdida por empaquetadura",
    "goteo por empaquetadura",
)


@dataclass
class Hallazgo:
    """Una fila del parte antes de validar contra el contrato."""

    tag: str | None
    variable: str
    valor: float | None
    unidad: str | None
    observacion: str | None = None
    texto_fuente: str | None = None
    foto_indice: int | None = None


@dataclass
class Pregunta:
    """Lo que hay que preguntarle al operador antes de guardar."""

    campo: CampoPregunta
    texto: str
    opciones: list[str] = field(default_factory=list)
    filas: list[int] = field(default_factory=list)
    variable: str | None = None
    valor_dicho: float | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class Extraccion:
    filas: list[Hallazgo] = field(default_factory=list)
    preguntas: list[Pregunta] = field(default_factory=list)
    seguridad: str | None = None
    sin_novedad: bool = False

    @property
    def tiene_ambiguedad(self) -> bool:
        return bool(self.preguntas)


# Palabras que delatan cada variable local.
_CUE_DIFERENCIAL = ("diferencial",)
_CUE_DESCARGA = ("descarga", "descarga local")
_CUE_RODAMIENTO = ("rodamiento",)
_CUE_MOTOR = ("motor",)
_CUE_NIVEL = ("nivel", "metros", "metro")
_CUE_CAUDAL = ("caudal",)
_CUE_GOTEO = ("gotea", "goteando", "pierde", "chorrea", "empaquetadura")
_CUE_ACEITE = ("aceite", "visor")
_CUE_CORREAS = ("correa", "correas")
_CUE_RUIDO = ("golpeteo", "piedras adentro", "ruido raro", "ruido")
_CUE_ESTADO = (
    "parada",
    "parado",
    "disparada",
    "disparado",
    "no arranca",
    "mantenimiento",
)
_CUE_REBALSE = ("rebals", "rebalse")


# El técnico dice el síntoma con su propio verbo ("hace un golpeteo
# metálico", "tiene un ruido raro de rodamiento", "chorrea por la
# empaquetadura"). La frase se busca con ese verbo delante para poder
# encontrar **dónde arranca** cuando el ASR juntó dos ideas en una sola
# cláusula (ver ``_separar_sintoma``).
_FRASE_SINTOMA = re.compile(
    r"\b(?:hace|tiene|suena|sigue|chorrea|gotea|pierde)\b"
    r"(?:\s+\w+){0,4}?"
    r"\s+\b(?:g\w{0,2}\s*peteo|pie\s*dras|ru\s*ido|cor\s*reas?|em\s*paquetadura|a\s*ceite)\b"
)


def _separar_sintoma(norm: str) -> list[str]:
    """Parte una cláusula que junta una medición con un síntoma.

    El técnico dice las dos ideas separadas por una coma ("el rodamiento me
    dio 61 grados con la pistola, hace un golpeteo metálico"). Cuando
    el ASR pierde esa coma, las dos ideas quedan en la misma cláusula y pasa
    algo que no puede pasar: el síntoma se perdía (la fila de ruido no se
    publicaba, así que el equipo no se escalaba) y la fila de la medición
    tomaba el número del artículo ("hace un golpeteo metálico" se
    leía como 1). Se parte donde arranca la frase del síntoma y cada idea va
    en su fila, en el orden en que se dijeron.
    """
    coincidencia = _FRASE_SINTOMA.search(norm)
    if coincidencia is None:
        return [norm]

    inicio = coincidencia.start()
    corte = len(norm)
    for separador in ",.;:":
        posicion = norm.find(separador, inicio)
        if posicion != -1:
            corte = min(corte, posicion)

    antes = norm[:inicio].strip(" ,")
    sintoma = norm[inicio:corte].strip(" ,")
    despues = norm[corte:].strip(" ,")
    partes = [parte for parte in (antes, sintoma, despues) if parte]
    return partes if len(partes) > 1 else [norm]


def _menciona(norm: str, cues: tuple[str, ...]) -> bool:
    """Si el texto dice alguna de las palabras, **completa**.

    "manometro" no es un "metro": el manómetro es el instrumento, no la
    altura del tanque. Con la búsqueda por substring, "le saqué foto al
    manómetro" se tomaba como una medición de nivel.

    Y hay un segundo caso: el ASR a veces **parte una palabra en dos**
    ("gol peteo metálico" por "golpeteo metálico", "em paquetadura" por
    "empaquetadura"). Una observación garblada no es una anomalía: el
    síntoma se perdía y el equipo no se escalaba aunque el técnico lo
    hubiera dicho. Por eso, en las palabras largas, también se acepta que
    tengan un espacio en el medio.
    """
    for cue in cues:
        if re.search(rf"(?<!\w){re.escape(cue)}(?!\w)", norm):
            return True
        if len(cue.replace(" ", "")) >= 6:
            partido = r"\s*".join(re.escape(letra) for letra in cue.replace(" ", ""))
            if re.search(rf"(?<!\w){partido}(?!\w)", norm):
                return True
    return False


#: Distancia de edición máxima para reconocer un síntoma que el ASR garbló.
#: Con dos alcanza: "gropeteo" por "golpeteo" y "competeo" por "golpeteo"
#: son el mismo garblado de siempre. Con tres, ya sería otra palabra y el
#: sistema no puede arriesgarse a inventar una anomalía.
_MAX_DISTANCIA = 2

#: Para las palabras cortas, un error alcanza: "correa" y "chorrea" están a
#: dos de distancia y son dos cosas distintas (una correa y un goteo).
_MAX_DISTANCIA_CORTA = 1

#: A partir de esta longitud, dos errores son tolerables.
_LONGITUD_LARGA = 8


def _familia_sintoma(norm: str) -> str | None:
    """Qué síntoma dice el texto: ``"ruido"``, ``"goteo"``, ``"correas"`` o ``None``.

    Primero se busca la coincidencia **exacta** (o con la palabra partida) en
    las tres familias: si el texto dice "correas", eso es una correa y no un
    goteo, aunque "correas" y "chorrea" se parezcan. Y sólo si el texto no
    dice ninguno de los síntomas, se prueba la coincidencia aproximada: ahí
    es donde entra el garblado del ASR ("gropeteo" por "golpeteo").
    """
    familias = (
        ("ruido", _CUE_RUIDO),
        ("goteo", _CUE_GOTEO),
        ("correas", _CUE_CORREAS),
    )
    for familia, cues in familias:
        if _menciona(norm, cues):
            return familia
    for familia, cues in familias:
        if _menciona_aproximada(norm, cues):
            return familia
    return None


def _menciona_sintoma(norm: str, cues: tuple[str, ...]) -> bool:
    """Si el texto dice alguno de los síntomas, aunque el ASR lo garble.

    Primero la coincidencia exacta (con la palabra partida) y, si no hay
    ninguna, la aproximada.
    """
    return _menciona(norm, cues) or _menciona_aproximada(norm, cues)


def _menciona_aproximada(norm: str, cues: tuple[str, ...]) -> bool:
    """Si alguna palabra del texto se parece a una de las pistas.

    Sólo para los síntomas: son palabras muy distintas entre sí y un par de
    letras cambiadas no puede cambiar el significado. En las demás
    (variables, valores, estados) la búsqueda sigue siendo exacta.

    Dos reglas la mantienenanya:

    * la palabra tiene que tener **la misma cantidad de letras**: un garblado
      no cambia el largo ("gropeteo" por "golpeteo"), pero "correas" y
      "chorrea" tienen largo distinto y son dos cosas distintas;
    * y la distancia es como mucho dos (una para las palabras cortas).
    """
    tokens = re.findall(r"\w+", norm)
    for cue in cues:
        if " " in cue or len(cue) < 6:
            continue
        maximo = (
            _MAX_DISTANCIA if len(cue) >= _LONGITUD_LARGA else _MAX_DISTANCIA_CORTA
        )
        for token in tokens:
            if len(token) != len(cue):
                continue
            if _distancia(token, cue) <= maximo:
                return True
    return False


def _distancia(uno: str, otro: str) -> int:
    """La distancia de edición (Damerau-Levenshtein).

    Cuenta la **transposición** como un error solo: "gropeteo" por
    "golpeteo" es el mismo error que el ASR comete siempre, y a dos letras
    de distancia ya no se sabe si es un garblado o otra palabra.
    """
    if uno == otro:
        return 0
    if not uno:
        return len(otro)
    if not otro:
        return len(uno)
    anterior = list(range(len(otro) + 1))
    for i, letra in enumerate(uno, start=1):
        actual = [i]
        for j, otra in enumerate(otro, start=1):
            coste = 0 if letra == otra else 1
            valor = min(
                actual[-1] + 1,
                anterior[j] + 1,
                anterior[j - 1] + coste,
            )
            if (
                i > 1
                and j > 1
                and letra == otro[j - 2]
                and uno[i - 2] == otra
            ):
                valor = min(valor, anterior[j - 2] + 1)
            actual.append(valor)
        anterior = actual
    return anterior[-1]

# Observaciones que se derivan del VALOR de la fila y no de lo que dijo el
# operador. Viven en una tabla para que la regla sea una sola: si el operador
# corrige el valor en la tablet, la observación se vuelve a derivar (si no, un
# diferencial que el operador corrigió de 4 a 0,4 sigue marcado como
# "diferencial alto" y se escala una bomba que está bien).
#
#   variable                (umbral, observación, el umbral cuenta)
_OBSERVACION_POR_VALOR = {
    "temperatura_rodamiento_ir": (75.0, "rodamiento caliente", False),
    "presion_diferencial": (1.5, "diferencial alto", True),
    # TX350-D §8.4: "Goteo por encima de 10 gotas/min en un prensaestopas" es
    # la firma en la recorrida de la falla F01 (§8.3: por encima de 30
    # gotas/min hay que cambiar la empaquetadura). El goteo normal es de hasta
    # 10 gotas/min. Sin esta regla, una corrección del operador que **crea**
    # la fila ("estaba en ciento cuarenta y dos gotas por minuto") quedaba
    # sin observación y el evento salía como "no hay novedad": el goteo
    # confirmado por el operador no escalaba.
    "goteo_empaquetadura": (10.0, "goteo por empaquetadura", False),
}

# Muletillas y palabras de enlace que no son una observación por sí solas.
_STOPWORDS = {
    "a",
    "ah",
    "al",
    "algo",
    "ademas",
    "aqui",
    "asi",
    "bien",
    "bueno",
    "con",
    "de",
    "despues",
    "dio",
    "el",
    "en",
    "es",
    "esta",
    "este",
    "estaba",
    "fue",
    "la",
    "las",
    "le",
    "listo",
    "lo",
    "los",
    "me",
    "mi",
    "no",
    "o",
    "para",
    "pero",
    "por",
    "que",
    "se",
    "si",
    "sin",
    "su",
    "tambien",
    "un",
    "una",
    "ver",
    "y",
    "ya",
}

_OBS_ESTADO = {
    "parada, en reserva": "parada, en reserva",
    "parada en reserva": "parada, en reserva",
    "parada por mantenimiento": "parada por mantenimiento",
    "disparada, no arranca": "disparada, no arranca",
    "disparada no arranca": "disparada, no arranca",
}


class ExtractorParte:
    """Extrae filas y preguntas de una nota."""

    def __init__(
        self,
        planta: dict[str, Any] | None = None,
        resolver: ResolutorNombres | None = None,
    ) -> None:
        self.planta = planta or {}
        self.resolver = resolver or ResolutorNombres(self.planta.get("equipos") or [])
        self._reset()

    def _reset(self) -> None:
        self.filas: list[Hallazgo] = []
        self.preguntas: list[Pregunta] = []
        self._fotos: list[str] = []
        self._foto_cursor = 0
        self._tag: str | None = None
        self._tipo: str | None = None
        self._pendiente: Pregunta | None = None
        self._fuente = ""

    def extraer(self, texto: str, fotos: list[str] | None = None) -> Extraccion:
        """Convierte el texto de la nota en filas del parte."""
        self._reset()
        self._fotos = list(fotos or [])
        self._fuente = texto.strip()
        norm = normalizar(texto)

        seguridad = _detectar_seguridad(norm)
        if seguridad:
            self._extraer_seguridad(texto, norm, seguridad)
            return self._resultado(seguridad=seguridad)

        sin_novedad = "sin novedad" in norm or "todo normal" in norm

        for oracion in oraciones(texto):
            for clausula in clausulas(oracion):
                self._procesar_clausula(clausula)

        self._asignar_fotos_restantes()
        if not self.filas and sin_novedad:
            self.filas.append(
                Hallazgo(
                    tag="PLANTA",
                    variable="estado",
                    valor=None,
                    unidad=None,
                    observacion="Sin novedad",
                    texto_fuente=self._fuente,
                )
            )
        return self._resultado(sin_novedad=sin_novedad)

    # ------------------------------------------------------------------ internos

    def _resultado(
        self,
        *,
        seguridad: str | None = None,
        sin_novedad: bool = False,
    ) -> Extraccion:
        # Las preguntas sin filas asociadas no hacen falta.
        preguntas = [p for p in self.preguntas if p.filas]
        return Extraccion(
            filas=self.filas,
            preguntas=preguntas,
            seguridad=seguridad,
            sin_novedad=sin_novedad,
        )

    def _procesar_clausula(self, clausula: str) -> None:
        norm = normalizar(clausula)
        if not norm:
            return

        # Una cláusula puede traer dos ideas cuando el ASR perdió la coma
        # que las separaba ("... con la pistola hace un golpeteo
        # metálico"). Se parte para que la medición y el síntoma vayan cada
        # uno en su fila.
        partes = _separar_sintoma(norm)
        if len(partes) > 1:
            for parte in partes:
                self._procesar_parte(parte)
            return
        self._procesar_parte(norm)

    def _procesar_parte(self, norm: str) -> None:
        mencion = self.resolver.resolver(norm)
        resto = norm
        if mencion is not None:
            resto = (norm[: mencion.inicio] + " " + norm[mencion.fin :]).strip()
            if mencion.es_ambigua:
                self._abrir_generico(mencion)
            else:
                self._fijar_equipo(mencion.tag or "")
            if self._partir_por_equipo(resto):
                return

        if _es_foto(resto):
            # La cláusula puede traer la foto **y** la medición ("le saqué
            # foto, el rodamiento me dio 52 grados"). Si la foto se come
            # toda la cláusula, la temperatura se pierde: la foto se asigna y
            # la medición sigue su curso.
            self._asignar_foto(resto)
            resto = _sin_foto(resto)
            norm = _sin_foto(norm)
            if not resto:
                return
            numero = parsear_numero(resto)
            variable = self._variable(norm, resto, numero)
            if variable is None:
                return
            self._agregar_medicion(variable, resto, numero)
            return

        numero = parsear_numero(resto)
        variable = self._variable(norm, resto, numero)
        if variable is None:
            if _CUE_REBALSE[0] in norm or _CUE_REBALSE[1] in norm:
                self._marcar_rebalse()
            elif _parece_observacion(resto):
                self._agregar_observacion(resto)
            return

        self._agregar_medicion(variable, resto, numero)

    def _fijar_equipo(self, tag: str) -> None:
        self._tag = tag
        self._tipo = self._tipo_de(tag)
        self._pendiente = None

    def _partir_por_equipo(self, resto: str) -> bool:
        """Parte la cláusula cuando nombra **dos** equipos.

        El ASR junta con una coma lo que el técnico dijo en dos frases ("el
        filtro B me dio uno de diferencial, además la 3 está parada por
        mantenimiento"). El extractor tomaba el primer nombre de equipo y
        perdía el segundo: la fila del equipo que quedó detrás no se
        publicaba y el evento de ese equipo no salía. Se parte sólo cuando
        entre los dos nombres hay una medición (o sea, cuando la primera
        parte ya es una idea completa): "el manómetro de la uno" no se parte.
        """
        otra = self.resolver.resolver(resto)
        if otra is None or otra.inicio <= 0 or otra.es_ambigua:
            return False
        primera = resto[: otra.inicio]
        if not _tiene_medicion(primera):
            return False
        self._procesar_clausula(primera)
        self._procesar_clausula(resto[otra.inicio :])
        return True

    def _abrir_generico(self, mencion: Mencion) -> None:
        candidatos = self.resolver.candidatos(mencion.generico or "")
        if not candidatos:
            self._tag = None
            self._tipo = None
            return
        nombre = mencion.generico or "equipo"
        self._tag = None
        self._tipo = nombre
        self._pendiente = Pregunta(
            campo="equipo",
            texto=f"¿A qué equipo te referís con «{nombre}»?",
            opciones=self.resolver.nombres_de(candidatos),
            tags=list(candidatos),
        )
        self.preguntas.append(self._pendiente)

    def _tipo_de(self, tag: str) -> str | None:
        for equipo in self.planta.get("equipos") or []:
            if equipo.get("tag") == tag:
                return equipo.get("tipo")
        return None

    def _variable(
        self,
        norm: str,
        resto: str,
        numero: NumeroDicho | None,
    ) -> str | None:
        if _menciona(norm, _CUE_DIFERENCIAL):
            return "presion_diferencial"
        if _menciona(norm, _CUE_DESCARGA):
            return "presion_descarga_local"
        # El síntoma se resuelve **una vez** para las tres familias: si el
        # texto dice "correas", eso es una correa y no un goteo, aunque las
        # dos palabras se parezcan. Y si no dice ninguno, se busca el
        # garblado ("gropeteo" por "golpeteo").
        sintoma = _familia_sintoma(norm)
        # El ruido manda sobre el rodamiento: "un ruido raro de rodamiento"
        # habla del ruido, no de la temperatura del rodamiento. Sin esto, la
        # frase se tomaba como una temperatura y el síntoma se perdía. Si la
        # misma frase trae la temperatura ("60 grados con la pistola"), la
        # temperatura es lo que se midió y manda ella.
        if sintoma == "ruido" and not (
            "grado" in norm or "pistola" in norm
        ):
            return "ruido"
        if _menciona(norm, _CUE_RODAMIENTO) and (
            "grado" in norm or "pistola" in norm or numero is not None
        ):
            return "temperatura_rodamiento_ir"
        if _menciona(norm, _CUE_MOTOR) and "grado" in norm:
            return "temperatura_motor_ir"
        if _menciona(norm, _CUE_CAUDAL):
            return "caudal"
        if sintoma == "goteo":
            return "goteo_empaquetadura"
        if _menciona(norm, _CUE_ACEITE):
            return "nivel_aceite_visor"
        if sintoma == "correas":
            return "estado_correas"
        if sintoma == "ruido":
            return "ruido"
        if _menciona(norm, _CUE_ESTADO):
            return "estado"
        if _menciona(norm, _CUE_NIVEL) or (
            self._tipo == "tanque" and numero is not None
        ):
            return "nivel"

        # Formas abreviadas: "la uno está en ciento quince", "el filtro me dio uno".
        if numero is not None:
            if self._tipo == "bomba" and ("esta en" in resto or _unidad_dicha(resto)):
                return "presion_descarga_local"
            if self._tipo == "filtro" and "me dio" in resto:
                return "presion_diferencial"
            if self._tipo == "tanque":
                return "nivel"
            # Si el equipo tiene una sola variable medible en el catálogo,
            # esa es la que se está diciendo: los filtros solo tienen
            # diferencial, y el ASR a veces se come la palabra ("el filtro A
            # me dio 1,3 de").
            variable = self._unica_variable_del_equipo(self._tag)
            if variable:
                return variable
        return None

    def _unica_variable_del_equipo(self, tag: str | None) -> str | None:
        """Variable medible del equipo, si el catálogo tiene una sola."""
        if not tag:
            return None
        variables = {
            sensor.get("variable")
            for sensor in self.planta.get("sensores") or []
            if sensor.get("tag") == tag and sensor.get("variable")
        }
        if len(variables) == 1:
            return next(iter(variables))
        return None

    def _agregar_medicion(
        self,
        variable: str,
        resto: str,
        numero: NumeroDicho | None,
    ) -> None:
        if variable in {"estado", "nivel_aceite_visor", "estado_correas", "ruido"}:
            observacion = _observacion(variable, resto)
            self._agregar(self._tag, variable, None, None, observacion, resto)
            return

        if variable == "goteo_empaquetadura":
            observacion = _observacion(variable, resto)
            self._agregar(self._tag, variable, None, None, observacion, resto)
            return

        valor = numero.valor if numero else None
        unidad = _unidad_de(variable, resto)
        pregunta_valor: Pregunta | None = None
        if valor is not None:
            if variable == "presion_descarga_local":
                canonica = unidad_canonica(variable, self._tag or "", self.planta)
                valor = convertir(valor, unidad, canonica)
            elif variable == "presion_diferencial":
                valor = self._ajustar_diferencial(valor, numero)
                if valor >= 10:
                    # "dieciocho de diferencial" no es 18 kg/cm²: es 1.8.
                    # Se pregunta, pero se deja la interpretación plausible.
                    pregunta_valor = self._preguntar_valor(variable, valor)
                    valor = valor / 10
            canonica = unidad_canonica(variable, self._tag or "", self.planta) or unidad
            unidad = canonica

        observacion = _observacion(variable, resto, valor)
        fila = self._agregar(self._tag, variable, valor, unidad, observacion, resto)
        if fila is not None and pregunta_valor is not None:
            indice = self.filas.index(fila)
            if indice not in pregunta_valor.filas:
                pregunta_valor.filas.append(indice)

    def _ajustar_diferencial(self, valor: float, numero: NumeroDicho | None) -> float:
        """Corrige un diferencial al que el ASR le sacó el punto decimal.

        "cero nueve" llega como ``09``: el ASR escribe el cero adelante. Un
        diferencial de 9 kg/cm² está muy por encima de la alarma del catálogo
        (1,5 kg/cm²), así que lo que se oyó fue 0,9. Solo se corrige cuando
        el número viene con ese cero adelante, para no tocar los demás.
        """
        if numero is None or not re.fullmatch(r"0\d", numero.dijo):
            return valor
        if valor <= self._alarma_diferencial():
            return valor
        return valor / 10

    def _alarma_diferencial(self) -> float:
        """Alarma alta del diferencial según el catálogo de la planta."""
        limites = [
            float(sensor["alarma_alta"])
            for sensor in self.planta.get("sensores") or []
            if sensor.get("variable") == "presion_diferencial"
            and sensor.get("alarma_alta") is not None
        ]
        return max(limites) if limites else 1.5

    def _preguntar_valor(self, variable: str, valor: float) -> Pregunta:
        nombre = self.resolver.nombre_canonico(self._tag) if self._tag else "el equipo"
        pregunta = Pregunta(
            campo="valor",
            texto=(
                f"¿El valor de {nombre} fue {valor / 10:g} o {valor:g}? "
                "Decime cuál para no guardar un número mal."
            ),
            variable=variable,
            valor_dicho=valor,
        )
        self.preguntas.append(pregunta)
        return pregunta

    def _agregar(
        self,
        tag: str | None,
        variable: str,
        valor: float | None,
        unidad: str | None,
        observacion: str | None,
        fuente: str,
    ) -> Hallazgo | None:
        if tag is None and (
            self._pendiente is None or not self._pendiente_admite(variable)
        ):
            return None
        for fila in self.filas:
            if fila.tag == tag and fila.variable == variable:
                if valor is not None:
                    fila.valor = valor
                    fila.unidad = unidad
                if observacion:
                    fila.observacion = _unir_observacion(fila.observacion, observacion)
                if not fila.texto_fuente:
                    fila.texto_fuente = fuente
                self._asociar_pregunta(fila)
                return fila
        fila = Hallazgo(
            tag=tag,
            variable=variable,
            valor=valor,
            unidad=unidad,
            observacion=observacion,
            texto_fuente=fuente,
        )
        self.filas.append(fila)
        self._asociar_pregunta(fila)
        return fila

    def _asociar_pregunta(self, fila: Hallazgo) -> None:
        """Enlaza la fila con la pregunta pendiente.

        Toda fila que queda sin equipo porque el operador dijo "la bomba" o
        "el filtro" depende de la respuesta. Si la fila no se enlaza, la
        pregunta se descarta y el parte sale con un equipo sin resolver.
        """
        if self._pendiente is None or fila.tag is not None:
            return
        if not self._pendiente_admite(fila.variable):
            return
        indice = self.filas.index(fila)
        if indice not in self._pendiente.filas:
            self._pendiente.filas.append(indice)

    def _pendiente_admite(self, variable: str) -> bool:
        """¿La lectura puede ser de los equipos que se están preguntando?

        Si el operador dijo "la bomba" y el ASR también se comió el nombre
        de un filtro, un diferencial no es de ninguna de las bombas: esa
        lectura se descarta en vez de adjudicarse al equipo equivocado.
        """
        if self._pendiente is None:
            return False
        tipos = {self._tipo_de(tag) for tag in (self._pendiente.tags or [])}
        if not tipos:
            return True
        if variable == "presion_diferencial" and tipos == {"bomba"}:
            return False
        if variable == "presion_descarga_local" and tipos == {"filtro"}:
            return False
        return True

    def _agregar_observacion(self, resto: str) -> None:
        # Se pega a la última fila del equipo actual: "está parada, en reserva".
        for fila in reversed(self.filas):
            if fila.tag == self._tag or (fila.tag is None and self._pendiente):
                fila.observacion = _unir_observacion(fila.observacion, _canonizar_obs(resto))
                return
        self._agregar(
            self._tag,
            "estado",
            None,
            None,
            _canonizar_obs(resto),
            resto,
        )

    def _marcar_rebalse(self) -> None:
        for fila in reversed(self.filas):
            if fila.variable == "nivel" and fila.tag == self._tag:
                fila.observacion = _unir_observacion(fila.observacion, "rebalse a pileta")
                return
        self._agregar(
            self._tag,
            "nivel",
            None,
            None,
            "rebalse a pileta",
            "rebalsando a la pileta",
        )

    def _asignar_foto(self, resto: str) -> None:
        if self._foto_cursor >= len(self._fotos):
            return
        objetivo = None
        if "manometro" in resto or "diferencial" in resto:
            objetivo = "presion_diferencial"
        elif "empaquetadura" in resto or "gotea" in resto:
            objetivo = "goteo_empaquetadura"
        elif "correa" in resto:
            objetivo = "estado_correas"
        elif "rotura" in resto or "descarga" in resto or "lejos" in resto:
            objetivo = "condicion_insegura"

        elegida = None
        if objetivo:
            for fila in reversed(self.filas):
                if fila.variable == objetivo and (
                    fila.tag == self._tag or (fila.tag is None and self._pendiente)
                ):
                    elegida = fila
                    break
        if elegida is None:
            for fila in reversed(self.filas):
                if fila.tag == self._tag or (fila.tag is None and self._pendiente):
                    elegida = fila
                    break
        if elegida is None and self.filas:
            elegida = self.filas[-1]
        if elegida is not None:
            elegida.foto_indice = self._foto_cursor
            self._foto_cursor += 1

    def _asignar_fotos_restantes(self) -> None:
        while self._foto_cursor < len(self._fotos) and self.filas:
            destino = self.filas[-1]
            if destino.foto_indice is None:
                destino.foto_indice = self._foto_cursor
            self._foto_cursor += 1

    def _extraer_seguridad(self, texto: str, norm: str, codigo: str) -> None:
        mencion = self.resolver.resolver(norm)
        tag = mencion.tag if mencion and mencion.tag else "PLANTA"
        self.filas.append(
            Hallazgo(
                tag=tag,
                variable="condicion_insegura",
                valor=None,
                unidad=None,
                observacion=texto.strip(),
                texto_fuente=texto.strip(),
                foto_indice=0 if self._fotos else None,
            )
        )
        self._foto_cursor = 1 if self._fotos else 0


def _es_foto(norm: str) -> bool:
    return (
        "foto" in norm
        and any(palabra in norm for palabra in ("saque", "saco", "saqué", "saca", "tomé", "tome"))
        or norm in {"foto", "le saque foto", "saque foto"}
    )


# La mención de la foto dentro de la cláusula: "le saqué foto", "saque una
# foto", "te mando la foto". Se busca del verbo hacia la foto para no comerse
# lo que viene después ("le saqué foto al manómetro").
_FRASE_FOTO = re.compile(
    r"\b(?:le|te|una|dos|tres)?\s*"
    r"(?:saco|saque|sacas|saques|saca|tome|tomes|toma|mando)\b"
    r"[^,;]*?\bfotos?\b"
    r"|\bfotos?\b"
)


def _sin_foto(norm: str) -> str:
    """La cláusula sin la mención de la foto.

    "le saqué foto, el rodamiento me dio 52 grados" son dos cosas: la foto
    y la medición. Si la foto se come la cláusula entera, la medición se
    pierde.
    """
    limpio = _FRASE_FOTO.sub(" ", norm)
    return re.sub(r"\s+", " ", limpio).strip(" ,;")


def _unidad_dicha(norm: str) -> str | None:
    for palabra in (
        "psi",
        # El ASR a veces separa la unidad: "1600 PS y de descarga".
        "ps",
        # Y a veces la junta mal: "1600 preses y de descarga".
        "preses",
        "libras",
        "libra",
        "bar",
        "bares",
        "kilos",
        "kilo",
        "kilogramos",
        "kilogramo",
        # El ASR abrevia los kilogramos: "115 kg de descarga".
        "kgs",
        "kg",
    ):
        if re.search(rf"\b{re.escape(palabra)}\b", norm):
            return normalizar_unidad(palabra)
    return None


def _unidad_de(variable: str, norm: str) -> str | None:
    if variable in {"presion_descarga_local", "presion_diferencial"}:
        return _unidad_dicha(norm)
    if variable in {"temperatura_rodamiento_ir", "temperatura_motor_ir"}:
        return "degC"
    if variable == "nivel":
        return "m"
    if variable == "caudal":
        if "metros cubicos por dia" in norm or "m3/d" in norm:
            return "m3/d"
    return None


def _parece_observacion(norm: str) -> bool:
    if not norm or any(ch.isdigit() for ch in norm):
        return False
    palabras = [p for p in norm.replace(",", " ").split() if p]
    return any(palabra not in _STOPWORDS for palabra in palabras)


# Las palabras que delatan una medición (o una lectura). Se usan para saber
# si una parte de la cláusula ya es una idea completa.
_CUE_MEDICION = (
    _CUE_DIFERENCIAL
    + _CUE_DESCARGA
    + _CUE_RODAMIENTO
    + _CUE_MOTOR
    + _CUE_NIVEL
    + _CUE_CAUDAL
    + _CUE_GOTEO
    + _CUE_ACEITE
    + _CUE_CORREAS
    + _CUE_RUIDO
)


def _tiene_medicion(norm: str) -> bool:
    """Si el texto trae una cifra o una palabra de medición."""
    if any(caracter.isdigit() for caracter in norm):
        return True
    if parsear_numero(norm) is not None:
        return True
    return _menciona(norm, _CUE_MEDICION)


def _observacion(
    variable: str,
    norm: str,
    valor: float | None = None,
) -> str | None:
    if variable == "estado":
        for dicho, canonica in _OBS_ESTADO.items():
            if dicho in norm:
                return canonica
        # Las palabras se buscan **completas**: "parada" está dentro de
        # "disparada" y, sin límite de palabra, una bomba que está armada y no
        # arranca se clasificaría como una bomba parada (que no es una falla).
        for palabra in ("disparada", "disparado", "parada", "parado", "mantenimiento"):
            if re.search(rf"\b{palabra}\b", norm):
                return palabra
    if variable == "nivel_aceite_visor":
        return "nivel de aceite bajo"
    if variable == "estado_correas":
        if "floja" in norm:
            return "correas flojas"
        if "rota" in norm:
            return "correas rotas"
        if "desalineada" in norm:
            return "correas desalineadas"
        return "correas en mal estado"
    if variable == "ruido":
        if _menciona_sintoma(norm, ("golpeteo",)):
            return "hace un golpeteo metálico"
        if _menciona(norm, ("piedras adentro",)):
            return "suena como si tuviera piedras adentro"
        if _menciona(norm, ("ruido raro",)):
            return "tiene un ruido raro de rodamiento"
        return "ruido anormal"
    if variable == "goteo_empaquetadura":
        if "chorrea" in norm or "pierde bastante" in norm:
            return "pérdida importante por empaquetadura"
        return "goteo por empaquetadura"
    if valor is not None and _cumple_umbral(variable, valor):
        return _OBSERVACION_POR_VALOR[variable][1]
    return None


def _cumple_umbral(variable: str, valor: float) -> bool:
    """Si el valor de la fila dispara la observación derivada del valor."""
    regla = _OBSERVACION_POR_VALOR.get(variable)
    if regla is None:
        return False
    umbral, _, incluye = regla
    return valor >= umbral if incluye else valor > umbral


def recalcular_observacion(fila: Hallazgo) -> None:
    """Vuelve a derivar la observación que depende del valor de la fila.

    El operador puede corregir un valor en la tablet ("era cero cuatro, no
    cuatro"). La observación que se había derivado del valor viejo ("diferencial
    alto") ya no corresponde: se saca y se calcula de nuevo con el valor
    nuevo. Las observaciones que salen del texto (el ruido, el goteo, ...) se
    dejan como están.
    """
    derivadas = {texto for _, texto, _ in _OBSERVACION_POR_VALOR.values()}
    if fila.observacion and fila.valor is not None:
        # Sólo se recalcula lo que salió del valor. Una fila sin valor no
        # puede tener una observación derivada de un valor, y una observación
        # que salió del texto ("chorrea por la empaquetadura") se queda: si el
        # operador mueve el goteo a otro equipo, el goteo sigue siendo goteo.
        partes = [
            parte.strip()
            for parte in fila.observacion.split(",")
            if parte.strip() and parte.strip() not in derivadas
        ]
        fila.observacion = ", ".join(partes) or None
    regla = _OBSERVACION_POR_VALOR.get(fila.variable)
    if regla is not None and fila.valor is not None and _cumple_umbral(fila.variable, fila.valor):
        fila.observacion = _unir_observacion(fila.observacion, regla[1])


def _canonizar_obs(norm: str) -> str:
    if "rebals" in norm:
        return "rebalse a pileta"
    return norm


def _unir_observacion(actual: str | None, nueva: str | None) -> str | None:
    if not nueva:
        return actual
    if not actual:
        return nueva
    if nueva in actual:
        return actual
    if actual in nueva:
        return nueva
    return f"{actual}, {nueva}"
