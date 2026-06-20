# BL-COST-1+2 — Especificación operativa Sprint 1 (DIAG + Schema v19)

**Tipo:** Especificación arquitectónica + plan operativo Nivel-3 — **lista para implementación Nivel-2 Sonnet**
**Fecha:** 19 de mayo de 2026
**Autor:** José + Claude (sesión Nivel-3, Opus 4.7)
**Estado:** PROPUESTA v2 — pendiente aprobación checklist §11
**Versión:** 2.0 (corrige carencias detectadas en v1 por José + auditoría sistemática Opus)
**Sprint:** Sprint 1 de 2 (BL-COST-1 DIAG + BL-COST-2 SCHEMA). Sprint 2 = BL-COST-3 (router) + BL-COST-4 (extractor PRIIPs) + BL-COST-5 (validadores INTER).
**Dependencia bloqueante:** **BL-DLA-2 Sub-fase 2B cerrada con verificación pendiente**. Confirmar antes de iniciar Sprint 1 que el ciclo completo con `DLA_TABLE_SERIALIZATION_ENABLED=True` ha corrido al menos una vez contra BD producción sin errores. Si no, cerrar 2B primero.

---

## 0. Resumen ejecutivo

**Problema raíz cuantificado en sesión Opus 18-may-2026 (sesión previa):**

El extractor de fees actual (`kiid_parser.py`) está calibrado para el formato regulatorio **UCITS_KIID** (porcentajes escalares fijos). El corpus de 3.205 fondos es mayoritariamente **PRIIPS_KID** (valores en EUR absolutos sobre 10.000 EUR de inversión + función de coste por horizonte). El diagnóstico DLA-2 actual (`dla2_decision_diag.py v1.2`) mide solo NULLs (`entry_fee_null`, `exit_fee_null`, `oc_null`), ignorando dos patologías más graves:

1. **Falsos positivos silenciosos**: `Entry_Fee_Pct = 0` en BD cuando el KID dice `"5,25% / 510 USD"` (LU1084165304) o `"497 EUR"` (IE0032875985). El KPI de cobertura los marca como "extraídos correctamente".
2. **Mezcla conceptual en `Ongoing_Charge`**: el campo registra a veces el TER recurrente (gestión + admin + transaction) y a veces el "Annual cost impact" del RHP (incluye amortización de one-offs). IE0032875985: `OC=2.4%` en BD = ACI@3Y; el TER real es `~0.70%`. Cifras semánticamente distintas, no comparables.

**Validación empírica:** los 8 PDFs muestra analizados son **8/8 PRIIPS_KID**. El "caso por defecto" del extractor actual (UCITS) es el caso raro en el corpus.

**Impacto sobre P3 (capital preservation, IPC+M3):** las decisiones de scoring por coste se basan en cifras semánticamente incoherentes. Un fondo con `OC=2.4%` (=ACI_RHP) y uno con `OC=2.4%` (=TER real) no son comparables, pero hoy se tratan como si lo fueran. Bajo principio #1 (root cause): mientras `Ongoing_Charge` mezcle dos conceptos distintos, ninguna corrección de extractor downstream resolverá el problema.

**Alcance Sprint 1 (este documento):**

1. **BL-COST-1 (Diagnóstico ampliado)**: refactorizar `dla2_decision_diag.py` v1.2 → v1.3 con 4 fases nuevas (8/9/10/11), módulo puro `cost_format_signals.py` con funciones aisladas (R-7.4), CSV inventario ampliado a 27 columnas.

2. **BL-COST-2 (Schema v19)**: redefinir BBDD completa (no ALTER incremental) para:
   - Añadir 11 columnas nuevas a `fund_master`.
   - Renombrar `Ongoing_Charge → Ongoing_Charge_Recurrent`.
   - Crear tabla `fund_cost_schedule` con 2 índices.
   - Actualizar 4 módulos `shared/*` + `sqlite_writer.py` + `pipeline.py` (R-3).
   - Migración export → drop → recreate → import idempotente.

**FUERA del alcance Sprint 1 (Sprint 2 posterior):**

- BL-COST-3: `cost_format_router.py` (detecta formato KID por fondo).
- BL-COST-4: `priips_cost_extractor.py` + `ucits_cost_extractor.py` (refactor del legacy).
- BL-COST-5: reglas INTER-COST-1/2/3 en `classify_utils.py` + integración pipeline.
- BL-COST-6: re-ejecución completa + reporte de cambios.

Sprint 1 **solo prepara el terreno y cuantifica el problema**; las 11 columnas nuevas quedan NULL en producción tras el Sprint 1. Sprint 2 las puebla.

**Criterio de cierre Sprint 1:**

- ✅ Nuevo diagnóstico ejecutado sobre 3.205 fondos.
- ✅ Cuantificación de % falsos positivos en `Entry_Fee_Pct = 0`, `Exit_Fee_Pct = 0`, `Ongoing_Charge` vs ACI gap.
- ✅ Schema v19 desplegado, BD con 59 columnas en `fund_master` + tabla `fund_cost_schedule` + 2 índices.
- ✅ `Ongoing_Charge` renombrado a `Ongoing_Charge_Recurrent` en BD y en todos los módulos consumidores.
- ✅ AST validation pasada (R-8) en todos los módulos modificados.
- ✅ Test suite aislado (R-7) pasa 8/8 sobre PDFs muestra.
- ✅ Smoke test sobre BD producción confirma sin regresión.

---

## 1. Decisiones arquitectónicas (cerradas en sesión Opus 18+19-may-2026)

### Decisión 1: Modelado híbrido de la función de coste por horizonte

**Decisión:** escalares `ACI_1Y` y `ACI_RHP` en `fund_master` + tabla `fund_cost_schedule` 1:N para puntos adicionales.

**Razonamiento (Principio #2 DRY + ergonomía P3):**
- P3 consulta sistemáticamente "salida 1Y" y "salida RHP". Esos dos puntos deben ser query-friendly sin join.
- Casos con 3-5 puntos (RV largo plazo) preservan trazabilidad en la tabla normalizada.
- DRY: el escalar es **cache desnormalizado** de la fila correspondiente. Una fuente de verdad (la tabla). El writer mantiene la coherencia tras cada upsert.

**Consecuencia operativa:** `ACI_1Y` y `ACI_RHP` se derivan de `fund_cost_schedule` en cada upsert al fondo. Si hay incoherencia, la tabla manda. Sprint 2 implementa esta derivación; Sprint 1 deja los escalares NULL.

### Decisión 2: Sprint 1 = Diagnóstico ampliado + Schema v19 (redefinición BBDD completa)

**Decisión:** preparar el terreno (medir + esquema) antes de implementar router/extractor. **Redefinición BBDD completa**, no ALTER incremental.

**Razonamiento:**

- Solo diagnóstico sin schema = sabemos el problema pero no podemos arreglarlo.
- Sprint completo end-to-end con solo 8 PDFs como test = compromiso prematuro sin cuantificación del corpus.
- ALTER incremental obligaría a usar `Ongoing_Charge` confusamente durante Sprint 2 (no se puede `RENAME COLUMN` con seguridad en SQLite <3.25; build actual no garantizado). **Renombrar ahora elimina deuda semántica.**
- Schema canónico en `schema_fondos.sql` queda como única fuente de verdad sin "v18 más parches v19".

**Consecuencia operativa:** las 11 columnas nuevas quedan NULL en todos los fondos al final del Sprint 1. Se pueblan en Sprint 2. `Ongoing_Charge_Recurrent` conserva el valor que tenía `Ongoing_Charge` antes (copia durante migración); Sprint 2 podrá luego decidir, fondo por fondo, si ese valor era realmente TER o si era ACI@RHP mal etiquetado.

### Decisión 3: Doble columna para fees condicionales

**Decisión:** `Entry_Fee_Pct` (valor aplicado hoy, puede ser 0) + `Entry_Fee_Pct_Max` (techo declarado). Idem `Exit_Fee_Pct_Max`.

**Razonamiento:**
- IE00BZ4D7085: `"0,00% No se aplica...pero podría aplicar hasta 5% en el futuro"` → hoy=0, máximo=5.
- LU1502282632 (Candriam): `"3,50% máximo del importe que paga"` → declarado 3.50%, aplicado puede ser cualquier valor ≤ ese.
- Colapsar pierde semántica crítica para P3 (análisis worst-case).

### Decisión 4: `Cost_RHP_Years` y `Horizon_Years` ambos REAL

**Decisión:** ambos campos REAL. Razón:
- Monetarios con RHP = 3 meses → 0.25.
- Necesario para coherencia entre `fund_master.Cost_RHP_Years` y filas de `fund_cost_schedule` que correspondan al RHP.

### Decisión 5: Valores categóricos de los nuevos campos en inglés UPPERCASE (Principio #8)

**Decisión y razonamiento:**

Los nuevos campos categóricos siguen la convención existente del Principio #8 para los flags técnicos:

| Campo nuevo | Convención | Razón |
|---|---|---|
| `KID_Format` | Inglés UPPERCASE | Paralelo a `Replication_Method` (ACTIVE/PASSIVE), `Hedging_Policy` (HEDGED/UNHEDGED) |
| `Cost_Extraction_Quality` | Inglés UPPERCASE | Paralelo a `SRRI_Quality_Flag` (HIGH/MEDIUM_VISUAL/...) |
| `KID_Currency` | ISO 4217 (inglés UPPERCASE) | Estándar internacional |
| `fund_cost_schedule.Source` | Inglés UPPERCASE con guion bajo | Identificador técnico interno |

**Acción explícita §3.3:** añadir estos campos al diccionario `ALLOWED_VALUES_BY_COLUMN` de `classify_utils.py`.

### Decisión 6: Convención de valores de calidad paralela a SRRI_Quality_Flag

**Decisión:** corregir v1 que usaba `HIGH_CROSS_VALIDATED`. La convención paralela a `SRRI_Quality_Flag` da:

| Valor | Significado | Análogo SRRI |
|---|---|---|
| `HIGH` | % en descripción confirmado por EUR en celda (cruzados). | `HIGH` (textual + visual coinciden) |
| `MEDIUM_CROSS` | % y EUR presentes pero discrepan <0.1pp; se usa el % (más fiable). | `MEDIUM_TEXT` |
| `MEDIUM_EUR` | Solo valor EUR presente, % derivado por división. | `MEDIUM_VISUAL` |
| `MEDIUM_PCT` | Solo % presente, sin verificación EUR. | `MEDIUM_TEXT` |
| `LOW` | Texto contradictorio o ambiguo (`"hasta"`, `"máximo"`, `"podría"`). | `LOW_CONFLICT` |
| `NONE` | Extracción fallida o atributo no presente en el KID. | `NONE` |

---

## 2. Schema v19 — DDL completo

### 2.1 Estado actual (v18) que se modifica

`fund_master` actualmente tiene 48 columnas (incluye `DLA2_Table_Text` añadida en v18 BL-DLA-2 Sub-fase 2A). Tras v19:
- **+11 columnas nuevas.**
- **`Ongoing_Charge` renombrado a `Ongoing_Charge_Recurrent`.**
- **Total = 59 columnas en `fund_master`.**
- **+1 tabla nueva (`fund_cost_schedule`) + 2 índices.**

### 2.2 Diff por columna en `fund_master`

| # | Columna | Tipo | Política UPSERT | Dominio / valores |
|---|---|---|---|---|
| 1 | `KID_Format` | TEXT | Sobrescritura directa (es metadato del documento; si cambia, debe reflejarse) | `'UCITS_KIID'`, `'PRIIPS_KID'`, `'UNKNOWN'` |
| 2 | `KID_Currency` | TEXT | COALESCE | ISO 4217 (`'EUR'`, `'USD'`, `'GBP'`, `'CHF'`, etc.) |
| 3 | `Cost_Extraction_Quality` | TEXT | COALESCE | `'HIGH'`, `'MEDIUM_CROSS'`, `'MEDIUM_EUR'`, `'MEDIUM_PCT'`, `'LOW'`, `'NONE'` |
| 4 | `Cost_RHP_Years` | REAL | COALESCE | Real >0, ≤50 (típicamente 0.25, 0.5, 1, 3, 5, 6, 8, 10) |
| 5 | `Entry_Fee_Pct_Max` | REAL | COALESCE | Real ≥0, ≤25 |
| 6 | `Exit_Fee_Pct_Max` | REAL | COALESCE | Real ≥0, ≤25 |
| 7 | `Management_Fee_Pct` | REAL | COALESCE | Real ≥0, ≤10 |
| 8 | `Transaction_Cost_Pct` | REAL | COALESCE | Real ≥0, ≤5 |
| 9 | `Performance_Fee_Pct` | REAL | COALESCE | Real ≥0, ≤30 |
| 10 | `ACI_1Y` | REAL | COALESCE | Real ≥0, ≤50 (puede ser alto si entry fee grande amortizado en 1 año) |
| 11 | `ACI_RHP` | REAL | COALESCE | Real ≥0, ≤25 |

**Renombrado:**

| Antes (v18) | Después (v19) | Política UPSERT |
|---|---|---|
| `Ongoing_Charge` | `Ongoing_Charge_Recurrent` | COALESCE (mismo patrón que antes) |

**Justificación de la política "Sobrescritura directa" para `KID_Format`:**

Análogamente a `Heuristic_Block` (que se sobrescribe en cada ciclo porque refleja la decisión actual del clasificador), `KID_Format` refleja la decisión actual del detector. Si el detector cambia o el KID se re-descarga, el valor debe actualizarse. **Sin embargo**, COALESCE para los demás campos cumple Principio #1 de Principios de Diseño (`COALESCE obligatorio para preservar información en ciclos CACHED`).

### 2.3 DDL canónico completo `schema_fondos.sql` (v19)

**Sección `fund_master`** (parche aplicado al final del bloque de columnas, antes de cierre `)`; las columnas previas no se modifican excepto el renombrado):

```sql
-- ====================================================================
-- fund_master v19 — BL-COST-2: PRIIPs/KID-aware cost model
-- Modificaciones respecto v18:
--   1. RENAME COLUMN: Ongoing_Charge → Ongoing_Charge_Recurrent
--      (semántica: solo gestión + admin + transaction, no incluye amortización
--       de one-offs. Es el TER recurrente puro, comparable inter-fondos.)
--   2. +11 columnas nuevas para modelado dual UCITS/PRIIPs.
--   3. +1 tabla normalizada fund_cost_schedule (función coste por horizonte).
-- ====================================================================

CREATE TABLE fund_master (
    ISIN                  TEXT PRIMARY KEY,
    Fund_Name             TEXT,
    Management_Company    TEXT,
    Fund_Nature           TEXT,
    Profile               TEXT,
    Type                  TEXT,
    Strategy              TEXT,
    Family                TEXT,
    Style_Profile         TEXT,
    Geography             TEXT,
    Theme                 TEXT,
    Is_ESG                INTEGER DEFAULT 0,
    Exposure_Bias         TEXT,
    Benchmark_Type        TEXT,
    Subtype               TEXT,
    Market_Cap_Focus      TEXT,
    Sector_Focus          TEXT,
    Currency_Hedged       TEXT,
    Investment_Universe   TEXT,
    Investment_Focus      TEXT,
    Credit_Quality        TEXT,
    Accumulation_Policy   TEXT,
    Heuristic_Block       TEXT,
    Heuristic_Core        INTEGER DEFAULT 0,
    SRRI                  INTEGER,
    Fund_Currency         TEXT,
    Portfolio_Currency    TEXT,
    Hedging_Policy        TEXT,
    Replication_Method    TEXT,
    Derivatives_Usage     TEXT,
    Benchmark_Declared    TEXT,
    -- v19: renombrado, mismo tipo y posición
    Ongoing_Charge_Recurrent  REAL,
    Entry_Fee_Pct         REAL,
    Exit_Fee_Pct          REAL,
    Fee_Known_Flag        TEXT,
    Sfdr_Article          INTEGER,
    Recommended_Holding_Period TEXT,
    Leverage_Used         TEXT,
    Liquidity_Profile     TEXT,
    Distribution_Frequency TEXT,
    fund_family_id        TEXT,
    Inference_Trace       TEXT,
    SRRI_Quality_Flag     TEXT,
    Data_Quality_Flag     TEXT,
    Created_At            TEXT DEFAULT (datetime('now')),
    Updated_At            TEXT DEFAULT (datetime('now')),
    -- ============================================================
    -- v19 (BL-COST-2): bloque coste PRIIPs/KID-aware
    -- ============================================================
    KID_Format            TEXT,
    KID_Currency          TEXT,
    Cost_Extraction_Quality TEXT,
    Cost_RHP_Years        REAL,
    Entry_Fee_Pct_Max     REAL,
    Exit_Fee_Pct_Max      REAL,
    Management_Fee_Pct    REAL,
    Transaction_Cost_Pct  REAL,
    Performance_Fee_Pct   REAL,
    ACI_1Y                REAL,
    ACI_RHP               REAL
);
```

**Nota:** la posición exacta de algunas columnas (Created_At, Updated_At, fund_family_id) debe verificarse contra el `schema_fondos.sql` actual al momento de aplicar el cambio (R-3 verificar ficheros). La lista de 48+11 = 59 columnas es la canónica v19.

**Sección `fund_cost_schedule`** (nueva tabla):

```sql
-- ====================================================================
-- fund_cost_schedule v19 — Función de coste por horizonte (PRIIPs)
-- 1:N respecto a fund_master. Para cada fondo, una fila por punto del
-- escenario "salida después de X años" presente en la tabla
-- "Costes a lo largo del tiempo" / "Costs over time" del KID.
--
-- Para fondos UCITS (1 valor único de OC), se sintetiza 1 fila con
--   Horizon_Years = Cost_RHP_Years
--   Annual_Impact_Pct = Ongoing_Charge_Recurrent
--   Source = 'UCITS_DERIVED'
-- Esto permite a P3 consultar uniformemente sin lógica condicional.
-- ====================================================================

CREATE TABLE fund_cost_schedule (
    ISIN              TEXT NOT NULL,
    Horizon_Years     REAL NOT NULL,
    Is_RHP            INTEGER NOT NULL DEFAULT 0,
    Total_Costs_EUR   REAL,
    Total_Costs_Pct   REAL,
    Annual_Impact_Pct REAL,
    Source            TEXT NOT NULL,
    Created_At        TEXT NOT NULL DEFAULT (datetime('now')),
    Updated_At        TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ISIN, Horizon_Years),
    CHECK (Horizon_Years > 0 AND Horizon_Years <= 50),
    CHECK (Is_RHP IN (0, 1)),
    CHECK (Source IN ('PRIIPS_COSTS_OVER_TIME', 'UCITS_DERIVED', 'MANUAL'))
);

CREATE INDEX idx_cost_schedule_isin ON fund_cost_schedule(ISIN);
CREATE INDEX idx_cost_schedule_rhp  ON fund_cost_schedule(ISIN) WHERE Is_RHP = 1;
```

**Justificación de no usar FK formal `FOREIGN KEY(ISIN) REFERENCES fund_master(ISIN)`:**

El proyecto actual NO usa FK declarativos por consistencia con el patrón establecido (revisado en `sqlite_writer.py`). La integridad referencial se garantiza por contrato del writer: todo upsert en `fund_cost_schedule` requiere que el ISIN exista en `fund_master`. Las CHECK constraints aportan integridad local sin coste de transacción.

**Documentación del campo `Source`:**

| Valor | Cuándo se usa | Quién lo escribe |
|---|---|---|
| `'PRIIPS_COSTS_OVER_TIME'` | Extracción directa de la tabla "Costes a lo largo del tiempo" del KID PRIIPs. Múltiples filas (1Y, RHP, opcionalmente más). | Sprint 2: `priips_cost_extractor.py` |
| `'UCITS_DERIVED'` | Síntesis para fondos UCITS con OC anual único. Una sola fila con `Horizon_Years = Cost_RHP_Years` y `Annual_Impact_Pct = Ongoing_Charge_Recurrent`. | Sprint 2: `ucits_cost_extractor.py` |
| `'MANUAL'` | Enriquecimiento manual posterior (no usado en Sprints 1-2). | Operativo José (futuro) |

### 2.4 Migración v18 → v19 (export → drop → recreate → import)

**Script:** `scripts/mig/migrate_v18_to_v19.py`

```python
"""
Migración no destructiva v18 → v19.
Estrategia: export → drop → recreate → import. Justificación: permite renombrar
Ongoing_Charge → Ongoing_Charge_Recurrent (SQLite RENAME COLUMN no disponible
con seguridad en builds previos a 3.25, y este proyecto no garantiza versión).

Garantías:
- 0 filas perdidas en fund_master.
- 0 valores modificados (excepto el renombrado de columna).
- fund_cost_schedule queda vacía (se puebla en Sprint 2).
- Idempotente: si ya está en v19, no hace nada.

Backup requerido ANTES de ejecutar: copia de fondos.sqlite.
"""
import sqlite3
import shutil
import datetime
from pathlib import Path
from shared.config import DB_PATH
from shared.init_db import create_schema_v19


def detect_schema_version(conn: sqlite3.Connection) -> int:
    """Devuelve 18 o 19 según presencia/ausencia de columnas v19."""
    cur = conn.execute("PRAGMA table_info(fund_master)")
    cols = {row[1] for row in cur.fetchall()}
    if "KID_Format" in cols and "Ongoing_Charge_Recurrent" in cols:
        return 19
    if "Ongoing_Charge" in cols:
        return 18
    raise RuntimeError("Schema no reconocido: ni v18 ni v19.")


def backup_db(db_path: Path) -> Path:
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db_path.parent / f"{db_path.stem}_pre_v19_{timestamp}{db_path.suffix}"
    shutil.copy2(db_path, backup)
    print(f"[BACKUP] {backup}")
    return backup


def migrate():
    db_path = Path(DB_PATH)
    if not db_path.exists():
        raise FileNotFoundError(f"No existe BD: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.isolation_level = None

    version = detect_schema_version(conn)
    if version == 19:
        print("[SKIP] BD ya está en v19. Nada que hacer.")
        conn.close()
        return

    print(f"[INFO] BD detectada en v{version}. Iniciando migración a v19.")
    backup_db(db_path)

    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")
    try:
        # 1. Renombrar tabla actual a tabla temporal
        cur.execute("ALTER TABLE fund_master RENAME TO fund_master_v18_tmp")

        # 2. Crear schema v19 limpio (incluye fund_master y fund_cost_schedule)
        create_schema_v19(conn, drop_existing=False)

        # 3. Copiar datos. Ongoing_Charge → Ongoing_Charge_Recurrent.
        #    Las 11 columnas nuevas quedan NULL (default).
        cur.execute("""
            INSERT INTO fund_master (
                ISIN, Fund_Name, Management_Company, Fund_Nature, Profile,
                Type, Strategy, Family, Style_Profile, Geography, Theme,
                Is_ESG, Exposure_Bias, Benchmark_Type, Subtype,
                Market_Cap_Focus, Sector_Focus, Currency_Hedged,
                Investment_Universe, Investment_Focus, Credit_Quality,
                Accumulation_Policy, Heuristic_Block, Heuristic_Core,
                SRRI, Fund_Currency, Portfolio_Currency, Hedging_Policy,
                Replication_Method, Derivatives_Usage, Benchmark_Declared,
                Ongoing_Charge_Recurrent,
                Entry_Fee_Pct, Exit_Fee_Pct, Fee_Known_Flag, Sfdr_Article,
                Recommended_Holding_Period, Leverage_Used, Liquidity_Profile,
                Distribution_Frequency, fund_family_id, Inference_Trace,
                SRRI_Quality_Flag, Data_Quality_Flag, Created_At, Updated_At
            )
            SELECT
                ISIN, Fund_Name, Management_Company, Fund_Nature, Profile,
                Type, Strategy, Family, Style_Profile, Geography, Theme,
                Is_ESG, Exposure_Bias, Benchmark_Type, Subtype,
                Market_Cap_Focus, Sector_Focus, Currency_Hedged,
                Investment_Universe, Investment_Focus, Credit_Quality,
                Accumulation_Policy, Heuristic_Block, Heuristic_Core,
                SRRI, Fund_Currency, Portfolio_Currency, Hedging_Policy,
                Replication_Method, Derivatives_Usage, Benchmark_Declared,
                Ongoing_Charge,                          -- v18 origen
                Entry_Fee_Pct, Exit_Fee_Pct, Fee_Known_Flag, Sfdr_Article,
                Recommended_Holding_Period, Leverage_Used, Liquidity_Profile,
                Distribution_Frequency, fund_family_id, Inference_Trace,
                SRRI_Quality_Flag, Data_Quality_Flag, Created_At, Updated_At
            FROM fund_master_v18_tmp
        """)
        n = cur.execute("SELECT COUNT(*) FROM fund_master").fetchone()[0]
        n_orig = cur.execute("SELECT COUNT(*) FROM fund_master_v18_tmp").fetchone()[0]
        if n != n_orig:
            raise RuntimeError(f"Pérdida de filas: {n_orig} → {n}")

        # 4. Drop temporal
        cur.execute("DROP TABLE fund_master_v18_tmp")

        cur.execute("COMMIT")
        print(f"[OK] Migración v18 → v19 completa. {n} filas preservadas.")
        print("[OK] fund_cost_schedule creada vacía. 11 columnas nuevas NULL.")
    except Exception as e:
        cur.execute("ROLLBACK")
        print(f"[ERROR] migración revertida: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
```

**Verificación post-migración (José ejecuta en DBeaver):**

```sql
-- Q-MIG-1: schema v19 verificado
SELECT COUNT(*) FROM pragma_table_info('fund_master');  -- esperado: 59

-- Q-MIG-2: Ongoing_Charge ya NO existe (renombrado)
SELECT COUNT(*) FROM pragma_table_info('fund_master')
WHERE name = 'Ongoing_Charge';
-- esperado: 0

SELECT COUNT(*) FROM pragma_table_info('fund_master')
WHERE name = 'Ongoing_Charge_Recurrent';
-- esperado: 1

-- Q-MIG-3: datos preservados (compara contra el backup)
SELECT COUNT(*) FROM fund_master;  -- esperado: 3205
SELECT
    SUM(Ongoing_Charge_Recurrent IS NOT NULL) AS oc_pob,
    SUM(Entry_Fee_Pct IS NOT NULL) AS ef_pob,
    SUM(Exit_Fee_Pct IS NOT NULL) AS xf_pob
FROM fund_master;
-- esperado: cifras ≥ pre-migración (no se pierde nada)

-- Q-MIG-4: 11 columnas nuevas todas NULL
SELECT
    SUM(KID_Format IS NULL)              AS kf,
    SUM(KID_Currency IS NULL)            AS kc,
    SUM(Cost_Extraction_Quality IS NULL) AS ceq,
    SUM(Cost_RHP_Years IS NULL)          AS crhp,
    SUM(Entry_Fee_Pct_Max IS NULL)       AS efm,
    SUM(Exit_Fee_Pct_Max IS NULL)        AS xfm,
    SUM(Management_Fee_Pct IS NULL)      AS mf,
    SUM(Transaction_Cost_Pct IS NULL)    AS tc,
    SUM(Performance_Fee_Pct IS NULL)     AS pf,
    SUM(ACI_1Y IS NULL)                  AS a1,
    SUM(ACI_RHP IS NULL)                 AS ar,
    COUNT(*) AS total
FROM fund_master;
-- esperado: las 11 sumas = total = 3205

-- Q-MIG-5: tabla nueva creada y vacía
SELECT name FROM sqlite_master WHERE type='table' AND name='fund_cost_schedule';
-- esperado: 1 fila
SELECT COUNT(*) FROM fund_cost_schedule;
-- esperado: 0

-- Q-MIG-6: índices creados
SELECT name FROM sqlite_master WHERE type='index'
  AND name IN ('idx_cost_schedule_isin', 'idx_cost_schedule_rhp');
-- esperado: 2 filas
```

---

## 3. Cambios por módulo

### 3.1 `proyecto1/db/schema_fondos.sql`

**Modificaciones:**

1. Renombrar columna en bloque CREATE TABLE: `Ongoing_Charge` → `Ongoing_Charge_Recurrent`.
2. Añadir 11 columnas nuevas al final del bloque (antes del cierre `)`).
3. Añadir bloque CREATE TABLE `fund_cost_schedule` después de `fund_master`.
4. Añadir 2 CREATE INDEX.

**Verificación AST equivalente para SQL:** ejecutar `sqlite3 :memory: < schema_fondos.sql` y verificar exit code 0.

### 3.2 `shared/config.py`

**Modificaciones:**

```python
# ====================================================================
# v19 (BL-COST-2): constantes de coste PRIIPs/KID-aware
# ====================================================================

SCHEMA_VERSION = 19  # Versión actual del schema canónico

# Valores permitidos para los nuevos campos categóricos (Principio #8)
KID_FORMAT_VALUES = ('UCITS_KIID', 'PRIIPS_KID', 'UNKNOWN')

COST_EXTRACTION_QUALITY_VALUES = (
    'HIGH', 'MEDIUM_CROSS', 'MEDIUM_EUR', 'MEDIUM_PCT', 'LOW', 'NONE'
)

COST_SCHEDULE_SOURCE_VALUES = (
    'PRIIPS_COSTS_OVER_TIME', 'UCITS_DERIVED', 'MANUAL'
)

# Inversión base estándar para tablas de coste PRIIPs (10.000 EUR/USD).
# Usado por priips_cost_extractor.py (Sprint 2) para convertir
# valores EUR absolutos a porcentajes.
PRIIPS_INVESTMENT_BASE = 10000.0

# Tolerancia para cross-validation %↔EUR en priips_cost_extractor.
# Si |implied_pct - declared_pct| <= este valor → quality = 'HIGH'.
COST_CROSS_VALIDATION_TOLERANCE_PCT = 0.05  # 5 basis points

# Kill-switch Sprint 2 (no operativo en Sprint 1):
# PRIIPS_COST_EXTRACTION_ENABLED = False  # se activa cuando Sprint 2 entrega
```

**Verificación:** AST OK (Python).

### 3.3 `shared/init_db.py`

**Modificaciones:**

1. Función `create_schema_v19(conn, drop_existing=False)` que:
   - Si `drop_existing`: `DROP TABLE IF EXISTS fund_master`, `fund_cost_schedule`.
   - Lee `schema_fondos.sql` y ejecuta su contenido.
2. La función principal `init_db()` llama a `create_schema_v19(conn, drop_existing=True)` cuando crea BD desde cero.

```python
def create_schema_v19(conn: sqlite3.Connection, drop_existing: bool = False) -> None:
    """
    Crea (o re-crea) el schema v19 desde schema_fondos.sql.

    Args:
        conn: conexión SQLite activa.
        drop_existing: si True, DROP TABLE antes de CREATE. Usado al crear BD
                       nueva. NUNCA usar drop_existing=True en migración v18→v19
                       (la migración usa RENAME TO _tmp para preservar datos).
    """
    if drop_existing:
        conn.execute("DROP TABLE IF EXISTS fund_cost_schedule")
        conn.execute("DROP TABLE IF EXISTS fund_master")
    schema_path = Path(__file__).parent.parent / 'proyecto1' / 'db' / 'schema_fondos.sql'
    with open(schema_path, encoding='utf-8') as f:
        sql = f.read()
    conn.executescript(sql)
```

### 3.4 `shared/schema_checks.py`

**Modificaciones:**

1. Función `expected_columns_v19()` que devuelve set canónico de 59 columnas.
2. Función `check_schema_v19(conn)` que valida:
   - `fund_master` tiene exactamente las 59 columnas esperadas.
   - `Ongoing_Charge` NO existe en `fund_master` (renombrado).
   - `Ongoing_Charge_Recurrent` SÍ existe.
   - `fund_cost_schedule` existe.
   - Los 2 índices existen.
   - Las CHECK constraints están activas (`PRAGMA integrity_check`).

```python
EXPECTED_COLUMNS_V19 = {
    # Identidad y nombres
    'ISIN', 'Fund_Name', 'Management_Company',
    # Clasificación principal
    'Fund_Nature', 'Profile', 'Type', 'Strategy', 'Family',
    'Style_Profile', 'Geography', 'Theme', 'Is_ESG', 'Exposure_Bias',
    'Benchmark_Type', 'Subtype', 'Market_Cap_Focus', 'Sector_Focus',
    'Currency_Hedged', 'Investment_Universe', 'Investment_Focus',
    'Credit_Quality', 'Accumulation_Policy',
    # Heurística
    'Heuristic_Block', 'Heuristic_Core',
    # Parsing KIID
    'SRRI', 'Fund_Currency', 'Portfolio_Currency', 'Hedging_Policy',
    'Replication_Method', 'Derivatives_Usage', 'Benchmark_Declared',
    'Ongoing_Charge_Recurrent',    # v19: renombrado desde Ongoing_Charge
    'Entry_Fee_Pct', 'Exit_Fee_Pct', 'Fee_Known_Flag',
    'Sfdr_Article', 'Recommended_Holding_Period', 'Leverage_Used',
    'Liquidity_Profile', 'Distribution_Frequency',
    # Identidad familiar
    'fund_family_id',
    # QA / trazabilidad
    'Inference_Trace', 'SRRI_Quality_Flag', 'Data_Quality_Flag',
    'Created_At', 'Updated_At',
    # v19 BL-COST-2: bloque coste PRIIPs/KID-aware (11 nuevas)
    'KID_Format', 'KID_Currency', 'Cost_Extraction_Quality',
    'Cost_RHP_Years', 'Entry_Fee_Pct_Max', 'Exit_Fee_Pct_Max',
    'Management_Fee_Pct', 'Transaction_Cost_Pct', 'Performance_Fee_Pct',
    'ACI_1Y', 'ACI_RHP',
}
assert len(EXPECTED_COLUMNS_V19) == 59, "v19 tiene 59 columnas en fund_master"


def check_schema_v19(conn: sqlite3.Connection) -> dict:
    issues = []
    cur = conn.execute("PRAGMA table_info(fund_master)")
    actual = {row[1] for row in cur.fetchall()}

    missing = EXPECTED_COLUMNS_V19 - actual
    extra = actual - EXPECTED_COLUMNS_V19
    if missing:
        issues.append(f"Columnas faltantes en fund_master: {missing}")
    if extra:
        issues.append(f"Columnas inesperadas en fund_master: {extra}")
    if 'Ongoing_Charge' in actual:
        issues.append("Ongoing_Charge debería estar renombrada a "
                      "Ongoing_Charge_Recurrent (v19)")

    # fund_cost_schedule
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='fund_cost_schedule'"
    )
    if not cur.fetchone():
        issues.append("Tabla fund_cost_schedule no existe (v19)")

    # Índices
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name IN ('idx_cost_schedule_isin', 'idx_cost_schedule_rhp')"
    )
    n_idx = len(cur.fetchall())
    if n_idx != 2:
        issues.append(f"Faltan índices fund_cost_schedule (esperado 2, hay {n_idx})")

    return {'ok': len(issues) == 0, 'issues': issues}
```

### 3.5 `proyecto1/sqlite_writer.py` — modificaciones detalladas

Este es el módulo más afectado. Cambios:

**3.5.1. Bloque INSERT INTO fund_master (líneas ~440-486):**

Renombrar `Ongoing_Charge` → `Ongoing_Charge_Recurrent` y añadir 11 columnas. La lista pasa de 44 columnas listadas (sin Created_At) a 55 columnas listadas.

**Estructura propuesta** (las inserciones nuevas se marcan con `-- v19`):

```sql
INSERT INTO fund_master (
    ISIN,
    Fund_Name,
    Management_Company,
    Fund_Nature,
    Profile,
    Type,
    Strategy,
    Family,
    Style_Profile,
    Geography,
    Theme,
    Is_ESG,
    Exposure_Bias,
    Benchmark_Type,
    Subtype,
    Market_Cap_Focus,
    Sector_Focus,
    Currency_Hedged,
    Investment_Universe,
    Investment_Focus,
    Credit_Quality,
    Accumulation_Policy,
    Heuristic_Block,
    Heuristic_Core,
    SRRI,
    Fund_Currency,
    Portfolio_Currency,
    Hedging_Policy,
    Replication_Method,
    Derivatives_Usage,
    Benchmark_Declared,
    Ongoing_Charge_Recurrent,    -- v19: renombrado
    Entry_Fee_Pct,
    Exit_Fee_Pct,
    Fee_Known_Flag,
    Sfdr_Article,
    Recommended_Holding_Period,
    Leverage_Used,
    Liquidity_Profile,
    Distribution_Frequency,
    fund_family_id,
    Inference_Trace,
    SRRI_Quality_Flag,
    Data_Quality_Flag,
    Updated_At,
    -- v19 BL-COST-2: bloque coste (11 nuevas, todas NULL en Sprint 1)
    KID_Format,
    KID_Currency,
    Cost_Extraction_Quality,
    Cost_RHP_Years,
    Entry_Fee_Pct_Max,
    Exit_Fee_Pct_Max,
    Management_Fee_Pct,
    Transaction_Cost_Pct,
    Performance_Fee_Pct,
    ACI_1Y,
    ACI_RHP
)
VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)
```

Total: 45 + 11 = 56 placeholders.

**3.5.2. Bloque ON CONFLICT DO UPDATE (líneas ~494-551):**

Para `Ongoing_Charge_Recurrent` (línea 534 original):

```sql
Ongoing_Charge_Recurrent = COALESCE(excluded.Ongoing_Charge_Recurrent, fund_master.Ongoing_Charge_Recurrent),
```

Añadir 11 cláusulas nuevas tras `Distribution_Frequency`:

```sql
-- v19 BL-COST-2: bloque coste PRIIPs/KID-aware
KID_Format               = excluded.KID_Format,    -- sobrescritura directa (metadato documento)
KID_Currency             = COALESCE(excluded.KID_Currency,             fund_master.KID_Currency),
Cost_Extraction_Quality  = COALESCE(excluded.Cost_Extraction_Quality,  fund_master.Cost_Extraction_Quality),
Cost_RHP_Years           = COALESCE(excluded.Cost_RHP_Years,           fund_master.Cost_RHP_Years),
Entry_Fee_Pct_Max        = COALESCE(excluded.Entry_Fee_Pct_Max,        fund_master.Entry_Fee_Pct_Max),
Exit_Fee_Pct_Max         = COALESCE(excluded.Exit_Fee_Pct_Max,         fund_master.Exit_Fee_Pct_Max),
Management_Fee_Pct       = COALESCE(excluded.Management_Fee_Pct,       fund_master.Management_Fee_Pct),
Transaction_Cost_Pct     = COALESCE(excluded.Transaction_Cost_Pct,     fund_master.Transaction_Cost_Pct),
Performance_Fee_Pct      = COALESCE(excluded.Performance_Fee_Pct,      fund_master.Performance_Fee_Pct),
ACI_1Y                   = COALESCE(excluded.ACI_1Y,                   fund_master.ACI_1Y),
ACI_RHP                  = COALESCE(excluded.ACI_RHP,                  fund_master.ACI_RHP),
```

**3.5.3. Bloque `params` (líneas ~556 onwards):**

Añadir 11 valores nuevos al final, todos `record.get(...)`. En Sprint 1 todos serán None porque nadie los puebla.

```python
record.get("KID_Format"),
record.get("KID_Currency"),
record.get("Cost_Extraction_Quality"),
record.get("Cost_RHP_Years"),
record.get("Entry_Fee_Pct_Max"),
record.get("Exit_Fee_Pct_Max"),
record.get("Management_Fee_Pct"),
record.get("Transaction_Cost_Pct"),
record.get("Performance_Fee_Pct"),
record.get("ACI_1Y"),
record.get("ACI_RHP"),
```

**3.5.4. Renombrar referencia en línea actual 472 (`Ongoing_Charge` → `Ongoing_Charge_Recurrent`)** en la lista de columnas del INSERT y en el dict `params` correspondiente.

**3.5.5. Nueva función `upsert_cost_schedule()` (al final del módulo):**

```python
def upsert_cost_schedule(
    conn: sqlite3.Connection,
    isin: str,
    schedule_rows: list[dict],
) -> int:
    """
    Persiste filas de fund_cost_schedule para un fondo. NO usada en Sprint 1
    (el extractor PRIIPs no existe todavía). Sí usada en Sprint 2.

    Política:
    - DELETE previo de todas las filas del ISIN.
    - INSERT de las nuevas filas.
    - Razón: la función de coste por horizonte es atómica por fondo; si cambia
      la extracción (p.ej. KID actualizado con más horizontes), la versión
      antigua debe reemplazarse íntegra, no fusionarse fila a fila.

    Args:
        conn: conexión activa con isolation_level=None.
        isin: ISIN del fondo.
        schedule_rows: lista de dicts con claves obligatorias:
            Horizon_Years, Is_RHP, Source.
            Opcionales: Total_Costs_EUR, Total_Costs_Pct, Annual_Impact_Pct.

    Returns:
        número de filas insertadas.
    """
    if not schedule_rows:
        return 0
    cur = conn.cursor()
    cur.execute("DELETE FROM fund_cost_schedule WHERE ISIN = ?", (isin,))
    for row in schedule_rows:
        cur.execute(
            """
            INSERT INTO fund_cost_schedule (
                ISIN, Horizon_Years, Is_RHP,
                Total_Costs_EUR, Total_Costs_Pct, Annual_Impact_Pct,
                Source, Updated_At
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                isin,
                row['Horizon_Years'],
                int(row.get('Is_RHP', 0)),
                row.get('Total_Costs_EUR'),
                row.get('Total_Costs_Pct'),
                row.get('Annual_Impact_Pct'),
                row['Source'],
            ),
        )
    return len(schedule_rows)
```

### 3.6 `proyecto1/pipeline.py` — R-3 + R-4

**R-3: ampliar `_v3_row`** (línea ~562). Necesario para que CACHED detecte que faltan los nuevos atributos:

```python
_v3_row = conn.execute(
    "SELECT Investment_Universe, Accumulation_Policy, Currency_Hedged, "
    "Investment_Focus, Credit_Quality, Geography, Fund_Nature, "
    # v19 BL-COST-2: añadir KID_Format y Cost_Extraction_Quality
    "KID_Format, Cost_Extraction_Quality "
    "FROM fund_master WHERE ISIN=?", (isin,)
).fetchone()
```

Y la lógica de decisión:

```python
if _v3_row is None:
    _needs_char = True
else:
    # Campos v3 originales (NULL → re-char)
    _needs_char = any(v is None for v in _v3_row[:5])
    if not _needs_char and _v3_row[5] is None:    # Geography
        _needs_char = True
    if not _needs_char:                            # P09 universe/nature
        _db_universe = _v3_row[0]
        _db_nature = _v3_row[6]
        if (_db_universe == "Liquidity"
                and _db_nature not in ("Monetario", "Renta Fija Corto Plazo")):
            _needs_char = True
    # v19 BL-COST-2: si KID_Format o Cost_Extraction_Quality NULL → re-extract
    # (esto permite Sprint 2 poblar los atributos al re-procesar)
    if not _needs_char and (_v3_row[7] is None or _v3_row[8] is None):
        _needs_char = True
```

**Nota:** en Sprint 1 esto provoca que TODOS los fondos CACHED entren al characterize, porque las dos columnas nuevas están NULL en todos. Es lo esperado: prepara el terreno para que Sprint 2, cuando active el extractor PRIIPs, pueda poblar los campos en el primer ciclo de re-pasada.

**R-4: lectura de valores efectivos.** Sprint 1 NO añade reglas INTER que consuman los nuevos atributos (eso es Sprint 2 con BL-COST-5). Sin embargo, hay que dejar previsto el patrón: cuando Sprint 2 añada `validate_oc_vs_aci()`, deberá leer `_oc_bd`, `_aci_rhp_bd` en el bloque actual de líneas 1164-1169 y usar los valores efectivos.

**Documentación inline obligatoria en pipeline.py:**

```python
# v19 BL-COST-2: Sprint 1 NO añade reglas INTER. Sprint 2 añadirá:
#   validate_oc_vs_aci(oc_recurrent_efectivo, aci_rhp_efectivo, ...)
# usando el patrón _X_efectivo = record.get("X") or _X_bd según R-4.
# Las variables _oc_bd, _aci_rhp_bd, _kf_bd se añadirán al bloque de
# lectura BD previa (líneas ~1164-1169) en Sprint 2.
```

**Renombrado `Ongoing_Charge` en pipeline.py línea 721:**

```python
# ANTES:
"Ongoing_Charge": parsed.get("Ongoing_Charge"),

# DESPUÉS:
# v19: renombrado a Ongoing_Charge_Recurrent. Hasta Sprint 2, parsed["Ongoing_Charge"]
# sigue siendo lo que kiid_parser.py extrae (semánticamente puede ser TER o ACI;
# Sprint 2 desambigua). Lo mantenemos en la misma clave hasta entonces.
"Ongoing_Charge_Recurrent": parsed.get("Ongoing_Charge"),
```

**Justificación:** durante Sprint 1, `kiid_parser.py` no se toca (R-2 acción 1: el classifier de fees no se modifica todavía). El valor que produce sigue siendo el que producía antes; solo cambia la clave del dict y el nombre de la columna BD. La distinción semántica TER vs ACI se hace en Sprint 2 con el extractor nuevo.

### 3.7 `proyecto1/classify_utils.py`

**3.7.1. Añadir constantes al diccionario `ALLOWED_VALUES_BY_COLUMN`:**

```python
ALLOWED_VALUES_BY_COLUMN = {
    # ... (entradas existentes)
    # v19 BL-COST-2: nuevos campos categóricos
    'KID_Format': ['UCITS_KIID', 'PRIIPS_KID', 'UNKNOWN'],
    'Cost_Extraction_Quality': [
        'HIGH', 'MEDIUM_CROSS', 'MEDIUM_EUR', 'MEDIUM_PCT', 'LOW', 'NONE'
    ],
}
```

**3.7.2. Renombrar referencias internas:**

Buscar en `classify_utils.py` cualquier referencia a `'Ongoing_Charge'` (string literal) y renombrar a `'Ongoing_Charge_Recurrent'`. Localizadas:

- Línea 1779: función `detect_ongoing_charge_from_kiid` (la función queda como está, devuelve None — comportamiento sin cambio).
- Línea 1845-1847: comentarios sobre `Ongoing_Charge` → actualizar a `Ongoing_Charge_Recurrent`.

**3.7.3. NO se añaden validadores INTER-COST-N en Sprint 1.** Esos son Sprint 2.

### 3.8 `proyecto1/scripts/diag/dla2_decision_diag.py` v1.3

**Versión:** 1.2 → 1.3.

**Cambios:** ampliación con 4 fases nuevas (8, 9, 10, 11) y CSV inventario de 18 → 27 columnas. Las funciones puras de detección viven en módulo separado (R-7.4):

**Nuevo módulo:** `proyecto1/scripts/diag/cost_format_signals.py`

```python
"""
cost_format_signals.py — funciones PURAS de detección de formato KID
y patologías de falsos positivos en costes. Aisladas para R-7.4.

NO importa de pipeline.py, core.io, proyecto1.* — solo stdlib + re.
"""
import re
from typing import Optional

# ----------------------------------------------------------------------
# Patrones de detección de formato KID (BL-COST-1 fase 8)
# ----------------------------------------------------------------------

PRIIPS_SIGNALS_STRONG = [
    # Encabezados oficiales PRIIPs
    r'documento de datos fundamentales',
    r'key information document',
    # Secciones específicas PRIIPs
    r'composici[óo]n de los costes',
    r'composition of costs',
    r'costes a lo largo del tiempo',
    r'costs over time',
    r'incidencia anual de los costes',
    r'annual cost impact',
    # Escenarios PRIIPs Cat. 3
    r'escenarios? de rentabilidad',
    r'performance scenarios',
    r'per[ií]odo de mantenimiento recomendado',
    r'recommended holding period',
]

UCITS_SIGNALS_STRONG = [
    r'datos fundamentales para el inversor',
    r'key investor information',
    r'gastos corrientes',
    r'entry charge.{0,80}exit charge.{0,80}ongoing charge',
    r'comisi[óo]n de entrada.{0,200}comisi[óo]n de salida'
    r'.{0,200}comisi[óo]n de gesti[óo]n',
]

EUR_VALUES_NEAR_COSTS_PATTERN = (
    r'(?:costes?\s+de\s+entrada|costes?\s+de\s+salida|'
    r'entry\s+costs?|exit\s+costs?|management\s+fees?|'
    r'comisi[óo]n\s+de\s+gesti[óo]n)'
    r'.{0,200}?'
    r'(?:\d{1,5})\s*(?:EUR|USD|€|\$)'
)


def detect_kid_format(text: str) -> str:
    """
    Score-based detection del formato regulatorio del KID.

    Returns: 'PRIIPS_KID' | 'UCITS_KIID' | 'UNKNOWN'

    Lógica:
    - >=3 señales PRIIPs strong + >=1 valor EUR cerca de costes → PRIIPS_KID
    - >=2 señales UCITS strong + 0 señales PRIIPs strong → UCITS_KIID
    - Resto → UNKNOWN
    """
    if not text:
        return 'UNKNOWN'
    priips_hits = sum(1 for p in PRIIPS_SIGNALS_STRONG if re.search(p, text, re.I))
    ucits_hits = sum(1 for p in UCITS_SIGNALS_STRONG if re.search(p, text, re.I))
    eur_hits = len(re.findall(EUR_VALUES_NEAR_COSTS_PATTERN, text, re.I | re.S))

    if priips_hits >= 3 and eur_hits >= 1:
        return 'PRIIPS_KID'
    if ucits_hits >= 2 and priips_hits == 0:
        return 'UCITS_KIID'
    return 'UNKNOWN'


# ----------------------------------------------------------------------
# Detección de falsos positivos en Entry_Fee_Pct=0 (BL-COST-1 fase 9)
# ----------------------------------------------------------------------

ENTRY_FEE_NONZERO_SIGNALS = [
    # % explícito en descripción (capturando el valor para excluir 0,00%)
    (r'(\d+[,.]\d+)\s*%\s*(?:m[áa]ximo\s+)?del\s+importe.{0,80}entrada', 'pct_inline'),
    (r'(\d+[,.]\d+)\s*%\s*(?:m[áa]ximo\s+)?del\s+(?:importe|valor)\s+(?:que\s+)?(?:pagar|invertir)', 'pct_inline'),
    # Valor EUR/USD ≥10 en línea de entry costs
    (r'costes?\s+de\s+entrada.{0,200}?(\d{2,5})\s*(?:EUR|USD|€|\$)', 'eur_value'),
    (r'entry\s+costs?.{0,200}?(\d{2,5})\s*(?:EUR|USD|€|\$)', 'eur_value'),
    # Frase condicional: máximo / hasta / could be up to
    (r'(?:m[áa]ximo|hasta\s+el|up\s+to)\s+(\d+[,.]?\d*)\s*%.{0,80}entrada', 'pct_max'),
]


def detect_entry_fee_false_positive(text: str, entry_fee_db: Optional[float]) -> dict:
    """
    Para fondos con entry_fee_db == 0.0, evalúa si hay evidencia textual
    de fee no-cero (falso positivo).

    Returns:
        {'is_suspect': bool, 'signal_count': int, 'signals_matched': list[(pattern, value)]}
    """
    if entry_fee_db != 0.0:
        return {'is_suspect': False, 'signal_count': 0, 'signals_matched': []}

    matched = []
    for pat, tag in ENTRY_FEE_NONZERO_SIGNALS:
        for m in re.finditer(pat, text, re.I | re.S):
            captured = m.group(1) if m.groups() else ''
            try:
                val = float(captured.replace(',', '.')) if captured else 0.0
            except (ValueError, IndexError):
                val = 0.0
            # Excluir señales con valor 0 (no son falsos positivos)
            if val == 0.0:
                continue
            matched.append((tag, val))

    return {
        'is_suspect': len(matched) > 0,
        'signal_count': len(matched),
        'signals_matched': matched,
    }


# ----------------------------------------------------------------------
# Detección de falsos positivos en Exit_Fee_Pct=0 (BL-COST-1 fase 10)
# ----------------------------------------------------------------------

EXIT_FEE_NONZERO_SIGNALS = [
    (r'(\d+[,.]\d+)\s*%\s*(?:m[áa]ximo\s+)?del\s+importe.{0,80}salida', 'pct_inline'),
    (r'costes?\s+de\s+salida.{0,200}?(\d{2,5})\s*(?:EUR|USD|€|\$)', 'eur_value'),
    (r'exit\s+costs?.{0,200}?(\d{2,5})\s*(?:EUR|USD|€|\$)', 'eur_value'),
]


def detect_exit_fee_false_positive(text: str, exit_fee_db: Optional[float]) -> dict:
    """Análogo a detect_entry_fee_false_positive."""
    if exit_fee_db != 0.0:
        return {'is_suspect': False, 'signal_count': 0, 'signals_matched': []}
    matched = []
    for pat, tag in EXIT_FEE_NONZERO_SIGNALS:
        for m in re.finditer(pat, text, re.I | re.S):
            captured = m.group(1) if m.groups() else ''
            try:
                val = float(captured.replace(',', '.')) if captured else 0.0
            except (ValueError, IndexError):
                val = 0.0
            if val == 0.0:
                continue
            matched.append((tag, val))
    return {
        'is_suspect': len(matched) > 0,
        'signal_count': len(matched),
        'signals_matched': matched,
    }


# ----------------------------------------------------------------------
# Detección de sospecha OC ≠ TER real (BL-COST-1 fase 11)
# ----------------------------------------------------------------------

ACI_PATTERN_ES = r'incidencia\s+anual\s+de\s+los\s+costes.{0,80}?(\d+[,.]\d+)\s*%'
ACI_PATTERN_EN = r'annual\s+cost\s+impact.{0,80}?(\d+[,.]\d+)\s*%'


def detect_oc_aci_gap(text: str, oc_db: Optional[float]) -> dict:
    """
    Detecta si el valor de Ongoing_Charge en BD parece ser realmente un
    "Annual Cost Impact" (ACI) que incluye amortización de one-offs, no
    el TER recurrente puro.

    Heurística: si en el texto hay "Annual cost impact: X%" y ese X% coincide
    con oc_db (tolerancia 0.1pp), entonces oc_db está mal etiquetado.

    Returns:
        {'is_suspect': bool, 'aci_values_found': list[float],
         'oc_db': float, 'min_gap': float}
    """
    if oc_db is None:
        return {'is_suspect': False, 'aci_values_found': [],
                'oc_db': None, 'min_gap': None}

    aci_values = []
    for pat in (ACI_PATTERN_ES, ACI_PATTERN_EN):
        for m in re.finditer(pat, text, re.I | re.S):
            try:
                val = float(m.group(1).replace(',', '.'))
                aci_values.append(val)
            except (ValueError, IndexError):
                continue

    if not aci_values:
        return {'is_suspect': False, 'aci_values_found': [],
                'oc_db': oc_db, 'min_gap': None}

    min_gap = min(abs(oc_db - v) for v in aci_values)
    is_suspect = min_gap < 0.1  # 10 basis points

    return {
        'is_suspect': is_suspect,
        'aci_values_found': aci_values,
        'oc_db': oc_db,
        'min_gap': min_gap,
    }
```

**Modificaciones en `dla2_decision_diag.py` v1.3:**

Añadir 4 fases nuevas tras la Fase 7 existente:

- **Fase 8:** Distribución de `KID_Format` sobre el corpus.
- **Fase 9:** Falsos positivos `Entry_Fee_Pct=0`.
- **Fase 10:** Falsos positivos `Exit_Fee_Pct=0`.
- **Fase 11:** Sospecha OC ↔ ACI gap en `Ongoing_Charge_Recurrent`.

Lectura: cada fondo lee `Raw_KIID_Text + DLA2_Table_Text` desde `fund_kiid_metadata`, aplica las funciones puras de `cost_format_signals.py`, agrega resultados.

**CSV inventario ampliado** (`dla2_table_inventory.csv` v1.3, 18 → 27 columnas):

```
+ kid_format_inferred         (TEXT: PRIIPS_KID|UCITS_KIID|UNKNOWN)
+ priips_signals_count        (INT)
+ ucits_signals_count         (INT)
+ eur_near_costs_count        (INT)
+ entry_fee_suspect           (INT: 0/1)
+ entry_fee_suspect_signals   (INT)
+ exit_fee_suspect            (INT: 0/1)
+ exit_fee_suspect_signals    (INT)
+ oc_aci_gap_suspect          (INT: 0/1)
```

**Nueva decisión Go/No-Go Sprint 2:**

```
DECISIÓN SPRINT 2 (post-DIAG v1.3) — umbrales actualizados:

CRITERIO A (cobertura, ya existente):
  Cat. 2 prevalencia REAL ≥40% → adelante con extractor

CRITERIO B (calidad, NUEVO):
  % falsos positivos detectados ≥3% del corpus → BLOQUEANTE
                                                  (router obligatorio Sprint 2)
  % falsos positivos detectados <1% → diferible

CRITERIO C (formato, NUEVO):
  % PRIIPS_KID ≥80% del corpus → router obligatorio
  % PRIIPS_KID 30-80% → router recomendado
  % PRIIPS_KID <30% → reconsiderar alcance Sprint 2

GO Sprint 2 si A y (B≥3% o C≥80%).
```

### 3.9 Archivos NO modificados en Sprint 1

Para acotar el scope y prevenir regresiones:

- `kiid_parser.py`: NO se toca. El parser sigue extrayendo lo que extrae hoy. Sprint 2 lo refactoriza.
- `fund_characterizer.py`: NO se toca.
- `blocks/*.py`: NO se toca.
- `srri_*.py`: NO se toca.
- `core/io.py` y `core/dla_table_serializer.py`: NO se tocan.
- `core/dla_extractor.py`: NO se toca.

---

## 4. Convención de logging (Norma 7.2 + 7.3)

### 4.1 Tags reservados para BL-COST en backlog

| Tag | Nivel | Uso |
|---|---|---|
| `[BL-COST-1]` | INFO | Fase de diagnóstico ejecutada (un fondo, una fase) |
| `[BL-COST-1-FP-ENTRY]` | WARNING | Falso positivo detectado en Entry_Fee_Pct=0 |
| `[BL-COST-1-FP-EXIT]` | WARNING | Falso positivo detectado en Exit_Fee_Pct=0 |
| `[BL-COST-1-OC-GAP]` | WARNING | Sospecha OC = ACI mal etiquetado |
| `[BL-COST-1-FORMAT]` | INFO | KID_Format inferido (PRIIPS_KID/UCITS_KIID/UNKNOWN) |
| `[BL-COST-2-MIG]` | INFO | Migración v18→v19 (logs del script de migración) |
| `[BL-COST-2-CHECK]` | ERROR | check_schema_v19 detecta inconsistencia |

**Ejemplos de formato (Norma 7.3):**

```
[BL-COST-1-FORMAT] LU0006277684 KID_Format=PRIIPS_KID (priips=5, ucits=0, eur=3)
[BL-COST-1-FP-ENTRY] LU1084165304 Entry_Fee_Pct=0 sospechoso (3 señales): pct_inline=5.25, eur_value=510
[BL-COST-1-OC-GAP] IE0032875985 OC_Recurrent=0.024 coincide con ACI@1Y=0.057 (gap=0.033). Posible mal etiquetado.
[BL-COST-2-MIG] 3205 filas preservadas. 11 columnas nuevas inicializadas NULL.
[BL-COST-2-CHECK] ERROR-Schema: columna Ongoing_Charge sigue presente — RENAME no aplicado.
```

### 4.2 Resumen de ciclo obligatorio (Norma 7.5)

El diagnóstico v1.3 DEBE emitir al final:

```
--- RESUMEN BL-COST-1 DIAGNOSTIC v1.3 ---
[INFO] BL-COST-1-FORMAT PRIIPS_KID: N1 fondos (P1%)
[INFO] BL-COST-1-FORMAT UCITS_KIID: N2 fondos (P2%)
[INFO] BL-COST-1-FORMAT UNKNOWN:    N3 fondos (P3%)
[WARN] BL-COST-1-FP-ENTRY: N4 fondos sospechosos
[WARN] BL-COST-1-FP-EXIT:  N5 fondos sospechosos
[WARN] BL-COST-1-OC-GAP:   N6 fondos sospechosos
---
```

---

## 5. Reglas anti-regresión y restricciones (R-1 a R-8)

### 5.1 R-1 Punto único de normalización lingüística

**Impacto:** los nuevos valores categóricos (`KID_Format`, `Cost_Extraction_Quality`) están en inglés UPPERCASE. **NO requieren** entrar en los mapas EN→ES porque ya están en el idioma objetivo.

**Acción:** añadirlos a `ALLOWED_VALUES_BY_COLUMN` en `classify_utils.py` (sección 3.7.1). NO añadirlos a `_normalize_record` ni a `_post_upsert_normalize_db` de `sqlite_writer.py`.

### 5.2 R-2 Triple acción para atributos persistidos

**Sprint 1 no añade reglas INTER que consuman los nuevos atributos**, así que la triple acción no aplica completamente. Sin embargo, el principio se cumple:

- (1) Fix del classifier/characterizer: **Sprint 2** lo aborda.
- (2) Fix de pipeline.py reglas INTER: **Sprint 2** lo aborda.
- (3) Migración SQL one-shot: **Sprint 1 §2.4** la entrega para el cambio de schema.

Sprint 1 cumple R-2 para la modificación que hace (renombrado de columna), aunque la modificación parcial deja las nuevas columnas NULL.

### 5.3 R-3 _v3_row ampliado

**Cumplido en §3.6.** Se añaden `KID_Format` y `Cost_Extraction_Quality` a la query `_v3_row`.

### 5.4 R-4 Valores efectivos

**Sprint 1 no añade reglas INTER**, pero documenta inline (§3.6) el patrón que Sprint 2 deberá usar:

```python
_kf_efectivo = fund_master_record.get("KID_Format") or _kf_bd
_oc_rec_efectivo = fund_master_record.get("Ongoing_Charge_Recurrent") or _oc_rec_bd
_aci_rhp_efectivo = fund_master_record.get("ACI_RHP") or _aci_rhp_bd
```

### 5.5 R-5 Word boundary

**No aplica** a Sprint 1 (no se escriben regex sobre nombres de fondos; solo sobre texto KIID).

### 5.6 R-6 Ventana acotada para inferencias

**Aplica a `cost_format_signals.py`**: las patrones `EUR_VALUES_NEAR_COSTS_PATTERN` y los de detección de falsos positivos usan ventana acotada (`.{0,200}?` lazy). No hay verificación global "ausencia de keyword".

### 5.7 R-7 Tests obligatorios

**Cumplido en §6.**

### 5.8 R-8 AST validation tras cada edit

**Procedimiento obligatorio post-edit (Sonnet en sesión Nivel-2):**

```bash
python -c "import ast; ast.parse(open('shared/config.py').read()); print('AST OK shared/config.py')"
python -c "import ast; ast.parse(open('shared/init_db.py').read()); print('AST OK shared/init_db.py')"
python -c "import ast; ast.parse(open('shared/schema_checks.py').read()); print('AST OK shared/schema_checks.py')"
python -c "import ast; ast.parse(open('proyecto1/sqlite_writer.py').read()); print('AST OK sqlite_writer.py')"
python -c "import ast; ast.parse(open('proyecto1/pipeline.py').read()); print('AST OK pipeline.py')"
python -c "import ast; ast.parse(open('proyecto1/classify_utils.py').read()); print('AST OK classify_utils.py')"
python -c "import ast; ast.parse(open('proyecto1/scripts/diag/dla2_decision_diag.py').read()); print('AST OK dla2_decision_diag.py')"
python -c "import ast; ast.parse(open('proyecto1/scripts/diag/cost_format_signals.py').read()); print('AST OK cost_format_signals.py')"
python -c "import ast; ast.parse(open('scripts/mig/migrate_v18_to_v19.py').read()); print('AST OK migrate.py')"
```

Para SQL: `sqlite3 :memory: < proyecto1/db/schema_fondos.sql` debe terminar con exit code 0.

---

## 6. Test suite (R-7 + P-4)

### 6.1 Tests aislados (R-7.4)

**Archivo:** `tests/test_cost_format_signals.py`

Importa ÚNICAMENTE de `proyecto1.scripts.diag.cost_format_signals`. NO importa de pipeline, io, ni shared (este último no es estrictamente necesario para los tests, evitamos dependencia).

```python
"""
Test suite BL-COST-1 — cost_format_signals.
R-7.4: imports aislados, sin pipeline ni IO ni proyecto1.* dependencies.
"""
import pathlib
import pytest

# Único import del proyecto:
from proyecto1.scripts.diag.cost_format_signals import (
    detect_kid_format,
    detect_entry_fee_false_positive,
    detect_exit_fee_false_positive,
    detect_oc_aci_gap,
)

SAMPLE_DIR = pathlib.Path(__file__).parent / "fixtures" / "kid_samples"
# Los .txt se generan extrayendo del PDF muestra con pdfplumber en setup.

# ---- Tabla de verdad: análisis manual sesión Opus 18-may-2026 ----

EXPECTED_FORMAT = {
    "IE00BJGT6Q17": "PRIIPS_KID",   # PIMCO Multi-Asset
    "LU1084165304": "PRIIPS_KID",   # Fidelity World Fund A-ACC-USD
    "IE0032875985": "PRIIPS_KID",   # PIMCO Global Bond
    "IE00B45H7020": "PRIIPS_KID",   # iShares ultrashort bond
    "FR0000989626": "PRIIPS_KID",   # Groupama monetario
    "LU0135992385": "PRIIPS_KID",   # Schroders
    "IE00BZ4D7085": "PRIIPS_KID",   # Polar Capital Global Tech
    "LU1502282632": "PRIIPS_KID",   # Candriam
}

EXPECTED_ENTRY_FEE_SUSPECT = {
    "IE00BJGT6Q17": False,  # Entry real 0 EUR → correctamente 0
    "LU1084165304": True,   # 5,25% / 510 USD → falso positivo (CASO PARADIGMÁTICO)
    "IE0032875985": True,   # 497 EUR → falso positivo (CASO PARADIGMÁTICO)
    "IE00B45H7020": False,  # "no cobramos" → correctamente 0
    "FR0000989626": True,   # 0,50% / 50 € → falso positivo
    "LU0135992385": False,  # "No cobramos" + EUR 0 → correctamente 0
    "IE00BZ4D7085": False,  # 0,00% hoy + "5% futuro" → no es FP estricto
                            # (el 5% va a Entry_Fee_Pct_Max, NO contradice 0% actual)
    "LU1502282632": True,   # 3,50% máximo + Hasta 350 EUR → falso positivo
}

EXPECTED_EXIT_FEE_SUSPECT = {
    "IE00BJGT6Q17": True,   # 197 EUR → falso positivo
    # resto: todos "no cobramos" o explícito 0
    "LU1084165304": False, "IE0032875985": False, "IE00B45H7020": False,
    "FR0000989626": False, "LU0135992385": False, "IE00BZ4D7085": False,
    "LU1502282632": False,
}

EXPECTED_OC_GAP_SUSPECT = {
    "IE00BJGT6Q17": False,  # OC y ACI distintos lo suficiente (sin gap)
    "LU1084165304": False,
    "IE0032875985": True,   # OC_BD=2.4% coincide con ACI@1Y=5.7% / ACI@3Y=2.4% — sospecha
    "IE00B45H7020": False,
    "FR0000989626": False,
    "LU0135992385": False,
    "IE00BZ4D7085": False,
    "LU1502282632": False,
}


@pytest.mark.parametrize("isin,expected", EXPECTED_FORMAT.items())
def test_detect_kid_format(isin, expected):
    text = (SAMPLE_DIR / f"{isin}.txt").read_text(encoding="utf-8")
    actual = detect_kid_format(text)
    assert actual == expected, (
        f"{isin}: esperado {expected}, obtenido {actual}"
    )


@pytest.mark.parametrize("isin,expected", EXPECTED_ENTRY_FEE_SUSPECT.items())
def test_detect_entry_fee_false_positive(isin, expected):
    text = (SAMPLE_DIR / f"{isin}.txt").read_text(encoding="utf-8")
    result = detect_entry_fee_false_positive(text, entry_fee_db=0.0)
    assert result["is_suspect"] == expected, (
        f"{isin}: esperado is_suspect={expected}, got {result}"
    )


@pytest.mark.parametrize("isin,expected", EXPECTED_EXIT_FEE_SUSPECT.items())
def test_detect_exit_fee_false_positive(isin, expected):
    text = (SAMPLE_DIR / f"{isin}.txt").read_text(encoding="utf-8")
    result = detect_exit_fee_false_positive(text, exit_fee_db=0.0)
    assert result["is_suspect"] == expected, (
        f"{isin}: esperado {expected}, got {result}"
    )


def test_detect_oc_aci_gap_ie0032875985():
    """Caso paradigmático: OC en BD = 2.4% coincide con ACI@3Y = 2.4%."""
    text = (SAMPLE_DIR / "IE0032875985.txt").read_text(encoding="utf-8")
    result = detect_oc_aci_gap(text, oc_db=0.024)
    assert result["is_suspect"] is True
    # ACI@3Y debe estar entre los valores detectados (2.4%)
    assert any(abs(v - 2.4) < 0.05 for v in result["aci_values_found"])


def test_detect_kid_format_empty():
    assert detect_kid_format("") == "UNKNOWN"
    assert detect_kid_format(None) == "UNKNOWN"


def test_detect_entry_fee_false_positive_with_value():
    """Si entry_fee_db != 0, NO debe marcarse sospechoso."""
    text = "5,25% del importe que pagará usted al realizar esta inversión. 510 USD"
    result = detect_entry_fee_false_positive(text, entry_fee_db=5.25)
    assert result["is_suspect"] is False
```

### 6.2 Smoke test post-deployment (P-4)

**Archivo:** `tests/smoke_v19_deployment.py`

Se ejecuta DESPUÉS de aplicar la migración sobre BD producción.

```python
"""
Smoke test post-deployment v19.
Verifica que el pipeline puede leer/escribir contra la BD migrada.
"""
import sqlite3
from shared.config import DB_PATH
from shared.schema_checks import check_schema_v19


def main():
    conn = sqlite3.connect(DB_PATH)

    # 1. Schema correcto
    res = check_schema_v19(conn)
    assert res['ok'], f"Schema inválido: {res['issues']}"
    print("[OK] Schema v19 verificado")

    # 2. Datos preservados
    n = conn.execute("SELECT COUNT(*) FROM fund_master").fetchone()[0]
    assert n >= 3200, f"Pérdida de datos detectada: {n} filas"
    print(f"[OK] {n} filas preservadas en fund_master")

    # 3. Ongoing_Charge_Recurrent poblado en los fondos que tenían OC en v18
    pob = conn.execute(
        "SELECT COUNT(*) FROM fund_master WHERE Ongoing_Charge_Recurrent IS NOT NULL"
    ).fetchone()[0]
    assert pob >= 3000, f"Poblamiento OC_Recurrent insuficiente: {pob}"
    print(f"[OK] {pob} fondos con Ongoing_Charge_Recurrent poblado")

    # 4. Columnas v19 todas NULL (Sprint 1: terreno preparado, no poblado)
    cnts = conn.execute(
        "SELECT "
        "SUM(KID_Format IS NULL), "
        "SUM(Cost_Extraction_Quality IS NULL), "
        "SUM(ACI_1Y IS NULL), "
        "SUM(ACI_RHP IS NULL) "
        "FROM fund_master"
    ).fetchone()
    assert all(c == n for c in cnts), (
        f"Columnas v19 no están todas NULL: {cnts} vs {n}"
    )
    print(f"[OK] Columnas v19 todas NULL ({n} fondos)")

    # 5. fund_cost_schedule existe y vacía
    c = conn.execute("SELECT COUNT(*) FROM fund_cost_schedule").fetchone()[0]
    assert c == 0, f"fund_cost_schedule debería estar vacía: {c} filas"
    print("[OK] fund_cost_schedule vacía (esperado en Sprint 1)")

    # 6. Lectura simulando lo que hace pipeline.py
    row = conn.execute(
        "SELECT Investment_Universe, Accumulation_Policy, Currency_Hedged, "
        "Investment_Focus, Credit_Quality, Geography, Fund_Nature, "
        "KID_Format, Cost_Extraction_Quality "
        "FROM fund_master LIMIT 1"
    ).fetchone()
    assert row is not None
    print("[OK] Query _v3_row ampliada funciona")

    print("\n=== SMOKE TEST v19 PASS ===")


if __name__ == "__main__":
    main()
```

### 6.3 Cobertura de tests por requisito

| Requisito | Test que lo cubre |
|---|---|
| R-7.1 casos positivos | `test_detect_kid_format` (8 fondos PRIIPS) |
| R-7.2 casos control negativos | `test_detect_entry_fee_false_positive` (4 fondos correctamente 0) |
| R-7.3 casos regresión | Los 3 casos paradigmáticos del backlog (LU1084165304, IE0032875985, FR0000989626) |
| R-7.4 aislamiento | `cost_format_signals.py` no importa nada de proyecto |
| R-8 AST | Todos los módulos modificados (§5.8) |
| P-4 smoke test | `smoke_v19_deployment.py` |

---

## 7. Plan de ejecución Sprint 1

### 7.1 Orden estricto de tareas (cada una con AST + tests + commit)

| # | Tarea | Módulos | Verificación |
|---|---|---|---|
| 1 | Verificar BL-DLA-2 Sub-fase 2B cerrada (pre-requisito) | (operativo José) | DLA2_Table_Text populated en >0 fondos en BD |
| 2 | Implementar `cost_format_signals.py` (módulo puro) | nuevo | AST OK + suite tests pasa 8/8 |
| 3 | Implementar `dla2_decision_diag.py` v1.3 (fases 8/9/10/11) | `scripts/diag/` | AST OK + ejecución log v1.3 limpia |
| 4 | Ejecutar diagnóstico v1.3 sobre BD producción (lectura) | (operativo) | Log v1.3 + CSV v1.3 emitidos |
| 5 | Revisar resultados Fase 8/9/10/11 con José | (sesión) | Decisión informada Go/NO-Go Sprint 2 |
| 6 | Backup `fondos.sqlite` → `fondos_pre_v19_TIMESTAMP.sqlite` | (operativo) | Archivo backup en disco |
| 7 | Actualizar `schema_fondos.sql` con DDL v19 completo | `db/` | sqlite :memory: ejecuta sin error |
| 8 | Actualizar `shared/config.py` con constantes v19 | `shared/` | AST OK + import-test |
| 9 | Actualizar `shared/init_db.py` con `create_schema_v19()` | `shared/` | AST OK |
| 10 | Actualizar `shared/schema_checks.py` con `check_schema_v19()` | `shared/` | AST OK + test contra BD memoria |
| 11 | Actualizar `sqlite_writer.py` (INSERT + ON CONFLICT + params + nueva función) | `proyecto1/` | AST OK + smoke contra BD memoria |
| 12 | Actualizar `pipeline.py` (_v3_row R-3 + renombrar `Ongoing_Charge`) | `proyecto1/` | AST OK |
| 13 | Actualizar `classify_utils.py` (ALLOWED_VALUES_BY_COLUMN) | `proyecto1/` | AST OK |
| 14 | Crear `scripts/mig/migrate_v18_to_v19.py` | nuevo | AST OK + idempotencia (correr 2 veces) |
| 15 | Ejecutar migración sobre BD producción | (operativo) | Smoke test v19 PASS |
| 16 | Ejecutar pipeline completo (1 bloque MONETARIOS, 30 fondos) | (operativo) | Sin regresión en columnas v18 |
| 17 | Verificación SQL §2.4 + smoke deployment | (operativo José) | Todas las queries devuelven esperado |
| 18 | Actualizar `ESTADO_BACKLOG_APR2026.md` cerrando BL-COST-1 y BL-COST-2 | docs | Documentación |

**Estimación operativa:** 6-8h de Sonnet ejecutando + 1-2h de revisión José tras paso 5 y tras paso 17.

### 7.2 Disparadores de roll-back

Si CUALQUIERA de los siguientes ocurre durante despliegue:

- Paso 7: SQL no se ejecuta limpiamente.
- Paso 11: tests aislados fallan en cualquier fondo.
- Paso 15: migración aborta con error.
- Paso 16: smoke test detecta regresión en valor de cualquier columna v18.
- Paso 17: cualquier query devuelve resultado inesperado.

→ Acción: restaurar backup, abortar Sprint 1, documentar la causa, replanificar.

---

## 8. Riesgos y mitigaciones (auditoría completa)

| ID | Riesgo | Probabilidad | Severidad | Mitigación |
|---|---|---|---|---|
| R1 | Migración corrompe datos | Muy baja | Alta | Backup pre-migración obligatorio. Transacción BEGIN/COMMIT en script. Verificación SQL post-migración. |
| R2 | `Ongoing_Charge_Recurrent` causa regresión en consumers no identificados | Media | Media | Grep exhaustivo del proyecto por `Ongoing_Charge` antes de la migración. Listar TODOS los consumers. Si hay módulos P2/P3 que lo consumen, coordinar update. |
| R3 | Heurística `detect_kid_format` clasifica mal en corpus | Media | Baja | Tests sobre 8/8 PDFs validan. Sprint 1 es solo informativo (no actúa sobre los valores). Falsa clasificación se detecta en Sprint 2. |
| R4 | Heurística falsos positivos genera ruido excesivo | Media | Baja | KPI informativo. La decisión de actuar se toma tras revisar el log con José (paso 5). |
| R5 | R-3 _v3_row ampliada provoca re-characterize masivo (3.205 fondos) en próximo ciclo | Alta | Media | **Esperado.** Sprint 2 necesita esa re-characterize para poblar campos. En Sprint 1, si se ejecuta el pipeline, será más lento. Documentado. |
| R6 | Falta de FK genera huérfanos en `fund_cost_schedule` si se borra un fondo de `fund_master` | Baja | Baja | Contrato del writer. En Sprint 2, `upsert_cost_schedule` solo se llama desde el pipeline después del upsert al master. |
| R7 | Sprint 2 entrega tarde y el corpus queda con columnas NULL indefinidamente | Media | Baja | Las columnas NULL son válidas (`Cost_Extraction_Quality=NONE` semánticamente). El pipeline en producción no degrada. |
| R8 | El renombrado de `Ongoing_Charge` rompe queries SQL ad-hoc en notebooks o dashboards externos | Alta | Media | Documentar el cambio en notes de release. Comunicar a stakeholders P2/P3. |
| R9 | `kiid_parser.py` produce `parsed["Ongoing_Charge"]` y pipeline ahora lo mapea a `"Ongoing_Charge_Recurrent"` — semánticamente confuso temporalmente | Media | Baja | **Aceptado**. Durante Sprint 1, el VALOR no cambia, solo el nombre de columna. Sprint 2 desambigua semánticamente y el parser entregará TER puro vs ACI separados. |
| R10 | Idempotencia de migración falla si se ejecuta 2 veces | Baja | Media | `detect_schema_version` al inicio retorna 19 → SKIP. |

---

## 9. Lo que NO se hace en Sprint 1 (alcance explícito)

Para prevenir scope creep:

- ❌ No se implementa `cost_format_router.py` (Sprint 2 = BL-COST-3).
- ❌ No se implementa `priips_cost_extractor.py` ni `ucits_cost_extractor.py` (Sprint 2 = BL-COST-4).
- ❌ No se pueblan las 11 columnas nuevas con datos reales (Sprint 2).
- ❌ No se pueblan filas en `fund_cost_schedule` (Sprint 2).
- ❌ No se modifican los detectores existentes de `kiid_parser.py`.
- ❌ No se cambia el comportamiento funcional del pipeline (es transparente, salvo el efecto colateral R5 de re-characterize por R-3).
- ❌ No se modifican las validaciones semánticas existentes en `classify_utils.py` (Sprint 2 añade INTER-COST-1/2/3).
- ❌ No se tocan los módulos `core/io.py`, `core/dla_extractor.py`, `core/dla_table_serializer.py`.

---

## 10. Sprint 2 — preview (NO compromiso vinculante)

| BL | Tarea Sprint 2 | Estimación |
|---|---|---|
| BL-COST-3 | `cost_format_router.py` con tests | ~2h |
| BL-COST-4a | `priips_cost_extractor.py` con cross-validation %↔EUR | ~6h |
| BL-COST-4b | `ucits_cost_extractor.py` (refactor del legacy actual) | ~2h |
| BL-COST-4c | Integración en pipeline.py con kill-switch `PRIIPS_COST_EXTRACTION_ENABLED` | ~1h |
| BL-COST-4d | Lógica derivación cache `ACI_1Y`/`ACI_RHP` desde `fund_cost_schedule` en sqlite_writer | ~1h |
| BL-COST-5 | Reglas INTER-COST-1/2/3 en `classify_utils.py` con tags `[BL-COST-5-OC-ACI]` etc. + _X_bd ampliados en pipeline.py | ~3h |
| BL-COST-6 | Re-ejecución pipeline completo + reporte cambios | ~2h |
| **Total Sprint 2** | | **~17h** |

**Total programa BL-COST (Sprints 1+2):** ~25h efectivas.

---

## 11. Checklist de aprobación Sprint 1

Antes de abrir la sesión Sonnet de implementación, José confirma:

- [ ] Decisiones arquitectónicas §1 (las 6) aceptadas.
- [ ] Lista de 11 columnas + dominios §2.2 aceptada.
- [ ] DDL completo §2.3 aceptado (incluyendo CHECK constraints, ausencia FK formal).
- [ ] Política UPSERT por columna §2.2 (sobrescritura para `KID_Format`, COALESCE resto) aceptada.
- [ ] Renombrado `Ongoing_Charge → Ongoing_Charge_Recurrent` aceptado.
- [ ] Estrategia migración export→drop→recreate §2.4 aceptada.
- [ ] Convención valores `Cost_Extraction_Quality` paralela a SRRI_Quality_Flag §1 (Decisión 6) aceptada.
- [ ] Cambios a `pipeline.py` (R-3 ampliación _v3_row) §3.6 aceptados — implicación: próximo ciclo re-characterize 3.205 fondos.
- [ ] Tags de logging §4.1 aceptados.
- [ ] Backup `fondos.sqlite` realizado antes de paso 15.
- [ ] Stakeholders P2/P3 (si los hay) informados del renombrado.
- [ ] Confirmar BL-DLA-2 Sub-fase 2B cerrada con DLA2_Table_Text populated.

---

## 12. Bibliografía (documentos del proyecto referenciados)

- `CUSTOM_INSTRUCTIONS_V3_PERFECTO.md` (v3.0, 9 abr 2026) — Principios #1, #2, #8, #9.
- `PRINCIPIOS_DISENO.md` (30 abr 2026) — Principios #1 COALESCE, #3 verificar ficheros, Norma 7 logging v2.
- `RESTRICCIONES_ARQUITECTURA.md` (v1.0, 25 abr 2026) — R-1 a R-8, P-1 a P-4.
- `WORKFLOWS_ESTRUCTURADOS.md` — Norma 5.4 autocontenido, flujo Opus→Sonnet, NIVEL-2.
- `BL_DLA_DESIGN_DECISION.md` (v1.1) — contexto de BL-DLA-2 sub-fases 2A/2B.
- `INCIDENCIA_CRITICA_DLA2_ANALISIS.md` (10 may 2026) — cat_max bug que BL-DLA-2-LOGIC-FIX resolvió.
- `ESTADO_BACKLOG_APR2026_v3_9.md` — backlog vigente al inicio de Sprint 1.

---

**FIN DEL SPEC BL-COST-1+2 v2.**

*Documento autocontenido conforme a Norma 5.4 (backlog v3.4).*
*Auditoría sistemática Opus realizada el 19 may 2026 cubriendo R-1 a R-8 + Principios #1 #2 #8 #9 + Norma 7 logging v2.*
*Aprobación pendiente de José sobre los checklist items §11.*
