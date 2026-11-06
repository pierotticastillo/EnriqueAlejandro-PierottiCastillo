# PIA-3 — Sistema agéntico de la planta de inyección de agua

Trabajo práctico de la Academia Novit 2026. El sistema corre junto a la planta
simulada [`NovitSoftware/academia2026-tp`](https://github.com/NovitSoftware/academia2026-tp)
y cubre el parte de recorrida por voz, la detección determinista de eventos, el
análisis de anomalías con RAG, la operación sin enlace y el cierre/certificación
de órdenes.

> Nota: el repositorio de la planta es la **simulación**; este repositorio es la
> **solución**. El informe técnico está en [`INFORME.md`](INFORME.md).

## Estado

- [x] Fase 0 — `.gitignore`, `docker-compose.yml`, esqueleto y latido `sistema/estado`.
- [x] Fase 0.5 — esquema y migraciones.
- [x] Fase 1 — listener MQTT, transcripción local, extracción determinista y
      conversación con tablet, verificado end-to-end.
- [x] Fase 2 — alertas S1–S4 con procedimiento MBP-4 y aviso por radio,
      un evento por equipo en partes confirmados y detección sostenida de
      sensores con límites de `/api/planta`.
- [x] Fase 3 — tools de solo lectura con Pydantic estricto y servidor MCP.
- [x] Fase 4 — RAG híbrido en PostgreSQL + pgvector con citas y metadatos,
      retrieval medido contra el baseline BM25 oficial (hit@5 0,873 contra
      0,845), context builder con validación de citas y política de memoria.
- [ ] Fase 5 — análisis de anomalía y borrador de orden.
- [ ] Fase 6 — operación sin enlace y cola.
- [ ] Fase 7 — cierre y certificación.

## Requisitos

- Docker Engine + Docker Compose v2.
- La red externa `planta-lan`, que crea el compose de la planta simulada. Si
  todavía no existe:

  ```bash
  docker network create planta-lan
  ```

## Puesta en marcha

```bash
cp .env.example .env   # ajustar credenciales locales
docker compose up -d --build
```

- `postgres` publica en `localhost:${POSTGRES_PORT:-55432}` (PostgreSQL 16 +
  pgvector). El puerto es configurable porque Windows reserva rangos dinámicos
  que incluyen 5432; el sistema sigue usando `postgres:5432` dentro de la red.
- `sistema` se conecta a `broker:1883` y `sim:8100` dentro de `planta-lan`.
- `mcp` publica el servidor MCP en `localhost:${MCP_PORT:-58000}/mcp`
  (streamable-http) y lee la API de la planta desde `planta-lan`.
- La build descarga el modelo ASR local en `/modelos`; el contenedor arranca
  con `ASR_LOCAL_ONLY=true` y no necesita internet para transcribir.
- El volumen `datos` guarda audios y fotos en `/datos/notas/<nota>/`.

Para levantar solo la base de datos:

```bash
docker compose up -d postgres
```

## RAG (Fase 4)

El corpus documental de la planta (manuales, contrato e historial de órdenes)
se indexa en PostgreSQL + pgvector con metadatos (`equipo`, `fabricante`,
`documento`, `seccion`, `cita`, `vigente`) y embeddings locales de 384
dimensiones (`paraphrase-multilingual-MiniLM-L12-v2`, descargado en la build).

```bash
docker compose --profile rag run --rm rag-indexar   # indexa (idempotente)
```

- El índice es **híbrido**: BM25 (pata léxica) + coseno de embeddings (pata
  densa), con peso 0,8 para lo léxico. La Rev. C vencida del manual de la
  bomba queda en la base con `vigente=false` y **no** aparece en las
  respuestas.
- Medido sobre las 71 preguntas de la planta: **hit@5 0,873 · recall@5 0,711
  · distractor@5 0,000**, contra la línea de base BM25 oficial (0,845 ·
  0,664 · 0,099).
- La tool `busqueda_rag` devuelve fragmentos **con su cita** y cae a la
  búsqueda léxica si el índice no está disponible.

### RAG, API o SQL: por qué el RAG no decide

| Dato | De dónde sale | Por qué |
| --- | --- | --- |
| Límites de alarma y disparo | `/api/planta` | Son valores vivos de la planta; el manual es referencia histórica y puede estar vencido. |
| Stock, tareas y costos | `/api/inventario`, `/api/tareas` | Cambian con cada movimiento y tienen reglas de aprobación propias. |
| Texto de manual, contrato e historial | RAG (pgvector) | Es texto largo, con secciones y versiones: es lo que un índice semántico resuelve bien y siempre vuelve con su cita. |

En una frase: **el RAG aporta texto citable; los números que disparan una
acción salen de la API y de la base.**

### Memoria

La conversación de tablet y las preguntas se conservan 90 días
(`RAG_MEMORIA_DIAS`); el parte, los eventos, las alertas y el registro
operativo **no se purgan**. La limpieza es un *dry-run* por defecto:

```bash
docker compose exec mcp python -m rag.cli purgar          # cuenta
docker compose exec mcp python -m rag.cli purgar --aplicar # borra
```

## Parte de recorrida por voz (Fase 1)

El flujo completo es:

```text
planta/recorridas
  → descarga de audio y fotos (/datos)
  → transcripción local (faster-whisper, CPU/int8)
  → extracción determinista de FilaParte
  → resolución de nombres, unidades y ambigüedades
  → borrador o pregunta en la tablet
  → respuesta del operador
  → parte confirmado + EventoSistema
```

### Transcripción

- Motor: `faster-whisper`, modelo `small` como **placeholder** hasta confirmar
  la lista cerrada de modelos del curso.
- El motor está detrás de un protocolo (`Transcriber`), configurable por
  `ASR_MOTOR` y `ASR_MODELO`.
- El `Dockerfile` descarga el modelo en `/modelos` durante la build.
- Se fija `av>=11,<16` porque `faster-whisper` 1.2.x usa
  `av.open(metadata_errors=...)`, eliminado en `av` 16.
- La salida del ASR trae números en dígitos y sin puntuación; el extractor
  separa mediciones pegadas (`... de descarga la uno ...`).

### Extracción y unidades

- Una fila por equipo y variable.
- `hora` tomada de `ts` de la nota, nunca del reloj local.
- B‑101 guarda en `kg/cm2`, B‑102 en `psi` y B‑103 en `bar`; si el operador
  dicta otra unidad, se convierte.
- “dieciocho” de diferencial se interpreta como 1.8 y se pregunta para
  confirmar; “uno ocho” se lee como 1.8.

### Ambigüedades y tablet

- “la bomba” o “el filtro” sin número generan una pregunta con opciones.
- Si hay ambigüedad, el borrador se envía después de la respuesta.
- La tablet interpreta “confirmo”, correcciones de valor, unidad o equipo, y
  correcciones por falta o sobra.
- Los estados de la conversación se persisten en PostgreSQL
  (`pregunta → borrador → confirmada`).

### Notas `VIVO-…`

No tienen parte esperado. Se guardan con `origen = vivo`; si no traen datos se
envía un borrador seguro y no se genera error.

## Base de datos y migraciones

`docker compose up` ejecuta el servicio efímero `migraciones`
(`alembic upgrade head`) y recién después arranca `sistema`.

- `migrations/versions/0001_esquema_inicial.py`: 22 tablas, enums, `uuid-ossp`,
  `pgvector` e índice HNSW.
- `migrations/versions/0002_nota_externa.py`: `nota.externa_id` único, estado de
  la conversación de tablet, un parte por nota y `fila_parte.valor/unidad`
  nullables para filas de solo observación.

```bash
# aplicar migraciones manualmente
DATABASE_URL=postgresql+psycopg://pia3:pia3@localhost:55432/pia3 alembic upgrade head

# tests de idempotencia y constraints
TEST_DATABASE_URL=postgresql+psycopg://pia3:pia3@localhost:55432/pia3 pytest
```

Los tests cubren doble `evento_sistema.id`, doble `orden.id`, doble
`idempotency_key.clave`, `alerta_seguridad.tipo` fuera de S1–S4,
`certificacion.decision` inválida y `orden.estado` fuera del enum.

## Latido

El servicio `sistema` publica cada 30 s en MQTT (`sistema/estado`, QoS 1,
retain) un mensaje validado con Pydantic:

```json
{
  "sistema": "pia3",
  "enlace_ok": true,
  "pendientes_sincronizar": 0,
  "ts": "2026-11-04T12:00:00+00:00"
}
```

- `enlace_ok` se evalúa contra `ENLACE_CHECK_URL`; si no está definida se
  reporta `true` y se registra una advertencia.
- `pendientes_sincronizar` cuenta `outbox_cola` con estado `pendiente`; si la
  tabla todavía no existe (antes de las migraciones) se reporta `0`.

## Detección de sensores y seguridad

El listener se suscribe a `planta/sensores/+/+` y valida cada muestra con el
contrato `LecturaSensor`. Los límites se cargan desde `/api/planta`: una alarma
baja o alta debe aparecer en tres muestras consecutivas para generar un evento
`Escalado`; un valor que alcanza `disparo` se escala inmediatamente. Se ignoran
lecturas sin dato, desconocidas o repetidas/fuera de orden.

## Condiciones inseguras S1–S4 (Fase 2)

La detección es local, determinista y sin LLM. El módulo `src/deteccion/seguridad.py`
toma el contenido del **manual de buenas prácticas MBP-4** (capítulo 10
"Condiciones inseguras", §3.2 "Escalamiento" y Anexo D "Contactos"), que es el
material del curso: no hay procedimientos inventados.

- **Qué se detecta**: S1 pérdida con proyección de agua a presión, S2 olor a
  gas, S3 humo u olor a quemado en un motor, S4 persona lesionada.
- **Qué no se detecta** (§10.6): un goteo de empaquetadura sin proyección, el
  olor a aceite caliente, un disparo por vibración sin humo y la nota que pide
  aprobar una orden o saltear un control. Las negaciones ("no sale humo")
  tampoco escalan.
- **Qué se publica**: un evento `AlertaSeguridad` en `sistema/eventos`, un aviso
  en la tablet con el procedimiento y qué hacer, y un aviso por radio al
  **supervisor de turno, canal 1** (Anexo D). Los dos avisos salen con
  `espera_respuesta = false`: la alerta no espera aprobación.
- **Evidencia**: `alerta_seguridad.evidencia` guarda el código, el equipo, el
  texto de la nota, el procedimiento aplicado, el canal de radio y la
  referencia al manual.

### Un evento por equipo

Un parte confirmado publica **un evento por equipo** (`parte:<nota>:<tag>`), no
un único evento con el primer tag anómalo: el corrector de la planta califica
cada equipo de la nota por separado. El escalado lleva el contexto del parte
(cantidad de filas y fotos) y, cuando hay sensor de catálogo, la tendencia de
las últimas horas. Un equipo **parado en reserva o por mantenimiento** no es una
anomalía.

## Tools y servidor MCP (Fase 3)

Las tools son de **solo lectura**: leen la API de la planta y los manuales que
ella sirve. No escriben nada en la base ni en la planta.

| Tool | Qué devuelve |
|------|--------------|
| `equipos` | Catálogo de equipos con nombres de campo, manómetro local y sensores con sus límites de alarma |
| `historial_sensores` | Serie de un sensor con mínimo, máximo, promedio y tendencia |
| `historial_ordenes` | Órdenes del archivo con equipo, tareas, repuestos y el texto libre de quien hizo el trabajo |
| `inventario` | Stock de repuestos por almacén (`yacimiento`, `central`), con costo y equipos |
| `lista_materiales` | Repuestos que le corresponden a un equipo |
| `catalogo_tareas` | Tareas estándar y no estándar: si detienen, horas, ejecutor, repuestos y procedimiento del fabricante |
| `busqueda_manuales` | Fragmentos de manual con su cita (por ejemplo `MBP-4 §10.2`) |
| `busqueda_rag` | Fragmentos de manuales, contrato e historial con su cita, ordenados por ranking híbrido (BM25 + embeddings) |

- Los modelos de entrada y salida heredan de `ModeloEstricto`
  (`extra="forbid"`): una tool que reciba un campo que no conoce falla en vez
  de ignorarlo en silencio.
- El cliente HTTP tiene timeout, reintentos con espera creciente y errores
  tipados (`ErrorHerramienta`).
- `busqueda_manuales` es **léxica** (solapamiento de términos) y queda como
  respaldo: `busqueda_rag` usa el índice híbrido de pgvector y, si el índice
  no se puede armar, cae de vuelta a la búsqueda léxica. Los documentos
  vencidos se marcan y bajan de puntaje; en el RAG quedan fuera.
- El servidor MCP (`src/mcp_server/server.py`) expone todas las tools por
  `streamable-http` en `/mcp` o por `stdio`.

```bash
# listar y llamar tools con un cliente MCP
docker compose up -d mcp
docker compose exec mcp python -m mcp_server.server   # stdio
```

El catálogo de tareas, el stock y la lista de materiales se leen de las
secciones de MBP-4 que sirve la planta (`/api/documentos/MBP-4/secciones`),
que coinciden con la tabla de materiales de la simulación.

## Variables de entorno

Las principales están documentadas en `.env.example`:

| Variable | Default | Uso |
|----------|---------|-----|
| `PIA3_HTTP_PROXY` / `PIA3_HTTPS_PROXY` | `http://enlace:3128` | salida a internet |
| `PIA3_NO_PROXY` | `broker,sim,localhost,127.0.0.1,postgres` | exclusiones |
| `POSTGRES_*` | `pia3` | base local |
| `DATABASE_URL` | `postgresql+psycopg://…@postgres:5432/pia3` | conexión |
| `MQTT_HOST` / `MQTT_PORT` | `broker` / `1883` | planta |
| `PLANTA_API_URL` | `http://sim:8100` | API de la planta |
| `ASR_MOTOR` | `faster-whisper` | motor de transcripción |
| `ASR_MODELO` | `small` | modelo local |
| `ASR_LOCAL_ONLY` | `true` | sin descargas en runtime |
| `ASR_DOWNLOAD_ROOT` | `/modelos` | caché del modelo |
| `ASR_HILOS_CPU` | `2` | hilos de CPU |
| `DATOS_DIR` | `/datos` | audios y fotos |
| `PARTE_MAX_INTENTOS` | `3` | reintentos de tablet |
| `MCP_TRANSPORTE` | `streamable-http` | transporte del servidor MCP |
| `MCP_HOST` / `MCP_PUERTO` | `0.0.0.0` / `8000` | escucha del servidor MCP |
| `MCP_PORT` | `58000` | puerto del host para el servidor MCP |
| `RAG_MOTOR` | `pgvector` | `pgvector` o `memoria` |
| `RAG_EMBEDDER` | `fastembed` | `fastembed` o `hashing` |
| `RAG_MODELO` | `paraphrase-multilingual-MiniLM-L12-v2` | modelo local de embeddings |
| `RAG_CACHE_DIR` | `/modelos/rag` | caché del modelo de embeddings |
| `RAG_PESO_LEXICO` | `0.8` | peso de BM25 en el ranking híbrido |
| `RAG_MEMORIA_DIAS` | `90` | retención de la conversación de tablet |

## Estructura

```text
.
├── README.md
├── INFORME.md
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── pyproject.toml
├── alembic.ini
├── migrations/
│   ├── env.py
│   └── versions/
│       ├── 0001_esquema_inicial.py
│       ├── 0002_nota_externa.py
│       ├── 0003_cola_outbox.py
│       └── 0004_rag_pgvector.py
├── src/
│   ├── config.py
│   ├── db/
│   │   ├── repositorio.py
│   │   └── session.py
│   ├── deteccion/
│   │   ├── partes.py
│   │   ├── seguridad.py
│   │   └── sensores.py
│   ├── mcp_server/
│   │   └── server.py
│   ├── rag/
│   │   ├── cli.py
│   │   ├── contexto.py
│   │   ├── corpus.py
│   │   ├── embedder.py
│   │   ├── evaluar.py
│   │   ├── indice.py
│   │   ├── indexer.py
│   │   ├── memoria.py
│   │   ├── retriever.py
│   │   └── texto.py
│   ├── tools/
│   │   ├── cliente.py
│   │   ├── equipos.py
│   │   ├── firma.py
│   │   ├── historial.py
│   │   ├── inventario.py
│   │   ├── manuales.py
│   │   ├── modelos.py
│   │   ├── rag.py
│   │   ├── registro.py
│   │   └── tareas.py
│   └── planta/
│       ├── almacenamiento.py
│       ├── heartbeat.py
│       ├── historial.py
│       ├── main.py
│       ├── mqtt_listener.py
│       ├── nombre_resolver.py
│       ├── numeros.py
│       ├── parte_extractor.py
│       ├── servicio.py
│       ├── tablet.py
│       ├── texto.py
│       ├── transcriber.py
│       └── unidades.py
└── tests/
```

## Pruebas

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"   # requiere git (instala planta-contrato)
.venv/Scripts/python -m pytest
.venv/Scripts/python -m ruff check src tests migrations
```

Resultado actual: **190 passed** con `TEST_DATABASE_URL` apuntando al
PostgreSQL del compose. Sin base quedan **182 passed, 8 skipped** (los de
idempotencia/constraints y los del índice RAG), porque necesitan la base
levantada.

## Integración con la planta

Contrato confirmado: `planta-contrato` **0.2.0**, publicado dentro del repo de la
planta (`contrato/`) y fijado al commit `ab65b95e911731351dba395817a74a8cc60da2b2`.
El `Dockerfile` lo instala con pip y el latido se valida contra
`planta_contrato.EstadoSistema`.

Endpoints probados desde el contenedor `pia3-sistema`:

- `GET /api/planta` → 7 equipos, nombres informales, sensores y límites de alarma.
- `GET /api/historian/{tag}/{variable}` → serie de 45 días (por ejemplo
  `B-101/presion_succion`, 30.998 muestras).
- `GET /api/estado`, `/api/escenarios`, `/api/historial-ordenes`,
  `/api/documentos`, `/api/verdad` y `/api/registro`.

`/api/verdad` es solo de desarrollo: producción nunca debe depender de ella (el
corrector responde `403`).

Pendiente del material del curso: la **lista cerrada de modelos**.
