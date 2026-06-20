# Estado del Backlog P1 — Referencia v3 (post-ciclo v20 verificado)

**Fecha:** 18-abr-2026
**Export de referencia:** `p1_export_20260418.xlsx` (3.204 fondos, ciclo 2026-04-18T05:35–05:46)
**Pipeline log:** `log_pipeline_20260418_073414.log`
**Versiones desplegadas:**

- `pipeline.py` v20 → entregar **v21** (2 fixes finales: Geography lookup BD + UPDATE directo bench)
- `kiid_parser.py` v20 (desplegado y verificado)
- `sqlite_writer.py` (existente, con política COALESCE documentada)
- `classify_utils.py` v4, `restantes.py` v4
- `fund_characterizer.py`, `benchmark_normalizer.py` (verificados en producción)

---

## 1. RESUMEN: efectos reales del ciclo v20

Verificado comparando `p1_export_20260416` (pre-v20) vs `p1_export_20260418` (post-v20):

| Métrica | Pre-v20 (16-abr) | Post-v20 (18-abr) | Δ |
|---------|------------------|-------------------|---|
| BL-30 (Broad+Sector) | 97 | **0** ✅ | -97 |
| BL-31 (Hedged+UNHEDGED) | 57 | **0** ✅ | -57 |
| BL-38 contaminados | 18 | **11** | -7 (residuales "sofr) además") |
| BL-37 OC NULL | 275 | 275 | 0 (límite teórico) |
| BL-35 Entry NOT_FOUND | 870 | 870 | 0 (límite teórico) |
| BL-27 Market_Cap_Focus RV | 433 | 433 | 0 |
| Currency_Hedged NULL | 2.565 | **1.195** ✅ | **-1.370** |
| Currency_Hedged Unhedged | 8 | **1.524** ✅ | **+1.516** |
| Accumulation NULL | 786 | **594** ✅ | **-192** |
| Accumulation ACCUMULATION | 1.906 | **2.133** ✅ | **+227** |
| Geography NULL | 424 | **412** ✅ | **-12** |
| SFDR Art.9 | 242 | **212** ✅ | **-30** (corregido Franklin) |
| SFDR Art.6 | 0 | **30** ✅ | **+30** |
| Subtype, Sector_Focus, MCF, Style_Profile, Hedging, Entry_Fee, Exit_Fee, Benchmark, SFDR NULL | — | — | 0 (**límite teórico**) |

**Los fixes v20 funcionaron correctamente**. Los atributos que no cambiaron tienen una explicación precisa (ver sección 3).

---

## 2. DIAGNÓSTICO DE LOS 3 GRUPOS DE ATRIBUTOS

### Grupo A — LÍMITE TEÓRICO ALCANZADO (no hay fix posible)

Atributos donde el parser v20 **ya extrajo todo lo extraíble** del KIID. Validado ejecutando `_detect_*` sobre los fondos NULL restantes: el parser devuelve **0 nuevas detecciones**.

| Atributo | NULL actual | % NULL | Razón |
|----------|-------------|--------|-------|
| Subtype | 3.021 | 94.3% | Solo aplica a Alternativo/RF específico (por diseño) |
| Sector_Focus | 2.830 | 88.3% | Solo sectoriales (374 con valor es el universo real) |
| Market_Cap_Focus | 2.738 | 85.5% | Solo RV con señal explícita. 433 RV poblados ≈ techo |
| Hedging_Policy | 2.309 | 72.1% | Solo 488 UNHEDGED + 407 HEDGED tienen señal en KIID |
| Style_Profile | 2.210 | 69.0% | Análisis de sesiones: límite ≈ +61 (5% adicional, baja prioridad) |
| Benchmark_Declared | 1.344 | 42.0% | Fondos sin benchmark detectable en KIID |
| SFDR_Article | 1.217 | 38.0% | 49% sin mención SFDR, 51% mención indirecta no extractable |
| Entry_Fee NOT_FOUND | 870 | 27.2% | Verificado: parser v20 sobre los 870 devuelve 0 nuevas detecciones |
| Exit_Fee NULL | 742 | 23.2% | Mismo análisis |
| Ongoing_Charge NULL | 275 | 8.6% | Verificado: parser v20 sobre los 275 devuelve 0 nuevas detecciones |

**Acción: ninguna.** Estos son los techos reales del sistema. Requerirían fuentes externas (Morningstar, Bloomberg) no KIID.

---

### Grupo B — BUG ARQUITECTÓNICO: COALESCE bloquea limpieza

**11 fondos con `Benchmark_Declared="sofr), además"`** persisten tras v20.

**Flujo exacto**:
1. El parser v20 detecta contaminación en el KIID actual, devuelve `None`.
2. El pipeline v20 asigna `fund_master_record["Benchmark_Declared"] = None`.
3. El UPSERT en `sqlite_writer` ejecuta:
   ```sql
   Benchmark_Declared = COALESCE(excluded.Benchmark_Declared, fund_master.Benchmark_Declared)
   ```
4. `COALESCE(NULL, 'sofr), además')` → `'sofr), además'`. **El valor antiguo se preserva.**

**Fix en v21**: UPDATE SQL directo bypass-COALESCE antes de `publish_fund`:

```python
if _is_contaminated:
    conn.execute("UPDATE fund_master SET Benchmark_Declared=NULL WHERE ISIN=?", (isin,))
    log_ingestion(conn, isin, "BENCHMARK_CLEANUP_V21", "INFO", f"UPDATE directo: {_bench_bd_val[:60]!r}")
```

**Impacto esperado:** BL-38 11 → 0.

---

### Grupo C — FIX INCOMPLETO: Geography Regla 1 no lee BD

**35 fondos Alternativos/Mixtos con `Investment_Universe='Global'` en BD y `Geography=NULL`** no se capturaron por la Regla 1.

**Causa raíz**: el pipeline v20 lee `fund_master_record.get("Investment_Universe")` que puede ser `None` en fondos CACHED (el classifier no lo re-calcula). Como BD preserva el valor `"Global"` vía COALESCE, el dato existe pero el pipeline no lo ve.

**Fix en v21**: fallback a BD cuando el dict tiene Investment_Universe=None:

```python
_universe = fund_master_record.get("Investment_Universe")
if not _universe:
    _univ_bd = conn.execute(
        "SELECT Investment_Universe FROM fund_master WHERE ISIN=?", (isin,)
    ).fetchone()
    if _univ_bd and _univ_bd[0]:
        _universe = _univ_bd[0]
```

**Impacto esperado:** Geography NULL 412 → 377 (-35).

---

## 3. PRINCIPIO #10 (arquitectónico, CONFIRMADO por el ciclo)

La experiencia del ciclo v20 confirma el principio anticipado:

> **Toda corrección INTER que lea valores de `fund_master_record` debe tener fallback a BD vía `SELECT` cuando el valor del dict sea `None`.**
>
> Motivo: los fondos CACHED no re-calculan todos los atributos en cada ciclo. `sqlite_writer` preserva los valores antiguos vía COALESCE, pero el `fund_master_record` del ciclo solo tiene los valores recién calculados (que pueden ser `None`).

**Casos que ya aplican este principio correctamente en v20+v21:**
- BL-30/31: lookup BD de `Sector_Focus`, `Currency_Hedged`, `Hedging_Policy`, `Investment_Focus`
- Geography Regla 1 (v21): lookup BD de `Investment_Universe`
- BL-38 v20: lookup BD de `Benchmark_Declared` contaminado

**Casos que NO requieren BD lookup (el dict siempre tiene valor fresco):**
- Fund_Currency, Benchmark_Declared (recalculado cada ciclo por parser)
- Currency_Hedged en bloque Unhedged default (pipeline lo asigna directamente)

---

## 4. LÍMITES DE SQLITE_WRITER — política de escritura

Extraído de `sqlite_writer.py` (UPSERT en `upsert_fund_master`):

### 4.1 Asignación directa (sobrescribe siempre, incluso con NULL)
`Fund_Name`, `Management_Company`, `Fund_Nature`, `Profile`, `Type`, `Strategy`, `Family`, `Style_Profile`, `Geography`, `Theme`, `Is_ESG`, `Exposure_Bias`, `Benchmark_Type`, `Subtype`, `Heuristic_Block`, `Heuristic_Core`

**Implicación**: si el ciclo devuelve `None` para cualquiera de estos, **BORRA** el valor anterior. El classifier debe ser determinista para no perder información.

### 4.2 COALESCE (preserva valor antiguo si nuevo es NULL)
- **v3/v17**: `Market_Cap_Focus`, `Sector_Focus`, `Currency_Hedged`, `Investment_Universe`, `Investment_Focus`, `Credit_Quality`, `Accumulation_Policy`, `Fee_Known_Flag`
- **Texto**: `Fund_Currency`, `Portfolio_Currency`, `Hedging_Policy`, `Replication_Method`, `Derivatives_Usage`, `Benchmark_Declared`, `Ongoing_Charge`, `Entry_Fee_Pct`, `Exit_Fee_Pct`, `Sfdr_Article`, `Recommended_Holding_Period`, `Leverage_Used`, `Liquidity_Profile`, `Distribution_Frequency`

### 4.3 CASE especial
- `SRRI`: solo actualiza si `SRRI_Quality_Flag != 'NONE'`

**Importante**: para limpiar un campo en COALESCE, se necesita `UPDATE` directo (como hace v21 para BL-38 residual).

---

## 5. QUERIES DE VALIDACIÓN POST-v21

```sql
-- Fix B: BL-38 limpieza directa
SELECT COUNT(*) FROM fund_master
WHERE LENGTH(Benchmark_Declared) > 100 AND Benchmark_Declared != 'NO_BENCHMARK'
   OR Benchmark_Declared LIKE '%además%'
   OR Benchmark_Declared LIKE '%último informe%'
   OR Benchmark_Declared LIKE '%través%';
-- Esperado post-v21: 0

-- Fix C: Geography Regla 1 con BD lookup
SELECT COUNT(*) FROM fund_master
WHERE Geography IS NULL AND Investment_Universe = 'Global'
  AND Fund_Nature NOT IN ('Monetario', 'Renta Fija Corto Plazo');
-- Esperado post-v21: 0

-- Cobertura agregada
SELECT 'Geography NULL'         AS attr, COUNT(*) AS n FROM fund_master WHERE Geography IS NULL
UNION ALL
SELECT 'BL-38 contaminados',    COUNT(*) FROM fund_master
  WHERE Benchmark_Declared LIKE '%además%' OR Benchmark_Declared LIKE '%último informe%'
     OR Benchmark_Declared LIKE '%través%' OR LENGTH(Benchmark_Declared) > 100
UNION ALL
SELECT 'Accumulation NULL',     COUNT(*) FROM fund_master WHERE Accumulation_Policy IS NULL
UNION ALL
SELECT 'Currency_Hedged NULL',  COUNT(*) FROM fund_master WHERE Currency_Hedged IS NULL;
```

Valores esperados tras próximo ciclo con v21:
- Geography NULL: **~377** (412 −35)
- BL-38 contaminados: **0** (11 →0)
- Accumulation NULL: **594** (sin cambio — ya en límite teórico)
- Currency_Hedged NULL: **~1.195** (sin cambio — ya aplicado)

---

## 6. ACCIONES INMEDIATAS

1. **Desplegar `pipeline.py` v21** — entregado en `/mnt/user-data/outputs/pipeline.py`
2. **Ejecutar ciclo completo** — un único `discoverAllFunds.bat` normal (no FORCE_REFRESH)
3. **Validar queries de sección 5** — confirmar que BL-38 residuales = 0 y Geography NULL ≈ 377
4. **Cerrar backlog P1** si los controles pasan

---

## 7. POST-P1 — siguientes prioridades

Una vez validado v21, se puede dar por **completada la fase de depuración P1**:

1. **P2 Factores macro** — series FRED (BAMLH0A0HYM2, VIXCLS, T10Y2YM) + infraestructura de descarga histórica NAV
2. **Documentar Principio #10** en `PRINCIPIOS_DISENO.md`
3. **Investigación optativa** Style_Profile +61 fondos (bajo ROI, posponible a después de P2)
4. **Fuentes externas** para atributos con límite teórico alto: Morningstar/Bloomberg para MCF, Style, Hedging_Policy en fondos grandes

---

**Fin del documento.**
