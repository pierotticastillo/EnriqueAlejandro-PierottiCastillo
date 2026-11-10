"""Cierre de órdenes por voz: entender el cierre, controlarlo contra la orden y
decidir el estado de la orden.

El técnico cierra la orden hablando: qué hizo, qué repuestos usó, cuántas horas
tardó y cómo quedó la prueba final (CT-SVP-2025 §3.1: la orden aprobada, la
nota de cierre y el registro de ingreso y egreso).

Acá está lo que se puede decidir **sin la voz**: el texto del cierre se usa
para extraer datos (tareas, repuestos, horas, prueba) y después se comparan
contra la orden. Las diferencias se calculan en código, igual que las reglas de
aprobación: lo que dice el técnico no puede certificar ni habilitar por sí
solo.

Las decisiones que salen de acá:

* **Cierre propio** → la orden queda ``Cerrada`` con las diferencias a la vista.
* **Cierre de contratista con diferencias sustantivas** → la orden queda
  ``Observada`` con el motivo, y vuelve a la contratista para que aclare
  (CT-SVP-2025 §3.3).
* **Cierre de contratista sin diferencias** → la orden queda ``Certificada`` y
  se emite el habilita con el monto.

El monto del habilita sale de **la orden y la lista de precios del contrato**,
nunca de lo que declara el cierre (CT-SVP-2025 §3.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from planta_contrato import OrdenSistema

from analisis.catalogo import CatalogoManuales
from analisis.orden import TAREA_NO_ESTANDAR
from planta.numeros import CENTENAS, DECENAS, UNIDADES, parsear_numero
from tools.manuales import normalizar
from tools.precios import ListaPrecios

# Fracción del nombre de una tarea o de un repuesto que tiene que aparecer en
# el texto para darlo por dicho. Con 0,6 alcanza con que falte una palabra de
# un nombre de cuatro: el ASR no siempre escribe todo.
UMBRAL_NOMBRE = 0.60
# Tolerancia al comparar cantidades de repuestos: 10 % o medio repuesto.
TOLERANCIA_CANTIDAD = 0.10
# Tolerancia al comparar las horas declaradas con las de la orden: 30 %.
# Las horas de la orden son las *incluidas* en el precio, no un tope de
# trabajo: un trabajo de 16 h puede llevar 20 h y seguir siendo correcto.
TOLERANCIA_HORAS = 0.30

# Verbos con los que el técnico dice qué hizo y qué usó.
_VERBOS_TRABAJO = (
    "hice",
    "hizimos",
    "hicimos",
    "realice",
    "realizamos",
    "efectue",
    "cambie",
    "cambio",
    "reemplace",
    "alinee",
    "repuse",
    "ajuste",
    "instale",
    "monte",
)
_VERBOS_USO = (
    "use",
    "usamos",
    "utilice",
    "consumi",
    "gaste",
    "emplee",
    "puse",
    "sustitu",
)

# Una frase del cierre: hasta el punto, el signo de exclamación o el signo de
# interrogación.
_FIN_FRASE = re.compile(r"(?<=[.!?])\s+")

# Palabras que no dicen nada al comparar un nombre con lo que dijo el
# técnico.
_VACIAS = {
    "de",
    "del",
    "la",
    "el",
    "los",
    "las",
    "un",
    "una",
    "unos",
    "unas",
    "y",
    "a",
    "al",
    "en",
    "con",
    "para",
    "por",
    "lo",
    "su",
    "sus",
    "que",
    "como",
}

# Un número pegado a un código no es una cantidad: "C-48 cartucho filtrante"
# habla del filtro FC-48, no de 48 cartuchos.
_CODIGO = re.compile(r"^[a-z]{1,4}[-_]\d+$")

# Un decimal que el ASR escribió con guion: "fueron 6-2 horas" son 6,2 h.
_DECIMAL_GUION = re.compile(r"^(\d+)[-](\d)$")

# Entre dos palabras del nombre de una tarea puede haber palabras vacías:
# "cambio de rodamientos del cigüeñal". Esto es lo que se permite entre
# dos palabras cuando se busca la mención de una tarea.
_ENTRE = r"(?:\W+(?:" + "|".join(sorted(_VACIAS, key=len, reverse=True)) + r"))*\W+"

# Palabras que pueden preceder a un número y hacer que el número empiece
# antes: "cuarenta y ocho", "tres con dos", "ciento veinte".
_PUEDEN_PRECEDER = {"mil", "y", "medio", "con", "coma", "punto"}

# Cuántas palabras se miran antes del nombre: el número va pegado al
# nombre ("usé cuarenta y ocho cartucho filtrante"). Mirar más lejos trae
# números que no son del repuesto ("... uno a FZ-CEO vos dice ... cartucho").
_PALABRAS_CANTIDAD = 4


# --------------------------------------------------------------------- modelos


@dataclass(frozen=True)
class RepuestoCierre:
    """Un repuesto que el técnico dice haber usado."""

    codigo: str
    cantidad: float | None
    nombre: str | None = None


@dataclass(frozen=True)
class Cierre:
    """Lo que el técnico dijo al cerrar la orden."""

    orden_id: str | None
    tareas: tuple[str, ...] = ()
    repuestos: tuple[RepuestoCierre, ...] = ()
    horas: float | None = None
    prueba_final: str | None = None
    texto: str = ""


@dataclass(frozen=True)
class Diferencia:
    """Una diferencia entre lo que pedía la orden y lo que dice el cierre."""

    tipo: str
    detalle: str
    sustantiva: bool = True


@dataclass(frozen=True)
class Comparacion:
    """El control del cierre contra la orden."""

    diferencias: tuple[Diferencia, ...] = ()

    @property
    def sustantivas(self) -> tuple[Diferencia, ...]:
        return tuple(diferencia for diferencia in self.diferencias if diferencia.sustantiva)

    @property
    def informativas(self) -> tuple[Diferencia, ...]:
        return tuple(diferencia for diferencia in self.diferencias if not diferencia.sustantiva)

    @property
    def hay_diferencias(self) -> bool:
        return bool(self.diferencias)

    @property
    def resumen(self) -> str:
        """Las diferencias en una línea, para el motivo de la orden."""
        return "; ".join(diferencia.detalle for diferencia in self.diferencias)


@dataclass(frozen=True)
class Decision:
    """Qué se decide con el cierre y por qué."""

    estado: str
    motivo: str | None = None
    habilita: str | None = None
    monto_habilita_usd: float | None = None
    diferencias: tuple[Diferencia, ...] = ()


# ------------------------------------------------------------------ parseo


def parsear_cierre(
    texto: str,
    orden: OrdenSistema,
    catalogo: CatalogoManuales,
) -> Cierre:
    """Entiende el texto del cierre: qué hizo, qué usó, cuántas horas.

    El texto libre no decide nada: acá sólo se extraen los datos que después se
    comparan contra la orden. Si algo no se entiende, queda sin dato (no se
    inventa) y la comparación lo marca como diferencia.
    """
    limpio = (texto or "").strip()
    normal = normalizar(limpio)
    # La mención de la orden no es parte de lo que el técnico hizo: si no se
    # sacara, sus letras y sus números ("ORD B 101 TX T03") harían matchear
    # con el nombre de un repuesto que el técnico nunca dijo.
    cuerpo = _sin_la_orden(normal, orden.id)
    clausula_trabajo = _clausula(cuerpo, _VERBOS_TRABAJO)
    tareas = _tareas_del_texto(clausula_trabajo, catalogo)
    # Lo que el técnico dice que *hizo* no es lo que dice que *usó*: "se
    # cambió de rodamientos del cigüeñal" habla de la tarea, no de un juego
    # de rodamientos. Si las dos cosas se buscaran juntas, la tarea se
    # contaría como repuesto y la orden aparecería con un repuesto de más.
    cuerpo_sin_tareas = _sin_las_tareas(cuerpo, catalogo, tareas)
    clausula_uso = _clausula(cuerpo_sin_tareas, _VERBOS_USO)
    return Cierre(
        orden_id=orden.id,
        tareas=tareas,
        repuestos=_repuestos_del_texto(clausula_uso, catalogo),
        horas=_horas_del_texto(normal),
        prueba_final=_prueba_del_texto(normal),
        texto=limpio,
    )


def _sin_las_tareas(
    texto: str,
    catalogo: CatalogoManuales,
    tareas: tuple[str, ...],
) -> str:
    """El texto sin las menciones de las tareas que se detectaron.

    Se busca la tarea completa ("cambio de rodamientos del cigüeñal"), con
    las palabras vacías que el técnico intercala ("cambio *de* rodamientos
    *del* cigüeñal"). Una mención parcial ("juego de rodamientos de
    cigüeñal TX-350") no se saca: esa sí es la del repuesto.
    """
    if not tareas:
        return texto
    for codigo in tareas:
        tarea = next((t for t in catalogo.tareas if t.codigo == codigo), None)
        if tarea is None:
            continue
        texto = _sin_la_mencion(texto, tarea.nombre)
    return texto


def _sin_la_mencion(texto: str, nombre: str) -> str:
    """Saca del texto la mención de una cosa por su nombre.

    "cambio de cartuchos" deja "se cambió de cartuchos" sin la tarea, pero
    deja "usó un juego de rodamientos" como está: el nombre del repuesto no
    es el de la tarea.
    """
    tokens = _tokens(nombre)
    if len(tokens) < 2:
        return texto
    piezas = []
    for token in tokens:
        singular = _singular(token)
        piezas.append(
            f"(?:{re.escape(token)}|{re.escape(singular)})"
            if singular != token
            else re.escape(token)
        )
    patron = r"\b" + _ENTRE.join(piezas) + r"\b"
    return re.sub(patron, " ", texto)


def _sin_la_orden(texto: str, orden_id: str | None) -> str:
    """El texto del cierre sin la mención del número de la orden.

    "Cierro la orden ORD B 101 TX T03. Hice ..." → "Hice ...".
    """
    if not orden_id:
        return texto
    partes = [parte for parte in re.split(r"[^a-z0-9]+", normalizar(orden_id)) if parte]
    if not partes:
        return texto
    patron = r"\b" + r"\W+".join(re.escape(parte) for parte in partes) + r"\b"
    coincidencia = re.search(patron, texto)
    if coincidencia is None:
        return texto
    return texto[coincidencia.end() :]


def _clausula(texto: str, verbos: tuple[str, ...]) -> str:
    """La parte del texto donde se dice qué se hizo o qué se usó.

    Si el técnico no usa ninguno de los verbos, se devuelve el texto entero:
    es mejor buscar en todo que no encontrar nada.
    """
    normal = normalizar(texto)
    frases = _FIN_FRASE.split(normal)
    elegidas = [frase for frase in frases if any(verbo in frase for verbo in verbos)]
    return " ".join(elegidas) if elegidas else normal


def _tokens(texto: str) -> list[str]:
    """Palabras útiles para comparar nombres: sin tildes, minúsculas, de dos
    letras o más y sin palabras vacías.

    Las palabras vacías no dicen nada del nombre: si se dejaran, el mismo
    nombre matchearía con menos palabras y el ASR tendría que acertar más.
    """
    return [
        palabra
        for palabra in re.findall(r"[a-z0-9]+", normalizar(texto))
        if len(palabra) > 1 and palabra not in _VACIAS
    ]


def _singular(palabra: str) -> str:
    """``cartuchos`` → ``cartucho``: el ASR singulariza y no siempre."""
    if len(palabra) > 4 and palabra.endswith("s"):
        return palabra[:-1]
    return palabra


def _formas(tokens: list[str]) -> set[str]:
    salida = set(tokens)
    salida.update(_singular(token) for token in tokens)
    return salida


def _tareas_del_texto(texto: str, catalogo: CatalogoManuales) -> tuple[str, ...]:
    """Las tareas del plan que el técnico dice haber hecho.

    Se buscan en todo el catálogo, no sólo en las de la orden: si el técnico
    declara una tarea que no estaba pedida, esa diferencia también tiene que
    verse.
    """
    tokens = _tokens(texto)
    if not tokens:
        return ()
    formas = _formas(tokens)
    candidatos: list[tuple[float, int, str, set[str]]] = []
    vistos: set[str] = set()
    for tarea in catalogo.tareas:
        if tarea.codigo in vistos:
            # T-F-CAR tiene dos variantes (una por filtro) con el mismo nombre.
            continue
        vistos.add(tarea.codigo)
        objetivo = _tokens(tarea.nombre)
        if not objetivo:
            continue
        encontrados = {
            token
            for token in objetivo
            if token in formas or _singular(token) in formas
        }
        if not encontrados:
            continue
        puntuacion = len(encontrados) / len(objetivo)
        if puntuacion < UMBRAL_NOMBRE:
            continue
        candidatos.append((puntuacion, len(encontrados), tarea.codigo, encontrados))

    candidatos.sort(key=lambda item: (item[0], item[1]), reverse=True)
    elegidas: list[str] = []
    usados: set[str] = set()
    for _puntuacion, _cantidad, codigo, encontrados in candidatos:
        if encontrados & usados:
            continue
        usados |= encontrados
        elegidas.append(codigo)
    return tuple(elegidas)


def _repuestos_del_texto(
    texto: str,
    catalogo: CatalogoManuales,
) -> tuple[RepuestoCierre, ...]:
    """Los repuestos que el técnico dice haber usado, con su cantidad.

    La cantidad es el número que dice justo antes del nombre del repuesto
    ("usé cuarenta y ocho cartucho filtrante 25 µm"). Si no dice la cantidad,
    queda sin dato: no se inventa una cantidad para después compararla.
    """
    if not texto.strip():
        return ()
    materiales = _materiales(catalogo)
    if not materiales:
        return ()

    # Los nombres más largos primero: "cartucho filtrante 25" tiene que
    # ganarle a "cartucho filtrante 10" cuando el texto dice 25.
    materiales.sort(key=lambda material: (-len(material[2]), material[0]))
    separador = r"\W+"
    alternativas: list[tuple[int, list[str]]] = [
        (indice, variante)
        for indice, (_codigo, _nombre, _tokens, variantes) in enumerate(materiales)
        for variante in variantes
    ]
    alternativas.sort(key=lambda alternativa: (-len(alternativa[1]), alternativa[0]))
    grupos: dict[str, int] = {}
    partes: list[str] = []
    for posicion, (indice, variante) in enumerate(alternativas):
        nombre_grupo = f"m{posicion}"
        grupos[nombre_grupo] = indice
        partes.append(
            f"(?P<{nombre_grupo}>{separador.join(re.escape(token) for token in variante)})"
        )
    patron = "|".join(partes)

    salida: list[RepuestoCierre] = []
    anterior_fin = 0
    for coincidencia in re.finditer(patron, texto):
        indice = grupos[coincidencia.lastgroup]
        codigo, nombre, _tokens_nombre, _variantes = materiales[indice]
        ventana = texto[anterior_fin : coincidencia.start()]
        salida.append(
            RepuestoCierre(
                codigo=codigo,
                cantidad=_cantidad_antes(ventana),
                nombre=nombre,
            )
        )
        anterior_fin = coincidencia.end()

    # Repuestos que el técnico nombró pero no completos (el ASR se comió una
    # palabra, o el nombre tiene un agregado que no se dice): se aceptan si el
    # nombre coincide en su mayoría y no pisa lo que ya se encontró.
    salida.extend(_repuestos_parciales(texto, catalogo, salida))
    return tuple(salida)


def _materiales(catalogo: CatalogoManuales) -> list[tuple[str, str, list[str], list[list[str]]]]:
    """Los repuestos del catálogo, con las variantes de su nombre.

    El catálogo trae el nombre entero ("kit de empaquetadura TX-350 (3
    émbolos)") y en campo el técnico dice el que se usa ("kit de empaquetadura
    TX-350"): las dos variantes se buscan.
    """
    salida: list[tuple[str, str, list[str], list[list[str]]]] = []
    for codigo, repuesto in catalogo.repuestos.items():
        nombre = (repuesto.nombre or "").strip()
        tokens = _tokens(nombre)
        if not tokens:
            continue
        variantes = [tokens]
        corto = _tokens(nombre.split(" (", 1)[0])
        if corto and corto != tokens:
            variantes.append(corto)
        salida.append((codigo, nombre, tokens, variantes))
    return salida


def _repuestos_parciales(
    texto: str,
    catalogo: CatalogoManuales,
    ya_encontrados: list[RepuestoCierre],
) -> list[RepuestoCierre]:
    """Repuestos nombrados a medias: el nombre aparece casi entero.

    El ASR puede comerse una palabra ("cartucho filtrante 25 micrones") o
    escribir el nombre de otra manera. Se acepta el repuesto cuyo nombre
    coincide en su mayoría, y **el que mejor coincide gana**: si el texto
    dice "cartucho filtrante 25", es el de 25 y no el de 10.
    """
    formas_texto = _formas(_tokens(texto))
    ocupados = {
        token
        for repuesto in ya_encontrados
        for token in _tokens(repuesto.nombre or "")
    }
    candidatos: list[tuple[float, str, str, set[str], list[list[str]]]] = []
    for codigo, nombre, _tokens_nombre, variantes in _materiales(catalogo):
        if any(repuesto.codigo == codigo for repuesto in ya_encontrados):
            continue
        mejor: tuple[float, set[str]] | None = None
        for variante in variantes:
            encontrados = {
                token
                for token in variante
                if token in formas_texto or _singular(token) in formas_texto
            }
            if not encontrados:
                continue
            puntuacion = len(encontrados) / len(variante)
            if mejor is None or puntuacion > mejor[0]:
                mejor = (puntuacion, encontrados)
        if mejor is None:
            continue
        puntuacion, encontrados = mejor
        if puntuacion < UMBRAL_NOMBRE:
            continue
        candidatos.append((puntuacion, codigo, nombre, encontrados, variantes))

    candidatos.sort(key=lambda candidato: (-candidato[0], candidato[1]))
    salida: list[RepuestoCierre] = []
    for _puntuacion, codigo, nombre, encontrados, variantes in candidatos:
        if encontrados & ocupados:
            continue
        ocupados |= encontrados
        salida.append(
            RepuestoCierre(
                codigo=codigo,
                cantidad=_cantidad_antes(_ventana_antes_del_nombre(texto, variantes)),
                nombre=nombre,
            )
        )
    return salida


def _ventana_antes_del_nombre(texto: str, variantes: list[list[str]]) -> str:
    """El texto anterior al nombre del repuesto, para buscarle la cantidad."""
    for variante in variantes:
        if not variante:
            continue
        ancla = re.search(rf"\b{re.escape(variante[0])}\b", texto)
        if ancla is not None:
            return texto[: ancla.start()]
    return ""


def _cantidad_antes(ventana: str) -> float | None:
    """El número que se dice justo antes del nombre del repuesto.

    Se busca el número **más cercano al final** de la ventana: es el que va
    pegado al nombre ("usé cuarenta y ocho cartucho filtrante 25"). La
    ventana son las últimas palabras, no todo el texto: más lejos hay
    números que no son del repuesto ("... uno a FZ-CEO vos dice ... cartucho").

    Dos cuidados:
    * si el número es parte de un código ("C-48 cartucho filtrante"), no es
      una cantidad;
    * si el número empieza antes ("cuarenta y ocho", "tres con dos"), se lee
      completo: por eso se retrocede mientras la palabra anterior pueda
      preceder a un número.
    """
    if not ventana.strip():
        return None
    palabras = [_limpia(palabra) for palabra in normalizar(ventana).split()]
    if not palabras:
        return None
    for inicio in range(len(palabras) - 1, max(0, len(palabras) - _PALABRAS_CANTIDAD) - 1, -1):
        # El ASR a veces escribe el decimal con guion ("fueron 6-2 horas").
        decimal = _DECIMAL_GUION.match(palabras[inicio])
        if decimal is not None:
            return round(float(decimal.group(1)) + float(decimal.group(2)) / 10, 2)
        resto = " ".join(palabras[inicio:])
        # La cantidad se lee sin la regla del artículo: en "usé un kit de
        # empaquetadura" el "un" **sí** es la cantidad (uno).
        dicho = parsear_numero(resto, articulos=False)
        if dicho is None:
            continue
        if not normalizar(resto).startswith(normalizar(dicho.dijo)):
            continue
        if _CODIGO.match(palabras[inicio]):
            # "C-48" es el código del filtro, no una cantidad.
            continue
        if inicio > 0 and _puede_preceder(palabras[inicio - 1]):
            # El número arranca antes: "cuarenta y ocho", "tres con dos".
            continue
        if inicio > 0 and _es_digito(palabras[inicio - 1]) and _es_digito(palabras[inicio]):
            # El ASR separa el decimal con un espacio ("3 1" por "3,1").
            return round(float(palabras[inicio - 1]) + float(palabras[inicio]) / 10, 2)
        return float(dicho.valor)
    return None


def _limpia(palabra: str) -> str:
    """La palabra sin la puntuación que la pegaba al texto.

    "cartuchos." → "cartuchos"; "3,1" se deja como está (la coma es del
    decimal).
    """
    return re.sub(r"^[^\w]+|[^\w]+$", "", palabra)


def _puede_preceder(palabra: str) -> bool:
    """¿Esta palabra puede ir delante de un número?"""
    if palabra in _PUEDEN_PRECEDER:
        return True
    return palabra in UNIDADES or palabra in DECENAS or palabra in CENTENAS


def _es_digito(palabra: str) -> bool:
    """¿La palabra es un número escrito con cifras?"""
    return palabra.isdigit()


def _horas_del_texto(texto: str) -> float | None:
    """Las horas que dice el técnico ("fueron tres con dos horas").

    Se mira la frase que termina en "horas": lo que va entre la última coma
    (o el último punto) y la palabra. Si no, el número de las horas sería el
    de cualquier otra cosa que dijo antes ("cartucho filtrante 25").
    """
    normal = normalizar(texto)
    menciones = [m.start() for m in re.finditer(r"\bhoras?\b", normal)]
    if not menciones:
        return None
    fin = menciones[-1]
    inicio = _corte_de_frase(normal, fin)
    ventana = normal[inicio:fin]
    # "probé a ciento diez kilos, sin pérdida. Fueron tres con dos horas": si
    # la ventana menciona kilos, lo de los kilos no son las horas.
    if "kilo" in ventana:
        ventana = ventana.rsplit("kilo", 1)[-1]
    return _cantidad_antes(ventana)


def _corte_de_frase(texto: str, fin: int) -> int:
    """Dónde empieza la frase que termina en ``fin``.

    La última coma, punto o signo de exclamación antes de esa posición, pero
    **no** la coma de un decimal ("fueron 3,1 horas": la coma del 3,1 no
    corta la frase).
    """
    corte = 0
    for marca in re.finditer(r"[.,;!?]", texto[:fin]):
        if marca.group() == "," and _coma_decimal(texto, marca.start()):
            continue
        corte = marca.end()
    return corte


def _coma_decimal(texto: str, posicion: int) -> bool:
    """¿Esa coma es la de un decimal ("3,1")?"""
    antes = texto[posicion - 1] if posicion > 0 else ""
    despues = texto[posicion + 1] if posicion + 1 < len(texto) else ""
    return antes.isdigit() and despues.isdigit()


def _prueba_del_texto(texto: str) -> str | None:
    """La prueba final que dice el técnico ("probé a 110 kilos, sin pérdida").

    Se toma el tramo que habla de la prueba: el ASR separa las ideas con
    comas, así que el tramo es el que va entre comas.
    """
    normal = normalizar(texto)
    marcas = ("prob", "quedo en servicio", "diferencial", "sin perdida")
    for tramo in re.split(r"[.;!?]", normal):
        for parte in tramo.split(","):
            if any(marca in parte for marca in marcas):
                return parte.strip() or None
    return None


# --------------------------------------------------------------- comparación


def comparar_cierre(
    orden: OrdenSistema,
    cierre: Cierre,
    catalogo: CatalogoManuales,
) -> Comparacion:
    """Controla el cierre contra la orden: tareas pedidas vs hechas y repuestos
    reservados vs usados.

    Las diferencias de **tareas** y **repuestos** son sustantivas: la orden
    pasa a Observada (CT-SVP-2025 §3.3). Las de **horas** son informativas:
    la planta no publica el registro de ingreso y egreso (§3.2), así que no se
    puede establecer que las horas declaradas no sean las trabajadas; el
    monto sale del precio cerrado de la orden, que se paga completo de todos
    modos (§2.2).
    """
    diferencias: list[Diferencia] = []
    esperadas = list(orden.tareas)
    declaradas = list(cierre.tareas)

    for tarea in esperadas:
        if tarea == TAREA_NO_ESTANDAR:
            diferencias.append(
                Diferencia(
                    "tarea_no_verificable",
                    "La orden pide una tarea que no está en el catálogo: "
                    "el cierre no la puede verificar",
                    sustantiva=False,
                )
            )
            continue
        if tarea not in declaradas:
            diferencias.append(
                Diferencia(
                    "tarea_faltante",
                    f"La orden pide {tarea} y el cierre no la declara",
                )
            )
    for tarea in declaradas:
        if tarea not in esperadas:
            diferencias.append(
                Diferencia(
                    "tarea_extra",
                    f"El cierre declara {tarea}, que no estaba en la orden",
                )
            )

    _comparar_repuestos(orden, cierre, catalogo, diferencias)
    _comparar_horas(orden, cierre, catalogo, diferencias)
    return Comparacion(diferencias=tuple(diferencias))


def _comparar_repuestos(
    orden: OrdenSistema,
    cierre: Cierre,
    catalogo: CatalogoManuales,
    diferencias: list[Diferencia],
) -> None:
    reservados = {repuesto.codigo: repuesto.cantidad for repuesto in orden.repuestos}
    usados = {repuesto.codigo: repuesto for repuesto in cierre.repuestos}

    for codigo, cantidad in reservados.items():
        nombre = _nombre_repuesto(catalogo, codigo)
        if codigo not in usados:
            diferencias.append(
                Diferencia(
                    "repuesto_faltante",
                    f"La orden reservó {cantidad:g} de {nombre} y el cierre no lo declara",
                )
            )
            continue
        declarado = usados[codigo].cantidad
        if declarado is None:
            # El técnico nombró el repuesto pero no dijo la cantidad: no se
            # puede dar por diferente lo que no se sabe.
            diferencias.append(
                Diferencia(
                    "repuesto_sin_cantidad",
                    f"El cierre menciona {nombre} pero no dice la cantidad usada",
                    sustantiva=False,
                )
            )
            continue
        if not _misma_cantidad(cantidad, declarado):
            diferencias.append(
                Diferencia(
                    "cantidad_repuesto",
                    f"La orden reservó {cantidad:g} de {nombre} y el cierre declara {declarado:g}",
                )
            )
    for codigo, repuesto in usados.items():
        if codigo in reservados:
            continue
        diferencias.append(
            Diferencia(
                "repuesto_extra",
                f"El cierre declara {_cantidad_texto(repuesto.cantidad)} de "
                f"{_nombre_repuesto(catalogo, codigo)}, que no estaba en la orden",
            )
        )


def _cantidad_texto(cantidad: float | None) -> str:
    """La cantidad escrita, o "una cantidad sin decir" si no la dijo."""
    if cantidad is None:
        return "un repuesto (sin decir la cantidad)"
    return f"{cantidad:g}"


def _misma_cantidad(reservada: float, declarada: float) -> bool:
    """¿La cantidad declarada es la reservada, tolerando el error del ASR?"""
    tolerancia = max(TOLERANCIA_CANTIDAD * abs(reservada), 0.5)
    return abs(reservada - declarada) <= tolerancia


def _nombre_repuesto(catalogo: CatalogoManuales, codigo: str) -> str:
    repuesto = catalogo.repuestos.get(codigo)
    return (repuesto.nombre if repuesto and repuesto.nombre else codigo)


def _comparar_horas(
    orden: OrdenSistema,
    cierre: Cierre,
    catalogo: CatalogoManuales,
    diferencias: list[Diferencia],
) -> None:
    """Las horas declaradas contra las de la orden: diferencia informativa."""
    if cierre.horas is None:
        return
    esperadas = 0.0
    for codigo in orden.tareas:
        tarea = catalogo.tarea_por_codigo(codigo, orden.equipo)
        if tarea is not None and tarea.horas:
            esperadas += float(tarea.horas)
    if esperadas <= 0:
        return
    if abs(cierre.horas - esperadas) <= TOLERANCIA_HORAS * esperadas:
        return
    diferencias.append(
        Diferencia(
            "horas",
            f"La orden supone {esperadas:g} h y el cierre declara {cierre.horas:g} h",
            sustantiva=False,
        )
    )


# ------------------------------------------------------------------- decisión


def monto_de_la_orden(
    orden: OrdenSistema,
    precios: ListaPrecios,
    *,
    horas: float | None = None,
    equipos: int = 1,
) -> tuple[float, list[str]]:
    """El monto del habilita: sale de la orden y de la lista de precios.

    Nunca de lo que declara el cierre (CT-SVP-2025 §3.2). Para los precios
    cerrados va el precio del ítem; para los ítems por equipo, el precio por
    cada equipo de la orden; para los ítems por hora, las horas (la menor
    entre el registro de ingreso y las declaradas, §3.2).
    """
    total = 0.0
    detalle: list[str] = []
    for codigo in orden.tareas:
        precio = precios.de_tarea(codigo)
        if precio is None:
            continue
        if precio.por_hora:
            if horas is None:
                continue
            importe = precio.precio_usd * horas
        elif precio.por_equipo:
            importe = precio.precio_usd * equipos
        else:
            importe = precio.precio_usd
        total += importe
        detalle.append(f"{precio.codigo} ({precio.descripcion}): USD {importe:,.0f}")
    return round(total, 2), detalle


def decidir_cierre(
    orden: OrdenSistema,
    comparacion: Comparacion,
    precios: ListaPrecios,
    *,
    habilita: str | None = None,
    monto_usd: float | None = None,
) -> Decision:
    """El estado que corresponde al cierre, con su motivo.

    * Trabajo propio → ``Cerrada``, con las diferencias a la vista.
    * Contratista con diferencias sustantivas → ``Observada`` con el motivo:
      vuelve a la contratista para que aclare (CT-SVP-2025 §3.3).
    * Contratista sin diferencias → ``Certificada`` con el habilita y el monto.
    """
    ejecutor = (orden.ejecutor or "propio").strip().lower()
    if ejecutor == "propio":
        return Decision(
            estado="Cerrada",
            motivo=comparacion.resumen or None,
            diferencias=comparacion.diferencias,
        )
    if comparacion.sustantivas:
        return Decision(
            estado="Observada",
            motivo=comparacion.resumen,
            diferencias=comparacion.diferencias,
        )
    return Decision(
        estado="Certificada",
        motivo=comparacion.resumen or None,
        habilita=habilita,
        monto_habilita_usd=monto_usd,
        diferencias=comparacion.diferencias,
    )
