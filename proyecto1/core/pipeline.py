# -*- coding: utf-8 -*-
"""
core/pipeline.py  — v38

Cambios v38 (2026-05-23 — BL-COST-4c-FIX):
  BL-COST-4c-FIX-1  Kill-switch PRIIPS_COST_EXTRACTION_ENABLED leído en runtime
                    desde _priips_ext_mod en lugar de a nivel de módulo.
                    Causa raíz: shared.config no está en sys.path cuando
                    run_block.py importa pipeline.py; el try/except caía
                    silenciosamente a _COST_ENABLED=False en toda la sesión.
  BL-COST-4c-FIX-2  proyecto1/core/ añadido a sys.path antes del import de
                    los extractores. Causa raíz: priips_cost_extractor y sus
                    dependencias (cost_format_router, cost_table_parser, etc.)
                    se importan entre sí sin prefijo 'core.', requieren que
                    proyecto1/core/ esté en sys.path. run_block.py solo añade
                    proyecto1/, no proyecto1/core/.

Cambios v37 (2026-05-23 — BL-COST Sprint 2 S2-C):
  BL-COST-4c  Bloque de extracción de costes integrado en el ciclo principal,
              tras Geography y antes de publish_fund.
              Routing PRIIPs/UCITS via detect_kid_format (DRY cost_format_router).
              _schedule_rows pasado a publish_fund (A-3 S2-C, atomicidad).
              Imports condicionales: pipeline no rompe si módulos ausentes.

Cambios v36 (2026-05-09):
  BL-LANG-EN  Family y Type: idioma objetivo cambiado a inglés.
              Puntos corregidos en pipeline:
                BL-48 fallback Family Monetario → "Money Market".
                BL-64e _RFC_INCOMPATIBLE_FAMILIES: valores ES → EN
                  ("RF Emergentes"→"Emerging Market Debt", etc.).
                  Asignaciones correctivas Family/Type → EN canónico.

Cambios v35 (2026-05-08):
  BL-65b  Comparacion Credit_Quality actualizada: Family="Income Oriented"
          (antes "Orientado a Renta"). Alineado con classify_utils BL-65b.

Cambios v34 (2026-05-08):
  BL-64a  Filtro Data_Quality_Flag antes de validate_classification_contract.
          Causa raiz: restantes.py emite DQ_Flag en su dict -> contrato falla.

  BL-64b  BL-44 defensivo: int(float(str(_srri44))) con try/except.
          Causa raiz: fondos sin SRRI (NaN) pasan is not None; int(NaN) falla.

  BL-64c  Sector_Focus ES->EN en punto canonico. 266 fondos.

  BL-64d  Family 'Orientado a Renta' -> 'Income Oriented' residual BD. 104 fondos.

  BL-64e  INTER Nature↔Family: RFC con Family de RF Flexible -> corregir a RFC.
          3 fondos BGF China Bond afectados.

Cambios v32 (2026-05-08):
  BL-63  Investment_Focus default por Fund_Nature (P11b).
         Causa raiz: 466 fondos con IF=NULL tras DLA-1. Los bloques RF_CORTO
         y MONETARIOS nunca asignaron Investment_Focus. El valor Broad previo
         se preservaba por COALESCE desde ciclos anteriores; con DLA-1 y la
         expansion de BL-44 (mas fondos a RESTANTES), ese mecanismo deja de
         funcionar para fondos cuyo texto cambio.
         Fix: default deterministico en el punto canonico de defaults:
           - Renta Fija Corto Plazo -> Broad
           - Monetario              -> Broad
           - Renta Variable         -> Broad (si no detectado)
           - Renta Fija Flexible    -> Broad (si no detectado)
           - Mixtos                 -> Broad (si no detectado)
           - Alternativo/Estructurado/Restantes: sin default (semantica ambigua)
         Impacto esperado: IF NULL baja de 466 a ~20 (Alternativo/Estructurado/Restantes)
         Control SQL post-ejecucion:
           SELECT COUNT(*) FROM fund_master WHERE Investment_Focus IS NULL;
           -- Objetivo: < 30

Cambios v31 (2026-04-30):
  Revert BL-65  Revertir la desviación introducida en v30. La decisión
                aprobada el 29-abr (opción A) establece que cuando BL-44
                dispara, Fund_Nature='Restantes' SIEMPRE (nunca None ni
                la inferida). El BL-65 de v30 asignaba Fund_Nature=None
                lo que causaba errores NOT NULL constraint failed en BD.
                Cambios:
                  - BL-44: Fund_Nature='Restantes' incondicional cuando
                    Nature/SRRI son incompatibles.
                  - Import detect_nature_from_name eliminado (ya no se usa).
                  - Tags [BL44] → [BL-44] y [BL62] → [BL-62] normalizados.
                  - validate_classification_contract: sin relajación para
                    RESTANTES (Fund_Nature=None rechazado universalmente).

  Logging Ola 1  Tags normalizados [BL44]→[BL-44], [BL62]→[BL-62],
                [NORM]→[NORM-XXX] según normativa sección 7 v2.
                Resumen de incidencias agregado al final del ciclo.

Cambios v30 (2026-04-30):
  BL-65  [REVERTIDO en v31] Corrección semántica en BL-44: "Restantes" no es una Fund_Nature
         válida. Cuando un fondo Mon/RFCP es incompatible con su SRRI, la
         acción correcta es re-inferir la naturaleza real (o dejar NULL),
         no asignar Fund_Nature="Restantes".
         Fix: BL-44 ahora intenta inferir la naturaleza real desde nombre
         vía detect_nature_from_name(). Si lo consigue → asigna esa naturaleza
         y dispara BL-62 para re-inferir Type/Family coherentes.
         Si no lo consigue → Fund_Nature=None, Data_Quality_Flag=WARN.
         El flag _bl44_force_overwrite sigue activo en ambos casos para
         garantizar sobrescritura en BD (BL-64).
         validate_classification_contract: relajado para RESTANTES —
         Fund_Nature=None permitido cuando el bloque no puede determinarlo
         (marca DQ=WARN en lugar de lanzar excepción).
         Import classify_utils ampliado: detect_nature_from_name (BL-65).

Cambios v29 (2026-04-30):
  BL-44 v2  Fix R-4: la regla BL-44 leía Fund_Nature y SRRI solo desde el
            dict del ciclo. Para fondos CACHED donde el bloque no re-emite
            esos campos (vienen como None en el dict), el predicado nunca
            disparaba aunque BD contuviera Nature=Monetario y SRRI=4.
            Fix: leer valores efectivos (_nat44_bd, _srri44_bd) desde BD
            vía consulta dedicada antes del bloque BL-44. Usar
            `_X_eff = record.get('X') or _X_bd` para ambos campos.
            Resultado: cobertura correcta de fondos CACHED (causa raíz R-4).

  BL-62     Post-corrección BL-44: cuando un fondo termina con
            Fund_Nature='Restantes' por BL-44, sus Type/Family heredados
            de la clasificación errónea original son falsos por construcción.
            Nuevo bloque tras BL-44 que invoca
            propagate_nature_to_restantes_type_family() de classify_utils.
            Estrategia: inferencia léxica desde Fund_Name (Fase 2) con
            fallback a NULL+DQ=WARN (Fase 3). Marca flags
            _bl62_force_overwrite_family/_type para que BL-64 en
            sqlite_writer fuerce sobrescritura sin COALESCE.
            Import classify_utils ampliado: propagate_nature_to_restantes_type_family.

core/pipeline.py  — v28

Cambios v28 (2026-04-29):
  BL-61  Fix preventivo Strategy ↔ Replication_Method (REGLA INTER-1).
         Causa raíz: la lógica P03 (líneas anteriores al bloque INTER)
         solo rellenaba Replication_Method cuando era NULL. Si un bloque
         clasificador emitía Strategy='Indexado'/'Pasivo'/'Factor' con
         Replication_Method='ACTIVE' ya poblado (ej. bloques RV), la
         inconsistencia sobrevivía al pipeline sin corrección.
         validate_all_semantic_consistency() en classify_utils solo se
         invocaba desde restantes.py; fondos de otros bloques nunca pasaban
         por ella.
         Fix: añadir bloque INTER-1 explícito en el punto universal de
         correcciones INTER de pipeline.py, que cubre TODOS los fondos
         (nuevos, CACHED, cualquier bloque de origen).
         Invoca validate_strategy_replication() de classify_utils para
         mantener DRY — el validador existente ya implementa la lógica.
         Import classify_utils ampliado: validate_strategy_replication.

  BL-49/4  Detección Currency_Hedged desde texto KIID (segunda fase).
           Actúa solo si CH sigue NULL tras nombre + BL-31 + BL-45 + BL-49/3.
           Restringe a Fund_Currency ≠ EUR (los EUR sin CH son genuinamente
           unhedged sin necesidad de señal explícita).
           Invoca detect_currency_hedged_from_kiid() de classify_utils v7
           (10 patrones HEDGED + 8 UNHEDGED, inglés y español).
           Import classify_utils ampliado: detect_currency_hedged_from_kiid.

  BL-49/2  Tres fixes para la regresión Hedged→Unhedged detectada en ciclo
           del 25/04/2026 (7 fondos):
           Fix 1 (fund_characterizer.detect_currency_hedged):
             Ampliado _HEDGED con variantes EURH/USDH/GBPH/CHFH (sin "DG"
             final) que aparecen en iShares/Candriam/GAM/GS/Amundi.
           Fix 2 (pipeline default conservador, regex _has_hedge_signal):
             Ampliado para detectar EURH/USDH/GBPH/CHFH/EURHDG/etc. SIN
             word boundary interno (causa raíz: \\b entre EUR y HDG falla
             porque ambos son letras).
           Fix 3 (condición de entrada del default conservador):
             Considera ahora valores en BD (_ch_bd, _hp_bd) además del
             record actual. Sin esto, CACHED con _needs_char=False entra
             al default con None y sobreescribe valor BD real.

Cambios v26 (2026-04-25):
  BL-50  Inferencia inversa Investment_Universe → Geography para los casos
         unívocos donde Universe está poblado y Geography=NULL:
           Universe='Global'   → Geography='Global'  (unívoco al 100%)
           Universe='Liquidity' + Fund_Currency='EUR' → Geography='Europa'
           Universe='Liquidity' + Fund_Currency='USD' → Geography='EEUU'
         Para Country/Regional con Geography=NULL: no se infiere (sin valor
         canónico sin información adicional — requieren auditoría manual).
         Bloque insertado después de BL-52 (corrección Country→Regional).

  BL-54  Bloque P10 (Sector_Focus desde Theme): eliminado dict inline con
         17 entradas hardcoded. Sustituido por llamada a
         map_theme_to_sector_focus(_theme) importada desde classify_utils.
         Principio #2 DRY: un único punto de verdad para el mapeo Theme→Sector.
         Import classify_utils ampliado: apply_post_characterize_normalization,
         map_theme_to_sector_focus.

  BL-56  apply_post_characterize_normalization(classification) invocada
         después de mezclar resultado de characterize_fund y antes de
         validate_classification_contract. Garantiza cobertura universal
         de normalización lingüística (Sector_Focus, Type, Family) sobre
         todos los fondos (nuevos Y CACHED). Principio #2 DRY.

Pipeline canónico de Proyecto 1:
- carga maestro
- IO documental KIID
- parsing genérico (texto + visual)
- clasificación por bloque
- persistencia en SQLite
"""

import datetime
import time
import gc
import importlib
from pathlib import Path
from typing import Optional, List, Dict, Any

import pandas as pd
import re

from core.io import get_kiid_for_isin
from core.kiid_parser import parse_kiid_generic, detect_wrong_kiid_document
from core.classify_utils import (
    detect_strategy        as _detect_strategy,
    detect_benchmark_type  as _detect_benchmark_type,
    detect_theme           as _detect_theme_pipeline,       # ← NUEVO
    apply_post_characterize_normalization,                   # BL-56
    map_theme_to_sector_focus,                              # BL-54
    validate_strategy_replication,                          # BL-61
    detect_currency_hedged_from_kiid,                       # BL-49
    propagate_nature_to_restantes_type_family,              # BL-62
    detect_explicit_equity_majority,                        # INTER-DBLCLAIM
    detect_nature_from_benchmark,                           # INTER-DBLCLAIM (voto 3/3)
    detect_nature_from_kiid,                                # INTER-VOTE3
    resolve_rf_subtype,                                     # INTER-VOTE3
    _NATURE_CANONICAL,                                      # INTER-VOTE3
    resolve_nature_vote,                                    # OPT-B: nature-first vote (superseded)
    resolve_nature_evidence,                                # OPT-B3: evidence-weighted classifier
    _NATURE_TO_BLOCK,                                       # OPT-B: nature → block routing (R-1)
    detect_fx_share_class_mismatch,                         # BL-44-FX
    detect_asset_currency_from_name,                        # BL-44-FX / Asset_Currency
    detect_asset_currency_from_kiid_text,                   # Asset_Currency (fallback)
    detect_fund_currency_from_name,                         # FIX-FUNDCCY-2 (cross-check)
    detect_geography           as detect_geography_from_name,   # FIX-GEO-1 (cross-check)
    detect_geography_from_kiid,                             # FIX-GEO-1 (cross-check)
    _derive_geography_en,                                   # FIX-GEO-1 (traducción ES→EN)
    derive_development_status,                               # FIX-GEO-1 (recalculado, no heredado del bloque)
    validate_geography_universe,                             # FIX-GEO-4 (BL-52, única fuente de verdad)
    validate_all_semantic_consistency,                       # Fase 4: validación universal
    semantic_validation_to_dq_tuples,                       # Fase 4: DQ persistence
    RFC_INCOMPATIBLE_FAMILIES,                              # BL-64e / BL-64E-INLINE (P#11 DRY)
)
try:
    from proyecto1.core.fund_characterizer import characterize_fund
except ImportError:
    from core.fund_characterizer import characterize_fund
from core.sqlite_writer import (
    publish_fund,
    log_ingestion,
    correct_oc_aci_mismatch,             # BL-COST-5: OC/ACI mismatch correction
    global_post_pipeline_normalize_db,   # BL-53/56/57: barrido global
)
from core._db_utils import EffectiveReader   # BL-49/50: lectura efectiva
# P1-19: única definición de la escala de coste (P#11 / R-1)
try:
    from core.cost_scale import OC_RATIO_MAX as _OC_RATIO_MAX
except ImportError:                          # proyecto1/core ya en sys.path
    from cost_scale import OC_RATIO_MAX as _OC_RATIO_MAX

# BL-COST-4c: extractores Sprint 2 (kill-switch interno en cada módulo)
# Import condicional: el pipeline no rompe si los módulos no están presentes.
# sys.path: proyecto1/core/ debe estar presente porque priips_cost_extractor
# y sus dependencias (cost_format_router, etc.) se importan entre sí sin
# prefijo 'core.'. run_block.py añade proyecto1/ pero no proyecto1/core/.
import sys as _sys
_core_dir = str(Path(__file__).resolve().parent)
if _core_dir not in _sys.path:
    _sys.path.insert(0, _core_dir)
try:
    from core.priips_cost_extractor import extract_priips_costs
    from core.ucits_cost_extractor  import extract_ucits_costs
    import core.priips_cost_extractor as _priips_ext_mod  # para leer kill-switch en runtime
    _COST_EXTRACTORS_AVAILABLE = True
except ImportError as _e_cost_import:
    _COST_EXTRACTORS_AVAILABLE = False
    _priips_ext_mod = None
    print(f"[BL-COST-4c] ImportError: {_e_cost_import}")

# Nota: PRIIPS_COST_EXTRACTION_ENABLED se lee en runtime desde _priips_ext_mod
# (no a nivel de módulo) porque shared.config puede no estar en sys.path
# cuando pipeline.py se importa desde run_block.py (BL-COST-4c-FIX).

# ── v20 (INTEGRATED_SPEC_v20_v2 — Job B): arbitración dual de coste ──────────
# Import defensivo del callable core; kill-switch leído en runtime (mismo motivo
# que PRIIPS_COST_EXTRACTION_ENABLED: config puede no estar en sys.path en import).
try:
    from cost_arbitration import arbitrate_costs_from_pdf
    _ARB_AVAILABLE = True
except ImportError:
    try:
        from core.cost_arbitration import arbitrate_costs_from_pdf
        _ARB_AVAILABLE = True
    except ImportError as _e_arb_import:
        _ARB_AVAILABLE = False
        arbitrate_costs_from_pdf = None
        print(f"[DLA2-ARB] ImportError: {_e_arb_import}")


def _dla2_arbitration_enabled() -> bool:
    """Lee DLA2_ARBITRATION_ENABLED en runtime (config = dependency leaf)."""
    try:
        from config import DLA2_ARBITRATION_ENABLED as _f
        return bool(_f)
    except ImportError:
        try:
            from shared.config import DLA2_ARBITRATION_ENABLED as _f
            return bool(_f)
        except ImportError:
            return False

#print("[DEBUG] Cargo pipeline.py")        

# -------------------------------------------------
# Carga de maestro (USADO POR run_block.py)
# -------------------------------------------------

# FIX-MASTER-LOAD-2 (2026-07-05): guardián de formato ISIN.
# Causa raíz: la hoja "Franklin" del maestro contiene una fila de
# cabecera repetida con el texto literal "Código ISIN" en la columna
# ISIN. El filtro .notna() previo no la elimina (es un string no-nulo).
# Un ISIN real tiene siempre exactamente 12 caracteres: 2 letras de
# país + 9 alfanuméricos + 1 dígito de control. Validado contra las
# 22,407 celdas no-nulas del maestro real: solo 1 fallo (el falso ISIN).
_ISIN_RE = re.compile(r"^[A-Za-z]{2}[A-Za-z0-9]{9}[0-9]$")

# FIX-HEDGCCY-2 (2026-07-12): patrón genérico de clase cubierta por sufijo
# de clase [A-Z]{1,2}H inmediatamente antes de ACC/INC/DIS[T].
# Convención estándar UCITS: AH, BH, CH, DH, EH, ZH… = "X hedged".
# Límite {1,2} excluye "HIGH" (4 chars) y otros falsos positivos de palabras
# comunes. Se evalúa JUNTO con _explicit_hedge_suffixes (OR-lógico).
# FIX-HEDGCCY-3 (2026-07-12): extended to allow optional parenthetical currency
# "(CCY)" between the class-code H and the distribution type, covering conventions
# like "BH (EUR) INC" (Robeco HY Bonds BH EUR class).
_GENERIC_HEDGE_CLASS_PAT = re.compile(
    r"\b[A-Z]{1,2}H\s+(?:\([A-Z]{2,3}\)\s+)?(?:ACC|INC|DIS[T]?)\b"
)

# Sufijos de cobertura con divisa explícita (ej. EURH, USDHDG, GBPHDG).
# Clave = Fund_Currency; valor = regex para el sufijo en el nombre del fondo.
_EXPLICIT_HEDGE_SUFFIXES: dict[str, re.Pattern] = {
    # FIX-HEDGCCY-3 (2026-07-12): added "EUR HD" for "Euro Hedged" abbreviated
    # suffix used by Nordea (e.g. "NORDEA GL STBL EQ EUR HD BC AC").
    "EUR": re.compile(r"EUR\s*H(?:DG|G)?(?!\w)|EUR\s+HD\b|EURHEDGE"),
    "USD": re.compile(r"USD\s*H(?:DG|G)?(?!\w)|USDHEDGE"),
    "GBP": re.compile(r"GBP\s*H(?:DG|G)?(?!\w)|GBPHEDGE"),
    "CHF": re.compile(r"CHF\s*H(?:DG|G)?(?!\w)|CHFHEDGE"),
    "SEK": re.compile(r"SEK\s*H(?:DG|G)?(?!\w)"),
    "NOK": re.compile(r"NOK\s*H(?:DG|G)?(?!\w)"),
    "JPY": re.compile(r"JPY\s*H(?:DG|G)?(?!\w)"),
}


def _is_valid_isin(value) -> bool:
    """Devuelve True si 'value' tiene formato de ISIN (2 letras + 9 alfanum + 1 dígito)."""
    return isinstance(value, str) and bool(_ISIN_RE.match(value.strip()))


def load_master_excel(path: Path) -> pd.DataFrame:
    xls = pd.ExcelFile(path)

    isin_candidates = {"isin", "codigo isin", "código isin", "isin code"}
    name_candidates = {"nombre", "nombre de fondo", "nombrefondo", "fund_name"}

    frames = []

    for sheet in xls.sheet_names:
        df = xls.parse(sheet)

        # normalizar columnas
        norm_cols = {str(c).strip().lower(): c for c in df.columns}

        isin_col = next((norm_cols[c] for c in isin_candidates if c in norm_cols), None)
        name_col = next((norm_cols[c] for c in name_candidates if c in norm_cols), None)

        if not isin_col or not name_col:
            # FIX-MASTER-LOAD-1 (2026-07-05): antes este `continue` era
            # silencioso -- causa raíz de que la hoja "Wellington" (sin fila
            # de cabecera, datos empezando en la fila 0) desapareciera del
            # universo del maestro sin ningún aviso: 23 ISIN nunca llegaron
            # a fund_master (nunca se descargó KIID, nunca se clasificaron).
            # Ahora se avisa explícitamente para que este tipo de hueco no
            # vuelva a pasar inadvertido si otra hoja tiene el mismo problema.
            print(
                f"[MASTER-LOAD-WARNING] Hoja '{sheet}' omitida del maestro: "
                f"no se encontró columna ISIN/Nombre reconocible "
                f"(columnas detectadas: {list(df.columns)})"
            )
            continue

        df = df.rename(columns={
            isin_col: "ISIN",
            name_col: "Fund_Name",
        })

        df = df[df["ISIN"].notna()].copy()

        # FIX-MASTER-LOAD-2: eliminar celdas ISIN con formato inválido
        # (e.g. filas de cabecera repetidas como "Código ISIN" en Franklin).
        _valid = df["ISIN"].astype(str).map(_is_valid_isin)
        if not _valid.all():
            _dropped = df.loc[~_valid, "ISIN"].astype(str).tolist()
            print(
                f"[MASTER-LOAD-WARNING] Hoja '{sheet}': {len(_dropped)} valor(es) "
                f"ISIN con formato no válido descartados: {_dropped}"
            )
        df = df[_valid].copy()

        # la gestora viene del nombre de la hoja
        df["Management_Company"] = sheet.strip()

        frames.append(df[["ISIN", "Fund_Name", "Management_Company"]])

    if not frames:
        raise ValueError("No se ha encontrado ninguna hoja válida con columna ISIN.")

    master_df = pd.concat(frames, ignore_index=True)
    return master_df


def load_master_db(conn) -> pd.DataFrame:
    """
    Load the fund master from db_document_catalogue (latest harvest).
    Returns DataFrame with ISIN, Fund_Name, Management_Company — same schema as load_master_excel.
    Raises ValueError if the table has no valid ISINs (run --harvest first).
    """
    sql = """
        SELECT
            isin                AS ISIN,
            MIN(fund_name)      AS Fund_Name,
            MIN(gestora_label)  AS Management_Company
        FROM db_document_catalogue
        WHERE isin IS NOT NULL
          AND isin != ''
          AND harvest_ts = (SELECT MAX(harvest_ts) FROM db_document_catalogue)
        GROUP BY isin
        ORDER BY isin
    """
    df = pd.read_sql_query(sql, conn)

    if df.empty:
        raise ValueError(
            "db_document_catalogue vacío o sin ISINs. "
            "Ejecuta: python proyecto1/harvest/p1_db_harvest.py --harvest"
        )

    _valid = df["ISIN"].map(_is_valid_isin)
    n_dropped = int((~_valid).sum())
    if n_dropped:
        print(
            f"[MASTER-LOAD-WARNING] DB: {n_dropped} ISIN(s) con formato no válido descartados"
        )
        df = df[_valid].copy()

    print(f"[MASTER-LOAD-DB] {len(df)} fondos cargados desde db_document_catalogue (harvest MAX)")
    return df.reset_index(drop=True)


def load_master_excelPrevio(path: Path) -> pd.DataFrame:
    xls = pd.ExcelFile(path)
    isin_candidates = {"isin", "codigo isin", "código isin", "isin code"}
    name_candidates = {"nombre", "nombre de fondo", "nombrefondo", "fund_name"}
    management_candidates = {"gestora", "gestora del fondo", "Management_Company"}

    for sheet in xls.sheet_names:
        df = xls.parse(sheet)
        for c in df.columns:
            print("[DEBUG] columnas df " + c)        
        norm_cols = {str(c).strip().lower(): c for c in df.columns}

        isin_col = next((norm_cols[c] for c in isin_candidates if c in norm_cols), None)
        name_col = next((norm_cols[c] for c in name_candidates if c in norm_cols), None)
        mgmt_col = next((norm_cols[c] for c in management_candidates if c in norm_cols), None)
        
        if isin_col and name_col:
            col_map = {isin_col: "ISIN", name_col: "Fund_Name"}
            if mgmt_col:
                col_map[mgmt_col] = "Management_Company"
            
            
            df = df.rename(columns=col_map)
            df = df[df["ISIN"].notna()]
            return df

    raise ValueError("No se ha encontrado columna ISIN en el maestro.")



# -------------------------------------------------
# Utilidades
# -------------------------------------------------

def dynamic_getattr(mod, names):
    for n in names:
        if hasattr(mod, n):
            return getattr(mod, n)
    return None


# -------------------------------------------------
# Data Quality derivation (canónica)
# -------------------------------------------------
def _derive_data_quality_flag(parsed: dict) -> str:
    srri_q = parsed.get("SRRI_Quality_Flag")

    if srri_q in (None, "NONE"):
        return "MISSING"

    if srri_q == "LOW_CONFLICT":
        return "WARN"

    return "OK"


# FIX-DQ-1 (2026-07-05): rollup determinista de Data_Quality_Flag a partir
# de múltiples issues concurrentes -- ver comentario junto a `_dq_issues`
# en el bucle principal para el porqué (antes: mutaciones secuenciales con
# guards inconsistentes, y al menos un caso -- INTER_NTC_CONTRADICTION --
# donde el propio log_ingestion quedaba condicionado al guard y se perdía
# por completo cuando otro chequeo ya había tocado el flag).
def _finalize_data_quality_issues(
    conn, isin: str, base_level: str,
    issues: list[tuple[str, str, str, str]],
) -> str:
    """
    Vuelca TODOS los issues acumulados para este ISIN a ingestion_log
    (incondicionalmente, uno por uno) y a fund_data_quality_issues
    (reemplazando cualquier fila de un ciclo anterior para este ISIN),
    y devuelve el Data_Quality_Flag final como el máximo de severidad
    entre `base_level` (derivado de SRRI_Quality_Flag) y el `dq_level`
    de cada issue.

    issues: lista de (check_code, dq_level, log_status, message).
    """
    from shared.config import DATA_QUALITY_SEVERITY

    for check_code, dq_level, log_status, message in issues:
        log_ingestion(conn, isin, check_code, log_status, message)

    conn.execute(
        "DELETE FROM fund_data_quality_issues WHERE ISIN = ?", (isin,)
    )
    if issues:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        conn.executemany(
            "INSERT INTO fund_data_quality_issues "
            "(ISIN, check_code, level, message, detected_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (isin, check_code, dq_level, message, now)
                for check_code, dq_level, _log_status, message in issues
            ],
        )

    levels = [base_level] + [dq_level for _, dq_level, _, _ in issues]
    return max(levels, key=lambda lvl: DATA_QUALITY_SEVERITY.get(lvl, 0))



def _get_available_isins(conn, df_master: pd.DataFrame) -> list[str]:
    """
    Devuelve los ISIN del maestro que aún no han sido clasificados
    en ningún bloque previo (Heuristic_Block IS NULL).
    """
    all_isins = set(df_master["ISIN"].dropna().unique())

    rows = conn.execute(
        """
        SELECT DISTINCT ISIN
        FROM fund_master
        WHERE Heuristic_Block IS NOT NULL
        """
    ).fetchall()

    classified_isins = {r[0] for r in rows if r[0]}

    return sorted(all_isins - classified_isins)



_CANON_KEYS_CACHE = None


def _canonical_attribute_keys() -> set:
    """Fuente única de verdad para las claves canónicas (v20): el catálogo de
    atributos de config (R-1/§2). Evita la lista hardcodeada v17 que derivó del
    schema (causa raíz del fallo de contrato post-v20).

    'Is_ESG' es legacy: los bloques aún lo emiten y la lógica Sfdr de pipeline lo
    consume (no se persiste en v20 — la columna fue eliminada, sustituida por
    Sfdr_Article). Se tolera hasta limpiar los bloques. Las señales internas
    transitorias (_signal_*) se ignoran por convención de prefijo '_'.
    """
    global _CANON_KEYS_CACHE
    if _CANON_KEYS_CACHE is None:
        try:
            from shared.config import ATTRIBUTE_CATALOG as _cat
        except Exception:
            from config import ATTRIBUTE_CATALOG as _cat
        _CANON_KEYS_CACHE = set(_cat.keys()) | {"Is_ESG"}
    return _CANON_KEYS_CACHE


def validate_classification_contract(
    classification: Dict[str, Any],
    block_name: str,
    isin: str,
) -> None:
    CANONICAL_KEYS = _canonical_attribute_keys()

    # --- claves inesperadas ---
    # v20: ignorar señales internas transitorias (_signal_type/_signal_subtype),
    # que no son atributos canónicos y nunca se persisten.
    extra_keys = {k for k in classification if not k.startswith("_")} - CANONICAL_KEYS
    if extra_keys:
        raise ValueError(
            f"[{block_name}] ISIN {isin} - claves no canónicas: {extra_keys}"
        )

    # --- clave obligatoria ---
    # BL-65: RESTANTES puede emitir Fund_Nature=None cuando no puede determinar
    # la naturaleza financiera real. Todos los demás bloques sí la requieren.
    if classification.get("Fund_Nature") is None and block_name != "RESTANTES":
        raise ValueError(
            f"[{block_name}] ISIN {isin} - Fund_Nature es obligatoria"
        )

    # --- tipos ---
    # Is_ESG es int (0/1), el resto son str o None
    _INT_KEYS = {"Is_ESG"}
    for k, v in classification.items():
        if v is None:
            continue
        if k in _INT_KEYS:
            if not isinstance(v, int):
                raise ValueError(
                    f"[{block_name}] ISIN {isin} - {k} debe ser int, got {type(v)}"
                )
        elif not isinstance(v, str):
            raise ValueError(
                f"[{block_name}] ISIN {isin} - {k} debe ser str o None (recibido {type(v)})"
            )





# -------------------------------------------------
# Ejecución de bloque
# -------------------------------------------------

# OPT-B3: umbral de confianza de resolve_nature_evidence por debajo del cual la
# clasificación de Fund_Nature se marca con un DQ WARNING para revisión humana.
# 0.5 marca ~170 fondos del corpus (los casos de conflicto de señales / falsos
# positivos KIID que antes parcheaban INTER-DBLCLAIM/INTER-VOTE3).
_NATURE_CONF_THRESHOLD: float = 0.5


def run_block(
    block_module,
    df_master: pd.DataFrame,
    conn,
    master_excel_path: Optional[Path] = None,
    sample_size: Optional[int] = None,
    stop_on_error: bool = False,
    list_isin: Optional[List[str]] = None,
    kiid_source: str = "auto",
    nature_first: bool = False,
    recompute_costs: bool = False,
) -> List[Dict[str, Any]]:

    # OPT-B: in nature_first mode block_module may be None; skip block-specific setup
    if nature_first:
        block_name = "NATURE_FIRST"
        heuristic_core = 1      # updated per-fund after dispatch
        get_universe = None
    else:
        block_name = getattr(block_module, "BLOCK_NAME", block_module.__name__).upper()
        heuristic_core = 0 if block_name == "RESTANTES" else 1
        get_universe = dynamic_getattr(
            block_module,
            ["get_universe_isins", "get_heuristic_isins", "get_universe", "get_isins"],
        )
        if not get_universe:
            raise AttributeError(f"{block_module.__name__} no expone función de universo.")

    # BL-KIID-LOCAL-FIRST: traza de la modalidad de carga del binario KIID.
    print(f"[{block_name}] kiid_source={kiid_source}")

    # -----------------------------
    # Selección de ISINs
    # -----------------------------
    if list_isin:
        isins = list_isin
    elif nature_first:
        # OPT-B: all ISINs from master; nature vote decides block per-fund.
        # FIX (2026-07-17): .unique() — el maestro lista cada ISIN en ~7 hojas
        # de gestora (22.406 filas / 3.227 ISINs únicos). Sin dedup se procesaba
        # cada fondo ~7 veces (7× duración + 7× ingestion_log). Los bloques ya
        # deduplican vía get_universe_isins().unique(); esta rama debe igualarlo.
        _master_rows = int(df_master["ISIN"].dropna().shape[0])
        isins = df_master["ISIN"].dropna().astype(str).unique().tolist()
        # Guard de dedup: hace visible en el log una regresión de duplicados.
        print(f"[NATURE_FIRST] universo: {len(isins)} ISINs únicos "
              f"(maestro: {_master_rows} filas, dedup {_master_rows/max(1,len(isins)):.1f}x)")
        if _master_rows > len(isins) * 1.5:
            print(f"[NATURE_FIRST] nota: el maestro lista cada ISIN en varias "
                  f"hojas; se procesa una vez por ISIN único (dedup OK).")
        isins = isins[:sample_size] if sample_size else isins
    else:
        # RESTANTES es bloque residual: necesita conn para excluir ISINs ya clasificados
        universe = get_universe(df_master, conn) if heuristic_core == 0 else get_universe(df_master)
        isins = universe[:sample_size] if sample_size else universe

    # Excluir fondos con documento erróneo — aplica a todos los bloques
    wrong_doc = {
        r[0] for r in conn.execute(
            "SELECT ISIN FROM fund_kiid_metadata WHERE KIID_Status = 'WRONG_DOC'"
        ).fetchall() if r[0]
    }
    if wrong_doc:
        before = len(isins)
        isins = [i for i in isins if i not in wrong_doc]
        excluded = before - len(isins)
        if excluded:
            print(f"[{block_name}] {excluded} ISINs excluidos por KIID_Status=WRONG_DOC")

    total = len(isins)
    published = []
    _cycle_start_ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    try:
        log_ingestion(
            conn, None, "RUN_START", "INFO",
            f"nature_first={nature_first} cycle_start={_cycle_start_ts}"
        )
    except Exception:
        pass

    # ── SC-H: batch-load fund_benchmarks once per block run ──────────────────
    # Prefer MORNINGSTAR (independent market signal) over KIID (same source as
    # classifier). Falls back to KIID when no Morningstar row exists.
    # Carries: asset_class, benchmark_role, benchmark_name, confidence.
    # A single SELECT avoids per-ISIN queries in the hot loop.
    _bmk_by_isin: dict = {}
    try:
        _bmk_rows = conn.execute("""
            SELECT ISIN, source, asset_class, benchmark_role, benchmark_name, confidence
            FROM fund_benchmarks
            WHERE source IN ('MORNINGSTAR','KIID')
        """).fetchall()
        # Two-pass: MORNINGSTAR takes priority over KIID.
        _seen_ms: set = set()
        for _bi in _bmk_rows:
            _b_isin, _b_src, _b_ac, _b_role, _b_name, _b_conf = _bi
            if _b_src == 'MORNINGSTAR':
                _bmk_by_isin[_b_isin] = {
                    "asset_class":    _b_ac,
                    "benchmark_role": _b_role or "asset_proxy",
                    "benchmark_name": _b_name,
                    "confidence":     _b_conf or "HIGH",
                }
                _seen_ms.add(_b_isin)
        for _bi in _bmk_rows:
            _b_isin, _b_src, _b_ac, _b_role, _b_name, _b_conf = _bi
            if _b_src == 'KIID' and _b_isin not in _seen_ms:
                _bmk_by_isin[_b_isin] = {
                    "asset_class":    _b_ac,
                    "benchmark_role": _b_role or "asset_proxy",
                    "benchmark_name": _b_name,
                    "confidence":     _b_conf or "MEDIUM",  # KIID = semi-redundant
                }
    except Exception as _bmk_err:
        print(f"[WARN] SC-H: no se pudo cargar fund_benchmarks: {_bmk_err}")

    # ── OPT-B3: batch-load realized-volatility band (srri_nav) once ──────────
    # Ground-truth anchor/veto for resolve_nature_evidence. None for funds
    # without NAV history (P2 not yet run) → engine falls back to ex-ante only.
    _srri_nav_by_isin: dict = {}
    if nature_first:
        try:
            for _si, _sv in conn.execute(
                "SELECT ISIN, CAST(ROUND(value) AS INT) FROM fund_metrics "
                "WHERE metric='srri_nav' AND horizon='since_inception' "
                "AND real_flag=0 AND value IS NOT NULL"
            ).fetchall():
                if _sv is not None:
                    _srri_nav_by_isin[_si] = max(1, min(7, int(_sv)))
        except Exception as _srri_err:
            print(f"[WARN] OPT-B3: no se pudo cargar srri_nav: {_srri_err}")

    for idx, isin in enumerate(isins, 1):
        _t_fund_start = time.perf_counter()
        _t_phases: dict = {}          # desglose por fase

        ts = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {block_name} {isin} ({idx}/{total})")

        kiid_text = kiid_meta = parsed = classification = pdf_bytes = None

        # BL-49/50: lector de valor efectivo (dict ciclo > BD > None) con
        # caché por ISIN. Centraliza el patrón de fallback a BD para todas
        # las reglas INTER posteriores. Una sola SELECT por fondo (lazy).
        eff = EffectiveReader(conn, isin)

        try:
            row = df_master[df_master["ISIN"] == isin]
            if row.empty:
                log_ingestion(conn, isin, f"{block_name}_MASTER", "WARN", "ISIN not in master")
                continue

            row0 = row.iloc[0]
            fund_name = row0.get("Fund_Name", "")
            mgmt = row0.get("Management_Company")

            _t0 = time.perf_counter()
            kiid_text, kiid_meta = get_kiid_for_isin(
                isin,
                str(master_excel_path) if master_excel_path else None,
                conn=conn,
                kiid_source=kiid_source,
            )
            _t_phases["kiid_fetch"] = round((time.perf_counter() - _t0) * 1000)
            if not kiid_text:
                log_ingestion(conn, isin, f"{block_name}_KIID", "WARN", kiid_meta.get("KIID_Error"))

                continue

            pdf_bytes = kiid_meta.pop("KIID_PDF_BYTES", None)

            # Recuperar SRRI_Visual previo de la BD
            _srri_visual_prev  = None
            _srri_textual_prev = None
            _row = conn.execute(
                "SELECT SRRI_Visual, SRRI_Textual FROM fund_kiid_metadata "
                "WHERE ISIN=? AND KIID_Class=1",
                (isin,)
            ).fetchone()
            if _row:
                if _row[0] is not None:
                    _srri_visual_prev  = int(_row[0])
                if _row[1] is not None:
                    _srri_textual_prev = int(_row[1])

            # SRRI_Visual_Recovery: ELIMINADA (v16)
            # Razón: los únicos casos con pdf_bytes=None son CACHED y OK.
            # Para esos fondos, si SRRI_Visual=NULL en BD, la extracción visual
            # requiere una re-descarga → usar FORCE_REFRESH explícito vía SQL:
            #   UPDATE fund_kiid_metadata SET KIID_Status='FORCE_REFRESH'
            #   WHERE SRRI_Visual IS NULL AND KIID_Class=1;
            # La Recovery automática causaba descargas masivas no controladas
            # (throttling del servidor) al procesar RESTANTES con 2000+ fondos.

            _t0 = time.perf_counter()
            parsed = parse_kiid_generic(kiid_text, pdf_bytes=pdf_bytes,
                                        isin=isin, fund_name=fund_name,
                                        srri_visual_prev=_srri_visual_prev,
                                        srri_textual_prev=_srri_textual_prev)
            _t_phases["kiid_parse"] = round((time.perf_counter() - _t0) * 1000)
            # v20 (§4.2): NO liberar pdf_bytes aquí. El bloque de coste y la
            # arbitración dual (hook más abajo) reutilizan el MISMO binario,
            # abierto una sola vez por fondo (DRY). Se libera tras el hook.

            # ── B1/B2 (2026-07-11): Detector de documento erróneo ────────
            # Detecta textos que son estatutos SICAV coordinados o informes
            # anuales en lugar de KIIDs de fondo único. Estos documentos
            # contaminan los atributos extraídos (SRRI, Family, Geography)
            # con valores de sub-fondos hermanos. KIID_Status='OK' no protege.
            # Política: CACHE → FORCE_REFRESH (1 reintento limpio);
            #           REMOTE/LOCAL → ya re-descargado, sigue mal → WRONG_DOC.
            # En ambos casos se hace `continue` para no clasificar con texto
            # contaminado. El estado WRONG_DOC excluye el fondo de todos los
            # bloques en ciclos posteriores (ver KIID_Status state machine).
            _wrong_doc_reason = detect_wrong_kiid_document(
                kiid_text, srri=parsed.get("SRRI")
            )
            if _wrong_doc_reason:
                _kiid_src = (kiid_meta or {}).get("KIID_Source", "CACHE")
                if _kiid_src == "CACHE":
                    with conn:
                        conn.execute(
                            "UPDATE fund_kiid_metadata SET KIID_Status='FORCE_REFRESH' "
                            "WHERE ISIN=? AND KIID_Class=1", (isin,)
                        )
                    log_ingestion(conn, isin, "KIID_WRONG_DOC_RETRY", "INFO",
                                  f"FORCE_REFRESH marcado (1er intento): {_wrong_doc_reason}")
                else:
                    with conn:
                        conn.execute(
                            "UPDATE fund_kiid_metadata SET KIID_Status='WRONG_DOC' "
                            "WHERE ISIN=? AND KIID_Class=1", (isin,)
                        )
                    log_ingestion(conn, isin, "KIID_WRONG_DOC", "WARN",
                                  f"WRONG_DOC (fuente incorrecta, re-descarga confirmó): "
                                  f"{_wrong_doc_reason}")
                continue  # No clasificar con texto contaminado

            # ── Override universal: Estructurado ─────────────────────────
            _t0 = time.perf_counter()
            _name_l = (fund_name or "").lower()
            _structured_kw = ["autocall", "structured", "estructurado",
                              "capital protec", "guaranteed", "barrier"]
            _is_structured = any(k in _name_l for k in _structured_kw)

            _bench = parsed.get("Benchmark_Declared")
            _srri_for_classify = parsed.get("SRRI")
            # BL-SRRI-GUARD: si parsed["SRRI"] es dict (path CACHED anómalo),
            # extraer el escalar antes de int(). Previene '>= dict int'.
            if isinstance(_srri_for_classify, dict):
                _srri_for_classify = _srri_for_classify.get("SRRI")

            if nature_first:
                # OPT-B3 (2026-07-16): evidence-weighted classifier → dispatch.
                # KIID-primary + guarded name-override (Monetario/RFC) + benchmark
                # coverage/corroboration + realized-vol veto. Retires INTER-DBLCLAIM
                # + INTER-VOTE3 (nature resolved once, with a confidence + trace).
                _srri_band = _srri_nav_by_isin.get(isin)
                # FIX-NLC-MSBENCH-1 (2026-07-21): pass Morningstar asset_class
                # from _bmk_by_isin as additional corroboration/arbitration signal.
                _ms_asset_cls = _bmk_by_isin.get(isin, {}).get("asset_class")
                _voted_nature, _nat_conf, _ev_trace = resolve_nature_evidence(
                    _name_l, kiid_text, benchmark_declared=_bench,
                    srri_nav_band=_srri_band,
                    ext_asset_class=_ms_asset_cls,
                )
                _dispatch_blk = _NATURE_TO_BLOCK.get(_voted_nature) if _voted_nature else None
                if _dispatch_blk and not _is_structured:
                    try:
                        _dispatch_mod = importlib.import_module(f"blocks.{_dispatch_blk}")
                    except ImportError:
                        _dispatch_mod = importlib.import_module(f"proyecto1.blocks.{_dispatch_blk}")
                    _dispatch_clf = dynamic_getattr(_dispatch_mod, ["classify_fund"])
                    if _dispatch_clf:
                        try:
                            classification = _dispatch_clf(
                                fund_name, kiid_text,
                                benchmark_declared=_bench,
                                srri_parsed=int(_srri_for_classify) if _srri_for_classify else None,
                            )
                        except TypeError:
                            classification = _dispatch_clf(fund_name, kiid_text)
                        classification["Fund_Nature"] = _voted_nature
                    else:
                        classification = {"Fund_Nature": _voted_nature}
                    heuristic_core = 0 if _dispatch_blk == "restantes" else 1
                    log_ingestion(
                        conn, isin, "OPT_B3_DISPATCH", "INFO",
                        f"[OPT-B3] {_ev_trace['reason']} conf={_nat_conf} "
                        f"name={_ev_trace['name']} kiid={_ev_trace['kiid']} "
                        f"bench={_ev_trace['benchmark']} "
                        f"msbench={_ev_trace.get('msbench')} vol={_srri_band} "
                        f"→ {_voted_nature} → dispatch={_dispatch_blk}"
                    )
                    # Baja confianza → DQ flag (revisión). Sustituye el parcheo
                    # INTER-DBLCLAIM/VOTE3 por señalización explícita.
                    if _nat_conf < _NATURE_CONF_THRESHOLD:
                        log_ingestion(
                            conn, isin, "NATURE_LOW_CONFIDENCE", "WARN",
                            f"[OPT-B3] Fund_Nature={_voted_nature} con confianza baja "
                            f"({_nat_conf} < {_NATURE_CONF_THRESHOLD}): "
                            f"{_ev_trace['reason']} — revisar."
                        )
                else:
                    # Structured override or all-abstain → restantes minimum classification
                    try:
                        _rest_mod = importlib.import_module("blocks.restantes")
                    except ImportError:
                        _rest_mod = importlib.import_module("proyecto1.blocks.restantes")
                    _rest_clf = dynamic_getattr(_rest_mod, ["classify_fund"])
                    if _rest_clf:
                        try:
                            classification = _rest_clf(
                                fund_name, kiid_text,
                                benchmark_declared=_bench,
                                srri_parsed=int(_srri_for_classify) if _srri_for_classify else None,
                            )
                        except TypeError:
                            classification = _rest_clf(fund_name, kiid_text)
                    else:
                        classification = {"Fund_Nature": _voted_nature or "Restantes"}
                    heuristic_core = 0
            else:
                # Original block dispatch
                classifier = dynamic_getattr(block_module, ["classify_fund"])
                if classifier:
                    try:
                        classification = classifier(fund_name, kiid_text,
                                                    benchmark_declared=_bench,
                                                    srri_parsed=int(_srri_for_classify) if _srri_for_classify else None)
                    except TypeError:
                        classification = classifier(fund_name, kiid_text)
                else:
                    classification = {}

            # ── Override Fund_Nature si es estructurado ──────────────────
            if _is_structured:
                classification["Fund_Nature"] = "Estructurado"

            # INTER-DBLCLAIM (2026-07-04): tiebreaker para fondos reclamados
            # por nombre tanto por renta_variable como por mixtos. Causa raíz:
            # mixtos.get_universe_isins() usa patrones de nombre muy genéricos
            # ("growth"/"income"/"dynamic"/"moderate"/"conservative") que son
            # también descriptores de ESTILO comunes en renta variable pura
            # (Growth investing, Income/dividend equity). mixtos se ejecuta
            # DESPUÉS de renta_variable en el pipeline, así que sobrescribe
            # silenciosamente vía COALESCE la clasificación correcta. Auditoría
            # de 149 fondos con doble-reclamo: AB American Growth Portfolio
            # ("mínimo 80%...en valores de renta variable"), Allianz EU EQ
            # Growth ("mínimo 70%..."), Fidelity European/American Growth,
            # JPM Europe Dynamic -- todos renta variable pura mal clasificada
            # como Mixtos. Cuando el bloque MIXTOS clasifica Fund_Nature=
            # 'Mixtos' pero BD ya tiene 'Renta Variable' (de un pase anterior
            # de renta_variable en este mismo ciclo, o de un ciclo previo) Y el
            # KIID declara explícitamente un umbral mayoritario de renta
            # variable (≥60%), se re-clasifica con renta_variable.classify_fund()
            # en lugar de aceptar la sobrescritura de mixtos -- corrección
            # completa (Family/Type/etc.), no solo un parche de Fund_Nature.
            # OPT-B: nature vote resolved this upfront; INTER-DBLCLAIM is a no-op in nature_first mode.
            if not nature_first and classification.get("Fund_Nature") == "Mixtos" and block_name == "MIXTOS":
                _bd_nature_dblclaim = conn.execute(
                    "SELECT Fund_Nature FROM fund_master WHERE ISIN=?", (isin,)
                ).fetchone()
                _bd_nature_dblclaim = _bd_nature_dblclaim[0] if _bd_nature_dblclaim else None
                if _bd_nature_dblclaim == "Renta Variable":
                    _eq_pct = detect_explicit_equity_majority(kiid_text)
                    # FIX-P1-BENCH-VOTE (2026-07-04): cuando el KIID no declara
                    # un umbral % explícito (ni numérico ni en palabras), se
                    # consulta el índice de referencia como tercer voto
                    # independiente antes de aceptar la sobrescritura de mixtos.
                    _bench_vote = None
                    if _eq_pct is None or _eq_pct < 60:
                        _bench_vote = detect_nature_from_benchmark(_bench)
                    if (_eq_pct is not None and _eq_pct >= 60) or _bench_vote == "Renta Variable":
                        try:
                            _rv_mod = importlib.import_module("blocks.renta_variable")
                        except ImportError:
                            _rv_mod = importlib.import_module("proyecto1.blocks.renta_variable")
                        try:
                            classification = _rv_mod.classify_fund(
                                fund_name, kiid_text,
                                benchmark_declared=_bench,
                                srri_parsed=int(_srri_for_classify) if _srri_for_classify else None,
                            )
                        except TypeError:
                            classification = _rv_mod.classify_fund(fund_name, kiid_text)
                        if _eq_pct is not None and _eq_pct >= 60:
                            log_ingestion(
                                conn, isin, "INTER_DBLCLAIM_RV_WINS", "INFO",
                                f"Doble-reclamo renta_variable+mixtos: KIID declara "
                                f"{_eq_pct}% mínimo en renta variable → se preserva "
                                f"Fund_Nature='Renta Variable' (mixtos no sobrescribe)"
                            )
                        else:
                            log_ingestion(
                                conn, isin, "INTER_DBLCLAIM_RV_WINS_BENCHMARK", "INFO",
                                f"Doble-reclamo renta_variable+mixtos: KIID sin umbral "
                                f"% explícito, pero Benchmark_Declared='{_bench}' es "
                                f"índice de renta variable → se preserva "
                                f"Fund_Nature='Renta Variable' (mixtos no sobrescribe)"
                            )

            # INTER-VOTE3 (2026-07-04): control de doble-verificación universal,
            # extendido a TODOS los bloques y todo Fund_Nature (no solo el
            # doble-reclamo mixtos+renta_variable de INTER-DBLCLAIM arriba).
            # Tres señales independientes: Nombre (bloque ya asignado vía
            # get_universe_isins), Texto-KIID (detect_nature_from_kiid +
            # resolve_rf_subtype) y Benchmark_Declared (detect_nature_from_
            # benchmark). Cuando Texto-KIID y Benchmark COINCIDEN entre sí en
            # un valor distinto del ya asignado, se re-clasifica con el
            # classify_fund() del bloque correspondiente al valor acordado.
            # Auditoría full-corpus (2026-07-04): 62 fondos con este doble
            # acuerdo independiente frente al valor ya asignado, en 5 bloques
            # de origen distintos (MIXTOS, RESTANTES, MONETARIOS, ALTERNATIVOS,
            # RENTA_VARIABLE) -- confirma que el gap no es exclusivo de un
            # bloque (caso JPMorgan Europe High Yield Bond mal clasificado
            # como Renta Variable pese a nombre truncado "H.YIEL.B.D" que los
            # excludes de texto no capturan; DWS ESG Euro Money Market Fund
            # mal clasificado como Renta Variable en RESTANTES).
            # Excepción Monetario: detect_nature_from_kiid() solo llega a
            # "Monetario" por dos vías -- patrones MMF explícitos (fiables) o
            # el árbitro de último recurso "SRRI==1" (línea ~1730), que puede
            # coincidir por casualidad con fondos absolute-return/macro que
            # declaran su benchmark en términos de tipo de interés monetario
            # como OBJETIVO DE RENTABILIDAD relativo, no como descripción de
            # sus tenencias (confirmado: JPM Global Macro Opportunities
            # LU0095938881/LU0115098948, SRRI bajo + benchmark ESTR overnight
            # usado como "revalorización superior a su índice de referencia
            # monetario" -- no es un fondo monetario). Por eso, para Monetario
            # se exige además una frase MMF explícita en el propio texto KIID.
            # OPT-B: nature vote resolved this upfront; INTER-VOTE3 is a no-op in nature_first mode.
            _v3_current_nature = classification.get("Fund_Nature")
            if not nature_first and _v3_current_nature and kiid_text:
                _v3_raw = detect_nature_from_kiid(kiid_text)
                if _v3_raw == "_RF_pending":
                    _v3_raw = resolve_rf_subtype(_name_l, kiid_text)
                _v3_kiid_nature = _NATURE_CANONICAL.get(_v3_raw) if _v3_raw else None
                _v3_bench_nature = detect_nature_from_benchmark(_bench)

                def _v3_coarse(_n):
                    return "Renta Fija" if _n in (
                        "Renta Fija Corto Plazo", "Renta Fija Flexible"
                    ) else _n

                if (_v3_kiid_nature and _v3_bench_nature
                        and _v3_coarse(_v3_kiid_nature) == _v3_bench_nature
                        and _v3_coarse(_v3_current_nature) != _v3_bench_nature):
                    # Monetario queda fuera de la reclasificación automática:
                    # detect_nature_from_kiid() solo llega a "Monetario" vía
                    # patrones MMF explícitos (evaluados con múltiples guards
                    # ya afinados dentro de la propia función) o el árbitro de
                    # último recurso SRRI==1 -- replicar aquí esos mismos
                    # guards duplicaría lógica (viola R-1). Confirmado con
                    # falso positivo real: JPM Global Macro Opportunities
                    # menciona "mercado monetario" solo para una asignación
                    # SECUNDARIA de liquidez (hasta 10%), no como estrategia
                    # primaria -- una regex simple aquí no distingue eso.
                    # Se registra como aviso (Data_Quality_Flag) igual que
                    # INTER-NTC, sin reclasificar.
                    if _v3_bench_nature == "Monetario":
                        log_ingestion(
                            conn, isin, "INTER_VOTE3_MONETARIO_FLAG_ONLY", "WARN",
                            f"KIID-text+Benchmark ('{_bench}') sugieren Monetario "
                            f"frente a '{_v3_current_nature}' asignado por bloque "
                            f"{block_name}, pero no se reclasifica automáticamente "
                            f"(riesgo de falso positivo por asignación secundaria de "
                            f"liquidez o benchmark usado como objetivo relativo) -- "
                            f"revisar manualmente"
                        )
                        _v3_target_block = None
                    else:
                        _v3_target_block = {
                            "Renta Variable":        "renta_variable",
                            "Renta Fija Corto Plazo":"rf_corto",
                            "Renta Fija Flexible":   "rf_flexible",
                        }.get(_v3_kiid_nature)
                    if _v3_target_block:
                        try:
                            _v3_mod = importlib.import_module(f"blocks.{_v3_target_block}")
                        except ImportError:
                            _v3_mod = importlib.import_module(f"proyecto1.blocks.{_v3_target_block}")
                        _v3_classifier = dynamic_getattr(_v3_mod, ["classify_fund"])
                        if _v3_classifier:
                            try:
                                classification = _v3_classifier(
                                    fund_name, kiid_text,
                                    benchmark_declared=_bench,
                                    srri_parsed=int(_srri_for_classify) if _srri_for_classify else None,
                                )
                            except TypeError:
                                classification = _v3_classifier(fund_name, kiid_text)
                            log_ingestion(
                                conn, isin, "INTER_VOTE3_RECLASSIFIED", "INFO",
                                f"Doble señal independiente KIID-text+Benchmark "
                                f"('{_bench}') acuerdan '{_v3_kiid_nature}' frente a "
                                f"'{_v3_current_nature}' asignado por bloque "
                                f"{block_name} → reclasificado"
                            )

            _t_phases["classify"] = round((time.perf_counter() - _t0) * 1000)

            # ── Enriquecer con fund_characterizer ────────────────────────
            # Solo ejecutar si hay atributos v3 por rellenar (Investment_Universe,
            # Accumulation_Policy, Currency_Hedged, etc.) o si la clasificación
            # es nueva/actualizada. Para fondos CACHED ya clasificados, comprueba
            # si los atributos v3 ya están en BD antes de invocar el characterizer.
            _kiid_status_c = kiid_meta.get("KIID_Status", "CACHED")
            _needs_char = (_kiid_status_c != "CACHED")  # siempre para re-descargas
            if not _needs_char:
                # Para CACHED: verificar si faltan atributos v3 en BD
                # P06: ampliado para detectar Geography=NULL y
                #      inconsistencia Nature/Investment_Universe (P09)
                _v3_row = conn.execute(
                    "SELECT Investment_Universe, Accumulation_Policy, Hedging_Policy, "
                    "Investment_Focus, Credit_Quality, Geography, Fund_Nature, "
                    # v19 BL-COST-2: añadir KID_Format y Cost_Extraction_Quality (R-3)
                    "KID_Format, Cost_Extraction_Quality "
                    "FROM fund_master WHERE ISIN=?", (isin,)
                ).fetchone()
                if _v3_row is None:
                    _needs_char = True
                else:
                    # Campos v3 originales (NULL → re-char)
                    _needs_char = any(v is None for v in _v3_row[:5])
                    # P06: Geography NULL
                    if not _needs_char and _v3_row[5] is None:
                        _needs_char = True
                    # P09: Investment_Universe incoherente con Nature
                    if not _needs_char:
                        _db_universe = _v3_row[0]
                        _db_nature = _v3_row[6]
                        if (_db_universe == "Liquidity"
                                and _db_nature not in (
                                    "Monetario", "Renta Fija Corto Plazo")):
                            _needs_char = True
                    # v19 BL-COST-2 R-3: KID_Format o Cost_Extraction_Quality NULL
                    # → re-characterize para que Sprint 2 pueda poblarlos al re-procesar.
                    # Nota: en Sprint 1, ambas columnas son NULL en TODOS los fondos,
                    # por lo que TODOS los fondos CACHED entrarán al characterize.
                    # Comportamiento esperado: prepara el terreno para Sprint 2.
                    if not _needs_char and (_v3_row[7] is None or _v3_row[8] is None):
                        _needs_char = True

            if _needs_char:
                _srri_for_char = parsed.get("SRRI") or classification.get("SRRI")
                _char_result = characterize_fund(
                    fund_name=fund_name,
                    kiid_text=kiid_text,
                    fund_nature=classification.get("Fund_Nature") or "",
                    srri=int(_srri_for_char) if _srri_for_char else None,
                    pre_assigned={
                        k: v for k, v in classification.items()
                        if k not in ("Fund_Nature",) and v is not None
                    },
                    # BL-27: pasar benchmark_declared para inferir Market_Cap_Focus
                    **({
                        "benchmark_declared": parsed.get("Benchmark_Declared")
                    } if parsed.get("Benchmark_Declared") else {}),
                )
                # Mezclar: el resultado del bloque tiene precedencia,
                # fund_characterizer solo rellena los None.
                # v20: el characterizer es legacy y aún emite claves eliminadas
                # (Type/Subtype/Currency_Hedged) y señales; se filtra al conjunto
                # canónico (fuente única = config) para no inyectarlas en classification.
                _canon = _canonical_attribute_keys()
                for _k, _v in _char_result.items():
                    if _k.startswith("_") or _k not in _canon:
                        continue
                    if _k not in classification or classification[_k] is None:
                        classification[_k] = _v

            # ── BL-56: Normalización lingüística post-characterize ────────
            # Aplica TYPE/FAMILY/SECTOR_FOCUS al idioma objetivo (Principio #8).
            # Punto único de mantenimiento — classify_utils.apply_post_characterize_normalization().
            # Se ejecuta aquí (después de consolidar bloque+characterizer) para
            # garantizar cobertura universal: fondos nuevos Y fondos CACHED.
            classification = apply_post_characterize_normalization(classification)

            # --- validación estricta ---
            # BL-64a: filtrar claves no-canonicas emitidas por bloques externos.
            # restantes.py emite Data_Quality_Flag directamente -> contrato falla.
            for _nc in ("Data_Quality_Flag", "data_quality_flag"):
                classification.pop(_nc, None)
            validate_classification_contract(
                classification=classification,
                block_name=block_name,
                isin=isin,
            )

            # --- copia defensiva ---
            classification = dict(classification)

            # --- warning si clasificación mínima ---
            if all(
                classification.get(k) is None
                for k in classification
                if k != "Fund_Nature"
            ):
                log_ingestion(
                    conn,
                    isin,
                    f"{block_name}_CLASSIFICATION",
                    "WARN",
                    "Clasificación mínima (solo Fund_Nature)",
                )

            # FIX-DQ-1 (2026-07-05): acumulador de issues de calidad de datos
            # para este ISIN. Antes, cada chequeo (BL-65, FUNDCCY/ASSETCCY,
            # HEDGCCY, INTER-NTC...) mutaba Data_Quality_Flag directamente,
            # cada uno con su propio guard ('== OK', '!= WARN', sin guard) --
            # inconsistente, y en el caso de INTER-NTC el propio log_ingestion
            # quedaba condicionado al guard, perdiendo el registro por completo
            # cuando un chequeo anterior ya había tocado el flag. Ahora cada
            # chequeo solo hace .append(...) aquí; el rollup final (máximo de
            # severidad) y el volcado a ingestion_log + fund_data_quality_issues
            # se hacen una sola vez, de forma incondicional, justo antes de
            # publish_fund (ver _finalize_data_quality_issues más abajo).
            #
            # Cada entrada es (code, dq_level, log_status, message):
            #   - dq_level:  vocabulario de Data_Quality_Flag (OK/INFERRED/
            #     WARN/MISSING/ERROR) -- alimenta el rollup por severidad y
            #     la columna `level` de fund_data_quality_issues.
            #   - log_status: vocabulario de ingestion_log.status (ERROR/
            #     WARNING/WARN/INFO/DEBUG, ver convención en CLAUDE.md) --
            #     son dos vocabularios distintos que coincidían por accidente
            #     en varios casos existentes (p.ej. "WARN"/"WARN") pero no en
            #     todos (RC-08 ya usaba log_status='INFO' con dq_level=
            #     'INFERRED' -- una inferencia de fallback no es un evento de
            #     severidad WARNING, pero sí debe degradar el flag agregado).
            _dq_issues: list[tuple[str, str, str, str]] = []

            # BL-65: RESTANTES puede emitir Fund_Nature=None cuando no puede
            # determinar la naturaleza financiera real. En ese caso forzar DQ=WARN
            # para trazabilidad y auditoría manual posterior.
            if block_name == "RESTANTES" and classification.get("Fund_Nature") is None:
                _dq_issues.append((
                    "BL65_NATURE_UNKNOWN", "WARN", "WARN",
                    "Fund_Nature no determinable por RESTANTES → Data_Quality_Flag=WARN"
                ))

            # C3 (BL-44 hardening 2026-07-11): fondo MMF confirmado por nombre
            # (VNAV/LVNAV/CNAV) con SRRI anómalo en el KIID almacenado. El bloque
            # monetarios.py preserva Fund_Nature='Monetario' pero pone el flag
            # _bl44_srri_anomaly para que aquí se emita un DQ WARN visible en
            # fund_data_quality_issues → auditadle sin pipeline re-run.
            _bl44_anomaly = classification.get("_bl44_srri_anomaly")
            if _bl44_anomaly is not None:
                _dq_issues.append((
                    "BL44_SRRI_ANOMALY", "WARN", "WARNING",
                    f"MMF confirmado por nombre (VNAV/LVNAV/CNAV) con SRRI={_bl44_anomaly} "
                    f"anómalo en KIID — posible KIID de subfondo incorrecto (revisar manualmente)"
                ))

            # FIX-FUNDCCY-3 / FIX-ASSETCCY-3 (2026-07-05): precedencia unificada
            # COALESCE(texto KIID, nombre) para AMBAS divisas -- antes,
            # Fund_Currency solo usaba el texto KIID (sin fallback a nombre) y
            # Asset_Currency usaba el nombre COMO PRIMARIO (orden inverso). Se
            # unifica el criterio: el texto KIID es la fuente regulatoria
            # (documento oficial), el nombre es un fallback/cross-check de
            # convención de mercado. Verificado corpus-wide antes de aplicar:
            # para Asset_Currency, invertir el orden no cambia NINGÚN valor
            # (0 desacuerdos de 186 fondos con ambas señales presentes) -- es
            # una unificación de criterio sin coste, no una corrección.
            _fundccy_kiid = parsed.get("Fund_Currency")
            _fundccy_name = detect_fund_currency_from_name(fund_name)
            _assetccy_kiid = detect_asset_currency_from_kiid_text(kiid_text)
            _assetccy_name = detect_asset_currency_from_name(fund_name)

            # FIX-GEO-1 (2026-07-05): mismo backbone COALESCE(texto KIID,
            # nombre) que las divisas -- antes, Geography se resolvía
            # exclusivamente desde el nombre dentro de cada bloque
            # (detect_geography), y detect_kiid_attributes() solo consultaba
            # el texto KIID cuando el nombre no daba señal (fallback, no
            # cross-check). Auditoría de corpus (crossValidateFundAttribute)
            # encontró bugs de raíz en AMBOS extractores antes de llegar a
            # cablear este COALESCE -- ver FIX-GEO-NAME-1 / FIX-GEO-KIID-1 en
            # classify_utils.py. Se recalculan ambas señales de forma
            # independiente aquí (no se reutiliza classification.get(
            # "Geography"), que ya viene mezclada nombre-primero desde el
            # bloque) para poder comparar y registrar el desacuerdo.
            _geo_name_l = (fund_name or "").lower()
            _geo_kiid = detect_geography_from_kiid(kiid_text)
            _geo_name = detect_geography_from_name(_geo_name_l)
            # El valor elegido (ES, vocabulario interno) debe pasar por la
            # MISMA traducción ES→EN que aplica derive_v20_attributes al
            # valor del bloque -- si no, un desacuerdo resuelto aquí a favor
            # del texto KIID dejaría "Geography" en español sin traducir
            # (bug detectado en el propio dry-run de backfill: classification
            # .get("Geography") ya viene traducido por el bloque, pero un
            # valor recalculado de nuevo en pipeline.py no pasaba por
            # _derive_geography_en). Development_Status se recalcula igual,
            # en vez de heredar classification.get("Development_Status")
            # (que reflejaría la Geography ANTIGUA decidida por el bloque,
            # no la de este COALESCE).
            # FIX-GEO-7 (2026-07-06): when KIID says 'Global' but the fund name
            # carries a specific country/region signal, the name is more
            # authoritative. KIID boilerplate ("invests in global bond/equity
            # markets") is generic and often attached to country-focused funds
            # (e.g. BGF CHINA BOND → KIID says "global bond markets"; 69 funds
            # affected in the corpus). The opposite is not done: when KIID says
            # a specific country, it may be right even if the name says 'Global'
            # (e.g. ROBECO GLOBL PREM invests in China), so KIID keeps priority
            # in all cases except the clear KIID=Global/name=specific mismatch.
            _GEO_GLOBAL = "Global"
            # FIX-GEO-8 (2026-07-13) / FIX-GEO-11 (2026-07-14):
            # name is a geographic sub-region of the KIID value → name wins.
            # FIX-GEO-8 example: TEMPLETON EASTERN EURO (name=Europa del Este,
            #   KIID=Europa) — name pinpoints sub-region; KIID describes container.
            # FIX-GEO-11 extension: same logic for EM container regions —
            #   KIID='Emergentes' + name=concrete EM sub-region (Europa del Este,
            #   India, China, Latinoamérica, Asia) → name wins.
            #   KIID='Asia' + name='India' → name wins (India ⊂ Asia-Pacific).
            _EM_SPECIFIC_SUBREGIONS = frozenset([
                "Europa del Este", "India", "China", "Latinoamérica", "Asia"
            ])
            _is_name_subregion = bool(
                (_geo_kiid == "Europa" and _geo_name == "Europa del Este")
                or (_geo_kiid == "Emergentes" and _geo_name in _EM_SPECIFIC_SUBREGIONS)
                or (_geo_kiid == "Asia" and _geo_name == "India")
            )
            _geo_name_wins = bool(
                (_geo_kiid == _GEO_GLOBAL and _geo_name and _geo_name != _GEO_GLOBAL)
                or _is_name_subregion
            )
            _geo_es_final = _geo_name if _geo_name_wins else (_geo_kiid or _geo_name)
            _geo_en_final = _derive_geography_en(_geo_es_final, _geo_name_l)
            _devstat_final = derive_development_status(_geo_es_final, _geo_en_final, _geo_name_l)

            fund_master_record = {
                # -------------------------
                # Identidad
                # -------------------------
                "ISIN": isin,
                "Fund_Name": fund_name,
                "Management_Company": mgmt,

                # -------------------------
                # Clasificación canónica
                # (producida por el bloque)
                # -------------------------
                "Fund_Nature": classification.get("Fund_Nature"),

                "Profile": classification.get("Profile"),
                # v20: Type→Vehicle_Structure (renombrada). El engine
                # derive_v20_attributes la fija en `classification`.
                "Vehicle_Structure": classification.get("Vehicle_Structure"),
                "Family": classification.get("Family"),
                "Style_Profile": classification.get("Style_Profile")
                    # BL-41 v23: fallback desde parser si el bloque no asignó valor
                    or parsed.get("Style_Profile"),
                "Geography": _geo_en_final,
                "Theme": classification.get("Theme"),
                "Exposure_Bias":   classification.get("Exposure_Bias"),
                "Subtype":         classification.get("Subtype")
                    # BL-43 v23: fallback desde parser según Fund_Nature del bloque
                    or (
                        parsed.get("_Subtype_Monetario")
                        if classification.get("Fund_Nature") == "Monetario"
                        else parsed.get("_Subtype_Mixtos")
                        if classification.get("Fund_Nature") == "Mixtos"
                        else None
                    ),
                # canonico v2: Strategy e Is_ESG vienen del bloque
                "Strategy":        classification.get("Strategy") or _detect_strategy(
                    parsed.get("Replication_Method"),
                    classification.get("Subtype"),
                    (fund_name or "").lower(),
                ),
                "Is_ESG":          classification.get("Is_ESG", 0),
                # Benchmark_Type calculado con datos reales del parser
                "Benchmark_Type":  _detect_benchmark_type(
                    parsed.get("Benchmark_Declared"),
                    parsed.get("Replication_Method"),
                ),
                # Canonico v3 — fund_characterizer
                "Market_Cap_Focus":    classification.get("Market_Cap_Focus"),
                "Sector_Focus":        classification.get("Sector_Focus"),
                "Currency_Hedged":     classification.get("Currency_Hedged"),
                "Investment_Universe": classification.get("Investment_Universe"),

                # Canonico v17 — fund_characterizer
                "Investment_Focus": classification.get("Investment_Focus"),
                "Credit_Quality":  classification.get("Credit_Quality"),

                # Canonico v20 — derive_v20_attributes (engine). ROOT-CAUSE
                # (Issue-1): sin estas claves el dict cherry-pick descartaba los
                # 5 atributos nuevos antes de publish_fund → 100% NULL en BD.
                "Development_Status": _devstat_final,
                "Duration_Profile":   classification.get("Duration_Profile"),
                "MMF_Structure":      classification.get("MMF_Structure"),
                "Alt_Strategy":       classification.get("Alt_Strategy"),
                "Payoff_Profile":     classification.get("Payoff_Profile"),

                # -------------------------
                # Heurística / estado
                # -------------------------
                "Heuristic_Block": block_name,
                "Heuristic_Core": heuristic_core,

                # -------------------------
                # Parsing documental (KIID)
                # -------------------------
                "SRRI": parsed.get("SRRI"),
                # FIX-FUNDCCY-NAME (2026-07-06): name suffix (share-class
                # denomination) is definitionally the Fund_Currency for that
                # specific share class. KIID text describes the BASE FUND
                # currency, which differs for currency-hedged or multi-currency
                # share classes (e.g. EUR INC class of a USD base fund).
                # Name signal is authoritative when present; KIID as fallback.
                "Fund_Currency": _fundccy_name or _fundccy_kiid,
                "Asset_Currency": _assetccy_kiid or _assetccy_name,
                "Portfolio_Currency": parsed.get("Portfolio_Currency"),
                "Hedging_Policy": parsed.get("Hedging_Policy"),
                "Replication_Method": parsed.get("Replication_Method"),
                "Derivatives_Usage": parsed.get("Derivatives_Usage"),
                "Benchmark_Declared": parsed.get("Benchmark_Declared"),
                # v19: renombrado a Ongoing_Charge_Recurrent.
                # El parser sigue produciendo "Ongoing_Charge"; Sprint 2 desambigua
                # semánticamente (TER puro vs ACI). El valor no cambia en Sprint 1.
                # v19 BL-COST-2 R-4: Sprint 1 NO añade reglas INTER. Sprint 2 añadirá:
                #   validate_oc_vs_aci(oc_recurrent_efectivo, aci_rhp_efectivo, ...)
                # usando el patrón _X_efectivo = record.get("X") or _X_bd según R-4.
                # Las variables _oc_bd, _aci_rhp_bd, _kf_bd se añadirán al bloque de
                # lectura BD previa en Sprint 2.
                "Ongoing_Charge_Recurrent": parsed.get("Ongoing_Charge"),
                # Accumulation_Policy: combinar characterizer (nombre) + kiid_parser (texto)
                "Accumulation_Policy": (
                    classification.get("Accumulation_Policy") or
                    parsed.get("Accumulation_Policy")
                ),
                "Entry_Fee_Pct":       parsed.get("Entry_Fee_Pct"),
                "Exit_Fee_Pct":        parsed.get("Exit_Fee_Pct"),
                "Fee_Known_Flag":      parsed.get("Fee_Known_Flag"),
                "Sfdr_Article":        parsed.get("Sfdr_Article"),
                "Recommended_Holding_Period": parsed.get("Recommended_Holding_Period"),
                "Leverage_Used":       parsed.get("Leverage_Used"),
                "Liquidity_Profile":   parsed.get("Liquidity_Profile"),
                "Distribution_Frequency": parsed.get("Distribution_Frequency"),

                # -------------------------
                # QA / trazabilidad
                # -------------------------
                "Inference_Trace": parsed.get("Inference_Trace"),
                "SRRI_Quality_Flag": parsed.get("SRRI_Quality_Flag"),
                "Data_Quality_Flag": _derive_data_quality_flag(parsed),
            }

            # FIX-FUNDCCY-2 / FIX-ASSETCCY-2 (2026-07-05): cross-validación de
            # AMBAS divisas -- comparar la señal de texto KIID (autoritativa,
            # ver FIX-FUNDCCY-3 arriba) contra la señal de nombre. NO se
            # sobrescribe el valor ya asignado (COALESCE ya decidió cuál usar);
            # esto solo registra la discrepancia para revisión manual, igual
            # que INTER-NTC para Benchmark_Declared -- la auditoría que motivó
            # este chequeo encontró errores en AMBAS direcciones (118 fondos
            # JPM donde el texto KIID caía a un fallback de nivel de subfondo;
            # 2 fondos donde el nombre inducía a error y el texto KIID era
            # correcto), así que ninguna señal se trata como autoritativa por
            # sí sola a la hora de DECIDIR si hay un problema -- solo a la
            # hora de elegir qué valor persistir.
            _fundccy_mismatch = bool(
                _fundccy_name and _fundccy_kiid and _fundccy_name != _fundccy_kiid
            )
            if _fundccy_mismatch:
                # FIX-FUNDCCY-NAME: name (share-class suffix) is now the stored
                # value. Log as INFO — the disagreement is expected and resolved.
                _dq_issues.append((
                    "FUNDCCY_NAME_WINS", "INFO", "INFO",
                    f"Fund_Currency fijada por sufijo de nombre ({_fundccy_name}); "
                    f"texto KIID indicaba {_fundccy_kiid} (divisa de subfondo base)."
                ))

            _assetccy_mismatch = bool(
                _assetccy_name and _assetccy_kiid and _assetccy_name != _assetccy_kiid
            )
            if _assetccy_mismatch:
                _dq_issues.append((
                    "ASSETCCY_NAME_KIID_MISMATCH", "WARN", "WARN",
                    f"Asset_Currency (texto KIID)={_assetccy_kiid} pero el nombre "
                    f"del fondo indica {_assetccy_name} -- revisar manualmente, "
                    f"ninguna señal es autoritativa por sí sola."
                ))

            # FIX-GEO-1 (2026-07-05): cross-validación Geography, mismo
            # patrón que FUNDCCY/ASSETCCY -- no sobrescribe el valor ya
            # asignado (COALESCE ya decidió), solo registra el desacuerdo
            # para revisión manual. Verificado corpus-wide tras arreglar los
            # dos extractores (FIX-GEO-NAME-1, FIX-GEO-KIID-1): de 619 fondos
            # con ambas señales, 594 concuerdan (96%) y quedan 25 casos
            # residuales -- genuinamente ambiguos (p.ej. 'ROBECO INDIAN EQ':
            # nombre=India vs KIID=Asia, ambos correctos a distinto nivel de
            # especificidad), no bugs de extracción adicionales.
            if _geo_name_wins:
                if _is_name_subregion:
                    # FIX-GEO-8: name is a geographic sub-region of KIID → more specific wins.
                    _dq_issues.append((
                        "GEOGRAPHY_NAME_WINS", "INFO", "INFO",
                        f"Geography corregida: nombre indica {_geo_name} (sub-región de "
                        f"{_geo_kiid}); señal de nombre más específica prevalece."
                    ))
                else:
                    # FIX-GEO-7: name overrode KIID=Global → log as INFO, not WARN.
                    _dq_issues.append((
                        "GEOGRAPHY_NAME_WINS", "INFO", "INFO",
                        f"Geography corregida: nombre indica {_geo_name}, "
                        f"texto KIID decía 'Global' (genérico); se usa señal de nombre."
                    ))
            elif _geo_name and _geo_kiid and _geo_name != _geo_kiid:
                # FIX-GEO-MISMATCH-3 (2026-07-12): para fondos WRONG_DOC el texto
                # KIID es de un PDF incorrecto; la geografía KIID no es fiable.
                # No registrar mismatch -- el valor en fund_master viene de ciclos
                # anteriores con KIID correcto (COALESCE) y es más fiable que el
                # texto WRONG_DOC actual.
                # Suprimir mismatch cuando el KIID dice 'Emergentes' y el nombre
                # indica una sub-región EM: Asia, China, Latinoamérica, India.
                # La discrepancia es de especificidad, no de contradicción.
                # p.ej. PIMCO ASIA HY: KIID="Emergentes", nombre="Asia" → compatible.
                _EM_SUBREGIONS = {"Asia", "China", "India", "Latinoamérica"}
                _is_em_subregion_match = (
                    _geo_kiid == "Emergentes" and _geo_name in _EM_SUBREGIONS
                )
                if (not _is_em_subregion_match
                        and not _is_name_subregion
                        and _kiid_status_c != "WRONG_DOC"):
                    _dq_issues.append((
                        "GEOGRAPHY_NAME_KIID_MISMATCH", "WARN", "WARN",
                        f"Geography (texto KIID)={_geo_kiid} pero el nombre del "
                        f"fondo indica {_geo_name} -- revisar manualmente, "
                        f"ninguna señal es autoritativa por sí sola."
                    ))

            # FIX-HEDGCCY-1 (2026-07-05): cross-validación Hedging_Policy
            # reutilizando la comparación de divisas ya existente en
            # detect_fx_share_class_mismatch -- NO como detector independiente
            # nuevo, sino como señal de plausibilidad sobre el propio
            # Hedging_Policy almacenado: si el fondo se declara "Hedged" pero
            # Asset_Currency == Fund_Currency (no hay descalce de divisa que
            # cubrir), la combinación es lógicamente inconsistente.
            #
            # IMPORTANTE: detect_fx_share_class_mismatch() devuelve False
            # tanto para "sin descalce confirmado" como para "Asset_Currency
            # desconocida" (None) -- una conflación segura para su uso
            # original en BL-44 (donde "sin datos" debe tratarse como "no
            # eximir"), pero incorrecta aquí si se usa sin más: Asset_Currency
            # es None en ~70% del corpus por diseño conservador (ver
            # detect_asset_currency_from_name/_kiid_text), así que tratar
            # "desconocida" como "confirmada igual" habría marcado 628 fondos
            # como inconsistentes (verificado corpus-wide antes de aplicar) --
            # casi todos simplemente sin dato de Asset_Currency, no con un
            # descalce genuinamente ausente. Se sigue invocando la función
            # (fuente única de la comparación de divisas), pero se exige
            # ADEMÁS que ambas divisas estén REALMENTE pobladas antes de
            # interpretar su `False` como "confirmada igual" -- solo esa
            # combinación es evidencia positiva de que no hay descalce que
            # justifique la cobertura declarada. La dirección inversa
            # (descalce confirmado pero Hedging_Policy no dice "Hedged") NO
            # se marca -- una clase sin cobertura con divisas distintas es
            # una estructura legítima y común, no un error.
            _asset_ccy_eff = fund_master_record.get("Asset_Currency")
            _fund_ccy_eff = fund_master_record.get("Fund_Currency")
            _hedging_eff = fund_master_record.get("Hedging_Policy")
            _hedging_claims_hedged = bool(_hedging_eff) and _hedging_eff.strip().lower() in (
                "hedged", "partially hedged"
            )
            _both_currencies_known = bool(_asset_ccy_eff) and bool(_fund_ccy_eff)
            _raw_currency_mismatch = detect_fx_share_class_mismatch(
                _asset_ccy_eff, _fund_ccy_eff, hedging_policy=None, srri=None
            )
            _confirmed_no_mismatch = _both_currencies_known and not _raw_currency_mismatch
            # Suprimir false positive para share classes con sufijo de cobertura
            # en el nombre. Dos guards complementarios (OR):
            # 1. Sufijo con divisa explícita: EURH, USDHDG, GBPHDG…
            # 2. FIX-HEDGCCY-2 (2026-07-12): patrón genérico [A-Z]{1,2}H ACC/INC
            #    captura clases M&G "AH ACC", "CH INC", y similares (Jupiter AH,
            #    MS AH, Robeco DH, T.Rowe AH…) donde H = hedged sin prefijo de
            #    divisa explícito. El KIID de la clase EUR presenta activos en EUR
            #    aunque la divisa base sea GBP; la señal del nombre es autoritativa.
            _fund_name_hccy = (fund_master_record.get("Fund_Name") or "").upper()
            _is_explicit_hedge_class = (
                _fund_ccy_eff in _EXPLICIT_HEDGE_SUFFIXES
                and bool(_EXPLICIT_HEDGE_SUFFIXES[_fund_ccy_eff].search(_fund_name_hccy))
            )
            _is_generic_hedge_class = bool(_GENERIC_HEDGE_CLASS_PAT.search(_fund_name_hccy))
            if _hedging_claims_hedged and _confirmed_no_mismatch and not (
                _is_explicit_hedge_class or _is_generic_hedge_class
            ):
                _dq_issues.append((
                    "HEDGCCY_NO_MISMATCH_INCONSISTENCY", "WARN", "WARN",
                    f"Hedging_Policy={_hedging_eff!r} pero Asset_Currency == "
                    f"Fund_Currency (={_fund_ccy_eff!r}, ambas confirmadas) -- "
                    f"no hay descalce de divisa que justifique la cobertura "
                    f"declarada; revisar manualmente."
                ))

            # BL-65: si Fund_Nature=None (naturaleza no determinable), DQ=WARN
            # independientemente de la calidad SRRI (acumulado, no mutación
            # incondicional directa -- ver FIX-DQ-1).
            if fund_master_record.get("Fund_Nature") is None:
                _dq_issues.append((
                    "BL65_NATURE_NULL", "WARN", "WARN",
                    "Fund_Nature no determinable → Data_Quality_Flag=WARN"
                ))

            # RC-08: Restantes con Strategy inferido desde nombre (sin texto KIID)
            # → issue de nivel INFERRED. Causa raíz: restantes.py llama
            # _detect_strategy(None, subtype, name_l) — el primer argumento
            # (texto KIID) es None. Se acumula siempre (FIX-DQ-1): el rollup
            # final por severidad ya garantiza que un WARN/MISSING más grave
            # en otro chequeo no se vea "tapado" por este, sin necesidad de
            # condicionar el propio log_ingestion a que el flag siga en 'OK'
            # (ese gate perdía el registro por completo cuando ya había otro
            # problema — el mismo bug de fondo que INTER-NTC).
            if (fund_master_record.get("Fund_Nature") == "Restantes"
                    and fund_master_record.get("Strategy")):
                _dq_issues.append((
                    "RC08_STRATEGY_INFERRED", "INFERRED", "INFO",
                    f"Strategy='{fund_master_record['Strategy']}' inferido desde nombre"
                    " (sin texto KIID) → Data_Quality_Flag='INFERRED'"
                ))

            # Is_ESG override: SFDR Art.8/9 es más fiable que keywords en nombre
            if parsed.get("Sfdr_Article") in (8, 9):
                fund_master_record["Is_ESG"] = 1

            # BL-47: default defensivo Sfdr_Article=8 para fondos ESG sin artículo.
            # Causa raíz: el detector SFDR no cubre todos los formatos de texto KIID
            # (~43 fondos). Is_ESG=1 implica obligación SFDR mínima → Art.8 es el
            # mínimo razonable. Solo actúa si Is_ESG=1 Y Sfdr_Article sigue NULL.
            # Nunca sobreescribe un artículo ya detectado (6, 8 o 9).
            # Nota: fuente externa ESMA pendiente (P2) será más fiable que este default.
            if (fund_master_record.get("Is_ESG") == 1
                    and not fund_master_record.get("Sfdr_Article")):
                fund_master_record["Sfdr_Article"] = 8
                log_ingestion(
                    conn, isin, "BL47_SFDR_DEFAULT", "INFO",
                    "Sfdr_Article=8 por default (Is_ESG=1, artículo no detectado en KIID)"
                )

            # ── Normalización final (Principio #8) ──────────────────────
            # Se ejecuta AQUÍ porque Accumulation_Policy y Currency_Hedged
            # provienen de múltiples fuentes (bloque, characterizer, parser)
            # con convenciones de casing diferentes.

            # Accumulation_Policy: v20 canónico es TITLE (Accumulation/
            # Distribution). El override legacy v19 a UPPER se ELIMINA: era
            # contraproducente (forzaba Title→UPPER, contra config.DOMAIN_VALUES).
            # normalize_casing en sqlite_writer canonicaliza el casing.

            # Currency_Hedged: mapear "Yes" → "Hedged"
            if fund_master_record.get("Currency_Hedged") == "Yes":
                fund_master_record["Currency_Hedged"] = "Hedged"

            # BL-48: Family=LVNAV/VNAV/CNAV → normalizar a "Monetario".
            # Origen: bloque MONETARIOS asignaba tipología regulatoria en Family (bug).
            # Tras BL-43a, Subtype es el lugar correcto para esta info.
            # Se ejecuta aquí como net defensivo para fondos CACHED con valor antiguo
            # en BD — el bloque ya fue corregido en monetarios.py v3.
            _fam48 = fund_master_record.get("Family")
            if (fund_master_record.get("Fund_Nature") == "Monetario"
                    and _fam48 in ("LVNAV", "VNAV", "CNAV")):
                if not fund_master_record.get("Subtype"):
                    fund_master_record["Subtype"] = _fam48
                fund_master_record["Family"] = "Money Market"

            # Profile-SRRI coherencia: corregir Conservador con SRRI≥5
            _profile = fund_master_record.get("Profile")
            _srri_val = fund_master_record.get("SRRI")
            if _profile == "Conservador" and _srri_val is not None and _srri_val >= 5:
                fund_master_record["Profile"] = "Dinámico"
                print(
                    f"  [NORM-Profile-SRRI] {isin} Profile recalculado: "
                    f"Conservador->Dinamico (SRRI={_srri_val})"
                )

            # BL-44 v3: net defensivo — Nature incompatible con SRRI (cobertura universal).
            # CAMBIO RESPECTO A v30: revertir BL-65. Cuando BL-44 dispara, Fund_Nature
            # se asigna SIEMPRE a 'Restantes' (decisión usuario 29-abr-2026, opción A).
            # La inferencia léxica del nombre se delega a propagate_nature_to_restantes_type_family,
            # que actúa SOLO sobre Family/Type, no sobre Fund_Nature.
            #
            # Razón: 'Restantes' es un valor canónico de Fund_Nature (ver schema DDL y backlog
            # v3.4 que documenta 33 fondos en esta clase). Marca que BL-44 detectó la
            # incoherencia. P3 puede filtrar por Fund_Nature='Restantes' para excluir o auditar
            # estos fondos. Si Fund_Nature=None, el schema NOT NULL lo rechaza y se pierde
            # la trazabilidad del fondo en BD.
            #
            # Lectura BD R-4 (mantenida de v29).
            # Umbrales: Monetario SRRI≥3, RFC SRRI≥5 (alineado con _NATURE_VOL_BANDS={2,3,4}).
            _nat44_bd_row = conn.execute(
                "SELECT Fund_Nature, SRRI FROM fund_master WHERE ISIN=?", (isin,)
            ).fetchone()
            _nat44_bd   = _nat44_bd_row[0] if _nat44_bd_row else None
            _srri44_bd  = _nat44_bd_row[1] if _nat44_bd_row else None
            _nat44  = fund_master_record.get("Fund_Nature") or _nat44_bd
            _srri44 = fund_master_record.get("SRRI")
            if _srri44 is None:
                _srri44 = _srri44_bd

            _bl44_triggered = False
            # BL-64b: conversion segura — NaN/dict/None lanzarían excepción
            _srri44_int = None
            if _srri44 is not None:
                try:
                    _srri44_int = int(float(str(_srri44)))
                except (ValueError, TypeError):
                    _srri44_int = None
            if _nat44 is not None and _srri44_int is not None:
                # FIX-BL44-OPTB2 (2026-07-17): bypass BL-44 when either:
                # (a) monetarios block set _bl44_srri_anomaly — confirmed MMF with
                #     anomalous SRRI via STRONG_MMF_STRUCTURE_MARKERS in fund name.
                # (b) block classified fund as Money Market Fund via KIID signals
                #     (Vehicle_Structure=Money Market Fund / MMF_Structure populated) —
                #     strong KIID evidence (VNAV, "money market fund" language) overrides
                #     the SRRI_KIID mismatch. Confirmed: DWS ESG EU M MKT IC100
                #     (LU2098886703) has "vnav"/"money market fund" in KIID but returns
                #     via early strong-signal path, never setting _bl44_srri_anomaly.
                _bl44_already_handled = (
                    classification.get("_bl44_srri_anomaly") is not None
                    or classification.get("Vehicle_Structure") == "Money Market Fund"
                    or classification.get("MMF_Structure") not in (None, "Not Applicable")
                )
                # FIX-BL44-RFC-SRRI4-1 (2026-07-31): align RFCP threshold with
                # _NATURE_VOL_BANDS["Renta Fija Corto Plazo"] = {2,3,4} which
                # explicitly includes band 4 as valid for EM credit / covered bonds.
                # Old threshold >= 4 reclassified genuinely short-duration EM bond
                # funds (e.g. BSF EM DURAT BOND SRRI=4) to Restantes. Changed to
                # >= 5, which catches real SRRI mismatches while allowing SRRI=4.
                _reclasify44 = (
                    (_nat44 == "Monetario" and _srri44_int >= 3 and not _bl44_already_handled)
                    or (_nat44 == "Renta Fija Corto Plazo" and _srri44_int >= 5)
                )
                # BL-44-FX (2026-07-05, extiende la decisión de usuario del
                # 29-abr-2026): antes de forzar 'Restantes' incondicionalmente,
                # comprobar si el conflicto Nature/SRRI tiene una explicación
                # cambiaria legítima -- el nombre declara una divisa distinta
                # de la clase de participación (Fund_Currency), lo que añade
                # una capa de riesgo cambiario al SRRI sin alterar la Nature
                # real del fondo. Confirmado: SISF US Dollar Liquidity (SRRI=3,
                # Fund_Currency=EUR, genuino MMF) y BGF US Dollar Short
                # Duration Bond (SRRI=4, Fund_Currency=EUR, genuino RF Corto).
                # Deliberadamente NO exime JPM Global Macro Opportunities
                # (mismo SRRI=3, pero sin descalce divisa-nombre -- su voto
                # Monetario es un falso positivo genuino de benchmark de tasa
                # de referencia, no una cuestión cambiaria) -- verificado
                # corpus-wide contra los 83 ISIN marcados históricamente por
                # BL-44 antes de activar esta excepción.
                _bl44_fx_exempt = _reclasify44 and detect_fx_share_class_mismatch(
                    fund_master_record.get("Asset_Currency"),
                    fund_master_record.get("Fund_Currency"),
                    fund_master_record.get("Hedging_Policy"),
                    _srri44_int,
                )
                if _reclasify44 and _bl44_fx_exempt:
                    log_ingestion(
                        conn, isin, "BL44_FX_RISK_EXEMPTION", "INFO",
                        f"Fund_Nature={_nat44} SRRI={_srri44} incompatibilidad detectada "
                        f"pero exenta -- nombre declara divisa distinta de "
                        f"Fund_Currency={fund_master_record.get('Fund_Currency')!r} "
                        f"(riesgo cambiario explica el SRRI elevado); Nature preservada."
                    )
                elif _reclasify44:
                    # SIEMPRE asignar 'Restantes', nunca None ni la inferida.
                    fund_master_record["Fund_Nature"] = "Restantes"
                    fund_master_record["_bl44_force_overwrite"] = True
                    _bl44_triggered = True

                    print(
                        f"  [BL-44] {isin} Nature_efectivo={_nat44} "
                        f"incompatible con SRRI_efectivo={_srri44} → Restantes"
                    )
                    log_ingestion(
                        conn, isin, "BL44_NATURE_SRRI_R4", "WARN",
                        f"Fund_Nature={_nat44} reclasificado a Restantes (SRRI={_srri44}); "
                        f"mem={fund_master_record.get('Fund_Nature') or 'NULL'}, "
                        f"bd={_nat44_bd or 'NULL'}"
                    )

            # BL-62: re-inferir Family/Type para fondo ahora marcado Restantes.
            # La función actúa SOLO sobre Family/Type. Fund_Nature ya está en 'Restantes'
            # y no debe alterarse.
            if _bl44_triggered:
                _dq_before_bl62 = fund_master_record.get("Data_Quality_Flag")
                fund_master_record = propagate_nature_to_restantes_type_family(
                    fund_master_record,
                    isin,
                    log_fn=print,
                )
                # FIX-DQ-1: la función interna (classify_utils.py) puede fijar
                # Data_Quality_Flag='WARN' en su Fase 3 residual (sin patrón
                # léxico identificable) -- antes solo visible por `print`
                # (log_fn), nunca en ingestion_log. Se detecta el cambio aquí
                # y se acumula como issue explícito para que quede auditado
                # igual que el resto.
                if (fund_master_record.get("Data_Quality_Flag") != _dq_before_bl62
                        and fund_master_record.get("Data_Quality_Flag") is not None):
                    _dq_issues.append((
                        "BL62_RESIDUAL_NO_PATTERN", fund_master_record["Data_Quality_Flag"], "INFO",
                        "Family/Type=NULL tras BL-44→Restantes: sin patrón léxico "
                        "identificable en el nombre del fondo"
                    ))

            # FIX-BGF-CHINA-BOND-A (BL-64E-INLINE): cuando BL-44 se disparó
            # porque la naturaleza pre-BL-44 era RFC, BL-62 puede asignar
            # una Family incompatible con RFC (p.ej. "Emerging Market Debt"
            # para BGF China Bond). BL-64e estándar (~línea 2334) no puede
            # corregirlo porque Fund_Nature ya es "Restantes".
            # Aplicar la misma corrección inline usando _nat44 como guarda.
            if _bl44_triggered and _nat44 == "Renta Fija Corto Plazo":
                _fam_after_bl62 = fund_master_record.get("Family")
                if _fam_after_bl62 in RFC_INCOMPATIBLE_FAMILIES:
                    fund_master_record["Family"] = "Short-Term Fixed Income"
                    fund_master_record["_bl62_force_overwrite_family"] = True
                    _dq_issues.append((
                        "BL64E_INLINE_POST_BL44", "INFO", "INFO",
                        f"Family '{_fam_after_bl62}' → 'Short-Term Fixed Income': "
                        f"pre-BL-44 Nature era RFC; BL-62 infirió Family incompatible "
                        f"(_nat44='{_nat44}')"
                    ))

            # Theme: rellenar para bloques que no lo asignan
            if not fund_master_record.get("Theme"):
                fund_master_record["Theme"] = (
                    _detect_theme_pipeline((fund_name or "").lower())
                    or "Core/General"
                )

            # Strategy default: si no detectado y sin señales de indexación → Activo (P03)
            if not fund_master_record.get("Strategy"):
                fund_master_record["Strategy"] = "Activo"
                fund_master_record["Replication_Method"] = (
                    fund_master_record.get("Replication_Method") or "ACTIVE"
                )

            # Replication_Method: coherencia con Strategy (P03)
            if not fund_master_record.get("Replication_Method"):
                _strat = fund_master_record.get("Strategy")
                if _strat in ("Indexado", "Pasivo"):
                    fund_master_record["Replication_Method"] = "PASSIVE"
                elif _strat == "Activo":
                    fund_master_record["Replication_Method"] = "ACTIVE"

            # Investment_Universe: inferir desde Geography si NULL (P04)
            # BL-50: catálogos ampliados para cobertura bidireccional completa.
            # BL-50/2: fallback a BD vía eff.get() — antes leía solo el dict
            # del ciclo, perdiendo Geography preservada por COALESCE para
            # fondos CACHED. Resolvía 7 casos confirmados (5×EEUU + 2×Asia).
            if not fund_master_record.get("Investment_Universe"):
                _geo = eff.get("Geography", fund_master_record)
                _nat = fund_master_record.get("Fund_Nature")
                if _nat in ("Monetario", "Renta Fija Corto Plazo"):
                    # v20 §2A.1 #5: 'Liquidity' eliminated; 'Global' is correct.
                    fund_master_record["Investment_Universe"] = "Global"
                # FIX-GEO-2 (2026-07-05): estas listas comparaban _geo (que
                # Geography almacena EN-canónico desde hace tiempo, ver
                # DOMAIN_VALUES['Geography'] en shared/config.py) contra
                # literales ES ("EEUU", "Japón", "Europa", "América del
                # Norte"...) -- nunca coincidían salvo "China" (idéntico en
                # ambos idiomas), dejando esta rama efectivamente muerta.
                # Confirmado sin impacto en el corpus actual (0 fondos con
                # Geography poblado e Investment_Universe NULL a la vez),
                # pero se corrige la comparación al vocabulario real para no
                # dejar una trampa latente. Solo China/Japan/India son
                # "país" en el catálogo v20 (el resto de geografías son
                # regiones/continentes, incl. North America -- ver BL-52
                # más abajo, con el mismo bug, para el caso "país único
                # dentro de una región" como EEUU).
                elif _geo in ("China", "Japan", "India"):
                    fund_master_record["Investment_Universe"] = "Country"
                elif _geo in ("Europe", "North America", "Asia-Pacific",
                              "Latin America", "Eastern Europe",
                              "Middle East & Africa"):
                    fund_master_record["Investment_Universe"] = "Regional"
                elif _geo == "Global":
                    fund_master_record["Investment_Universe"] = "Global"

            # BL-52: corrección semántica Investment_Universe='Country' cuando
            # Geography contiene una región (no un país individual).
            # Causa raíz: el clasificador asigna Country pero luego la inferencia
            # de Geography devuelve un valor de región amplia (Latinoamérica,
            # Europa del Este, etc.) que es semánticamente incompatible con Country.
            # FIX-GEO-2 (2026-07-05): _REGION_VALUES comparaba contra literales
            # ES (Geography es EN-canónico desde hace tiempo) -- esta regla
            # llevaba tiempo sin dispararse nunca. Confirmado corpus-wide: 34
            # fondos con Investment_Universe='Country' + Geography='North
            # America' (p.ej. 'JPM US Growth', familia 'JPM Income') que esta
            # regla debía corregir a 'Regional'. Revivida con el vocabulario
            # EN correcto, tras confirmar con el usuario que 'North America'
            # (única traducción disponible de 'EEUU' en DOMAIN_VALUES, sin
            # valor 'United States' propio) debe tratarse como región aquí,
            # igual que la intención original (su lista ES ya incluía
            # "América del Norte").
            # FIX-GEO-4 (2026-07-05): dos bugs adicionales encontrados al
            # auditar el run de producción del usuario tras FIX-GEO-2 -- 8
            # fondos (p.ej. FIDELITY ITALY, FTGF PUT LG CAP VAL) seguían
            # mostrando la incoherencia en BD pese al fix anterior:
            #   1. Reimplementaba aquí, en local, el mismo catálogo de
            #      regiones que ya vive en classify_utils.py
            #      (_REGION_GEOGRAPHIES, usado por validate_geography_universe
            #      / INTER-10) -- violación DRY (Principio R-1: los mapas de
            #      normalización/caracterización viven solo en classify_utils,
            #      la responsabilidad de esta validación es suya, no de
            #      pipeline.py). Sustituido por una llamada directa a la
            #      función canónica.
            #   2. Leía Geography/Investment_Universe directamente de
            #      fund_master_record (valor recalculado ESTE ciclo, que
            #      puede ser None si el fondo está CACHED y ninguna señal
            #      nueva de nombre/KIID aporta geografía) en vez del valor
            #      EFECTIVO vía EffectiveReader (como ya hace BL-50, arriba).
            #      Cuando fund_master_record["Geography"] es None este ciclo,
            #      la condición nunca se cumplía y la regla no se disparaba
            #      -- el COALESCE de sqlite_writer conservaba entonces el
            #      Geography/Investment_Universe de BD, potencialmente
            #      incoherentes entre sí desde un ciclo anterior, sin que
            #      esta regla llegara nunca a re-certificarlos.
            _geo_eff  = eff.get("Geography", fund_master_record)
            _univ_eff = eff.get("Investment_Universe", fund_master_record)
            _geouniv_status, _geouniv_msg, _geouniv_corrected = validate_geography_universe(
                _geo_eff, _univ_eff
            )
            if _geouniv_status == "CORRECTED":
                fund_master_record["Investment_Universe"] = _geouniv_corrected
                _dq_issues.append((
                    "GEOGRAPHY_UNIVERSE_CORRECTED", "INFO", "INFO", _geouniv_msg
                ))
            elif _geouniv_status == "WARNING" and _geo_name_wins:
                # FIX-GEO-7: name drove Geography to a specific country AND
                # Universe is still 'Global' (stale from a prior cycle).
                # Safe to auto-correct because the name signal is the
                # authority for both Geography AND Universe in this case.
                fund_master_record["Investment_Universe"] = "Country"
                _dq_issues.append((
                    "GEOGRAPHY_UNIVERSE_CORRECTED", "INFO", "INFO",
                    f"Universe corregido a 'Country' ({_geo_eff} confirmado "
                    f"por señal de nombre; KIID decía 'Global')."
                ))
            elif _geouniv_status == "WARNING":
                _dq_issues.append((
                    "GEOGRAPHY_UNIVERSE_WARNING", "WARN", "WARN", _geouniv_msg
                ))

            # ── BL-50: Inferencia inversa Universe → Geography ─────────────
            # Para los casos unívocos (Global, Liquidity) donde Universe está
            # poblado pero Geography=NULL. Para Country/Regional con Geography=NULL
            # no se infiere (no hay valor canónico sin información adicional) —
            # esos casos requieren auditoría manual del clasificador de origen.
            if not fund_master_record.get("Geography"):
                _univ_inv = fund_master_record.get("Investment_Universe")
                if _univ_inv == "Global":
                    # Universe=Global → Geography=Global (unívoco al 100%)
                    fund_master_record["Geography"] = "Global"
                elif _univ_inv == "Liquidity":
                    # Universe=Liquidity → Geography inferida desde divisa del fondo
                    # (solo EUR/USD tienen valor canónico inequívoco)
                    _curr_liq = fund_master_record.get("Fund_Currency")
                    if _curr_liq == "EUR":
                        fund_master_record["Geography"] = "Europe"
                    elif _curr_liq == "USD":
                        fund_master_record["Geography"] = "North America"
                    # GBP, JPY, CHF — sin señal canónica fiable → dejar NULL

            # Investment_Universe + Geography: inferir desde Benchmark_Declared
            # cuando ambos siguen a NULL tras las reglas anteriores (v22).
            # Solo aplica patrones de alta precisión (≥95%) para evitar falsos
            # positivos. Los benchmarks mixtos (US+Europa) y los euribor en RV
            # se excluyen explícitamente.
            if (not fund_master_record.get("Investment_Universe")
                    or not fund_master_record.get("Geography")):
                _bench_for_univ = (
                    fund_master_record.get("Benchmark_Declared") or ""
                ).lower()
                _nat_for_univ = fund_master_record.get("Fund_Nature")
                _inferred_univ_b = None
                _inferred_geo_b = None

                if _bench_for_univ and _bench_for_univ != "no_benchmark":
                    # Global: índices mundiales inequívocos
                    if re.search(
                        r'\bmsci\s+(?:ac\s+)?world\b|\bmsci\s+acwi\b'
                        r'|\bmsci\s+all\s+country\b|\bbloomberg\s+global\b'
                        r'|\bftse\s+all.?world\b|\bmsci\s+world\s+net\b',
                        _bench_for_univ
                    ):
                        _inferred_univ_b = "Global"
                        _inferred_geo_b  = "Global"

                    # Europa regional: índices europeos inequívocos
                    elif re.search(
                        r'\bmsci\s+europe\b|\beuro\s+stoxx\b|\bstoxx\s+europe\b'
                        r'|\bbloomberg\s+euro.?aggregate\b'
                        r'|\bbloomberg\s+euro-aggregate\b',
                        _bench_for_univ
                    ):
                        _inferred_univ_b = "Regional"
                        _inferred_geo_b  = "Europa"

                    # Emergentes
                    elif re.search(
                        r'\bmsci\s+emerging\b|\bmsci\s+frontier\b'
                        r'|\bmsci\s+em\b',
                        _bench_for_univ
                    ):
                        _inferred_univ_b = "Regional"
                        _inferred_geo_b  = "Emergentes"

                    # Italia (único Country inferible sin ambigüedad)
                    elif re.search(r'\bftse\s+italia\b', _bench_for_univ):
                        _inferred_univ_b = "Country"
                        _inferred_geo_b  = "Italia"

                    # Liquidez: solo para naturalezas no-RV que no tengan
                    # señal geográfica clara en el benchmark.
                    # Nota: €STR usa lookbehind/lookahead porque € no es
                    # un carácter de palabra y \b no funciona con él.
                    elif _nat_for_univ not in ("Renta Variable",):
                        _liq_signal = (
                            re.search(r'\b(?:estr|euribor|sofr|sonia|libor)\b',
                                      _bench_for_univ)
                            or re.search(r'(?<!\w)€str(?!\w)', _bench_for_univ)
                        )
                        if _liq_signal:
                            _inferred_univ_b = "Liquidity"
                            _inferred_geo_b  = None

                if _inferred_univ_b:
                    if not fund_master_record.get("Investment_Universe"):
                        fund_master_record["Investment_Universe"] = _inferred_univ_b
                    if _inferred_geo_b and not fund_master_record.get("Geography"):
                        # FIX-GEO-1 (2026-07-05): _inferred_geo_b llega en
                        # vocabulario ES ("Europa", "Emergentes", "Italia")
                        # -- traducir antes de persistir, igual que el valor
                        # COALESCE principal más arriba (ver _geo_en_final).
                        fund_master_record["Geography"] = _derive_geography_en(
                            _inferred_geo_b, _geo_name_l
                        )

            # Accumulation_Policy: inferir desde nombre si NULL (P05)
            if not fund_master_record.get("Accumulation_Policy"):
                _fn_l = (fund_name or "").lower()
                # ACC/ACCUM al final del nombre o como token separado
                if re.search(r"\bacc(?:um)?\b", _fn_l):
                    fund_master_record["Accumulation_Policy"] = "ACCUMULATION"
                elif re.search(r"\b(?:inc|dis(?:t)?)\b", _fn_l):
                    fund_master_record["Accumulation_Policy"] = "DISTRIBUTION"

            # Sector_Focus: inferir desde Theme si Investment_Focus=Sector y SF=NULL (P10)
            # BL-54: mapa inline eliminado — se usa map_theme_to_sector_focus()
            # (classify_utils), punto único de verdad (Principio #2 DRY).
            if (fund_master_record.get("Investment_Focus") == "Sector"
                    and not fund_master_record.get("Sector_Focus")):
                _theme = fund_master_record.get("Theme")
                _sf = map_theme_to_sector_focus(_theme)
                if _sf:
                    fund_master_record["Sector_Focus"] = _sf
                elif _theme == "Megatrends":
                    # Megatrends es multisectorial → reclasificar a Thematic
                    fund_master_record["Investment_Focus"] = "Thematic"
                elif _theme == "Core/General":
                    # Sector sin tema específico → reclasificar a Broad
                    fund_master_record["Investment_Focus"] = "Broad"


            # Investment_Focus: default por Fund_Nature cuando no detectado (P11b)
            # BL-63: 466 fondos con IF=NULL tras DLA-1. Causa raiz: los bloques
            # RF_CORTO y MONETARIOS no asignan IF; BL-44 redirige estos fondos a
            # RESTANTES que tampoco lo asigna. El valor IF=Broad que existia antes
            # se preservaba por COALESCE desde ciclos anteriores. Con DLA-1 y la
            # expansion de BL-44, COALESCE ya no puede recuperarlo porque el fondo
            # entra por primera vez o su texto cambio. Fix: default deterministico
            # por Nature en el unico punto canonico de defaults del pipeline.
            if not fund_master_record.get("Investment_Focus"):
                _nat_if = fund_master_record.get("Fund_Nature")
                if _nat_if in ("Renta Fija Corto Plazo", "Monetario"):
                    # RF_CORTO y Monetarios son por definicion fondos de liquidez/corto
                    # plazo sin enfoque geografico ni sectorial especifico -> Broad
                    fund_master_record["Investment_Focus"] = "Broad"
                elif _nat_if == "Renta Variable":
                    # RV sin enfoque detectado: Broad (universo global de acciones)
                    fund_master_record["Investment_Focus"] = "Broad"
                elif _nat_if in ("Renta Fija Flexible", "Mixtos"):
                    # RF Flexible y Mixtos: Broad como default conservador
                    fund_master_record["Investment_Focus"] = "Broad"
                # Alternativo, Estructurado, Restantes: no asignar default (semantica ambigua)

            # Derivatives_Usage: default NO si no detectado (P12)
            if not fund_master_record.get("Derivatives_Usage"):
                fund_master_record["Derivatives_Usage"] = "NO"

            # Leverage_Used: default NO si no detectado (P13)
            if not fund_master_record.get("Leverage_Used"):
                fund_master_record["Leverage_Used"] = "NO"

            # Credit_Quality: default para Nature sin detección (P14)
            # BL-34: "Not Applicable" en inglés (coherente con BL-24/Principio #8)
            # BL-34b: normalizar "No aplica" existente en BD → "Not Applicable"
            # BL-42 v23: añadido default para Mixtos (219 NULL)
            _cq = fund_master_record.get("Credit_Quality")
            if _cq == "No aplica":
                fund_master_record["Credit_Quality"] = "Not Applicable"
            elif not _cq:
                _nat14 = fund_master_record.get("Fund_Nature")
                if _nat14 == "Renta Variable":
                    fund_master_record["Credit_Quality"] = "Not Applicable"
                elif _nat14 in ("Renta Fija Flexible", "Renta Fija Corto Plazo"):
                    fund_master_record["Credit_Quality"] = "Mixed"
                elif _nat14 == "Monetario":
                    fund_master_record["Credit_Quality"] = "Investment Grade"
                elif _nat14 == "Mixtos":
                    # BL-42: default diferenciado por Family.
                    # Income Oriented (RV dominante con búsqueda de renta): Not Applicable
                    # Mixtos genérico sin señal de crédito: Mixed (blend de calidades)
                    # BL-65b: comparacion actualizada a EN canónico.
                    _fam14 = fund_master_record.get("Family")
                    if _fam14 == "Income Oriented":
                        fund_master_record["Credit_Quality"] = "Not Applicable"
                    else:
                        fund_master_record["Credit_Quality"] = "Mixed"

            # BL-B6-HY-KIID (2026-07-13): KIID-mandate High Yield override for
            # name-silent HY funds. derive_credit_quality() is name-only and
            # defaults RF_Corto/RF_Flexible to "Investment Grade" when no HY
            # token appears in the fund name. Some funds invest in sub-IG bonds
            # without using "High Yield" / "HY" / "h.y." in their name (e.g.
            # UBS Floating Rate Income — "calificaciones de menor calidad").
            # Only fires when: (a) current CQ is IG, (b) KIID text is available,
            # (c) an unambiguous sub-IG / HY phrase appears in the full text,
            # (d) fund name does not identify it as an IG-primary mandate
            # (government/sovereign/treasury bonds — FIX-B6-2, 2026-07-15).
            # Deliberately conservative — does NOT include "alto rendimiento"
            # (too generic; often describes performance target, not credit tier)
            # or bare "high yield" (appears in risk warnings of IG funds).
            _cq_cur = fund_master_record.get("Credit_Quality")
            # FIX-B6-3a (2026-07-15): Monetario funds are money-market instruments
            # by regulatory definition — they cannot hold HY bonds. Any "inferior a
            # investment grade" language in their KIID describes a permitted exception
            # floor, not the primary mandate. Exclude Monetario entirely.
            if (_cq_cur == "Investment Grade" and kiid_text
                    and fund_master_record.get("Fund_Nature") != "Monetario"):
                _kt_lower = kiid_text.lower()
                # FIX-B6-2 (2026-07-15): government-bond funds can use sub-IG CDS
                # for hedging; their KIID therefore contains "inferior a grado de
                # inversión" in the derivatives section, which is not a mandate
                # description. Guard: if the fund name identifies it as a government
                # or sovereign bond fund, skip BL-B6-HY-KIID entirely.
                # Confirmed false positive: SISF EURO GOVERNMENT BOND (LU0106236002).
                # FIX-B6-3b (2026-07-15): "aggregate" bond index funds (Bloomberg US
                # Aggregate, Euro Aggregate) are IG broad-market trackers. They can
                # hold ~3-5% HY (index composition) and their KIID may describe that
                # HY floor — this is not a HY mandate. Guard: "aggregate" in name.
                # Confirmed false positive: JPM US AGGREGATE (LU0679000579).
                _fund_name_l_b6 = (fund_master_record.get("Fund_Name") or "").lower()
                _IG_NAME_GUARDS = [
                    "government bond", "sovereign bond", "treasury bond",
                    "euro government", "staatsanleihen", "gilt",
                    "aggregate",   # FIX-B6-3b: broad IG index trackers
                ]
                _is_ig_name = any(g in _fund_name_l_b6 for g in _IG_NAME_GUARDS)
                if not _is_ig_name:
                    _hy_kiid_signals = [
                        "calificaciones de menor calidad",   # UBS Floating Rate Income
                        "inferior a grado de inversión",     # explicit sub-IG declaration
                        "inferiores a grado de inversión",   # plural variant
                        "sub-investment grade",              # EN sub-IG (PRIIPs KIDs)
                        "calificación inferior a grado de inversión",
                        # FIX-B6-2 (2026-07-15): EN/mixed PRIIPs KIDs mix Spanish
                        # "inferior a" with English "investment grade" term.
                        "inferior a investment grade",
                    ]
                    # FIX-B6-4 (2026-07-16): IG-primary / allowance context guard (R-6).
                    # Some IG-primary funds (e.g. SISF Euro Short Term, JPM US Sh Duration)
                    # describe a *limited* sub-IG allowance in their KIID using the same
                    # phrases that genuinely identify HY mandates. The name-only guards
                    # (FIX-B6-2/3) don't catch them. Add a generic context check (no fund
                    # names — P#5): when a sub-IG signal matches, inspect a bounded window
                    # (~±180 chars) around the match; if it contains an IG-majority/allowance
                    # qualifier the fund is IG-primary and the override is suppressed.
                    # Conservative: genuine HY funds state sub-IG as the primary mandate
                    # without majority-qualifier qualifications.
                    _IG_PRIMARY_QUALIFIERS = re.compile(
                        r"al\s+menos\s+(?:dos\s+tercios|el\s+\d+\s*%|\d+\s*%)?"
                        r"|como\s+m[ií]nimo\s+(?:el\s+)?\d+\s*%"
                        r"|(?:el\s+)?\d{2,3}\s*%\s+de\s+(?:los\s+)?t[íi]tulos?\s+con\s+calificaci[oó]n\s+investment\s+grade"
                        r"|principalmente\s+en\s+t[íi]tulos?\s+de\s+deuda\s+con\s+calificaci[oó]n\s+investment\s+grade"
                        r"|de\s+manera\s+limitada"
                        r"|de\s+forma\s+limitada"
                        r"|hasta\s+un\s+\d+\s*%[^.]{0,30}(?:inferior|sub.investment|menor\s+calidad)"
                        r"|podr[aá]\s+invertir(?:[^.]{0,60}de\s+manera\s+limitada)"
                        r"|mayoritariamente\s+en\s+.{0,60}grado\s+de\s+inversi[oó]n",
                        re.I,
                    )

                    def _is_ig_allowance_context(kt: str, signal: str) -> bool:
                        """Return True if 'signal' in 'kt' appears inside an IG-majority/allowance clause."""
                        pos = kt.find(signal)
                        if pos == -1:
                            return False
                        # Look back 400 chars (covers long IG-mandate sentences where
                        # the qualifier precedes the sub-IG allowance clause by ~300+ chars,
                        # as in "al menos dos tercios … grado de inversión … inferior a").
                        window = kt[max(0, pos - 400): pos + len(signal) + 200]
                        return bool(_IG_PRIMARY_QUALIFIERS.search(window))

                    _matched_signal = next(
                        (k for k in _hy_kiid_signals if k in _kt_lower), None
                    )
                    if _matched_signal and not _is_ig_allowance_context(_kt_lower, _matched_signal):
                        fund_master_record["Credit_Quality"] = "High Yield"
                        # FIX-B6-HY-LOGGER (2026-07-14): pipeline.py has no `logger`
                        # object (it logs via print + _dq_issues tuples). The previous
                        # `logger.info(...)` call here was a NameError that crashed the
                        # per-ISIN loop on every fund reaching this branch, silently
                        # aborting their persist. Use _dq_issues so the override is
                        # visible in fund_data_quality_issues.
                        _dq_issues.append((
                            "BL_B6_HY_KIID", "INFERRED", "INFO",
                            f"[{isin}] BL-B6-HY-KIID: Credit_Quality IG→HY "
                            "(sub-IG mandate in KIID text)",
                        ))

            # ── Defaults semánticos P14-ext (v24) ──────────────────────────
            # Principio: NULL puede significar "no detectado" o "no aplica
            # estructuralmente". Cuando la distinción es semánticamente
            # relevante para P3, se asigna un valor explícito.

            # BL-43a-ext: Subtype Monetario sin tipología MMF → "Standard MMF"
            # Fondos UCITS monetarios no sujetos al Reglamento MMF 2017/1131:
            # no tienen VNAV/LVNAV/CNAV porque preexisten o están fuera del
            # perímetro regulatorio específico. "Standard MMF" los distingue
            # de fondos con tipología regulatoria no detectada.
            if (fund_master_record.get("Fund_Nature") == "Monetario"
                    and not fund_master_record.get("Subtype")):
                fund_master_record["Subtype"] = "Standard MMF"

            # BL-41-ext: Style_Profile en Renta Variable sin detección
            # - Indexado/Pasivo: el estilo de gestión no aplica → "Not Applicable"
            # - Activo sin estilo declarado: gestión agnóstica de estilo → "Blend"
            #   ("Blend" es la convención estándar del sector para fondos activos
            #    sin sesgo Growth/Value/Income declarado)
            # Solo aplica si RV y sin valor previo (bloque + parser + BD vía COALESCE)
            if (fund_master_record.get("Fund_Nature") == "Renta Variable"
                    and not fund_master_record.get("Style_Profile")):
                _strat_sp = fund_master_record.get("Strategy")
                if _strat_sp in ("Indexado", "Pasivo"):
                    fund_master_record["Style_Profile"] = "Not Applicable"
                elif _strat_sp == "Activo":
                    fund_master_record["Style_Profile"] = "Blend"
                # Strategy=NULL → no hay información suficiente, dejar NULL

            # INTER-SP: Style_Profile (Value/Growth/Blend) solo aplica a Renta Variable.
            # Monetario, RFCP y RFF son fondos de renta fija pura donde el concepto de
            # estilo de gestión de acciones no tiene significado semántico.
            # Mixtos y Alternativo se preservan: pueden tener exposición equity significativa
            # con sesgo de estilo declarado en el KID.
            # _style_profile_cleared=True → sqlite_writer usa OW en lugar de COALESCE,
            # limpiando valores stale en BD incluso en ciclos CACHED (R-4 defensivo).
            _sp_inter_nature = fund_master_record.get("Fund_Nature")
            if _sp_inter_nature in ("Monetario", "Renta Fija Corto Plazo", "Renta Fija Flexible"):
                _sp_val = fund_master_record.get("Style_Profile")
                if _sp_val:
                    log_ingestion(
                        conn, isin, "INTER_SP_NULL", "INFO",
                        f"Style_Profile='{_sp_val}' → NULL "
                        f"(no aplica para Fund_Nature='{_sp_inter_nature}')"
                    )
                fund_master_record["Style_Profile"] = None
                fund_master_record["_style_profile_cleared"] = True

            # BL-27-ext: Market_Cap_Focus en RV sin restricción de cap → "All Cap"
            # Si RV sin MCF y sin Sector_Focus (fondos sectoriales no tienen eje
            # de cap), el fondo invierte sin restricción de capitalización.
            # "All Cap" es la convención estándar para fondos sin sesgo de cap.
            # Se ejecuta DESPUÉS del bloque BL-27 que intenta inferir desde
            # benchmark (líneas siguientes) — aquí es el fallback final.
            # NOTA: el bloque BL-27 (benchmark→cap) sigue en la sección INTER
            # por razones arquitectónicas; este default actúa como capa posterior.
            # Se marca con flag para no colisionar con el BL-27 INTER.
            _apply_allcap_default = (
                fund_master_record.get("Fund_Nature") == "Renta Variable"
                and not fund_master_record.get("Market_Cap_Focus")
                and not fund_master_record.get("Sector_Focus")
            )
            # Se ejecutará tras el bloque BL-27 INTER (ver más abajo)

            # ── Correcciones INTER (BL-30, BL-31) ─────────────────────────
            # Ejecutar AQUÍ porque es el único punto donde todos los atributos
            # están consolidados (bloque + characterizer + parser + BD previa).
            # validate_all_semantic_consistency() en classify_utils solo se invoca
            # desde restantes.py — los fondos CACHED de otros bloques nunca pasan
            # por ella. Este bloque garantiza cobertura universal.
            #
            # CAUSA RAÍZ previa: si fund_master_record tenía un campo a None pero
            # BD tenía un valor antiguo, el COALESCE en sqlite_writer preservaba
            # el valor antiguo — creando inconsistencia con los campos nuevos
            # escritos con valor no-NULL. Fix: leer valores BD previos y usarlos
            # en la comparación INTER.
            _bd_prev = conn.execute(
                "SELECT Sector_Focus, Hedging_Policy, "
                "Investment_Focus, Benchmark_Declared, Benchmark_Type "
                "FROM fund_master WHERE ISIN=?",
                (isin,)
            ).fetchone()
            _sf_bd        = _bd_prev[0] if _bd_prev else None
            # v20: Currency_Hedged eliminado del schema (consolidado en Hedging_Policy).
            # No hay valor BD previo; el subsistema CH opera solo en memoria y se
            # propaga a Hedging_Policy (única columna persistida).
            _ch_bd        = None
            _hp_bd        = _bd_prev[1] if _bd_prev else None
            _if_bd        = _bd_prev[2] if _bd_prev else None
            _bench_bd     = _bd_prev[3] if _bd_prev else None
            _benchtype_bd = _bd_prev[4] if _bd_prev else None

            # BL-64e: INTER Nature↔Family — RFC no puede tener Family de RF Flexible.
            # RFC_INCOMPATIBLE_FAMILIES importado de classify_utils (P#11 DRY).
            # 3 fondos afectados: BGF China Bond (LU2267/LU0719/LU0764).
            if (fund_master_record.get("Fund_Nature") == "Renta Fija Corto Plazo"
                    and fund_master_record.get("Family") in RFC_INCOMPATIBLE_FAMILIES):
                _bl64e_old_family = fund_master_record.get("Family")
                fund_master_record["Family"] = "Short-Term Fixed Income"
                fund_master_record["Type"]   = fund_master_record.get("Type") or "Short-Term Fixed Income"
                log_ingestion(
                    conn, isin, "BL64E_FAMILY_RFC_CORRECTION", "INFO",
                    f"Family '{_bl64e_old_family}'→'Short-Term Fixed Income'"
                )

            # BL-64c: Sector_Focus ES->EN (Principio #8). 266 fondos afectados.
            _SF_ES_TO_EN = {
                "Tecnología e Innovación":      "Technology & Innovation",
                "Salud y Ciencias de la Vida":  "Healthcare & Life Sciences",
                "Energía y Recursos":           "Energy & Resources",
                "Materiales y Minería":         "Materials & Mining",
                "Utilities y Medio Ambiente":   "Utilities & Environment",
                "Servicios Financieros":        "Financial Services",
                "Consumo y Retail":             "Consumer & Retail",
                "Infraestructura":              "Infrastructure",
                "Inmobiliario":                 "Real Estate",
                "Activos Reales":               "Real Assets",
            }
            _sf_curr = fund_master_record.get("Sector_Focus")
            if _sf_curr and _sf_curr in _SF_ES_TO_EN:
                fund_master_record["Sector_Focus"] = _SF_ES_TO_EN[_sf_curr]

            # BL-64d: Family ES->EN. 'Orientado a Renta' -> 'Income Oriented' (104 fondos).
            # Aplica al residual en BD; los nuevos fondos ya son corregidos en mixtos.py.
            _fam_curr = fund_master_record.get("Family")
            if _fam_curr == "Orientado a Renta":
                fund_master_record["Family"] = "Income Oriented"

            # BL-46: Benchmark_Type NULL cuando Benchmark_Declared está en BD pero
            # el ciclo actual procesó el fondo CACHED (parsed.Benchmark_Declared=None).
            # Causa raíz: _detect_benchmark_type() solo recibe parsed.Benchmark_Declared;
            # para fondos CACHED ese valor es None → devuelve None → COALESCE mantiene
            # Benchmark_Declared en BD pero Benchmark_Type llega NULL y lo sobreescribe.
            # Fix: recalcular usando el valor efectivo de Benchmark_Declared.
            if not fund_master_record.get("Benchmark_Type"):
                _bench_eff = (
                    fund_master_record.get("Benchmark_Declared") or _bench_bd
                )
                if _bench_eff:
                    _bt_recalc = _detect_benchmark_type(
                        _bench_eff,
                        fund_master_record.get("Replication_Method")
                        or parsed.get("Replication_Method"),
                    )
                    if _bt_recalc:
                        fund_master_record["Benchmark_Type"] = _bt_recalc
                        # Si Benchmark_Declared venía solo de BD, propagarlo al dict
                        # para que COALESCE no lo pierda en la escritura.
                        if not fund_master_record.get("Benchmark_Declared"):
                            fund_master_record["Benchmark_Declared"] = _bench_eff

            # BL-61: INTER-1 — Strategy ↔ Replication_Method (cobertura universal)
            # Causa raíz: P03 (líneas ~688-694) solo cubría Replication_Method=NULL.
            # Si un bloque clasificador emitía Strategy='Indexado' con
            # Replication_Method='ACTIVE' ya poblado, la inconsistencia sobrevivía.
            # Este bloque INTER actúa DESPUÉS de que todos los atributos están
            # consolidados (bloque + characterizer + parser + defaults P03), por lo
            # que corrige cualquier combinación inconsistente sin importar su origen.
            # Usa valores efectivos (actual o BD previa) conforme a R-4.
            _strat_inter1 = fund_master_record.get("Strategy")
            _rep_inter1 = fund_master_record.get("Replication_Method")
            _corrected_rep, _err_inter1 = validate_strategy_replication(
                _strat_inter1, _rep_inter1
            )
            if _err_inter1:
                fund_master_record["Replication_Method"] = _corrected_rep
                log_ingestion(
                    conn, isin, "BL61_STRATEGY_REPLICATION", "INFO",
                    f"Strategy='{_strat_inter1}' Replication '{_rep_inter1}'→'{_corrected_rep}'"
                )

            # BL-30: Investment_Focus=Broad con Sector_Focus poblado → corregir a Sector
            # Considerar tanto el valor actual como el preservado por COALESCE
            _sf_p = fund_master_record.get("Sector_Focus") or _sf_bd
            _if_p = fund_master_record.get("Investment_Focus") or _if_bd
            if _sf_p and _if_p == "Broad":
                fund_master_record["Investment_Focus"] = "Sector"
                # Asegurar que Sector_Focus queda poblado (si solo venía de BD)
                if not fund_master_record.get("Sector_Focus"):
                    fund_master_record["Sector_Focus"] = _sf_bd
                log_ingestion(
                    conn, isin, "BL30_INVESTMENT_FOCUS_SECTOR", "INFO",
                    f"Investment_Focus 'Broad'→'Sector' (Sector_Focus='{_sf_p}')"
                )

            # BL-31: Currency_Hedged contradice Hedging_Policy → Hedging_Policy prevalece
            # Usar valores efectivos (actual o BD previa) para detectar el conflicto
            _ch_p = fund_master_record.get("Currency_Hedged") or _ch_bd
            _hp_p = fund_master_record.get("Hedging_Policy") or _hp_bd
            if _ch_p and _hp_p:
                _hp_as_ch = "Hedged" if _hp_p == "HEDGED" else "Unhedged"
                if _ch_p != _hp_as_ch:
                    fund_master_record["Currency_Hedged"] = _hp_as_ch
                    log_ingestion(
                        conn, isin, "BL31_CH_HP_RECONCILE", "INFO",
                        f"Currency_Hedged '{_ch_p}'→'{_hp_as_ch}' (Hedging_Policy='{_hp_p}')"
                    )

            # BL-45 v24: Hedging_Policy inferida desde Currency_Hedged cuando HP=NULL
            # Si Currency_Hedged está poblado pero Hedging_Policy es NULL, son
            # semánticamente equivalentes → propagar el valor (199 fondos).
            # Se ejecuta tras BL-31 para usar los valores ya validados (_ch_p/_hp_p).
            # Solo actúa si Hedging_Policy sigue NULL tras BL-31.
            if not (fund_master_record.get("Hedging_Policy") or _hp_bd):
                _ch_eff = fund_master_record.get("Currency_Hedged") or _ch_bd
                if _ch_eff == "Hedged":
                    fund_master_record["Hedging_Policy"] = "HEDGED"
                    log_ingestion(
                        conn, isin, "BL45_HP_FROM_CH_PROPAGATE", "INFO",
                        f"Hedging_Policy NULL→'HEDGED' (Currency_Hedged='{_ch_eff}')"
                    )
                elif _ch_eff == "Unhedged":
                    fund_master_record["Hedging_Policy"] = "UNHEDGED"
                    log_ingestion(
                        conn, isin, "BL45_HP_FROM_CH_PROPAGATE", "INFO",
                        f"Hedging_Policy NULL→'UNHEDGED' (Currency_Hedged='{_ch_eff}')"
                    )

            # BL-49/3: propagación inversa HP → CH cuando CH=NULL pero HP poblado.
            # Causa raíz previa: BL-31 solo dispara con AMBOS poblados; BL-45
            # solo cubre CH→HP. Faltaba la simetría HP→CH. Resuelve los 29
            # fondos del export con HP poblado y CH=NULL.
            if not (fund_master_record.get("Currency_Hedged") or _ch_bd):
                _hp_eff_b49 = fund_master_record.get("Hedging_Policy") or _hp_bd
                if _hp_eff_b49 == "HEDGED":
                    fund_master_record["Currency_Hedged"] = "Hedged"
                    log_ingestion(
                        conn, isin, "BL49_CH_FROM_HP_PROPAGATE", "INFO",
                        f"Currency_Hedged NULL→'Hedged' (Hedging_Policy='{_hp_eff_b49}')"
                    )
                elif _hp_eff_b49 == "UNHEDGED":
                    fund_master_record["Currency_Hedged"] = "Unhedged"
                    log_ingestion(
                        conn, isin, "BL49_CH_FROM_HP_PROPAGATE", "INFO",
                        f"Currency_Hedged NULL→'Unhedged' (Hedging_Policy='{_hp_eff_b49}')"
                    )

            # BL-49/4: detección Currency_Hedged desde texto KIID (segunda fase).
            # Solo actúa si Currency_Hedged sigue NULL tras todas las fases anteriores
            # (nombre, BL-31, BL-45, BL-49/3). Restringe a Fund_Currency ≠ EUR.
            _ch_eff_bl49 = fund_master_record.get("Currency_Hedged") or _ch_bd
            if not _ch_eff_bl49:
                _fc_bl49 = fund_master_record.get("Fund_Currency")
                if _fc_bl49 and _fc_bl49 != "EUR" and kiid_text:
                    _ch_from_kiid, _ch_pat_id = detect_currency_hedged_from_kiid(kiid_text)
                    if _ch_from_kiid:
                        fund_master_record["Currency_Hedged"] = _ch_from_kiid
                        log_ingestion(
                            conn, isin, "BL49_CH_FROM_KIID", "INFO",
                            f"Currency_Hedged='{_ch_from_kiid}' via patrón CH-KIID-{_ch_pat_id}"
                        )

            # v20: Currency_Hedged no se persiste (columna eliminada). Propagación
            # final CH→Hedging_Policy para no perder la detección por KIID (BL-49/4),
            # que en el orden original ocurría DESPUÉS de BL-45 (CH→HP) y por tanto
            # no alcanzaba Hedging_Policy. Solo actúa si HP sigue NULL.
            if not (fund_master_record.get("Hedging_Policy") or _hp_bd):
                _ch_final = fund_master_record.get("Currency_Hedged")
                if _ch_final == "Hedged":
                    fund_master_record["Hedging_Policy"] = "HEDGED"
                elif _ch_final == "Unhedged":
                    fund_master_record["Hedging_Policy"] = "UNHEDGED"

            # BL-27: Market_Cap_Focus desde benchmark si NULL y RV (cubre fondos CACHED)
            if (fund_master_record.get("Fund_Nature") == "Renta Variable"
                    and not fund_master_record.get("Market_Cap_Focus")):
                _bench_l = (fund_master_record.get("Benchmark_Declared") or "").lower()
                if any(k in _bench_l for k in ["small cap", "small-cap", "smallcap"]):
                    fund_master_record["Market_Cap_Focus"] = "Small Cap"
                elif any(k in _bench_l for k in ["mid cap", "mid-cap", "midcap", "smid"]):
                    fund_master_record["Market_Cap_Focus"] = "Mid Cap"
                elif any(k in _bench_l for k in [
                    "msci world", "msci acwi", "s&p 500", "stoxx europe 600",
                    "euro stoxx 50", "ftse 100", "dax", "nasdaq 100",
                ]):
                    fund_master_record["Market_Cap_Focus"] = "Large Cap"

            # BL-27-ext v24: All Cap default tras BL-27 (que puede haber llenado MCF)
            # Si después de todos los intentos RV sigue sin MCF y no es sectorial
            # → "All Cap" como valor semántico explícito
            if (_apply_allcap_default
                    and not fund_master_record.get("Market_Cap_Focus")):
                fund_master_record["Market_Cap_Focus"] = "All Cap"

            # INTER-MCF: Market_Cap_Focus (Large/Mid/Small/All Cap) solo aplica a
            # Renta Variable. Para natures no-equity (Monetario, RFCP, RF Flexible,
            # Alternativo, Restantes, Estructurado) Market_Cap_Focus es semántica-
            # mente incoherente y debe anularse. Extendido (2026-07-11) para cubrir
            # RF Flexible (73 fondos con 'All Cap' stale de ciclos anteriores) y
            # otras natures no-equity además de Monetario/RFCP.
            # _market_cap_focus_cleared=True → sqlite_writer OW para limpiar stale en BD.
            _MCF_NON_EQUITY_NATURES_INTER = (
                "Monetario", "Renta Fija Corto Plazo", "Renta Fija Flexible",
                "Alternativo", "Restantes", "Estructurado",
            )
            _mcf_inter_nature = fund_master_record.get("Fund_Nature")
            if _mcf_inter_nature in _MCF_NON_EQUITY_NATURES_INTER:
                _mcf_val = fund_master_record.get("Market_Cap_Focus")
                if _mcf_val:
                    log_ingestion(
                        conn, isin, "INTER_MCF_NULL", "INFO",
                        f"Market_Cap_Focus='{_mcf_val}' → NULL "
                        f"(no aplica para Fund_Nature='{_mcf_inter_nature}')"
                    )
                fund_master_record["Market_Cap_Focus"] = None
                fund_master_record["_market_cap_focus_cleared"] = True

            # ── Limpieza defensiva Benchmark_Declared (BL-38 v22) ──────────
            # Causa raíz: el parser puede devolver None para el benchmark,
            # pero BD preserva vía COALESCE el valor antiguo contaminado.
            # Adicionalmente, el parser puede capturar un benchmark contaminado
            # como string no-None (ej: "sofr), además" con texto narrativo
            # posterior), en cuyo caso el bloque anterior no activaba la limpieza.
            # Fix v22: verificar contaminación TANTO en el valor del dict actual
            # como en el valor de BD — en ambos casos limpiar.
            _BENCH_CONTAMINATION_MARKERS = [
                "además", "través", "último informe",
                " canal", "management (ireland)", "limited,",
                "bank and", "business centre", "route de",
                "hemos clasificado", "riesgo", "corro ",
                "página", "producto", " canales",
            ]

            def _is_bench_contaminated(val):
                if not val or val == "NO_BENCHMARK":
                    return False
                v_lower = val.lower()
                return (
                    len(val) > 100
                    or any(m in v_lower for m in _BENCH_CONTAMINATION_MARKERS)
                )

            _bench_new = parsed.get("Benchmark_Declared")
            # Caso A: el parser devolvió un benchmark — verificar si está contaminado
            if _bench_new and _is_bench_contaminated(_bench_new):
                fund_master_record["Benchmark_Declared"] = None
                log_ingestion(
                    conn, isin, "BENCHMARK_CLEANUP", "INFO",
                    f"Dict contaminado limpiado: {_bench_new[:60]!r}"
                )
            # Caso B: el parser devolvió None — verificar si BD tiene un valor
            # contaminado que el COALESCE preservaría
            elif not fund_master_record.get("Benchmark_Declared"):
                _bench_bd_row = conn.execute(
                    "SELECT Benchmark_Declared FROM fund_master WHERE ISIN=?",
                    (isin,)
                ).fetchone()
                _bench_bd = _bench_bd_row[0] if _bench_bd_row else None
                if _is_bench_contaminated(_bench_bd):
                    # Forzar NULL explícito (evita preservación vía COALESCE)
                    fund_master_record["Benchmark_Declared"] = None
                    log_ingestion(
                        conn, isin, "BENCHMARK_CLEANUP", "INFO",
                        f"BD contaminado limpiado: {_bench_bd[:60]!r}"
                    )

            # INTER-NTC: Name-vs-KIID-Text Contradiction check (genérico, todos
            # los bloques). Causa raíz (sesión 2026-06-30): get_universe_isins()
            # selecciona el universo de cada bloque por patrones del NOMBRE
            # (heurística), y classify_fund() no siempre contrasta esa selección
            # con señales fuertes del TEXTO del KIID. Esto permitió que 14 fondos
            # de bonos (iShares/PIMCO/Vanguard) entraran al universo RV por falsos
            # positivos de substring ("shares" en "ishares", "climate" en "pimco
            # climate bnd", "global" en "vgd global bd indx" — ver BL-RV-EX1/EX2
            # en renta_variable.py). Esos 14 casos ya se corrigieron en el origen
            # (universo + patrones), pero esta regla añade una red de seguridad
            # genérica para futuros casos similares en cualquier bloque, usando
            # Benchmark_Declared (ya extraído y depurado arriba) como señal de
            # texto independiente del nombre.
            # No re-enruta el fondo entre bloques (son mutuamente excluyentes y
            # el cambio de Heuristic_Block fuera de su bloque de origen podría
            # crear bucles) — solo degrada Data_Quality_Flag para visibilidad,
            # dejando la corrección de fondo a una investigación dirigida (mismo
            # patrón que el caso de los 14 fondos de bonos).
            _ntc_nature   = fund_master_record.get("Fund_Nature")
            _ntc_bench_l  = (fund_master_record.get("Benchmark_Declared") or "").lower()
            _NTC_BOND_KW = [
                "bond", "aggregate", "treasury", "gilt", "bund", "obligaciones",
                "corporate bond", "government bond", "credit index",
            ]
            _NTC_EQUITY_KW = [
                "msci world", "msci acwi", "msci europe", "msci emerging",
                "s&p 500", "stoxx europe", "euro stoxx", "ftse 100", "dax",
                "nasdaq 100", "russell 2000", "nikkei 225",
            ]
            _ntc_contradiction = None
            if _ntc_nature == "Renta Variable" and any(
                    k in _ntc_bench_l for k in _NTC_BOND_KW):
                _ntc_contradiction = (
                    f"Fund_Nature='Renta Variable' pero Benchmark_Declared "
                    f"sugiere renta fija: '{fund_master_record.get('Benchmark_Declared')}'"
                )
            elif _ntc_nature in (
                    "Monetario", "Renta Fija Corto Plazo", "Renta Fija Flexible"
            ) and any(k in _ntc_bench_l for k in _NTC_EQUITY_KW):
                # BL-NTC-SRRI (2026-07-05): Renta Fija Flexible con SRRI≤2
                # no puede tener mandato de renta variable (vol <5% es
                # incompatible con renta variable pura). El benchmark de equity
                # en estos fondos es aspiracional/comparativo, no el mandato
                # (ej. DWS Invest Conservative Opportunities: bonos
                # convertibles y preservación de capital con MSCI World como
                # referencia relativa). Confirmed: only affects RF_Flexible;
                # Monetario/RF_Corto nunca tienen SRRI≤2 con equity benchmark
                # en el corpus (0 casos verificado).
                _ntc_srri_raw = fund_master_record.get("SRRI")
                _ntc_srri_guard = (
                    _ntc_nature == "Renta Fija Flexible"
                    and _ntc_srri_raw is not None
                    and int(float(str(_ntc_srri_raw))) <= 2
                )
                if not _ntc_srri_guard:
                    _ntc_contradiction = (
                        f"Fund_Nature='{_ntc_nature}' pero Benchmark_Declared "
                        f"sugiere renta variable: '{fund_master_record.get('Benchmark_Declared')}'"
                    )
            if _ntc_contradiction:
                _dq_issues.append((
                    "INTER_NTC_CONTRADICTION", "WARN", "WARN",
                    _ntc_contradiction
                ))

            # Causa raíz: el parser solo detecta Hedged con señales positivas.
            # La ausencia de "hedged" en nombre/KIID no implica que el fondo
            # esté cubierto, pero tampoco implica que NO lo esté — salvo cuando
            # la divisa del fondo es la natural de su geografía (EUR+Europa,
            # USD+EEUU, etc.) y no hay señal explícita de hedge.
            # En ese caso, Unhedged es el default correcto porque no habría
            # motivo económico para cubrir una divisa que ya es la natural.
            # Estrategia conservadora: solo aplica si ambos Currency_Hedged
            # y Hedging_Policy están NULL (no hay detección previa en ningún
            # ciclo) y la combinación divisa/geografía es natural.
            # v22: se aplica también Hedging_Policy='UNHEDGED' simultáneamente,
            # garantizando coherencia entre ambos atributos desde el origen.
            #
            # BL-49/2 (2026-04-25): la condición de entrada considera AHORA los
            # valores en BD (_ch_bd, _hp_bd ya leídos en líneas 911-919). Sin
            # esto, fondos CACHED cuyo classifier no reemite Currency_Hedged
            # (porque _needs_char=False ya que BD tiene valor) entran al default
            # como (None, None) y se les sobreescribe con Unhedged, perdiendo el
            # valor real Hedged que ya estaba en BD. Causa raíz de 7 fondos en
            # regresión Hedged → Unhedged en el ciclo del 25/04/2026.
            _ch_eff_default = fund_master_record.get("Currency_Hedged") or _ch_bd
            _hp_eff_default = fund_master_record.get("Hedging_Policy") or _hp_bd
            if (not _ch_eff_default
                    and not _hp_eff_default):
                _fc = fund_master_record.get("Fund_Currency")
                _geo = fund_master_record.get("Geography")
                _name_l_nh = (fund_name or "").lower()
                # Exclusión: si el nombre menciona hedge, no aplicar default.
                # BL-49/2 (2026-04-25): añadidos EURH/USDH/GBPH/CHFH y EURHDG/etc.
                # SIN word boundaries internos (\b falla porque EUR+HDG no tiene
                # boundary). Patrón: (a) hedge/cubierta/cobertura como palabra
                # completa, o (b) prefijo divisa + h/hdg/hgd como sufijo, o (c)
                # variantes truncadas.
                _has_hedge_signal = bool(re.search(
                    r'\b(?:hedg(?:ed|ing)?|cubiert[oa]|cobertura)\b'
                    r'|\b(?:eur|usd|gbp|chf|jpy|cnh)h(?:dg|gd)?\b'
                    r'|\bhdg\b|\bhgd\b'
                    r'|(?:eur|usd|gbp|chf|jpy|cnh)hdg'
                    r'|(?:eur|usd|gbp|chf|jpy|cnh)hgd',
                    _name_l_nh))
                if not _has_hedge_signal:
                    _natural_combos = {
                        ("EUR", "Europa"), ("EUR", "España"), ("EUR", "Italia"),
                        ("EUR", "Alemania"), ("EUR", "Global"),
                        ("USD", "EEUU"), ("USD", "Norteamérica"), ("USD", "Global"),
                        ("GBP", "Reino Unido"), ("JPY", "Japón"),
                        ("CHF", "Suiza"),
                    }
                    if (_fc, _geo) in _natural_combos:
                        fund_master_record["Currency_Hedged"] = "Unhedged"
                        fund_master_record["Hedging_Policy"] = "UNHEDGED"

            # ── Inferencia Geography v20 (NULL → valor con ≥90% precisión) ──
            # Causa: 424 fondos tienen Geography=NULL (13.2%), mayoritariamente
            # RESTANTES con nombres ambiguos. Aplicar reglas de alta precisión
            # validadas contra los 2780 fondos ya clasificados.
            # Orden de precedencia: Universe → Nombre → Benchmark → KIID contexto.
            #
            # v21: leer Investment_Universe desde BD si el dict del ciclo lo tiene
            # a None (fondos CACHED donde el classifier no lo re-calcula).
            # Sin esta lectura, Regla 1 no capturaba los ~35 fondos Alternativos/Mixtos
            # con Universe='Global' en BD y Geography=NULL.
            if not fund_master_record.get("Geography"):
                _nat = fund_master_record.get("Fund_Nature")
                _liquidity_nats = ("Monetario", "Renta Fija Corto Plazo")
                if _nat not in _liquidity_nats:
                    # v21: fallback a BD para Investment_Universe
                    _universe = fund_master_record.get("Investment_Universe")
                    if not _universe:
                        _univ_bd = conn.execute(
                            "SELECT Investment_Universe FROM fund_master WHERE ISIN=?",
                            (isin,)
                        ).fetchone()
                        if _univ_bd and _univ_bd[0]:
                            _universe = _univ_bd[0]
                    _name_geo = (fund_name or "").upper()
                    _bench_geo = (fund_master_record.get("Benchmark_Declared") or "").lower()
                    _kiid_geo = (kiid_text or "")

                    _inferred_geo = None

                    # Regla 1: Universe=Global → Global (100% validado)
                    if _universe == "Global":
                        _inferred_geo = "Global"

                    # Regla 2: Nombre con patrones ≥90% precisión
                    if not _inferred_geo:
                        if re.search(r'\b(?:US|USA|AMERICAN|AMERIC)\b', _name_geo):
                            _inferred_geo = "EEUU"
                        elif re.search(r'\b(?:EUROP|EURO(?!\s*STR))\b', _name_geo):
                            _inferred_geo = "Europa"
                        elif re.search(r'\bCHINA\b|\bCHN\b', _name_geo):
                            _inferred_geo = "China"
                        elif re.search(r'\bASIA\b', _name_geo):
                            _inferred_geo = "Asia"

                    # Regla 3: Benchmark específico (≥95% validado)
                    if not _inferred_geo and _bench_geo and _bench_geo != "no_benchmark":
                        if re.search(r'\brussell\s+\d{4}\b|\bmsci\s+usa\b', _bench_geo):
                            _inferred_geo = "EEUU"
                        elif re.search(r'\bmsci\s+china\b|\bcsi\s*300\b|\bhang\s+seng\b', _bench_geo):
                            _inferred_geo = "China"

                    # Regla 4: KIID contextual (87-88% precisión, solo EEUU y Asia)
                    if not _inferred_geo and _kiid_geo:
                        _pat_us = re.compile(
                            r'(?:invierte|principalmente|invertir|mayormente)'
                            r'[\s\S]{0,60}?(?:estados\s+unidos|norteam[eé]rica|ee\.?uu\.?\b)',
                            re.IGNORECASE)
                        _pat_as = re.compile(
                            r'(?:invierte|principalmente|invertir|mayormente)'
                            r'[\s\S]{0,60}?(?:\basia\b|asi[aá]ticos?)',
                            re.IGNORECASE)
                        _has_us = _pat_us.search(_kiid_geo) is not None
                        _has_as = _pat_as.search(_kiid_geo) is not None
                        if _has_us and not _has_as:
                            _inferred_geo = "EEUU"
                        elif _has_as and not _has_us:
                            _inferred_geo = "Asia"

                    if _inferred_geo:
                        # FIX-GEO-1 (2026-07-05): _inferred_geo llega en
                        # vocabulario ES ("EEUU", "Europa", "Asia") -- mismo
                        # gap de traducción que el fallback BL-50 de arriba.
                        fund_master_record["Geography"] = _derive_geography_en(
                            _inferred_geo, _name_geo.lower()
                        )



            _total_ms = round((time.perf_counter() - _t_fund_start) * 1000)
            _breakdown = "|".join(
                f"{k}:{v}ms" for k, v in _t_phases.items()
            ) if _t_phases else ""

            # ── BL-COST-4c: Extracción de costes Sprint 2 ────────────────────────
            # Se ejecuta DESPUÉS de todas las normalizaciones de clasificación.
            # Kill-switch leído desde el módulo en runtime (BL-COST-4c-FIX):
            # evita el problema de sys.path no configurado en tiempo de import.
            # Principio #2 DRY: routing PRIIPs/UCITS lo hace detect_kid_format.
            # Atomicidad: _schedule_rows se pasa a publish_fund (A-3 S2-C).
            _schedule_rows: list = []
            # FIX-OC-WRITE-ORDER: initialized here so the no-COALESCE repair
            # can fire AFTER publish_fund (see post-publish block below).
            _oc_mismatch: bool = False
            _oc_mismatch_ter: "Optional[float]" = None
            _cost_enabled_rt = (
                _COST_EXTRACTORS_AVAILABLE
                and _priips_ext_mod is not None
                and getattr(_priips_ext_mod, 'PRIIPS_COST_EXTRACTION_ENABLED', False)
            )
            if _cost_enabled_rt:
                # Leer Cost_Extraction_Quality ya en BD para skip logic (A-5 S2-C)
                # y Ongoing_Charge/Entry/Exit para comparación mismatch (PC-3 S2-C).
                # SELECT dedicado — no ampliar _v3_row (preserva índice _v3_row[:5]).
                # P1-17: Management_Fee_Pct se lee también — el extractor lo usa SOLO
                # como destino de reparación de FIX-OC-BIND cuando no logra rederivar
                # la gestión del texto (nunca se republica como extracción).
                _cost_bd_row = conn.execute(
                    "SELECT Cost_Extraction_Quality, Ongoing_Charge_Recurrent, "
                    "Entry_Fee_Pct_Max, Exit_Fee_Pct_Max, Management_Fee_Pct "
                    "FROM fund_master WHERE ISIN=?", (isin,)
                ).fetchone()
                _ceq_bd   = _cost_bd_row[0] if _cost_bd_row else None
                _oc_bd    = _cost_bd_row[1] if _cost_bd_row else None
                _entry_bd = _cost_bd_row[2] if _cost_bd_row else None
                _exit_bd  = _cost_bd_row[3] if _cost_bd_row else None
                _mgmt_bd  = _cost_bd_row[4] if _cost_bd_row else None

                # v20 (§4.2): el bloque de coste se ejecuta SOLO con PDF en mano
                # (status ∈ refresh/new ⇔ pdf_bytes is not None). En CACHED
                # (pdf_bytes is None) se omite por completo: COALESCE preserva en
                # BD los valores de coste y los veredictos de arbitración previos.
                # Cambio de comportamiento NOMBRADO: antes el extractor de texto se
                # re-ejecutaba en CACHED (skip solo si _ceq_bd=='HIGH'); ahora se
                # cachea y solo recomputa en refresh (justificado por >96% acuerdo
                # / >98% exactitud del extractor).
                #
                # FIX-COST-RECOMPUTE-CACHED (2026-08-23): `recompute_costs` abre una
                # excepción EXPLÍCITA y bajo demanda a esa puerta. La puerta es una
                # decisión de RENDIMIENTO, no una dependencia de datos: el extractor
                # trabaja sobre `_text_for_cost`, y en CACHED io.py ya construye
                # `Fed_Text_For_Cost` = Raw_KIID_Text + DLA2_Table_Text (io.py §Caché A),
                # exactamente el mismo texto que en la ruta de descarga. Sin esta vía,
                # una corrección del extractor solo llega al corpus re-descargando
                # todos los PDFs afectados. La arbitración DLA2 de más abajo sigue
                # exigiendo pdf_bytes: esa sí necesita el binario.
                if pdf_bytes is not None or recompute_costs:
                    try:
                        from cost_format_router import detect_kid_format as _dkf
                    except ImportError:
                        from core.cost_format_router import detect_kid_format as _dkf

                    # FIX-DATA-INTEGRITY-3 (2026-06-17): io.py ya NO devuelve
                    # kiid_text pre-enriquecido con el bloque DLA2 (ver
                    # FIX-DATA-INTEGRITY-1/2 en io.py) — kiid_text permanece
                    # puro para Raw_KIID_Text. El texto enriquecido, cuando
                    # existe, llega vía kiid_meta["Fed_Text_For_Cost"]; si no
                    # (extracción sin tabla, flag desactivado, o ruta antigua),
                    # cae a kiid_text sin tabla (comportamiento ya correcto
                    # para KIIDs cuya composición de costes esté en texto llano).
                    _text_for_cost = kiid_meta.get("Fed_Text_For_Cost") or kiid_text

                    _fmt = _dkf(_text_for_cost)
                    _cost_dict: dict = {}

                    if _fmt == 'PRIIPS_KID':
                        _cost_dict = extract_priips_costs(
                            text=_text_for_cost,
                            isin=isin,
                            existing_oc=_oc_bd,
                            existing_entry=_entry_bd,
                            existing_exit=_exit_bd,
                            existing_mgmt=_mgmt_bd,     # P1-17: destino de reparación
                            parser_oc=parsed.get("Ongoing_Charge"),  # FIX-OC-PARSER-BIND
                        )
                    elif _fmt == 'UCITS_KIID':
                        _cost_dict = extract_ucits_costs(
                            text=_text_for_cost,
                            isin=isin,
                            existing_oc=_oc_bd,
                        )
                    # _fmt == 'UNKNOWN' → _cost_dict = {} → no se modifica nada

                    if _cost_dict:
                        # Extraer claves privadas antes de mezclar en fund_master_record
                        _schedule_rows    = _cost_dict.pop('_cost_schedule_rows', []) or []
                        _oc_mismatch      = _cost_dict.pop('_oc_aci_mismatch', False)
                        _oc_mismatch_ter  = _cost_dict.pop('_oc_aci_mismatch_ter_pct', None)

                        # Campos que van a fund_master (11 columnas Sprint 2)
                        _COST_FIELDS = {
                            'KID_Format', 'KID_Currency', 'Cost_Extraction_Quality',
                            'Cost_RHP_Years', 'Entry_Fee_Pct_Max', 'Exit_Fee_Pct_Max',
                            'Management_Fee_Pct', 'Transaction_Cost_Pct',
                            'Performance_Fee_Pct', 'ACI_1Y', 'ACI_RHP',
                            'Ongoing_Charge_Recurrent',   # solo presente si existing_oc is None
                        }
                        # COST-RANGE-GUARD (2026-07-18): guarda de seguridad de
                        # último recurso — los rangos están en los CHECK constraints
                        # del schema (db/schema_fondos.sql). Previene que un error
                        # de parsing fuera-de-rango aborte el UPSERT entero y pierda
                        # el fondo. La corrección de raíz es FIX-COST-RATIO-SAFE en
                        # cost_table_parser.py; esta guarda es cinturón + tirantes.
                        # P1-19 (2026-08-24): esta guarda vigilaba TODAS las columnas
                        # de coste MENOS `Ongoing_Charge_Recurrent`, que es justo la
                        # única con tres escritores distintos. Por ese hueco entraron
                        # sin oposición los 5 fondos con un gasto corriente del 208 %
                        # (FIX-OC-SCALE): ninguna guarda los miró.
                        #
                        # ⚠ TRAMPA DE ESCALA: los límites de abajo están en PORCENTAJE
                        # ENTERO, pero `Ongoing_Charge_Recurrent` está en RATIO DECIMAL.
                        # Su techo NO puede escribirse aquí como 25.0; se toma de
                        # `cost_scale.OC_RATIO_MAX`, la única definición de la
                        # convención (P#11 / R-1).
                        _COST_PCT_LIMITS: dict = {
                            'Transaction_Cost_Pct': (0.0, 5.0),
                            'Management_Fee_Pct':   (0.0, 10.0),
                            'Entry_Fee_Pct_Max':    (0.0, 25.0),
                            'Exit_Fee_Pct_Max':     (0.0, 25.0),
                            'Performance_Fee_Pct':  (0.0, 30.0),
                            'ACI_1Y':               (0.0, 50.0),
                            'ACI_RHP':              (0.0, 25.0),
                            # RATIO, no porcentaje — ver nota de arriba.
                            'Ongoing_Charge_Recurrent': (0.0, _OC_RATIO_MAX),
                        }
                        for _cf in _COST_FIELDS:
                            if _cf in _cost_dict:
                                _cv = _cost_dict[_cf]
                                if (_cv is not None and _cf in _COST_PCT_LIMITS):
                                    _lo, _hi = _COST_PCT_LIMITS[_cf]
                                    if not (_lo <= _cv <= _hi):
                                        log_ingestion(
                                            conn, isin, "COST_RANGE_GUARD", "WARN",
                                            f"{_cf}={_cv} fuera de rango [{_lo},{_hi}]"
                                            f" → NULL (parse error, fondo preservado)"
                                        )
                                        _cv = None
                                fund_master_record[_cf] = _cv

                        # BL-COST-5 repair moved to AFTER publish_fund — see post-publish block.
            # ── Fin BL-COST-4c ────────────────────────────────────────────────────

            # ── v20 (§4.2/§4.4): arbitración dual de coste (Job B) ─────────────────
            # Gated por kill-switch DLA2_ARBITRATION_ENABLED y por PDF-en-mano.
            # Reutiliza el binario ya cargado (un open por fondo, DRY). Sin efectos
            # secundarios: devuelve veredictos por componente que se persisten en
            # fund_kiid_metadata vía COALESCE (CACHED → no toca → preserva BD).
            _arb_fields: dict = {}
            if pdf_bytes is not None and _ARB_AVAILABLE and _dla2_arbitration_enabled():
                try:
                    _arb = arbitrate_costs_from_pdf(pdf_bytes)
                    _arb_fields = {
                        "Cost_Mgmt_BandsX":      _arb["mgmt"]["bandsx"],
                        "Cost_Mgmt_Ruled":       _arb["mgmt"]["ruled"],
                        "Cost_Mgmt_Arbitration": _arb["mgmt"]["verdict"],
                        "Cost_Oper_BandsX":      _arb["oper"]["bandsx"],
                        "Cost_Oper_Ruled":       _arb["oper"]["ruled"],
                        "Cost_Oper_Arbitration": _arb["oper"]["verdict"],
                        "Cost_ACI_RHP_BandsX":   _arb["aci"]["rhp_bandsx"],
                        "Cost_ACI_RHP_Ruled":    _arb["aci"]["rhp_ruled"],
                        "Cost_ACI_RHP_Arbitration": _arb["aci"]["rhp_verdict"],
                        "Cost_ACI_1Y_BandsX":    _arb["aci"]["1y_bandsx"],
                        "Cost_ACI_1Y_Ruled":     _arb["aci"]["1y_ruled"],
                        "Cost_ACI_1Y_Arbitration": _arb["aci"]["1y_verdict"],
                    }
                    # tabla de mayor fidelidad (si la hubiera) → extractor existente
                    if _arb.get("table_text"):
                        _arb_fields["DLA2_Table_Text"] = _arb["table_text"]
                except Exception as _arb_e:
                    log_ingestion(conn, isin, "DLA2_ARBITRATION", "WARN", str(_arb_e))

            # ── FIX-ARB-FALLBACK (2026-06-20): arbitration-by-result-quality ──────
            # pdfplumber's borderless/multi-line table extraction COLLAPSES the
            # cost grid on certain issuer layouts (BNP/DWS/Amundi families): the
            # whole composition section lands in one run-on cell, so the values
            # path (extract_priips_costs) yields NULL Management_Fee_Pct /
            # Transaction_Cost_Pct. The arbitration extractor (xBand, different
            # cell-detection) succeeds on exactly these layouts. Audit 2026-06-20:
            # 357 funds (223 oper + 150 mgmt, overlap) had values-path NULL AND
            # Arbitration='AGREE' with a stored value — 100% recoverable.
            # Rule (gated, fill-only, never override a successful extraction):
            #   values-path Pct is None  AND  component Arbitration == 'AGREE'
            #   AND BandsX value present  →  write BandsX into fund_master.
            # Scale: Cost_*_BandsX is stored in the SAME percent scale as
            # fund_master.*_Pct (confirmed empirically: BandsX==Pct on funds where
            # both succeeded), so NO _ratio_to_pct conversion is applied here.
            # 'AGREE' only: CONFLICT/ONLY_*/BOTH_FAIL are NOT trusted (BL-COST-5).
            if _arb_fields:
                _arb_map = [
                    ('Management_Fee_Pct',  'Cost_Mgmt_Arbitration', 'Cost_Mgmt_BandsX'),
                    ('Transaction_Cost_Pct', 'Cost_Oper_Arbitration', 'Cost_Oper_BandsX'),
                    ('ACI_RHP',            'Cost_ACI_RHP_Arbitration', 'Cost_ACI_RHP_BandsX'),
                    ('ACI_1Y',             'Cost_ACI_1Y_Arbitration',  'Cost_ACI_1Y_BandsX'),
                ]
                # P0-ARB-GUARD: BandsX ACI values above threshold are xband
                # extraction errors (scenario section bleed). Confirmed:
                # LU0503631987 had Cost_ACI_RHP_BandsX > 25, blocking publish.
                # Tightened 2026-06-28 (audit RC-01): multi-year RHP guard
                # lowered to 15% to match P0-ACI-RHP-GUARD in priips_cost_extractor.
                # ACI is stored in percent form in fund_master (schema CHECK <= 25).
                _ACI_COLS = ('ACI_RHP', 'ACI_1Y')
                _rhp_yrs = fund_master_record.get('Cost_RHP_Years') or 1.0
                _MAX_ACI_PCT = 15.0 if _rhp_yrs > 1.0 else 25.0
                for _fm_col, _verdict_col, _bandsx_col in _arb_map:
                    _verdict = _arb_fields.get(_verdict_col)
                    _bandsx  = _arb_fields.get(_bandsx_col)
                    _accept = (_verdict == 'AGREE'
                               or (_fm_col in ('ACI_RHP', 'ACI_1Y')
                                   and _verdict == 'ONLY_BANDS_X'))
                    if _fm_col in _ACI_COLS and _bandsx is not None and _bandsx > _MAX_ACI_PCT:
                        log_ingestion(conn, isin, "FIX_ARB_FALLBACK", "WARN",
                                      f"{_fm_col}={_bandsx} from {_bandsx_col} rejected "
                                      f"(>{_MAX_ACI_PCT}% — parser bleed, not written)")
                        continue
                    if (fund_master_record.get(_fm_col) is None
                            and _accept
                            and _bandsx is not None):
                        fund_master_record[_fm_col] = _bandsx
                        log_ingestion(conn, isin, "FIX_ARB_FALLBACK", "INFO",
                                      f"{_fm_col}={_bandsx} from {_bandsx_col} "
                                      f"(values-path NULL, arbitration {_verdict})")

            # PDF ya consumido por parse + coste + arbitración: liberar (memoria).
            pdf_bytes = None

            # ── INTER-FASE4 (2026-07-11): Validación semántica universal ─────────
            # Hasta este punto, validate_all_semantic_consistency solo se invocaba
            # desde restantes.py. Este bloque garantiza cobertura universal: TODOS
            # los fondos (de cualquier bloque) pasan por la función maestra TRAS
            # la consolidación completa de atributos (R-4: effective values ya
            # presentes en fund_master_record desde los bloques INTER anteriores).
            #
            # A1: llamada directa (no apply_semantic_validation — derive_v20_attributes
            #     ya fue invocada en la fase de characterize, no debe re-ejecutarse aquí).
            # A2: merge campo a campo (no blanket replace). La función es idempotente;
            #     si el inline ya corrigió un campo, corrected_record tiene el mismo
            #     valor → merge es no-op. Si el inline no lo corrigió, corrected_record
            #     aporta la corrección faltante.
            # A3: extender _dq_issues con inconsistencias residuales (persistidas en
            #     fund_data_quality_issues vía _finalize_data_quality_issues abajo).
            # SC-H: pull benchmark scalars for this ISIN (batch-loaded above).
            _bmk_scalars = _bmk_by_isin.get(isin, {})
            _sem_val_result = validate_all_semantic_consistency(
                fund_master_record,
                ext_asset_class    = _bmk_scalars.get("asset_class"),
                ext_role           = _bmk_scalars.get("benchmark_role"),
                ext_benchmark_name = _bmk_scalars.get("benchmark_name"),
                ext_confidence     = _bmk_scalars.get("confidence"),
            )
            _sem_corrected_rec = _sem_val_result.get("corrected_record", {})

            # A2: merge field-by-field; omitir claves privadas (p.ej. _signal_*)
            for _sk, _sv in _sem_corrected_rec.items():
                if not _sk.startswith("_"):
                    fund_master_record[_sk] = _sv

            # INTER-MCF universal: si el master-validator anuló Market_Cap_Focus
            # para una naturaleza no-equity, marcar para overwrite (bypass COALESCE).
            if (_sem_corrected_rec.get("Market_Cap_Focus") is None
                    and fund_master_record.get("Fund_Nature") in _MCF_NON_EQUITY_NATURES_INTER
                    and not fund_master_record.get("_market_cap_focus_cleared")):
                fund_master_record["Market_Cap_Focus"] = None
                fund_master_record["_market_cap_focus_cleared"] = True

            # A3: persistir errores y warnings al DQ table (flush abajo en _finalize)
            _dq_issues.extend(semantic_validation_to_dq_tuples(_sem_val_result))

            # FIX-DQ-1: rollup final de Data_Quality_Flag -- único punto de
            # escritura, tras acumular todos los issues detectados durante
            # el procesamiento de este ISIN (ver _dq_issues arriba).
            fund_master_record["Data_Quality_Flag"] = _finalize_data_quality_issues(
                conn, isin, _derive_data_quality_flag(parsed), _dq_issues,
            )

            kiid_record = {
                "ISIN": isin,
                "KIID_URL": kiid_meta.get("KIID_URL"),                
                "KIID_Class": 1,
                "SRRI": parsed.get("SRRI"),
                "SRRI_Visual": parsed.get("SRRI_Visual"),
                "SRRI_Textual": parsed.get("SRRI_Textual"),
                "SRRI_Validation_Status": parsed.get("SRRI_Validation_Status"),

                #Benchmark_Declared
                #"Inference_Trace": parsed.get("Inference_Trace"),
                "Language": parsed.get("Language"),
                "Raw_KIID_Text": kiid_text,                
                "KIID_Published_Date": parsed.get("KIID_Published_Date"),
                "KIID_Downloaded_At": kiid_meta.get("KIID_Downloaded_At"),                
                "KIID_PDF_Hash": kiid_meta.get("KIID_PDF_Hash"),
                "KIID_Status": kiid_meta.get("KIID_Status"),
                # BL-COST-METADATA-FIX: persistir la tabla DLA2 serializada que io.py
                # ya produjo y guardo en kiid_meta (_process_pdf_bytes). Antes nunca se
                # copiaba a kiid_record -> upsert_kiid_metadata leia None -> COALESCE
                # preservaba NULL -> DLA2_Table_Text 0/N para siempre.
                "DLA2_Table_Text": kiid_meta.get("DLA2_Table_Text"),
                "Processing_Time_Ms":   _total_ms,
                "Processing_Breakdown": _breakdown,
                # v20: veredictos de arbitración (NULL/ausentes si flag off o CACHED)
                **_arb_fields,
            }

            publish_fund(conn, fund_master_record, None, kiid_record,
                         cost_schedule_rows=_schedule_rows or None)

            # FIX-OC-WRITE-ORDER (2026-08-23): BL-COST-5 no-COALESCE repair fires
            # HERE — after publish_fund — so the COALESCE UPSERT above cannot
            # overwrite the correction with a parser-emitted value.
            # Pre-fix the call was at line ~2949 (before publish_fund); any non-NULL
            # parsed["Ongoing_Charge"] fed via COALESCE(excluded, col) re-contaminated
            # the DB value on every reprocess.
            if _oc_mismatch and _oc_mismatch_ter is not None:
                correct_oc_aci_mismatch(conn, isin, _oc_mismatch_ter,
                                        source_note="BL-COST-5")
                log_ingestion(conn, isin, "BL_COST_4C_OC_ACI_MISMATCH",
                              "FIX", f"OC corregido: {_oc_mismatch_ter:.4f}% (TER recon)")
            elif _oc_mismatch:
                log_ingestion(conn, isin, "BL_COST_4C_OC_ACI_MISMATCH",
                              "WARN", "OC parece ACI pero TER no reconstruible; sin cambio")

            published.append(fund_master_record)

        except Exception as e:
            import traceback; traceback.print_exc()          # ← añadir esta línea
            print(f"  [ERROR] {block_name} {isin}: {e}")
            log_ingestion(conn, isin, f"{block_name}_PROCESS", "ERROR", str(e))
            if stop_on_error:
                raise
        finally:
            # Timing summary — siempre visible, independientemente de CACHED vs descarga
            _elapsed = round((time.perf_counter() - _t_fund_start) * 1000)
            # BL-LOG-TRUTH: etiquetar por KIID_Source real (CACHE/LOCAL/REMOTE),
            # no por "KIID_Status != CACHED". Antes, un fondo servido desde el
            # repositorio LOCAL (FLUJO A) volvía con KIID_Status='OK' y se
            # etiquetaba [DESCARGA], simulando una descarga de red inexistente
            # — causa del diagnóstico erróneo de "regresión de descarga".
            # Solo REMOTE es descarga real; LOCAL es lectura de repositorio.
            # BL-LOG-NORMALIZE: emitir SIEMPRE la línea [STATE] timings para
            # TODOS los fondos procesados (30/30). Antes 'if _elapsed > 2000 or
            # _is_download' silenciaba las lecturas LOCAL/CACHED rápidas (<2s),
            # dejando opaco el Subset B (fast-path por hash). Sin gate: estado
            # consistente y métricas siempre visibles. El estado se deriva de
            # KIID_Source real; si falta (skip/error), cae a KIID_Status.
            _src = (kiid_meta or {}).get("KIID_Source")
            _label = ({"CACHE": "CACHED", "LOCAL": "LOCAL", "REMOTE": "DESCARGA"}.get(_src)
                      or (kiid_meta or {}).get("KIID_Status")
                      or "UNKNOWN")
            _phase_str = " | ".join(f"{k}:{v}ms" for k, v in _t_phases.items()) if _t_phases else ""
            print(f"  [{_label}] {_elapsed}ms  {_phase_str}")
            kiid_text = kiid_meta = parsed = classification = pdf_bytes = None
            gc.collect()

    # ── Resumen de incidencias del ciclo (normativa sección 7.5 v2) ──────
    # Schema: ingestion_log (id, ISIN, step, status, message, created_at)
    # Filtramos por created_at >= _cycle_start_ts para acotar al ciclo actual.
    try:
        _incidencias = conn.execute("""
            SELECT step, status, COUNT(DISTINCT ISIN) as n
            FROM ingestion_log
            WHERE created_at >= ?
              AND status != 'OK'
            GROUP BY step, status
            ORDER BY n DESC
        """, (_cycle_start_ts,)).fetchall()
        if _incidencias:
            print("\n--- RESUMEN DE INCIDENCIAS DEL CICLO ---")
            for _step, _status, _n in _incidencias:
                print(f"  [{_status}] {_step}: {_n} fondos")
            print("---")
    except Exception as _e_summary:
        print(f"  [WARN] No se pudo generar resumen de incidencias: {_e_summary}")

    # FIX-DQ-STALE-SWEEP-1 (2026-07-21): purge stale fund_data_quality_issues
    # rows for funds not processed in this cycle, restoring the table's
    # "current issues only, rebuilt each cycle" contract (SCHEMA_REFERENCE).
    # Root cause: the per-ISIN DELETE+INSERT (_finalize_data_quality_issues)
    # only clears rows for ISINs that PASS through classification this cycle;
    # ISINs not attempted (not in master, or WRONG_DOC/NOT_FOUND that hit
    # `continue`) keep their rows from previous cycles indefinitely → inflated
    # WARN counts in dashboards/P3. Fix: delete all rows whose detected_at
    # predates this cycle's start timestamp — they were not refreshed here.
    # Guard: only runs on full-universe runs (nature_first=True, no list_isin,
    # no sample_size) to avoid wiping valid rows during partial runs.
    if nature_first and list_isin is None and sample_size is None:
        try:
            _dq_deleted = conn.execute(
                "DELETE FROM fund_data_quality_issues WHERE detected_at < ?",
                (_cycle_start_ts,)
            ).rowcount
            if _dq_deleted:
                print(f"[DQ-SWEEP] purged {_dq_deleted} stale "
                      f"fund_data_quality_issues rows "
                      f"(detected_at < {_cycle_start_ts})")
            conn.commit()
        except Exception as _e_sweep:
            print(f"  [WARN] FIX-DQ-STALE-SWEEP-1: {_e_sweep}")

    try:
        log_ingestion(
            conn, None, "RUN_SUMMARY", "OK",
            f"published={len(published)} cycle_start={_cycle_start_ts}"
        )
    except Exception:
        pass

    # FIX-OC-WRITE-ORDER rollout guard (2026-08-23): count active funds that
    # still have Ongoing_Charge_Recurrent ≈ ACI_RHP (the contamination
    # signature). These funds need --recompute-costs to be repaired; they
    # were not reprocessed in this cycle (CACHED or not in universe scope).
    # Goes to 0 after a full --recompute-costs sweep.
    try:
        _oc_contaminated = conn.execute("""
            SELECT COUNT(*) FROM fund_master
            WHERE In_Current_Universe = 1
              AND Ongoing_Charge_Recurrent IS NOT NULL
              AND ACI_RHP IS NOT NULL
              AND ABS(Ongoing_Charge_Recurrent * 100.0 - ACI_RHP) < 0.01
        """).fetchone()[0]
        if _oc_contaminated > 0:
            print(
                f"  [WARN] FIX-OC-WRITE-ORDER: {_oc_contaminated} fondo(s) activos con "
                f"Ongoing_Charge_Recurrent≈ACI_RHP (valor contaminado). "
                f"Ejecutar --recompute-costs para reparar."
            )
            log_ingestion(
                conn, None, "OC_ACI_CONTAMINATION", "WARN",
                f"active_funds_oc_eq_aci={_oc_contaminated}; run --recompute-costs"
            )
    except Exception:
        pass

    return published


# =============================================================
# BL-53/56/57: Barrido global post-pipeline (Principio #1 + #2)
# =============================================================
#
# Causa raíz arquitectónica:
#   _post_upsert_normalize_db() en sqlite_writer.py opera sobre el ISIN
#   recién upserted (WHERE ISIN=?). Los fondos no procesados en el ciclo
#   (KIID_Status=WRONG_DOC, sin bloque que los recoja, errores) conservan
#   indefinidamente sus valores stale en inglés en BD.
#
# Solución: tras procesar TODOS los bloques del ciclo, ejecutar UNA query
# global sin filtro de ISIN que normaliza Sector_Focus/Type/Subtype y
# traduce Family. Idempotente; coste ~150ms sobre 3.204 filas.
#
# El orquestador (run_pipeline.py o cmd) debe invocar:
#   from core.pipeline import run_global_normalization
#   run_global_normalization(conn)
# tras el último run_block() y antes del cierre de la BD.
# =============================================================

def run_global_normalization(conn) -> dict:
    """
    Aplica normalización lingüística global sobre fund_master.

    Cubre los fondos que no entraron en ningún bloque del ciclo y que por
    tanto no pasaron por _post_upsert_normalize_db (filtrado por ISIN).

    Returns:
        dict con métricas de filas afectadas para logging/auditoría.
    """
    metrics = global_post_pipeline_normalize_db(conn)
    print(f"[GLOBAL_NORM] {metrics}")
    return metrics

