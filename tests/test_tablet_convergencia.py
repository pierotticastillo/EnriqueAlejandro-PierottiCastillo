"""El borrador de la tablet tiene que converger con el operador.

Una corrección del operador que se aplica a la lectura equivocada hace que
el borrador nunca cierre: el operador vuelve a corregir lo mismo y la nota
se queda dando vueltas. Estos tests cubren las tres causas que se encontraron:

* la corrección de un valor tenía que ir a la fila de **esa variable**, no a
  la última fila del equipo (un equipo puede tener la presión de descarga y
  la temperatura del rodamiento);
* al mover una lectura a otro equipo, el valor tenía que **convertirse** a la
  unidad con la que se guarda en ese equipo;
* "lo de la uno no lo dije" niega **una** lectura, no todas las del equipo.
"""

from __future__ import annotations

from planta.nombre_resolver import desde_api
from planta.parte_extractor import Hallazgo
from planta.tablet import aplicar_correcciones, interpretar


def _filas() -> list[Hallazgo]:
    return [
        Hallazgo("B-101", "presion_descarga_local", 102.0, "kg/cm2"),
        Hallazgo("B-101", "temperatura_rodamiento_ir", 59.0, "degC"),
    ]


def _aplicar(filas: list[Hallazgo], respuesta: str, planta: dict, afirmados=None):
    resolver = desde_api(planta)
    interpretacion = interpretar(respuesta, resolver, planta=planta)
    assert interpretacion.correcciones, f"no entendimos: {respuesta!r}"
    return aplicar_correcciones(
        filas,
        interpretacion.correcciones,
        planta,
        resolver,
        afirmados=afirmados or set(),
    )


def test_la_correccion_de_valor_va_a_su_variable(planta: dict) -> None:
    """Corregir la presión no toca la temperatura del mismo equipo."""
    filas = _filas()
    _aplicar(filas, "No. La uno estaba en ciento doce kilos, no en ciento dos.", planta)

    presion = next(f for f in filas if f.variable == "presion_descarga_local")
    temperatura = next(f for f in filas if f.variable == "temperatura_rodamiento_ir")
    assert presion.valor == 112.0
    assert presion.unidad == "kg/cm2"
    assert temperatura.valor == 59.0
    assert temperatura.unidad == "degC"


def test_la_correccion_de_temperatura_va_a_su_variable(planta: dict) -> None:
    filas = _filas()
    _aplicar(filas, "No. La uno estaba en cincuenta y nueve grados, no en ciento dos.", planta)

    presion = next(f for f in filas if f.variable == "presion_descarga_local")
    temperatura = next(f for f in filas if f.variable == "temperatura_rodamiento_ir")
    assert presion.valor == 102.0
    assert temperatura.valor == 59.0


def test_al_mover_de_equipo_se_convierte_el_valor(planta: dict) -> None:
    """1600 psi que pasa de la dos (psi) a la uno (kg/cm2) se convierte."""
    filas = [Hallazgo("B-102", "presion_descarga_local", 1600.0, "psi")]
    _aplicar(filas, "Eso de mil seiscientos era de la uno, no de la dos.", planta)

    assert len(filas) == 1
    fila = filas[0]
    assert fila.tag == "B-101"
    assert fila.unidad == "kg/cm2"
    # 1600 psi son 112,5 kg/cm2: el mismo número con otra unidad no sirve.
    assert abs(fila.valor - 112.5) < 0.2


def test_al_mover_de_equipo_se_elige_la_lectura_del_valor(planta: dict) -> None:
    """Con varias lecturas del equipo, se mueve la que dice el operador."""
    filas = [
        Hallazgo("B-101", "presion_descarga_local", 112.0, "kg/cm2"),
        Hallazgo("B-101", "temperatura_rodamiento_ir", 59.0, "degC"),
        Hallazgo("B-102", "presion_descarga_local", 1600.0, "psi"),
    ]
    _aplicar(filas, "Eso de mil seiscientos era de la uno, no de la dos.", planta)

    movida = next(f for f in filas if f.tag == "B-101" and f.variable == "presion_descarga_local")
    assert abs(movida.valor - 112.5) < 0.2
    assert movida.unidad == "kg/cm2"
    # La lectura que se movió ya no está en la dos y no queda duplicada.
    assert not any(f.tag == "B-102" for f in filas)
    assert len([f for f in filas if f.tag == "B-101"]) == 2


def test_sobra_saca_una_sola_lectura(planta: dict) -> None:
    """El operador niega una lectura, no todas las del equipo."""
    filas = _filas()
    _aplicar(
        filas,
        "Lo de la uno no lo dije.",
        planta,
        afirmados={("B-101", "presion_descarga_local")},
    )

    variables = {f.variable for f in filas}
    assert variables == {"presion_descarga_local"}
    assert filas[0].valor == 102.0


def test_sobra_sin_afirmaciones_saca_la_ultima(planta: dict) -> None:
    filas = _filas()
    _aplicar(filas, "Lo de la uno no lo dije.", planta)

    assert len(filas) == 1
    assert filas[0].variable == "presion_descarga_local"


def test_el_bucle_de_correcciones_termina(planta: dict) -> None:
    """Las correcciones del operador dejan el borrador como corresponde.

    Es el caso que se trababa: el ASR oía "102" donde el operador había
    dicho "112" y la nota entraba en un bucle de borradores.
    """
    filas = [Hallazgo("B-101", "presion_descarga_local", 1600.0, "kg/cm2")]
    _aplicar(filas, "No. La uno estaba en ciento doce kilos, no en mil seiscientos.", planta)
    _aplicar(filas, "Te faltó la dos, que estaba en mil seiscientos psi.", planta)
    _aplicar(
        filas,
        "No. La uno estaba en cincuenta y nueve grados, no en mil seiscientos.",
        planta,
    )

    lecturas = {(f.tag, f.variable): (f.valor, f.unidad) for f in filas}
    assert lecturas[("B-101", "presion_descarga_local")] == (112.0, "kg/cm2")
    assert lecturas[("B-101", "temperatura_rodamiento_ir")] == (59.0, "degC")
    assert lecturas[("B-102", "presion_descarga_local")] == (1600.0, "psi")


def test_la_unidad_se_corrige_en_la_fila_de_esa_variable(planta: dict) -> None:
    """Lo de la dos es en psi: se corrige la presión, no la temperatura."""
    filas = [
        Hallazgo("B-102", "presion_descarga_local", 1600.0, "kg/cm2"),
        Hallazgo("B-102", "temperatura_rodamiento_ir", 59.0, "degC"),
    ]
    _aplicar(filas, "Lo de la dos es en psi, no en kilos.", planta)

    presion = next(f for f in filas if f.variable == "presion_descarga_local")
    temperatura = next(f for f in filas if f.variable == "temperatura_rodamiento_ir")
    assert presion.unidad == "psi"
    assert temperatura.unidad == "degC"
