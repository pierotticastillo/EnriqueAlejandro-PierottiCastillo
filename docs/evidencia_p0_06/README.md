# Evidencia auditable de P0-06

## Ancla reproducible

- Solución auditada: `4eb00f54b5861c6c3c922aad060de266064012ef`
- Corrector oficial: `ab65b95e911731351dba395817a74a8cc60da2b2`
- Escenarios oficiales: 7 (martes-tranquilo, tormenta-perfecta, fuga-sin-enlace, sin-luz, contratista, ataque, libre)
- Resultado oficial: **92.6/100 — APRUEBA**
- Eliminatorias: 0
- Mensajes inválidos: 0
- Imágenes digeridas (SHA-256): 4

## Verificaciones del proyecto

- PostgreSQL: `631 passed, 7 warnings in 102.59s (0:01:42)`
- Sin base: `553 passed, 78 skipped in 90.89s (0:01:30)`
- G2/G3 autenticados: `============================= 77 passed in 11.54s =============================`
- RAG en memoria (hit@5 / recall@5): 0.845 / 0.695
- RAG pgvector (hit@5 / recall@5): 0.873 / 0.711

G2 y G3 están inactivos en la rúbrica oficial, pero son obligatorios para el
proyecto y quedan verificados por las pruebas autenticadas. El repositorio de
la Academia no se modifica: los dos ajustes del corrector se aplican sólo en
memoria y están documentados en `oficial/integridad_correccion.json`.

## Contenido

- `manifest.json`: manifiesto autocontenido con hashes, criterios,
  eliminatorias, mensajes inválidos, modelos, corpus, dataset, tokens, costo,
  latencia y digest de imágenes.
- `imagenes.json`: digest SHA-256 de cada imagen referida por la verdad de campo.
- `oficial/`: resultados, informe, manifiestos y métricas de los siete escenarios.
- `verificaciones/`: pruebas, RAG, migración, lint y salida del indexado.

Los artefactos no incluyen `.env`, claves, tokens ni credenciales.

Los SHA-256 se calculan sobre los bytes publicados: UTF-8 sin BOM y saltos de
línea LF. La regla queda fijada en `.gitattributes`, de modo que cada digest
del manifiesto puede reproducirse descargando el blob correspondiente de Git.
