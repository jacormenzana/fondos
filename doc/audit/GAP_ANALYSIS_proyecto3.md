# Análisis de Gaps Funcionales — `proyecto3`
**Fecha:** 2026-11-17 | **Inventario base:** `doc/audit/INVENTARIO_FUNCIONAL_proyecto3.md` (commit 28673e6, revisión 2026-11-17) | **Fuente de requisitos:** `doc/audit/ESPECIFICACION_proyecto3.md` (los tres informes se movieron de `docs/audit/` a `doc/audit/` el 2026-11-18)

> **Revisión 2026-11-17:** actualización incremental de la edición del 2026-11-16 (inventario b42ee0f). La especificación no cambió (modificada el 11-16 22:24, sin cambios posteriores) y el único código nuevo de `proyecto3/` es `pit_selection_test.py` (FND-0429). Los 38 requisitos y su clasificación se mantienen; cambian: la nota de GAP-01 (hay ya evidencia empírica a nivel de selección), GAP-07 (hallazgo nuevo sobre la regla de familia), la observación de REQ-23, y se añaden IMP-13 e IMP-14. Se contrastó con `doc/decisiones/RESULT_FND-0429_WP5_M2.md` y `PREREG_FND-0429_WP5_AMENDMENT1.md` (lectura parcial por búsqueda de líneas clave, no completa).

> Las rutas de los argumentos llevaban una `/` inicial; se resolvieron relativas a la raíz del repositorio (`c:\desarrollo\fondos`).
> Verificación empírica: además del inventario, se inspeccionó el código (en `proyecto3/`, `shared/`, `proyecto2/`) para cada módulo o parámetro que la especificación nombra y que el inventario no cubría (`transfer_eligibility`, `TRANSITION_MAX_DEVIATION`, `DILUTION_ALLOWANCE`, HMM, Newey-West, EMA, FDR/shrinkage, ROT-0, ledger A'8, `redemption_days`, impuestos, `series_benchmark`, `Ongoing_Charge`). Las búsquedas fueron por texto sobre `*.py`, `*.yaml` y `*.sql`, excluyendo tests y `out/`. No se ejecutó código ni se consultó la BD.
> **Observación sobre la fuente:** la especificación titula sus secciones "Lógica y Criterios *Implementados*", pero describe en realidad un diseño objetivo (To-Be) con varias piezas que no existen en el repositorio (ver §3). Conviene renombrar esas secciones o separar "vigente" de "objetivo" para que la propia spec no induzca a error.

## 1. Resumen Ejecutivo de Cobertura
| Estado | Cantidad | % del Total |
| :--- | :---: | :---: |
| CUBIERTO | 10 | 26,3 % |
| PARCIAL / DESVIADO | 14 | 36,8 % |
| NO IMPLEMENTADO (GAP) | 14 | 36,8 % |
| **Total Requisitos Evaluados** | **38** | **100 %** |

* **Comportamientos no especificados detectados en código:** 14 (sección 4; IMP-13 e IMP-14 nuevos el 2026-11-17).
* **Lectura por capa:** L1 sólida salvo la estructura de vehículo; L2 es el mayor hueco (el "Juez" y el "Navegador" descritos no existen: el código usa un compuesto de percentiles y un árbol de reglas); L3 cumple los topes de cartera pero no los límites de transición; L4 existe solo como filtro de coste apagado por defecto y con un defecto de cálculo.

## 2. Matriz de Trazabilidad (To-Be vs. As-Is)

| ID Req | Descripción del Requisito | Estado | Evidencia en Código (`archivo:línea`) | Detalle de Desviación / Observaciones |
| :--- | :--- | :---: | :--- | :--- |
| REQ-01 | L1: filtros duros por datos corruptos, `WRONG_DOC` y cuarentena | CUBIERTO | `fund_scorer.py:778-789` | `WRONG_DOC` (ON), data hold (ON, registro `P3_DATA_HOLDS` vacío: `shared/config.py:472`) y sospecha de datos (ON: `shared/config.py:703`). La cuarentena por NAV muerto vive en P1 (`In_Current_Universe=0`), fuera de ámbito, y P3 la aplica con el join (`fund_scorer.py:394,399`). |
| REQ-02 | L1: historial mínimo de NAV continuo (`MIN_NAV_ROWS`) | PARCIAL / DESVIADO | Live: ninguna; P2 `run_pipeline.py:714`; PIT `pit_candidates.py:150`; continuidad `fund_scorer.py:798-800` | El scorer en vivo **no** comprueba `MIN_NAV_ROWS` (=12, `shared/config.py:661`): depende de que P2 no genere métricas para fondos cortos (efecto lateral aguas arriba). La continuidad sí se exige (cobertura ≥ 0,80, racha < 6 meses). En PIT sí es explícito. |
| REQ-03 | L1: límite de antigüedad del último NAV (`P3_FUND_NAV_MAX_AGE_DAYS`) | CUBIERTO | `fund_scorer.py:790-794`; `shared/config.py:448` | 60 días. Un fondo sin NAV mensual queda con edad NaN y pasa (fail-open, hallazgo E-6 del inventario). |
| REQ-04 | L1: monitorizar fusiones/liquidaciones mediante la serie de presencia en catálogos | PARCIAL / DESVIADO | `fund_scorer.py:394,399`; `data_freshness.py:96,142` | Solo hay un join con `In_Current_Universe` y un control de frescura del último *harvest* del catálogo. No hay serie de presencia ni señal explícita de "fondo fusionado/liquidado" en P3 (la lógica, si existe, está en P1). |
| REQ-05 | L1: `shared.transfer_eligibility` (estructura del vehículo UCITS vs. ETF) | NO IMPLEMENTADO (GAP) | — (búsqueda sin resultados) | El módulo no existe en el repositorio. |
| REQ-06 | L1: tipos de vehículo permitidos (Open-End UCITS, Money Market…) | NO IMPLEMENTADO (GAP) | — | P3 no filtra por tipo de vehículo; solo por `Fund_Nature` (`scorer_params.yaml:43-55`) y `Credit_Quality` en Defensiva (`fund_scorer.py:818-820`). |
| REQ-07 | L1→L2: universo elegible en la fecha *t* | CUBIERTO | Live `fund_scorer.py:1011-1019`; PIT `pit_candidates.py:148-186` | En vivo es una marca `eligible` en `fund_scores`; en PIT se reconstruye por fecha con la información observable. |
| REQ-08 | L2.1: objetivo predictivo = *Net Real Appraisal Ratio* a largo plazo | NO IMPLEMENTADO (GAP) | `scorer_params.yaml:17-38`; `fund_scorer.py:489-558` | El score es un compuesto de percentiles de `return_ann_real`, `sharpe`, `max_dd`, `alpha_persistence`, `capture_ratio`, `momentum_rank`. No hay ratio de evaluación (appraisal) ni alfa estructural neto. |
| REQ-09 | L2.1: ventanas móviles de 36 meses solapadas con corrección Newey-West | NO IMPLEMENTADO (GAP) | — | Sin referencias a Newey-West/HAC. Las métricas del scorer son *since_inception* (`fund_scorer.py:259-267`); la ventana común PIT está apagada (`COMMON_WINDOW_MONTHS: 0`, `scorer_params.yaml:160`, y solo ≥ 12). |
| REQ-10 | L2.1: control de falsos descubrimientos (FDR) y encogimiento empírico-bayesiano frente a categoría/peers | NO IMPLEMENTADO (GAP) | — | Existe normalización por pares dentro de `Fund_Nature` (`fund_scorer.py:524-540`) y Holm/DSR/PBO, pero estos últimos evalúan el **modelo** (`pit_value.py:427-446`), no la habilidad de cada gestor. No hay shrinkage. |
| REQ-11 | L2.1: impacto determinista de `ongoing_charge_recurrent` sobre el alfa neto | NO IMPLEMENTADO (GAP) | `fund_scorer.py:386-391` (se carga); sin uso posterior | `Ongoing_Charge` se lee al marco de scoring pero ningún peso, filtro ni multiplicador lo usa; solo lo consume el baseline `B-CHEAP` (`pit_baselines.py:168-170`). Los NAV son netos de comisiones, así que el coste entra de forma implícita, no determinista. |
| REQ-12 | L2.1: salida = ranking con bandas de incertidumbre estadística | PARCIAL / DESVIADO | `fund_scorer.py:1002-1017` | Hay ranking (`score_final`); no hay bandas de incertidumbre. |
| REQ-13 | L2.1: agrupación por exposición real (`Fund_Nature`) | CUBIERTO | `fund_scorer.py:524-540`, `969-973` | Percentil dentro de cada naturaleza y bonus por naturaleza. (La spec habla de "exposición real a factores"; el código agrupa por la naturaleza clasificada por P1, no por regresión de factores.) |
| REQ-14 | L2.2: modelo probabilístico de regímenes (HMM, Expansión/Contracción, Risk-On/Off) | NO IMPLEMENTADO (GAP) | `regime_classifier.py:353-415` | Árbol determinista de **7** regímenes (`flat-tree-v2`, `:64`), no HMM ni 2 estados. |
| REQ-15 | L2.2: matriz de probabilidad del régimen actual | NO IMPLEMENTADO (GAP) | `regime_classifier.py:118-146` | `RegimeResult` lleva una sola etiqueta y pesos fijos; no hay probabilidades. |
| REQ-16 | L2.2: indicadores adelantados (diferenciales de crédito, pendiente de curvas) | PARCIAL / DESVIADO | `regime_classifier.py:377-380`, `:321-328`; `:518-525` | `spread_hy` sí interviene (crisis y semáforo). `term_spread` se carga, se persiste y se muestra pero **no entra** en `_classify_row` por decisión explícita (`:321-326`). |
| REQ-17 | L2.2: EMAs en lugar de señales estáticas de 36 meses | NO IMPLEMENTADO (GAP) | — (sin `ewm`/EMA en `proyecto3/`, `proyecto2/src`, `shared/`) | Las señales son yoy/diff y métricas *since_inception*; las ventanas cortas existen solo como filtro (REQ-19). |
| REQ-18 | L2.2: `tactical_allocator_weights` (señales de sobre/infraponderación de riesgo) | PARCIAL / DESVIADO | `shared/regime_taxonomy.py:46-54`; `portfolio_builder.py:646-647` | Los pesos de sub-cartera son una tabla discreta por régimen, sin sesgo táctico graduado. Los módulos `macro_indicators_stream`, `regime_inference_engine` y `tactical_allocator_weights` no existen con esos nombres. |
| REQ-19 | L2.2: ventanas de volatilidad rápida de 21 a 63 días | PARCIAL / DESVIADO | `shared/config.py:781-795`; `fund_scorer.py:825-849` | Las ventanas 21/63 (y 126) existen, pero alimentan **filtros duros de fondo** (DD 6 m, vol 3 m), no la detección de régimen. |
| REQ-20 | L2.2: umbrales de activación para diferenciales de crédito | CUBIERTO | `regime_classifier.py:98`, `:112-113` | Crisis con `spread_hy > 6,0` y semáforo 4,0 / 6,0. |
| REQ-21 | L3: una única clase por familia (`fund_family_id`) | CUBIERTO | `fund_scorer.py:858-898`; `portfolio_engine.py:293-309` | Deduplicación en scoring y exclusión global por familia en la selección (clave de respaldo: 3 primeras palabras del nombre). |
| REQ-22 | L3: elegir siempre la clase **más barata accesible** | PARCIAL / DESVIADO | `fund_scorer.py:884-887` | Se conserva la de mayor `score_final` (desempate `nav_count`); el coste y la accesibilidad no intervienen (ver REQ-11). La prueba FND-0429 documenta "la más barata" (`pit_selection_test.py:8-9,90-96`), pero sobre scores que ya llegan deduplicados por mayor score (ver GAP-07). |
| REQ-23 | L3: ponderación equiponderada dentro de los bloques | CUBIERTO | `shared/config.py:269`; `portfolio_engine.py:386-399` | `equal`, con tope 20 % y suelo 3 %; con < 5 fondos el resto queda en liquidez (`:155-160`). El propósito declarado en la spec (que lo medido provenga de la selección y no de la concentración) cuenta desde 2026-11-17 con una prueba que lo mide: `pit_selection_test.py` (IMP-13). |
| REQ-24 | L3: límite de transición `TRANSITION_MAX_DEVIATION` (ej. 10 % por sub-cartera) | NO IMPLEMENTADO (GAP) | `portfolio_builder.py:646-647`; `shared/regime_taxonomy.py:46-54` | Sin la constante ni lógica equivalente. Un cambio de régimen aplica los pesos nuevos de golpe (p. ej. Expansión 20/45/35 → Crisis 70/25/5: 50 pp). |
| REQ-25 | L3: pesos por sub-cartera Defensiva / Equilibrada / Dinámica | CUBIERTO | `shared/regime_taxonomy.py:46-54` | Siete filas, suman 1. |
| REQ-26 | L3: límites de exposición por divisa | CUBIERTO | `shared/config.py:428,434`; `portfolio_engine.py:317-364`; `scenario_lifecycle.py:295-297` | 30 % no-EUR sin cobertura como restricción de construcción y puerta dura de promoción. |
| REQ-27 | L3: límites de concentración de activos | CUBIERTO | `portfolio_engine.py:92-97` | 20 % por fondo, 3 % mínimo, 10 por sub-cartera, 5 por naturaleza, 2 por gestora. |
| REQ-28 | L3→L4: la cartera objetivo se ensambla **antes** de evaluar la conveniencia económica (capas independientes) | PARCIAL / DESVIADO | `portfolio_builder.py:643-644`, `:749-752`; `portfolio_engine.py:269-278` | El filtro de rotación (L4) vive dentro de `PortfolioBuilder.build` y modifica la cartera objetivo; la histéresis (L4) actúa en la selección (L3) como bonus de score. Las capas no están separadas. |
| REQ-29 | L4: rotar solo si ΔScore > coste pesimista + impacto fiscal + margen de seguridad | PARCIAL / DESVIADO | `portfolio_builder.py:348-397`, `:400-463`; `shared/config.py:278` | Compara `Δscore > coste/max(score_actual, 0,01) + 0,05`; coste central (salida + entrada + 0,1 % fijo), sin impuestos. **Defecto:** `score_actual` es siempre 0 (ver GAP-04). Gate **OFF** por defecto. |
| REQ-30 | L4: fiscalidad por tramo (UCITS vs. ETF) y tipo marginal sobre ganancias | NO IMPLEMENTADO (GAP) | — | Sin lógica fiscal en `proyecto3/` ni en `shared/config.py`. |
| REQ-31 | L4: horquillas bid-ask, comisiones, penalización por tiempo fuera de mercado | PARCIAL / DESVIADO | `portfolio_builder.py:291-345` | Hay comisiones de entrada/salida (por fondo → naturaleza → 0,5 % / 0,0 %) y un spread fijo del 0,1 %. No hay bid-ask por vehículo ni penalización por días fuera de mercado (`redemption_days` no se lee). |
| REQ-32 | L4: ROT-0, salida automática por pérdida de elegibilidad, alarmas críticas o expulsión del catálogo | PARCIAL / DESVIADO | `fund_scorer.py:984`; `score_candidates.py:97-100`; `portfolio_builder.py:466-526`; `scenario_lifecycle.py:336-338` | Implícito: un fondo inelegible tiene score 0 y desaparece de los candidatos del siguiente DRAFT. No es un mecanismo explícito; las alarmas solo impiden **ganar peso** en la promoción (ALARM_WATCH); y con el gate de rotación ON el titular puede reinstalarse aunque sea inelegible (GAP-05). |
| REQ-33 | L4: control de histéresis (banda 0,10) | PARCIAL / DESVIADO | `shared/config.py:253,258`; `portfolio_engine.py:269-278`; `hysteresis_shadow.py:254` | La banda viva es 0,10 (coincide), pero `PORTFOLIO_HYSTERESIS_ENABLED = False`: hoy solo se mide en sombra. |
| REQ-34 | L4: Ledger de Ejecuciones (A'8) | NO IMPLEMENTADO (GAP) | — | No existe. Solo hay filas de auditoría de promoción en `ingestion_log` (`scenario_lifecycle.py:431-451`); el `ledger` de `pit_value.py:247` es un registro de pruebas estadísticas, otra cosa. |
| REQ-35 | L4: salida = plan de rotación, órdenes de compra/venta, cartera ejecutada | PARCIAL / DESVIADO | `portfolio_builder.py:752`; `monthly_report.py:822-877` | El plan se persiste en las notas del escenario **solo con el gate ON**; no hay órdenes ni cartera ejecutada: la "ejecución" es la promoción manual DRAFT→PRODUCTION. |
| REQ-36 | L4: cuadrícula de estrés de costes (*k-grid*) | PARCIAL / DESVIADO | `pit_backtest.py:355`, `:358-379` | Rejilla 0/25/50 pb en la sensibilidad del backtest PIT; no interviene en la decisión de rotación en vivo. |
| REQ-37 | L4: `DILUTION_ALLOWANCE` (10-25 pb) | NO IMPLEMENTADO (GAP) | — | Sin la constante. El único valor análogo es `TX_COST_BPS = 25.0` de la medición en sombra (`hysteresis_shadow.py:33`). |
| REQ-38 | L4: `redemption_days` (días fuera de mercado) | NO IMPLEMENTADO (GAP) | Columna en `db/schema_fondos_*.sql` (`rotation_costs`); sin lectura en `proyecto3/` | La columna existe pero el código no la usa. |

## 3. Catálogo Detallado de Gaps y Desviaciones

### [GAP-01] Motor "Juez" ausente: no hay estimación de alfa neto, Newey-West, FDR ni shrinkage — Severidad: ALTA
* **Requisito afectado:** `REQ-08`, `REQ-09`, `REQ-10`, `REQ-11`, `REQ-12`
* **Comportamiento esperado (To-Be):** ranking por *Net Real Appraisal Ratio* sobre ventanas de 36 meses solapadas con errores Newey-West, con estimación empírico-bayesiana y control de FDR frente a pares, descontando el `ongoing_charge_recurrent`, y entregando bandas de incertidumbre.
* **Comportamiento actual (As-Is):** score = suma ponderada de percentiles por naturaleza de seis métricas *since_inception* de P2, más un bonus por naturaleza (`fund_scorer.py:489-558`, `scorer_params.yaml:17-68`). `Ongoing_Charge` se carga y no se usa (`fund_scorer.py:386-391`). Sin incertidumbre por fondo.
* **Impacto funcional:** la selección premia los extremos del ranking de métricas históricas sin separar habilidad de suerte; el propio inventario anota que "la selección elige extremos de score" (`scenario_lifecycle.py:250`). El estudio FND-0193 ya dictaminó SIMPLIFY para las capas de ajuste (`scorer_params.yaml:86-92`), lo que refuerza que el valor añadido del scoring actual es poco demostrado.
* **Evidencia empírica añadida (2026-11-17):** la prueba de selección de FND-0429 (`pit_selection_test.py`, IMP-13) mide justo esta pregunta y su veredicto firmado es **CONTINUE, con margen delgado**: estimación M2 0,073 con cota inferior +0,012; por debajo del 0,10 que el propietario fijó como relevante; con tasa de abandono del 10 % el ajuste de supervivencia cambia el signo (−0,029); la estimación sin naturaleza es mayor (0,123), es decir, parte de la habilidad aparente es la elección de naturaleza y no de fondo (`doc/decisiones/RESULT_FND-0429_WP5_M2.md`, citado de lectura parcial). Matiz para la decisión de producto: el compuesto vigente tiene valor de selección débil pero positivo; eso no sustituye a un "Juez" con alfa neto, Newey-West, FDR y shrinkage (REQ-08..12), que sigue sin existir y sin criterio de aceptación en la spec.
* **Acción recomendada:** decidir si el "Juez" es objetivo vigente o aspiración. Si lo es: definir la métrica de alfa neto y su estimador en la spec, y diseñar su evaluación con el mismo protocolo PIT preregistrado (`pit_value.py`) antes de sustituir el compuesto actual. Si no lo es: retirar `REQ-08..12` de la especificación o marcarlos como "futuro".

### [GAP-02] "Navegador" sin HMM, probabilidades ni EMAs; indicadores adelantados solo parciales — Severidad: MEDIA
* **Requisito afectado:** `REQ-14`, `REQ-15`, `REQ-16`, `REQ-17`, `REQ-18`, `REQ-19`
* **Comportamiento esperado (To-Be):** probabilidades de régimen en tiempo real (HMM, 2 estados), señales adelantadas de crédito y pendiente de curva, EMAs de corto plazo y un generador de sesgos tácticos de peso.
* **Comportamiento actual (As-Is):** árbol determinista de 7 regímenes sobre variaciones yoy y niveles mensuales con etiqueta única (`regime_classifier.py:353-415`); `term_spread` expuesto pero deliberadamente desconectado (`:321-326`); pesos de sub-cartera tabulados por régimen (`shared/regime_taxonomy.py:46-54`); ventanas 21/63 días solo en filtros de fondo (`fund_scorer.py:825-849`).
* **Impacto funcional:** el régimen cambia por umbrales duros (saltos de etiqueta), con retrasos de publicación (IPC y CLI hasta 2 meses en backtest, y en vivo el dato más reciente disponible), y no hay medida de confianza. Combinado con GAP-03 produce saltos de asignación.
* **Acción recomendada:** acordar si el HMM sustituye o complementa al árbol; hasta entonces, documentar el árbol actual como la implementación vigente (hallazgos H-1 y H-9 del inventario) y registrar el HMM como objetivo con criterio de aceptación (p. ej. comparación PIT contra `flat-tree-v2`).

### [GAP-03] Sin límite de transición entre regímenes (`TRANSITION_MAX_DEVIATION`) — Severidad: ALTA
* **Requisito afectado:** `REQ-24`
* **Comportamiento esperado (To-Be):** la desviación de pesos por sub-cartera entre carteras sucesivas se limita (ejemplo: 10 %).
* **Comportamiento actual (As-Is):** el constructor toma `regime_result.weights` directamente (`portfolio_builder.py:646-647`); la tabla permite saltos de hasta 50 pp (Expansión 20/45/35 → Crisis 70/25/5, `shared/regime_taxonomy.py:46-54`). La única amortiguación es la histéresis a nivel de fondo, que está apagada.
* **Impacto funcional:** un solo mes de cambio de etiqueta (incluida una etiqueta ruidosa) reconfigura toda la cartera; la rotación y los costes asociados no están acotados. Contradice el principio de preservación de capital con horizonte de 3-5 años.
* **Acción recomendada:** definir `TRANSITION_MAX_DEVIATION` y su semántica (por sub-cartera, por paso de construcción, interacción con el cambio de régimen y con la promoción manual) y evaluarlo en el backtest PIT (rejilla de cadencia y banda de `pit_oos.py` ya existe como base).

### [GAP-04] Capa 4 económica inoperante: gate apagado, defecto en el umbral y sin fiscalidad ni coste pesimista — Severidad: ALTA
* **Requisito afectado:** `REQ-29`, `REQ-30`, `REQ-31`, `REQ-35`
* **Comportamiento esperado (To-Be):** rotar solo si ΔScore supera coste pesimista (bid-ask, comisiones, tiempo fuera de mercado) + impuestos + margen, generando plan y órdenes.
* **Comportamiento actual (As-Is):**
  * El gate está desactivado por defecto (`ROTATION_COST_GATE_ENABLED = False`, `shared/config.py:278`).
  * **Defecto verificado en el código:** `load_previous` reconstruye los fondos del escenario previo con las claves `isin, weight, role, fund_nature` (`portfolio_builder.py:589-591`), sin `score`; `rotation_plan` lee `curr_funds[isin_out].get("score", 0)` (`:441`), por lo que el score del titular es siempre 0 y `should_rotate` usa `coste / max(0, 0,01)` = `100 × coste` (`:390`). Con un coste típico de 0,0075-0,0125, el listón sube a ≈ 0,80-1,30 + 0,05 frente a scores de entrada en [0, ~1]: casi ninguna rotación se recomienda y `_apply_rotation_gate` la revierte.
  * **Corrección 2026-11-18 (P3-1.1, FND-0431):** lo anterior sobre la reversión era inexacto. El plan solo llevaba el entrante cuando la rotación SÍ se recomendaba, y la reversión solo actuaba sobre las NO recomendadas con entrante, condición que no podía cumplirse: `_apply_rotation_gate` no revertía nunca, ni siquiera con el score del titular bien leído. Corregido (plan con entrante emparejado siempre, score del titular de la última ejecución de scoring, emparejamiento uno a uno); `tests/test_rotation_gate_fnd0431.py`.
  * El bucle recorre `isins_in` (un `set`) y se detiene en el primer entrante que cumple, sin retirarlo para otras salidas (`:435-450`): emparejamiento arbitrario y reutilizable.
  * Sin impuestos; el coste usa un spread fijo del 0,1 % (`:316`) y comisiones centrales.
* **Impacto funcional:** si se activara el gate tal como está, bloquearía casi todas las rotaciones por un error de datos, no por economía; con él apagado no hay evaluación económica en absoluto y la rotación depende de la decisión manual de promover.
* **Acción recomendada:** antes de activar el gate, corregir el origen del score del titular (releer de `fund_scores` con el régimen y la fecha vigentes), consumir cada entrante una sola vez con orden determinista, y definir en la spec el coste pesimista (REQ-31), el modelo fiscal (REQ-30) y el margen de seguridad. Añadir prueba de regresión que fije el score del titular distinto de 0.

### [GAP-05] ROT-0: la salida por pérdida de elegibilidad no es explícita y puede revertirse — Severidad: ALTA
* **Requisito afectado:** `REQ-32`
* **Comportamiento esperado (To-Be):** salida automática ante pérdida de elegibilidad, alarma crítica o expulsión del catálogo, con prioridad sobre el cálculo económico.
* **Comportamiento actual (As-Is):** un fondo inelegible recibe score 0 (`fund_scorer.py:984`) y deja de ser candidato (`score_candidates.py:97-100`), de modo que sale del DRAFT de forma implícita. Si `ROTATION_COST_GATE_ENABLED` se activa, `_apply_rotation_gate` sustituye al entrante por el titular cuando la rotación "no se recomienda" y relee su `score_total` sin filtrar por `eligible`, régimen ni fecha (`portfolio_builder.py:508-526`), por lo que puede reinstalar un fondo inelegible. Las alarmas solo bloquean aumentos de peso (`scenario_lifecycle.py:336-338`).
* **Corrección 2026-11-18 (P3-1.2, FND-0431):** la reinstalación de un titular inelegible descrita arriba no podía ocurrir con el código anterior, porque la rama de reversión era inalcanzable (ver GAP-04). El riesgo real era el contrario y aparece al arreglar el plan: una reversión viva sin comprobaciones. Ahora ROT-0 es explícito: un titular sin fila en la última ejecución, inelegible, fuera del universo o con score 0 no se retiene nunca, y uno retenible solo vuelve si cumple ISIN y familia únicos, tope por gestora y por naturaleza y presupuesto de divisa. La vigilancia de alarmas (`ALARM_WATCH`) sigue en la puerta de promoción.
* **Impacto funcional:** el mecanismo que la spec presenta como salvaguarda de integridad queda subordinado a un filtro de coste; el riesgo aparece al activar el gate.
* **Acción recomendada:** definir ROT-0 como regla de prioridad previa a `should_rotate` (los fondos inelegibles, en alarma crítica o fuera de catálogo no pueden ser retenidos), con prueba que lo verifique con el gate ON.

### [GAP-06] Elegibilidad de vehículo (UCITS/ETF, tipos permitidos) inexistente — Severidad: MEDIA
* **Requisito afectado:** `REQ-05`, `REQ-06`
* **Comportamiento esperado (To-Be):** `shared.transfer_eligibility` evalúa la estructura del vehículo y se aplican tipos permitidos.
* **Comportamiento actual (As-Is):** no existe el módulo; el único filtro estructural en P3 es `Credit_Quality == "High Yield"` en Defensiva (`fund_scorer.py:818-820`) y el mapeo por naturaleza.
* **Impacto funcional:** un vehículo no permitido que el universo P1 deje pasar entraría en el scoring; además la fiscalidad por tramo (GAP-04) no tiene de dónde derivar el tipo de vehículo.
* **Acción recomendada:** verificar si P1 garantiza ya el universo permitido (en cuyo caso documentarlo como precondición) o crear el módulo y el filtro.

### [GAP-07] La clase representante de la familia no se elige por coste — Severidad: MEDIA
* **Requisito afectado:** `REQ-22`
* **Comportamiento esperado (To-Be):** conservar la clase más barata accesible.
* **Comportamiento actual (As-Is):** se conserva la de mayor `score_final` y, en empate, la de mayor `nav_count` (`fund_scorer.py:884-887`); la accesibilidad no se modela.
* **Hallazgo nuevo (2026-11-17, lectura estática, sin ejecutar):** `pit_selection_test.pick_units` implementa "una clase por familia, la de menor gasto corriente" (`pit_selection_test.py:90-96`; el docstring del módulo lo afirma, `:8`). Pero sus scores proceden de `score_funds_from_df`, que ya aplica `deduplicate_by_family` al final y **descarta** las demás clases (`fund_scorer.py:1021-1022`, `:898`; `pit_candidates.py:180`; el lanzador solo llama a `pick_units`, `p3_pit_selection_test.py:51`). Si ese flujo es el que alimenta la prueba, la regla "más barata" es un no-op y la evidencia M2 mide la clase de **mayor score**, la misma regla de producción y no la de la spec. Conviene confirmarlo con una corrida de comprobación (contar familias con más de una fila por (as_of, sub-cartera) en `run["scores"]`) y, en cualquier caso, que el docstring y la spec digan lo mismo.
* **Impacto funcional:** puede elegirse una clase más cara si su historial es algo mejor; el coste recurrente se paga durante todo el horizonte. Además, la divergencia spec ↔ prueba ↔ producción hace ambigua la lectura de qué regla de familia validó el veredicto de FND-0429.
* **Acción recomendada:** definir el criterio (coste como primer orden o como desempate dentro de una tolerancia de score) y evaluar su efecto con una réplica read-only antes de cambiarlo.

### [GAP-08] Capas 3 y 4 acopladas — Severidad: MEDIA
* **Requisito afectado:** `REQ-28`, `REQ-35`
* **Comportamiento esperado (To-Be):** L3 entrega la cartera objetivo ideal; L4 decide después la conveniencia de transicionar.
* **Comportamiento actual (As-Is):** el filtro de rotación está dentro de `PortfolioBuilder.build` (`portfolio_builder.py:749-752`) y la histéresis modifica el ranking de L3 (`portfolio_engine.py:269-278`); la cartera persistida mezcla objetivo y decisión.
* **Impacto funcional:** no se puede auditar la cartera ideal independientemente de la rotación, ni medir el coste de no rotar.
* **Acción recomendada:** persistir la cartera objetivo sin rotación y la decisión de L4 como artefactos separados (el DRAFT ya es una buena base).

### [GAP-09] Sin ledger de ejecuciones ni órdenes — Severidad: MEDIA
* **Requisito afectado:** `REQ-34`, `REQ-35`
* **Comportamiento esperado (To-Be):** ledger A'8, órdenes de compra y venta y cartera ejecutada.
* **Comportamiento actual (As-Is):** solo el ciclo DRAFT→PRODUCTION con fila de auditoría (`scenario_lifecycle.py:431-451`); no se registra qué se ejecutó realmente.
* **Impacto funcional:** no hay base para medir turnover, costes reales ni desvío entre cartera propuesta y mantenida.
* **Acción recomendada:** definir la entidad del ledger y su alimentación (cualquier alta manual es una carga manual a flaggear según la Directiva D-0).

### [GAP-10] Parámetros de fricción de L4 ausentes — Severidad: MEDIA
* **Requisito afectado:** `REQ-36`, `REQ-37`, `REQ-38`
* **Comportamiento esperado (To-Be):** k-grid de costes, `DILUTION_ALLOWANCE` 10-25 pb, `redemption_days`, tipo marginal.
* **Comportamiento actual (As-Is):** k-grid solo en el backtest (`pit_backtest.py:355`); `DILUTION_ALLOWANCE` inexistente; `redemption_days` almacenado y sin lectura; sin tipo impositivo.
* **Impacto funcional:** los costes de rotación en vivo no reflejan los supuestos con que se valida el modelo (25 pb por lado en PIT).
* **Acción recomendada:** unificar en `shared/config.py` una única fuente de parámetros de fricción para el backtest y para L4 (principio P#11).

### [GAP-11] Historial mínimo y supervivencia solo de forma indirecta en el scorer — Severidad: BAJA
* **Requisito afectado:** `REQ-02`, `REQ-04`
* **Comportamiento esperado (To-Be):** L1 exige historial mínimo continuo y monitoriza la presencia en catálogos.
* **Comportamiento actual (As-Is):** el scorer en vivo no comprueba `MIN_NAV_ROWS`; depende de P2 (`run_pipeline.py:714`); la supervivencia se apoya en `In_Current_Universe` y en el control de frescura del *harvest*.
* **Impacto funcional:** si P2 cambiara su regla, P3 la heredaría sin aviso.
* **Acción recomendada:** declarar la dependencia en la spec o añadir una comprobación explícita en `check_hard_filters`.

## 4. Lógica Implícita o No Especificada en Código (Auditoría Inversa)
* **[IMP-01]:** filtros de riesgo por sub-cartera — drawdown (−0,20 / −0,30 / −0,40), retorno real mínimo (−0,05 / −0,02 / 0,0), SRRI > 5 en Defensiva y *High Yield* en Defensiva (`fund_scorer.py:802-820`, `scorer_params.yaml:73-83`) -> *Recomendación: documentar como requisitos de mandato en L1.*
* **[IMP-02]:** gates cortos diarios (DD 6 m −0,08/−0,15/−0,25; vol 3 m 0,15/0,22; omitidos si liquidez > 0,20) (`fund_scorer.py:825-849`, `scorer_params.yaml:138-146`) -> *Recomendación: formalizar como L1 o eliminar.*
* **[IMP-03]:** compuesto de seis métricas y pesos por sub-cartera, y bonus por naturaleza (Monetario 1,25 … ) (`scorer_params.yaml:17-68`) -> *Recomendación: documentar como "scoring vigente" mientras no exista el Juez (GAP-01).*
* **[IMP-04]:** capa de multiplicadores de régimen y valores fijos en código (×0,90 / ×1,10 con cortes 80/20 en rolling-percentil) (`fund_scorer.py:617-633`) -> *Recomendación: retirar el código muerto (neutro desde FND-0193) o moverlo al YAML.*
* **[IMP-05]:** árbol de 7 regímenes con umbrales concretos (CLI 100, IPC 3 % / 4 %, petróleo 25 %, tipo 1 %) y semáforo Verde/Ámbar/Rojo (`regime_classifier.py:86-115`, `:353-415`, `:472-537`) -> *Recomendación: documentar; la spec habla de regímenes simplificados (2 estados).*
* **[IMP-06]:** puertas de gobierno de datos y promoción (frescura NAV p10/macro/harvest/uniformidad de versión/canario FX/cobertura FX; auditoría, linaje, identidad, alarmas, sospecha, liquidez, FX) (`data_freshness.py:294-319`, `scenario_lifecycle.py:225-373`) -> *Recomendación: formalizar como requisitos de gobierno (no aparecen en la spec).*
* **[IMP-07]:** ciclo de vida DRAFT/PRODUCTION/ARCHIVED con una sola PRODUCTION (`scenario_lifecycle.py:26-29`, `:431-451`) -> *Recomendación: documentar; es la "ejecución" real hoy (GAP-09).*
* **[IMP-08]:** liquidez estructural declarada, tope 10 % y residuo cuando faltan fondos (`portfolio_engine.py:46-54`, `shared/config.py:438`) -> *Recomendación: documentar.*
* **[IMP-09]:** retención de datos y exenciones: data hold (≤ 90 d), sospecha de datos, exenciones verificadas (`fund_scorer.py:782-789`, `shared/config.py:472,703`) -> *Recomendación: documentar o fusionar con "cuarentena" de L1 (REQ-01).*
* **[IMP-10]:** medición de histéresis en sombra (25 pb, flujos `one_step` y `chain`) (`hysteresis_shadow.py:28-279`) -> *Recomendación: documentar como control de L4.*
* **[IMP-11]:** backtest PIT y estudio de valor del modelo con veredicto KEEP/SIMPLIFY/REPLACE (`pit_value.py:427-446`, `pit_oos.py`, `backtest_inference.py`) -> *Recomendación: documentar como capa de validación.*
* **[IMP-12]:** informe Excel con puertas de reconciliación y umbrales de color codificados en el código, con valores desalineados de los del scorer (|fx| > 20, `beta_oil > 0,01`) (`monthly_report.py:493`, `:502-506`, `:616-627`) -> *Recomendación: documentar las puertas; retirar o alinear los colores (H-6 del inventario).*
* **[IMP-13]:** prueba de la capa de ranking a nivel de selección (FND-0429 / WP5): quintil superior frente a mediana de pares, retorno 12 m / volatilidad 36 m, bootstrap de fechas y familias, controles de permutación y alfa plantado, ajuste de supervivencia y veredicto CONTINUE / PIVOT / INCONCLUSIVE (`pit_selection_test.py:29-385`; parámetros en la matriz del inventario) -> *Recomendación: es un requisito de validación sin respaldo en la spec; incorporarlo (con su regla firmada en `doc/decisiones/PREREG_FND-0429_WP5*.md`) como criterio de aceptación del "Juez" (GAP-01) y de la capa 3 (REQ-23).* Controles débiles señalados en el inventario: condición de *drawdown* y brecha de supervivencia con apertura por defecto 0,0 cuando faltan datos (E-14, `:344,357,384`), suelo de volatilidad duplicado como literal (H-11, `:35`), `n_dates` con fechas sin outcome (H-12, `:160`).
* **[IMP-14]:** ceguera de la estimación puntual como regla de diseño (las rutas WP5.1/WP5.2 solo llaman a `bootstrap_se`) y umbral decisorio de margen de *drawdown* 2 pp (`pit_selection_test.py:14-16,221-226,319`) -> *Recomendación: documentar como política de pre-registro; hoy vive solo en un docstring y en un test.*

## 5. Hoja de Ruta Sugerida de Remediación
1. **Bloqueantes / Integridad (Prioridad 1):**
   * GAP-04 (defecto del score del titular y del emparejamiento) y GAP-05 (ROT-0 con prioridad sobre el coste): ambos deben resolverse **antes** de encender `ROTATION_COST_GATE_ENABLED`; hoy el riesgo está latente porque el gate está apagado.
   * GAP-03: decidir `TRANSITION_MAX_DEVIATION` y medirlo con el backtest PIT.
2. **Reglas de negocio y cálculos (Prioridad 2):**
   * GAP-01: decisión de producto sobre el "Juez" (implementar con evaluación preregistrada o retirar de la spec); ya existe un primer dato (M2 débil, IMP-13) que acota qué mejora hay que exigirle.
   * GAP-07: comprobar si `pick_units` es un no-op y alinear spec, docstring y producción antes de usar M2 como evidencia de la regla de familia.
   * GAP-02: decisión sobre HMM/EMAs; mientras tanto, documentar el árbol actual.
   * GAP-07 (coste en la elección de clase), GAP-10 (fuente única de parámetros de fricción), GAP-06 (vehículo permitido).
3. **Limpieza documental y casos borde (Prioridad 3):**
   * Corregir la especificación: separar "vigente" de "objetivo" en los títulos "Lógica y Criterios Implementados"; incorporar las IMP-01 a IMP-12 como requisitos o retirarlas.
   * GAP-08 (separar objetivo y decisión de L4), GAP-09 (ledger), GAP-11 (precondición `MIN_NAV_ROWS`).
   * Resolver los hallazgos documentales H-1 a H-9 del inventario (desfases de `AGENTS.md` y docstrings).

## 6. Límites de la verificación
* La clasificación de requisitos de P1/P2 (p. ej. cuarentena de NAV, `MIN_NAV_ROWS` aplicado en P2) se basa en las referencias que aparecen fuera de `proyecto3/` y no se auditó el módulo externo.
* "No implementado" significa que no se encontró en `*.py`, `*.yaml` y `*.sql` (excluyendo tests y `out/`); no se buscó en documentación ni en el backlog (`gestion.backlog`), donde algunos podrían estar registrados como trabajo planificado.
* Las partes del inventario que no se leyeron línea a línea (módulos PIT auxiliares, hoja macro del informe) no se han cruzado contra la especificación.
* **Revisión 2026-11-17:** los documentos de `doc/decisiones/` solo se leyeron por búsqueda de líneas clave (varias líneas largas se omitieron en la salida), de modo que las cifras M2 citadas deben contrastarse con el texto completo antes de usarlas como decisión. El hallazgo de GAP-07 sobre `pick_units` es lectura estática; no se ejecutó la prueba ni se inspeccionó `run["scores"]`. No se reevaluaron los 38 requisitos: nada en el código de sus evidencias cambió en el rango b42ee0f..28673e6 (`git diff --stat` sobre `proyecto3` solo muestra `pit_selection_test.py`, su test y `AGENTS.md`).
