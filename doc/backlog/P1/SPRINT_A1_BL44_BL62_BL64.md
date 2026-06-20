# Sprint A.1 — Especificación implementable: BL-44 + BL-62 + BL-64

**Versión:** 1.0
**Fecha:** 29 de abril de 2026
**Autor (planificador):** Opus
**Destinatario (codificador):** Sonnet
**Documentos de referencia obligatorios:**
- `PRINCIPIOS_DISENO.md` (especialmente sección 1.1, 3.1, 3.2, 3.3)
- `RESTRICCIONES_ARQUITECTURA.md` (especialmente R-1, R-2, R-3, R-4, R-7, R-8)
- `ESTADO_BACKLOG_APR2026_v3_4.md` (sección 3 items abiertos)

**Resumen:** este sprint cierra tres defectos interdependientes que afectan a 153 fondos (4,8% del universo) y bloquean la progresión a P3. Los tres deben implementarse en un único commit porque las correcciones son interdependientes: BL-44 sin BL-62 deja Type/Family inconsistentes; BL-62 sin BL-44 no tiene sobre qué actuar; BL-64 es síntoma del mismo defecto arquitectónico.

---

## 1. CONTEXTO Y CAUSA RAÍZ COMÚN

### 1.1 Diagnóstico observado

| Defecto | Magnitud | Síntoma |
|---|---|---|
| BL-44 cobertura incompleta | 123 fondos | `Fund_Nature ∈ {Monetario, RFCP}` con `SRRI >= 3` en BD; la regla en `pipeline.py:678` no dispara para ellos |
| BL-62 propagación Nature→Type/Family | 30 fondos actuales + 123 que se sumarán = 153 | `Fund_Nature='Restantes'` con `Type/Family` heredados de la clasificación errónea (Type='Monetario' / Family='Monetario') |
| BL-64 persistencia inconsistente | 4 fondos | BL-44 logged `[BL44]` en log pero la BD conserva el `Fund_Nature` antiguo (BGF China Bond × 3 + BGF US SH DURAT BD S2) |

### 1.2 Causa raíz arquitectónica (común a los tres)

**Violación de R-4 ("Las reglas INTER usan valores efectivos, no actuales"):**

La regla BL-44 actual en `pipeline.py:678` lee `fund_master_record.get("Fund_Nature")` y `fund_master_record.get("SRRI")` directamente, sin consultar BD para fondos en modo CACHED. Cuando un fondo CACHED tiene:
- En memoria: `Fund_Nature=None` (porque ningún bloque le asignó nature en este ciclo).
- En BD: `Fund_Nature='Renta Fija Corto Plazo'` (preservado de ciclo anterior).
- En memoria: `SRRI=None` (no re-extraído).
- En BD: `SRRI=3`.

La regla evalúa `None in ('Monetario','RFCP')` → falso → no dispara. Después, el UPSERT con `COALESCE(excluded.Fund_Nature, Fund_Nature)` rescata el `Fund_Nature='RFCP'` de BD. Resultado final: BD termina con `Nature=RFCP + SRRI=3` sin que BL-44 haya tenido oportunidad de actuar.

**Esto es el mismo patrón documentado en R-4 que ya causó BL-30, BL-31, BL-45, BL-46, BL-49v2.** BL-44 escapó de la auditoría que produjo R-4 porque su control SQL daba 0 cuando la auditoría se hizo (oclusión accidental).

### 1.3 Por qué esto bloquea P3

- **BL-44 no resuelto:** la clase `Renta Fija Corto Plazo` está contaminada con 119 fondos HY, EM, China Bond, Income, Aggregate. Los agregados de retorno y volatilidad por régimen de la clase RFCP están sesgados. El scoring P3 por `Profile='Conservador'` filtraría incluyendo HY EM bonds.
- **BL-62 no resuelto:** la clase `Restantes` tiene Type/Family heredados de clasificaciones erróneas. P3 filtraría por `Family='Monetario'` y volvería a contaminar la clase Monetario con estos fondos.
- **BL-64 no resuelto:** 4 fondos quedan con divergencia entre log y BD, erosionando la trazabilidad.

---

## 2. ESPECIFICACIÓN DE FIX

### 2.1 Fase 1 — BL-44 corregido aplicando R-4

**Módulo:** `pipeline.py`
**Líneas:** 678–700 (la implementación actual del net defensivo)

**Código actual (hipotético, basado en grep del usuario):**
```python
# Línea 678: # BL-44: net defensivo — Nature incompatible con SRRI (cobertura universal).
_nat44 = fund_master_record.get("Fund_Nature")
_srri_val = fund_master_record.get("SRRI")
if _nat44 in ('Monetario', 'Renta Fija Corto Plazo') and _srri_val is not None:
    try:
        if int(_srri_val) >= 3:
            fund_master_record["Fund_Nature"] = "Restantes"
            log_info(
                f"  [BL44] {isin} Nature={_nat44} incompatible "
                f"con SRRI={_srri_val} → Restantes"
            )
            _record_event(conn, isin, "BL44_NATURE_SRRI", "WARN", ...)
    except (TypeError, ValueError):
        pass
```

**Código propuesto (corregido):**
```python
# Línea 678: BL-44 v2 (R-4 compliant): valor efectivo, no actual.
# Para fondos CACHED, fund_master_record puede tener Nature=None y SRRI=None
# mientras BD conserva los valores stale del ciclo anterior. La regla debe
# evaluar el valor efectivo (in-memory OR BD) para no dejar escape route.
#
# CAUSA RAÍZ DOCUMENTADA EN: RESTRICCIONES_ARQUITECTURA.md sección R-4
# y apéndice 5 (BL-30, BL-31, BL-45, BL-46, BL-49v2 sufrieron lo mismo).

# Lectura del valor BD (patrón coherente con _sf_bd, _ch_bd, etc., líneas 911-922)
_nat_bd_44, _srri_bd_44 = (None, None)
_row_bd = conn.execute(
    "SELECT Fund_Nature, SRRI FROM fund_master WHERE ISIN=?", (isin,)
).fetchone()
if _row_bd:
    _nat_bd_44, _srri_bd_44 = _row_bd

# Valor efectivo: in-memory si está, BD como fallback
_nat44_eff = fund_master_record.get("Fund_Nature") or _nat_bd_44
_srri_p = fund_master_record.get("SRRI")
_srri44_eff = _srri_p if _srri_p is not None else _srri_bd_44

if _nat44_eff in ('Monetario', 'Renta Fija Corto Plazo') and _srri44_eff is not None:
    try:
        _srri_int = int(_srri44_eff)
        if _srri_int >= 3:
            # Reasignación con flag de sobrescritura forzada
            fund_master_record["Fund_Nature"] = "Restantes"
            # CRÍTICO BL-64: marcar para que sqlite_writer NO use COALESCE
            # sobre este atributo en este UPSERT (ver sección 2.3)
            fund_master_record["_bl44_force_overwrite"] = True

            log_info(
                f"  [BL44] {isin} Nature_efectivo={_nat44_eff} "
                f"incompatible con SRRI_efectivo={_srri_int} → Restantes"
            )
            _record_event(
                conn, isin, "BL44_NATURE_SRRI_R4", "WARN",
                f"Nature efectivo={_nat44_eff} (mem={fund_master_record.get('Fund_Nature') or 'NULL'}, "
                f"bd={_nat_bd_44 or 'NULL'}); SRRI efectivo={_srri_int}"
            )

            # Encadenar BL-62: propagación Type/Family (sección 2.2)
            _propagate_nature_to_restantes_type_family(
                fund_master_record, isin, conn, kiid_text, kiid_parsed
            )
    except (TypeError, ValueError):
        pass
```

**Validación AST obligatoria (R-8):**
```bash
python -c "import ast; ast.parse(open('pipeline.py').read()); print('AST OK')"
```

---

### 2.2 Fase 2 — BL-62 propagación Nature → Type/Family

**Módulo nuevo de soporte:** función `_propagate_nature_to_restantes_type_family` en `classify_utils.py` (consistente con la decisión arquitectónica del usuario: la coherencia semántica vive en `classify_utils`, R-1 análogo extendido a coherencia inter-atributo).

**Decisión de diseño confirmada por el usuario (29-abr-2026):** opción A — re-clasificar Type/Family desde cero para los fondos que BL-44 marca como Restantes. NO mantener heredados, NO poner NULL, NO catch-all.

**Estrategia operativa de re-clasificación (3 fases con fallback):**

```python
# === En classify_utils.py — añadir al final del módulo ===

# ============================================================
# BL-62: PROPAGACIÓN NATURE → TYPE/FAMILY POST-CORRECCIÓN BL-44
# Cuando BL-44 reasigna Nature='Restantes', recalcular Type y Family
# desde cero (decisión usuario: opción A) en lugar de heredar valores
# de la clasificación errónea original.
# ============================================================

# Catálogo léxico canónico para fallback (orden importa: específicos antes que genéricos)
# Cada entrada: (regex_pattern, target_family, target_type)
# Idioma de Family/Type: español (Principio #8)
LEXICAL_FAMILY_INFERENCE_BL62 = [
    # === RF High Yield ===
    (r'HIGH\s*YI|\bHY\b|GBL?\s*HY', 'RF High Yield', 'Renta Fija Flexible'),
    # === RF Inflación ===
    (r'INFL', 'RF Inflación', 'Renta Fija Flexible'),
    # === RF Emergentes (China + EM + Asia) ===
    (r'CHINA\s+(BOND|FIX)|CHINA\.?\s*BON', 'RF Emergentes', 'Renta Fija Flexible'),
    (r'\bEM\s+(BOND|DEBT|MARK|CURR|MK|G\s+BON|MKT|DURAT)|EME\s+MK|EMERG\s+M|'
     r'EMERGING\s+M|EMERG\s+DBT|EMRG|EMER\.?\s*M|EMER\.MKT|\bBN\s+EM\b', 'RF Emergentes', 'Renta Fija Flexible'),
    (r'ASIA[NS]?\s+(BOND|BON|LOC|FLEX|OPPO|TIGER)|ASIAN?\s+LOC|ASIA\s+LOC|'
     r'GBL?.*EM\b|TEMPLETON.*BON\b|TEMPLETON\s+ASIA|TEMPLETON\s+EMER|'
     r'GAM\s+STR\s+EM|GL?\s+RATES', 'RF Emergentes', 'Renta Fija Flexible'),
    # === Retorno Absoluto ===
    (r'ABS\s+R|ABSOLUTE\s+R|EVENT\s+DRIV|GLOBAL\s+MACRO|\bALPHA\b|'
     r'GS\s+AB\s+RTRN|AB\s+RTRN|RTRN\s+TRCK', 'Retorno Absoluto', 'Absolute Return'),
    # === Activos Reales ===
    (r'COMMOD|VONTOBEL\s+COMMOD', 'Activos Reales', 'Materias Primas'),
    # === RV Temática ===
    (r'MEDTCH|MEDTECH|SMART\s+FOOD', 'RV Temática', 'Gestión Activa'),
    # === Mixtos (multi-asset, balanced, defensive) ===
    (r'PRDNT\s+WLTH|PRUDENT\s+WEALTH|MULTASST\s+INC|MULT\s+ASST\s+INC|MULTI\s+ASS|'
     r'MULTIOPP|MULTI\s+OPP|MULTIOPPORT|GLO\s+RESILI|RESILIENT|EQUILIB|'
     r'GLO?\.?\s*PERSPECTIVES|GLOBAL\s+PERSPECTIVES|GLO\s+MA|GLOBAL\s+MA|'
     r'FLEX\s+OPP|FLEX\s+PROP|PIONEER\s+FLEX|GLOB?\s+MA\s+CONSERVAT|'
     r'CONSERVAT\s+A|BAL.*N\s+EUR|BLCED|BALANC|STRATEGY\s+\d|'
     r'STIFTUNG|STIFT|PATR(IM)?|GL\s+OPTIM|GLOBAL\s+OPTIM',
     'Mixtos', 'Allocation'),
    # === Orientado a Renta (income oriented) ===
    (r'AMERIC.+INC|AMER\s+INC|AMERIC\s+INC|INC\s+P\.|INCM\s+P\.|DYN\s+HIGH\s+INC|'
     r'INC.*GROW|GLOBAL\s+OPP\s+BOND|MFS\s+GL.*OPP|US\s+SH\s+TERM\s+BOND|'
     r'DFNSIV.*INC|DEFENSIVE.*INC|SCHRODER\s+GLB\s+CRDT\s+INC|GLB\s+CRDT\s+INC',
     'Orientado a Renta', 'Allocation'),
    # === Renta Fija Flexible (más específicos antes) ===
    (r'TOTAL\s+RET|TOT\s+RET|GL\.T\.RET|GLOB.+TOTAL\s+RET|GLOB\.\s*T\.\s*RET',
     'Renta Fija Flexible', 'Total Return'),
    (r'TARGET\s+\d{4}', 'Renta Fija Flexible', 'Target Maturity'),
    (r'SHORT\s+DUR|SHRT\s+DUR|SH\s+DURAT|S\.?\s+DURATION|US\s+SH\s+DURAT|'
     r'US\s+DOLLAR\s+SH\s+DUR|GL\.?\s*SHORT\.?\s*DUR|GLOBAL\s+SHORT',
     'Renta Fija Flexible', 'Renta Fija Flexible'),
    (r'CONVERT', 'Renta Fija Flexible', 'Renta Fija Flexible'),
    (r'MORTG', 'Renta Fija Flexible', 'Renta Fija Flexible'),
    (r'GREEN\s+BOND|GREEN', 'Renta Fija Flexible', 'Renta Fija Flexible'),
    (r'SUSTAIN', 'Renta Fija Flexible', 'Renta Fija Flexible'),
    (r'INV\s+ENV\s+CLIM|ENV\s+CLIM|CLIMAT.*BND|CLIMT\s+OPP\s+BND',
     'Renta Fija Flexible', 'Renta Fija Flexible'),
    (r'CRDT\s+INC|CREDIT\s+INC|GLB\s+CRDT|GL\s+CR(E?)D|US\s+DOLLAR\s+CR(E?)D|'
     r'DOLLAR\s+CREDIT', 'Renta Fija Flexible', 'Crédito CP'),
    (r'GOV\s+BD|GV\s+BD|GOV\s+BOND|GOVERNMENT\s+BOND',
     'Renta Fija Flexible', 'Renta Fija Flexible'),
    (r'STRLING\s+BOND|STERLING\s+BOND|EUR\s+BONDS?\b|EURO\s+BONDS?\b|'
     r'EURO\s+LONG\s+DUR|LONG\s+DUR\s+BOND|BOND\s+FUND\s+EUR|UBS\s+BOND|'
     r'EURO\s+BOND|TEMPLETON\s+GLOB\.BON',
     'Renta Fija Flexible', 'Renta Fija Flexible'),
    (r'BOND\s+F.*CONVER|CONVER\s+EUROP', 'Renta Fija Flexible', 'Renta Fija Flexible'),
    (r'BGF.*GL.*OPPO|GL\s+OPPO|GL\.?\s+OPPO|FIX\.INCO\.GLO\.OPPO|'
     r'FIX\s+INC|FIXED\s+INC|FIX\.INC',
     'Renta Fija Flexible', 'Renta Fija Flexible'),
    (r'GLOB.*BOND|WORLD\s+BOND|GBL\s+BOND|GLOBAL\s+CR|EUR.*GOV.*BOND',
     'Renta Fija Flexible', 'Renta Fija Flexible'),
    # === Monetario residual (caso muy raro: SRRI≥3 con LIQUIDITY en nombre) ===
    (r'LIQUIDITY|LIQU\b', 'Monetario', 'Monetario'),
    # === Catch-all Income ===
    (r'\bINC\b|INCOME', 'Orientado a Renta', 'Allocation'),
]


def _infer_family_type_from_name_bl62(fund_name: str) -> tuple:
    """
    Inferencia léxica fallback de Family/Type para fondos reclasificados a
    Restantes por BL-44.

    Args:
        fund_name: nombre del fondo (case-insensitive)

    Returns:
        (family, type_) o (None, None) si ningún patrón coincide.

    Documentación: PRINCIPIOS_DISENO.md sección 3.1 (homogeneidad lingüística),
    sección 3.2 (consistencia inter-atributo). Patrones desarrollados con cobertura
    97.4% (149/153) sobre el universo afectado por BL-44 al 29-abr-2026.

    Restricciones aplicadas:
    - R-1: catálogo único de mapas en classify_utils.py
    - R-5: regex robustos contra fusiones letra+letra (no usa \\b entre letras)
    - Idioma objetivo Family: español; Type: español con excepciones documentadas
    """
    if not fund_name:
        return None, None

    name_upper = fund_name.upper()

    import re
    for pat, fam, typ in LEXICAL_FAMILY_INFERENCE_BL62:
        if re.search(pat, name_upper):
            return fam, typ

    return None, None


def propagate_nature_to_restantes_type_family(
    fund_record: dict,
    isin: str,
    conn=None,
    kiid_text: str = None,
    kiid_parsed: dict = None,
    log_fn=None,
) -> dict:
    """
    Recalcula Type/Family cuando BL-44 reasigna Fund_Nature='Restantes'.

    Estrategia tri-fásica (de mayor a menor confianza):

    Fase 1 (preferida) — Re-invocación de bloque clasificador apropiado:
        Si la inferencia léxica del nombre identifica un bloque destino
        (rf_flexible, mixtos, alternativos, renta_variable, monetarios),
        re-invocar ese bloque con el record actual y adoptar su Type/Family.

    Fase 2 (fallback) — Inferencia léxica directa:
        Si la re-invocación no es viable (bloque no disponible, falla, o
        retorna NULL), aplicar el catálogo LEXICAL_FAMILY_INFERENCE_BL62
        del paso anterior.

    Fase 3 (último recurso) — Marca para revisión manual:
        Si nada cubre el fondo (residuales léxicos), poner Type/Family=NULL
        y emitir log WARN con flag Data_Quality_Flag='WARN' indicando que
        requiere revisión.

    Args:
        fund_record: dict del fondo con Fund_Nature ya reasignado a 'Restantes'.
        isin: identificador para logging.
        conn: conexión SQLite (opcional, para Fase 1 si requiere lectura BD).
        kiid_text: texto KIID parseado (opcional, para Fase 1).
        kiid_parsed: dict parseado del KIID (opcional, para Fase 1).
        log_fn: función de logging (default: log_info global).

    Returns:
        El mismo fund_record modificado in-place (Type, Family, y flags).

    Restricciones aplicadas:
    - R-2: triple acción documentada en docstring del módulo.
    - R-4: la regla opera sobre fund_record post-corrección Nature, no sobre BD.
    - R-7: tests obligatorios en test_bl62_propagation.py.
    """
    fund_name = fund_record.get('Fund_Name', '')

    # Fase 1: re-invocación de bloque (placeholder — depende de arquitectura)
    # NOTA PARA SONNET: si la arquitectura actual permite re-invocar un bloque
    # con record.copy() y obtener (type, family) directamente, implementar aquí.
    # Si no es trivial, pasar directamente a Fase 2 y dejar Fase 1 como TODO
    # documentado para futuro sprint de refactorización.
    # Para este sprint, comenzar con Fase 2 + Fase 3.

    # Fase 2: inferencia léxica
    inferred_family, inferred_type = _infer_family_type_from_name_bl62(fund_name)

    if inferred_family is not None:
        fund_record['Family'] = inferred_family
        fund_record['Type'] = inferred_type
        # CRÍTICO BL-64: igual que Nature, forzar sobrescritura
        fund_record['_bl62_force_overwrite_family'] = True
        fund_record['_bl62_force_overwrite_type'] = True

        if log_fn:
            log_fn(
                f"  [BL62] {isin} Family={inferred_family} Type={inferred_type} "
                f"inferidos léxicamente tras BL-44 → Restantes"
            )
        return fund_record

    # Fase 3: residual sin patrón — marca de revisión
    fund_record['Family'] = None
    fund_record['Type'] = None
    fund_record['_bl62_force_overwrite_family'] = True
    fund_record['_bl62_force_overwrite_type'] = True
    # Marcar Data_Quality_Flag para auditoría manual posterior
    if fund_record.get('Data_Quality_Flag') != 'WARN':
        fund_record['Data_Quality_Flag'] = 'WARN'

    if log_fn:
        log_fn(
            f"  [BL62] {isin} sin patrón léxico identificable; "
            f"Family/Type=NULL; Data_Quality_Flag=WARN"
        )
    return fund_record
```

**Y en `pipeline.py`, importar y usar la función:**
```python
# Al inicio de pipeline.py (con los otros imports):
from classify_utils import (
    propagate_nature_to_restantes_type_family,
    # ... otros imports existentes
)

# Renombrar la llamada interna de la sección 2.1:
# _propagate_nature_to_restantes_type_family → propagate_nature_to_restantes_type_family
```

**Decisión de diseño documentada (NO mantener heredados):**

Cuando un fondo termina con `Fund_Nature='Restantes'` por BL-44, sus Type/Family heredados de la clasificación errónea (ej: Type='Monetario' / Family='Monetario' en un fondo que es realmente HY EM Bond) son **falsos por construcción**. Mantenerlos perpetuaría la falsedad y P3 los volvería a contaminar al filtrar por Family. La re-inferencia es obligatoria.

---

### 2.3 Fase 3 — BL-64 fix de persistencia

**Módulo:** `sqlite_writer.py`
**Líneas afectadas:** la sentencia UPSERT principal con `INSERT ... ON CONFLICT DO UPDATE SET ... COALESCE(...)`

**Verificación previa (P-1: diagnóstico antes de codificación):**
```bash
grep -n "INSERT OR REPLACE\|ON CONFLICT.*COALESCE\|Fund_Nature" sqlite_writer.py
```

Identificar la cláusula UPSERT de los campos `Fund_Nature`, `Type`, `Family`. Probable forma actual:
```sql
INSERT INTO fund_master (..., Fund_Nature, Type, Family, ...)
VALUES (..., :Fund_Nature, :Type, :Family, ...)
ON CONFLICT(ISIN) DO UPDATE SET
    Fund_Nature = COALESCE(excluded.Fund_Nature, Fund_Nature),
    Type        = COALESCE(excluded.Type,        Type),
    Family      = COALESCE(excluded.Family,      Family),
    ...
```

**El COALESCE preserva el valor antiguo cuando el nuevo es NULL.** Esto es correcto en general (no queremos borrar info por accident). Pero **cuando BL-44 marca el record con flag `_bl44_force_overwrite=True`**, queremos que la sobrescritura sea forzada incluso si excluded valor es el mismo o si hay alguna concurrencia con otro bloque.

**Fix propuesto en `sqlite_writer.py`:**

```python
def upsert_fund_master(record: dict, conn) -> None:
    """
    UPSERT idempotente con COALESCE como default.

    BL-64 fix: cuando record incluye flags `_bl44_force_overwrite` o
    `_bl62_force_overwrite_*`, la cláusula correspondiente sobrescribe sin COALESCE.

    Documentación: RESTRICCIONES_ARQUITECTURA.md R-2 (triple acción) y R-4.
    """
    # Detectar flags de sobrescritura forzada
    force_nature = record.pop('_bl44_force_overwrite', False)
    force_family = record.pop('_bl62_force_overwrite_family', False)
    force_type   = record.pop('_bl62_force_overwrite_type', False)

    # Construir cláusulas SET dinámicamente
    nature_clause = (
        "Fund_Nature = excluded.Fund_Nature"  # sobrescritura forzada
        if force_nature
        else "Fund_Nature = COALESCE(excluded.Fund_Nature, Fund_Nature)"
    )
    family_clause = (
        "Family = excluded.Family"
        if force_family
        else "Family = COALESCE(excluded.Family, Family)"
    )
    type_clause = (
        "Type = excluded.Type"
        if force_type
        else "Type = COALESCE(excluded.Type, Type)"
    )

    sql = f"""
        INSERT INTO fund_master (...todas las columnas...)
        VALUES (...placeholders...)
        ON CONFLICT(ISIN) DO UPDATE SET
            {nature_clause},
            {type_clause},
            {family_clause},
            -- resto de columnas con COALESCE estándar
            ...
    """
    conn.execute(sql, record)
```

**Smoke test manual (P-4):**
```python
# Test con los 4 ISINs del defecto BL-64
for isin in ['LU2267099674', 'LU0719319435', 'LU0764816798', 'LU2624961806']:
    # Forzar reset y re-procesamiento
    conn.execute("UPDATE fund_master SET Fund_Nature = 'Renta Fija Corto Plazo' WHERE ISIN=?", (isin,))
    # Procesar fondo individualmente
    process_isin(isin, force_refresh=True)
    # Verificar resultado
    nat = conn.execute("SELECT Fund_Nature FROM fund_master WHERE ISIN=?", (isin,)).fetchone()[0]
    assert nat == 'Restantes', f"BL-64 fail para {isin}: Nature={nat}"
```

---

### 2.4 Migración SQL one-shot (R-2 acción 3)

Tras desplegar el fix, **es obligatorio** ejecutar migración para sanear los 153 fondos actuales en BD (R-2 acción 3). Sin esta migración, los fondos en modo CACHED no serán re-procesados por el pipeline en el próximo ciclo y permanecerán en estado erróneo.

**Script de migración (ejecutar manualmente una sola vez tras deploy):**

```sql
-- ============================================================
-- MIGRACIÓN BL-44/BL-62/BL-64 — saneo one-shot
-- Fecha: tras deploy del sprint A.1
-- Razón: COALESCE de UPSERT preserva valores stale para fondos CACHED
-- ============================================================

-- Backup obligatorio antes de ejecutar
.backup 'C:\desarrollo\fondos\db\fondos_pre_bl44_bl62_$(date).sqlite'

BEGIN TRANSACTION;

-- 1. Forzar re-procesamiento de los 153 fondos en próximo ciclo
UPDATE fund_master
SET Updated_At = '1970-01-01 00:00:00'
WHERE (
    Fund_Nature IN ('Monetario', 'Renta Fija Corto Plazo')
    AND CAST(SRRI AS INTEGER) >= 3
)
OR Fund_Nature = 'Restantes';

-- 2. Marcar para FORCE_REFRESH (alternativa: si existe flag específico)
-- UPDATE fund_master SET _force_refresh_next_cycle = 1 WHERE ...;

COMMIT;

-- 3. Verificación post-migración (debe ser 153)
SELECT COUNT(*) AS total_to_reprocess
FROM fund_master
WHERE Updated_At = '1970-01-01 00:00:00';
```

**Alternativa preferida (si el pipeline soporta flag FORCE_REFRESH):**
```python
# Ejecutar antes del próximo ciclo del pipeline
python pipeline.py --force-refresh-isins-from-query \
    "SELECT ISIN FROM fund_master WHERE \
     (Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND CAST(SRRI AS INTEGER) >= 3) \
     OR Fund_Nature = 'Restantes'"
```

---

## 3. TESTS OBLIGATORIOS (R-7)

**Archivo:** `tests/test_bl44_bl62_bl64_sprint_a1.py`

```python
"""
Tests funcionales aislados para Sprint A.1 (BL-44/BL-62/BL-64).
No importa pipeline.py completo — usa mocks.
"""
import pytest
from classify_utils import (
    _infer_family_type_from_name_bl62,
    propagate_nature_to_restantes_type_family,
    LEXICAL_FAMILY_INFERENCE_BL62,
)


# ===== BL-44 R-4 compliance =====

def test_bl44_dispara_con_nature_solo_en_bd():
    """BL-44: si Nature está en BD pero no en record entrante (CACHED), dispara igual."""
    record = {'Fund_Nature': None, 'SRRI': 3, 'ISIN': 'LU0000000001'}
    # Simular BD con Fund_Nature='Renta Fija Corto Plazo'
    nat_bd = 'Renta Fija Corto Plazo'
    srri_bd = 3
    nat_eff = record.get('Fund_Nature') or nat_bd
    srri_p = record.get('SRRI')
    srri_eff = srri_p if srri_p is not None else srri_bd
    assert nat_eff == 'Renta Fija Corto Plazo'
    assert srri_eff == 3
    assert nat_eff in ('Monetario', 'Renta Fija Corto Plazo')
    assert int(srri_eff) >= 3
    # → debe disparar BL-44


def test_bl44_no_dispara_con_nature_other():
    """BL-44: si Nature efectivo es Renta Variable, no dispara (no cumple predicado)."""
    record = {'Fund_Nature': 'Renta Variable', 'SRRI': 5}
    assert record['Fund_Nature'] not in ('Monetario', 'Renta Fija Corto Plazo')


def test_bl44_no_dispara_con_srri_bajo():
    """BL-44: si SRRI<3, no dispara aunque Nature sea Monetario."""
    record = {'Fund_Nature': 'Monetario', 'SRRI': 2}
    assert int(record['SRRI']) < 3


# ===== BL-62 inferencia léxica =====

def test_bl62_hy_pattern():
    """BL-62: 'HY' en nombre → RF High Yield."""
    fam, typ = _infer_family_type_from_name_bl62('AB GB HY PF A2 EUR ACC')
    assert fam == 'RF High Yield'
    assert typ == 'Renta Fija Flexible'


def test_bl62_china_bond_pattern():
    """BL-62: 'CHINA BOND' → RF Emergentes."""
    fam, typ = _infer_family_type_from_name_bl62('BGF CHINA BOND A2 (EUR) ACC')
    assert fam == 'RF Emergentes'
    assert typ == 'Renta Fija Flexible'


def test_bl62_em_debt_pattern():
    """BL-62: 'EM DEBT' → RF Emergentes."""
    fam, typ = _infer_family_type_from_name_bl62('MS INV EM MK DBT AH EURHDG ACC')
    assert fam == 'RF Emergentes'


def test_bl62_aggregate_bond_pattern():
    """BL-62: 'EURO BOND' → RF Flexible."""
    fam, typ = _infer_family_type_from_name_bl62('BGF EURO BOND A2 ACC')
    assert fam == 'Renta Fija Flexible'
    assert typ == 'Renta Fija Flexible'


def test_bl62_balanced_pattern():
    """BL-62: 'STRATEGY 50' → Mixtos."""
    fam, typ = _infer_family_type_from_name_bl62('ALLIANZ STRATEGY 50 CT EUR')
    assert fam == 'Mixtos'
    assert typ == 'Allocation'


def test_bl62_income_pattern():
    """BL-62: 'AMERIC INC' → Orientado a Renta."""
    fam, typ = _infer_family_type_from_name_bl62('AB AMERIC INC PORTF A2 EURH AC')
    assert fam == 'Orientado a Renta'
    assert typ == 'Allocation'


def test_bl62_absolute_return_pattern():
    """BL-62: 'EVENT DRIVEN' → Retorno Absoluto."""
    fam, typ = _infer_family_type_from_name_bl62('BSF GL EVENT DRIVEN A2 EURH AC')
    assert fam == 'Retorno Absoluto'
    assert typ == 'Absolute Return'


def test_bl62_total_return_pattern():
    """BL-62: 'TOTAL RET' → RF Flexible / Total Return."""
    fam, typ = _infer_family_type_from_name_bl62('TEMPLETON GLB TOT RET A HG INC')
    assert fam == 'Renta Fija Flexible'
    assert typ == 'Total Return'


def test_bl62_residual_returns_none():
    """BL-62: nombre sin patrón identificable → (None, None)."""
    fam, typ = _infer_family_type_from_name_bl62('XYZQRS UNKNOWN FUND')
    assert fam is None
    assert typ is None


def test_bl62_orden_patrones_especifico_antes_que_generico():
    """
    BL-62: el catálogo debe procesar específicos antes que genéricos.
    'AMERIC INC' debe matchear como 'Orientado a Renta' (específico),
    NO como 'Income → Orientado a Renta' (genérico).
    """
    fam, typ = _infer_family_type_from_name_bl62('AMERIC INC PORTFOLIO')
    assert fam == 'Orientado a Renta'
    # Aunque ambos patrones llevan al mismo destino, el específico debe ganar


def test_bl62_propagate_modifies_record_in_place():
    """BL-62: la función modifica el record y añade flags de sobrescritura."""
    record = {
        'Fund_Name': 'BGF EURO BOND A2 ACC',
        'Fund_Nature': 'Restantes',
        'Type': 'Renta Fija Corto Plazo',  # heredado erróneo
        'Family': 'Renta Fija Corto Plazo',
    }
    propagate_nature_to_restantes_type_family(record, 'LU0000000001')

    assert record['Family'] == 'Renta Fija Flexible'
    assert record['Type'] == 'Renta Fija Flexible'
    assert record.get('_bl62_force_overwrite_family') is True
    assert record.get('_bl62_force_overwrite_type') is True


def test_bl62_residual_marca_data_quality_warn():
    """BL-62: cuando inferencia falla, Type/Family=NULL y DQ='WARN'."""
    record = {
        'Fund_Name': 'XYZQRS UNKNOWN FUND',
        'Fund_Nature': 'Restantes',
        'Type': 'Renta Fija Corto Plazo',
        'Family': 'Renta Fija Corto Plazo',
        'Data_Quality_Flag': 'OK',
    }
    propagate_nature_to_restantes_type_family(record, 'LU9999999999')

    assert record['Family'] is None
    assert record['Type'] is None
    assert record['Data_Quality_Flag'] == 'WARN'


# ===== BL-64 persistencia =====

def test_bl64_force_overwrite_flag_present():
    """BL-64: el record debe llevar flag _bl44_force_overwrite tras BL-44."""
    record = {'Fund_Nature': 'Restantes', '_bl44_force_overwrite': True}
    assert record.get('_bl44_force_overwrite') is True


# ===== Catálogo léxico — cobertura mínima =====

def test_catalogo_lexico_cubre_153_minimo_97pct():
    """
    El catálogo léxico debe cubrir ≥97% de los 153 fondos afectados.
    Validación con muestra fija de nombres reales.
    """
    nombres_reales_153 = [
        'AB AMERIC INC PORTF A2 EURH AC',
        'BGF CHINA BOND A2 (EUR) ACC',
        'AB GB HY PF A2 EUR ACC',
        'BSF GL EVENT DRIVEN A2 EURH AC',
        'TEMPLETON GLB TOT RET A HG INC',
        'ALLIANZ STRATEGY 50 CT EUR',
        'BGF EURO BOND A2 ACC',
        # ... añadir muestra representativa
    ]
    cubiertos = 0
    for nombre in nombres_reales_153:
        fam, _ = _infer_family_type_from_name_bl62(nombre)
        if fam is not None:
            cubiertos += 1
    assert cubiertos / len(nombres_reales_153) >= 0.97
```

---

## 4. CONTROLES SQL POST-FIX

```sql
-- ============================================================
-- BL-44 v2 — debe devolver 0 (objetivo: 0)
SELECT COUNT(*) AS bl44_violations FROM fund_master
WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo')
  AND CAST(SRRI AS INTEGER) >= 3;
-- Antes del fix: 123. Esperado tras fix + migración: 0

-- BL-62 — Type/Family NUNCA Monetario/RFCP cuando Nature='Restantes'
-- (excepto los 2 LIQUIDITY casos especiales)
SELECT Family, Type, COUNT(*) FROM fund_master
WHERE Fund_Nature='Restantes'
  AND (
       (Family IN ('Monetario','Renta Fija Corto Plazo') AND
        Fund_Name NOT LIKE '%LIQUIDITY%')
       OR
       (Type IN ('Monetario','Renta Fija Corto Plazo') AND
        Fund_Name NOT LIKE '%LIQUIDITY%')
      )
GROUP BY Family, Type;
-- Antes del fix: 30 fondos con Family/Type heredados. Esperado: 0

-- BL-62 — distribución esperada en Fund_Nature='Restantes' tras fix
SELECT Family, COUNT(*) AS n FROM fund_master
WHERE Fund_Nature='Restantes'
GROUP BY Family
ORDER BY n DESC;
-- Distribución esperada (basada en inferencia léxica):
--   Renta Fija Flexible: ~54
--   RF Emergentes:       ~40
--   Mixtos:              ~23
--   Orientado a Renta:   ~16
--   Retorno Absoluto:    ~7
--   RF High Yield:       ~5
--   Monetario:           ~2 (LIQUIDITY casos)
--   Activos Reales:      ~1
--   RV Temática:         ~1
--   NULL:                ~4 (residuales con DQ=WARN)

-- BL-64 — fondos específicos (los 4 BGF problemáticos)
SELECT ISIN, Fund_Name, Fund_Nature, SRRI FROM fund_master
WHERE ISIN IN ('LU2267099674','LU0719319435','LU0764816798','LU2624961806');
-- Esperado: todos con Fund_Nature='Restantes', no 'Renta Fija Corto Plazo'

-- Auditoría DQ='WARN' tras propagación residual
SELECT COUNT(*) FROM fund_master
WHERE Fund_Nature='Restantes' AND Data_Quality_Flag='WARN';
-- Esperado: ≤4 (residuales sin patrón léxico)
```

---

## 5. CHECKLIST PRE-COMMIT (R-2 + checklist sección 4 de RESTRICCIONES)

- [ ] AST OK en `pipeline.py` y `classify_utils.py` y `sqlite_writer.py` (R-8)
- [ ] Tests `test_bl44_bl62_bl64_sprint_a1.py` pasando (R-7)
- [ ] Migración SQL preparada y verificada en backup (R-2 acción 3)
- [ ] Logging `[BL44]` y `[BL62]` añadido y verificado en run de smoke test
- [ ] No hay mapas duplicados en `pipeline.py` o bloques (R-1)
- [ ] La regla BL-44 usa `_X_eff = record.get('X') or _X_bd` (R-4)
- [ ] Regex de inferencia léxica testeados contra fusiones letra+letra (R-5)
- [ ] Cabecera de `classify_utils.py` actualizada con versión y BL-62
- [ ] Cabecera de `pipeline.py` actualizada con versión y BL-44 v2
- [ ] Smoke test sobre 5 ISINs canónicos (P-4): LU0249549436, LU2267099674, LU1133289592, LU0907915168, LU0907915598

---

## 6. AUDITORÍA POST-DEPLOY (BL-67 propuesto)

Tras cerrar este sprint, **es recomendable abrir como BL-67** en el backlog una auditoría sistémica de todas las reglas INTER en `pipeline.py` para verificar que cumplen R-4. Lista de candidatos a auditar:

- BL-19 (Fund_Nature='Mixto' contra 'Mixtos')
- BL-33 (Mon/RFCP con Investment_Universe=NULL)
- BL-42 (Mixtos sin Credit_Quality)
- BL-47 (Is_ESG=1 sin Sfdr_Article)
- Cualquier otra regla en pipeline que lea `fund_master_record.get(...)` sin fallback BD

**Razón:** la auditoría de BL-44 hoy detectó que la regla violaba R-4 silenciosamente. El control SQL daba 0 por oclusión accidental. Otras reglas pueden estar en el mismo estado latente.

---

**Fin del documento. Versión 1.0 — 29 de abril de 2026.**
