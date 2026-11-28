# Capability Matrix — PIA-3

**Fecha:** 2026-11-23
**Fuente:** `src/routing/matriz_capacidades.py`
**Plan:** Ítem 3.3 del Plan de implementación 3

> **La ficha dice qué existe; la evaluación decide si alcanza.** Esta matriz es
> la fuente de diseño. Los valores medidos (% de aciertos en casos propios,
> costo por tarea resuelta) se agregan a partir del set de evaluación propia.

---

## Matriz de capacidades

| Modelo | Ubicación | Tool Calling | Structured Output | Reasoning | Visión | Audio | Contexto | Velocidad | Costo |
|---|---|---|---|---|---|---|---|---|---|
| Qwen2.5-3B-Instruct (Q4_K_M) | planta (Ollama) | medio | medio | bajo | nulo | nulo | 2048 tokens | medio | bajo |
| Nemotron 3 Nano Omni 30B A3B | nube (NVIDIA API) | medio | medio | medio | alto | nulo | 128k tokens | alto | medio |
| Qwen2.5-0.5B-Instruct (Q4_K_M) | planta (Ollama) | medio | medio | bajo | nulo | nulo | 2048 tokens | alto | bajo |
| faster-whisper-base | planta (CPU) | nulo | nulo | nulo | nulo | alto | según audio | alto | bajo |
| paraphrase-multilingual-MiniLM-L12-v2 | planta (CPU) | nulo | nulo | nulo | nulo | nulo | según texto | alto | bajo |

---

## ¿Qué familia para cada tarea de PIA-3?

| Tarea | Familia | Modelo de la lista | Por qué |
|---|---|---|---|
| Clasificar el mensaje entrante | Económico | Qwen2.5-0.5B-Instruct | Simple y en volumen; modelo barato de la cascada |
| Responder en el chat | Rápido | Qwen2.5-3B-Instruct | El cliente está esperando |
| Extraer datos de un mensaje | Económico | Qwen2.5-0.5B-Instruct | Tarea mecánica de extracción; modelo barato de la cascada |
| Transcribir un audio de WhatsApp | Audio | faster-whisper-base | Después se procesa como texto |
| Leer la factura de un proveedor | Visión | Nemotron 3 Nano Omni | La entrada es una imagen |
| Buscar en las preguntas frecuentes | Embeddings | paraphrase-multilingual-MiniLM | Comparar significados, no palabras |
| Analizar las cancelaciones del mes | Razonamiento | Qwen2.5-3B-Instruct | Varios pasos de análisis (capacidad limitada) |
| Foto del plato del día | Imágenes | Nemotron 3 Nano Omni | Hay que generar, no entender (percepción) |

---

## Decisiones de selección

**LLM de planta:** `Qwen2.5-3B-Instruct` se mantiene como el único LLM local habilitado. Fue el único candidato que consultó herramientas nativamente, produjo una propuesta válida y terminó dentro del presupuesto de 420 s (evaluación del 2026-11-16). Otros candidatos descartados: `qwen3:4b` (superó el presupuesto y no consultó tools), `phi4-mini` (no produjo tool calls nativos), `llama3.2:3b` (inventó un código de falla).

**LLM de nube con visión:** `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`. Es el Nemotron con visión vigente, el más rápido de los que funcionan y el único que devuelve la respuesta en `content` limpio. Fall-safe: salida indeterminada, nunca "no hay daño".

**ASR:** `faster-whisper-base`. Local, CPU, solo local. Pasó de `small` (placeholder) a `base` por la restricción 04.

**Embeddings:** `paraphrase-multilingual-MiniLM-L12-v2` (384 dims). Índice pgvector. Sin cambios.

---

## Mapeo de tareas del Anexo de Entrega 3 (módulos 4.1 a 4.2)

| Tarea del anexo | Modelo asignado (lista cerrada) | Dataset de evaluación | Acierto medido | Latencia media | Memoria pico | Costo total | Estado |
|---|---|---|---|---|---|---|---|
| Transcripción | `faster-whisper-base` | `docs/evaluaciones/entrega3/dataset_entrega3_transcripcion.json` | 0.00 | 0.0 s | 0 MB | USD 0.00 | Medido (faster-whisper no instalado en este entorno) |
| Extracción del parte | `Qwen2.5-0.5B-Instruct` (económico) | `docs/evaluaciones/entrega3/dataset_entrega3_extraccion_parte.json` | 0.00 | 3.95 s | 400 MB | USD 0.00 | Medido (Ollama respondió pero sin JSON válido) |
| Interpretación de respuestas | `Qwen2.5-3B-Instruct` (generalista) | `docs/evaluaciones/entrega3/dataset_entrega3_interpretacion_respuestas.json` | 0.70 | 6.79 s | 2100 MB | USD 0.00 | Medido (Ollama local) |
| Alerta de seguridad (S1–S4) | Determinista (sin LLM) | `docs/evaluaciones/entrega3/dataset_entrega3_alerta.json` | 1.00 | 0.5 s | 50 MB | USD 0.00 | Implementado y verificado (B2 100 %) |
| Análisis de anomalía | `Qwen2.5-3B-Instruct` + RAG | `docs/evaluaciones/entrega3/dataset_entrega3_analisis.json` | 0.60 | 6.47 s | 2100 MB | USD 0.00 | Medido (Ollama local) |
| Visión | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | `docs/evaluaciones/entrega3/dataset_entrega3_vision.json` | 0.50 | 0.0 s | 0 MB | USD 0.00 | Fail-safe (sin NVIDIA API key en este entorno) |
| Cierre y certificación | Determinista + decisión humana | `docs/evaluaciones/entrega3/dataset_entrega3_cierre.json` | 1.00 | 1.0 s | 100 MB | USD 0.00 | Implementado y verificado (G1 100 %) |

> La matriz de capacidades publicada arriba cubre las 8 dimensiones del curso (tool calling, structured output, reasoning, visión, audio, contexto, velocidad, costo). Este mapeo la vincula explícitamente con las 7 tareas que pide el Anexo de Entrega 3. Los valores de acierto, latencia, memoria y costo se obtienen ejecutando `python scripts/evaluar_entrega3.py` contra los modelos reales de la lista cerrada.

## Estado de medición (Entrega 3)

Los 7 datasets de evaluación se ejecutaron con `python scripts/evaluar_entrega3.py --tarea TODAS`. Los valores medidos se guardaron en cada `dataset_entrega3_*.json`. Limitaciones del entorno de prueba:

- **Transcripción:** `faster-whisper` no instalado → acierto 0.00 (requiere el contenedor de planta).
- **Extracción del parte:** Ollama respondió pero no generó JSON válido → acierto 0.00 (requiere ajuste de prompt).
- **Visión:** sin `NVIDIA_API_KEY` → fail-safe, acierto 0.50 (indeterminado).
- **Análisis:** acierto 0.60 (algunos casos 1.0, otros 0.5 según formato de respuesta de Ollama).

Para medición en entorno de producción real, ejecutar contra Ollama con `qwen2.5:0.5b-instruct` y `qwen2.5:3b-instruct`, y contra NVIDIA API con clave válida.

## Próximos pasos para Entrega 3

1. Reejecutar `scripts/evaluar_entrega3.py` en el entorno de planta (con faster-whisper y Ollama funcionando) para reemplazar valores 0.00 por mediciones reales.
2. Configurar `NVIDIA_API_KEY` para medir visión real contra la API de NVIDIA.
3. Medir RAM, tokens/s y latencia por nota en producción (parcialmente medido en `medicion_arranque_corte_luz.json`).
4. Actualizar `INFORME.md` con la tabla final de costos medidos.

---

## Cómo se usa en el routing

El `Router` (`src/routing/router.py`) usa esta matriz a través de `capacidad_filtrada()` para descartar modelos incapaces:
- ¿Imagen? → solo modelos con `vision != nulo`
- ¿Más de 100k tokens? → solo modelos con ventana grande
- ¿Usa tools? → solo modelos con `tool_calling != nulo`
- ¿Datos sensibles? → solo modelos locales o con contrato
