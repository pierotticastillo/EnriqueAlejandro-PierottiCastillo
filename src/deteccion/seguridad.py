"""Condiciones inseguras S1-S4: detección local, procedimiento y aviso por radio.

El contenido sale del **manual de buenas prácticas MBP-4** (capítulo 10
"Condiciones inseguras", §3.2 "Escalamiento" y Anexo D "Contactos"), que es el
material del curso. No se inventan procedimientos: lo que el manual no dice,
este módulo no lo dice.

Tres reglas del manual guían el detector:

* **§10.1 a §10.5** — qué es cada condición (S1 a S4) y qué hacer en cada caso.
* **§10.6** — lo que *no* es una condición insegura. Un goteo de empaquetadura
  sin proyección, el olor a aceite caliente o un disparo por vibración sin humo
  son fallas de equipo, no condiciones inseguras: avisarlas de más resta.
* **§3.2 y Anexo D** — las condiciones inseguras se escalan **siempre y de
  inmediato, por radio**, aunque no haya enlace de datos. El supervisor de
  turno escucha el **canal 1**.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from planta.texto import normalizar

# --------------------------------------------------------------------- canales

RADIO_SUPERVISOR = "1"
RADIO_SALA_CONTROL = "4"
RADIO_SERVICIO_MEDICO = "9"


@dataclass(frozen=True)
class Procedimiento:
    """Qué hacer ante una condición insegura, según MBP-4 capítulo 10."""

    codigo: str
    titulo: str
    como_aparece: str
    que_hacer: str
    aviso_por_radio: str
    contactos: tuple[str, ...] = field(default_factory=tuple)


# Textos tomados del manual. La cita es la referencia para el informe.
PROCEDIMIENTOS: dict[str, Procedimiento] = {
    "S1": Procedimiento(
        codigo="S1",
        titulo="Pérdida con proyección de agua a presión",
        como_aparece=(
            "Nota de voz que describe un chorro, niebla o 'sale agua con fuerza', "
            "con caída de la presión de descarga. La foto muestra daño estructural."
        ),
        que_hacer=(
            "Alejarse, avisar, detener las bombas desde el tablero y aislar a distancia. "
            "Nadie se acerca a la pérdida."
        ),
        aviso_por_radio=(
            "Condición insegura S1: pérdida con proyección de agua a presión. "
            "La cuadrilla se alejó y las bombas se detienen desde el tablero."
        ),
    ),
    "S2": Procedimiento(
        codigo="S2",
        titulo="Olor a gas",
        como_aparece=(
            "Solo nota de voz: 'olor a gas', 'huele a huevo podrido', alarma del detector personal."
        ),
        que_hacer=(
            "Evacuar a favor del viento hacia el punto de reunión. "
            "No operar equipos eléctricos. Avisar a seguridad."
        ),
        aviso_por_radio=(
            "Condición insegura S2: olor a gas. La cuadrilla evacúa a favor del viento "
            "hacia el punto de reunión; no se operan equipos eléctricos."
        ),
        contactos=("Seguridad del yacimiento",),
    ),
    "S3": Procedimiento(
        codigo="S3",
        titulo="Humo u olor a quemado en un motor",
        como_aparece="Nota de voz más el disparo de la bomba.",
        que_hacer=(
            "Aislar eléctricamente el motor desde el CCM, usar extintor de CO2 si hay fuego "
            "incipiente, no rearmar."
        ),
        aviso_por_radio=(
            "Condición insegura S3: humo u olor a quemado en un motor. "
            "El motor se aísla desde el CCM y no se rearma."
        ),
    ),
    "S4": Procedimiento(
        codigo="S4",
        titulo="Persona lesionada",
        como_aparece="Solo nota de voz.",
        que_hacer=(
            "Asegurar la zona, no mover al lesionado salvo peligro, llamar al servicio médico "
            "del yacimiento."
        ),
        aviso_por_radio=(
            "Condición insegura S4: persona lesionada. La zona está asegurada y no se mueve "
            "al lesionado; se llamó al servicio médico."
        ),
        contactos=(f"Servicio médico (radio canal {RADIO_SERVICIO_MEDICO} · interno 1000)",),
    ),
}

PELIGRO = (
    "Ninguna tarea de mantenimiento ni orden pendiente tiene prioridad sobre una condición "
    "insegura. La alerta no espera aprobación."
)

# --------------------------------------------------------------------- patrones

# S1 — pérdida con proyección de agua a presión. La proyección es lo que
# distingue S1 de un goteo: "agua a presión" sola no alcanza.
_S1 = re.compile(
    r"(?:sale|salio|saliendo|salta|saltando|chorro|niebla|proyeccion|proyecta|"
    r"proyectando|con fuerza|a borbotones|bombea)"
    r"[^.;!?]{0,60}?agua a presion"
    r"|agua a presion[^.;!?]{0,60}?(?:sale|saliendo|salta|chorro|niebla|proyeccion|"
    r"con fuerza|para todos lados|a borbotones)"
    r"|chorro de agua"
    r"|(?:se rompio|se revienta|revento|ruptura|explota|exploto)"
    r"[^.;!?]{0,60}?(?:descarga|presion)"
)

# S2 — olor a gas. Solo nota de voz.
_S2 = re.compile(
    r"olor a gas|huele a gas|fuga de gas|olor a huevo podrido|huele a huevo podrido|"
    r"alarma del detector|detector personal"
)

# S3 — humo u olor a quemado. El humo alcanza por sí solo; "olor a quemado"
# se acepta, pero no así "olor a aceite caliente" (§10.6).
_S3 = re.compile(r"\bhumo\b|olor a quemado|huele a quemado|fuego|llamas|chispas")
_SIN_HUMO = re.compile(r"\bhumo\b|fuego|llamas|olor a quemado|huele a quemado")

# S4 — persona lesionada.
_S4 = re.compile(
    r"persona lesionada|esta lesionado|esta lesionada|se golpeo|se lastimo|"
    r"se golpea|se lastima|esta sangrando|sangrando bastante|sangra mucho|herido|herida|"
    r"golpeo la (?:mano|cabeza|brazo|pierna)"
)

# §10.6 — un goteo de empaquetadura, por grande que sea, no proyecta agua.
_GOTEO = re.compile(r"goteo|gotea|goteando|gotas|empaquetadura|prensaestopas")
_ACEITE = re.compile(r"olor a aceite|aceite caliente|calienta el aceite")

# §10.6 — una nota que pide aprobar una orden o saltear un control no es una
# condición insegura ni una instrucción válida: se registra y se informa.
_INSTRUCCION_NO_AUTORIZADA = re.compile(
    r"aprobala|aprobalo|aprobala directamente|apruebe|autorizado por|autorizada por|"
    r"sin pasar por el planificador|sin el planificador|saltar el control|saltear|"
    r"sin esperar autoriz|cargue el kit|carga el kit|ya esta autorizada"
)

# Negación delante del_trigger: "no sale humo", "sin olor a gas".
_NEGACION = re.compile(r"\b(?:no|nunca|sin|jamas)\b")


def procedimiento(codigo: str) -> Procedimiento:
    """Devuelve el procedimiento del manual para ``S1`` a ``S4``."""
    try:
        return PROCEDIMIENTOS[codigo]
    except KeyError as exc:  # pragma: no cover - uso interno
        raise ValueError(f"Código de seguridad inválido: {codigo}") from exc


def detectar(texto: str) -> str | None:
    """Detecta la condición insegura de una nota, o ``None``.

    Determinista y local: sin nube y sin LLM. La misma nota da siempre el
    mismo código.
    """
    norm = normalizar(texto)
    if not norm:
        return None

    if _coincide(_S1, norm):
        return "S1"
    if _coincide(_S2, norm):
        return "S2"
    if _coincide(_S3, norm):
        # §10.6: el olor a aceite caliente cerca de una bomba en marcha no es
        # humo. Sin humo ni fuego explícito, no se escala.
        if _ACEITE.search(norm) and not _SIN_HUMO.search(norm):
            return None
        return "S3"
    if _coincide(_S4, norm):
        return "S4"
    return None


def _coincide(patron: re.Pattern[str], norm: str) -> bool:
    """``True`` si el patrón aparece sin una negación delante.

    "no sale humo" no es una condición insegura: el operador está diryendo que
    no hay humo.
    """
    for match in patron.finditer(norm):
        clausula = re.split(r"[.;!?]", norm[: match.start()])[-1]
        if not _NEGACION.search(clausula):
            return True
    return False


def es_goteo_sin_proyeccion(texto: str) -> bool:
    """``True`` si la nota describe un goteo que no proyecta agua (§10.6)."""
    norm = normalizar(texto)
    return bool(_GOTEO.search(norm)) and not _S1.search(norm)


def es_instruccion_no_autorizada(texto: str) -> bool:
    """``True`` si la nota pide aprobar algo o saltear un control (§10.6).

    No es una condición insegura: no se emite alerta. Se registra y se informa
    al supervisor; las reglas de aprobación viven en el código, no en la nota.
    """
    return bool(_INSTRUCCION_NO_AUTORIZADA.search(normalizar(texto)))


def texto_aviso_tablet(
    codigo: str,
    equipo: str | None = None,
    descripcion_foto: str | None = None,
) -> str:
    """Texto de la tablet: qué hacer, sin esperar respuesta.

    ``descripcion_foto`` es lo que el modelo de visión vio en la foto adjunta
    (si lo pudo describir). Es **información para quien revise**, no una
    decisión: si el modelo no está o no valida, el aviso sale igual que
    siempre, sin descripción.
    """
    proc = procedimiento(codigo)
    donde = f" en {equipo}" if equipo else ""
    foto = ""
    if descripcion_foto and descripcion_foto.strip():
        foto = f" La foto adjunta muestra: {descripcion_foto.strip()}"
    return (
        f"ALERTA DE SEGURIDAD {proc.codigo}{donde}: {proc.titulo}. "
        f"Qué hacer: {proc.que_hacer} "
        f"Avisa al supervisor de turno por radio (canal {RADIO_SUPERVISOR}). "
        f"{foto}"
        f"{PELIGRO}"
    )


def texto_aviso_radio(codigo: str, equipo: str | None = None) -> str:
    """Texto del aviso por radio al supervisor de turno (canal 1)."""
    proc = procedimiento(codigo)
    donde = f" ({equipo})" if equipo else ""
    extra = ""
    if proc.contactos:
        extra = " Contactar: " + "; ".join(proc.contactos) + "."
    return (
        f"Radio canal {RADIO_SUPERVISOR} - Supervisor de turno: {proc.aviso_por_radio}"
        f"{donde}. Procedimiento MBP-4 {proc.codigo}: {proc.que_hacer}{extra}"
    )


def evidencia(
    codigo: str,
    *,
    nota_id: str,
    texto: str,
    ts: datetime,
    equipo: str | None = None,
) -> dict:
    """Evidencia que queda en ``alerta_seguridad.evidencia``."""
    proc = procedimiento(codigo)
    return {
        "nota_id": nota_id,
        "tipo": codigo,
        "equipo": equipo,
        "texto": texto,
        "ts_nota": ts.isoformat(),
        "titulo": proc.titulo,
        "como_aparece": proc.como_aparece,
        "que_hacer": proc.que_hacer,
        "radio_canal": RADIO_SUPERVISOR,
        "radio_destinatario": "Supervisor de turno",
        "contactos": list(proc.contactos),
        "fuente": "MBP-4 Rev. 4, capítulo 10",
    }
