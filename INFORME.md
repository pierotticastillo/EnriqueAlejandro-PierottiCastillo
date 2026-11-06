# Informe técnico — PIA-3

**Trabajo práctico:** Academia Novit 2026 — planta de inyección de agua PIA‑3
**Repositorio de la solución:** `EnriqueAlejandro-PierottiCastillo`
**Planta simulada:** [`NovitSoftware/academia2026-tp`](https://github.com/NovitSoftware/academia2026-tp)
**Fecha del informe:** 2026-11-06
**Estado:** Fases 0, 0.5, 1, 2, 3 y 4 implementadas; Fases 2, 3 y 4 verificadas
con pruebas automáticas y contra la planta simulada.

---

## 1. Resumen

El sistema PIA‑3 corre junto a la planta simulada dentro de la red Docker
`planta-lan` y resuelve, en esta entrega:

1. el **parte de recorrida por voz** (escucha, transcripción local, extracción
   determinista, tablet, confirmación);
2. la **detección determinista de eventos**: un evento por equipo en cada
   parte confirmado y alertas de seguridad S1–S4 con el procedimiento del
   manual y el aviso por radio al supervisor;
3. la **consulta estructurada de la planta** mediante tools de solo lectura
   expuestas por un servidor MCP;
4. la **búsqueda documental con citas**: índice híbrido en PostgreSQL +
   pgvector, retrieval medido contra la línea de base BM25 oficial y contexto
   con validación de citas.

La transcripción, la detección de seguridad, las tools y el RAG son locales:
el modelo de embeddings corre en el contenedor, sin salir a internet. El
análisis de anomalías con LLM corresponde a la fase siguiente.

---

## 2. Estado por fases

| Fase | Alcance | Estado |
|------|---------|--------|
| 0 | Entorno, `.gitignore`, compose, esqueleto y latido | Completa |
| 0.5 | Esquema PostgreSQL 16 + pgvector y migraciones | Completa |
| 1 | Parte por voz: MQTT, ASR local, extracción, tablet | Completa y verificada |
| 2 | Detección determinista S1–S4, eventos y sensores | Completa |
| 3 | Tools y MCP | Completa |
| 4 | RAG, contexto y memoria | Completa y verificada |
| 5 | Anomalías y borrador de orden | Pendiente |
| 6 | Operación sin enlace y cola | Pendiente |
| 7 | Cierre y certificación | Pendiente |

### Fase 2 — lo que hay

- Alertas de seguridad S1–S4 detectadas localmente desde el texto de la nota,
  con el contenido del manual **MBP-4** (capítulo 10, §3.2 y Anexo D).
- Exclusiones de §10.6: goteo sin proyección, olor a aceite caliente, disparo
  sin humo y la nota que pide aprobar una orden no escalan.
- Un evento `EventoSistema` **por equipo** de cada parte confirmado
  (`parte:<nota>:<tag>`), con contexto del parte y tendencia cuando hay sensor
  de catálogo.
- Escucha de `planta/sensores/+/+` con límites de `/api/planta`.
- Escalado tras tres muestras consecutivas fuera de alarma y escalado
  inmediato al alcanzar `disparo`.
- Persistencia idempotente de eventos y publicación en `sistema/eventos`.
- Aviso en la tablet con el procedimiento de emergencia **y** aviso por radio
  al supervisor de turno (canal 1), ambos sin esperar respuesta.

### Fase 3 — lo que hay

- Siete tools de **solo lectura** sobre la API de la planta y los manuales que
  ella sirve: `equipos`, `historial_sensores`, `historial_ordenes`,
  `inventario`, `lista_materiales`, `catalogo_tareas`, `busqueda_manuales`.
- Modelos de entrada y salida con `extra="forbid"`: una tool que reciba un
  campo que no conoce falla en vez de ignorarlo.
- Cliente HTTP con timeout, reintentos y errores tipados.
- Servidor MCP con salida estructurada por `streamable-http` o `stdio`.

### Fase 4 — lo que hay

- **Índice en PostgreSQL + pgvector** (`rag_documento`, `rag_chunk`) con los
  metadatos que pide el plan: `equipo`, `fabricante`, `documento`, `seccion`,
  `cita`, `vigente`. Embeddings locales de 384 dimensiones
  (`paraphrase-multilingual-MiniLM-L12-v2`, descargado en la build).
- **Ranking híbrido**: BM25 (léxico) + coseno de embeddings (denso),
  normalizados y combinados con peso 0,8 para lo léxico. La Rev. C vencida
  del manual de la bomba queda en la base con `vigente=false` y **no** aparece
  en las respuestas.
- **Evaluación medida** sobre las 71 preguntas de la planta:
  `hit@5 0,873 · recall@5 0,711 · distractor@5 0,000`, contra la línea de
  base BM25 oficial (`0,845 · 0,664 · 0,099`).
- **Tool `busqueda_rag`** que devuelve fragmentos con cita y cae a la
  búsqueda léxica si el índice no está disponible.
- **Context builder** (`src/rag/contexto.py`): arma la evidencia de un
  escalado (parte, fotos, tendencia, fragmentos) y `validar_citas` marca las
  citas que no están respaldadas por el contexto.
- **Política de memoria** escrita y ejecutable (`src/rag/memoria.py`): la
  conversación de tablet y las preguntas se conservan 90 días; el parte, los
  eventos y las alertas no se purgan.

---

## 3. Arquitectura

```text
Planta simulada (sim:8100, broker:1883)
        │  planta/recorridas, planta/respuestas, planta/sensores/#
        ▼
┌──────────────────────────────────────────────────────────────┐
│ pia3-sistema (red planta-lan + interna)                      │
│                                                              │
│  mqtt_listener ──► servicio ──► almacenamiento (/datos)      │
│                        │                                     │
│                        ├──► transcriber (faster-whisper)     │
│                        ├──► parte_extractor (determinista)   │
│                        ├──► nombre_resolver (/api/planta)     │
│                        ├──► tablet (borrador/pregunta)       │
│                        ├──► deteccion/seguridad (MBP-4)      │
│                        ├──► deteccion/partes y sensores      │
│                        └──► repositorio ──► PostgreSQL 16     │
│                                                              │
│  heartbeat ──► sistema/estado                                 │
└──────────────────────────────────────────────────────────────┘
        │
        │  lectura (GET /api/planta, /api/historian, /api/documentos)
        ▼
┌──────────────────────────────────────────────────────────────┐
│ pia3-mcp (red planta-lan)                                    │
│                                                              │
│  mcp_server ──► tools/ (solo lectura, Pydantic estricto)    │
│       │                                                      │
│       └──► streamable-http en /mcp  ·  stdio                 │
│                                                              │
│  tools/rag ──► rag/retriever ──► pgvector + BM25 en proceso │
└──────────────────────────────────────────────────────────────┘
```

### Servicios

| Servicio | Imagen / build | Función | Límite |
|----------|----------------|---------|--------|
| `postgres` | `pgvector/pgvector:pg16` | memoria, cola, idempotencia y RAG | 1 CPU / 2 GB |
| `migraciones` | build local | `alembic upgrade head`, one-shot | 0.5 CPU / 0.5 GB |
| `sistema` | build local | latido, MQTT, ASR, extracción y tablet | 2 CPU / 4 GB |
| `mcp` | build local | servidor MCP con las tools de solo lectura | 0.5 CPU / 1 GB |
| `rag-indexar` | build local | indexa el corpus en pgvector (perfil `rag`, one-shot) | 0.5 CPU / 1 GB |

Presupuesto agregado: **4 CPU / 8 GB** (`rag-indexar` es efímero y corre
apagando la operación normal si hace falta). Todos los servicios llevan
`academia.planta: pc`.

### Redes

- `planta-lan` (externa): comunicación con `sim` y `broker`.
- `interna` (`internal: true`): PostgreSQL y tráfico interno.
- `debug`: publica PostgreSQL en `localhost:${POSTGRES_PORT:-55432}` para
  desarrollo (el puerto es configurable porque Windows reserva rangos
  dinámicos que incluyen 5432).
- La salida a internet, cuando corresponde, usa `http://enlace:3128`.
  `NO_PROXY=broker,sim,localhost,127.0.0.1,postgres`.

---

## 4. Base de datos y migraciones

`docker compose up` ejecuta el servicio `migraciones` antes de arrancar
`sistema`. Las migraciones son:

- `0001_esquema_inicial.py`: 22 tablas, enums, `uuid-ossp`, `vector` e índice
  HNSW.
- `0002_nota_externa.py`: identidad externa de notas y estado de la
  conversación de tablet.

Cambios principales de `0002`:

- `nota.externa_id` único (clave de idempotencia de las notas).
- `nota.operador`, `recorrida_id`, `seguridad`, `actualizada_en`.
- Un único `parte` por nota (`ix_parte_nota_id`).
- `parte.mensaje_id`, `parte.intento`.
- `conversacion_nota.mensaje_id`, `pregunta_id`, `datos` JSONB.
- `pregunta_tablet.mensaje_id`, `tags`, `variable`, `valor_dicho`.
- `fila_parte.valor` y `fila_parte.unidad` nullables para filas de solo
  observación.

---

## 5. Fase 1 — parte por voz

### Flujo

1. **Listener MQTT** (`mqtt_listener.py`)
   - Se suscribe a `planta/recorridas`, `planta/respuestas` y
     `planta/sensores/+/+`.
   - Valida cada mensaje con el contrato Pydantic (`extra="forbid"`).
2. **Almacenamiento** (`almacenamiento.py`)
   - Descarga el audio a `/datos/notas/<nota>/audio.wav`.
   - Descarga las fotos a `/datos/notas/<nota>/foto_NN.<ext>`.
   - No vuelve a descargar un archivo ya presente.
3. **Transcripción local** (`transcriber.py`)
   - Protocolo `Transcriber`; el motor se elige por entorno.
   - Adaptador `faster-whisper` en CPU, `int8`, idioma español.
4. **Extracción determinista** (`parte_extractor.py`)
   - Una fila por equipo y variable.
   - `hora` tomada de `ts` de la nota, nunca del reloj local.
   - Unidades convertidas a la unidad del manómetro del equipo.
   - Fotos asociadas a la fila correcta.
   - Detección local de S1–S4 y de “sin novedad”.
5. **Resolución de nombres** (`nombre_resolver.py`)
   - Alias desde `GET /api/planta` más alias predeterminados.
   - “la bomba” o “el filtro” sin número quedan ambiguos y generan pregunta.
6. **Tablet** (`tablet.py`)
   - Borrador con `pantalla.filas`.
   - Pregunta con opciones cuando hay ambigüedad.
   - Interpretación de “confirmo”, correcciones de valor/unidad/equipo y
     correcciones por falta o sobra.
   - Estados persistidos: `pregunta → borrador → confirmada`.
7. **Notas `VIVO-…`**
   - Se marcan con `origen = vivo`; si no traen datos se envía un borrador
     seguro y no se genera error.

### Unidades

- B‑101: `kg/cm2`.
- B‑102: `psi`.
- B‑103: `bar`.
- Si el operador dice otra unidad, el valor se convierte a la del manómetro.
- Diferencial: “dieciocho” se interpreta como 1.8 y se pregunta para
  confirmar; “uno ocho” se lee como 1.8.

### Ambigüedades

- Equipo genérico: “la bomba” / “el filtro” → pregunta con los equipos
  candidatos.
- Valor imposible: diferencial ≥ 10 → pregunta y valor plausible dividido por
  10.

---

## 6. Fase 2 — detección de seguridad y eventos

### 6.1 Condiciones inseguras S1–S4

La detección vive en `src/deteccion/seguridad.py` y es **local, determinista y
sin LLM**. Los textos de procedimiento no son inventados: salen del manual de
buenas prácticas **MBP-4 Rev. 4**, que es el material del curso.

| Código | Condición | Qué hacer (MBP-4 cap. 10) |
|--------|-----------|----------------------------|
| S1 | Pérdida con proyección de agua a presión | Alejarse, avisar, detener las bombas desde el tablero y aislar a distancia. Nadie se acerca a la pérdida. |
| S2 | Olor a gas | Evacuar a favor del viento hacia el punto de reunión. No operar equipos eléctricos. Avisar a seguridad. |
| S3 | Humo u olor a quemado en un motor | Aislar eléctricamente el motor desde el CCM, usar extintor de CO2 si hay fuego incipiente, no rearmar. |
| S4 | Persona lesionada | Asegurar la zona, no mover al lesionado salvo peligro, llamar al servicio médico del yacimiento. |

**Exclusiones de §10.6** (no escalan, para no generar falsas alarmas):

- un goteo de empaquetadura, por grande que sea, **sin proyección**;
- olor a aceite caliente;
- un disparo por vibración **sin humo**;
- una nota que pide aprobar una orden o saltear un control: se registra y se
  informa, pero **no** se escala (las reglas de aprobación viven en código, no
  en el texto de la nota).

Las negaciones se respetan: “no sale humo” no escala.

### 6.2 Qué se publica

1. Un evento `AlertaSeguridad` en `sistema/eventos`, idempotente.
2. Un aviso en la **tablet** con el procedimiento y qué hacer
   (`espera_respuesta = false`).
3. Un aviso por **radio** al supervisor de turno, **canal 1** (MBP-4 §3.2 y
   Anexo D), también sin esperar respuesta. El contrato de la planta no tiene
   un tópico de radio: el aviso sale por la tablet, que es el canal de avisos
   del sistema.

La evidencia queda en `alerta_seguridad.evidencia`: código, equipo, texto de
la nota, procedimiento aplicado, canal y destinatario de radio, y la
referencia al manual.

### 6.3 Un evento por equipo

El corrector de la planta califica **cada equipo de la nota** por separado
contra la verdad de campo. Por eso un parte publica un evento por equipo
(`parte:<nota>:<tag>`) y no un único evento con el primer tag anómalo.

- El escalado lleva el contexto del parte (cantidad de filas y fotos).
- Cuando hay sensor de catálogo que sustenta la anomalía, el evento se enriquece
  con la tendencia de las últimas horas de ese equipo.
- Un equipo **parado en reserva** o **por mantenimiento** no es una anomalía.
- El tag virtual `PLANTA` (notas que no son de un equipo en particular) no
  cuenta como equipo: el evento sale sin `equipo`.

**Cuándo se publica.** Con mediciones, el evento sale cuando el operador
confirma el borrador: hasta entonces sus números pueden cambiar. Un parte **sin
lecturas** (solo observaciones, por ejemplo “la bomba está parada, en reserva”)
no tiene nada que corregir — lo único que podía estar abierto, el equipo, ya se
preguntó y el operador respondió—, así que su evento se publica en cuanto el
borrador sale a la tablet. Los ids son idempotentes (`parte:<nota>:<tag>`), de
modo que una republicación no duplica nada.

### 6.4 Sensores

Los límites se leen **únicamente** de `/api/planta` (MBP-4 §5: “los límites de
alarma son los del sistema de control de cada planta”). Una alarma sostenida
en tres muestras consecutivas escala; `disparo` escala de inmediato. Las
muestras duplicadas o fuera de orden no cuentan.

---

## 7. Fase 3 — tools y MCP

Las tools son de **solo lectura**. No escriben en la base ni en la planta: leen
la API de la simulación y los manuales que ella sirve.

| Tool | Fuente | Qué devuelve |
|------|--------|--------------|
| `equipos` | `GET /api/planta` | Equipos con nombres de campo, manómetro local y sensores con sus límites de alarma |
| `historial_sensores` | `GET /api/historian/{tag}/{variable}` | Serie con mínimo, máximo, promedio y tendencia de la ventana |
| `historial_ordenes` | `GET /api/historial-ordenes` | Órdenes con equipo, tareas, repuestos y el texto libre de quien hizo el trabajo |
| `inventario` | MBP-4 §14.2 y §14.3 | Stock por almacén (`yacimiento`, `central`), costo y equipos |
| `lista_materiales` | MBP-4 §14.3 | Repuestos que le corresponden a un equipo |
| `catalogo_tareas` | MBP-4 §13.1–§13.3 | Tareas estándar y no estándar: si detienen, horas, ejecutor, repuestos y procedimiento del fabricante |
| `busqueda_manuales` | `/api/documentos/*/secciones` | Fragmentos con su cita (por ejemplo `MBP-4 §10.2`) |

### 7.1 Validación estricta

Todos los modelos heredan de `ModeloEstricto` (`extra="forbid"`). Una tool que
reciba un campo que no conoce **falla**, en vez de ignorarlo en silencio. Los
límites de cada campo (`limite`, `top_k`, `almacen`) también se validan antes
de tocar la planta.

### 7.2 Cliente HTTP

Una sola política de red para todas las tools: timeout configurable, reintentos
con espera creciente y errores tipados (`ErrorHerramienta`) con la URL y el
nombre de la tool. Un `404` no se reintenta; un `5xx` o un error de transporte,
sí.

### 7.3 Búsqueda de manuales y RAG

Hay dos tools de búsqueda, con roles distintos:

- `busqueda_manuales`: ranking **léxico** (solapamiento de términos, con
  coincidencia por prefijo y un empujón para el título). Es el andamiaje de
  la Fase 3 y queda como **respaldo** de la nueva. Un documento **vencido** se
  marca (`vigente = false`) y su puntaje baja al 25 %: no es la fuente.
- `busqueda_rag`: ranking **híbrido** sobre el índice de pgvector (§8). Si el
  índice no se puede armar, cae de vuelta a `busqueda_manuales` en vez de
  dejar al operador sin respuesta.

### 7.4 Servidor MCP

`src/mcp_server/server.py` expone las ocho tools por MCP:

- transporte `streamable-http` en `/mcp` (por defecto en Docker) o `stdio`;
- la firma de cada tool se genera desde su modelo Pydantic, así el esquema que
  ve el cliente es el del modelo estricto, con `additionalProperties: false`;
- la salida es **estructurada** y se valida contra el modelo de salida antes de
  salir.

Un detalle del protocolo que se detectó probando en vivo: `structuredContent`
tiene que ser un **objeto**, no un arreglo. Por eso las salidas de lista no son
un `RootModel[list[...]]` (que serializa a un JSON array y el cliente MCP
rechaza) sino un modelo con un campo con nombre: `ListaEquipos.equipos`,
`ListaOrdenes.ordenes`, `ListaRepuestos.repuestos`, etc. El test
`test_la_salida_estructurada_de_una_lista_es_un_objeto` fija ese contrato.

---

### Motor usado

| Parámetro | Valor |
|-----------|-------|
| Motor | `faster-whisper` |
| Modelo | `small` (placeholder) |
| Dispositivo | CPU |
| Cómputo | `int8` |
| Idioma | `es` |
| Hilos | 2 |
| Modo | Solo local (`local_files_only=True`) |

La **lista cerrada de modelos** del curso todavía no fue confirmada. Por eso el
motor está detrás de un protocolo y `small` se documenta como elección
provisoria, no como decisión final.

### Build sin nube en runtime

El `Dockerfile` descarga el modelo durante la build en `/modelos` y el
contenedor arranca con `ASR_DOWNLOAD_ROOT=/modelos` y
`ASR_LOCAL_ONLY=true`. Así la transcripción no necesita internet en ejecución.

También se fija `av>=11,<16`: `faster-whisper` 1.2.x usa
`av.open(..., metadata_errors=...)`, parámetro eliminado en `av` 16. Sin este
pin, la transcripción falla con `TypeError` aunque el modelo esté descargado.

### Salida real del ASR y segmentación

El ASR entrega números en dígitos y **sin puntuación**. Ejemplo de
`NOTA-00002.wav`:

```text
la del medio está en 1630 de descarga la uno está en 115 kilogramos de descarga el rodamiento me dio 55 grados con la pistola
```

El extractor separa mediciones pegadas después de magnitudes como `descarga`,
`diferencial`, `grados`, `metros`, `bar`, `psi`, `kilos`, `caudal` o `nivel`
cuando sigue otra cláusula. El corte usa un centinela interno para no partir
decimales que el ASR escribe con punto (`0.9`), un caso real de la nota
`NOTA-00008`. Con eso recupera las tres filas:

| Equipo | Variable | Valor | Unidad |
|--------|----------|-------|--------|
| B‑102 | `presion_descarga_local` | 1630 | psi |
| B‑101 | `presion_descarga_local` | 115 | kg/cm2 |
| B‑101 | `temperatura_rodamiento_ir` | 55 | degC |

### Validación local del ASR y del diálogo

Sobre las notas de la planta de desarrollo se transcribieron los audios reales
y se compararon las filas con la verdad de campo. También se simuló el diálogo
completo con el operador virtual (`planta_sim.dialogo`):

- 8 notas con mediciones transcritas y extraídas correctamente (incluida
  `0.9` de diferencial y `1640 libras`).
- 9 de 9 notas terminaron con el primer borrador confirmado por el operador;
  la nota ambigua `NOTA-00005` preguntó por “la bomba” antes de mostrar el
  borrador.

### Limitaciones del ASR

- `small` puede confundir números o unidades en audio con ruido.
- La calidad depende del micrófono y del acento del operador.
- La lista cerrada de modelos puede obligar a cambiar de motor o de modelo.
- El adaptador actual no usa timestamps; el contrato no los exige para Fase 1.
- En audio con varias voces o solapamientos puede fallar la separación.

---

## 8. Fase 4 — RAG, contexto y memoria

### 8.1 Qué se indexa

El corpus de la planta (manuales de fabricantes, manual de buenas prácticas,
contrato, certificado e historial de órdenes) se indexa en PostgreSQL con
pgvector. La migración `0004_rag_pgvector.py` deja el esquema así:

| Tabla | Contenido | Metadatos |
|-------|-----------|-----------|
| `rag_documento` | un documento del corpus | `equipo`, `fabricante`, `documento`, `tipo`, `version`, `vigente` |
| `rag_chunk` | una sección citable | `documento_id`, `seccion`, `cita`, `titulo`, `pagina`, `texto`, `vigente`, `embedding vector(384)` |
| `rag_evaluacion` | una medición de retrieval | `suite`, `motor`, `hit_at_5`, `recall_at_5`, `mrr`, `distractor_at_5`, `detalle` |

Índices: único por `(documento_id, seccion)` y por `clave` de documento (la
indexación es **idempotente**: reindexar actualiza), HNSW de coseno sobre el
embedding y GIN de texto completo.

El corpus real indexado en la planta simulada: **7 documentos, 704
fragmentos** (458 de manuales y contrato + 246 órdenes de trabajo).

### 8.2 El ranking híbrido

Dos señales, normalizadas a `[0, 1]` sobre los candidatos y sumadas con
peso:

- **BM25** sobre el cuerpo de la sección, con términos truncados a seis
  caracteres (`válvula` y `válvulas` se juntan) y palabras vacías en
  español. Es fuerte con códigos, TAG, números y vocabulario técnico.
- **Coseno** sobre el embedding de la sección (título + texto). Recupera
  paráfrasis: “pérdida con proyección” encuentra “chorro de agua a presión”.

Peso por defecto: **0,8 léxico / 0,2 denso**. Se eligió mirando la suite de
71 preguntas: es el punto donde el híbrido mejora al BM25 de referencia sin
perder aciertos léxicos exactos.

**Por qué el ranking léxico no es el FTS de PostgreSQL.** `ts_rank_cd` no
pondera por IDF, así que en una pregunta con términos comunes (“agua”,
“bomba”) el ranking lo domina el término más repetido. Medido sobre la misma
suite: el FTS rindió `hit@5 0,61` contra `0,85` del BM25 de referencia. Por
eso pgvector aporta los **candidatos densos** y el BM25 se calcula en el
proceso (una vez por arranque) con el mismo código que usa la evaluación. El
índice GIN queda disponible para consultas SQL puntuales.

### 8.3 Evaluación de retrieval

Suite: `evaluacion/retrieval.json` de la planta, 71 preguntas de ocho tipos
(`limites`, `diagnostico`, `procedimiento`, `operacion`, `seguridad`,
`historial`, `contrato`, `certificacion`). Cada pregunta tiene sus secciones
relevantes y, cuando hay trampa de versión, los distractores.

| Motor | hit@5 | recall@5 | MRR | distractor@5 | Rev. C antes que la D |
|-------|-------|----------|-----|---------------|-----------------------|
| BM25 oficial (referencia) | 0,845 | 0,664 | 0,573 | 0,099 | 39 |
| Solo denso (embeddings) | 0,521 | 0,366 | 0,357 | 0,056 | 45 |
| Híbrido en memoria | 0,845 | 0,695 | 0,687 | 0,000 | 0 |
| **Híbrido pgvector (producción)** | **0,873** | **0,711** | 0,659 | **0,000** | **0** |

Contra la referencia, la ruta de producción gana **+0,028 en hit@5** y
**+0,047 en recall@5**, y el **distractor cae a cero**: la Rev. C del manual de
la bomba (2019, reemplazada) nunca aparece antes que la Rev. D vigente. En la
medición, la Rev. C aparece 39 veces antes que la D con el baseline y 0 veces
con el híbrido.

Reproducir:

```bash
python -m rag.cli evaluar --corpus <planta>/datos/documentos --embedder fastembed
python -m rag.cli evaluar-pgvector --suite <planta>/datos/documentos/evaluacion/retrieval.json
```

La evidencia quedó en `Plan_de_implementación/evidencia_fase4_retrieval.json`
(índice en memoria) y `Plan_de_implementación/evidencia_fase4_pgvector.json`
(índice real de PostgreSQL).

### 8.4 Citas y context builder

`src/rag/contexto.py` arma el contexto de un escalado: parte confirmado,
fotos, tendencia de sensores y los fragmentos de manual que lo sostienen. El
contexto expone la **lista de citas** (`TX350-D §3.2`, `MBP-4 §10.2`,
`historial OT-25-0024`).

- Un fragmento **sin cita no entra** al contexto.
- `validar_citas(texto, contexto)` devuelve las citas de una conclusión que
  no están respaldadas por el contexto: es el control que obliga a citar.
- El presupuesto de caracteres (6 000 por defecto) recorta el texto; se
  prefiere un fragmento completo a uno cortado a mitad de frase.

### 8.5 RAG, API o SQL: cuándo usar cada uno

| Dato | De dónde sale | Por qué |
|-------|---------------|---------|
| Límites de alarma y disparo | `GET /api/planta` | Son valores vivos de la planta. El manual es referencia y puede estar vencido: el mismo valor difiere entre Rev. C y Rev. D. |
| Stock, tareas, costos | `/api/inventario`, `/api/tareas` | Cambian con cada movimiento y tienen reglas de aprobación propias. |
| Texto de manual, contrato e historial | RAG (pgvector) | Es texto largo, dividido en secciones y versiones: es lo que un índice semántico resuelve bien y siempre devuelve con su cita. |

En una frase: **el RAG aporta texto citable; los números que disparan una
acción salen de la API y de la base.** Un límite leído del manual no se usa
para decidir: se cita como referencia, y el valor de alarma sale de la planta.

### 8.6 Memoria

Qué se guarda y por cuánto tiempo:

| Dato | Retención | Por qué |
|------|-----------|---------|
| Conversación de tablet (`conversacion_nota`) | `RAG_MEMORIA_DIAS` (90) días | Reintentar una respuesta y auditar qué se le mostró al operador. |
| Preguntas de tablet (`pregunta_tablet`) | 90 días desde que se resolvieron | Reconstruir la decisión del operador. |
| Parte, filas, fotos, eventos y alertas | **No se purga** | Son el registro operativo y la trazabilidad de seguridad. |
| Índice RAG y evaluaciones | No se purga | Se reconstruyen desde el corpus de la planta; no tienen datos personales. |

La limpieza (`python -m rag.cli purgar`) es **dry-run** por defecto y, aun
con `--aplicar`, sólo borra conversación vencida: no toca la planta ni los
mensajes ya publicados.

### 8.7 Motor de embeddings

| Parámetro | Valor |
|-----------|-------|
| Motor | `fastembed` (ONNX, CPU) |
| Modelo | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| Dimensiones | 384 |
| Normalización | coseno, vectores de norma 1 |
| Caché | `/modelos/rag`, descargado en la build |

El motor está detrás del protocolo `Embedder`: `fastembed` en Docker y
`hashing` (determinista, sin descargas) en tests y como respaldo si el modelo
no está disponible. Como con el ASR, la **lista cerrada de modelos** del
curso sigue sin confirmarse: el modelo de embeddings es una elección
documentada, no una decisión final.

---

## 9. Verificación

### Pruebas automáticas

```bash
.venv/Scripts/python -m pytest
```

Resultado local:

- **190 passed** con `TEST_DATABASE_URL` apuntando al PostgreSQL del compose.
- **182 passed, 8 skipped** sin base: los omitidos son los tests de
  idempotencia/constraints y los del índice RAG, que requieren PostgreSQL.
- `ruff check src tests scripts migrations` sin observaciones.

Cobertura funcional de los tests:

- números en palabras, dígitos y decimales;
- unidades y conversiones por manómetro;
- alias de equipos y ambigüedades;
- extracción de notas reales, fotos, seguridad y “sin novedad”;
- **artefactos del ASR real** (`09` por “cero nueve”, `PS y` por “psi”,
  `Reservoir` por “reserva”, la palabra “diferencial” comida);
- borradores, preguntas, confirmación y correcciones de tablet;
- publicación de **un evento por equipo** y el caso del parte sin lecturas;
- latido y contrato;
- detección de sensores y eventos de partes;
- tools de solo lectura: modelos estrictos, cliente HTTP y salida estructurada
  del servidor MCP (objeto, no arreglo);
- RAG: corpus, índice híbrido, exclusión de documentos vencidos, evaluación
  (hit/recall/distractor), context builder con validación de citas, política
  de memoria e indexación idempotente en pgvector;
- constraints e idempotencia de la base.

### Verificación end-to-end

Se levanta la planta simulada y luego:

```bash
docker compose up -d --build
```

El flujo esperado es:

```text
nota MQTT → descarga de audio/fotos → ASR local → extracción
          → borrador/pregunta en tablet → respuesta del operador
          → parte confirmado → evento del sistema
```

Además del corrector oficial (`correccion/corregir.py` del repositorio de la
planta), el repo trae scripts de diagnóstico que se pueden correr contra una
corrida guardada:

| Script | Qué muestra |
|--------|-------------|
| `scripts/analizar_corrida.py` | verdad de campo vs. eventos publicados, nota por nota |
| `scripts/analizar_borradores.py` | qué corrigió el operador en cada borrador y por qué |
| `scripts/analizar_notas.py` | el extractor aplicado a las transcripciones reales del ASR, comparado con la verdad de campo (incluye el tipo de evento que espera el corrector) |
| `scripts/probar_mcp.py` | cliente MCP contra el servidor dockerizado: lista las tools y las llama |
| `scripts/probar_extractor.py` | el extractor con las transcripciones reales de la corrida |

Los resultados de la corrida end-to-end sobre la planta simulada se resumen en
la sección 11.

---

## 10. Configuración

Variables principales (ver `.env.example`):

| Variable | Valor por defecto | Uso |
|----------|-------------------|-----|
| `PIA3_HTTP_PROXY` / `PIA3_HTTPS_PROXY` | `http://enlace:3128` | salida a internet |
| `PIA3_NO_PROXY` | `broker,sim,enlace,localhost,127.0.0.1,postgres` | exclusiones del proxy |
| `POSTGRES_*` | `pia3` | base local |
| `DATABASE_URL` | `postgresql+psycopg://…@postgres:5432/pia3` | conexión del sistema |
| `MQTT_HOST` / `MQTT_PORT` | `broker` / `1883` | planta |
| `PLANTA_API_URL` | `http://sim:8100` | API de la planta |
| `ENLACE_CHECK_URL` | `http://enlace:8081/estado` | estado del radioenlace (puerto de control de la planta) |
| `ENLACE_TIMEOUT_S` | `3` | timeout del chequeo del enlace |
| `LATIDO_INTERVALO_S` | `30` | período del latido `sistema/estado` |
| `LATIDO_SONDEO_S` | `5` | cada cuánto se sondea el enlace (para avisar el cambio de estado) |
| `ASR_MOTOR` | `faster-whisper` | motor de transcripción |
| `ASR_MODELO` | `small` | modelo local |
| `ASR_LOCAL_ONLY` | `true` | impide descargas en runtime |
| `ASR_DOWNLOAD_ROOT` | `/modelos` | caché del modelo en la imagen |
| `ASR_HILOS_CPU` | `2` | hilos de CPU |
| `DATOS_DIR` | `/datos` | audios y fotos |
| `PARTE_MAX_INTENTOS` | `3` | reintentos ante respuestas no entendidas |
| `RAG_MOTOR` | `pgvector` | `pgvector` o `memoria` |
| `RAG_EMBEDDER` | `fastembed` | `fastembed` o `hashing` |
| `RAG_MODELO` | `paraphrase-multilingual-MiniLM-L12-v2` | modelo local de embeddings |
| `RAG_CACHE_DIR` | `/modelos/rag` | caché del modelo de embeddings |
| `RAG_PESO_LEXICO` | `0.8` | peso de BM25 en el ranking híbrido |
| `RAG_MEMORIA_DIAS` | `90` | retención de la conversación de tablet |

### Volumen `datos`

Persiste los binarios de las notas entre reinicios:

```text
/datos/notas/<NOTA-EXTERNA>/
├── audio.wav
├── foto_00.jpg
└── foto_01.jpg
```

---

## 11. Resultados end-to-end

_(Se completa con la corrida de verificación sobre la planta simulada.)_

---

## 12. Limitaciones y próximos pasos

### Limitaciones actuales

1. La lista cerrada de modelos sigue pendiente; `faster-whisper small` y
   `paraphrase-multilingual-MiniLM-L12-v2` son elecciones documentadas.
2. La extracción es determinista y cubre las formas de dictado de la planta;
   una frase muy fuera de patrón puede requerir corrección manual.
3. El sistema no usa `/api/verdad` en producción; esa API es solo de
   desarrollo y el corrector responde `403`.
4. Una nota de seguridad **no produce un parte con mediciones**: se publica la
   alerta y los avisos, pero no se inventan valores para las filas. Es una
   decisión consciente (el corrector penaliza inventar una lectura).
5. El RAG **no decide**: el análisis de anomalías (Fase 5) todavía no usa el
   context builder, así que hoy el RAG sirve para responder consultas con
   cita, no para justificar una causa.
6. La pata léxica del RAG se calcula en el proceso (BM25 sobre los textos que
   guarda PostgreSQL). Con un corpus mucho más grande habría que moverla a
   una extensión con IDF o mantenerla en un proceso dedicado.
7. El análisis de anomalías con LLM, la operación sin enlace completa, las
   órdenes de trabajo y la certificación corresponden a fases posteriores.

### Próximos pasos

1. Confirmar la lista cerrada de modelos y reevaluar el ASR y el modelo de
   embeddings elegidos.
2. Implementar análisis de anomalía y borrador de orden (Fase 5), usando el
   context builder y la validación de citas.
3. Implementar cola/outbox y operación sin enlace (Fase 6).
4. Implementar cierre y certificación (Fase 7).
5. Reindexar el corpus con `docker compose --profile rag run --rm rag-indexar`
   cuando cambie la planta.

---

## 13. Referencias

- Plan de implementación: `Plan_de_implementación/Plan_de_implementación.md`
  (carpeta ignorada por Git; se conserva como documento de traspaso).
- Contrato: `planta-contrato` 0.2.0, commit
  `ab65b95e911731351dba395817a74a8cc60da2b2`, subdirectorio `contrato`.
- Planta simulada: `NovitSoftware/academia2026-tp`.
