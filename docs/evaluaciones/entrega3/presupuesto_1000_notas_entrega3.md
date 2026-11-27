# Presupuesto para 1.000 notas — Entrega 3 (módulo 4.4)

**Fuente:** `Academia _2026_Anexo_Entrega_3.txt`, sección Costos (4.4)  
**Fecha:** 2026-11-27  
**Estado:** Metodología definida con datos reales de las 7 corridas del corrector oficial + estimaciones para componentes sin medición directa

---

## 1. Método de cálculo

El costo total para 1.000 notas se calcula como:

```
Costo total = (Costo por nota de procesamiento local) × 1.000
            + (Costo por nota de visión) × (notas con foto)
            + (Costo por nota de registro de trayectoria) × 1.000
```

Todos los modelos locales tienen costo USD 0 (ejecutan en la PC de planta).  
El único costo real es el modelo de visión de NVIDIA (nube) por cada foto procesada.

---

## 2. Datos reales de las 7 corridas del corrector oficial

| Escenario | Entradas de verdad (notas) | Mensajes del sistema | Mensajes por nota |
|-----------|---------------------------|---------------------|-------------------|
| martes-tranquilo | 25 | 49 | 1.96 |
| tormenta-perfecta | 40 | 96 | 2.40 |
| fuga-sin-enlace | 11 | 23 | 2.09 |
| sin-luz | 20 | 49 | 2.45 |
| contratista | 44 | 96 | 2.18 |
| ataque | 19 | 48 | 2.53 |
| libre | 56 | 96 | 1.71 |
| **Total** | **215** | **455** | **2.12 promedio** |

**Fuente:** Informes del corrector oficial por escenario (`correccion/corridas/EnriqueAlejandro-PierottiCastillo/informe.md`)

---

## 3. Componentes de costo por nota

| Componente | Modelo | Costo por nota | Notas |
|---|---|---|---|
| Transcripción ASR | `faster-whisper-base` | USD 0 | Local, CPU, sin GPU |
| Extracción del parte | `Qwen2.5-0.5B-Instruct` | USD 0 | Local, económico |
| Interpretación de respuestas | `Qwen2.5-3B-Instruct` | USD 0 | Local, generalista |
| Alerta de seguridad | Determinista (código) | USD 0 | Sin LLM |
| Análisis de anomalía | `Qwen2.5-3B-Instruct` + RAG | USD 0 | Local + pgvector |
| Visión (solo si hay foto) | `nemotron-3-nano-omni-30b-a3b` | USD 0.0003–0.0006 | NVIDIA API, por llamada |
| Registro de trayectoria | PostgreSQL local | USD 0 | Almacenamiento local |

---

## 4. Estimación de costo por nota

### 4.1 Tokens por nota (basado en mediciones reales del agente)

| Componente | Tokens por nota | Fuente |
|---|---|---|
| Transcripción (ASR) | 0 | Audio, no genera tokens |
| Extracción del parte | ~200 | `Qwen2.5-0.5B-Instruct`, estimado |
| Interpretación de respuestas | ~300 | `Qwen2.5-3B-Instruct`, estimado |
| Análisis de anomalía | ~500 | `Qwen2.5-3B-Instruct` + RAG, estimado |
| Visión (por nota con foto) | 1.052 | 861 entrada + 191 salida, medido en `README.md` §9.8 |

**Total estimado por nota:** ~1.000–2.000 tokens (sin visión) / ~2.000–3.000 tokens (con visión)

### 4.2 Costo de visión por nota

| Concepto | Valor | Fuente |
|---|---|---|
| Tokens de entrada por llamada de visión | 861 | `README.md`, §Qué corre en planta y nube |
| Tokens de salida por llamada de visión | ~191 | `README.md`, §Qué corre en planta y nube |
| Costo por llamada de visión | USD 0.0003–0.0006 | `README.md`, §9.8 (tarifas NVIDIA NIM) |
| Notas con foto (estimado) | ~30% de las notas | Hipótesis de operación |

---

## 5. Presupuesto total para 1.000 notas

| Escenario | Notas con foto | Costo visión | Costo total estimado |
|---|---|---|---|
| Conservador (todas con foto) | 1.000 | USD 0.30–0.60 | **USD 0.30–0.60** |
| Realista (30% con foto) | 300 | USD 0.09–0.18 | **USD 0.09–0.18** |
| Optimista (ninguna con foto) | 0 | USD 0.00 | **USD 0.00** |

### 5.1 Presupuesto de tokens para 1.000 notas

| Concepto | Tokens por nota | Tokens para 1.000 notas |
|---|---|---|
| Transcripción (ASR) | 0 | 0 |
| Extracción del parte | ~200 | 200.000 |
| Interpretación de respuestas | ~300 | 300.000 |
| Análisis de anomalía | ~500 | 500.000 |
| Visión (300 notas con foto) | 1.052 | 315.600 |
| **Total estimado** | ~1.000–2.000 | **1.000.000–2.000.000** |

---

## 6. Fórmula de cálculo para medición real

Cuando se disponga de datos reales de producción, usar esta fórmula:

```
Costo total USD = (tokens_por_nota × precio_por_millón / 1.000.000) × 1.000
                + (notas_con_foto × costo_por_llamada_visión)
```

Donde:
- `tokens_por_nota` = tokens de extracción + tokens de interpretación + tokens de análisis + tokens de visión (si aplica)
- `precio_por_millón` = USD 0 para modelos locales (Qwen2.5, faster-whisper, embeddings)
- `costo_por_llamada_visión` = USD 0.0003–0.0006 (modelo NVIDIA Nemotron)

---

## 7. Reducción de costo mediante caching y reducción de contexto

| Estrategia | Ahorro estimado | Implementación |
|---|---|---|
| Caching de secciones de manuales (RAG) | ~40% en tokens de análisis | `src/rag/retriever.py`, caché de fragmentos |
| Caching de resultados de tools | ~30% en llamadas repetidas | `src/routing/cache_tools.py` (Plan 3 ítem 3.10) |
| Reducción de contexto en agente | ~25% en tokens por consulta | Presupuesto de 12.000 tokens, 6 pasos máx. |
| Modelo económico para clasificación | ~90% en tareas simples | `Qwen2.5-0.5B-Instruct` (cascada, ítem 3.2) |

---

## 8. Script para medición real

Para obtener el costo real por nota, ejecutar:

```bash
# 1. Ejecutar una corrida de prueba con N notas conocidas
python scripts/correr_correccion_oficial.py --escenarios tormenta-perfecta --salida /tmp/test

# 2. Extraer métricas de PostgreSQL (tokens, costo, latencia)
python -c "
from sqlalchemy import create_engine, text
engine = create_engine('postgresql+psycopg://pia3:pia3@localhost:55600/pia3')
with engine.connect() as c:
    resultado = c.execute(text('''
        SELECT 
          count(*) as total_notas,
          sum(tokens_prompt) as total_tokens_prompt,
          sum(tokens_completion) as total_tokens_completion,
          sum(costo_usd) as total_costo_usd
        FROM registro_trayectoria
        WHERE tipo = 'modelo'
    '''))
    print(resultado.fetchone())
"
```

---

## 9. Próximos pasos

1. **Ejecutar el script de medición** en un entorno de producción con notas reales para obtener valores medidos (no estimados).
2. **Reemplazar las estimaciones** de esta sección con valores medidos.
3. **Actualizar `INFORME.md`** con la tabla final de costos.

---

## 10. Archivos relacionados

| Archivo | Descripción |
|---|---|
| `dataset_entrega3_*.json` | 7 datasets de evaluación por tarea |
| `docs/evaluaciones/datasets_entrega3_indice.md` | Índice maestro de datasets |
| `scripts/medir_arranque_corte_luz.py` | Medición de tiempo de arranque (completado: 120.34 s promedio) |
| `medicion_arranque_corte_luz.json` | Resultado de la medición de arranque |
| `README.md` §9.8 | Costo medido de llamada de visión (861 entrada + 191 salida tokens) |