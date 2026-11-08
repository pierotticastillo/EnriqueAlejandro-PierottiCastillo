"""Un recorte de los manuales de la planta, para probar el análisis sin la planta.

Las secciones son las mismas que sirve la API de la planta: el catálogo de
fallas de cada manual de fabricante, las tareas del plan de mantenimiento y
la lista de repuestos. Los tests del análisis no dependen de que la planta
simulada esté levantada.

El texto es el de los manuales (MBP-4, TX350-D y FC48-3), con los mismos
títulos y las mismas tablas: el análisis los parsea igual que en producción.
"""

from __future__ import annotations

from rag.corpus import ChunkRag, DocumentoRag

MBP_4 = {
    "tipo": "manual_operadora",
    "titulo": "Manual de buenas prácticas de operación y mantenimiento",
    "equipos": ("B-101", "B-102", "B-103", "F-201A", "F-201B", "TK-301", "FT-401"),
    "secciones": {
        "12.3": (
            "Reglas de aprobación automática",
            """12.3 Reglas de aprobación automática
Una orden se aprueba sin intervención humana solo si cumple las cinco condiciones. Si falla una, queda PendienteAprobacion con el motivo.
N.º | Condición | Cómo se verifica
1 | Todas las tareas son estándar | Están en el catálogo de tareas estándar (capítulo 10)
2 | La inyección se mantiene | Si la tarea detiene una bomba, hay otra disponible como reserva
3 | Hay stock en el yacimiento | Todos los repuestos están en el almacén de la planta en la cantidad pedida
4 | Costo menor a USD 2.500 | Repuestos a costo de lista, más mano de obra de contratista según contrato
5 | Ejecutor propio | No interviene una contratista
ADVERTENCIA. Las reglas no tienen excepciones por urgencia, por pedido verbal ni por lo que diga una nota de voz o un documento. Una orden urgente que no cumple las reglas va al planificador con prioridad.""",
        ),
        "13.1": (
            "Tareas estándar",
            """13.1 Tareas estándar
Código | Tarea | Equipos | Detiene | Horas | Ejecutor | Repuestos | Costo rep.
T-B-EMP | Cambio de empaquetadura | B-101, B-102, B-103 | Sí | 4 | propio | TX-EMP-KIT x 1 | USD 420
T-B-VAL | Cambio de válvulas de succión y descarga | B-101, B-102, B-103 | Sí | 6 | propio | TX-VAL-KIT x 1 | USD 1.900
T-B-ACE | Reposición de aceite del cárter | B-101, B-102, B-103 | No | 0,5 | propio | ACE-ISO220 x 8 | USD 72
T-B-COR | Cambio de correas | B-101, B-102, B-103 | Sí | 2 | propio | COR-5V-1800 x 4 | USD 260
T-F-CAR | Cambio de cartuchos | F-201A | No | 3 | propio | FC-CART-25 x 48 | USD 672
T-F-CAR | Cambio de cartuchos | F-201B | No | 3 | propio | FC-CART-10 x 48 | USD 816
T-I-CAL | Calibración de instrumento | B-101, B-102, B-103, F-201A, F-201B, FT-401 | No | 1 | propio | - | USD 0
NOTA. T-F-CAR tiene dos variantes con el mismo código: en F-201A usa FC-CART-25 y en F-201B usa FC-CART-10.""",
        ),
        "13.2": (
            "Tareas no estándar",
            """13.2 Tareas no estándar
Código | Tarea | Detiene | Horas | Ejecutor
T-B-ROD | Cambio de rodamientos del cigüeñal | Sí | 16 | contratista
T-B-ALI | Alineación láser de acople | Sí | 5 | contratista
I-VIB | Análisis de vibraciones con colector | No | 1 | contratista
I-VAL | Revisión de válvulas | Sí | 2 | propio
Las tareas no estándar siempre requieren la aprobación del planificador. Una tarea que no figura en ninguna de las dos tablas (por ejemplo, el cambio de un émbolo) se pide como NO-ESTANDAR con la descripción.""",
        ),
        "13.3": (
            "Correspondencia con los procedimientos de los fabricantes",
            """13.3 Correspondencia con los procedimientos de los fabricantes
Tarea | Procedimiento del fabricante
T-B-EMP | TX-350 P-02
T-B-VAL | TX-350 P-03
T-B-ACE | TX-350 P-04
T-B-COR | TX-350 P-06
T-B-ROD | TX-350 P-07
T-B-ALI | TX-350 P-08
I-VAL | TX-350 P-09
I-VIB | TX-350 P-11
T-I-CAL | TX-350 P-10 / FC-48 FP-05
T-F-CAR | FC-48 FP-02 (y FP-03 si se cambia la junta)""",
        ),
        "14.2": (
            "Lista de repuestos y stock de referencia",
            """14.2 Lista de repuestos y stock de referencia
Código | Descripción | Unidad | Costo | Yacimiento | Central
TX-EMP-KIT | kit de empaquetadura TX-350 (3 émbolos) | kit | USD 420 | 3 | 12
TX-VAL-KIT | kit de válvulas de succión y descarga TX-350 | kit | USD 1.900 | 1 | 4
TX-ROD-CIG | juego de rodamientos de cigüeñal TX-350 | juego | USD 2.650 | 0 | 2
TX-SELLO-CAR | juego de sellos de cárter TX-350 | juego | USD 310 | 2 | 6
ACE-ISO220 | aceite ISO VG 220 para cárter | litro | USD 9 | 120 | 800
COR-5V-1800 | correa 5V-1800 | unidad | USD 65 | 8 | 40
ACO-ELAST-90 | elastómero de acople tamaño 90 | unidad | USD 150 | 1 | 5
FC-CART-25 | cartucho filtrante 25 µm | unidad | USD 14 | 96 | 600
FC-CART-10 | cartucho filtrante 10 µm | unidad | USD 17 | 40 | 600
FC-JUNTA-TAPA | junta de tapa de filtro FC-48 | unidad | USD 85 | 2 | 10
INS-TRANS-P | transmisor de presión 0-250 kg/cm2 | unidad | USD 980 | 1 | 3
El stock de esta tabla es el de referencia al emitir el manual. El stock real es el del sistema de almacén.""",
        ),
        "14.3": (
            "Lista de materiales por equipo",
            """14.3 Lista de materiales por equipo
Equipo | Repuestos que le corresponden
B-101 | TX-EMP-KIT, TX-VAL-KIT, TX-ROD-CIG, TX-SELLO-CAR, ACE-ISO220, COR-5V-1800, ACO-ELAST-90, INS-TRANS-P
B-102 | TX-EMP-KIT, TX-VAL-KIT, TX-ROD-CIG, TX-SELLO-CAR, ACE-ISO220, COR-5V-1800, ACO-ELAST-90, INS-TRANS-P
B-103 | TX-EMP-KIT, TX-VAL-KIT, TX-ROD-CIG, TX-SELLO-CAR, ACE-ISO220, COR-5V-1800, ACO-ELAST-90, INS-TRANS-P
F-201A | FC-CART-25, FC-JUNTA-TAPA, INS-TRANS-P
F-201B | FC-CART-10, FC-JUNTA-TAPA, INS-TRANS-P""",
        ),
    },
}

TX350_D = {
    "tipo": "manual_fabricante",
    "titulo": "Bomba alternativa triplex Trimax TX-350",
    "equipos": ("B-101", "B-102", "B-103"),
    "secciones": {
        "8.2": (
            "Tabla de fallas",
            """8.2 Tabla de fallas
Código | Síntoma | Causa probable | Acción recomendada
TX-T01 | Goteo creciente en uno o más prensaestopas; leve caída de caudal | Desgaste normal de los anillos | Ajustar el prensaestopas; si después de dos ajustes sigue por encima de 30 gotas/min, cambiar la empaquetadura. Si se repite en el mismo émbolo, inspeccionar el émbolo.
TX-T02 | Vibración alta, pulsación en la descarga, caudal bajo y golpepeo metálico | Desgaste por horas de marcha | Cambiar el kit de válvulas de las tres cámaras. Si la falla se repite pronto, revisar la filtración aguas arriba antes de volver a cambiar.
TX-T03 | Vibración que sube y se acelera, con temperatura de aceite en alza | Fatiga del rodamiento por horas | Programar el cambio de rodamientos del cigüeñal antes de llegar al disparo. Es una intervención mayor del extremo de potencia que debe hacer un servicio autorizado.
TX-T09 | Chorro o niebla de agua a presión; caída de la presión de descarga | Rotura de tapa o junta | Alejarse. Dar aviso de emergencia. Detener las bombas desde el tablero y aislar a distancia. No intentar contener la pérdida.""",
        ),
        "8.3": (
            "TX-T01 Pérdida por empaquetadura",
            """8.3 TX-T01 Pérdida por empaquetadura
La empaquetadura se gasta con las horas de marcha y con los sólidos del agua. Al principio el goteo aumenta despacio; cuando los anillos pierden la forma, el goteo crece rápido y puede convertirse en un chorro.
Aspecto | Detalle
Síntoma | Goteo creciente en uno o más prensaestopas; leve caída de caudal
Firma en sensores | Caída leve de caudal (unas decenas de m³/d por bomba). Presiones, temperatura y vibración sin cambios.
Firma en la recorrida | Goteo por encima de 10 gotas/min en un prensaestopas, que crece de una recorrida a la siguiente. Charco o rastro de agua bajo el extremo hidráulico. En la foto se ve agua escurriendo del prensaestopas.
Causas probables | 1) Desgaste normal de los anillos; 2) Ajuste excesivo o insuficiente del prensaestopas; 3) Émbolo rayado (si la falla se repite en el mismo émbolo a las pocas semanas); 4) Agua con sólidos
Cómo confirmar | Contar las gotas por minuto en cada prensaestopas. Comparar con la recorrida anterior.
Acción recomendada | Ajustar el prensaestopas; si después de dos ajustes sigue por encima de 30 gotas/min, cambiar la empaquetadura. Si se repite en el mismo émbolo, inspeccionar el émbolo.
Procedimiento | P-02
Repuestos | TX-EMP-KIT (1 kit)
¿Detiene la bomba? | Sí, durante el cambio (unas 4 h)
NOTA. Una pérdida que proyecta agua no es una falla de empaquetadura normal: es una condición insegura (ver TX-T09).""",
        ),
        "8.4": (
            "TX-T02 Válvulas de succión o descarga gastadas",
            """8.4 TX-T02 Válvulas de succión o descarga gastadas
Un disco o un asiento gastado no cierra a tiempo: parte del agua vuelve a la cámara en cada carrera. La presión de descarga oscila mucho más que lo normal y la bomba entrega menos caudal a la misma velocidad.
Aspecto | Detalle
Síntoma | Vibración alta, pulsación en la descarga, caudal bajo y golpepeo metálico
Firma en sensores | Vibración en aumento gradual, por encima de 7,1 mm/s en casos avanzados. Pulsación de la presión de descarga mayor a ±2 kg/cm². Caudal en baja (hasta 15 %). La temperatura del aceite no cambia.
Firma en la recorrida | Golpeteo metálico rítmico en el extremo hidráulico, una vez por vuelta. La aguja del manómetro local oscila.
Causas probables | 1) Desgaste por horas de marcha; 2) Agua con sólidos o arena (filtro aguas arriba roto); 3) Cavitación previa; 4) Resorte de válvula roto; 5) Amortiguador de pulsaciones descargado
Cómo confirmar | Inspección de válvulas (P-09). Verificar antes la precarga del amortiguador.
Acción recomendada | Cambiar el kit de válvulas de las tres cámaras. Si la falla se repite pronto, revisar la filtración aguas arriba antes de volver a cambiar.
Procedimiento | P-03 (cambio) / P-09 (inspección)
Repuestos | TX-VAL-KIT (1 kit)
¿Detiene la bomba? | Sí, unas 6 h""",
        ),
        "8.5": (
            "TX-T03 Rodamiento de cigüeñal dañado",
            """8.5 TX-T03 Rodamiento de cigüeñal dañado
El daño de un rodamiento de rodillos cónicos progresa: picaduras en la pista que se agrandan. La vibración sube despacio al principio y cada vez más rápido. El roce calienta el aceite y la tapa del cojinete.
Aspecto | Detalle
Síntoma | Vibración que sube y se acelera, con temperatura de aceite en alza
Firma en sensores | Vibración en aumento con tendencia que se acelera; supera 7,1 mm/s y puede llegar al disparo de 11,2 mm/s. Temperatura de aceite en alza, que puede pasar la alarma de 80 °C.
Firma en la recorrida | Ruido de rodamiento (zumbido o rumor grave, distinto del golpepeo de válvulas). Temperatura de rodamiento medida con IR por encima de 85 °C. Limaduras en el visor de aceite en casos avanzados.
Causas probables | 1) Fatiga del rodamiento por horas; 2) Lubricación deficiente (aceite bajo o contaminado); 3) Agua en el aceite; 4) Desalineación sostenida del acople; 5) Montaje defectuoso
Cómo confirmar | Medición de vibraciones con espectro (P-11): frecuencias de defecto de pista. Análisis de aceite: hierro alto.
Acción recomendada | Programar el cambio de rodamientos del cigüeñal antes de llegar al disparo. Es una intervención mayor del extremo de potencia que debe hacer un servicio autorizado.
Procedimiento | P-07
Repuestos | TX-ROD-CIG (1 juego) + TX-SELLO-CAR (1 juego)
¿Detiene la bomba? | Sí, unas 16 h
ADVERTENCIA. No siga operando una bomba con rodamientos dañados hasta el disparo: un rodamiento que colapsa puede dañar el cigüeñal y el cárter.""",
        ),
        "8.11": (
            "TX-T09 Pérdida con proyección de agua",
            """8.11 TX-T09 Pérdida con proyección de agua
Rotura de una tapa de válvula, de una junta, de una cañería de descarga o un prensaestopas que colapsa. El agua a más de 100 kg/cm² es capaz de cortar y de inyectarse bajo la piel.
Aspecto | Detalle
Síntoma | Chorro o niebla de agua a presión; caída de la presión de descarga
Firma en sensores | Caída de la presión de descarga, que puede bajar de 95 kg/cm². Caudal inyectado en baja.
Firma en la recorrida | Ruido de chorro, niebla o charco que crece. Daño estructural visible en la foto.
Causas probables | 1) Rotura de tapa o junta; 2) Fisura en el extremo hidráulico; 3) Rotura de cañería de descarga
Cómo confirmar | No acercarse para confirmar.
Acción recomendada | Alejarse. Dar aviso de emergencia. Detener las bombas desde el tablero y aislar a distancia. No intentar contener la pérdida.
Procedimiento | Plan de respuesta a emergencias de la planta
Repuestos | Según el daño
¿Detiene la bomba? | Sí
PELIGRO. No acercar las manos ni el cuerpo a una pérdida de agua a presión.""",
        ),
    },
}

FC48_3 = {
    "tipo": "manual_fabricante",
    "titulo": "Filtro de cartuchos Filtrapat FC-48",
    "equipos": ("F-201A", "F-201B"),
    "secciones": {
        "8.1": (
            "Tabla de fallas",
            """8.1 Tabla de fallas
Las causas están ordenadas de más a menos frecuente en la flota de filtros FC-48 en servicio de inyección. Revise el historial del filtro: si una falla se repite, la causa de fondo puede ser otra.
Código | Síntoma | Causa probable | Acción recomendada
FC-T01 | Presión diferencial alta y en aumento | Fin de vida normal de los cartuchos | Cambiar los 48 cartuchos. Si la vida fue mucho más corta que la habitual, investigar la calidad del agua.
FC-T02 | Caída brusca de la presión diferencial | Cartucho colapsado por ΔP excesiva | Cambiar todos los cartuchos e inspeccionar la placa. Avisar a mantenimiento de bombas: las válvulas pueden estar dañadas.""",
        ),
        "8.2": (
            "FC-T01 Cartuchos colmatados",
            """8.2 FC-T01 Cartuchos colmatados
Los cartuchos retuvieron sólidos hasta el límite de su capacidad. Es el fin de vida normal del cartucho. Si no se cambian, la presión diferencial sigue subiendo, cae la presión de succión de las bombas aguas abajo y estas pueden cavitar.
Aspecto | Detalle
Síntoma | Presión diferencial alta y en aumento
Firma en sensores | Presión diferencial por encima de 1,5 kg/cm² con tendencia en aumento. Presión de succión de las bombas en baja, todas a la vez.
Firma en la recorrida | Manómetro diferencial local alto, coincidente con el sistema de control. En las bombas puede oírse cavitación.
Causas probables | 1) Fin de vida normal de los cartuchos; 2) Colmatación acelerada por cambio de calidad del agua; 3) Filtro anterior en bypass; 4) Biopelícula bacteriana
Cómo confirmar | Corregir la ΔP por caudal y comparar con la tendencia de los días anteriores.
Acción recomendada | Cambiar los 48 cartuchos. Si la vida fue mucho más corta que la habitual, investigar la calidad del agua.
Procedimiento | FP-02
Repuestos | 48 x FC-CART-25 (primario) o 48 x FC-CART-10 (secundario)
¿Saca el filtro de servicio? | Queda en bypass unas 3 h""",
        ),
        "8.3": (
            "FC-T02 Cartucho roto o mal asentado",
            """8.3 FC-T02 Cartucho roto o mal asentado
Un cartucho se partió, se aplastó o se salió de su asiento: el agua pasa por ahí sin filtrar, por el camino de menor resistencia. La presión diferencial cae de golpe y queda baja. Desde afuera no se ve nada.
Aspecto | Detalle
Síntoma | Caída brusca de la presión diferencial
Firma en sensores | Caída de la presión diferencial de varias décimas en minutos, hasta menos de 0,15 kg/cm², sin cambio de caudal.
Firma en la recorrida | Nada visible. El manómetro diferencial local marca bajo. Días después, golpepeo de válvulas en las bombas.
Causas probables | 1) Cartucho colapsado por ΔP excesiva; 2) Cartucho mal trabado en el último cambio; 3) O-ring de base dañado; 4) Placa portacartuchos deformada
Cómo confirmar | Verificar que el bypass esté cerrado. Comparar turbidez de entrada y salida.
Acción recomendada | Cambiar todos los cartuchos e inspeccionar la placa. Avisar a mantenimiento de bombas: las válvulas pueden estar dañadas.
Procedimiento | FP-02 y FP-04
Repuestos | 48 cartuchos del grado + FC-ORING-B
¿Saca el filtro de servicio? | Queda en bypass unas 3 h""",
        ),
    },
}

# La revisión anterior del manual de las bombas: no entra al catálogo.
TX350_C = {
    "tipo": "manual_fabricante",
    "titulo": "Bomba alternativa triplex Trimax TX-350 (Rev. C)",
    "equipos": ("B-101", "B-102", "B-103"),
    "vigente": False,
    "secciones": {
        "8.3": (
            "TX-T01 Pérdida por empaquetadura",
            """8.3 TX-T01 Pérdida por empaquetadura
Aspecto | Detalle
Síntoma | Goteo creciente en uno o más prensaestopas; leve caída de caudal
Firma en la recorrida | Goteo por encima de 10 gotas/min en un prensaestopas.
Causas probables | 1) Desgaste normal de los anillos
Cómo confirmar | Contar las gotas por minuto en cada prensaestopas.
Acción recomendada | Ajustar el prensaestopas.
Procedimiento | P-02
Repuestos | TX-EMP-KIT (1 kit)
¿Detiene la bomba? | Sí""",
        ),
    },
}


# El contrato de servicios con la contratista: la lista de precios (§2.1) y
# las reglas de certificación (§3). El monto del habilita sale de acá.
CT_SVP_2025 = {
    "tipo": "contrato",
    "titulo": "Contrato de servicios de mantenimiento CT-SVP-2025",
    "equipos": ("B-101", "B-102", "B-103", "F-201A", "F-201B"),
    "secciones": {
        "1.4": (
            "Repuestos",
            """1.4 Repuestos
Los repuestos los provee la operadora desde sus almacenes y no integran el precio del servicio. Si la contratista provee un repuesto con autorización escrita del planificador, se reconoce a costo de la lista de materiales de la operadora más 10 %. Un repuesto provisto sin autorización no se paga.""",
        ),
        "2.1": (
            "Precios vigentes 2025-2026",
            """2.1 Precios vigentes 2025-2026
Precios en dólares estadounidenses, sin IVA. Se facturan en pesos al tipo de cambio del día anterior a la factura.
Ítem | Descripción | Unidad | Precio | Horas incluidas
SVP-01 | Cambio de rodamientos del cigüeñal TX-350 (T-B-ROD) | precio cerrado | USD 1.150 | 16
SVP-02 | Alineación láser de acople y cambio de elastómero (T-B-ALI) | precio cerrado | USD 780 | 5
SVP-03 | Análisis de vibraciones con colector (I-VIB) | por equipo medido | USD 320 | 1
SVP-04 | Hora de técnico mecánico | hora | USD 68 | -
SVP-05 | Hora de ayudante | hora | USD 45 | -
SVP-06 | Movilización a planta (solo trabajos por hora) | por intervención | USD 180 | -
SVP-07 | Guardia de emergencia fuera de horario | por llamada | USD 350 | -""",
        ),
        "3.2": (
            "Cálculo del monto",
            """3.2 Cálculo del monto
Tomar las tareas de la orden y su ítem en la lista de precios vigente a la fecha de inicio.
Para precios cerrados, el precio del ítem. Para ítems por equipo, el precio por la cantidad de equipos de la orden.
Para ítems por hora, las horas del registro de ingreso o las declaradas, la menor.
Sumar los repuestos provistos por la contratista con autorización (costo + 10 %).
No sumar lo declarado que no está en la orden.
El monto del habilita sale de la orden y de la lista de precios, no de lo que declara el cierre.""",
        ),
    },
}


def _documento(clave: str, datos: dict) -> DocumentoRag:
    chunks = tuple(
        ChunkRag(
            seccion=numero,
            cita=f"{clave} §{numero}",
            texto=texto,
            titulo=titulo,
            vigente=datos.get("vigente", True),
            orden=orden,
        )
        for orden, (numero, (titulo, texto)) in enumerate(datos["secciones"].items())
    )
    return DocumentoRag(
        clave=clave,
        titulo=datos["titulo"],
        tipo=datos["tipo"],
        fabricante="Trimax" if clave.startswith("TX") else "Filtrapat",
        equipos=tuple(datos["equipos"]),
        vigente=datos.get("vigente", True),
        chunks=chunks,
    )


def documentos(*, con_revision_vencida: bool = False) -> list[DocumentoRag]:
    """Los documentos del recorte, como los devuelve la API de la planta."""
    documentos = [
        _documento("MBP-4", MBP_4),
        _documento("TX350-D", TX350_D),
        _documento("FC48-3", FC48_3),
        _documento("CT-SVP-2025", CT_SVP_2025),
    ]
    if con_revision_vencida:
        documentos.append(_documento("TX350-C", TX350_C))
    return documentos
