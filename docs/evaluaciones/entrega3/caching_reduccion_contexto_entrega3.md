# Caching y reducción de contexto — Entrega 3 (módulo 4.4)

**Fuente:** `Academia _2026_Anexo_Entrega_3.txt`, sección Costos (4.4)  
**Fecha:** 2026-11-27  
**Estado:** Implementado parcialmente (Plan 3 ítems 3.2, 3.7, 3.8, 3.10), pendiente de formalizar como criterio de la Entrega 3

---

## 1. Estrategias de caching implementadas

| Componente | Tipo de caché | Alcance | TTL | Invalidación |
|---|---|---|---|---|
| Secciones de documentos RAG | Caché de fragmentos recuperados | `src/rag/retriever.py` | Configurable | Cuando cambia el documento |
| Resultados de tools | Caché de resultado por argumentos | `src/routing/cache_tools.py` (Plan 3, ítem 3.10) | Configurable | Cuando cambian los datos subyacentes |
| Respuestas completas del modelo | Caché de respuesta por pedido idéntico | `src/routing/cache_respuestas.py` (Plan 3, ítem 3.10) | Configurable | Cuando cambian los datos de entrada |
| Tendencias de sensores | Caché de ventana temporal | `src/tools/historial.py` | 5 min | Nueva muestra del sensor |
| Catálogo de fallas | Caché de parseo de manuales | `src/analisis/catalogo.py` | Indefinido | Reindexación del corpus RAG |

---

## 2. Estrategias de reducción de contexto

| Estrategia | Aplicación | Ahorro estimado |
|---|---|---|
| Presupuesto de tokens del agente | 12.000 tokens máx. por consulta (`AGENTE_MAX_TOKENS`) | ~25% |
| Pasos máximos del agente | 6 vueltas (`AGENTE_MAX_PASOS`) | Evita loops infinitos |
| Modelo económico en cascada | `Qwen2.5-0.5B-Instruct` para clasificar/extraer simples | ~90% en tareas simples |
| Structured output validado | `extra="forbid"` descarta propuestas inválidas | Evita reprocesamiento |
| Cierre estructurado | Solo se pide cierre estructurado si el modelo no llama a la tool de salida | Ahorra una vuelta del modelo |
| Filtro de texto libre del operador | El prompt del agente nunca lleva texto libre del operador | Evita inyección y ruido |

---

## 3. Impacto en el presupuesto para 1.000 notas

| Sin optimización | Con caching y reducción | Ahorro |
|---|---|---|
| ~2.000 tokens por nota | ~1.000 tokens por nota | ~50% |
| Costo visión por nota con foto | Costo visión solo si no hay caché | ~40% en notas repetidas |
| Tokens totales para 1.000 notas | ~1.000.000–2.000.000 | ~500.000–1.000.000 |

---

## 4. Próximos pasos para formalizar como criterio de Entrega 3

1. Documentar en `docs/capability_matrix.md` el mapeo de las 7 tareas del anexo con sus datasets de evaluación.
2. Agregar una sección de "Caching y reducción de contexto" al `README.md` con la tabla de métricas.
3. Medir el ahorro real de tokens y costo con la suite de evaluación ejecutada.
4. Actualizar `INFORME.md` con los resultados medidos.
