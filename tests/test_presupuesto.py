"""Tests del presupuesto: pasos, tokens y timeout global por nota.

El anexo de la Entrega 1 pide *"condiciones de corte, presupuesto de pasos y
tokens, y timeout global por nota"*. Estos tests fijan las tres cosas:

* el presupuesto corta cuando se acaban los pasos o los tokens;
* el corte es una **condición prevista**, no un fallo: el sistema sigue con
  lo que ya tenía;
* el timeout global por nota se aplica a la transcripción y al parte.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from agente.presupuesto import Presupuesto, PresupuestoAgotado

# ------------------------------------------------------------------- el reloj


def test_un_presupuesto_nuevo_no_esta_cortado() -> None:
    presupuesto = Presupuesto(max_pasos=6, max_tokens=1000, timeout_s=30)
    assert presupuesto.agotado() is False
    assert presupuesto.motivo_corte() is None
    assert presupuesto.pasos == 0
    assert presupuesto.tokens == 0


def test_el_reloj_corre() -> None:
    presupuesto = Presupuesto(timeout_s=10)
    assert presupuesto.restante_s <= 10
    assert presupuesto.segundos >= 0


def test_un_presupuesto_sin_limite_no_se_corta() -> None:
    """Sin límite no hay corte: 0 significa 'sin tope'."""
    presupuesto = Presupuesto(max_pasos=0, max_tokens=0, timeout_s=0)
    for _ in range(100):
        presupuesto.consumir_paso(1000, 1000)
    assert presupuesto.agotado() is False


# ------------------------------------------------------------------- los cortes


def test_se_corta_cuando_se_acaban_los_pasos() -> None:
    presupuesto = Presupuesto(max_pasos=2)
    presupuesto.consumir_paso()
    presupuesto.consumir_paso()
    assert presupuesto.agotado() is False
    with pytest.raises(PresupuestoAgotado):
        presupuesto.consumir_paso()
    assert presupuesto.corte is not None
    assert "pasos" in presupuesto.corte


def test_se_corta_cuando_se_acaban_los_tokens() -> None:
    presupuesto = Presupuesto(max_tokens=100)
    presupuesto.consumir_paso(tokens_prompt=60, tokens_completion=30)
    assert presupuesto.agotado() is False
    with pytest.raises(PresupuestoAgotado):
        presupuesto.consumir_paso(tokens_prompt=20, tokens_completion=20)
    assert presupuesto.tokens == 130
    assert "tokens" in (presupuesto.corte or "")


def test_se_corta_cuando_se_pasa_el_timeout() -> None:
    """El timeout global manda: si se pasó el plazo, no se sigue."""
    presupuesto = Presupuesto(timeout_s=0.01)
    time.sleep(0.02)
    assert presupuesto.agotado() is True
    assert "timeout global" in (presupuesto.motivo_corte() or "")


def test_el_corte_que_se_anota_no_se_pisa() -> None:
    """La primera razón del corte es la que queda."""
    presupuesto = Presupuesto(timeout_s=10)
    presupuesto.anotar_corte("el modelo falló")
    presupuesto.anotar_corte("otra cosa")
    assert presupuesto.corte == "el modelo falló"


def test_una_etapa_sin_tiempo_no_empieza_otra_vuelta() -> None:
    """El plazo se comprueba **antes** de la vuelta, no después.

    Sin esto, el presupuesto sólo se miraba cuando la vuelta terminaba: una
    etapa que ya se había pasado el plazo igual arrancaba otra vuelta y se
    iba más allá del timeout global.
    """
    presupuesto = Presupuesto(timeout_s=0.01)
    time.sleep(0.02)
    with pytest.raises(PresupuestoAgotado):
        presupuesto.exigir_vivo()
    assert "timeout global" in (presupuesto.corte or "")


def test_una_etapa_con_tiempo_arranca_la_vuelta() -> None:
    """Con plazo, la vuelta arranca: no se corta antes de tiempo."""
    presupuesto = Presupuesto(max_pasos=6, max_tokens=4000, timeout_s=30)
    presupuesto.exigir_vivo()
    assert presupuesto.corte is None


def test_el_resumen_cuentra_lo_gastado() -> None:
    """El resumen es lo que queda escrito en la bitácora."""
    presupuesto = Presupuesto(max_pasos=6, max_tokens=4000, timeout_s=120)
    presupuesto.consumir_paso(tokens_prompt=100, tokens_completion=20)
    resumen = presupuesto.resumen()
    assert resumen["pasos"] == 1
    assert resumen["tokens"] == 120
    assert resumen["max_pasos"] == 6
    assert resumen["max_tokens"] == 4000
    assert resumen["timeout_s"] == 120
    assert resumen["corte"] is None


# ------------------------------------------------- el timeout global por nota


class RepositorioFalso:
    """Un repositorio mínimo: no hay ninguna conversación de la nota."""

    def obtener_conversacion_por_nota(self, nota_id: str) -> None:
        return None


def _servicio(nota_timeout_s: float) -> Any:
    """Un servicio de parte por voz con las piezas mínimas."""
    from config import Settings
    from planta.servicio import ServicioParteVoz

    settings = Settings(
        nota_timeout_s=nota_timeout_s,
        llm_activo=False,
    )
    return ServicioParteVoz(
        settings,
        repositorio=RepositorioFalso(),  # type: ignore[arg-type]
        almacenamiento=None,  # type: ignore[arg-type]
        transcriptor=None,  # type: ignore[arg-type]
        cliente=None,  # type: ignore[arg-type]
        planta={},
    )


def _nota() -> Any:
    from planta_contrato import NotaRecorrida

    return NotaRecorrida(
        id="NOTA-1",
        ts="2026-11-10T10:00:00-03:00",
        operador="juan",
        audio_url="http://planta/audio.mp3",
        fotos=[],
        recorrida_id="REC-1",
    )


def test_el_plazo_global_de_la_nota_empieza_cuando_entra() -> None:
    """El presupuesto de la nota arranca con la nota, no con el ASR."""
    servicio = _servicio(nota_timeout_s=180)
    servicio._preparar_nota(_nota())  # noqa: SLF001 - es del test
    presupuesto = servicio._presupuesto_de("NOTA-1")  # noqa: SLF001 - es del test
    assert presupuesto is not None
    assert presupuesto.timeout_s == 180
    assert presupuesto.agotado() is False


def test_si_el_plazo_se_pasa_la_nota_se_corta() -> None:
    """Pasado el plazo, la etapa se corta y el corte queda escrito."""
    servicio = _servicio(nota_timeout_s=0.01)
    servicio._preparar_nota(_nota())  # noqa: SLF001 - es del test
    time.sleep(0.02)

    cortado = servicio._cortar_si_se_paso_el_plazo("NOTA-1", "transcripcion")  # noqa: SLF001

    assert cortado is True


def test_una_nota_dentro_de_plazo_no_se_corta() -> None:
    servicio = _servicio(nota_timeout_s=180)
    servicio._preparar_nota(_nota())  # noqa: SLF001 - es del test
    assert (
        servicio._cortar_si_se_paso_el_plazo("NOTA-1", "transcripcion")  # noqa: SLF001
        is False
    )


def test_el_corte_de_la_nota_queda_registrado() -> None:
    """El corte se escribe en la trayectoria: no se pierde en el log."""
    from agente.trayectoria import RegistradorTrayectoria

    class RegistradorFalso(RegistradorTrayectoria):
        def __init__(self) -> None:
            super().__init__(None)
            self.filas: list[dict[str, Any]] = []

        def registrar(self, **campos: Any) -> None:
            self.filas.append(campos)

    servicio = _servicio(nota_timeout_s=0.01)
    servicio._registrador = RegistradorFalso()  # noqa: SLF001 - es del test
    servicio._preparar_nota(_nota())  # noqa: SLF001 - es del test
    time.sleep(0.02)

    servicio._cortar_si_se_paso_el_plazo("NOTA-1", "transcripcion")  # noqa: SLF001

    filas = servicio._registrador.filas  # noqa: SLF001 - es del test
    assert len(filas) == 1
    assert filas[0]["tipo"] == "corte"
    assert filas[0]["etapa"] == "nota"
    assert filas[0]["argumentos"]["etapa"] == "transcripcion"
    assert "timeout global" in (filas[0]["error"] or "")


def test_la_nota_sin_presupuesto_no_se_corta() -> None:
    """Una nota que ya no está en curso no tiene plazo: no se corta."""
    servicio = _servicio(nota_timeout_s=0.01)
    time.sleep(0.02)
    assert (
        servicio._cortar_si_se_paso_el_plazo("NOTA-NO-EXISTE", "transcripcion")  # noqa: SLF001
        is False
    )

