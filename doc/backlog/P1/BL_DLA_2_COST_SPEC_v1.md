# BL-DLA-2-COST — Especificación operativa Sprint 1 (DIAG + Schema v19)

**Tipo:** Especificación arquitectónica + plan operativo Nivel-3 — **lista para implementación Nivel-2**
**Fecha:** 18 de mayo de 2026
**Autor:** José + Claude (sesión Nivel-3, Opus)
**Estado:** APROBADA por José ("Confío en tu criterio") — 3 decisiones de modelado tomadas
**Versión:** 1.0
**Sprint:** Sprint 1 de 2 (DIAG + Schema). Sprint 2 = router + extractor PRIIPs.
**Dependencias bloqueantes:** BL-DLA-2 (Sub-fase 2B cerrada — `dla_table_serializer.py` operativo)

---

## 0. Resumen ejecutivo

**Problema raíz cuantificado en sesión Opus (18-may-2026):** El extractor de fees actual está calibrado para el formato regulatorio **UCITS_KIID** (porcentajes escalares fijos). El corpus de 3.205 fondos es mayoritariamente **PRIIPS_KID** (valores en EUR absolutos sobre 10.000 EUR de inversión + función de coste por horizonte). El diagnóstico actual mide solo NULLs (`entry_fee_null`, `exit_fee_null`, `oc_null`), ignorando dos patologías más graves:

1. **Falsos positivos silenciosos**: `Entry_Fee_Pct = 0` en BD cuando el KID dice `"5,25% / 510 USD"` (LU1084165304) o `"497 EUR"` (IE0032875985). El KPI de cobertura los marca como "extraídos correctamente".
2. **Mezcla conceptual en `Ongoing_Charge`**: el campo registra a veces el TER recurrente (gestión + admin + transaction) y a veces el "Annual cost impact" del RHP (que incluye amortización de one-offs). IE0032875985: `OC=2.4%` en BD = ACI@3Y; el TER real es `~0.70%`. Son cifras distintas con interpretaciones distintas.

**Validación empírica:** los 8 PDFs muestra analizados en sesión Opus son **8/8 PRIIPS_KID**. Lo "normal" en el corpus es PRIIPs; el extractor actual asume el caso raro.

**Impacto en P3 (capital preservation, IPC+M3):** las decisiones de scoring por coste se basan en cifras semánticamente incoherentes. Un fondo con `OC=2.4%` (=ACI_RHP) y uno con `OC=2.4%` (=TER real) no son comparables, pero hoy se tratan como si lo fueran.

**Alcance Sprint 1 (este documento):**
1. Refactorizar `dla2_decision_diag.py` v1.2 → v1.3 para medir las dos patologías nuevas.
2. Crear schema v19 con 9 columnas nuevas en `fund_master` + tabla `fund_cost_schedule` 1:N.
3. Migración DDL con preservación total de datos existentes vía COALESCE.

**FUERA del alcance Sprint 1 (Sprint 2 posterior):** implementar `cost_format_router.py` y `priips_cost_extractor.py`. Sprint 1 solo prepara el terreno y cuantifica el problema; Sprint 2 lo arregla.

**Criterio de cierre Sprint 1:**
- ✅ Nuevo diagnóstico ejecutado sobre 3.205 fondos.
- ✅ Cuantificación de % de falsos positivos en `Entry_Fee_Pct = 0`.
- ✅ Schema v19 desplegado, BD con 9 columnas nuevas + 1 tabla nueva.
- ✅ AST validation + behavioral tests pasando.
- ✅ Sin regresión en ningún atributo existente.

---

## 1. Decisiones arquitectónicas (cerradas)

### Decisión 1: Modelado híbrido de la función de coste por horizonte

**Decisión:** escalares para `ACI_1Y` y `ACI_RHP` en `fund_master` + tabla `fund_cost_schedule` 1:N para puntos adicionales.

**Razonamiento:**
- P3 consulta sistemáticamente "salida 1Y" y "salida RHP". Esos dos puntos deben ser query-friendly sin join.
- Casos con 3-5 puntos (RV largo plazo) preservan trazabilidad en la tabla normalizada.
- DRY: no se duplica información esencial. El escalar y la fila correspondiente en la tabla son la misma información; el escalar es un cache desnormalizado para consultas frecuentes.
- Operativamente: el writer puebla la tabla y replica `Horizon=1y` → `ACI_1Y`, `Horizon=RHP` → `ACI_RHP`. Una sola fuente de verdad (la tabla).

**Consecuencia operativa:** `fund_master.ACI_1Y` y `fund_master.ACI_RHP` son **derivados** de `fund_cost_schedule`. El pipeline los recalcula en cada upsert. Si hay incoherencia, la tabla manda.

### Decisión 2: Sprint 1 = Diagnóstico ampliado + Schema v19

**Decisión:** preparar el terreno (medir + esquema) antes de implementar router/extractor.

**Razonamiento:**
- Solo diagnóstico sin schema = sabemos el problema pero no podemos arreglarlo.
- Sprint completo end-to-end con solo 8 PDFs como test = compromiso prematuro sin cuantificación del corpus.
- Diagnóstico + schema = mide el problema en la *nueva* ontología (con `KID_Format` empírico) antes de implementar el extractor que esa ontología requiere.

**Consecuencia operativa:** las nuevas columnas de fund_master quedan NULL en todos los fondos al final del Sprint 1. Se poblan en Sprint 2.

### Decisión 3: Doble columna para fees condicionales

**Decisión:** `Entry_Fee_Pct` (valor aplicado hoy, puede ser 0) + `Entry_Fee_Pct_Max` (techo declarado, puede ser 5% en el caso IE00BZ4D7085).

**Razonamiento:**
- IE00BZ4D7085: `"0,00% No se aplica ninguna comisión de entrada... Sin embargo, el producto puede aplicar una comisión de entrada de hasta el 5% en el futuro"` → hoy=0, máximo=5.
- LU1502282632 (Candriam): `"3,50% máximo del importe que paga"` → hoy puede ser cualquier valor ≤3.50%, declarado 3.50%.
- Colapsar en una sola columna pierde semántica crítica: P3 necesita el valor aplicado *y* el techo para análisis worst-case.
- Simétricamente: `Exit_Fee_Pct_Max` (mismo razonamiento).

**Consecuencia operativa:** 4 columnas (Entry/Exit × Pct/Pct_Max), no 2.

---

## 2. Schema v19 — DDL

### 2.1 Cambios en `fund_master`

```sql
-- 9 columnas nuevas
ALTER TABLE fund_master ADD COLUMN KID_Format TEXT;
ALTER TABLE fund_master ADD COLUMN KID_Currency TEXT;
ALTER TABLE fund_master ADD COLUMN Cost_Extraction_Quality TEXT;
ALTER TABLE fund_master ADD COLUMN Cost_RHP_Years INTEGER;

ALTER TABLE fund_master ADD COLUMN Entry_Fee_Pct_Max REAL;
ALTER TABLE fund_master ADD COLUMN Exit_Fee_Pct_Max REAL;

ALTER TABLE fund_master ADD COLUMN Management_Fee_Pct REAL;
ALTER TABLE fund_master ADD COLUMN Transaction_Cost_Pct REAL;
ALTER TABLE fund_master ADD COLUMN Performance_Fee_Pct REAL;

-- 2 escalares desnormalizados (cache de fund_cost_schedule)
ALTER TABLE fund_master ADD COLUMN ACI_1Y REAL;
ALTER TABLE fund_master ADD COLUMN ACI_RHP REAL;

-- Total: 11 columnas. La columna existente Ongoing_Charge SE MANTIENE intacta.
-- En Sprint 2 se documentará semánticamente como "TER recurrente puro"
-- y se separará de ACI_RHP. NO renombrar en Sprint 1.
```

**Dominios de valores:**

| Columna | Valores permitidos | Default | Razón |
|---|---|---|---|
| `KID_Format` | `'UCITS_KIID'`, `'PRIIPS_KID'`, `'UNKNOWN'` | NULL | Detectado por router en Sprint 2 |
| `KID_Currency` | ISO 4217 (`'EUR'`, `'USD'`, `'GBP'`, `'CHF'`...) | NULL | Moneda en que se expresan los costes EUR-equivalentes |
| `Cost_Extraction_Quality` | `'HIGH_CROSS_VALIDATED'`, `'MEDIUM_EUR_DERIVED'`, `'MEDIUM_PCT_ONLY'`, `'LOW_TEXT_ONLY'`, `'UNVERIFIED'` | NULL | Grado de confianza |
| `Cost_RHP_Years` | INTEGER ≥0 (puede ser 0 si RHP < 1 año: monetarios 3 meses → registrar 0) | NULL | RHP en años redondeados a entero (decimales en `fund_cost_schedule`) |
| `Entry_Fee_Pct_Max` | REAL ≥0, ≤25 | NULL | Techo declarado |
| `Exit_Fee_Pct_Max` | REAL ≥0, ≤25 | NULL | Techo declarado |
| `Management_Fee_Pct` | REAL ≥0, ≤10 | NULL | Solo gestión + admin |
| `Transaction_Cost_Pct` | REAL ≥0, ≤5 | NULL | Solo costes de operación internos |
| `Performance_Fee_Pct` | REAL ≥0, ≤30 | NULL | Comisión de éxito (variable) |
| `ACI_1Y` | REAL ≥0, ≤50 | NULL | Annual Cost Impact si salida a 1 año |
| `ACI_RHP` | REAL ≥0, ≤25 | NULL | Annual Cost Impact si salida al RHP |

**Decisión COALESCE-friendly:** todos los defaults son NULL. El writer usa `COALESCE(excluded.col, fund_master.col)` — nunca sobreescribe con NULL. En Sprint 2, cuando el extractor empiece a poblar, los datos quedarán protegidos contra reextracciones fallidas.

### 2.2 Tabla nueva `fund_cost_schedule`

```sql
CREATE TABLE IF NOT EXISTS fund_cost_schedule (
    ISIN                TEXT NOT NULL,
    Horizon_Years       REAL NOT NULL,          -- 0.25 = 3 meses, 1.0, 3.0, 5.0, 6.0...
    Is_RHP              INTEGER NOT NULL DEFAULT 0,  -- 1 si este horizonte es el RHP declarado
    Total_Costs_EUR     REAL,                   -- 567, 693, 1904... (sobre 10.000 EUR)
    Total_Costs_Pct     REAL,                   -- 5.67, 6.93, 19.04... acumulado
    Annual_Impact_Pct   REAL,                   -- 5.7, 2.4, 3.1... según horizonte
    Source              TEXT NOT NULL,          -- 'PRIIPS_COSTS_OVER_TIME' | 'UCITS_DERIVED' | 'MANUAL'
    Created_At          TEXT NOT NULL DEFAULT (datetime('now')),
    Updated_At          TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ISIN, Horizon_Years)
);

CREATE INDEX IF NOT EXISTS idx_cost_schedule_isin ON fund_cost_schedule(ISIN);
CREATE INDEX IF NOT EXISTS idx_cost_schedule_rhp ON fund_cost_schedule(ISIN) WHERE Is_RHP = 1;
```

**Justificación de no usar FK formal:** SQLite con `PRAGMA foreign_keys=ON` puede causar problemas en upserts masivos del pipeline actual. La integridad referencial se garantiza por contrato del writer (todo upsert en `fund_cost_schedule` requiere que el ISIN exista en `fund_master`), no por constraint declarativo. Esto es consistente con el patrón actual del proyecto.

**Justificación del índice parcial `WHERE Is_RHP=1`:** P3 consulta sistemáticamente "el ACI al RHP". El índice parcial es 10× más pequeño que un full index y cubre el caso de uso dominante.

### 2.3 Diff aplicado a `schema_fondos.sql`

El archivo `proyecto1/db/schema_fondos.sql` (no incluido en uploads pero referido en memorias) debe extenderse. Patch sugerido — bloque que se añade después de la última columna de `fund_master` y antes del cierre `);`:

```sql
    -- ============================================================
    -- v19 (BL-DLA-2-COST): atributos de coste PRIIPs/KID-aware
    -- ============================================================
    KID_Format              TEXT,         -- UCITS_KIID | PRIIPS_KID | UNKNOWN
    KID_Currency            TEXT,         -- ISO 4217
    Cost_Extraction_Quality TEXT,         -- HIGH_CROSS_VALIDATED | MEDIUM_* | LOW_TEXT_ONLY | UNVERIFIED
    Cost_RHP_Years          INTEGER,      -- RHP redondeado a entero
    Entry_Fee_Pct_Max       REAL,         -- Techo declarado entry fee
    Exit_Fee_Pct_Max        REAL,         -- Techo declarado exit fee
    Management_Fee_Pct      REAL,         -- Gestión + admin pura
    Transaction_Cost_Pct    REAL,         -- Costes operación internos
    Performance_Fee_Pct     REAL,         -- Comisión éxito (variable)
    ACI_1Y                  REAL,         -- Annual Cost Impact salida 1Y (cache)
    ACI_RHP                 REAL,         -- Annual Cost Impact salida RHP (cache)
```

(Las comas finales se ajustan según posición real.)

### 2.4 Diff aplicado a `init_db.py` y `schema_checks.py`

- `init_db.py`: cuando crea `fund_master` desde cero usa el DDL completo del SQL — ya cubierto por 2.3. Adicionalmente, debe ejecutar el `CREATE TABLE fund_cost_schedule` del bloque 2.2.
- `schema_checks.py`: amplía la lista de columnas esperadas en `fund_master` con las 11 nuevas. Añade verificación de existencia de `fund_cost_schedule` y sus índices.

**Lista canónica de columnas que `schema_checks.py` debe validar (v19):**

Las 25 columnas categóricas existentes + 15 numéricas + 8 flags + **11 nuevas v19** = **59 columnas** en `fund_master` post-v19.

### 2.5 Migración para BD existente (no destructiva)

Script `scripts/migrate_v18_to_v19.py`:

```python
"""
Migración no destructiva del schema v18 → v19.
Idempotente: detecta columnas ya creadas y las salta.

Garantías:
- 0 rows modificadas en fund_master.
- 0 columnas eliminadas o renombradas.
- ALTER ADD COLUMN puebla con NULL en todas las filas existentes (3.205).
- fund_cost_schedule queda vacía al final (se puebla en Sprint 2).
"""
import sqlite3
from shared.config import DB_PATH

NEW_COLUMNS = [
    ("KID_Format", "TEXT"),
    ("KID_Currency", "TEXT"),
    ("Cost_Extraction_Quality", "TEXT"),
    ("Cost_RHP_Years", "INTEGER"),
    ("Entry_Fee_Pct_Max", "REAL"),
    ("Exit_Fee_Pct_Max", "REAL"),
    ("Management_Fee_Pct", "REAL"),
    ("Transaction_Cost_Pct", "REAL"),
    ("Performance_Fee_Pct", "REAL"),
    ("ACI_1Y", "REAL"),
    ("ACI_RHP", "REAL"),
]

COST_SCHEDULE_DDL = """
CREATE TABLE IF NOT EXISTS fund_cost_schedule (
    ISIN              TEXT NOT NULL,
    Horizon_Years     REAL NOT NULL,
    Is_RHP            INTEGER NOT NULL DEFAULT 0,
    Total_Costs_EUR   REAL,
    Total_Costs_Pct   REAL,
    Annual_Impact_Pct REAL,
    Source            TEXT NOT NULL,
    Created_At        TEXT NOT NULL DEFAULT (datetime('now')),
    Updated_At        TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ISIN, Horizon_Years)
);
"""

INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_cost_schedule_isin ON fund_cost_schedule(ISIN);",
    "CREATE INDEX IF NOT EXISTS idx_cost_schedule_rhp ON fund_cost_schedule(ISIN) WHERE Is_RHP = 1;",
]


def existing_columns(cur, table):
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def migrate():
    conn = sqlite3.connect(DB_PATH)
    conn.isolation_level = None
    cur = conn.cursor()
    cur.execute("BEGIN")
    try:
        cols_master = existing_columns(cur, "fund_master")
        added = []
        for col, typ in NEW_COLUMNS:
            if col in cols_master:
                print(f"[SKIP] fund_master.{col} ya existe")
                continue
            cur.execute(f"ALTER TABLE fund_master ADD COLUMN {col} {typ}")
            added.append(col)
            print(f"[ADD]  fund_master.{col} ({typ})")

        cur.execute(COST_SCHEDULE_DDL)
        for stmt in INDEX_DDL:
            cur.execute(stmt)
        print(f"[OK]   fund_cost_schedule + 2 índices verificados/creados")

        cur.execute("COMMIT")
        print(f"\nMigración v18→v19 completa. Columnas añadidas: {len(added)}")
        for c in added:
            print(f"  + {c}")
    except Exception as e:
        cur.execute("ROLLBACK")
        print(f"[ERROR] migración revertida: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
```

**Verificación post-migración (control que tú ejecutas en DBeaver):**

```sql
-- Columnas en fund_master deben ser 59
SELECT COUNT(*) FROM pragma_table_info('fund_master');

-- Todas las nuevas deben ser NULL en todos los fondos
SELECT
    SUM(KID_Format IS NULL)            AS kf_null,
    SUM(KID_Currency IS NULL)          AS kc_null,
    SUM(Cost_Extraction_Quality IS NULL) AS ceq_null,
    SUM(ACI_1Y IS NULL)                AS aci1y_null,
    SUM(ACI_RHP IS NULL)               AS acirhp_null,
    COUNT(*) AS total
FROM fund_master;
-- Esperado: las 5 sumas = total = 3205

-- fund_cost_schedule debe existir y estar vacía
SELECT COUNT(*) FROM fund_cost_schedule;
-- Esperado: 0
```

---

## 3. Refactor `dla2_decision_diag.py` v1.2 → v1.3

### 3.1 Patología nueva a medir: FORMATO DEL KID (Fase 8 nueva)

**Objetivo:** clasificar cada uno de los 3.205 fondos como UCITS_KIID, PRIIPS_KID o UNKNOWN usando señales **léxicas + estructurales** sobre `Raw_KIID_Text + DLA2_Table_Text` ya en BD.

**Heurística (validada empíricamente en los 8 PDFs muestra):**

```python
PRIIPS_SIGNALS_STRONG = [
    # Encabezados oficiales
    r'documento de datos fundamentales',
    r'key information document',
    # Secciones específicas PRIIPs
    r'composici[óo]n de los costes',
    r'composition of costs',
    r'costes a lo largo del tiempo',
    r'costs over time',
    r'incidencia anual de los costes',
    r'annual cost impact',
    # Escenarios PRIIPs (Cat. 3)
    r'escenarios? de rentabilidad',
    r'performance scenarios',
    r'(?:per[ií]odo|periodo) de mantenimiento recomendado',
    r'recommended holding period',
]

UCITS_SIGNALS_STRONG = [
    r'datos fundamentales para el inversor',
    r'key investor information',
    r'^gastos\s*\n.*gastos corrientes',  # bloque "Gastos" UCITS clásico
    r'entry charge.*exit charge.*ongoing charge',  # tabla 3 filas UCITS
    r'comisi[óo]n de entrada.*comisi[óo]n de salida.*comisi[óo]n de gesti[óo]n',
]

EUR_VALUES_NEAR_COSTS_PATTERN = (
    r'(?:costes? de entrada|costes? de salida|entry costs?|exit costs?|management fees?)'
    r'.{0,200}'
    r'(?:\d{1,4})\s*(?:EUR|USD|€|\$)'
)


def detect_kid_format(text: str) -> str:
    """
    Score-based detection. Devuelve PRIIPS_KID | UCITS_KIID | UNKNOWN.

    Lógica:
    - Si >=3 señales PRIIPs strong + >=1 valor EUR cerca de costes → PRIIPS_KID
    - Si >=2 señales UCITS strong + 0 señales PRIIPs → UCITS_KIID
    - Resto → UNKNOWN
    """
    import re
    t = text.lower()
    priips_hits = sum(1 for p in PRIIPS_SIGNALS_STRONG if re.search(p, t, re.I))
    ucits_hits  = sum(1 for p in UCITS_SIGNALS_STRONG  if re.search(p, t, re.I))
    eur_hits    = len(re.findall(EUR_VALUES_NEAR_COSTS_PATTERN, t, re.I | re.S))

    if priips_hits >= 3 and eur_hits >= 1:
        return 'PRIIPS_KID'
    if ucits_hits >= 2 and priips_hits == 0:
        return 'UCITS_KIID'
    return 'UNKNOWN'
```

**Output esperado en log v1.3 Fase 8:**

```
======================================================================
  FASE 8 — Distribución de formato regulatorio del KID  (Q-DLA2-08, NUEVO v1.3)
======================================================================
  KID_Format    n_fondos  pct
  ----------------------------
  PRIIPS_KID    3120      97.4%
  UCITS_KIID    52        1.6%
  UNKNOWN       33        1.0%

  Interpretación:
    PRIIPS_KID es el formato dominante. El extractor actual está calibrado
    para UCITS_KIID. Esto explica los falsos positivos detectados en Fase 9.
```

(Las cifras esperadas son hipótesis; lo importante es que la fase EXISTE y mide.)

### 3.2 Patología nueva a medir: FALSOS POSITIVOS en `Entry_Fee_Pct = 0`

**Objetivo:** para cada fondo con `Entry_Fee_Pct = 0` en BD, comprobar si el `Raw_KIID_Text + DLA2_Table_Text` contiene señales contradictorias que sugieran que el valor real **no es 0**.

**Señales contradictorias:**

```python
ENTRY_FEE_NONZERO_SIGNALS = [
    # % explícito en descripción que NO sea 0,00%
    r'(\d+[,.]\d+)\s*%\s*(?:m[áa]ximo\s+)?del\s+importe.{0,80}entrada',
    r'(\d+[,.]\d+)\s*%\s*(?:m[áa]ximo\s+)?del\s+(?:importe|valor).{0,80}invertir',
    # Valor EUR/USD significativo (>5 EUR) en línea de entry costs
    r'costes?\s+de\s+entrada.{0,200}?(\d{2,4})\s*(?:EUR|USD|€|\$)',
    r'entry\s+costs?.{0,200}?(\d{2,4})\s*(?:EUR|USD|€|\$)',
    # Frase "máximo" / "hasta" / "could be up to" con porcentaje
    r'(?:m[áa]ximo|hasta\s+el|up\s+to)\s+\d+[,.]?\d*\s*%.{0,80}entrada',
]


def detect_entry_fee_false_positive(text: str, entry_fee_db: float) -> dict:
    """
    Para fondos con entry_fee_db=0, evalúa si hay evidencia textual que
    contradiga ese valor.

    Returns:
        {'is_suspect': bool, 'signal_count': int, 'signals_matched': list[str]}
    """
    if entry_fee_db != 0.0:
        return {'is_suspect': False, 'signal_count': 0, 'signals_matched': []}

    matched = []
    for pat in ENTRY_FEE_NONZERO_SIGNALS:
        m = re.search(pat, text, re.I | re.S)
        if m:
            # Filtrar 0,00% y 0 EUR explícitos (no son falsos positivos)
            captured = m.group(1) if m.groups() else ''
            try:
                if float(captured.replace(',', '.')) == 0.0:
                    continue
            except (ValueError, IndexError):
                pass
            matched.append(pat[:40])

    return {
        'is_suspect': len(matched) > 0,
        'signal_count': len(matched),
        'signals_matched': matched,
    }
```

**Output esperado en log v1.3 Fase 9:**

```
======================================================================
  FASE 9 — Falsos positivos en Entry_Fee_Pct=0 (Q-DLA2-09, NUEVO v1.3)
======================================================================
  Fondos con Entry_Fee_Pct = 0 en BD       : 2148
  De estos, sospechosos de falso positivo  : 187  (8.7%)

  -- Distribución por nº de señales contradictorias --
  signals  n_fondos
  ------------------
  1        134
  2        41
  3+       12

  -- Top 10 ISINs sospechosos (mayor nº de señales) --
  ISIN           signals  ejemplo
  ---------------------------------------------------------
  LU1084165304   3        "5,25 % del importe que pagará..."
  IE0032875985   2        "497 EUR" + "máximo"
  LU1502282632   3        "3,50% máximo" + "Hasta 350 EUR"
  ...

  ⚠ ALERTA: ~6-9% de los Entry_Fee_Pct=0 actuales son potencialmente incorrectos.
  Esto NO aparece en KPIs de cobertura actuales (entry_fee_null=0 → "correcto").
```

### 3.3 Patología análoga: Exit_Fee_Pct=0 y Ongoing_Charge sospechosos

Misma lógica simétrica:

- **Fase 10:** falsos positivos `Exit_Fee_Pct=0` (señales `"de salida.*\d+%"`, `"\d+\s*EUR.*salida"`).
- **Fase 11:** sospecha en `Ongoing_Charge` cuando hay `"Annual cost impact"` / `"Incidencia anual de los costes"` con valores ≥2× del OC registrado (caso IE0032875985: OC=2.4 ↔ ACI@1Y=5.7).

### 3.4 Schema mínimo del CSV ampliado v1.3

`dla2_table_inventory.csv` actual tiene 18 columnas. v1.3 añade:

```
+ kid_format_inferred       (TEXT: PRIIPS_KID|UCITS_KIID|UNKNOWN)
+ priips_signals_count      (INT: 0-N)
+ ucits_signals_count       (INT: 0-N)
+ eur_near_costs_count      (INT: 0-N)
+ entry_fee_suspect         (INT: 0/1 — falso positivo sospechoso)
+ entry_fee_suspect_signals (INT: nº señales)
+ exit_fee_suspect          (INT: 0/1)
+ exit_fee_suspect_signals  (INT: nº señales)
+ oc_suspect_aci_gap        (INT: 0/1 — sospecha OC ≠ TER real)
```

Total v1.3: 27 columnas. Tabla SQLite temporal `dla_inv` análoga.

### 3.5 Nueva decisión Go/No-Go Sprint 2

El umbral del log actual (`Cat. 2 prevalencia ≥40% → GO`) es **insuficiente**. Se complementa con:

```
DECISIÓN SPRINT 2 (post-DIAG v1.3):

CRITERIO A (cobertura, ya existente):
  Cat. 2 prevalencia REAL ≥40% → adelante con extractor

CRITERIO B (nuevo, calidad):
  % falsos positivos detectados ≥3% del corpus → BLOQUEANTE para release sin extractor PRIIPs
  % falsos positivos detectados <1% → diferible

CRITERIO C (nuevo, formato):
  % PRIIPS_KID ≥80% del corpus → router obligatorio
  % PRIIPS_KID 30-80% → router recomendado
  % PRIIPS_KID <30% → reconsiderar alcance Sprint 2

GO Sprint 2 si A y (B≥3% o C≥80%).
```

---

## 4. Plan de ejecución Sprint 1

### 4.1 Orden de tareas (atomic, cada uno con AST validation + tests)

| # | Tarea | Módulo | Output |
|---|---|---|---|
| 1 | Implementar funciones `detect_kid_format()` + suite tests con 8 PDFs muestra | `scripts/diag/dla2_decision_diag.py` v1.3 | 8/8 → PRIIPS_KID |
| 2 | Implementar `detect_entry_fee_false_positive()` + tests | mismo | LU1084165304, IE0032875985, LU1502282632 → suspect=True |
| 3 | Implementar Fases 8/9/10/11 nuevas en orquestador | mismo | Log v1.3 con 4 fases adicionales |
| 4 | Ampliar CSV inventario a 27 columnas | mismo (Fase 3 ampliada) | `dla2_table_inventory.csv` v1.3 |
| 5 | Crear `scripts/migrate_v18_to_v19.py` | nuevo | Script idempotente |
| 6 | Actualizar `schema_fondos.sql` con 11 columnas + tabla nueva | `proyecto1/db/schema_fondos.sql` | DDL completo v19 |
| 7 | Actualizar `init_db.py` para reflejar v19 | `proyecto1/db/init_db.py` | BD nueva nace en v19 |
| 8 | Actualizar `schema_checks.py` con validación v19 | `proyecto1/db/schema_checks.py` | Verificación 59 cols + tabla |
| 9 | Ejecutar migración sobre BD producción | (operativo José) | BD producción en v19 |
| 10 | Ejecutar `dla2_decision_diag.py` v1.3 | (operativo José) | Log v1.3 + CSV v1.3 |

### 4.2 Behavioral test suite (obligatorio antes de delivery)

Archivo nuevo: `tests/test_dla2_diag_v13.py`

```python
"""
Test suite BL-DLA-2-COST-DIAG v1.3.
Valida detección de formato y de falsos positivos sobre los 8 PDFs muestra.
"""
import pathlib
import pytest
from scripts.diag.dla2_decision_diag import (
    detect_kid_format,
    detect_entry_fee_false_positive,
)

SAMPLE_DIR = pathlib.Path(__file__).parent / "fixtures" / "kid_samples"

# Esperado tras análisis manual sesión Opus 18-may-2026:
EXPECTED_FORMAT = {
    "IE00BJGT6Q17": "PRIIPS_KID",
    "LU1084165304": "PRIIPS_KID",
    "IE0032875985": "PRIIPS_KID",
    "IE00B45H7020": "PRIIPS_KID",
    "FR0000989626": "PRIIPS_KID",
    "LU0135992385": "PRIIPS_KID",
    "IE00BZ4D7085": "PRIIPS_KID",
    "LU1502282632": "PRIIPS_KID",
}

EXPECTED_ENTRY_FEE_SUSPECT = {
    "IE00BJGT6Q17": False,  # Entry real 0 EUR, correctamente 0
    "LU1084165304": True,   # 5,25% / 510 USD → falso positivo
    "IE0032875985": True,   # 497 EUR → falso positivo
    "IE00B45H7020": False,  # "no cobramos" → correctamente 0
    "FR0000989626": True,   # 0,50% / 50 € → falso positivo
    "LU0135992385": False,  # "No cobramos" → correctamente 0
    "IE00BZ4D7085": False,  # 0,00% hoy (el "5% futuro" va a _Pct_Max, no es FP estricto)
    "LU1502282632": True,   # 3,50% máximo → falso positivo
}


@pytest.mark.parametrize("isin,expected", EXPECTED_FORMAT.items())
def test_detect_kid_format(isin, expected):
    text = (SAMPLE_DIR / f"{isin}.txt").read_text(encoding="utf-8")
    assert detect_kid_format(text) == expected, f"{isin}: esperado {expected}"


@pytest.mark.parametrize("isin,expected", EXPECTED_ENTRY_FEE_SUSPECT.items())
def test_detect_entry_fee_false_positive(isin, expected):
    text = (SAMPLE_DIR / f"{isin}.txt").read_text(encoding="utf-8")
    result = detect_entry_fee_false_positive(text, entry_fee_db=0.0)
    assert result["is_suspect"] == expected, (
        f"{isin}: esperado is_suspect={expected}, got {result}"
    )
```

**Criterio de éxito Sprint 1:** ambos tests pasan 8/8.

### 4.3 SQL de control post-Sprint 1 (José ejecuta en DBeaver)

```sql
-- Q-C1: Schema v19 verificado
SELECT COUNT(*) AS n_cols FROM pragma_table_info('fund_master');
-- Esperado: 59

SELECT name FROM sqlite_master WHERE type='table' AND name='fund_cost_schedule';
-- Esperado: 1 fila

SELECT COUNT(*) FROM sqlite_master WHERE type='index'
  AND name IN ('idx_cost_schedule_isin', 'idx_cost_schedule_rhp');
-- Esperado: 2

-- Q-C2: Sin regresión en columnas existentes
SELECT
    SUM(Entry_Fee_Pct IS NOT NULL) AS entry_pob,
    SUM(Exit_Fee_Pct IS NOT NULL) AS exit_pob,
    SUM(Ongoing_Charge IS NOT NULL) AS oc_pob,
    COUNT(*) AS total
FROM fund_master;
-- Esperado: las 3 cifras de poblamiento ≥ valores pre-migración

-- Q-C3: Columnas nuevas todas NULL
SELECT
    SUM(KID_Format IS NULL) AS kf,
    SUM(ACI_1Y IS NULL) AS a1,
    SUM(ACI_RHP IS NULL) AS ar
FROM fund_master;
-- Esperado: las 3 = 3205

-- Q-C4: fund_cost_schedule vacía (Sprint 2 la poblará)
SELECT COUNT(*) FROM fund_cost_schedule;
-- Esperado: 0
```

---

## 5. Riesgos y mitigaciones

| ID | Riesgo | Probabilidad | Mitigación |
|---|---|---|---|
| R1 | `ALTER TABLE ADD COLUMN` lento sobre BD producción (3.205 filas) | Muy baja | SQLite reescribe metadata, no datos. Operación <1s en BD pequeña. |
| R2 | Heurística `detect_kid_format` clasifica fondos UCITS como PRIIPs (falsos PRIIPs) | Media | Requiere **3 señales strong + 1 EUR signal**, no 1. Tests sobre 8 PDFs validan. |
| R3 | Heurística falsos positivos genera ruido excesivo (>30% sospechosos) | Media | El KPI **es informativo, no acciónable** en Sprint 1. Solo cuantifica. Las decisiones se toman tras revisar el log. |
| R4 | `schema_checks.py` puede rechazar BDs v18 existentes tras update | Alta si no se cuida | El check debe ser **inclusivo**: aceptar v18 (sin las 11 cols) Y v19. Detectar versión y validar conjunto correspondiente. |
| R5 | Migración aplicada dos veces duplica errores | Baja | Script idempotente: comprueba `PRAGMA table_info` antes de cada `ADD COLUMN`. |
| R6 | El log diagnóstico v1.3 sobreestima falsos positivos para fondos monetarios con `Entry_Fee=0.50%` legítimo | Media | Es informativo. Los monetarios con entry fee real se identifican en revisión manual de top-N sospechosos. |

---

## 6. Lo que NO se hace en Sprint 1 (explícito)

Para evitar scope creep:

- ❌ No se implementa `cost_format_router.py` (Sprint 2).
- ❌ No se implementa `priips_cost_extractor.py` (Sprint 2).
- ❌ No se pueblan las 11 columnas nuevas con datos reales (Sprint 2).
- ❌ No se pueblan filas en `fund_cost_schedule` (Sprint 2).
- ❌ No se modifican los detectores existentes de `kiid_parser.py` (Sprint 2).
- ❌ No se cambia el comportamiento del pipeline en producción (es transparente).
- ❌ No se renombra `Ongoing_Charge` (riesgo de regresión; se mantiene + se documenta).
- ❌ No se modifican las validaciones semánticas existentes en `classify_utils.py` (Sprint 2 añade INTER-COST-1/2/3).

---

## 7. Checklist de aprobación Sprint 1

Antes de iniciar la sesión Sonnet de implementación:

- [x] José ha aprobado las 3 decisiones arquitectónicas (sesión Opus 18-may-2026).
- [ ] José confirma la lista de 11 columnas nuevas + sus dominios (sección 2.1).
- [ ] José confirma el esquema de `fund_cost_schedule` (sección 2.2).
- [ ] José confirma que `Ongoing_Charge` no se renombra en Sprint 1 (queda para documentar en Sprint 2).
- [ ] José confirma orden de tareas y criterios de cierre.
- [ ] José prepara backup de `fondos.sqlite` antes de ejecutar migración.

---

## 8. Sprint 2 — preview (NO compromiso)

Para contexto del lector futuro:

| Tarea Sprint 2 | Estimación |
|---|---|
| `cost_format_router.py` + tests | ~2h |
| `priips_cost_extractor.py` con cross-validation %↔EUR | ~6h |
| `ucits_cost_extractor.py` (refactor del legacy actual) | ~2h |
| Integración en `pipeline.py` con kill-switch nuevo | ~1h |
| Reglas INTER-COST-1/2/3 en `classify_utils.py` | ~2h |
| Re-ejecución pipeline completo + reporte de cambios | ~3h |
| **Total estimado Sprint 2** | **~16h** |

**Sprint 1 estimado:** ~7h (sesión Sonnet con todo el material de este spec).

---

**FIN DEL SPEC BL-DLA-2-COST-SPRINT-1.**

*Documento autocontenido conforme a Norma 5.4 (backlog v3.4).*
*Aprobación pendiente de José sobre los checklist items §7.*
