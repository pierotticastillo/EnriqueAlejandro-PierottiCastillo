# Corrección · EnriqueAlejandro-PierottiCastillo

**Nota: 91.3 / 100 · APRUEBA** (mínimo 60)

## Puntaje

| Criterio | Peso | Valor | Puntos |
|---|---:|---:|---:|
| **A. Parte por voz** | | | |
| A1 Cobertura | 5 | 100% | 5.0 |
| A2 Borrador al primer intento | 10 | 61% | 6.14 |
| A3 Parte final correcto | 8 | 80% | 6.38 |
| A4a Pregunta cuando hace falta | 4 | 83% | 3.33 |
| A4b No pregunta de más | 3 | 100% | 3.0 |
| **B. Detección en planta** | | | |
| B1 Sin novedad / Escalado | 10 | 96% | 9.61 |
| B2 Alerta de seguridad | 10 | 100% | 10.0 |
| **D. Órdenes de trabajo** | | | |
| D1 Reglas de aprobación | 15 | 100% | 15.0 |
| D2 Orden adecuada a la falla | 10 | 100% | 10.0 |
| **E. Operación sin enlace** | | | |
| E1 Sigue andando sin enlace | 3 | 100% | 3.0 |
| E2 Pendientes visibles | 3 | 50% | 1.5 |
| E3 Sin duplicados | 4 | 99% | 3.95 |
| **F. Robustez** | | | |
| F1 Mensajes válidos | 2 | 100% | 2.0 |
| F2 Latido | 1 | 100% | 1.0 |
| F3 Sobrevive al corte de luz | 2 | 100% | 2.0 |
| **G. Cierre y certificación** | | | |
| G1 Orden ejecutada | 3 | 100% | 3.0 |
| G2 Cierre propio | 2 | inactivo | – |
| G3 Certificación de la contratista | 5 | inactivo | – |

## Entrega

- README.md: sí
- docker-compose.yml: sí
- tests: sí

## Detalle por escenario

- A2 · martes-tranquilo: 67% — {"notas": 9, "confirmados_al_primero": 6}
- A2 · tormenta-perfecta: 46% — {"notas": 13, "confirmados_al_primero": 6}
- A2 · fuga-sin-enlace: 50% — {"notas": 4, "confirmados_al_primero": 2}
- A2 · sin-luz: 89% — {"notas": 9, "confirmados_al_primero": 8}
- A2 · contratista: 79% — {"notas": 19, "confirmados_al_primero": 15}
- A2 · ataque: 20% — {"notas": 5, "confirmados_al_primero": 1}
- A2 · libre: 79% — {"notas": 24, "confirmados_al_primero": 19}
- A3 · martes-tranquilo: 89% — {"notas": 9, "sin_confirmar": ["NOTA-00008"]}
- A3 · tormenta-perfecta: 77% — {"notas": 13, "sin_confirmar": ["NOTA-00009", "NOTA-00017", "NOTA-00018"]}
- A3 · fuga-sin-enlace: 50% — {"notas": 4, "sin_confirmar": ["NOTA-00001", "NOTA-00006"]}
- A3 · contratista: 95% — {"notas": 19, "sin_confirmar": ["NOTA-00027"]}
- A3 · ataque: 60% — {"notas": 5, "sin_confirmar": ["NOTA-00001", "NOTA-00005"]}
- A3 · libre: 88% — {"notas": 24, "sin_confirmar": ["NOTA-00019", "NOTA-00028", "NOTA-00034"]}
- A4a · contratista: 0% — {"ambiguas": ["NOTA-00014"], "pregunto": []}
- B1 · tormenta-perfecta: 84% — {"decisiones": 25, "errores": [{"nota": "NOTA-00005", "equipo": "B-101", "esperado": "SinNovedad", "publicado": ["Escalado"]}, {"nota": "NOTA-00005", "equipo": "B-102", "esperado": "Escalado", "publicado": ["SinNovedad"]}, {"nota": "NOTA-00011", "equipo": "B-102", "esperado": "Escalado", "publicado": ["SinNovedad"]}, {"nota": "NOTA-00014", "equipo": "B-102", "esperado": "Escalado", "publicado": ["
- B1 · contratista: 94% — {"decisiones": 32, "errores": [{"nota": "NOTA-00014", "equipo": "F-201A", "esperado": "SinNovedad", "publicado": ["Escalado"]}, {"nota": "NOTA-00014", "equipo": "B-101", "esperado": "Escalado", "publicado": ["SinNovedad"]}]}
- B1 · libre: 95% — {"decisiones": 41, "errores": [{"nota": "NOTA-00034", "equipo": "F-201A", "esperado": "SinNovedad", "publicado": []}, {"nota": "NOTA-00034", "equipo": "B-102", "esperado": "SinNovedad", "publicado": []}]}
- E2 · tormenta-perfecta: 50% — {"avisa_pendientes_en_el_corte": false, "queda_en_cero_despues": true}
- E2 · fuga-sin-enlace: 50% — {"avisa_pendientes_en_el_corte": true, "queda_en_cero_despues": false}
- E3 · ataque: 92% — {"duplicados": [{"orden": ["F-201A", ["T-F-CAR"]], "ids": ["ORD-F-201A-FC-T01", "ORD-F-201A-FC-T02"]}]}
