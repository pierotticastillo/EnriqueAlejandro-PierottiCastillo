# Estado de implementación

**Actualizado:** 2026-11-06  
**Plan de referencia:** documento interno del curso (no se publica en este
repositorio). El detalle de lo que se entrega está en [`../INFORME.md`](../INFORME.md).

Este archivo registra el avance del repositorio y distingue entre código implementado y comportamiento validado contra la planta simulada.

## Resumen

El proyecto tiene las **Fases 0, 0.5, 1, 2, 3 y 4 implementadas y verificadas**. La Fase 4 (RAG, contexto y memoria) quedó cerrada con medición real: el corpus de la planta está indexado en PostgreSQL + pgvector, el retrieval híbrido se midió contra la línea de base BM25 oficial sobre las 71 preguntas de `evaluacion/retrieval.json` y las conclusiones se validan con citas.

El punto de partida de la Fase 5 es: el servidor MCP ya expone `busqueda_rag` con fragmentos citables y hay un context builder que arma la evidencia de un escalado con sus citas.

## Avance por fase

| Fase | Estado | Avance y pendientes |
|---|---|---|
| 0 — Preparación del entorno | Completa | Compose (`postgres`, `migraciones`, `sistema`, `mcp`), red externa `planta-lan`, labels `academia.planta: pc`, proxy `enlace:3128` con `NO_PROXY`, presupuesto agregado 4 CPU / 8 GB y latido `sistema/estado`. |
| 0.5 — Esquema y migraciones | Completa | Alembic con esquema versionado (nota, foto, parte, fila_parte, conversación, pregunta, evento, alerta, orden, outbox, latido) e idempotencia por `evento_sistema.id`. Seis pruebas de idempotencia y constraints requieren base y se omiten sin `TEST_DATABASE_URL` (son 8 omitidas en total, contando las dos del índice RAG). |
| 1 — Parte por voz | Completa y verificada | Listener MQTT, descarga de medios a disco, ASR local (`faster-whisper`), extracción determinista, resolución de nombres y unidades, preguntas de ambigüedad y conversación con tablet. |
| 2 — Detección sin LLM | Completa y verificada | S1–S4 con los procedimientos del MBP-4, aviso en tablet y por radio al supervisor (canal 1), **un evento por equipo** (`parte:<nota>:<tag>`), contexto y tendencia en los escalados, sensores MQTT con límites de `/api/planta`. |
| 3 — Tools y MCP | Completa y verificada | Siete tools de solo lectura con `extra="forbid"`, cliente HTTP con timeout/reintentos/errores tipados, búsqueda léxica de manuales y servidor MCP (streamable-http y stdio) probado en vivo. |
| 4 — RAG, contexto y memoria | **Completa y verificada** | Índice híbrido (BM25 + embeddings) en PostgreSQL + pgvector con metadatos (`equipo`, `fabricante`, `documento`, `seccion`, `cita`, `vigente`), evaluación contra el baseline oficial, citas obligatorias en el context builder, política de memoria escrita y tool `busqueda_rag` expuesta por MCP. |
| 5 — Análisis y borrador de orden | Pendiente | Análisis de anomalías, evidencia, causas probables y reglas de aprobación en código. |
| 6 — Operación sin enlace | Pendiente | Outbox, reintentos ordenados e idempotencia al recuperar enlace. |
| 7 — Cierre y certificación | Pendiente | Cierre por voz, revisión de diferencias, certificación y habilitación. |

## Decisiones que conviene no perder de vista

1. **El manual de la planta es MBP-4** (cap. 10, §3.2, Anexo D). Los procedimientos de emergencia y los contactos de radio salen de ahí; no se inventan ni se adaptan de otro manual.
2. **El aviso por radio sale por la tablet.** El contrato no tiene un tópico de radio, así que el supervisor de turno (canal 1) se avisa con un segundo `MensajeTablet` con `espera_respuesta=false`, no inventando un canal nuevo.
3. **Un evento por equipo**, no un evento por nota: el corrector califica cada equipo por separado contra la verdad de campo.
4. **Los límites de alarma salen de `/api/planta`**, nunca del manual (MBP-4 §5).
5. **Una nota de seguridad no inventa mediciones.** Se publica la alerta y los avisos; no se pone un número en una fila que el operador no dijo.
6. **`structuredContent` del MCP tiene que ser un objeto.** Las salidas de lista van en un campo con nombre (`ListaEquipos.equipos`), no como `RootModel[list[...]]`.
7. **`planta-lan` es interna**, así que el puerto del servidor MCP se publica desde la red `debug` del compose, como el de PostgreSQL.
8. **El RAG no reemplaza a la API ni al SQL.** Los límites de alarma y el stock salen de `/api/planta` y de las tablas del sistema; del RAG sale **texto citable** (manuales, contrato, historial). Un límite leído del manual no se usa para decidir: se cita como referencia y el valor de alarma sale de la planta.
9. **La revisión vencida no es fuente.** `TX350-C` (Rev. C de 2019) queda en el índice con `vigente=false` para poder medir distractores, pero la búsqueda normal la excluye. Medido: **0 apariciones de la Rev. C antes que la D** en las 71 preguntas.
10. **La pata léxica del ranking es BM25, no el FTS de PostgreSQL.** `ts_rank_cd` no pondera por IDF, así que una pregunta con términos comunes la domina: medido `hit@5 0,61` contra `0,85` del BM25 de referencia. pgvector aporta los candidatos densos; el BM25 se calcula en el proceso con el mismo código que la evaluación.
11. **Embeddings locales y agnósticos al proveedor.** `Embedder` es un protocolo: `fastembed` con `paraphrase-multilingual-MiniLM-L12-v2` (384 dims) en Docker, `hashing` determinista en tests y como respaldo si el modelo no está.
12. **El historial se indexa una sola vez.** La planta lo sirve dos veces (como documento y como órdenes); se indexa desde `/api/historial-ordenes`, que además trae las órdenes del escenario, y se descartan los `id` repetidos.

## Fase 4 — qué quedó hecho

### Indexación (PostgreSQL + pgvector)

- Migración `0004`: `rag_chunk.embedding` pasa a `vector(384)`, se agregan `titulo`, `pagina`, `vigente`, `orden`, `actualizado_en`, índices únicos por `(documento_id, seccion)` y por `clave` de documento, índice HNSW de coseno e índice GIN de texto completo.
- `src/rag/indexer.py` vuelca el corpus con `INSERT ... ON CONFLICT`: reindexar **actualiza**, no duplica. `python -m rag.cli indexar` es idempotente.
- Metadatos guardados por documento: `equipo`, `fabricante`, `documento`, `tipo`, `version`, `vigente`; por fragmento: `seccion`, `cita`, `titulo`, `pagina`, `vigente`.
- Índice real cargado en la planta simulada: **7 documentos, 704 fragmentos** (458 de manuales y contrato + 246 órdenes).

### Retrieval y evaluación

Suite oficial de la planta: 71 preguntas (`evaluacion/retrieval.json`) con sus relevantes y distractores.

| Motor | hit@5 | recall@5 | MRR | distractor@5 | Rev. C antes que la D |
|---|---|---|---|---|---|
| BM25 oficial (referencia) | 0,845 | 0,664 | 0,573 | 0,099 | 39 |
| Híbrido en memoria (Fase 4) | 0,845 | 0,695 | 0,687 | **0,000** | **0** |
| **Híbrido pgvector (producción)** | **0,873** | **0,711** | 0,659 | **0,000** | **0** |

La ruta de producción mejora la referencia en `hit@5` (+0,028), `recall@5` (+0,047) y deja el distractor en cero. Evidencia guardada en [`evidencia_fase4_retrieval.json`](evidencia_fase4_retrieval.json) (índice en memoria) y [`evidencia_fase4_pgvector.json`](evidencia_fase4_pgvector.json) (índice real de PostgreSQL).

Comandos:

```bash
python -m rag.cli evaluar --corpus <planta>/datos/documentos --embedder fastembed
python -m rag.cli evaluar-pgvector --suite <planta>/datos/documentos/evaluacion/retrieval.json
```

### Citas y context builder

- `src/rag/contexto.py` arma el contexto de un escalado: parte, fotos, tendencia, fragmentos y **la lista de citas**. Un fragmento sin cita no entra.
- `validar_citas(texto, contexto)` devuelve las citas del texto que no están respaldadas por el contexto: es el control que obliga a que toda conclusión cite (`TX350-D §3.2`, `historial OT-25-0024`).

### Memoria

- `src/rag/memoria.py` deja escrita la política: la conversación de tablet y las preguntas se conservan `RAG_MEMORIA_DIAS` (90) días; **no se purga** el parte, los eventos, las alertas ni el registro operativo.
- `python -m rag.cli purgar` es *dry-run* por defecto; con `--aplicar` borra sólo conversación vencida. No toca la planta ni los mensajes ya publicados.

### Tool y despliegue

- `busqueda_rag` (octava tool) usa el recuperador híbrido y **cae a la búsqueda léxica de la Fase 3** si el índice no se puede armar.
- Compose: servicio efímero `rag-indexar` (perfil `rag`) y variables `RAG_*` para el servidor MCP.
- Dockerfile: el modelo de embeddings se prefetchea en la build (`/modelos/rag`), como el de ASR.
- El indexador necesita CPU: con 0,5 CPU la indexación de 704 fragmentos tardó
  ~451 s; se sube el límite a **1,5 CPU / 2 GB** (sigue siendo efímero y no
  entra en el presupuesto de 4 CPU / 8 GB de los servicios permanentes).

### Incidencia del entorno local (no del código)

La base PostgreSQL local (`localhost:55432`) puede quedar con una revisión de
Alembic que no corresponde a este workspace (`alembic_version = '0005'` y una
columna `rag_chunk.tokens`). En ese caso el servicio `migraciones` falla con
`Can't locate revision identified by '0005'` y el `rag-indexar` del compose no
arranca si se le pide esperar a las migraciones.

No se toca `alembic_version` a mano para no romper el estado de otro trabajo.
Workaround verificado: correr el indexador con

```bash
docker compose --profile rag run --rm --no-deps rag-indexar
```

o equivalentemente con `python -m rag.cli indexar --embedder fastembed` desde
el host. En una base limpia (volumen nuevo) el flujo normal
`docker compose --profile rag run --rm rag-indexar` funciona sin más.

## Última verificación

- `python -m pytest`: **190 pasaron, 0 omitidas** (con `TEST_DATABASE_URL` apuntando al PostgreSQL del compose). Sin base: **182 pasaron, 8 omitidas**.
- `ruff check src tests scripts migrations`: **sin errores**.
- Corrector oficial (`martes-tranquilo` + `fuga-sin-enlace`): **72,2/100 — APRUEBA**, sin eliminatorias.
- Servidor MCP probado en vivo contra la planta: **8 tools**, todas con `additionalProperties: false` y `structuredContent` válido (`scripts/probar_mcp.py`).
- Extr extractor contrastado contra la verdad de campo con las transcripciones reales del ASR (`scripts/analizar_notas.py`): todas las lecturas coinciden y las 22 decisiones de B1 dan el tipo que espera el corrector.
- Índice RAG cargado contra la planta simulada y retrieval medido sobre la tabla `rag_chunk` real (704 fragmentos, 71 preguntas).

## Pruebas en vivo que deja el repo

| Script | Qué muestra |
|---|---|
| `scripts/probar_mcp.py` | cliente MCP contra el servidor dockerizado: lista las tools y las llama |
| `scripts/probar_extractor.py` | el extractor con las transcripciones reales de la corrida |
| `scripts/analizar_corrida.py` | verdad de campo vs. eventos publicados, nota por nota |
| `scripts/analizar_borradores.py` | qué corrigió el operador en cada borrador y por qué |
| `scripts/analizar_notas.py` | extractor vs. verdad de campo, con el tipo de evento esperado |

## Próximos pasos

1. **Fase 5:** evidencia del escalado, causas probables con cita y borrador de orden con las reglas de aprobación en código.
2. **Fase 6:** outbox en Postgres y reintento ordenado al volver el enlace.
3. **Fase 7:** cierre por voz, control contra la orden, certificación y habilitación.
4. Reindexar el corpus con `docker compose --profile rag run --rm rag-indexar` cuando cambie la planta.
5. Confirmar la lista cerrada de modelos del curso (hoy `faster-whisper small` y `paraphrase-multilingual-MiniLM-L12-v2` son elecciones documentadas, no decisiones finales).

## Historial

- **2026-11-06:** se cerró la Fase 4. Corpus indexado en PostgreSQL + pgvector (7 documentos, 704 fragmentos) con metadatos y citas; retrieval híbrido medido sobre las 71 preguntas de la planta (producción: hit@5 0,873, recall@5 0,711, distractor@5 0,000 contra 0,845 / 0,664 / 0,099 del BM25 oficial) y la Rev. C vencida nunca aparece antes que la vigente. Se agregaron la tool `busqueda_rag`, el context builder con validación de citas, la política de memoria con purga por antigüedad, la migración `0004` y el servicio `rag-indexar` del compose. La suite quedó en 190 pruebas aprobadas (182 aprobadas y 8 omitidas sin base).
- **2026-11-05:** se cerraron las Fases 2 y 3. La detección S1–S4 usa los procedimientos del MBP-4 con aviso en tablet y por radio; el parte publica un evento por equipo; los eventos de escalado llevan contexto y tendencia. Se agregaron las siete tools de solo lectura y el servidor MCP, con la corrección de la salida estructurada (objeto, no arreglo). La corrida oficial dio 72,2/100 sin eliminatorias; la suite quedó en 152 pruebas aprobadas y 6 omitidas.
- **2026-11-05:** se corrigieron los artefactos del ASR que aparecieron en la corrida (`09` por “cero nueve”, `PS y` por “psi”, `Reservoir` por “reserva”, la palabra “diferencial” comida) y se loss de eventos por equipo.
- **2026-11-04:** se conectó la detección sostenida de sensores y la clasificación de eventos por parte confirmado. La suite quedó en 86 pruebas aprobadas y 6 omitidas; Ruff pasó.
- **2026-11-04:** se había agregado el flujo inicial de alertas S1–S4, con persistencia idempotente, publicación en `sistema/eventos` y aviso en tablet.
