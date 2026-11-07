"""Resolución de nombres informales de equipos."""

from __future__ import annotations

from planta.nombre_resolver import ResolutorNombres, desde_api


def test_alias_de_bombas(planta: dict) -> None:
    resolver = desde_api(planta)
    assert resolver.resolver_tag("la del medio está en mil seiscientos") == "B-102"
    assert resolver.resolver_tag("la de la punta") == "B-101"
    assert resolver.resolver_tag("la del fondo") == "B-103"
    assert resolver.resolver_tag("bomba dos") == "B-102"


def test_alias_de_filtros_y_tanque(planta: dict) -> None:
    resolver = desde_api(planta)
    assert resolver.resolver_tag("el de la izquierda me dio uno") == "F-201A"
    assert resolver.resolver_tag("el secundario") == "F-201B"
    assert resolver.resolver_tag("el pulmón está en cinco metros") == "TK-301"
    assert resolver.resolver_tag("el colector") == "FT-401"


def test_mencion_generica_es_ambigua(planta: dict) -> None:
    resolver = desde_api(planta)
    mencion = resolver.resolver("en la bomba, el rodamiento me dio sesenta grados")
    assert mencion is not None
    assert mencion.es_ambigua
    assert mencion.generico == "bomba"
    assert set(resolver.candidatos("bomba")) == {"B-101", "B-102", "B-103"}


def test_generico_con_numero_no_es_ambiguo(planta: dict) -> None:
    resolver = desde_api(planta)
    # "la bomba uno" es un alias explícito, no una mención genérica.
    mencion = resolver.resolver("la bomba uno está en ciento quince")
    assert mencion is not None
    assert mencion.tag == "B-101"


def test_generico_sin_articulo_tambien_es_ambiguo(planta: dict) -> None:
    """El ASR a veces se come el artículo: "también bomba está en 1600"."""
    resolver = desde_api(planta)
    mencion = resolver.resolver("ambienta bomba esta en mil seiscientos psi")
    assert mencion is not None
    assert mencion.es_ambigua
    assert mencion.generico == "bomba"


def test_filtro_generico_sin_articulo(planta: dict) -> None:
    resolver = desde_api(planta)
    mencion = resolver.resolver("filtro me dio uno coma tres de diferencial")
    assert mencion is not None
    assert mencion.es_ambigua
    assert mencion.generico == "filtro"
    assert set(resolver.candidatos("filtro")) == {"F-201A", "F-201B"}


def test_nombre_canonico(planta: dict) -> None:
    resolver = desde_api(planta)
    assert resolver.nombre_canonico("B-101") == "la uno"
    assert resolver.nombre_canonico("F-201B") == "el filtro B"
    assert resolver.nombres_de(["B-101", "B-102"]) == ["la uno", "la dos"]


def test_fallback_sin_api() -> None:
    resolver = ResolutorNombres()
    assert resolver.resolver_tag("la dos está en mil seiscientos") == "B-102"
