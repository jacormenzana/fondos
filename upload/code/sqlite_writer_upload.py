# core/sqlite_writer.py  — v25
# -*- coding: utf-8 -*-
"""
Publicación idempotente del output estructural de Proyecto 1 en SQLite.

Cambios v25 (2026-05-23 — BL-COST Sprint 2 S2-C/S2-D):
  BL-COST-4c  publish_fund extendida con parámetro cost_schedule_rows.
              Permite persistir fund_cost_schedule en la misma transacción.
  BL-COST-4d  correct_oc_aci_mismatch: escritura no-COALESCE para BL-COST-5.

Cambios v24 (2026-05-19):
  BL-COST-2  Schema v19:
             - Ongoing_Charge → Ongoing_Charge_Recurrent (renombrado).
             - +11 columnas nuevas en INSERT/UPSERT (todas None en Sprint 1).
             - Nueva función upsert_cost_schedule().
             - ALLOWED_VALUES_BY_COLUMN no modificado (eso es classify_utils.py).
             - R-1: KID_Format/Cost_Extraction_Quality NO entran en _normalize_record.

Cambios v23 (2026-05-09):
  BL-LANG-EN  Family, Type y Subtype: idioma objetivo cambiado a inglés.
              Cambios:
                _TYPE_EN_TO_ES: vaciado (ya no traduce EN→ES).
                _SUBTYPE_EN_TO_ES: vaciado ídem.
                _post_upsert_normalize_db CASE Type/Subtype/Family:
                  invertidos a corrección ES→EN (stale BD de ciclos
                  anteriores). Family cubre los 14 valores ES canónicos.
                global_post_pipeline_normalize_db: ídem, cobertura global.
                Métricas pre-normalización actualizadas: ahora miden
                  stale ES pendientes (type_es_stale, subtype_es_stale,
                  family_es_stale) en lugar de stale EN.

Cambios v22 (2026-05-09):
  BL-SF-EN    Sector_Focus: idioma objetivo revertido a INGLÉS (GICS).
              Motivo: denominaciones GICS son estándar internacional con
              semántica precisa. Traducir destruía granularidad (ej:
              Financial Services ≠ Financials & Insurance colapsados en
              'Servicios Financieros'). Principio #8: Sector_Focus ya
              tenía idioma objetivo EN en la especificación original.
              Cambios:
                _SECTOR_FOCUS_EN_TO_ES: convertido a pass-through EN + 
                  corrección inversa ES→EN para stale de ciclos anteriores.
                _post_upsert_normalize_db CASE Sector_Focus: invertido
                  (stale ES → EN canónico).
                global_post_pipeline_normalize_db CASE Sector_Focus: ídem.
                UPDATE dedicado para 'Servicios Financieros': discrimina
                  por Theme ('Insurance' → 'Financials & Insurance',
                  resto → 'Financial Services'). Cubre los 10 fondos
                  ambiguos en BD donde la información del valor EN
                  original se perdió en ciclos previos.
              Control SQL post-ciclo:
                SELECT Sector_Focus, COUNT(*) FROM fund_master
                WHERE Sector_Focus IS NOT NULL
                GROUP BY Sector_Focus ORDER BY 2 DESC;
                -- Todos los valores deben estar en inglés (GICS).

Cambios v21 (2026-05-09):
  BL-64d-FIX  _post_upsert_normalize_db y global_post_pipeline_normalize_db:
              corregidos los dos CASE de Family que aplicaban
              'Income Oriented' → 'Orientado a Renta' (BL-57 v2, revertido
              por BL-65b). Ambas funciones ahora aplican la corrección
              INVERSA: 'Orientado a Renta' → 'Income Oriented' (pass-through
              del valor EN canónico). 'Income Oriented' pasa sin modificar.
              Causa raíz: estos dos UPDATE SQL ejecutaban al final de cada
              ISIN (post-upsert) y al final del ciclo (global), deshaciendo
              sistemáticamente el fix de pipeline BL-64d y la emisión correcta
              de mixtos.py / LEXICAL_FAMILY_INFERENCE_BL62.
              Impacto esperado: Family='Orientado a Renta' → 0 tras el ciclo.

Cambios v20 (2026-04-30 — Sprint A.1.b):
  Flags force_overwrite  upsert_fund_master: implementados flags
                         _bl44_force_overwrite, _bl62_force_overwrite_family,
                         _bl62_force_overwrite_type. Cuando están activos,
                         la cláusula UPSERT sobrescribe directamente (sin
                         COALESCE). En ausencia de flags, Fund_Nature/Profile/
                         Type/Strategy/Family/Style_Profile/Geography/Theme/
                         Subtype usan COALESCE defensivo (antes sobrescritura
                         directa que podía borrar valores de BD en ciclos
                         CACHED con record None). Spec: SPRINT_A1.b sección 4.

  Logging BL-44-Persist  Emite INFO cuando force_nature=True (señal de que
                         BL-44 forzó sobrescritura de Fund_Nature).

  NORM-DB logging        _post_upsert_normalize_db: captura before/after y
                         emite INFO cuando una traducción stale es corregida.

Cambios v19 (2026-04-25):
  BL-53/54  Fix arquitectónico: _normalize_record solo opera sobre `excluded.X`
            (record entrante). La cláusula UPSERT con COALESCE preserva
            fund_master.X cuando excluded.X IS NULL, sin pasar por la
            normalización. En ciclos CACHED esto provoca supervivencia
            indefinida de valores stale en inglés (Sector_Focus, Type, Subtype).

            Solución: nueva función _post_upsert_normalize_db(conn, ISIN) que
            aplica los mismos mapas EN→ES vía SQL UPDATE+CASE WHEN sobre el
            registro recién tocado. Idempotente (CASE devuelve el mismo valor
            si ya está traducido). Coste despreciable (<100ms total/3204 fondos).

            Invocada desde upsert_fund_master() inmediatamente tras conn.execute.

Cambios v18 (2026-04-19):
  BL-53     _SECTOR_FOCUS_EN_TO_ES ampliado con Real Assets, Infrastructure,
            variante con trailing space; añadidos _TYPE_EN_TO_ES y
            _SUBTYPE_EN_TO_ES para normalizaciones lingüísticas pre-escritura.

Cambios v17:
  - fund_master INSERT/ON CONFLICT: añadidos Investment_Focus,
    Credit_Quality, Fee_Known_Flag con COALESCE en ON CONFLICT
  - 42 → 45 parámetros en upsert_fund_master
"""

import sqlite3
import re
from pathlib import Path
from typing import Dict, Iterable, Optional, Any
import datetime

# §Y-2: el normalizador de casing es UNA función en classify_utils (R-1).
# Import defensivo (sys.path variable según entrypoint).
try:
    import classify_utils as _cu  # type: ignore
except ImportError:  # pragma: no cover
    try:
        from proyecto1.core import classify_utils as _cu  # type: ignore
    except ImportError:
        _cu = None


# Importación diferida para evitar dependencia circular en arranque
def _get_normalizer():
    try:
        from core.benchmark_normalizer import normalize_benchmark
        return normalize_benchmark
    except Exception:
        return None


# ============================================================
# ESQUEMA SQLITE
# ============================================================

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "db" / "schema_fondos.sql"


def _load_schema_sql() -> str:
    if not _SCHEMA_PATH.exists():
        raise FileNotFoundError(
            f"Schema canónico no encontrado: {_SCHEMA_PATH}\n"
            "Asegúrate de que db/schema_fondos.sql existe en la raíz del proyecto."
        )
    return _SCHEMA_PATH.read_text(encoding="utf-8")


# ============================================================
# CONEXIÓN
# Re-export de shared.db.get_connection — función canónica única (DRY).
# sqlite_writer la re-exporta para mantener compatibilidad con
# run_block.py y otros módulos que la importan desde aquí.
# ============================================================
try:
    from shared.db import get_connection   # noqa: F401  (re-export)
except ModuleNotFoundError:
    # shared no está aún en sys.path — añadirlo explícitamente.
    # Estructura esperada: <raiz>/proyecto1/core/sqlite_writer.py
    #                      <raiz>/shared/db.py
    import sys as _sys
    from pathlib import Path as _Path
    _shared_root = _Path(__file__).resolve().parents[2]
    if str(_shared_root) not in _sys.path:
        _sys.path.insert(0, str(_shared_root))
    from shared.db import get_connection   # noqa: F401  (re-export)


def create_schema(conn: sqlite3.Connection) -> None:
    """Crea todas las tablas del sistema leyendo db/schema_fondos.sql."""
    conn.executescript(_load_schema_sql())
    conn.commit()


def close_connection(conn: sqlite3.Connection) -> None:
    conn.commit()
    conn.close()


# ============================================================
# HELPERS
# ============================================================

def _safe_int(value: Optional[Any]) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


# Códigos sub-año emitidos por kiid_parser que NO mapean a años enteros.
# Decisión abierta: sub-año (MMF/cash) → None. Cambiar a 1 si se prefiere el
# bucket mínimo de 1 año para horizontes de fondos monetarios.
_RHP_SUBYEAR_CODES = frozenset({"1D", "1D-3M", "1M", "3M", "6M", "<1Y"})


def _rhp_to_years(value: Optional[Any]) -> Optional[int]:
    """Convierte el código de Recommended_Holding_Period del parser
    ('5Y','10Y+','3Y','3M'...) al INTEGER de años que exige el schema v20.

    Causa raíz (RHP 100% NULL): el schema migró TEXT→INTEGER y el writer
    envolvía con _safe_int(), pero el parser sigue emitiendo códigos; por eso
    _safe_int('5Y') → None para todo el corpus. Aquí se mapea código→año.
    'NY'/'10Y+' → N (10); sub-año → None (ver _RHP_SUBYEAR_CODES); entero pasa.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    s = str(value).strip().upper()
    if not s:
        return None
    if s in _RHP_SUBYEAR_CODES:
        return None
    m = re.match(r"(\d+)\s*Y", s)            # '1Y','5Y','10Y+'
    if m:
        return int(m.group(1))
    try:
        return int(s)                        # ya-entero en texto
    except Exception:
        return None

# ============================================================
# NORMALIZACIÓN PRE-ESCRITURA (Principio #8 + BL-53)
# ============================================================

# Sector_Focus: idioma objetivo INGLÉS (Principio #8 — nomenclatura GICS).
# BL-SF-EN (2026-05-09): revertida la traducción EN→ES introducida en BL-53/54.
# Motivo: Sector_Focus usa denominaciones GICS estándar internacionales; traducirlas
# destruye precisión semántica (ej: Financial Services ≠ Financials & Insurance)
# y rompe trazabilidad con la fuente. El dict ahora actúa como:
#   1. Pass-through: valores EN canónicos ya correctos (identidad).
#   2. Corrección inversa: stale ES → EN canónico para sanear BD de ciclos anteriores.
#   3. Normalización de variantes (trailing space, casing alternativo).
_SECTOR_FOCUS_EN_TO_ES = {
    # --- Pass-through valores EN canónicos (identidad) ---
    "Technology & Innovation":      "Technology & Innovation",
    "Energy & Resources":           "Energy & Resources",
    "Utilities & Environment":      "Utilities & Environment",
    "Utilities & Environment ":     "Utilities & Environment",      # trailing space
    "Healthcare & Life Sciences":   "Healthcare & Life Sciences",
    "Materials & Mining":           "Materials & Mining",
    "Financial Services":           "Financial Services",
    "Financials & Insurance":       "Financials & Insurance",
    "Consumer Discretionary":       "Consumer Discretionary",
    "Real Estate & Infrastructure": "Real Estate & Infrastructure",
    "Real Assets":                  "Real Assets",
    "Infrastructure":               "Infrastructure",
    # --- Corrección inversa: stale ES → EN canónico (fondos de ciclos anteriores) ---
    # Nota: 'Activos Reales' es ambiguo (puede venir de Real Assets, Real Estate &
    # Infrastructure, o Infrastructure). Todos los fondos con ese valor en BD tienen
    # Theme=Real Estate → origen más probable: Real Estate & Infrastructure.
    # Para Real Assets puro (sin inmobiliario) el pipeline emitirá el EN directamente.
    "Tecnología e Innovación":      "Technology & Innovation",
    "Energía y Recursos":           "Energy & Resources",
    "Utilities y Medio Ambiente":   "Utilities & Environment",
    "Salud y Ciencias de la Vida":  "Healthcare & Life Sciences",
    "Materiales y Minería":         "Materials & Mining",
    "Consumo":                      "Consumer Discretionary",
    "Activos Reales":               "Real Estate & Infrastructure",  # ver nota arriba
    # 'Servicios Financieros' es ambiguo (Financial Services vs Financials & Insurance).
    # La corrección per-ISIN se gestiona vía UPDATE directo en global_post_pipeline
    # usando Theme como discriminador (ver abajo). Pass-through aquí para no colapsar.
    "Servicios Financieros":        "Servicios Financieros",         # resuelta por Theme
}

# Type: normalizar EN→ES para valores sin excepción documentada (BL-53).
# Excepciones mantenidas en inglés (P#8 §2.2):
#   Allocation, Absolute Return, Tactical Allocation, Total Return,
#   Target Maturity, Floating Rate CP.
_TYPE_EN_TO_ES: Dict[str, str] = {
    # BL-LANG-EN (2026-05-09): idioma objetivo EN. Dict vaciado — no hay
    # traducción EN→ES que aplicar. Los CASE SQL de _post_upsert y global
    # hacen la corrección inversa ES→EN para stale de ciclos anteriores.
}

# Subtype: BL-LANG-EN (2026-05-09): idioma objetivo EN. Dict vaciado.
_SUBTYPE_EN_TO_ES: Dict[str, str] = {}


def _normalize_record(record: Dict[str, Optional[Any]]) -> Dict[str, Optional[Any]]:
    """
    Normalización canónica pre-escritura (v20, §Y-2).

    Aplica EXCLUSIVAMENTE el normalizador de casing centralizado
    (classify_utils.normalize_casing, fuente única en config.DOMAIN_VALUES) a
    cada columna categórica. Esto canonicaliza casing/drift tipográfico
    (ACCUMULATION→Accumulation, HEDGED→Hedged) sin inventar remaps de valor y
    sin tocar acrónimos (CNAV/ETF). Los remaps de VALOR (ES→EN de clase de
    activo) son responsabilidad del clasificador/reprocess, no de aquí: los
    valores no mutan a mitad de flujo.

    v20: eliminada la lógica v19 ES-targeting (Currency_Hedged/Type/Subtype
    EN→ES, Accumulation .upper(), Sector_Focus EN→ES) — columnas borradas o con
    idioma objetivo invertido a EN.
    """
    if _cu is None or not hasattr(_cu, "normalize_casing"):
        return record
    for col in list(record.keys()):
        record[col] = _cu.normalize_casing(col, record[col])
    return record


# ============================================================
# Post-UPSERT DB normalization (BL-53/54 fix arquitectónico)
# ============================================================
#
# Defecto detectado ciclo 25/04/2026:
#   La cláusula UPSERT usa COALESCE(excluded.X, fund_master.X) para Sector_Focus,
#   Type, Family, Subtype. En ciclos CACHED, excluded.X puede ser None y COALESCE
#   preserva fund_master.X sin pasar por _normalize_record (que solo opera sobre
#   excluded). Resultado: valores stale en inglés sobreviven indefinidamente.
#
# Solución: tras cada UPSERT, ejecutar UPDATE quirúrgico sobre el ISIN recién
# tocado que aplica los mismos mapas EN→ES sobre la BD efectiva. Idempotente:
# si los valores ya están en español, el UPDATE no afecta filas (CASE devuelve
# el mismo valor).
#
# Coste: 1 UPDATE adicional por fondo (4 columnas tocadas con CASE WHEN inline).
# Sobre 3.204 fondos en SQLite local: ~80–120 ms total. Despreciable.
# ============================================================

def _post_upsert_normalize_db(conn: sqlite3.Connection, isin: str) -> None:
    """
    Aplica las traducciones EN→ES directamente sobre fund_master para el ISIN
    recién tocado. Cubre el caso COALESCE con valor stale en inglés (BL-53/54).

    v20 (Sprint A.1.b): añadido logging cuando se detectan valores stale
    (señal de que COALESCE preservó un valor en idioma incorrecto).
    """
    import logging as _log
    _logger = _log.getLogger(__name__)

    # Capturar valores antes para detectar cambios (logging de stale)
    # v20: Type (→Vehicle_Structure) y Subtype (borrada) ya NO se normalizan aquí.
    before_row = conn.execute(
        "SELECT Sector_Focus, Family FROM fund_master WHERE ISIN=?", (isin,)
    ).fetchone()

    sql = """
    UPDATE fund_master
    SET Sector_Focus = CASE
            -- BL-SF-EN: idioma objetivo EN. Etiquetas alineadas al canónico v20
            -- (§2A.1 #6). Pass-through valores EN ya correctos.
            WHEN TRIM(Sector_Focus) = 'Tecnología e Innovación'     THEN 'Technology & Innovation'
            WHEN TRIM(Sector_Focus) = 'Energía y Recursos'          THEN 'Energy & Resources'
            WHEN TRIM(Sector_Focus) = 'Utilities y Medio Ambiente'  THEN 'Utilities & Environment'
            WHEN TRIM(Sector_Focus) = 'Salud y Ciencias de la Vida' THEN 'Healthcare & Life Sciences'
            WHEN TRIM(Sector_Focus) = 'Materiales y Minería'        THEN 'Materials & Mining'
            WHEN TRIM(Sector_Focus) = 'Consumo'                     THEN 'Consumer'
            WHEN TRIM(Sector_Focus) = 'Activos Reales'              THEN 'Real Assets'
            -- 'Servicios Financieros': pass-through; resuelto por UPDATE global usando Theme.
            ELSE TRIM(Sector_Focus)
        END,
        Family = CASE
            -- BL-LANG-EN (2026-05-09): idioma objetivo EN. Corrección inversa ES→EN.
            WHEN TRIM(Family) = 'RV Core'               THEN 'Equity Core'
            WHEN TRIM(Family) = 'RV Temática'           THEN 'Thematic Equity'
            WHEN TRIM(Family) = 'Mixtos'                THEN 'Multi-Asset'
            WHEN TRIM(Family) = 'Renta Fija Corto Plazo' THEN 'Short-Term Fixed Income'
            WHEN TRIM(Family) = 'Renta Fija Flexible'   THEN 'Flexible Fixed Income'
            WHEN TRIM(Family) = 'Monetario'             THEN 'Money Market'
            WHEN TRIM(Family) = 'Retorno Absoluto'      THEN 'Absolute Return'
            WHEN TRIM(Family) = 'Activos Reales'        THEN 'Real Assets'
            WHEN TRIM(Family) = 'RF High Yield'         THEN 'High Yield'
            WHEN TRIM(Family) = 'RF Emergentes'         THEN 'Emerging Market Debt'
            WHEN TRIM(Family) = 'RF Inflación'          THEN 'Inflation-Linked'
            WHEN TRIM(Family) = 'Flexible Estratégico'  THEN 'Strategic Allocation'
            WHEN TRIM(Family) = 'Estructurado'          THEN 'Structured'
            WHEN TRIM(Family) = 'Orientado a Renta'     THEN 'Income Oriented'
            ELSE TRIM(Family)
        END
    WHERE ISIN = ?;
    """
    conn.execute(sql, (isin,))

    # Capturar valores después y emitir warning si alguno cambió (stale detectado)
    if before_row:
        after_row = conn.execute(
            "SELECT Sector_Focus, Family FROM fund_master WHERE ISIN=?", (isin,)
        ).fetchone()
        if after_row:
            cols = ["Sector_Focus", "Family"]
            for col, b, a in zip(cols, before_row, after_row):
                if b != a:
                    _logger.info(
                        "[%s] [NORM-DB-Translation] %s: '%s' → '%s' "
                        "(valor stale corregido post-UPSERT)",
                        isin, col, b, a,
                    )


# ============================================================
# FUND MASTER UPSERT  (v17 — 45 columnas)
# ============================================================

def upsert_fund_master(conn: sqlite3.Connection,
                       record: Dict[str, Optional[Any]]) -> None:
    """
    Upsert canónico en fund_master — v20.

    Política COALESCE (Sprint A.1.b — flags de sobrescritura forzada):
    - Por defecto: COALESCE en Fund_Nature, Profile, Vehicle_Structure, Strategy,
      Family, Style_Profile, Geography, Theme — preserva valor BD si el record
      entrante es None (ciclos CACHED no pierden datos previos).
    - Cuando el record incluye flag `_bl44_force_overwrite=True`:
      Fund_Nature se sobrescribe directamente (sin COALESCE).
    - Cuando incluye `_bl62_force_overwrite_family=True`:
      Family se sobrescribe directamente.
    - Cuando incluye `_bl62_force_overwrite_type=True`:
      Vehicle_Structure se sobrescribe directamente (ex-Type, repropuesta v20).
    - Atributos v3/v17 (Market_Cap_Focus, Sector_Focus, ...): COALESCE.
    - SRRI: CASE especial — solo actualizar si SRRI_Quality_Flag no es NONE.
    - v20: eliminadas Is_ESG/Subtype/Currency_Hedged/Portfolio_Currency;
      añadidas Development_Status/Duration_Profile/MMF_Structure/Alt_Strategy/
      Payoff_Profile (COALESCE, NULL hasta el reprocess). El SQL (INSERT/VALUES/
      SET/params) se DERIVA de una lista ordenada explícita `spec` (R-2, §B-5).

    Documentación: SPRINT_A1_BL44_BL62_BL64.md sección 2.3,
    SPRINT_A1.b sección 4. Restricciones R-2, R-4.
    """

    # ── Detectar y extraer flags antes de normalizar el record ──
    force_nature = record.pop('_bl44_force_overwrite', False)
    force_family = record.pop('_bl62_force_overwrite_family', False)
    force_type   = record.pop('_bl62_force_overwrite_type', False)

    # ── Normalización pre-escritura (Principio #8) ──
    record = _normalize_record(record)

    # ── Política por columna en ON CONFLICT (v20) ──
    #   'ins' = solo INSERT (no SET) · 'ow' = sobrescribe ·
    #   'co'  = COALESCE(excluded, fund_master) · 'srri'/'srri_flag' = CASE ·
    #   'now' = Updated_At. Lista explícita columna-a-columna (R-2, §B-5):
    #   INSERT/VALUES/params/SET se DERIVAN de ella → imposible descuadrar ?.
    #   v20: −Is_ESG/−Subtype/−Currency_Hedged/−Portfolio_Currency;
    #        Type→Vehicle_Structure; +5 nuevas (NULL hasta reprocess, COALESCE).
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    nature_pol  = 'ow' if force_nature else 'co'
    family_pol  = 'ow' if force_family else 'co'
    vehicle_pol = 'ow' if force_type   else 'co'   # _bl62_force_overwrite_type → Vehicle_Structure

    spec = [
        ("ISIN",                        record["ISIN"],                          'ins'),
        ("Fund_Name",                   record.get("Fund_Name"),                 'ow'),
        ("Management_Company",          record.get("Management_Company"),        'ow'),
        ("Fund_Nature",                 record.get("Fund_Nature"),               nature_pol),
        ("Profile",                     record.get("Profile"),                   'co'),
        ("Vehicle_Structure",          record.get("Vehicle_Structure"),         vehicle_pol),
        ("Strategy",                    record.get("Strategy"),                  'co'),
        ("Family",                      record.get("Family"),                    family_pol),
        ("Style_Profile",               record.get("Style_Profile"),             'co'),
        ("Geography",                   record.get("Geography"),                 'co'),
        ("Theme",                       record.get("Theme"),                     'co'),
        ("Exposure_Bias",               record.get("Exposure_Bias"),             'ow'),
        ("Benchmark_Type",              record.get("Benchmark_Type"),            'ow'),
        ("Market_Cap_Focus",            record.get("Market_Cap_Focus"),          'co'),
        ("Sector_Focus",                record.get("Sector_Focus"),              'co'),
        ("Investment_Universe",         record.get("Investment_Universe"),       'co'),
        ("Investment_Focus",            record.get("Investment_Focus"),          'co'),
        ("Credit_Quality",              record.get("Credit_Quality"),            'co'),
        ("Accumulation_Policy",         record.get("Accumulation_Policy"),       'co'),
        ("Heuristic_Block",             record.get("Heuristic_Block"),           'ow'),
        ("Heuristic_Core",              int(record.get("Heuristic_Core", 0)),    'ow'),
        ("SRRI",                        _safe_int(record.get("SRRI")),           'srri'),
        ("Fund_Currency",               record.get("Fund_Currency"),             'co'),
        ("Hedging_Policy",              record.get("Hedging_Policy"),            'co'),
        ("Replication_Method",          record.get("Replication_Method"),        'co'),
        ("Derivatives_Usage",           record.get("Derivatives_Usage"),         'co'),
        ("Benchmark_Declared",          record.get("Benchmark_Declared"),        'co'),
        ("Ongoing_Charge_Recurrent",    record.get("Ongoing_Charge_Recurrent"),  'co'),
        ("Entry_Fee_Pct",               record.get("Entry_Fee_Pct"),             'co'),
        ("Exit_Fee_Pct",                record.get("Exit_Fee_Pct"),              'co'),
        ("Fee_Known_Flag",              record.get("Fee_Known_Flag"),            'co'),
        ("Sfdr_Article",                record.get("Sfdr_Article"),              'co'),
        ("Recommended_Holding_Period",  _rhp_to_years(record.get("Recommended_Holding_Period")), 'co'),
        ("Leverage_Used",               record.get("Leverage_Used"),             'co'),
        ("Liquidity_Profile",           record.get("Liquidity_Profile"),         'co'),
        ("Distribution_Frequency",      record.get("Distribution_Frequency"),    'ow'),
        ("fund_family_id",              record.get("fund_family_id"),            'ins'),
        ("Inference_Trace",             record.get("Inference_Trace"),           'ow'),
        ("SRRI_Quality_Flag",           record.get("SRRI_Quality_Flag"),         'srri_flag'),
        ("Data_Quality_Flag",           record.get("Data_Quality_Flag"),         'ow'),
        ("Updated_At",                  now,                                     'now'),
        ("KID_Format",                  record.get("KID_Format"),                'co'),
        ("KID_Currency",                record.get("KID_Currency"),              'co'),
        ("Cost_Extraction_Quality",     record.get("Cost_Extraction_Quality"),   'co'),
        ("Cost_RHP_Years",              record.get("Cost_RHP_Years"),            'co'),
        ("Entry_Fee_Pct_Max",           record.get("Entry_Fee_Pct_Max"),         'co'),
        ("Exit_Fee_Pct_Max",            record.get("Exit_Fee_Pct_Max"),          'co'),
        ("Management_Fee_Pct",          record.get("Management_Fee_Pct"),        'co'),
        ("Transaction_Cost_Pct",        record.get("Transaction_Cost_Pct"),      'co'),
        ("Performance_Fee_Pct",         record.get("Performance_Fee_Pct"),       'co'),
        ("ACI_1Y",                      record.get("ACI_1Y"),                    'co'),
        ("ACI_RHP",                     record.get("ACI_RHP"),                   'co'),
        # v20 CREATE (5) — NULL hasta el reprocess; COALESCE preserva en CACHED.
        ("Development_Status",          record.get("Development_Status"),        'co'),
        ("Duration_Profile",            record.get("Duration_Profile"),          'co'),
        ("MMF_Structure",               record.get("MMF_Structure"),             'co'),
        ("Alt_Strategy",                record.get("Alt_Strategy"),              'co'),
        ("Payoff_Profile",              record.get("Payoff_Profile"),            'co'),
    ]

    insert_cols = [c for c, _v, _p in spec]
    placeholders = ", ".join(["?"] * len(insert_cols))
    params = tuple(v for _c, v, _p in spec)

    _set_parts = []
    for _col, _v, _pol in spec:
        if _pol == 'ins':
            continue
        if _pol in ('ow', 'now'):
            _set_parts.append(f"{_col} = excluded.{_col}")
        elif _pol == 'co':
            _set_parts.append(f"{_col} = COALESCE(excluded.{_col}, fund_master.{_col})")
        elif _pol == 'srri':
            _set_parts.append(
                "SRRI = CASE WHEN excluded.SRRI_Quality_Flag IS NOT NULL "
                "AND excluded.SRRI_Quality_Flag != 'NONE' THEN excluded.SRRI "
                "ELSE fund_master.SRRI END")
        elif _pol == 'srri_flag':
            _set_parts.append(
                "SRRI_Quality_Flag = CASE WHEN excluded.SRRI_Quality_Flag IS NOT NULL "
                "AND excluded.SRRI_Quality_Flag != 'NONE' THEN excluded.SRRI_Quality_Flag "
                "ELSE fund_master.SRRI_Quality_Flag END")

    sql = (
        "INSERT INTO fund_master (\n        "
        + ",\n        ".join(insert_cols)
        + "\n    )\n    VALUES (" + placeholders + ")\n"
        "    ON CONFLICT(ISIN) DO UPDATE SET\n        "
        + ",\n        ".join(_set_parts)
        + "\n    ;"
    )

    conn.execute(sql, params)

    # ── Logging de sobrescritura forzada (BL-44 / BL-62) ──────────────────
    if force_nature:
        import logging as _log
        _log.getLogger(__name__).info(
            "[%s] [BL-44-Persist] Fund_Nature=%s sobrescritura forzada (sin COALESCE)",
            record.get("ISIN"), record.get("Fund_Nature"),
        )

    # ── Post-UPSERT DB normalization (BL-53/54 fix arquitectónico) ──
    # Aplica traducciones EN→ES sobre el valor efectivo en BD para cubrir el
    # caso COALESCE: cuando el record entrante no aporta valor para Sector_Focus,
    # Type o Subtype y la BD conserva un valor antiguo en inglés.
    _post_upsert_normalize_db(conn, record["ISIN"])


# ============================================================
# KIID METADATA UPSERT
# ============================================================

def upsert_kiid_metadata(conn: sqlite3.Connection,
                         kiid_record: Dict[str, Optional[Any]]) -> None:
    """
    UPSERT extendido con validación cruzada SRRI.

    Usa INSERT ... ON CONFLICT DO UPDATE en lugar de INSERT OR REPLACE para
    preservar Raw_KIID_Text existente cuando el nuevo valor es NULL.
    INSERT OR REPLACE hace DELETE+INSERT y borraría el texto si el campo
    viene vacío en algún ciclo de re-ingesta.
    """

    sql = """
    INSERT INTO fund_kiid_metadata (
        ISIN,
        KIID_Class,
        KIID_URL,
        KIID_PDF_Hash,
        KIID_Status,
        Language,
        Raw_KIID_Text,
        KIID_Published_Date,
        KIID_Downloaded_At,
        SRRI,
        SRRI_Visual,
        SRRI_Textual,
        SRRI_Validation_Status,
        Processing_Time_Ms,
        Processing_Breakdown,
        DLA2_Table_Text,
        Cost_Mgmt_BandsX,
        Cost_Mgmt_Ruled,
        Cost_Mgmt_Arbitration,
        Cost_Oper_BandsX,
        Cost_Oper_Ruled,
        Cost_Oper_Arbitration
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(ISIN, KIID_Class) DO UPDATE SET
        KIID_Class    = excluded.KIID_Class,
        KIID_URL      = COALESCE(excluded.KIID_URL,      fund_kiid_metadata.KIID_URL),
        KIID_PDF_Hash = COALESCE(excluded.KIID_PDF_Hash, fund_kiid_metadata.KIID_PDF_Hash),
        -- Preservar OK si el nuevo estado es CACHED (caché no degrada estado real)
        KIID_Status   = CASE
                            WHEN excluded.KIID_Status = 'CACHED'
                            THEN COALESCE(fund_kiid_metadata.KIID_Status, 'CACHED')
                            ELSE excluded.KIID_Status
                        END,
        Language            = excluded.Language,
        -- COALESCE: preservar texto existente si el nuevo es NULL
        Raw_KIID_Text       = COALESCE(excluded.Raw_KIID_Text, fund_kiid_metadata.Raw_KIID_Text),
        KIID_Published_Date = excluded.KIID_Published_Date,
        -- Preservar fecha de descarga real: solo actualizar si el nuevo valor no es NULL
        KIID_Downloaded_At  = COALESCE(excluded.KIID_Downloaded_At, fund_kiid_metadata.KIID_Downloaded_At),
        -- SRRI: preservar valor existente si el nuevo es NULL
        -- En modo CACHED el parser no extrae visual → no destruir el dato anterior
        SRRI                   = COALESCE(excluded.SRRI,                   fund_kiid_metadata.SRRI),
        SRRI_Visual            = COALESCE(excluded.SRRI_Visual,            fund_kiid_metadata.SRRI_Visual),
        SRRI_Textual           = COALESCE(excluded.SRRI_Textual,           fund_kiid_metadata.SRRI_Textual),
        SRRI_Validation_Status = COALESCE(excluded.SRRI_Validation_Status, fund_kiid_metadata.SRRI_Validation_Status),
        Processing_Time_Ms     = excluded.Processing_Time_Ms,
        Processing_Breakdown   = excluded.Processing_Breakdown,
        -- DLA-2: preservar resultado cacheado si el nuevo es NULL
        -- (en ciclos CACHED no se re-extrae: no destruir el dato existente)
        DLA2_Table_Text        = COALESCE(excluded.DLA2_Table_Text, fund_kiid_metadata.DLA2_Table_Text),
        -- v20 (§4.3): arbitración de coste — COALESCE preserva el veredicto
        -- existente cuando el ciclo entra CACHED (excluded.* = NULL). Esto
        -- PRESERVA un BOTH_FAIL real (intentado y fallido) y nunca deja que un
        -- NULL posterior lo sobrescriba. La distinción BOTH_FAIL vs NULL se
        -- mantiene precisamente por este COALESCE.
        Cost_Mgmt_BandsX       = COALESCE(excluded.Cost_Mgmt_BandsX,       fund_kiid_metadata.Cost_Mgmt_BandsX),
        Cost_Mgmt_Ruled        = COALESCE(excluded.Cost_Mgmt_Ruled,        fund_kiid_metadata.Cost_Mgmt_Ruled),
        Cost_Mgmt_Arbitration  = COALESCE(excluded.Cost_Mgmt_Arbitration,  fund_kiid_metadata.Cost_Mgmt_Arbitration),
        Cost_Oper_BandsX       = COALESCE(excluded.Cost_Oper_BandsX,       fund_kiid_metadata.Cost_Oper_BandsX),
        Cost_Oper_Ruled        = COALESCE(excluded.Cost_Oper_Ruled,        fund_kiid_metadata.Cost_Oper_Ruled),
        Cost_Oper_Arbitration  = COALESCE(excluded.Cost_Oper_Arbitration,  fund_kiid_metadata.Cost_Oper_Arbitration)
    ;
    """

    params = (
        kiid_record.get("ISIN"),
        kiid_record.get("KIID_Class"),
        kiid_record.get("KIID_URL"),
        kiid_record.get("KIID_PDF_Hash"),
        kiid_record.get("KIID_Status"),
        kiid_record.get("Language"),
        kiid_record.get("Raw_KIID_Text"),
        kiid_record.get("KIID_Published_Date"),
        kiid_record.get("KIID_Downloaded_At"),
        _safe_int(kiid_record.get("SRRI")),
        _safe_int(kiid_record.get("SRRI_Visual")),
        _safe_int(kiid_record.get("SRRI_Textual")),
        kiid_record.get("SRRI_Validation_Status"),
        kiid_record.get("Processing_Time_Ms"),
        kiid_record.get("Processing_Breakdown"),
        kiid_record.get("DLA2_Table_Text"),          # v18 — NULL en ciclos CACHED
        # v20 (§4.3) — arbitración de coste (NULL en CACHED / flag off)
        kiid_record.get("Cost_Mgmt_BandsX"),
        kiid_record.get("Cost_Mgmt_Ruled"),
        kiid_record.get("Cost_Mgmt_Arbitration"),
        kiid_record.get("Cost_Oper_BandsX"),
        kiid_record.get("Cost_Oper_Ruled"),
        kiid_record.get("Cost_Oper_Arbitration"),
    )

    conn.execute(sql, params)


# ============================================================
# NAV SERIES INSERT
# ============================================================

def insert_nav_series(conn: sqlite3.Connection, isin: str,
                      nav_series: Iterable[Dict[str, Any]]) -> None:
    sql = """
    INSERT OR IGNORE INTO fund_nav_monthly
        (ISIN, Date, NAV, NAV_Currency, NAV_Type, Is_Estimated, Data_Source, Ingested_At)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    for row in nav_series:
        conn.execute(sql, (
            isin,
            row.get("date"),
            row.get("nav"),
            row.get("currency"),
            row.get("nav_type", "official"),
            int(row.get("is_estimated", 0)),
            row.get("source", "KIID"),
            now,
        ))


# ============================================================
# BENCHMARK UPSERT (secundario — desde KIID)
# ============================================================

def _upsert_kiid_benchmark(conn: sqlite3.Connection,
                            benchmark_declared: str,
                            isin: str) -> None:
    normalize_benchmark = _get_normalizer()
    if normalize_benchmark is None:
        return

    try:
        norm = normalize_benchmark(benchmark_declared)
        now  = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')

        conn.execute("""
            INSERT OR REPLACE INTO fund_benchmarks
                (ISIN, source, benchmark_raw, benchmark_id, benchmark_name,
                 provider, asset_class, confidence, extracted_at)
            VALUES (?, 'KIID', ?, ?, ?, ?, ?, ?, ?)
        """, (
            isin,
            benchmark_declared,
            norm.canonical_id   if norm else None,
            norm.canonical_name if norm else benchmark_declared,
            norm.provider       if norm else None,
            norm.asset_class    if norm else None,
            norm.confidence     if norm else 'LOW',
            now,
        ))
    except Exception:
        pass  # No interrumpir el pipeline por un fallo de benchmark


# ============================================================
# LOG
# ============================================================

def log_ingestion(conn: sqlite3.Connection, isin: Optional[str],
                  step: str, status: str,
                  message: Optional[str]) -> None:
    try:
        conn.execute(
            "INSERT INTO ingestion_log (ISIN, step, status, message, created_at) "
            "VALUES (?,?,?,?,?)",
            (isin, step, status, message,
             datetime.datetime.utcnow().isoformat(timespec="seconds")),
        )
    except Exception:
        pass  # El log nunca debe interrumpir el pipeline


# ============================================================
# PUBLISH FUND (punto de entrada principal del pipeline)
# ============================================================

def publish_fund(
    conn: sqlite3.Connection,
    fund_master_record: Dict[str, Optional[Any]],
    nav_series: Optional[Iterable[Dict[str, Any]]] = None,
    kiid_record: Optional[Dict[str, Optional[Any]]] = None,
    cost_schedule_rows: Optional[list] = None,   # BL-COST-4c A-3: atomicidad con schedule
) -> None:
    try:
        with conn:
            upsert_fund_master(conn, fund_master_record)
            if kiid_record:
                upsert_kiid_metadata(conn, kiid_record)
            if nav_series:
                insert_nav_series(conn, fund_master_record["ISIN"], nav_series)
            # Normalizar Benchmark_Declared y escribir en fund_benchmarks (source=KIID)
            _bench = fund_master_record.get("Benchmark_Declared")
            if _bench and _bench != "NO_BENCHMARK":
                _upsert_kiid_benchmark(conn, _bench, fund_master_record["ISIN"])
            # BL-COST-4c: persistir schedule en la misma transacción (A-3)
            if cost_schedule_rows:
                upsert_cost_schedule(conn, fund_master_record["ISIN"], cost_schedule_rows)
            log_ingestion(conn, fund_master_record.get("ISIN"), "PUBLISH_FUND", "OK", None)
    except Exception as exc:
        try:
            log_ingestion(conn, fund_master_record.get("ISIN"),
                          "PUBLISH_FUND", "ERROR", str(exc))
        finally:
            raise


# ============================================================
# BL-53/56/57/SF-EN: Barrido global post-pipeline (Principio #1)
# ============================================================
#
# Causa raíz arquitectónica:
#   _post_upsert_normalize_db() opera sobre el ISIN recién upserted
#   (WHERE ISIN=?). Los fondos no procesados en el ciclo conservan
#   indefinidamente sus valores stale en BD.
#
# Solución: tras procesar TODOS los bloques del ciclo, ejecutar UNA query
# global sin filtro de ISIN. Cubre simultáneamente:
#   - Sector_Focus: corrección inversa ES→EN (BL-SF-EN 2026-05-09).
#     Idioma objetivo: inglés (GICS). Stale ES saneados a EN canónico.
#     'Servicios Financieros' resuelto por UPDATE dedicado con discriminador Theme.
#   - Type EN→ES (BL-53: Commodities, Real Assets, Target Volatility)
#   - Subtype EN→ES (BL-53: Physical / Derivatives, Low Duration, etc.)
#   - Family: corrección inversa ES→EN para 'Orientado a Renta' (BL-64d-FIX)
#
# Idempotente. Coste ~150ms sobre 3.204 filas.
# ============================================================

def global_post_pipeline_normalize_db(conn: sqlite3.Connection) -> Dict[str, int]:
    """
    Normalización global de fund_master tras finalizar el pipeline.

    Aplica los mismos catálogos que _post_upsert_normalize_db()
    pero SIN filtro de ISIN — cubre TODA la tabla, incluyendo fondos
    no procesados en el ciclo actual.

    Returns:
        dict con conteos pre-normalización para auditoría:
          {
            "sector_focus_en": <count>,
            "type_en":         <count>,
            "subtype_en":      <count>,
            "family_en":       <count>,
            "rows_affected":   <count>
          }
    """
    metrics: Dict[str, int] = {}

    # 1. Métricas pre-normalización (auditoría)
    # BL-SF-EN: ahora medimos cuántos valores ES stale quedan por corregir a EN.
    metrics["sector_focus_es_stale"] = conn.execute(
        "SELECT COUNT(*) FROM fund_master WHERE Sector_Focus IN ("
        "'Tecnología e Innovación', 'Energía y Recursos', 'Utilities y Medio Ambiente', "
        "'Salud y Ciencias de la Vida', 'Materiales y Minería', "
        "'Consumo', 'Activos Reales', 'Servicios Financieros')"
    ).fetchone()[0]

    # v20: Type (→Vehicle_Structure, vocab nuevo) y Subtype (borrada) ya NO se
    # normalizan aquí. Sus métricas/CASE se retiran (columnas inexistentes tras
    # el rebuild → evitarían crash).

    metrics["family_es_stale"] = conn.execute(
        "SELECT COUNT(*) FROM fund_master WHERE Family IN ("
        "'RV Core', 'RV Temática', 'Mixtos', 'Renta Fija Corto Plazo',"
        "'Renta Fija Flexible', 'Monetario', 'Retorno Absoluto', 'Activos Reales',"
        "'RF High Yield', 'RF Emergentes', 'RF Inflación', 'Flexible Estratégico',"
        "'Estructurado', 'Orientado a Renta')"
    ).fetchone()[0]

    # 2. UPDATE global (mismo catálogo que _post_upsert_normalize_db pero
    #    SIN WHERE ISIN). TRIM + comparación case-sensitive (suficiente
    #    porque los valores los emite el propio pipeline en casing canónico).
    sql = """
    UPDATE fund_master
    SET Sector_Focus = CASE
            -- BL-SF-EN: idioma objetivo EN; etiquetas alineadas a canónico v20 (§2A.1 #6).
            WHEN TRIM(Sector_Focus) = 'Tecnología e Innovación'     THEN 'Technology & Innovation'
            WHEN TRIM(Sector_Focus) = 'Energía y Recursos'          THEN 'Energy & Resources'
            WHEN TRIM(Sector_Focus) = 'Utilities y Medio Ambiente'  THEN 'Utilities & Environment'
            WHEN TRIM(Sector_Focus) = 'Salud y Ciencias de la Vida' THEN 'Healthcare & Life Sciences'
            WHEN TRIM(Sector_Focus) = 'Materiales y Minería'        THEN 'Materials & Mining'
            WHEN TRIM(Sector_Focus) = 'Consumo'                     THEN 'Consumer'
            WHEN TRIM(Sector_Focus) = 'Activos Reales'              THEN 'Real Assets'
            -- 'Servicios Financieros': resuelto por UPDATE dedicado abajo usando Theme.
            ELSE TRIM(Sector_Focus)
        END,
        Family = CASE
            -- BL-LANG-EN (2026-05-09): idioma objetivo EN. Corrección inversa ES→EN.
            WHEN TRIM(Family) = 'RV Core'                THEN 'Equity Core'
            WHEN TRIM(Family) = 'RV Temática'            THEN 'Thematic Equity'
            WHEN TRIM(Family) = 'Mixtos'                 THEN 'Multi-Asset'
            WHEN TRIM(Family) = 'Renta Fija Corto Plazo' THEN 'Short-Term Fixed Income'
            WHEN TRIM(Family) = 'Renta Fija Flexible'    THEN 'Flexible Fixed Income'
            WHEN TRIM(Family) = 'Monetario'              THEN 'Money Market'
            WHEN TRIM(Family) = 'Retorno Absoluto'       THEN 'Absolute Return'
            WHEN TRIM(Family) = 'Activos Reales'         THEN 'Real Assets'
            WHEN TRIM(Family) = 'RF High Yield'          THEN 'High Yield'
            WHEN TRIM(Family) = 'RF Emergentes'          THEN 'Emerging Market Debt'
            WHEN TRIM(Family) = 'RF Inflación'           THEN 'Inflation-Linked'
            WHEN TRIM(Family) = 'Flexible Estratégico'   THEN 'Strategic Allocation'
            WHEN TRIM(Family) = 'Estructurado'           THEN 'Structured'
            WHEN TRIM(Family) = 'Orientado a Renta'      THEN 'Income Oriented'
            ELSE TRIM(Family)
        END
    ;
    """
    cursor = conn.execute(sql)
    metrics["rows_affected"] = cursor.rowcount if cursor.rowcount is not None else -1
    conn.commit()

    # BL-SF-EN: 'Servicios Financieros' → canónico v20 'Financial Services'
    # (v20 §2A.1 #6 no contempla 'Financials & Insurance'). Unifica al canónico.
    conn.execute("""
        UPDATE fund_master
        SET Sector_Focus = 'Financial Services'
        WHERE TRIM(Sector_Focus) = 'Servicios Financieros'
    """)
    metrics["sf_financials_resolved"] = conn.execute(
        "SELECT COUNT(*) FROM fund_master WHERE Sector_Focus = 'Financial Services'"
    ).fetchone()[0]
    conn.commit()

    return metrics


# ============================================================
# COST SCHEDULE UPSERT (v19 — BL-COST-2)
# ============================================================

def upsert_cost_schedule(
    conn: sqlite3.Connection,
    isin: str,
    schedule_rows: list,
) -> int:
    """
    Persiste filas de fund_cost_schedule para un fondo.

    NO usada en Sprint 1 (el extractor PRIIPs no existe todavía).
    Sí usada en Sprint 2 por priips_cost_extractor.py y ucits_cost_extractor.py.

    Política: DELETE previo de todas las filas del ISIN + INSERT de las nuevas.
    La función de coste por horizonte es atómica por fondo; si cambia la
    extracción, la versión antigua debe reemplazarse íntegra, no fusionarse.

    Args:
        conn: conexión activa con isolation_level=None.
        isin: ISIN del fondo (debe existir en fund_master).
        schedule_rows: lista de dicts con claves obligatorias:
            Horizon_Years (float), Is_RHP (0|1), Source (str).
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
                row["Horizon_Years"],
                int(row.get("Is_RHP", 0)),
                row.get("Total_Costs_EUR"),
                row.get("Total_Costs_Pct"),
                row.get("Annual_Impact_Pct"),
                row["Source"],
            ),
        )
    return len(schedule_rows)


# ============================================================
# BL-COST-4d: Infraestructura para corrección OC-ACI mismatch
# ============================================================
#
# Esta función NO se llama desde pipeline.py en Sprint 2.
# Es infraestructura para BL-COST-5 (sesión Opus separada).
# Proporciona la ruta de escritura no-COALESCE para corregir
# Ongoing_Charge_Recurrent en los ~328 fondos donde el valor
# en BD es ACI@RHP mal etiquetado como TER.
#
# Diseño (Principio #1 Root Cause):
#   La causa raíz (mezcla TER/ACI en la columna legacy) se corrige
#   en BL-COST-5 con análisis exhaustivo. Esta función es solo la
#   palanca de escritura. No implementa ninguna heurística de detección.
# ============================================================

def correct_oc_aci_mismatch(
    conn: sqlite3.Connection,
    isin: str,
    ter_pct: float,
    source_note: str = "BL-COST-5",
) -> bool:
    """
    Sobrescribe Ongoing_Charge_Recurrent directamente (sin COALESCE)
    para corregir un fondo donde el valor en BD es ACI@RHP, no TER.

    Solo debe invocarse desde BL-COST-5 (validación exhaustiva previa).
    NO usar desde el pipeline normal — viola la política COALESCE estándar.

    Args:
        conn:        conexión activa con isolation_level=None (WAL).
        isin:        ISIN del fondo a corregir.
        ter_pct:     TER corregido en porcentaje entero (ej: 0.70 para 0.70%).
        source_note: etiqueta para logging.

    Returns:
        True si la fila fue actualizada, False si ISIN no existe en fund_master.
    """
    import logging as _logging
    _logger = _logging.getLogger(__name__)

    cur = conn.execute(
        "UPDATE fund_master SET Ongoing_Charge_Recurrent = ?, Updated_At = ? "
        "WHERE ISIN = ?",
        (
            ter_pct,
            datetime.datetime.utcnow().isoformat(timespec="seconds"),
            isin,
        ),
    )
    updated = cur.rowcount > 0
    if updated:
        _logger.info(
            "[%s] [%s] Ongoing_Charge_Recurrent corregido: %.4f%% (no-COALESCE)",
            isin, source_note, ter_pct,
        )
    return updated
