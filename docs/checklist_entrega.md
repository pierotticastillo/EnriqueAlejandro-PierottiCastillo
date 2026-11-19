# Checklist de entrega — PIA-3 · Academia Novit 2026

**Fecha:** 2026-11-19  
**Commit de referencia:** `4eb00f54b5861c6c3c922aad060de266064012ef`  
**Corrector oficial:** `ab65b95e911731351dba395817a74a8cc60da2b2`  
**Alembic:** `0011`

---

## 1. Requisitos P0 (bloqueantes — CERRADOS Y VERIFICADOS)

| ID | Requisito | Estado | Evidencia |
|----|-----------|--------|-----------|
| P0-01 | Decisión humana de supervisor e inspector (G2/G3) | **Cerrado** | `docs/evaluaciones/correccion-oficial/README.md` — 77 pruebas autenticadas G2/G3 pasando |
| P0-02 | Daño estructural en escalados ordinarios | **Cerrado** | Fase 7 + remediación, gate fotográfico en código |
| P0-03 | Evidencia y provenance de la orden y del cierre | **Cerrado** | Manifiesto canónico, ledger transaccional, migración 0010 |
| P0-04 | Autenticación y roles de la Central | **Cerrado** | Token Bearer, RBAC (planificador/supervisor/inspector/lectura) |
| P0-05 | XSS almacenado en la Central | **Cerrado** | `textContent`/`replaceChildren`, CSP con nonce |
| P0-06 | Corrida oficial sobre el commit final | **Cerrado** | 92.6/100 — APRUEBA, 0 eliminatorias, 467 mensajes válidos |
| P0-07 | Alcance, fecha, rúbrica de la Entrega 2 | **Cerrado** | Anexo de Entrega 2: fecha 20/11/2026, módulos 3.1 a 3.4 |

---

## 2. Métricas oficiales (corrector oficial)

| Métrica | Valor | Estado |
|---------|-------|--------|
| Nota oficial (corrida local con ASR) | **93.4 / 100 — APRUEBA** | ✅ |
| Nota oficial anterior (entorno de producción) | **92.6 / 100 — APRUEBA** | ✅ |
| Eliminatorias | 0 | ✅ |
| Mensajes inválidos | 0 | ✅ |
| Mensajes válidos | 467 | ✅ |
| Escenarios evaluados | 7 (martes-tranquilo, tormenta-perfecta, fuga-sin-enlace, sin-luz, contratista, ataque, libre) | ✅ |
| Pruebas con PostgreSQL | 631 passed | ✅ |
| Pruebas sin base | 553 passed, 78 skipped | ✅ |
| Pruebas G2/G3 autenticadas | 77 passed | ✅ |
| RAG pgvector (hit@5 / recall@5) | 0.873 / 0.711 | ✅ |
| RAG memoria (hit@5 / recall@5) | 0.845 / 0.695 | ✅ |

---

## 3. Checklist estructural del repositorio

| Ítem | Estado | Observación |
|------|--------|-------------|
| Repo público con nombre correcto | ✅ | `pierotticastillo/EnriqueAlejandro-PierottiCastillo` |
| Raíz: `.gitignore`, `README.md`, `docker-compose.yml`, `tests/`, `INFORME.md` | ✅ | |
| `docker compose up` levanta el sistema al lado de la planta | ✅ | postgres, migraciones, sistema, mcp |
| Servicios de planta en `planta-lan`, con label `academia.planta: pc` | ✅ | Compose verificado |
| `planta_contrato` 0.2.0 instalado con pip | ✅ | Commit `ab65b95e…` |
| Latido cada 30 s | ✅ | `heartbeat.py` |
| PostgreSQL 16 + pgvector en compose | ✅ | |
| Migraciones aplicadas al levantar | ✅ | Alembic hasta 0011 |
| Transcripción local y structured output validado | ✅ | `faster-whisper-base` |
| Detección determinista sin LLM | ✅ | S1–S4, reglas en código |
| Alerta de seguridad en < 30 s | ✅ | B2 100 % |
| Tools con Pydantic `extra="forbid"` y al menos una por MCP | ✅ | 8 tools de solo lectura |
| RAG con metadatos, citas y evaluación recall@5 | ✅ | 704 fragmentos, citas obligatorias |
| Memoria de conversación persistida | ✅ | `RAG_MEMORIA_DIAS=90` |
| Cola sin duplicados al volver el enlace | ✅ | Índice parcial `WHERE estado='pendiente'` |
| Cierre y certificación con habilitación | ✅ | Fase 7 verificada |
| `INFORME.md` con modelos, tokens, costo y justificación | ✅ | Sección 15 actualizada |
| Tests pasando y `corregir.py` sin eliminatorias | ✅ | |

---

## 4. Brechas P1 (no bloqueantes — estado al 2026-11-19)

| ID | Brecha | Estado | Evidencia |
|----|--------|--------|-----------|
| P1-01 | Suite de evaluación del agente vs baseline | **Fallido 0/5** (error de conexión a Ollama desde el host) | `docs/evaluaciones/reporte_agente_vs_baseline.json` |
| P1-11 | Calidad de visión con fotos reales | **Pendiente 0/4** (faltan imágenes de campo) | `docs/evaluaciones/reporte_vision.json` |
| P1-15 | Checkpoint durable | **Verificado 6/6** | `tests/test_checkpoint_reanudacion.py` |
| P1-16 | Correlation ID | **Verificado 6/6** | `tests/test_checkpoint_reanudacion.py` |
| P1-17 | Política única de retry/backoff/jitter | Pendiente | — |
| P1-18 | Detección de loops | Pendiente | — |
| P1-19 | Cancelación y recuperación | Pendiente | — |
| P1-20 | Handoff HITL con acuse/vencimiento | Pendiente | — |
| P1-21 | Validación multimodal adversarial | Pendiente | `docs/evaluaciones/baterias/multimodal.json` definida |
| P1-03 / P1-04 | Outbox transaccional y PUBACK | Pendiente | — |
| P1-07 a P1-14 | Observabilidad, CI/CD, seguridad, documentación C–G | Parcial / Pendiente | — |

---

## 5. Recomendación para la entrega

**Puedes entregar tranquilo.** Todos los P0 están cerrados y verificados. El corrector oficial da **92.6/100 — APRUEBA** con 0 eliminatorias.

Las brechas P1 son mejora interna y no bloquean la aceptación. Si decides atender alguna antes de cerrar la entrega, prioriza:
1. **P1-01:** Fixear la URL de Ollama en `scripts/evaluar_agente.py` (cambiar `host.docker.internal:11434` por `localhost:11434`) y reejecutar.
2. **P1-11:** Solicitar fotos de campo a la Academia para completar la batería de visión.

El resto (P1-15 a P1-21, P1-03/P1-04, P1-07 a P1-14) puede dejarse documentado como pendiente en el `INFORME.md`.

---
*Este checklist es interno y está documentado en `docs/checklist_entrega.md`. No forma parte del repositorio público entregable.*