# Evaluación del modelo local de la planta

- **Fecha de la evaluación:** 2026-11-16
- **Decisión:** mantener `qwen2.5:3b-instruct` (`Qwen2.5-3B-Instruct`, Q4_K_M).

## 1. Decisión

Se mantiene `Qwen2.5-3B-Instruct` como LLM local de la planta. No se modifica
la lista cerrada, la configuración productiva ni el código de ejecución.

La decisión no se tomó por la calidad general de un modelo aislado, sino por su
comportamiento en el **agente real**: consultar las herramientas, proponer una
falla existente y hacerlo dentro del presupuesto operativo. Entre los cuatro
modelos comparados, `Qwen2.5-3B-Instruct` fue el único que completó el ciclo
con consultas nativas a las fuentes y una propuesta que atravesó la validación
completa.

Un modelo más rápido no es mejor para este sistema si no consulta el manual y
el historial, o si inventa un código de falla. Esa es la razón por la que se
descartaron las alternativas.

## 2. Alcance y método

La evaluación fue una comparación controlada de **una consulta representativa**
de la planta, no un benchmark de calidad sobre todos los escenarios. Los tres
modelos alternativos no pertenecen a la lista cerrada: se descargaron sólo
para esta comparación y nunca se habilitaron en el runtime productivo.

Se usó el agente real de `src/agente/`, con el catálogo de manuales, el
corpus de la planta y las mismas ocho tools de solo lectura. Para todos los
candidatos se mantuvieron constantes:

- el prompt de un escalado de B-102 y la falla `TX-T01`;
- el corpus, el catálogo, las tools y sus esquemas estrictos;
- `temperature=0`, semilla `42`, contexto de 4096 tokens, dos hilos de CPU,
  salida máxima de 700 tokens y `keep_alive=0`;
- un presupuesto de seis pasos, 12 000 tokens y 420 segundos;
- la misma validación de la propuesta: esquema, código de falla existente,
  causas respaldadas y citas permitidas.

La medición se ejecutó en Docker sobre la planta simulada, con Ollama 0.40.16.
El arnés se ejecutó dentro del contenedor `sistema` y alcanzó el Ollama del
host por `host.docker.internal` para aislar el comportamiento de cada modelo.
La ruta productiva sancionada —el proxy `enlace:3128`— no se midió en esta
comparación: por eso las latencias son comparativas entre modelos, no una
promesa de latencia en el radioenlace real.

Una propuesta se considera válida si tiene el esquema esperado, su código de
falla existe en el catálogo y sus citas son verificables. Se
registró además si el modelo hizo llamadas **nativas** a las tools de
consulta; escribir una llamada como JSON dentro del texto no cuenta.

## 3. Resultados

| Modelo | Pesos en disco | Consulta tools nativamente | Propuesta válida | Llamadas LLM | Tiempo | Dentro de 420 s |
|---|---:|:---:|:---:|---:|---:|:---:|
| **`qwen2.5:3b-instruct`** | **1,9 GB** | **Sí** | **Sí** | 3 | **330,274 s** | **Sí** |
| `qwen3:4b` | 2,5 GB | No | Sí | 2 | 428,012 s | **No** |
| `phi4-mini` | 2,5 GB | No | Sí | 2 | 59,184 s | Sí |
| `llama3.2:3b` | 2,0 GB | No | **No** | 1 | 29,138 s | Sí |

Los pesos son el tamaño de los archivos de Ollama, no la memoria total de
proceso: el consumo efectivo de RAM también depende del contexto y de la
caché KV.

### `qwen2.5:3b-instruct` — se mantiene

- Hizo llamadas nativas a `busqueda_rag` e `historial_sensores`.
- Propuso `TX-T01` con causas y la cita `TX350-D §8.3`; no hubo citas ni
  causas descartadas.
- Completó la consulta en 330,274 s, dentro del presupuesto de 420 s.
- Consultó en una vuelta, escribió la propuesta en prosa y necesitó una tercera
  vuelta de **cierre estructurado**. Ese camino ya está contemplado por el
  agente y queda registrado como otro paso de la trayectoria.
- Es el modelo más liviano de la comparación y ya pertenece a la lista cerrada.

### `qwen3:4b` — descartado

- Produjo una propuesta formalmente válida, pero **no consultó ninguna tool**.
- Tardó 428,012 s: superó el presupuesto de 420 s.
- La primera vuelta consumió los 700 tokens de salida en razonamiento en prosa;
  la segunda fue el cierre estructurado.
- `ChatOllama` sí envió `think=false`, pero la metadata de esta variante en
  Ollama 0.40.16 declara `thinking.values=[true]` y `default=true`. El modo de
  razonamiento obligatorio hacía el comportamiento y el consumo de tiempo
  impredecibles para la planta.
- Pesa 0,6 GB más que el modelo actual.

Aunque dos llamadas parecen una ventaja frente a las tres de Qwen2.5, la
ventaja no compensa no consultar las fuentes ni superar el plazo operativo.

### `phi4-mini` — descartado

- Produjo una propuesta válida en 59,184 s y sin corte.
- No hizo llamadas nativas a las tools: escribió las llamadas como un arreglo
  JSON dentro de `message.content`.
- Una prueba directa contra la API `/api/chat` de Ollama, sin LangChain, y con
  una sola tool confirmó el comportamiento: la respuesta llegó en `content` y
  `message.tool_calls` quedó vacío. Por tanto, no es una limitación de la
  integración del agente.
- Pesa 0,6 GB más que el modelo actual.

Podría ser un candidato para otra tarea sin herramientas, pero no para el
agente de la planta: una respuesta rápida sin consulta verificable no es
suficiente para una justificación con citas.

### `llama3.2:3b` — descartado

- Fue el más rápido: 29,138 s y una sola llamada.
- Llamó nativamente a la herramienta de salida `PropuestaAgente`, pero **no
  consultó** las tools de fuentes.
- Inventó el código de falla `F02-B-102-4`, que no existe en el catálogo. La
  validación lo detectó y descartó la propuesta completa, que fue el
  comportamiento correcto del sistema.
- También repitió cuatro veces la misma cita en la salida.
- Pesa 0,1 GB más que el modelo actual.

Su salida estructurada nativa es mejor que la prosa de Qwen2.5, pero eso no
compensa una propuesta no trazable que el sistema debe rechazar.

## 4. Criterios de decisión

| Criterio | Por qué importa | Resultado |
|---|---|---|
| Consulta real de fuentes | La propuesta debe poder limitarse a material recuperado y citable | Sólo Qwen2.5 |
| Propuesta válida | Un código o una cita inventada hace perder toda la propuesta | Llama 3.2 falla |
| Presupuesto de 420 s | La consulta tiene que terminar antes del corte global | Qwen3 falla |
| Consumo local | La planta tiene 4 CPU y 8 GB, sin GPU | Qwen2.5 es el más liviano |
| Lista cerrada | Cambiar el LLM exigiría una autorización y actualizar la fuente de verdad | Qwen2.5 ya está autorizado |

## 5. Verificación posterior

Después de la comparación:

- el verificador de modelos pasó con la configuración de la lista cerrada;
- la suite completa recogió **637 pruebas**: **559 aprobadas, 78 omitidas y 0
  fallidas**;
- el repositorio quedó sin cambios de código: esta evaluación es evidencia
  documental y no modifica el modelo activo.

## 6. Alcance de la decisión y condiciones para revisarla

Esta decisión no certifica que `Qwen2.5-3B-Instruct` sea el mejor modelo para
cualquier tarea. Certifica que es la mejor de las alternativas evaluadas para
el agente local con las restricciones actuales: CPU sin GPU, memoria acotada,
presupuesto de 420 s, tools de consulta y validación estricta.

Corresponderá volver a evaluarla si cambia cualquiera de estas condiciones:

- aparece un modelo local de la lista cerrada con tool calling nativo y menos
  de 2 GB de pesos;
- cambia el presupuesto de tiempo o tokens;
- se agrega un escenario de consulta que no pueda resolverse con las tools
  actuales;
- Ollama actualiza el comportamiento de `thinking` de Qwen3;
- se autoriza formalmente un modelo fuera de la lista cerrada.

La fuente de verdad del modelo activo continúa siendo
`src/modelos_lista.py`; la lista publicada es
[`Lista_de_Modelos.md`](Lista_de_Modelos.md).
