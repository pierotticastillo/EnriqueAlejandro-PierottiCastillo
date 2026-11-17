# Dependencias externas — Plan de Implementación 2

Este documento registra las dependencias externas del Plan 2 (Fases 8–12),
su estado actual, el origen de la información, la fecha de confirmación y el
fallback documentado para cada una. Se mantiene para que el plan sea honesto
sobre qué está hecho y qué depende de información ajena al repositorio.

Fuente de la matriz: `Plan_de_implementación/Plan_de_implementación_2.md`, §8.

| ID | Dependencia | Qué se necesita | Estado | Origen de la información | Fecha de confirmación | Fallback |
|---|---|---|---|---|---|---|
| DEP-01 | Academia, informada por el usuario | Fecha, alcance, escenarios y rúbrica de Entrega 2. | Pendiente | Usuario / Academia | — | Documentar el alcance inferido hasta recibir la confirmación. |
| DEP-02 | Academia, informada por el usuario | Confirmación del modelo de visión. | Pendiente | Usuario / Academia | — | Mantener la desviación documentada y el verificador. |
| DEP-03 | Academia/planta, informada por el usuario | Registro de ingreso/egreso. | Pendiente | Usuario / Academia / planta | — | Horas informativas; no bloquean el cierre. |
| DEP-04 | Academia/planta, informada por el usuario | API o contrato de facturación. | Pendiente | Usuario / Academia / planta | — | Entregar habilita con número y monto; declarar el puente pendiente. |
| DEP-05 | Academia/planta, informada por el usuario | Fotos reales de campo con etiqueta. | Pendiente | Usuario / Academia / planta | — | Publicar que la calidad no está medida; fail-safe verificado. |
| DEP-06 | Academia/planta, informada por el usuario | Lista de fallas activas por equipo. | Pendiente | Usuario / Academia / planta | — | Ampliar síntomas del catálogo y vocabulario ASR. |
| DEP-07 | Academia, informada por el usuario | Canal/guion para que el corrector mida la Central y G2/G3. | Pendiente | Usuario / Academia | — | Prueba manual + tests + acta de revisión. |
| DEP-08 | Academia, informada por el usuario | Confirmación de si los criterios de Semana 4 forman parte de la Entrega 2 y SLA esperado para decisiones humanas. | Pendiente | Usuario / Academia | — | Tratar los criterios como mejoras internas; usar vencimiento configurable con escalamiento y nunca autoaprobar. |

## Resumen del estado

Todas las dependencias (DEP-01 a DEP-08) están **pendiente** de confirmación por parte de la Academia, comunicada a través del usuario. Hasta que se reciba dicha confirmación:

- El alcance de la Entrega 2 se considera **inferido** (no oficial).
- No se puede cerrar formalmente **P0-07** (fecha, alcance, rúbrica y formulario de la Entrega 2).
- Las consideraciones de Semana 4 (checkpoint, handoff, límites de autonomía, evaluación adversarial) se adoptan como **mejoras internas** con fallback documentado, no como requisitos obligatorios de la Entrega 2.

## Próxima acción

Solicitar al usuario que confirme con la Academia cada uno de los puntos DEP-01 a DEP-08 y registrar la respuesta oficial en este documento con la fecha de confirmación correspondiente.
