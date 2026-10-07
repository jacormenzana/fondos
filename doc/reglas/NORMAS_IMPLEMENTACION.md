# NORMAS DE IMPLEMENTACIÓN — Proyecto 1

**Propósito:** Estándares operativos de implementación y tiempo de ejecución para los módulos de P1.  
Responde a «¿cómo desarrollar, probar y registrar?».  
**Dominio:** Proceso previo · Logging · Convenciones DQ · Guía de clasificadores · Catálogo de regresiones  
**Lectura obligatoria** junto con `RESTRICCIONES_ARQUITECTURA.md` antes de modificar código P1.

---

## §1. Proceso previo a la codificación

Para cualquier BL que implique modificación de un atributo persistido o regla INTER,
**antes de escribir código** verificar:

- [ ] ¿Cuál es la distribución actual del atributo en BD? (query SQL + `value_counts`)
- [ ] ¿Cuántos fondos están afectados por el defecto? (query SQL exacta)
- [ ] ¿Cuál es la causa raíz, no el síntoma? (Principio #2 — `PRINCIPIOS_DISENO.md`)
- [ ] ¿Qué módulos emiten ese atributo? (`grep` en todos los `.py`)
- [ ] ¿Hay COALESCE sobre ese atributo en `fund_writer`?

Si alguna respuesta es "no sé", completar el diagnóstico antes de codificar.

---

## §2. Minimización de cambios

Preferir parches quirúrgicos (`str_replace`) sobre regeneración completa de archivos.
Cada cambio debe:

- Modificar las mínimas líneas posibles.
- Incluir referencia al BL-XX en el comentario del cambio.
- Preservar el resto del módulo intacto.

**Razón:** la regeneración completa es la principal causa de pérdida de imports auxiliares,
renumeración de líneas que rompe documentación, reescritura de regex con escape errors, y
tokens consumidos innecesariamente.

---

## §3. Guía de implementación: clasificadores e INTER rules

### §3.1 Para funciones `classify_fund` en cada bloque

1. Al asignar `Investment_Focus='Sector'`, establecer simultáneamente `Sector_Focus` usando
   la tabla de mapeo canónica en `MODELO_SEMANTICO.md` §5.
2. Al asignar `Investment_Focus='Thematic'`, no establecer `Sector_Focus`.
3. Cuando `Theme` es `'Inflation'` o `'Megatrends'`, usar siempre `Investment_Focus='Thematic'`.
4. Cuando `Family='Thematic Equity'`, nunca usar `Investment_Focus='Broad'`.

### §3.2 Para reglas INTER en `validate_all_semantic_consistency` (`classify_utils.py`)

- Reglas **auto-corregibles** (SC-B1, SC-B2, SC-B5, SC-B6, SC-D1, SC-D2, SC-E1, SC-F1, SC-F2):
  implementar como INTER rules que pueblan `corrected_record`.
- Reglas **no auto-corregibles** (SC-A1, SC-B3, SC-B4, SC-E2, SC-E3, SC-F3, SC-F4): emitir
  entrada DQ WARN sin modificar el registro.
- Prioridad de implementación: SC-B1/B2 (Inflation/Megatrends) → SC-B5 (Sector_Focus nulo en
  Thematic) → SC-B6 (Theme↔Sector_Focus alignment) → SC-C1/C2.

### §3.3 Qué NO hacer

No añadir reglas INTER ad-hoc para casos individuales de fondos. Cada regla debe derivarse del
modelo semántico (`MODELO_SEMANTICO.md`) y aplicarse genéricamente a todos los fondos.
Una regla que solo dispara para 1 fondo es o bien un bug de clasificador (corregir en el
clasificador) o un caso de wrong-KIID (corregir via `FORCE_REFRESH`).

---

## §4. Normativa de logging (v2 — vigente desde 30-abr-2026)

Esta sección surge del ciclo del 30-abr-2026, donde los warnings añadidos visibilizaron tres
bugs ocultos (Theme='Inflación', logging duplicado, regresión Fund_Nature=NULL) que hubieran
contaminado P3 silenciosamente.

### §4.1 Criterios obligatorios de emisión de log

Todo evento que cumpla CUALQUIERA de los siguientes criterios DEBE emitir log:

a) Una regla INTER detecta inconsistencia y aplica autocorrección.  
b) Una regla INTER detecta inconsistencia y NO puede corregir (residual con DQ=WARN).  
c) Un valor cae en autocorrección por defecto (fallback heurístico).  
d) Una decisión de clasificación se toma con confianza < umbral (≤3 atributos).  
e) Una validación de catálogo (`ALLOWED_VALUES_BY_COLUMN`) falla.  
f) Un atributo NOT NULL del schema recibe valor None tras procesamiento.  
g) Una operación de extracción (parser) devuelve None donde se esperaba valor.  
h) Un bloque clasificador retorna sin asignar Fund_Nature, Profile, Type, Strategy o Family.  
i) El UPSERT usa COALESCE preservando valor BD distinto al record entrante (cambio silente).

### §4.2 Niveles de severidad — convención obligatoria

| Nivel   | Cuándo se emite                                       | Acción del pipeline                                  |
|---------|-------------------------------------------------------|------------------------------------------------------|
| ERROR   | Datos críticos faltantes; el fondo no se persiste     | Logear; fondo queda con su estado anterior en BD     |
| WARNING | Inconsistencia detectada Y autocorregida; o residual  | Logear; persistir con valor corregido o flag DQ=WARN |
| INFO    | Inferencia exitosa por fallback; trazabilidad         | Logear; persistir con valor inferido                 |
| DEBUG   | Diagnóstico interno, no visible en producción         | Configurable por nivel                               |

**Criterio rápido:**
- ¿El fondo se persiste con datos coherentes? → WARNING (más DQ=WARN si aplica).
- ¿El fondo NO puede persistirse? → ERROR.
- ¿Incidencia informativa (fallback exitoso)? → INFO.

**Token canónico en `ingestion_log.status` (2026-07-19):**  
El valor de la columna `status` en `ingestion_log` para severidad WARNING es **`"WARN"`** (4 letras),
no `"WARNING"`. El RESUMEN al final de cada ciclo agrupa por `status` — emitir `"WARNING"` en lugar
de `"WARN"` produce un cubo independiente y rompe la agregación. Los tres sitios en `pipeline.py`
y `classify_utils.py` que emitían `"WARNING"` fueron normalizados a `"WARN"` en la sesión 19
(FIX-LOG-WARN-NORM, 2026-07-19). La tabla `fund_data_quality_issues.level` sigue usando `"WARN"` sin cambio.

### §4.3 Convención de tags

**Formato obligatorio** para reglas INTER documentadas en backlog:

```
[BL-XX] ISIN mensaje
```

**Ejemplos válidos:**
```
[BL-44] LU1133289592 Nature_efectivo=Monetario incompatible con SRRI_efectivo=3 → Restantes
[BL-62] LU0907915168 Family=Mixtos Type=Allocation inferidos léxicamente tras BL-44 → Restantes
[BL-44] LU0907915598 sin patrón léxico identificable; Family/Type=NULL; Data_Quality_Flag=WARN
```

**Para fallbacks sin BL específico:** prefijo descriptivo entre corchetes.
```
[NORM-Profile-SRRI] LU0907915168 Profile=Conservador SRRI=5 → Dinámico
[NORM-Theme-Default] LU0123456789 Theme no detectado en KIID → Core/General
```

**Para errores estructurales:** prefijo ERROR-CATEGORÍA.
```
[ERROR-NotNull] LU0171298564 Fund_Nature=None tras BL-62 fallback → INSERT rechazado
[ERROR-Persistence] LU2267099674 UPSERT failed: foreign key constraint
```

Tags sin guion (`[BL44]`, `[BL62]`) están desestimados.

### §4.4 Reglas anti-duplicación

**a)** Las funciones de validación master DEBEN ser puras (sin logging interno); el logging
   vive exclusivamente en el wrapper que las invoca.  
**b)** Si una regla puede dispararse desde múltiples puntos del pipeline, DEBE invocarse
   desde un único punto canónico (R-1 en `RESTRICCIONES_ARQUITECTURA.md`).  
**c)** Cualquier evento debe loguearse exactamente una vez por incidencia.  
**d)** Si una función puede invocarse desde múltiples wrappers que también logueen,
   ELLA debe ser pura; los wrappers son los responsables.

### §4.5 Resumen de ciclo obligatorio

Al final de cada ciclo, el pipeline DEBE emitir un resumen agregado por tag:

```
--- RESUMEN DE INCIDENCIAS DEL CICLO ---
[WARN] BL44_NATURE_SRRI_R4: N fondos
[INFO] BL47_SFDR_DEFAULT: N fondos
...
---
```

### §4.6 Métricas de monitorización (control SQL)

Cada regla INTER documentada en backlog DEBE tener:

a) **Control SQL "antes del fix"** — qué retorna en estado defectuoso.  
b) **Control SQL "después del fix esperado"** — qué retorna tras corrección.  
c) **Tag de log distintivo** para cuantificar disparos por ciclo.

Sin estos tres elementos, no se debe abrir un BL en backlog.

### §4.7 Cobertura mínima por módulo

| Módulo                    | Log mínimo obligatorio                                              |
|---------------------------|---------------------------------------------------------------------|
| `pipeline.py`             | Inicio/fin de bloque, BL disparos universales, resumen de ciclo     |
| `classify_utils.py`       | `apply_semantic_validation` (warnings), inferencias por fallback    |
| `fund_writer.py`        | UPSERT con flags forzados, normalizaciones EN→ES aplicadas          |
| `kiid_parser.py`          | Atributos NO detectados con patrones esperados (señal de regresión) |
| `fund_characterizer.py`   | Atributos enriquecidos por fallback (no por extracción directa)     |
| `benchmark_normalizer.py` | Benchmarks no reconocidos (señal de catálogo obsoleto)              |
| `fund_family_builder.py`  | Familias inconsistentes (Nature mixta), correcciones aplicadas      |
| `srri_v4_geometric.py`    | Detecciones VISUAL_ONLY donde el textual también está poblado       |
| `blocks/*.py`             | Clasificaciones con SRRI=None (Capa 3 fallback no funcional)        |
| `blocks/restantes.py`     | Detección por capa (cuál de las 3 disparó); confianza baja          |

### §4.8 Implementación incremental — orden de despliegue

**Ola 1** (Sprint A.1.b — completado): `pipeline.py`, `classify_utils.py`, `fund_writer.py`, `restantes.py`.  
**Ola 2** (Sprint A.2): `monetarios.py`, `rf_corto.py`, `rf_flexible.py`, `renta_variable.py`, `mixtos.py`, `alternativos.py`.  
**Ola 3** (Sprint A.3): `kiid_parser.py`, `benchmark_normalizer.py`, `srri_v4_geometric.py`, `fund_characterizer.py`.

---

## §5. Convenciones de codificación DQ (`check_code`)

Los códigos en `fund_data_quality_issues.check_code` siguen estas convenciones.

### §5.1 Prefijos estándar

| Prefijo | Origen | Ejemplos |
|---------|--------|---------|
| `SEM_` | Reglas semánticas (`validate_all_semantic_consistency`) | `SEM_MMFSTRUCTURE_NATURE`, `SEM_STYLEPROFILE_NATURE` |
| `BL` | Reglas de bloque específicas del pipeline | `BL44_SRRI_ANOMALY`, `BL47_SFDR_DEFAULT` |
| `KIID_` | Issues del documento KIID | `KIID_WRONG_DOC`, `KIID_WRONG_DOC_RETRY` |
| `FUNDCCY_` | Issues de divisa del fondo | `FUNDCCY_NAME_KIID_MISMATCH` |

### §5.2 Generación de `check_code` desde nombre de regla

La función canónica `_rule_to_code(rule: str) -> str` en `classify_utils.py`:

```python
def _rule_to_code(rule: str) -> str:
    return "SEM_" + rule.upper().replace("-","_").replace(":","_").replace(" ","_")[:50]
```

Ejemplos: `"MMFStructure-Nature"` → `"SEM_MMFSTRUCTURE_NATURE"`;
`"StyleProfile-Nature"` → `"SEM_STYLEPROFILE_NATURE"`.

### §5.3 Formato de 4-tupla para `_dq_issues`

El helper `semantic_validation_to_dq_tuples(result: dict) -> list[tuple]` en `classify_utils.py`
convierte el resultado de `validate_all_semantic_consistency` a la lista de 4-tuplas
`(check_code, dq_level, log_status, message)` que consume `_finalize_data_quality_issues` en
`pipeline.py`:

- `critical_errors[i]` → `("SEM_<RULE>", "WARN", "WARNING", msg)`
- `warnings[i]`        → `("SEM_<RULE>", "INFO", "INFO", msg)`

---

## §5b. Modelo operativo P2: run rutinario vs. backfill (v26)

El pipeline P2 opera en dos modos distintos que deben mantenerse separados.

### Modo rutinario (mensual)

Condición: ningún `--force` y `CALC_VERSION` sin cambios respecto a las filas ya almacenadas en `fund_metrics`.

- El mecanismo de fingerprint (`fund_metric_state.input_hash`) salta automáticamente los fondos sin cambios en NAV/IPC.
- Solo se reescriben las filas Gold de fondos con datos nuevos.
- No se emite marcador especial en `p2_pipeline_log`.

### Modo backfill (recalculo completo)

Condición: se cumple **cualquiera** de los dos criterios siguientes.

| Criterio | Causa habitual |
|----------|---------------|
| Flag `--force` en la invocación | Corrección de datos en fuente (NAV, macro), debugging |
| `CALC_VERSION` en código ≠ `algorithm_version` en la última fila de `fund_metrics` | Cambio de lógica de cálculo (bump de `CALC_VERSION`) |

**Al detectarse un backfill:**
1. Se escribe una fila `BACKFILL_START` en `p2_pipeline_log` con el motivo y el `batch_id` del run.
2. El pipeline ejecuta el recalculo completo ignorando la caché de fingerprint.
3. Al finalizar, se escribe una fila `BACKFILL_END` con el recuento de fondos recomputed.

**Regla de governance:** los backfills deben ser eventos controlados y atribuibles. Nunca lanzar `--force` en producción sin registrar el motivo en el historial de cambios (commit message o backlog). Un `CALC_VERSION` bump sin un commit que lo documente es un error de proceso.

**Versiones por familia (FND-0236, `FAMILY_VERSIONING_ENABLED`):** `CALC_VERSION` es la época *global* (cambio que afecta a todas las familias). Un cambio que afecta a una sola de las nueve familias de métricas (`risk, macro, momentum, capture, persistence, fx, regime, rolling, short`) se registra en `FAMILY_CALC_OVERRIDES` (`proyecto2/src/utils/family_versions.py`) y **no** incrementa `CALC_VERSION`: solo esa familia se recalcula. El token de una familia es `CALC_VERSION` (+ `.override` si lo tiene) y es el `algorithm_version` de sus filas; las puertas que comparan versiones (p. ej. `beta_shift_audit.expected_version`) esperan el token de la familia, no el `CALC_VERSION` desnudo. Un flag del bundle P2 pertenece a una familia o a varias (`FLAG_FAMILIES`); un test exige que todo flag de `P2_BUNDLE_FLAGS` esté mapeado. Con el interruptor apagado el comportamiento es el de siempre.

**Convención de anualización (FND-0240, `ANNUALIZATION_INTERVAL_ENABLED`, apagado):** `shared/annualization.years_spanned` es la única definición de "años que abarca una serie de N observaciones": N/12 (convención almacenada: cuenta puntos NAV) o (N−1)/12 con el interruptor (cuenta intervalos, como ya hace `annualized_return_from_returns`). La usan `returns.annualized_return`, `rolling_stats._roll_return_ann` y los espejos de la auditoría (`build_window_deflation_frame`, `scalar_window_cpi`), de modo que productor y auditoría no pueden discrepar. Afecta a las familias `risk` y `rolling` (mapeo en `FLAG_FAMILIES`): activarlo recalcula solo esas dos y **solo debe activarse junto con ese recálculo**, porque hasta entonces los valores almacenados siguen la convención antigua y las identidades nominal/Fisher de la auditoría reportarían la diferencia. Impacto medido el 2026-10-06: +0,5 pp en `rolling_1y`, −1,2 pp en las ventanas de crisis, +0,04 pp desde el inicio y ningún cambio en la cartera P3.

**Alineación mensual del IPC (FND-0241, `DEFLATION_MONTH_ALIGN_ENABLED`, apagado):** el IPC ES se fecha en el fin de mes natural (`load_ipc`) y un NAV mensual en el último día hábil; `deflate_nav` busca con `merge_asof(backward)`, así que el 29 % de los NAV (fin de mes en sábado/domingo) tomaba el IPC del mes **anterior** y su retorno real mensual era idéntico al nominal. `shared/deflation_alignment.cpi_lookup_dates` es la única definición: con el interruptor, cada NAV mensual busca el IPC de su propio fin de mes. La usan `deflate_nav` (consistency, risk_metrics, rolling_stats) y el espejo de la auditoría (`timeseries.nav_with_ipc`). Las series **diarias** (`short_horizon`) pasan `align_month_end=False`: alinear daría a cada día el IPC de su mes. Afecta a `risk` y `rolling` (`FLAG_FAMILIES`) y **solo debe activarse junto con ese recálculo**. Impacto medido el 2026-10-07 sobre 2.866 fondos: Spearman ≥ 0,999 en `return_ann`/vol/max_dd/worst_month reales, solapamiento del decil superior 99,7 % (retorno) y 97,6 % (max_dd); `worst_month` real cambia hasta 2,2 pp.

**Mínimo de observaciones a la baja del Sortino (FND-0242, `SORTINO_MIN_DOWNSIDE_COUNT_ENABLED`, apagado):** la desviación a la baja es la raíz de la **media** de k shortfalls al cuadrado (los demás meses aportan 0), con error estándar relativo ≈ 0,7/√k (35 % con k = 4, 25 % con k = 8). La guarda de magnitud `returns._MIN_DOWNSIDE_DEV_ANN` (FND-0075) es un acantilado: un fondo con 4 meses a la baja y desviación 0,00101 conservaba un Sortino de 12,95 mientras sus clases hermanas (0,0009) daban NaN, y ese único valor llevaba la curtosis de `PEER:Monetario` de 0,8 a 36,8. Con el interruptor, `shared/sortino_reliability.downside_count_reliable` exige `k >= max(SORTINO_MIN_DOWNSIDE_OBS=3, ceil(SORTINO_MIN_DOWNSIDE_SHARE=0,15 · n))` (escalado a la ventana: un k fijo ≥ 5 eliminaría el 34 % de `rolling_1y`). Medido el 2026-10-07 sobre las 22.311 ventanas con Sortino: elimina el 0,7 % (2,2 % de `rolling_1y`, 11 % de Monetario) y los eliminados tienen |Sortino| mediano 4,3 frente a 0,8. Solo en la ruta escalar (`sortino_ratio`) y la rolling (`_roll_sortino`); la ruta por régimen (`sortino_ratio_from_returns`, con su propio mínimo local `REGIME_MIN_OBS_SORTINO_DOWNSIDE`) no cambia porque P3 lee sus percentiles. Afecta a `risk` y `rolling`; **solo activar junto con el recálculo**.

**Trazabilidad:** tras un backfill, todas las filas de `fund_metrics` reescritas llevan el nuevo `algorithm_version` (= `CALC_VERSION` actualizado) y el `batch_id` del run de backfill. Las filas de `fund_metric_timeseries` conservan el `algorithm_version` y `batch_id` del run que las insertó originalmente (`INSERT OR IGNORE`), preservando la procedencia histórica.

---

## §6. Smoke test y catálogo de regresiones

### §6.1 Smoke test post-implementación

Antes de declarar un BL completado, ejecutar smoke test sobre 5–10 ISINs canónicos del defecto.
Si el ciclo regular no se puede ejecutar, simular el flujo en SQLite en memoria con datos
sintéticos (como se hizo en BL-53/54 fix arquitectónico).

El smoke test detecta el defecto COALESCE en menos de 30 segundos; ejecutarlo evita ciclos
completos infructuosos.

### §6.2 Coordinación LLM Opus / Sonnet

Si la sesión usa flujo Opus → Sonnet:

**Opus** (planificador) entrega un plan con:
- BL afectados con prioridad.
- Para cada BL: causa raíz, especificación de código (no solo descriptiva), módulo y línea aproximada.
- Lista de tests funcionales a producir.
- Restricciones aplicables citadas por número (R-X de `RESTRICCIONES_ARQUITECTURA.md`).
- Plan de migración SQL si aplica (R-2).

**Sonnet** (codificador) recibe el plan + documentos relevantes y debe:
- Leer `RESTRICCIONES_ARQUITECTURA.md` antes de tocar código.
- Reportar tensiones con restricciones antes de codificar.
- Validar AST tras cada edit (R-8).
- Producir tests (R-7).

### §6.3 Catálogo de regresiones históricas

| Ciclo | BL | Síntoma | Causa raíz | Restricción |
|-------|----|---------| -----------|-------------|
| 23/04 | BL-49 v1 | Currency_Hedged NULL persistente (728 fondos) | `detect_currency_hedged` no leía KIID | R-3 |
| 23/04 | BL-50 | Universe poblado, Geography NULL (110) | Sin inferencia direccional Universe→Geography | — |
| 23/04 | BL-52 | Universe='Country' con Geography=región (12) | Sin auto-corrección Country↔Regional | — |
| 23/04 | BL-53 | Sector_Focus en inglés (20) | Mapa Theme→Sector duplicado en 2 módulos | R-1 |
| 25/04 | BL-49 v2 | 7 fondos Hedged → Unhedged | (a) `_HEDGED` sin variantes EURH; (b) `\b` falla en EURHDG; (c) default sin `_ch_bd` | R-4, R-5 |
| 25/04 | BL-53/54 v2 | 20 fondos siguen en inglés tras Sonnet | COALESCE preserva valor stale; Sonnet añadió mapa duplicado en fund_writer | R-1, R-2 |
| 25/04 | BL-55 v1 | Inferencia Exit_Fee=0 captura solo 3/670 | Ventana global, no acotada al contexto | R-6 |

---

## §7. Guardas estáticas de dialecto (SQLite → Postgres) y protocolo de allowlists

Origen: el 2026-09-26 `P1_P2_Complete.bat` abortó en el primer ciclo posterior al cutover porque
`benchmark_loader.run_gap_analysis()` seleccionaba una columna no agregada con `GROUP BY` (SQLite lo
tolera, Postgres lanza `GroupingError`). Estaba en un tramo diagnóstico que solo se ejecuta tras una
carga real, así que ningún ensayo lo había ejecutado nunca. Un regex no puede decidir si SQL es
válido; el parser de Postgres sí, sin ejecutar nada (`EXPLAIN` planifica, no corre).

| Guarda | Fichero | Qué impide |
|--------|---------|-----------|
| Barrido EXPLAIN | `tests/test_sql_explain_sweep_pg.py` (+ `tests/_sql_sites.py`) | Toda sentencia que el código de producción ejecuta y que resuelve estáticamente se EXPLAINea contra el DDL real de `db/pg/`. Detecta `GroupingError`, función/columna/tabla inexistente, sintaxis. Las sentencias no resolubles (listas de columnas dinámicas) se fijan en un baseline de conjunto exacto y se EXPLAINean parcialmente con el fragmento dinámico sustituido por `NULL`. |
| SQL solo-SQLite acotado | ídem | `PRAGMA`, `INSERT OR REPLACE`, `datetime('now')`, `julianday`… solo dentro de funciones que bifurcan por dialecto. |
| INSERT idempotente | ídem | Todo `INSERT` en la ruta Postgres lleva `ON CONFLICT`, o es `DELETE`+`INSERT` de la misma tabla en la misma función, o su tabla figura como append-only. Es lo que hace seguro `P1_P2_Complete.bat --from N` (re-ejecuta el paso N completo). |
| `with conn:` | `tests/test_dialect_coverage.py` | En psycopg3 `with conn:` hace commit **y cierra** la conexión; usar `shared.db.db_transaction(conn)`. |
| Error SQL tragado | ídem | Un `except Exception` que traga una sentencia fallida sin `raise` ni `rollback()` deja abortada toda la transacción Postgres (`InFailedSqlTransaction` en cada sentencia posterior, lejos de la causa). Usar `fail_soft_block(conn)`, `execute_fail_soft()` o `conn.rollback()`. |

**Protocolo de allowlists** (`tests/_allowlist.py`). Cada entrada es `{clave: motivo}` y el motivo debe
citar un ticket del backlog (`FND-0123 …`) o empezar por `DESIGN:` seguido de una explicación real
(≥ 20 caracteres). Reglas: (1) una clave obsoleta (su destino ya no existe) **hace fallar** la suite;
(2) el mensaje de fallo imprime la línea exacta a añadir; (3) la evidencia de una exención va en el
mensaje de commit y se señala al operador para su revisión; (4) solo es exento un manejador cuya
conexión no puede ser Postgres (p. ej. una rama SQLite), nunca porque «normalmente funciona»;
(5) un hallazgo real se corrige en su módulo (P#7), no se exime. Añadir una exención es una decisión,
no un hábito.

**Reglas de precisión al escribir SQL portable:** `MIN()`/`MAX()` (o incluir la columna en el
`GROUP BY`) para toda columna no agregada; placeholders `%s` con la variante SQLite bajo
`is_postgres_connection(conn)`; el nombre de columna `window` es `window_label` en Postgres.
`get_connection()` registra un loader `numeric → float` para que `AVG()`/`SUM()` devuelvan `float`
como SQLite (el DDL vivo no tiene columnas NUMERIC).

---

**FIN NORMAS DE IMPLEMENTACIÓN**

*Creado 2026-07-12. Absorbe: §3 (P-1/P-4) de `RESTRICCIONES_ARQUITECTURA.md` v1.0,*
*§7 de `PRINCIPIOS_DISENO.md` (v2 logging), §6 de `SEMANTIC_MODEL_CLASSIFICATION.md`,*
*§5 de `RESTRICCIONES_ARQUITECTURA.md`.*
