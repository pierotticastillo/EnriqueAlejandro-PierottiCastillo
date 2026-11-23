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

## Lo que falta medir

- **% de aciertos en casos propios:** set de evaluación con 10+ casos propios con respuesta esperada.
- **Costo por tarea resuelta:** medición real de tokens y costo por tarea para cada modelo.
- **Calidad del modelo de visión:** no se pudo medir con fotos reales de campo (solo placeholders del simulador).

---

## Cómo se usa en el routing

El `Router` (`src/routing/router.py`) usa esta matriz a través de `capacidad_filtrada()` para descartar modelos incapaces:
- ¿Imagen? → solo modelos con `vision != nulo`
- ¿Más de 100k tokens? → solo modelos con ventana grande
- ¿Usa tools? → solo modelos con `tool_calling != nulo`
- ¿Datos sensibles? → solo modelos locales o con contrato
