# Especificaciones OpenAPI ("openspec") de PIA-3

Este directorio contiene las especificaciones OpenAPI 3.0 y la documentación
de integración del proyecto **PIA-3** (Sistema agéntico de la planta de
inyección de agua, Academia Novit 2026).

## ¿Por qué existen estos specs?

Para que el contexto del proyecto no se pierda entre sesiones. Cualquier
agente o persona que retome el proyecto en otra sesión puede leer estos
archivos y entender, sin necesidad de leer todo el código:

* qué endpoints existen, qué parámetros aceptan y qué devuelven,
* qué autenticación requieren,
* qué esquemas de datos usan (órdenes, cierres, repuestos, equipos, sensores,
  fragmentos de manual, etc.),
* cómo se integra con el proyecto externo `academia2026-tp` (planta simulada)
  y cómo correr los escenarios del corrector oficial contra ambos repositorios
  a la vez.

Los specs son fuente de verdad máquina-legible: se pueden cargar en Swagger
UI, Redoc, Postman, Insomnia o generar clientes/servidores a partir de ellos.

---

## Archivos de este directorio

| Archivo | Qué documenta |
|---------|---------------|
| [`central-api.openapi.yaml`](central-api.openapi.yaml) | API REST de la **Central de mantenimiento** (puerto 8200, token Bearer, RBAC: `planificador`/`supervisor`/`inspector`/`lectura`). Endpoints: `/api/sesion`, `/api/ordenes`, `/api/cierres`, `/api/ordenes/{id}/evidencia`, `/api/ordenes/{id}/decision`, `/api/cierres/{id}/decision`. |
| [`mcp-tools.openapi.yaml`](mcp-tools.openapi.yaml) | **Servidor MCP** (streamable-http en `/mcp` o stdio). 8 tools de solo lectura: `equipos`, `historial_sensores`, `historial_ordenes`, `inventario`, `lista_materiales`, `catalogo_tareas`, `busqueda_manuales`, `busqueda_rag`. Con todos los modelos Pydantic de entrada/salida (`extra="forbid"`). |
| [`integracion-academia2026-tp.md`](integracion-academia2026-tp.md) | Integración con el proyecto externo [`NovitSoftware/academia2026-tp`](https://github.com/NovitSoftware/academia2026-tp) (planta simulada): red, puertos, contrato REST, tópicos MQTT, contratos Pydantic, cómo levantar ambos proyectos y cómo correr los escenarios del corrector oficial. |

---

## Superficies de API del proyecto (resumen)

| Canal | Tecnología | Puerto / camino | Autenticación |
|-------|------------|-----------------|---------------|
| Central de mantenimiento | `http.server` (Python stdlib) | `127.0.0.1:8200` | Bearer token (`CENTRAL_CREDENCIALES`) |
| Servidor MCP | FastMCP (streamable-http o stdio) | `localhost:8000/mcp` | Sin auth (solo lectura, lectura de la planta) |
| Listener MQTT | paho-mqtt | `broker:1883` (red `planta-lan`) | Ninguna (red interna de la planta) |
| API de la planta simulada | HTTP (servicio externo `academia2026-tp`) | `sim:8100` | Ninguna (red interna) |

> El listener MQTT **no es una API REST**, por tanto no tiene un spec
> OpenAPI. Sus tópicos y contratos Pydantic están documentados en
> `integracion-academia2026-tp.md` (§4 y §5) y en `README.md` (§Ruteo por
> tipo de mensaje).

---

## Cómo validar los specs

Si tienes instalado un validador de OpenAPI (por ejemplo
`openapi-spec-validator` o el CLI de Swagger):

```bash
python -m openapi_spec_validator docs/openapi/central-api.openapi.yaml
python -m openapi_spec_validator docs/openapi/mcp-tools.openapi.yaml
```

O abrirlos visualmente en [Swagger Editor](https://editor.swagger.io/) o en
[Redoc](https://redoc.ly/).

---

## Relación con el resto del proyecto

* `src/planta/central.py` — implementa la Central (API REST documentada en
  `central-api.openapi.yaml`).
* `src/mcp_server/server.py` — implementa el servidor MCP y sus 8 tools
  (documentadas en `mcp-tools.openapi.yaml`).
* `src/tools/registro.py` — registro de tools (`REGISTRO`) que el MCP expone.
* `src/tools/modelos.py` — modelos Pydantic de entrada/salida de cada tool.
* `src/planta/mqtt_listener.py` — cliente MQTT con los tópicos del contrato
  (documentado en `integracion-academia2026-tp.md`).
* `README.md` — arquitectura completa, fases, RAG, Central, cola, cierre y
  certificación.
* `INFORME.md` — informe técnico detallado (§1 a §15), con referencias a los
  specs OpenAPI en §12.3 y §12.5.
* `docs/spec-parte.md` — mini spec del flujo del parte (etapas, condiciones de
  corte, presupuesto, decisión del planificador).
* `docs/estado_de_implementacion.md` — avance por fase y brechas P0/P1.
* `docs/checklist_entrega.md` — checklist estructural y métricas oficiales.

---

## Estado

Estos specs reflejan la implementación actual del repositorio (commit de
referencia en `docs/checklist_entrega.md`). Si se modifica una API, se debe
actualizar el spec correspondiente para que siga siendo fuente de verdad.
