# Especificación funcional de P3 (versión con estado y criterios de aceptación)

**Fecha:** 2026-11-18 | **Estado del documento:** borrador (P3-0, paso 4 del plan de alineación), pendiente de tu confirmación | **Parte de:** `doc/audit/ESPECIFICACION_proyecto3.md` (texto de entrada, que no se modifica), `doc/audit/GAP_ANALYSIS_proyecto3.md`, `doc/audit/INVENTARIO_FUNCIONAL_proyecto3.md`

## Cómo leer este documento
La especificación de entrada titulaba sus secciones "Lógica y Criterios *Implementados*" aunque describe en parte un diseño objetivo. Aquí cada requisito lleva un **estado** y un **criterio de aceptación**:
- **VIGENTE:** existe en el código y cumple lo especificado; la evidencia es el inventario.
- **VIGENTE (parcial):** existe con desviaciones; el texto dice qué hace hoy el código y qué parte sigue como objetivo.
- **OBJETIVO:** no existe; se construye solo si pasa una prueba preregistrada (decisión del propietario, 2026-11-17: la evidencia manda). El criterio de aceptación es la regla de esa prueba.
- **RETIRADO:** ninguno todavía. Un requisito se retira solo con el resultado de su prueba o con una decisión tuya en el punto de control C2.

Paquetes: P3-1 correcciones de integridad, P3-2' estudio de construcción (tras C1), P3-3 datos de L1, P3-4 economía de L4, P3-5a pruebas baratas de scoring, P3-5b Judge, P3-6 Navigator.

## Capa 1: Elegibilidad
| ID | Requisito | Estado | Criterio de aceptación / paquete |
|---|---|---|---|
| REQ-01 | Filtros duros: datos corruptos, `WRONG_DOC`, cuarentena | VIGENTE | Sin cambios; ver REQ-44 (retención y sospecha de datos). |
| REQ-02 | Historial mínimo continuo de NAV (`MIN_NAV_ROWS`) | VIGENTE (parcial) | La continuidad se exige; `MIN_NAV_ROWS` no se comprueba en vivo, solo en PIT. P3-1.7: diferencia de conjuntos elegibles con regla fijada antes de leerla; el cambio de interruptor lo ordenas tú. |
| REQ-03 | Antigüedad máxima del último NAV (`P3_FUND_NAV_MAX_AGE_DAYS`, 60 días) | VIGENTE | Un fondo sin fila mensual pasa la regla (hallazgo E-6): se mide en P3-1.7. |
| REQ-04 | Monitorizar fusiones y liquidaciones con la serie de presencia en catálogos | VIGENTE (parcial) / OBJETIVO | Hoy: `In_Current_Universe` y frescura del *harvest*. P3-3 (condicionado a C2): serie de presencia del Paquete J, que además sustituye la tasa de abandono supuesta (5 %). |
| REQ-05 | `shared.transfer_eligibility` (UCITS frente a ETF) | OBJETIVO | P3-3: primero un censo de solo lectura del campo de `fund_master` que identifica el vehículo; el censo decide también si la fiscalidad (REQ-30) aplica. |
| REQ-06 | Tipos de vehículo permitidos | OBJETIVO | P3-3: si el censo muestra solo vehículos permitidos, se documenta como precondición del universo; si no, filtro con diferencia de selección completa y comprobación a doble señal. |
| REQ-07 | Universo elegible en la fecha *t* | VIGENTE | Sin cambios. |

## Capa 2: Scoring (Judge) y regímenes (Navigator)
| ID | Requisito | Estado | Criterio de aceptación / paquete |
|---|---|---|---|
| REQ-08 | Objetivo predictivo: ratio de evaluación real neto (*net real appraisal ratio*) | OBJETIVO | P3-5b, solo si P3-5a deja margen: se adopta si M2 mejora en ≥ 0,10 (`MIN_MEANINGFUL_SELECTION_SPREAD`) y M1 no empeora, con prueba de viabilidad previa. Hoy: compuesto de percentiles (`scorer_params.yaml`), con valor de selección débil y positivo (FND-0429, M2 = +0,073, cota inferior +0,012). |
| REQ-09 | Ventanas de 36 meses solapadas con errores Newey-West | OBJETIVO | P3-5b. Antes se lee el resultado existente de FND-0303 (ventana común de 60 meses para dos métricas). |
| REQ-10 | Control de falsos descubrimientos y encogimiento empírico-bayesiano frente a pares | OBJETIVO | P3-5b, mismo criterio que REQ-08. |
| REQ-11 | Impacto determinista del `ongoing_charge_recurrent` | OBJETIVO | **Definición (decisión de P3-0):** comparación de TER entre clases de una misma familia únicamente; el NAV histórico ya es neto de cada clase, así que una penalización de alfa aparte contaría el coste dos veces. Se evalúa en P3-2' (brazo de familia) y en P3-5a (término de coste). |
| REQ-12 | Salida: ranking con bandas de incertidumbre | VIGENTE (parcial) | Hay ranking (`score_final`); las bandas llegan con REQ-08..10 (P3-5b). |
| REQ-13 | Agrupación por exposición real (`Fund_Nature`) | VIGENTE | La naturaleza viene de la clasificación de P1, no de regresión de factores. P3-5a prueba el bono por naturaleza. |
| REQ-14 | Modelo probabilístico de regímenes (HMM, 2 estados) | OBJETIVO | P3-6, condicionado a que el resultado A3 de FND-0193 muestre que la capa de regímenes aporta valor frente a la mezcla estática; si no, se retira junto con REQ-15, 17, 19. |
| REQ-15 | Matriz de probabilidad del régimen actual | OBJETIVO | P3-6. |
| REQ-16 | Indicadores adelantados (diferenciales de crédito, pendiente de curva) | VIGENTE (parcial) | `spread_hy` interviene; `term_spread` se carga y no entra en el árbol por decisión explícita. P3-6 decide si entra. |
| REQ-17 | Medias móviles exponenciales en lugar de señales estáticas de 36 meses | OBJETIVO | P3-6. |
| REQ-18 | `tactical_allocator_weights` (sesgo táctico) | VIGENTE (parcial) | Hoy: tabla discreta de pesos por régimen (`shared/regime_taxonomy.py:46-54`). El sesgo táctico graduado es P3-6; el límite de transición es REQ-24. |
| REQ-19 | Ventanas de volatilidad rápida de 21 a 63 días | VIGENTE (parcial) | Existen como filtros duros de fondo (REQ-40); no alimentan la detección de régimen (P3-6). |
| REQ-20 | Umbrales de activación del diferencial de crédito | VIGENTE | Crisis con `spread_hy > 6,0`; semáforo 4,0 / 6,0. |

## Capa 3: Construcción
| ID | Requisito | Estado | Criterio de aceptación / paquete |
|---|---|---|---|
| REQ-21 | Una única clase por familia | VIGENTE | Deduplicación en scoring y exclusión global por familia. |
| REQ-22 | Elegir la clase más barata accesible | VIGENTE (parcial) | Hoy se conserva la de mayor `score_final`. P3-1.3 aclara si `pick_units` de la prueba FND-0429 es inoperante; P3-2' prueba "más barata dentro de una tolerancia de score". |
| REQ-23 | Ponderación equiponderada dentro de los bloques | VIGENTE | Su finalidad (que lo medido provenga de la selección) la mide la prueba FND-0429 (REQ-52). |
| REQ-24 | Límite de transición `TRANSITION_MAX_DEVIATION` | OBJETIVO | P3-2' brazo 1, rejilla {0,10; 0,20} fijada en la preregistración, tras el punto de control C1. Se adopta con Holm, drawdown ≤ −15 %, victoria a 50 pb y sin cambio de signo con 10 % de abandono. |
| REQ-25 | Pesos por sub-cartera según régimen | VIGENTE | Siete filas que suman 1. |
| REQ-26 | Límites de exposición por divisa | VIGENTE | 30 % no EUR sin cobertura: restricción de construcción y puerta de promoción. |
| REQ-27 | Límites de concentración | VIGENTE | 20 % por fondo, 3 % mínimo, 10 por sub-cartera, 5 por naturaleza, 2 por gestora. Son límites de mandato o de diversificación: no se prueban por defecto. |
| REQ-28 | La cartera objetivo se ensambla antes de evaluar la conveniencia económica | VIGENTE (parcial) | Hoy el filtro de rotación está dentro de `PortfolioBuilder.build`. P3-4 los separa. |

## Capa 4: Ejecución y rotación
| ID | Requisito | Estado | Criterio de aceptación / paquete |
|---|---|---|---|
| REQ-29 | Rotar solo si el beneficio esperado supera el coste pesimista | VIGENTE (parcial) | Filtro apagado por defecto y con defecto (el score del titular es siempre 0). P3-1.1 corrige el defecto; la regla económica completa (calibración de unidades, coste pesimista, margen) es P3-4. |
| REQ-30 | Fiscalidad por tramo y tipo marginal | OBJETIVO (condicionado) | Solo si el censo de P3-3 encuentra vehículos no traspasables; si todos lo son, no aplica. El tipo marginal lo decides tú. |
| REQ-31 | Horquillas, comisiones y penalización por tiempo fuera de mercado | VIGENTE (parcial) | Hoy: comisiones de entrada y salida y un spread fijo del 0,1 %. P3-4: fuente única de parámetros de fricción. |
| REQ-32 | ROT-0: salida automática por pérdida de elegibilidad, alarma crítica o expulsión | VIGENTE (parcial) | Hoy implícito (score 0). P3-1.2: regla de prioridad previa al cálculo económico, con prueba con el filtro activado. |
| REQ-33 | Control de histéresis (banda 0,10) | VIGENTE (parcial) | La banda existe y está apagada; solo se mide en sombra. Decisión de activación: Paquete D (FND-0158). |
| REQ-34 | Libro de ejecuciones (A'8) | OBJETIVO | P3-4: alimentado automáticamente al promover; alerta de conciliación, no bloqueo. |
| REQ-35 | Salida: plan de rotación, órdenes y cartera ejecutada | VIGENTE (parcial) | Hoy la "ejecución" es la promoción manual DRAFT → PRODUCTION. P3-4. |
| REQ-36 | Cuadrícula de estrés de costes | VIGENTE (parcial) | Existe en el backtest PIT (0/25/50 pb), no en la decisión en vivo. P3-4. |
| REQ-37 | `DILUTION_ALLOWANCE` (10-25 pb) | OBJETIVO | P3-4: constante única compartida con el backtest. |
| REQ-38 | `redemption_days` | OBJETIVO | P3-4: la columna existe en `rotation_costs` y no se lee. |

## Requisitos absorbidos del código (antes comportamiento no especificado)
Describen lo que el código hace hoy; se documentan como vigentes y se retiran solo por decisión explícita.

| ID | Requisito (origen) | Estado |
|---|---|---|
| REQ-39 | Filtros de riesgo por sub-cartera: drawdown −0,20 / −0,30 / −0,40, retorno real mínimo −0,05 / −0,02 / 0,0, SRRI ≤ 5 y sin *High Yield* en Defensiva (IMP-01) | VIGENTE |
| REQ-40 | Puertas cortas diarias: drawdown a 6 meses −0,08 / −0,15 / −0,25, volatilidad a 3 meses 0,15 / 0,22, omitidas si la liquidez supera 0,20 (IMP-02) | VIGENTE |
| REQ-41 | Compuesto de seis métricas con pesos por sub-cartera y bono por naturaleza (IMP-03) | VIGENTE; el bono se prueba en P3-5a |
| REQ-42 | Capa de multiplicadores neutra desde FND-0193; código de percentil móvil por retirar (IMP-04) | VIGENTE (neutra); retirada del código aplazada (matriz, H-3) |
| REQ-43 | Árbol de 7 regímenes con umbrales (CLI 100, IPC 3 % / 4 %, petróleo 25 %, tipo 1 %) y semáforo (IMP-05) | VIGENTE |
| REQ-44 | Gobierno de datos y promoción: frescura, linaje, identidad, alarmas, sospecha de datos, retención (≤ 90 días) y exenciones (IMP-06, IMP-09) | VIGENTE |
| REQ-45 | Ciclo de vida DRAFT / PRODUCTION / ARCHIVED con una sola PRODUCTION (IMP-07) | VIGENTE |
| REQ-46 | Liquidez estructural declarada, tope 10 % (IMP-08) | VIGENTE |
| REQ-47 | Medición de histéresis en sombra (25 pb) (IMP-10) | VIGENTE; se generaliza en P3-2' |
| REQ-48 | Backtest PIT y estudio de valor KEEP / SIMPLIFY / REPLACE (IMP-11) | VIGENTE; su registro de fidelidad está en `PIT_FIDELITY_REGISTER.md` |
| REQ-49 | Informe Excel con puertas de reconciliación (IMP-12) | VIGENTE; colores desalineados aplazados (matriz, H-6) |
| REQ-50 | Prueba de selección FND-0429 (M2) y su regla firmada (IMP-13) | VIGENTE; es el criterio de validación de REQ-08..10 y REQ-23 |
| REQ-51 | Ceguera de la estimación puntual y margen de drawdown de 2 pp en pruebas preregistradas (IMP-14) | VIGENTE como política de preregistración |
| REQ-52 | Medir el efecto de la selección separado del de la construcción (propósito de REQ-23) | OBJETIVO: se cumple con las referencias de P3-1.4 (pool ajustado al mandato, selección aleatoria R1 y R2) |

## Cambios de redacción respecto del texto de entrada
- Las secciones "Lógica y Criterios Implementados" pasan a llevar estado por requisito (arriba).
- El "impacto determinista del `ongoing_charge_recurrent`" queda definido en REQ-11.
- Pendiente de tu confirmación: ningún requisito se marca RETIRADO hasta el punto de control C2.
