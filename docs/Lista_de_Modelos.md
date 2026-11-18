# Lista cerrada de modelos

1. ASR: `faster-whisper-base` (CTranslate2) para transcripción local rápida y de bajo consumo en CPU.
2. Embeddings: `paraphrase-multilingual-MiniLM-L12-v2` (384 dim) operando sobre PostgreSQL + pgvector para RAG local eficiente.
3. LLM de Planta: `Qwen2.5-3B-Instruct` (cuantización Q4_K_M vía Ollama/llama.cpp) para la estructuración y razonamiento auxiliar local sin requerir enlace a la nube.
4. LLM de Nube (Percepción/Contexto): `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` (API de NVIDIA, nube) para tareas de percepción multimodal y razonamiento auxiliar cuando se requiera mayor capacidad. Invocado vía `https://integrate.api.nvidia.com/v1/chat/completions` con `temperature=0`, `max_tokens=800`, timeout de 8 s y salida validada.

## Decisión de selección del LLM de planta

**Actualizado:** 2026-11-18. Se mantiene `Qwen2.5-3B-Instruct`; esta decisión no
agrega modelos ni modifica la configuración productiva.

La evaluación comparativa se hizo en el agente real, con el mismo prompt,
corpus, tools, contexto, semilla, presupuesto de 420 s y validación estricta
de la propuesta:

| Modelo evaluado | Resultado | Motivo |
|---|---|---|
| **`qwen2.5:3b-instruct`** | **Se mantiene** | Único candidato que consultó las fuentes con tool calls nativos, produjo una propuesta válida y terminó dentro del presupuesto; además, es el más liviano (1,9 GB). |
| `qwen3:4b` | Descartado | No consultó tools y tardó 428,0 s, por encima del presupuesto de 420 s. |
| `phi4-mini` | Descartado | Escribió las llamadas de tools como texto; no produjo tool calls nativos. |
| `llama3.2:3b` | Descartado | No consultó las fuentes e inventó un código de falla; la propuesta fue descartada. |

Los tres modelos alternativos se evaluaron como candidatos y **no forman parte
de la lista cerrada**. El modelo activo, su configuración y el verificador
continúan siendo los definidos en `src/modelos_lista.py`.

El protocolo, las mediciones y las condiciones para revisar la decisión están
en [`evaluacion_modelo_local.md`](evaluacion_modelo_local.md).
