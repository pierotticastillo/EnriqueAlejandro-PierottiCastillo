"""Fixtures compartidos: catálogo mínimo de la planta real."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def planta() -> dict:
    return {
        "planta": {"nombre": "Planta de Inyección PIA-3"},
        "equipos": [
            {
                "tag": "B-101",
                "tipo": "bomba",
                "nombres": ["la uno", "la 1", "la de la punta", "la del portón", "bomba uno"],
                "manometro_local": {"unidad": "kg/cm2"},
            },
            {
                "tag": "B-102",
                "tipo": "bomba",
                "nombres": ["la dos", "la 2", "la del medio", "bomba dos"],
                "manometro_local": {"unidad": "psi"},
            },
            {
                "tag": "B-103",
                "tipo": "bomba",
                "nombres": ["la tres", "la 3", "la del fondo", "la de reserva", "bomba tres"],
                "manometro_local": {"unidad": "bar"},
            },
            {
                "tag": "F-201A",
                "tipo": "filtro",
                "nombres": ["el filtro A", "el primario", "el de la izquierda", "el grueso"],
            },
            {
                "tag": "F-201B",
                "tipo": "filtro",
                "nombres": ["el filtro B", "el secundario", "el de la derecha", "el fino"],
            },
            {
                "tag": "TK-301",
                "tipo": "tanque",
                "nombres": ["el pulmón", "el tanque", "el tanque pulmón"],
            },
            {
                "tag": "FT-401",
                "tipo": "caudalimetro",
                "nombres": ["el caudalímetro", "el colector"],
            },
        ],
        "sensores": [
            {
                "tag": "B-101",
                "variable": "presion_succion",
                "unidad": "kg/cm2",
                "alarma_baja": 0.9,
            },
            {
                "tag": "B-101",
                "variable": "presion_descarga",
                "unidad": "kg/cm2",
                "alarma_baja": 95,
                "alarma_alta": 125,
            },
            {
                "tag": "F-201A",
                "variable": "presion_diferencial",
                "unidad": "kg/cm2",
                "alarma_baja": 0.15,
                "alarma_alta": 1.5,
            },
            {
                "tag": "F-201B",
                "variable": "presion_diferencial",
                "unidad": "kg/cm2",
                "alarma_baja": 0.15,
                "alarma_alta": 1.5,
            },
            {
                "tag": "FT-401",
                "variable": "caudal",
                "unidad": "m3/d",
                "alarma_baja": 2200,
            },
        ],
    }
