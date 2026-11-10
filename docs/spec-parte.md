# Mini spec del flujo del parte

**Spec-Driven Development** — la especificación corta que dice qué tiene que
pasar, con qué presupuesto y con qué condiciones de corte. El código la
implementa; los tests la fijan.

- **Alcance**: la nota de voz del técnico → el parte en la tablet → el
  escalado → la orden de trabajo → la decisión del planificador.
- **Fuera de alcance**: el cierre por voz (Fase 7), que tiene su propio flujo.
- **Regla que manda sobre todo lo demás**: *el LLM redacta; el código decide*.
  Las seis reglas de `analisis/aprobacion.py` no se delegan al LLM, y el
  prompt **nunca** lleva texto libre del operador.

---

## 1. El flujo, etapa por etapa

```
nota de voz (MQTT)
  │
  ├─ 1. descarga: audio + fotos                [planta/almacenamiento.py]
  ├─ 2. transcripción (ASR local)              [planta/transcriber.py]
  ├─ 3. extracción: filas, preguntas, seguridad [planta/parte_extractor.py]
  ├─ 4. parte a la tablet (mensaje + borrador) [planta/tablet.py]
  ├─ 5. el operador confirma o corrige         [planta/servicio.py]
  ├─ 6. detección: filas anómalas → evento     [deteccion/]
  ├─ 7. análisis: evidencia → falla → borrador [analisis/servicio.py]
  ├─ 8. reglas de aprobación (en código)       [analisis/aprobacion.py]
  ├─ 9. orden persistida y publicada           [db/repositorio.py]
  ├─10. agente (LangChain): justification      [agente/]     ← auxiliar
  └─11. decisión del planificador              [planta/central.py] ← persona
```

Las etapas 1 a 9 son **deterministas**: sin modelo, sin red y sin espera.
Las etapas 10 y 11 son las que agregan criterio humano o capacidad de
consulta, y **ninguna de las dos puede cambiar el resultado de las etapas 8 y
9**.

## 2. Qué decide el código y qué decide el modelo

| Decisión | Quién | Dónde |
|---|---|---|
| Qué filas tiene el parte | código (reglas + ASR) | `planta/parte_extractor.py` |
| Qué falla es | código (catálogo + evidencia) | `analisis/causas.py` |
| Qué tareas y repuestos | código (plan de mantenimiento) | `analisis/orden.py` |
| Si la orden se publica, se detiene el equipo, quién la ejecuta | **código** (6 reglas) | `analisis/aprobacion.py` |
| Cómo se justifica por escrito | modelo / agente | `analisis/redaccion.py`, `agente/` |
| Si la orden pendiente se aprueba o se rechaza | **persona** (planificador) | `planta/central.py` |

El agente de LangChain **consulta y propone**: usa las mismas ocho tools del
MCP, con los mismos esquemas Pydantic estrictos (`extra="forbid"`), y su
salida se contrasta con el catálogo antes de usarse. Una cita que no está en
la lista de permitidas **ni** entre las que le mostrará una herramienta
**descarta la propuesta entera**; una falla que no está en el catálogo,
también. Las causas que el manual no respalda se omiten, pero no invalidan el
resto.

La propuesta tiene dos caminos de salida, y el agente usa el que el modelo
pueda: la **herramienta de salida** (`PropuestaAgente`) o el **cierre
estructurado** (la conversación se le vuelve a pasar al modelo con el
esquema). El cierre es necesario con los modelos pequeños: consultan bien,
pero no llaman a la herramienta de salida y escriben la propuesta en prosa.
Es el mismo modelo y la misma conversación, y queda registrado como un paso
más del agente.

## 3. Condiciones de corte

Una etapa se corta —y el corte queda escrito— cuando:

| # | Corte | Qué pasa | Dónde |
|---|---|---|---|
| C1 | El modelo no responde | Se cae al texto determinista | `analisis/servicio.py` |
| C2 | El modelo propone una falla que no está en el catálogo | Se descarta la propuesta | `agente/nucleo.py` |
| C3 | El modelo cita un documento desconocido | Se descarta la propuesta | `agente/nucleo.py` |
| C4 | Se agotan los **pasos** | Se corta la consulta | `agente/presupuesto.py` |
| C5 | Se agotan los **tokens** | Se corta la consulta | `agente/presupuesto.py` |
| C6 | Se agota el **timeout** de la consulta | Se corta la consulta | `agente/presupuesto.py` |
| C7 | Se pasa el **timeout global de la nota** | La nota sale con lo que hay | `planta/servicio.py` |
| C8 | La base no responde | La conexión falla en 5 s y se sigue por el camino léxico | `db/session.py` |
| C9 | El enlace (MQTT) está caído | Lo pendiente queda en la cola de salida | `planta/cola.py` |
| C10 | La orden ya se ejecutó o cerró | La redacción tardía no la reabre | `planta/servicio.py` |

**Ninguna condición de corte borra lo que ya se decidió.** Un corte se
escribe en la bitácora (`registro_trayectoria`, tipo `corte`) y el sistema
sigue con el camino determinista.

## 4. Presupuesto

| Presupuesto | Tope | Configuración | Qué acota |
|---|---:|---|---|
| Pasos del agente | 6 vueltas del modelo | `AGENTE_MAX_PASOS` | las vueltas y las tool calls por consulta |
| Tokens del agente | 12 000 tokens | `AGENTE_MAX_TOKENS` | el gasto de la consulta (con el cierre de la propuesta) |
| Timeout de la consulta | 420 s | `AGENTE_TIMEOUT_S` | el reloj del agente |
| **Timeout global por nota** | 180 s | `NOTA_TIMEOUT_S` | desde que la nota entra hasta que el parte sale |
| Timeout del modelo | 90 s | `OLLAMA_TIMEOUT_S` | cada llamada al modelo local |
| Timeout de la conexión a la base | 5 s | fijo | que una base caída no bloquee el sistema |

El presupuesto es un objeto (`agente/presupuesto.py`) con tres límites y un
reloj. Cuando se agota uno, dice **por qué** se cortó: ese motivo queda en la
bitácora.

El reloj se comprueba **antes de cada vuelta** del modelo (la guarda
`_GuardaPresupuesto`), no después: una etapa que ya se pasó el plazo no
arranca otra vuelta, así que el timeout global no se puede pasar "por una
vuelta más". La guarda se engancha siempre, haya bitácora o no.

El timeout de **cada llamada** al modelo es el presupuesto entero
(`AGENTE_TIMEOUT_S`), no el del redactor: con el modelo local una vuelta
tarda entre 60 y 110 s, así que con el timeout del redactor (90 s) algunas
vueltas morían por la red en vez de cortarse por el presupuesto.

Si la llamada se cae **por la red** (el radioenlace de la planta simulada
cierra los túneles), se reintenta **una vez**
(`AGENTE_MAX_INTENTOS=2`, `AGENTE_ESPERA_REINTENTO_S=15`). El reintento
está en la llamada al modelo, así que cubre la consulta y el cierre sin
repetir las tools, y respeta el presupuesto: si ya no queda tiempo, no
reintenta y el corte queda anotado. Un error del modelo (no de la red) no
se reintenta: se propaga.

## 5. La trayectoria

Cada paso queda registrado con lo que el enunciado pide (sección F): **el
modelo usado, las herramientas llamadas y sus argumentos, los tokens, el
costo y el tiempo**.

| Tipo de fila | Qué tiene |
|---|---|
| `modelo` | modelo, prompt, salida, tokens de entrada y de salida, costo, segundos, paso |
| `tool` | herramienta, argumentos, salida, segundos, paso |
| `consulta` | el resumen de la consulta: modelo, tokens, costo y segundos |
| `corte` | por qué se cortó, y el resumen del presupuesto |
| `decision` | la decisión del planificador, con su usuario y su motivo |

Tabla: `registro_trayectoria` (migración `0007`). El costo sale de los tokens
medidos: la tarifa del modelo (`PRECIO_USD_POR_MILLON`, 0 con el modelo local
de la planta) se aplica a los tokens de cada llamada.

## 6. La decisión del planificador

El enunciado (sección D) pide que la orden pendiente *"se reanuda cuando el
planificador la aprueba o la rechaza"*.

- La orden pendiente queda **persistida** y se vuelve a publicar con el
  mismo id después de un reinicio.
- La **Central de mantenimiento** (`planta/central.py`, puerto 8200) es el
  canal de la decisión: una pantalla con las órdenes que esperan decisión y
  una API JSON equivalente.
- Sólo se pueden decidir órdenes **`Pendiente de aprobacion`**: una orden
  ya decidida no se toca.
- **Rechazar exige motivo.**
- La decisión queda en `aprobacion` con su usuario, y en la trayectoria.

**La Central no es una tool.** No está en `tools.registro.REGISTRO` ni en el
servidor MCP: el agente no puede llegar a ella.

## 7. Los criterios de aceptación

Cada línea tiene un test que la fija.

| # | Criterio | Test |
|---|---|---|
| A1 | El agente consulta con herramientas y propone con citas | `tests/test_agente.py` |
| A2 | Las tools del agente son las del MCP, con el mismo esquema estricto | `tests/test_agente.py` |
| A3 | Una cita o una falla inventada descarta la propuesta | `tests/test_agente.py` |
| A4 | Cada paso queda con modelo, tools, argumentos, tokens y tiempo | `tests/test_agente.py`, `tests/test_db_central.py` |
| A5 | El presupuesto corta y el corte queda escrito | `tests/test_presupuesto.py` |
| A6 | El timeout global por nota corta la transcripción y el parte | `tests/test_presupuesto.py` |
| A7 | La Central aprueba y rechaza, y el rechazo pide motivo | `tests/test_central.py` |
| A8 | Sólo se deciden órdenes pendientes, y la decisión queda en `aprobacion` | `tests/test_db_central.py` |
| A9 | La Central no es una tool | `tests/test_central.py` |
| A10 | Sin agente, sin modelo o sin base, el sistema sigue igual | `tests/test_agente.py`, `tests/test_db_central.py` |
| A11 | Si el modelo escribe prosa, el cierre estructurado arma la propuesta | `tests/test_agente.py` |
