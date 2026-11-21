# Integración con academia2026-tp (planta simulada)

Este repositorio (`EnriqueAlejandro-PierottiCastillo`) es la **solución** del
TP Academia Novit 2026. Funciona junto a la planta simulada
[`NovitSoftware/academia2026-tp`](https://github.com/NovitSoftware/academia2026-tp),
que es la **simulación**. Este repositorio **no toca nada** de ese proyecto:
solo lee su API REST y se suscribe a sus tópicos MQTT.

Los escenarios del corrector oficial se ejecutan contra **ambos repositorios a
la vez**: la planta simulada levanta `sim` (API) y `broker` (MQTT), y este
repositorio levanta `postgres`, `migraciones`, `sistema` y `mcp`.

---

## 1. Arquitectura conjunta

```
┌─────────────────────────────────────────────────────────────────────┐
│  Red Docker planta-lan (compartida por ambos proyectos)             │
│                                                                     │
│  academia2026-tp (simulación — NO se toca)    EnriqueAlejandro-   │
│  ┌─────────────┐                          PierottiCastillo         │
│  │ sim:8100    │ ─────────────────────────────► sistema (PIA-3)   │
│  │ API REST    │  lee /api/planta, /api/historian,                 │
│  │             │  /api/historial-ordenes, /api/documentos          │
│  │ broker:1883 │ ─────────────────────────────► sistema (PIA-3)   │
│  │ MQTT        │  publica/escucha planta/recorridas,               │
│  │             │  planta/respuestas, planta/sensores/+/+,          │
│  │             │  planta/cierres                                   │
│  └─────────────┘                                                   │
│                                                                     │
│  Este repositorio levanta:                                          │
│  • postgres (16 + pgvector, puerto 5432 interna / 55432 host)      │
│  • migraciones (alembic upgrade head)                               │
│  • sistema (latido, MQTT, ASR, extracción, análisis, órdenes)      │
│  • mcp (servidor MCP, streamable-http /mcp o stdio)                │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Red y puertos

| Servicio | Proyecto | Puerto interno | Puerto host | Red |
|----------|----------|----------------|-------------|-----|
| `sim` (API planta) | academia2026-tp | 8100 | — | planta-lan |
| `broker` (MQTT) | academia2026-tp | 1883 | — | planta-lan |
| `postgres` | EnriqueAlejandro-… | 5432 | POSTGRES_PORT (55432) | interna + debug |
| `sistema` (PIA-3) | EnriqueAlejandro-… | — | — | planta-lan + interna + debug |
| `mcp` (PIA-3) | EnriqueAlejandro-… | 8000 | MCP_PUERTO (8000) | debug |
| Central de mantenimiento | EnriqueAlejandro-… | 8200 | CENTRAL_PUERTO (8200) | loopback |

- `planta-lan` es la red externa creada por el compose de la planta
  simulada. Este repositorio solo la usa; no la crea.
- `interna` es una red `internal: true` para PostgreSQL y tráfico interno.
- `debug` publica PostgreSQL y MCP en `localhost` para desarrollo.

> En Windows, el puerto 55432 puede caer en un rango reservado por
> Hyper-V/WSL. Se deja configurable: `POSTGRES_PORT=55600` en `.env`.

---

## 3. Contrato de la API REST de la planta simulada

Este repositorio solo **lee** (GET) de la API de `sim:8100`. Nunca escribe.

| Endpoint | Qué devuelve | Uso en PIA-3 |
|----------|--------------|--------------|
| `GET /api/planta` | Catálogo de equipos, sensores, límites de alarma, nombres de campo | Carga inicial del catálogo, resolución de nombres, límites de sensores (alarmas vienen de acá, no del manual) |
| `GET /api/historian/{tag}/{variable}` | Serie histórica de un sensor con min/máx/promedio/tendencia | Tool `historial_sensores`, enriquecimiento de eventos de sensor |
| `GET /api/historial-ordenes` | Órdenes del archivo con equipo, tareas, repuestos, texto libre | Tool `historial_ordenes`, reincidencia (45 días) |
| `GET /api/documentos/{doc}/secciones` | Secciones de manuales con citas | Tool `busqueda_manuales` (léxica, respaldo) |
| `GET /api/estado` | Estado de bombas (`bombas[].estado`) | Reglas de aprobación (regla `inyección`) |
| `GET /api/documentos/MBP-4/secciones` | Secciones de MBP-4 (plan de mantenimiento) | Catálogo de tareas, repuestos, materiales |

> **Importante:** los límites de alarma y disparo salen siempre de
> `/api/planta` (MBP-4 §5), nunca del manual. El manual es referencia y
> puede estar vencido (Rev. C vs Rev. D).

---

## 4. Contrato MQTT (tópicos)

El listener MQTT (`src/planta/mqtt_listener.py`) se suscribe y publica los
siguientes tópicos. Los mensajes están validados con contratos Pydantic
desde `planta_contrato` (instalado desde academia2026-tp).

### Suscripciones (entrada de la planta)

| Tópico | Contrato Pydantic | Qué hace el sistema |
|--------|-------------------|---------------------|
| `planta/recorridas` | `NotaRecorrida` | Descarga audio/fotos, transcribe ASR, extrae parte determinista, borrador en tablet |
| `planta/respuestas` | `RespuestaOperador` | Procesa correcciones, preguntas, confirmaciones del operador |
| `planta/sensores/+/+` | `LecturaSensor` | Evalúa alarmas (S1–S4), eventos por equipo, órdenes por evento de sensor |
| `planta/cierres` | `NotaCierre` | Transcribe cierre por voz, compara contra orden, deja para decisión humana |

### Publicaciones (salida del sistema hacia la planta)

Todas pasan por la cola de salida (`outbox_cola` en PostgreSQL).

| Tópico | Contrato Pydantic | ¿Necesita radioenlace? | Qué publica |
|--------|-------------------|------------------------|-------------|
| `sistema/eventos` | `EventoSistema` | No | Eventos de sensor, alertas S1–S4, eventos por equipo de parte |
| `sistema/ordenes` | `OrdenSistema` | No | Órdenes de trabajo (una vez, con estado decidido) |
| `sistema/tablet` | `MensajeTablet` | No (tablet en planta) | Avisos en tablet, borrador de parte |
| `sistema/tablet` (radio) | `MensajeTablet` | **Sí** | Aviso por radio al supervisor, canal 1 |

> El orden de salida es **por destino**: el aviso por radio espera al
> radioenlace; los eventos y las órdenes no quedan esperando al radio. No
> hay duplicados (índice parcial `WHERE estado = 'pendiente'`).

---

## 5. Contratos Pydantic (`planta_contrato`)

El paquete `planta-contrato` (versión 0.2.0, commit `ab65b95e…` de
academia2026-tp, subdirectorio `contrato`) se instala como dependencia y
provee los contratos validados por Pydantic:

- `NotaRecorrida` — nota de voz del técnico (audio, fotos, ts, operador)
- `RespuestaOperador` — respuesta del operador en la tablet
- `LecturaSensor` — lectura de sensor (tag, variable, valor, unidad, ts)
- `NotaCierre` — nota de cierre de orden por voz
- `EventoSistema` — evento publicado al bus (id, tipo, equipo, ts, evidencia)
- `OrdenSistema` — orden de trabajo (id, estado, equipo, tareas, repuestos, …)
- `MensajeTablet` — aviso en tablet (tópico, espera_respuesta, …)
- `Topicos` — constantes de tópicos MQTT

Todos validados con `extra="forbid"`: un campo desconocido falla.

---

## 6. Cómo levantar ambos proyectos juntos

```bash
# 1. Crear la red externa (una sola vez)
docker network create planta-lan

# 2. Levantar la planta simulada (academia2026-tp, en otro directorio)
cd /ruta/a/academia2026-tp
docker compose up -d --build

# 3. Levantar este repositorio (PIA-3)
cd /ruta/a/EnriqueAlejandro-PierottiCastillo
cp .env.example .env   # ajustar POSTGRES_PORT, CENTRAL_CREDENCIALES, NVIDIA_API_KEY
docker compose up -d --build

# 4. Ejecutar migraciones (se hace automáticamente al levantar)
docker compose up -d migraciones

# 5. Indexar el RAG (opcional, perfil rag)
docker compose --profile rag run --rm rag-indexar
```

### Variables de entorno clave (`.env`)

| Variable | Default | Uso |
|----------|---------|-----|
| `POSTGRES_PORT` | `55432` | Puerto host de PostgreSQL (configurable en Windows) |
| `PLANTA_API_URL` | `http://sim:8100` | API de la planta simulada |
| `MQTT_HOST` / `MQTT_PORT` | `broker` / `1883` | Broker MQTT de la planta |
| `ENLACE_CHECK_URL` | `http://enlace:8081/estado` | Chequeo del radioenlace |
| `CENTRAL_HOST` / `CENTRAL_PUERTO` | `0.0.0.0` / `8200` | Central de mantenimiento |
| `CENTRAL_CREDENCIALES` | — | Tokens Bearer (requerido; sin credenciales 401) |
| `MCP_PUERTO` | `8000` | Puerto del servidor MCP |
| `NVIDIA_API_KEY` | (vacío) | Clave del modelo de visión (`.env` local, Git ignora) |
| `NVIDIA_PROXY` | `http://enlace:3128` | Proxy del enlace para la nube |

---

## 7. Cómo correr los escenarios del corrector oficial

El corrector oficial (`corregir.py`, `puntuar.py`, `rubrica.yaml`) vive en
el repositorio `academia2026-tp`, no en este. Se ejecuta contra ambos a la
vez:

```bash
# Desde este repositorio, con academia2026-tp levantado en planta-lan
python scripts/correr_correccion_oficial.py \
  --correccion-dir <ruta/a/academia2026-tp> \
  --repo . \
  --salida docs/evaluaciones/correccion-oficial/
```

El script:
1. Ejecuta `correccion/corregir.py` oficial sin modificarlo en disco (ajusta en memoria el `TypeError: int + str` del scorer).
2. Captura trayectoria, tokens, costo y latencia de PostgreSQL justo antes de que cada escenario elimine su volumen.
3. Documenta los hashes SHA-256 del corrector, scorer, rúbrica y suite RAG en `integridad_correccion.json`.
4. Los SHA-256 se calculan sobre la representación canónica UTF-8 sin BOM y LF (fijada por `.gitattributes`).

### Escenarios oficiales (7)

| Escenario | Qué evalúa |
|-----------|------------|
| `martes-tranquilo` | Parte por voz, detección B1, reglas D1/D2 |
| `tormenta-perfecta` | Detección sostenida, órdenes, sin enlace |
| `fuga-sin-enlace` | Alerta S1, operación sin enlace, cierre, visión fall-safe |
| `sin-luz` | Sobrevive al corte de luz, cola de salida |
| `contratista` | Cierre de contratista, certificación, habilita |
| `ataque` | Inyección por nota, inmunidad a instrucciones maliciosas |
| `libre` | Robustez general |

Resultado oficial: **92,6/100 — APRUEBA** (0 eliminatorias, 0 mensajes inválidos).

---

## 8. Requisitos de entorno

- Docker Engine + Docker Compose v2.
- Red externa `planta-lan` creada por el compose de la planta simulada.
- PostgreSQL 16 + pgvector (en compose de este repo).
- Ollama con `qwen2.5:3b-instruct` (modelo local de la lista cerrada).
- fastembed con `paraphrase-multilingual-MiniLM-L12-v2` (embeddings locales, 384 dims).
- faster-whisper con `base` (ASR local, CPU, int8).
- Para visión: `NVIDIA_API_KEY` y acceso a `https://integrate.api.nvidia.com/v1` por el proxy del enlace (`enlace:3128`).

> Este sistema **no depende de la nube**: si el enlace está caído o no hay
> clave, la descripción de la foto queda `indeterminada` y el aviso de
> seguridad sale igual.

---

## 9. Archivos relacionados en este repositorio

| Archivo | Qué documenta |
|---------|---------------|
| `README.md` | Arquitectura completa, fases, RAG, Central, cola, cierre, certificación |
| `INFORME.md` | Informe técnico detallado (§1 a §15) |
| `docs/spec-parte.md` | Mini spec del flujo del parte (etapas, condiciones de corte, presupuesto) |
| `docs/estado_de_implementacion.md` | Avance por fase y brechas P0/P1 |
| `docs/checklist_entrega.md` | Checklist estructural y métricas oficiales |
| `docs/openapi/central-api.openapi.yaml` | API REST de la Central de mantenimiento (OpenAPI 3.0) |
| `docs/openapi/mcp-tools.openapi.yaml` | Servidor MCP y 8 tools de solo lectura (OpenAPI 3.0) |
| `docs/dependencias_externas.md` | Dependencias externas del Plan de Implementación 2 |
