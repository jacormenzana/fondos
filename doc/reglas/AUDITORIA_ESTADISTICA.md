# AUDITORÍA ESTADÍSTICA — Distribución de Atributos de Coste y Métricas P2

**Propósito:** Documento canónico de la auditoría estadística de distribuciones — evaluación del
modelo de 7 bloques, catálogo de indicadores, y especificación de un motor genérico que lo
automatice. Responde a «¿cómo se audita estadísticamente la calidad de un cálculo, no solo su
rango?».
**Dominio:** `auditStatisticalDataDistributionCostAttributes` (P1) ·
`auditStatisticalDataDistributionP2Metrics` (P2) · implementación en `shared/statistical_audit/`.
**Lectura obligatoria** antes de modificar cualquiera de los dos skills o el motor de auditoría
estadística.

**Estado (2026-09-13): Fases A–D completas** (§8). El motor está implementado en
`shared/statistical_audit/` (11 funciones puras + persistencia + 5 catálogos declarativos),
ejecutable vía `scripts/audit/run_statistical_audit.py --domain costs|p2`, con persistencia real
en `audit_statistic`/`audit_finding` y comparación entre ejecuciones (`--compare-to`). Los tres
defectos de producción D1–D3 del motor de alertas (§2.5) están corregidos en código. Lo que sigue
abierto: PEER segmentation (solo GLOBAL hoy), 2 de 5 pares del Bloque 2 de P2 sin cablear, varios
invariantes de Bloque 5 de P2 sin columnas derivables en el modelo actual, y la función #12
(`reconcile_with_alerts`) especificada pero no implementada — ver el propio runner (`--help` o su
docstring) para la lista exacta y actualizada de huecos, que se imprime en cada ejecución.

---

## §1. Alcance y frontera con `pipelineP2Audit`

Las dos skills auditan **forma estadística**: momentos, vallas robustas de outliers, igualdad
cruzada entre métricas, concentración de moda/cero, plausibilidad de escala. La familia
`pipelineP1Audit` / `pipelineP2Audit` / `pipelineP1P2Audit` audita **fiabilidad operacional**:
cobertura NULL, staleness > 60 días, provenance/cohorte, delta de cobertura entre ejecuciones. Esta
frontera ya está bien trazada en ambas skills (`auditStatisticalDataDistributionP2Metrics.md` §1,
línea 11) y este documento no la modifica — la hereda.

Población auditada, verificada contra la base de datos en producción:

| Dominio | Tabla(s) | Filtro | Tamaño verificado |
|---|---|---|---|
| Coste (P1) | `fund_master` (11 columnas) + `fund_cost_schedule` (3 columnas) | `In_Current_Universe=1` | 2.930 fondos activos |
| Métricas (P2) | `fund_metrics` (escalar, 111 métricas) | idem, JOIN por ISIN | 1.588.052 filas, 486 grupos `(metric, horizon, real_flag, metric_version)` |
| Series (P2) | `fund_metric_timeseries` (5 métricas curadas × 5 ventanas × 2 signos) | idem | 31.705.170 filas |

---

## §2. Evaluación del modelo

### 2.1 Qué hace bien

El diseño de 7 bloques separa correctamente cuatro niveles de análisis — caracterización de
distribución (Bloques 1, 3), detección de individuos anómalos (Bloque 4), coherencia estructural
(Bloques 2, 5, 7) e integridad temporal (Bloque 6) — y esta separación debe conservarse.

Su mayor acierto es metodológico, no estadístico: **el Bloque 2 (igualdad cruzada) encuentra
defectos de binding que ningún control rango-a-rango puede ver.** La propia skill de coste cita un
hallazgo histórico de 663 fondos (`Exit_Fee_Pct == Management_Fee_Pct`) invisible en cualquier
tabla de distribución univariante — dos conceptos de coste distintos sosteniendo el mismo valor
solo puede ser un error de asignación de columna, nunca coincidencia. Esa es la contribución que
justifica el modelo completo.

La regla de snapshot transversal (§2 en ambas skills: operar sobre la última observación por
`(ISIN, metric, window, real_flag)`, nunca sobre historia mezclada) es correcta y evita conflactar
dispersión entre fondos con autocorrelación temporal dentro de un fondo — es una decisión ya
tomada correctamente en el motor de categorías existente
(`compute_category_snapshot`, `proyecto2/src/calculations/rolling_stats.py:279`), que agrupa por
fondo y toma la última fila (`.groupby([...]).last()`), no por fecha global máxima. Este patrón
debe reutilizarse literalmente, no reinventarse.

### 2.2 Debilidades — confirmadas contra el código y los datos en vivo, no solo teóricas

| # | Debilidad | Evidencia en este repositorio |
|---|---|---|
| W1 | Los nombres de métrica están hard-coded dentro del procedimiento, no en un catálogo separable de la lógica estadística | ambas skills listan pares/reglas inline en Markdown; cero código reutilizable |
| W2 | Población de Bloque 1/3/4 puede ser excesivamente agregada | `fund_metrics` mezcla 111 métricas en 486 grupos sin segmentación por `Fund_Nature`; el motor de categorías P2 ya segmenta así (`compute_category_snapshot`) pero el audit no lo reutiliza |
| W3 | Momentos (`skew`, `kurtosis`, `CV`) se calculan sin guardas | ver §2.3 — ejemplo real con `n=2.889` |
| W4 | Dos invariantes del Bloque 5 no son universales | `sortino ≥ sharpe` y `vol_ann>0 cuando return_ann≠0` — ver §2.4 |
| W5 | Sin dimensión run-to-run | ninguna skill compara la ejecución actual contra la anterior; una regresión que desplaza toda la población un 50% no dispara nada si cada valor individual sigue siendo plausible |
| W6 | Sin persistencia de hallazgos | cada ejecución es efímera; no hay tabla de findings, no hay comparación histórica (ver §6) |
| W7 | El motor operacional de alertas contra el que Bloque 4 reconcilia está roto en producción | ver §2.5 — 3 defectos verificados en vivo |

### 2.3 Ejemplo real — por qué los momentos necesitan `min_n` y guarda de escala

Verificado en `fund_metrics`, `metric='vol_ann'`, `horizon='since_inception'`, `real_flag=0`,
universo activo, `n=2.889`:

| Estadístico | Valor |
|---|---|
| p50 | 0.132 |
| p95 | 0.244 |
| max | **613.29** |
| skewness | 37.06 |
| kurtosis (Fisher) | 1394.97 |

Tres fondos (`LU2536454403`=613.29, `LU2536453348`=554.50, `LU2473381015`=123.48) — el 0.1% de la
población — bastan para inflar skewness/kurtosis del resto hasta hacerlos ilegibles. Ésta es
exactamente la firma de "contaminación de escala" que el Bloque 7 debe capturar (`> 5.0` es el
`_VOL_SANITY_CAP` ya definido en `proyecto2/src/calculations/srri.py:121`), y confirma que Bloques
3 y 7 no son redundantes: el Bloque 7 (bounds) filtra estos 3 casos antes de que Bloque 3
(momentos) los reciba. Calcular skewness/kurtosis **sin excluir primero las violaciones de bound
declaradas HARD/PLAUSIBILITY** es un error de secuenciación, no solo de guarda estadística.

### 2.4 Dos invariantes del Bloque 5 (coste y métricas) que no son universales

**`sortino ≥ sharpe`** — falso cuando el exceso de retorno es negativo: con `excess=-0.10`,
`vol=0.20`, `downside_dev=0.10` → `Sharpe=-0.50`, `Sortino=-1.00`, luego `Sortino < Sharpe` siendo
ambos cálculos correctos. La regla correcta, con idéntico numerador en ambos:
`excess_return > 0 → Sortino ≥ Sharpe`; `excess_return < 0 → Sortino ≤ Sharpe`.

**`vol_ann > 0` cuando `return_ann ≠ 0`** — no es una imposibilidad matemática: una serie con
retorno periódico idéntico y distinto de cero produce `vol_ann=0` sin que la serie esté congelada.
La comprobación correcta ata volatilidad cero a **varianza de retornos periódicos**, no a
`return_ann`: `vol_ann==0 AND periodic_return_variance > ε → inconsistencia`.

### 2.5 Defectos verificados en el motor de alertas operacional (`fund_metric_alerts`) — CORREGIDOS 2026-09-13

El Bloque 4 de la skill P2 exige "reconciliar contra `fund_metric_alerts`" para reportar solo
hallazgos incrementales. Verificado en producción, el motor que genera esa tabla tenía tres
defectos que invalidaban esa reconciliación tal como estaba especificada:

| # | Defecto | Evidencia en vivo (medida antes del fix) | Fix |
|---|---|---|---|
| D1 | `fund_metric_alerts.value` es NULL en el 100% de las filas | `14.674 / 14.674` filas con `value IS NULL` — `compute_alerts` (`rolling_stats.py:440`) lee `row.get("value")` pero `compute_category_snapshot` nunca emitía esa columna | `compute_category_snapshot` ahora añade `"value": v` a cada registro emitido |
| D2 | `reference_value` es NULL exactamente en las 612 filas ALARM | `235` de `DD_CAT_P03` + `377` de `RET_CAT_P05` — el motor construye `cat_p03`/`cat_p05` (`rolling_stats.py:434,438`) pero el snapshot solo emitía `cat_p10/p50/p90/p97` | `compute_category_snapshot` ahora calcula y emite también `cat_p03`/`cat_p05` |
| D3 | 2 de las 6 reglas de `ALERT_RULES` nunca disparan | `VOL_CAT_P90`/`VOL_CAT_P97` apuntaban a `window='rolling_6m'`, que tiene **0 filas** en `fund_metric_timeseries` (solo existen `rolling_1y/2y/3y/5y/10y`) | `ALERT_RULES` (`shared/config.py`) retargeteado a `window='rolling_1y'` — la ventana curada más corta que este motor puede ver; un alerta real sobre `rolling_6m` necesitaría una función separada que lea `fund_metrics` (metric_version `'d1'`), fuera de alcance de este fix |

Corregido en `proyecto2/src/calculations/rolling_stats.py` y `shared/config.py`
(commit pendiente de este mensaje de sesión), con 5 tests de regresión en
`proyecto2/tests/calculations/test_alert_engine_fix_20260913.py`. **Importante:** la tabla
`fund_metric_alerts` en producción sigue conteniendo las filas antiguas (rotas) escritas por el
código previo hasta que se ejecute un ciclo P2 real (`P2_calculateIndicators.bat` o
`run_pipeline.py`) — el fix cambia el código, no reescribe retroactivamente filas ya persistidas.
Por eso la función #12 (`reconcile_with_alerts`) sigue sin cablearse en el runner: reconciliar
contra la tabla actual reconciliaría contra datos aún obsoletos.

---

## §3. Catálogo de indicadores estadísticos

Cada indicador se describe por: qué mide, para qué sirve, su modo de fallo, y la población sobre
la que es legítimo calcularlo.

| Indicador | Qué mide | Para qué sirve | Guarda obligatoria |
|---|---|---|---|
| `n` / `n_total` / `n_valid` | Tamaño de la población con dato | Denominador de todo lo demás; sin `n_expected` no es interpretable | reportar siempre junto a `n_expected` (universo activo conocido: 2.930) |
| `null%` | Proporción sin valor calculado | Detecta caída de cobertura — **NULL ≠ error** (P#1/R-4: VIF, min-obs, régimen ausente son NULL correctos) | comparar contra `null%` de la ejecución anterior, no contra un umbral absoluto |
| `min` | Extremo inferior | Signo incorrecto, escalado, transformación errónea | — |
| `p50` (mediana) | Centro robusto | Insensible a colas — preferible a `mean` en distribuciones asimétricas | — |
| `p95` | Umbral de cola | Desplazamientos de cola sin depender del máximo absoluto | — |
| `max` | Extremo superior | Un único valor imposible revela ×100, /100, error de anualización o de binding — ver ejemplo real §2.3 | contrastar siempre contra el bound de plausibilidad del Bloque 7 antes de aceptarlo |
| `zero%` | Proporción exactamente cero | Variables sparse, defaults, procesos no ejecutados | `zero% > 70%` excluye la columna/métrica de MAD robust-z (MAD=0 → todo no-cero reporta como outlier) |
| `mode` / `mode%` (→ **dominant_value_pct**) | Concentración en un valor | `mode% > 40%` sugiere template/hardcode/bleed | para variables continuas, el modo exacto (`0.143628` ≠ `0.143629`) es inútil; usar tolerancia de redondeo declarada por métrica, no igualdad exacta |
| `mean` | Centro de masas | Solo informativo junto a `p50`; `mean >> median` indica cola derecha | no interpretar aislado en distribuciones muy asimétricas |
| `sd` | Dispersión transversal | Heterogeneidad **entre fondos**, no volatilidad financiera — `sd(vol_ann)` mide cuánto difieren las volatilidades entre sí | — |
| `CV` (`sd/mean`) | Dispersión relativa | Comparar dispersión entre escalas distintas | **inválido cuando `abs(mean) < ε_metric`** (returns/alpha/beta/Sharpe cruzan cero con frecuencia); devolver `CV=NA, CV_status=MEAN_NEAR_ZERO` en ese caso, nunca un número sin sentido |
| `skewness` | Asimetría | `\|skew\|>3` dispara revisión | requiere `n ≥ min_n` (30–50); con `n<20` es estadísticamente inestable — ver §2.3 |
| `kurtosis` | Intensidad de colas | `>10` (convención **Fisher, exceso**, normal=0 — la que usa `pandas.kurt()`) señala contaminación | mismo `min_n`; excluir familias de régimen (pocos `n_obs` por diseño) |
| IQR fence (`Q1−1.5·IQR`, `Q3+1.5·IQR`) | Outliers no paramétricos | Robusto, no requiere normalidad | en distribuciones legítimamente asimétricas (`return`, `drawdown`, `capture`, `beta`) marca muchos valores correctos — usar como señal de revisión, no de exclusión automática |
| MAD robust-z (`0.6745·(x−mediana)/MAD`) | Outliers robustos | `\|z\|>3.5`; extremo `>5` | cuando `MAD=0` (población concentrada), no omitir en silencio — devolver `OUTLIER_METHOD_UNAVAILABLE, reason=MAD_ZERO` |
| Igualdad cruzada (Bloque 2 / cost Bloque 2) | Dos métricas/columnas distintas sosteniendo el mismo valor | Firma de binding — ver hallazgo real de 663 fondos citado en la skill de coste | nunca `abs(a-b)<ε` sola: exige **predicado de elegibilidad** (p.ej. `IPC>0` antes de comparar nominal-vs-real) AND `n≥min_matches` |
| Invariante estructural (Bloque 5) | Relación aritméticamente imposible | Señal más fuerte que un outlier estadístico | separar `HARD_INVARIANT` (imposible) de `PLAUSIBILITY_BOUND` (extraordinario pero posible) — nunca mismo severity |
| Bound de plausibilidad (Bloque 7) | Rango económicamente razonable | Trigger de revisión, nunca auto-clamp | carve-out por prefijo `crisis_` (config-driven vía `CRISIS_WINDOWS`), nunca año hard-coded |

---

## §4. Catálogo de funciones genéricas

El nombre de la métrica **nunca** aparece dentro de una función estadística — solo en los
catálogos declarativos de §5. Ubicación propuesta: `shared/statistical_audit/` (P#11 — compartido
entre el dominio de coste, P1, y el de métricas, P2).

| # | Función | Entrada → Salida | Población aplicable | Nota de reutilización |
|---|---|---|---|---|
| 1 | `build_population(spec)` | especificación de tabla/filtro → frame homogéneo | cualquiera | aplica siempre `In_Current_Universe=1` |
| 2 | `build_snapshot(series, group_keys, tolerance_days)` | serie temporal → `(snapshot_df, held_out_df, max_date, date_spread)` | `fund_metric_timeseries`; identidad para escalares ya-latest | reutiliza el contrato `.groupby(...).last()` de `compute_category_snapshot` (`rolling_stats.py:320-324`) — **no reimplementar** |
| 3 | `profile_coverage(vector)` | vector → `n_total, n_valid, n_null, null_pct, coverage_pct` | todas | — |
| 4 | `profile_location(vector)` | vector → `min, p05, p25, p50, p75, p95, max` | continuas | p25/p75 se calculan aquí una sola vez y se reutilizan en `detect_outliers` (IQR) — no recalcular |
| 5 | `profile_moments(vector, cfg)` | vector + config → `mean, sd, cv, cv_status, skew, kurtosis` | continuas | aplica `min_n`, guarda de `CV`, convención Fisher fija; usar `pandas.Series.skew()/.kurt()` — no introducir `scipy` (instalado pero sin uso en el repo; no añadir la primera dependencia sin necesidad) |
| 6 | `profile_mass_points(vector, cfg)` | vector + config → `zero_pct, dominant_value, dominant_pct, mass_class` | todas | generaliza `mode` — soporta `equality_tolerance`/`rounding_digits` por métrica |
| 7 | `detect_outliers(vector, method, cfg)` | vector + `IQR`\|`MAD_Z` → filas con evidencia (`isin, value, q1, q3, mad, robust_z, severity`) o `unavailable_reason` | continuas no degeneradas | motivo explícito (`MAD_ZERO`, `ZERO_INFLATED`, `N_TOO_SMALL`) en vez de omisión silenciosa |
| 8 | `compare_pairs(left, right, rule)` | dos vectores alineados + regla declarativa → conteo de igualdad, deltas | pares declarados en catálogo | regla = `eligibility AND comparison(tolerance) AND min_matches` — nunca solo `abs(a-b)<ε` |
| 9 | `check_invariant(frame, rule)` | frame + expresión (`when` opcional) → violaciones | cualquiera | evaluador restringido sobre columnas nombradas; soporta `sortino >= sharpe - tolerance WHEN excess_return > 0` |
| 10 | `check_bounds(vector, bound_spec)` | vector + bound → breaches tipados | métricas con bound declarado | `bound_type ∈ {HARD_INVARIANT, PLAUSIBILITY}`; carve-out por prefijo `crisis_` |
| 11 | `check_timeseries_integrity(series)` | serie por `(isin, metric, window, real_flag)` → duplicados, huecos, provenance | `fund_metric_timeseries` | huecos contra `expected_date_index` derivado del calendario real de origen — no `mes_siguiente == mes_anterior+1` |
| 12 | `reconcile_with_alerts(findings, alerts_df)` | hallazgos + `fund_metric_alerts` → hallazgos incrementales | P2 | D1–D3 (§2.5) corregidos en código 2026-09-13; **aún no implementada ni cableada** — la tabla en producción conserva filas antiguas hasta el próximo ciclo P2 real, así que reconciliar hoy sería contra datos obsoletos |
| 13 | `compare_runs(current_profile, previous_profile)` | dos perfiles almacenados → `Δn, Δcoverage, Δmedian, Δp95, Δsd, Δzero%, Δskew, Δkurtosis` | todas | implementada (`shared/statistical_audit/compare_runs.py`); cableada en el runner vía `--compare-to <run_id>`; el "octavo bloque" que faltaba en ambas skills |
| 14 | `assert_recompute_happened(isins, prior_state)` | ISINs + estado previo → booleano | P2 | codifica en función el Method Control #3 de la skill (`fund_metric_state.input_hash` debe cambiar) |
| 15 | `emit_statistics(facts)` / `emit_findings(evaluations)` | hechos / evaluaciones de regla → filas en BD | todas | separa **hecho estadístico** de **evaluación de regla** — ver §6 |
| 16 | `preserve_and_write(isin, column, old, new, reason, evidence)` | valores → escritura + preservación | solo coste | **resuelve la brecha J de §7** — hoy `fund_cost_corrections` no tiene ningún escritor en código |

---

## §5. Catálogos declarativos

Cuatro catálogos Python (diccionarios, no YAML — ver §7, hallazgo N), mismo idioma que
`ALERT_RULES` en `shared/config.py:370` y el registro `GENERATORS` de
`scripts/audit/sync_agents_md.py`:

- **`catalog_metrics.py`** — una entrada por métrica P2: familia, tipo estadístico
  (continua-positiva / continua-con-signo / acotada-[0,1] / discreta-ordinal / conteo / serie
  temporal), qué perfiles aplican (§4 #3–7), segmentaciones (`GLOBAL`, `PEER` por `Fund_Nature`
  reutilizando `compute_category_snapshot`), `min_n`, épsilon de `CV`.
- **`catalog_cost_columns.py`** — una entrada por columna de coste: **escala y unidad** (ratio
  decimal vs porcentaje entero — el "trampa de escala" citado en la skill de coste), bounds con
  `bound_type`. Es el artefacto de mayor valor de esta fase: hoy la escala vive dispersa en un
  docstring (`cost_scale.py`), una constante (`OC_RATIO_MAX`), un diccionario **local a una
  función** (`_COST_PCT_LIMITS`, `pipeline.py:2972`), los `CHECK` de la DDL, y una tabla de
  `SCHEMA_REFERENCE.md` que los contradice (línea 126, "TER en %" vs convención ratio de
  `cost_scale.py`). Este catálogo se convierte en el import canónico único (P#11/R-1); los
  cuatro sitios anteriores se apuntan a él, no se duplican.
- **`catalog_pairs.py`** — pares de Bloque 2 (ambos dominios): `pair, eligibility, comparison,
  tolerance, min_matches, severity, diagnosis`.
- **`catalog_invariants.py`** — invariantes de Bloque 5 (ambos dominios), con la corrección de
  §2.4 aplicada: `expression`, `when` opcional, `bound_type`.

---

## §6. Modelo de hallazgos

`fund_data_quality_issues` **no sirve** como destino: `UNIQUE(ISIN, check_code)` permite una sola
fila por código y fondo, la tabla se reconstruye por DELETE+INSERT en cada ciclo de pipeline
(`pipeline.py:516`), y está indexada por ISIN mientras la mayoría de hallazgos de Bloque 1/3 son a
nivel de población, no de fondo. Se proponen dos tablas nuevas, append-only, indexadas por
`run_id`, añadidas a `db/schema_fondos.sql`:

- **`audit_statistic`** — el **hecho** estadístico: población, segmento, claves de grupo, nombre
  del estadístico, valor, `n`. Ejemplo: `metric=vol_ann, horizon=since_inception, stat=skewness,
  value=37.06, n=2889`.
- **`audit_finding`** — la **evaluación de regla** sobre ese hecho: bloque, `rule_id`, clase
  (`HARD_INVARIANT`/`PLAUSIBILITY`/`STATISTICAL_ANOMALY`), severidad, valor, referencia, umbral,
  distancia, ISIN (nullable — NULL para hallazgos de población), evidencia, candidato a causa
  raíz.

Separar ambas tablas permite reevaluar umbrales sin recalcular distribuciones, y convierte
`compare_runs` (§4 #13) en una consulta sobre `audit_statistic`, no en un mecanismo aparte.

---

## §7. Correcciones a las especificaciones actuales de las dos skills

| # | Hallazgo | Skill afectada | Corrección exacta |
|---|---|---|---|
| A | `fund_metric_alerts.value` NULL en 100% de filas | P2 | **corregido 2026-09-13** en `rolling_stats.py` (§2.5); Bloque 4 sigue sin reconciliar contra la tabla en vivo hasta el próximo ciclo P2 real |
| B | `reference_value` NULL en exactamente las 612 filas ALARM | P2 | **corregido 2026-09-13** en `rolling_stats.py` (§2.5) |
| C | `VOL_CAT_P90`/`VOL_CAT_P97` nunca disparan (`rolling_6m` sin filas) | P2 | **corregido 2026-09-13**: `ALERT_RULES` retargeteado a `rolling_1y` (§2.5) |
| D | La skill P2 incluye `rolling_1m/3m/6m` en el alcance de `fund_metric_timeseries` | P2 §2 | corregir: esas ventanas cortas viven en `fund_metrics` (`metric_version='d1'`, ~22k filas cada una), no en la serie curada |
| E | "~16.6M filas" en `fund_metric_timeseries` | P2 §2, `AGENTS.md` | actualizar a **31.705.170** (verificado) |
| F | "Reutilizar `compute_alerts` ... de `shared/config.py`" | P2 §Bloque 4 | `compute_alerts` está en `rolling_stats.py:370`; solo `ALERT_RULES` vive en `config.py` |
| G | Cita `MIN_OBS_REGIME`, `_VOL_SANITY_CAP` como constantes importables | P2 | son literales locales de función, duplicados entre P2 y P3 — registrar como deuda P#11/R-1 antes de que el motor los importe; no asumir que existen en `shared/config.py` |
| H | Carve-out de crisis lista `crisis_2020` | P2 §Bloque 7 | `crisis_2020` no tiene filas en `fund_metrics`; el carve-out sigue siendo correcto por prefijo (config-driven), pero el ejemplo en prosa es engañoso |
| I | Cita `reference_cost_recompute_cached` como si fuera un documento del repo | Coste §6 | es un fichero de memoria de Claude, no un artefacto del repositorio — la skill no es autocontenida; sustituir por la instrucción explícita (`--recompute-costs`, cache-only, sin descargas) |
| J | `fund_cost_corrections` tiene 6.077 filas pero **cero escritores en código** | Coste §1, §6 | la contract de preservación es hoy solo una promesa de prompt; implementar `preserve_and_write` (§4 #16) como único punto de escritura antes de construir cualquier automatización sobre este dominio |
| K | Sin catálogo de escala/unidad legible por máquina | Coste §7, Bloque 7 | resuelto por `catalog_cost_columns.py` (§5) |
| L | Convenciones de nombre incompatibles ya presentes en `fund_cost_corrections.Column_Name` | Coste | verificado: coexisten `schedule.Total_Costs_EUR@1.0y` y `fund_cost_schedule.Total_Costs_Pct@h=1` en la misma columna — normalizar antes de que cualquier lector automatizado consuma la tabla |
| M | Dos invariantes de Bloque 5 no universales (`sortino≥sharpe`, `vol_ann>0 si return≠0`) | ambas | corregidos en §2.4; aplicar en `catalog_invariants.py` |
| N | Recomendación externa de usar YAML para los catálogos | — | rechazada: `environment.yml` no incluye PyYAML y el CI actual (`agents-sync.yml`) no instala ninguna dependencia; usar diccionarios Python, el idioma ya establecido por `ALERT_RULES` |

---

## §8. Fases de implementación

1. **Fase A — corrección de specs** ✅ 2026-09-13: aplicados A–I y N a los dos ficheros
   `.claude/skills/auditStatisticalDataDistribution*.md`.
2. **Fase B — catálogos + funciones puras** ✅ 2026-09-13 (§4 #1–11, §5): implementado en
   `shared/statistical_audit/`; 65 tests R-7 (frames sintéticos, sin import de
   `pipeline.py`/`core.io`) en `proyecto1/tests/test_statistical_audit_*.py`.
3. **Fase C — persistencia + runner** ✅ 2026-09-13: tablas `audit_statistic`/`audit_finding`
   creadas en `db/schema_fondos.sql` y aplicadas a la BD en producción; `fund_cost_corrections`
   promovida a schema canónico; `preserve_and_write` (§4 #16) implementado, cerrando la brecha J;
   runner `scripts/audit/run_statistical_audit.py --domain costs|p2 --mode report|check [--persist]
   [--compare-to <run_id>]`; launcher `scripts/launch/AUDIT_statistical.bat`. Primera ejecución
   real persistida: dominio coste 402 stats / 2.621 findings, dominio P2 12.251 stats / 48.922
   findings. Dos resultados requieren investigación antes de tratarse como defecto confirmado
   (Method Control "decidir qué operando está mal antes de corregir"): `VOL_ANN_EQUALS_SRRI_VOL`
   coincide en el 100% de filas elegibles (19.577/19.577); `ANNUAL_EQUALS_TOTAL_AT_1Y` viola en el
   87% (1.784/2.042) — una tasa así de alta en una regla que debería ser tautológica sugiere
   revisar primero la definición de la regla, no 1.784 fondos rotos.
4. **Fase D — comparación entre ejecuciones + corrección de raíz D1–D3** ✅ 2026-09-13:
   `compare_runs` (§4 #13) implementado en `shared/statistical_audit/compare_runs.py` y cableado
   en el runner vía `--compare-to`; verificado contra la BD real (0 deltas entre dos ejecuciones
   consecutivas sobre datos sin cambios — confirma que el motor es determinista). D1–D3 (§2.5)
   corregidos en `rolling_stats.py`/`shared/config.py`, con 5 tests de regresión en
   `proyecto2/tests/calculations/test_alert_engine_fix_20260913.py`. **Pendiente:** la tabla
   `fund_metric_alerts` en producción conserva las filas antiguas hasta el próximo ciclo P2 real;
   función #12 (`reconcile_with_alerts`) queda especificada pero no implementada, a la espera de
   ese recompute para tener una base fiable contra la que reconciliar.

**Principio que se mantiene íntegro de ambas skills originales:** el resultado estadístico es
evidencia, nunca autoriza por sí solo una corrección. La corrección se hace sobre el módulo de
cálculo o escritura, y se valida mediante recómputo reproducible (Method Control #3 de ambas
skills, codificado en §4 #14).
