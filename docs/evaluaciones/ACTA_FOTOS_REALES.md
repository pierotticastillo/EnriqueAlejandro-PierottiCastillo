# Acta de limitación — fotos reales para P1-11 (Calidad de visión)

**Fecha:** 2026-11-19  
**Brecha:** P1-11 — Calidad de visión con fotos reales  
**Origen:** Academia (presentaciones del curso) / Mejora interna (auditoría)

## Situación

La planta simulada (`NovitSoftware/academia2026-tp`) no provee un dataset de fotos reales de campo. El archivo
`planta/sim/planta_sim/fotos.py` de la planta espera fotos en `datos/fotos/`, pero esa carpeta **no existe** en el
repositorio de la academia. Como consecuencia, `asegurar_fotos()` genera siempre **fotos de muestra sintéticas**
(todas idénticas: rectángulo azul con círculo gris y texto "FOTO DE MUESTRA"), no fotos de campo reales.

## Decisión

No se modificó `academia2026-tp` (el repositorio de la planta se levanta tal cual, sin tocar). En su lugar:

1. Se creó un **dataset mínimo sintético** de 9 fotos etiquetadas en este repositorio, bajo `fotos/campo/` y
   `fotos/adversario/`:
   - 3 fotos con daño estructural (grieta, rotura, deformación): `danio_estructural_0{1,2,3}.jpg`
   - 3 fotos sin daño estructural (equipo normal): `sin_danio_0{1,2,3}.jpg`
   - 1 foto corrupta (no es una imagen válida): `corrupta_01.jpg`
   - 2 fotos adversarias con inyección visual embebida: `inyeccion_visual_0{1,2}.jpg`

2. Se actualizó `docs/evaluaciones/baterias/vision.json` con 9 casos correspondientes.

3. Se ejecutó `scripts/evaluar_vision.py` contra el modelo de nube con visión real (NVIDIA Nemotron 3 Nano Omni):
   - **8/9 casos aprobados**
   - Precisión de daño estructural: 1,0
   - Recall de daño estructural: 0,667 (1 falso negativo por respuesta 422 de la API, probablemente transitorio)
   - Tasa de indeterminado: 0,111 (la foto corrupta resultó correctamente `indeterminado`)
   - 0 falsos positivos
   - Fail-safe verificado: sin resultado válido de visión, la foto va a revisión humana, nunca se afirma "no hay daño"

## Limitación documentada

**No se puede afirmar** "el modelo de visión detecta daño estructural con X% de precisión sobre N fotos de campo
reales", porque **no hay fotos de campo reales disponibles** en la planta simulada. Las métricas acima se midieron
sobre el dataset sintético de este repositorio.

Cuando la Academia provea fotos reales, se deben copiar a `datos/fotos/` de la planta (o montarlas como volumen)
y volver a ejecutar `scripts/evaluar_vision.py` para obtener métricas sobre campo real.

## Archivos relacionados

| Archivo | Descripción |
|---------|-------------|
| `fotos/campo/` | 7 fotos de campo etiquetadas (daño, sin daño, corrupta) |
| `fotos/adversario/` | 2 fotos con inyección visual embebida |
| `docs/evaluaciones/baterias/vision.json` | Batería de 9 casos de visión |
| `docs/evaluaciones/reporte_vision.json` | Reporte de ejecución con métricas reales |
| `scripts/evaluar_vision.py` | Corredor de la batería de visión |
| `generar_fotos_dataset.py` | Script que generó el dataset sintético |
