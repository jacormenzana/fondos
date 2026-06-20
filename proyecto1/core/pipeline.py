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
from pathlib import Path
from typing import Optional, List, Dict, Any

import pandas as pd
import re

from core.io import get_kiid_for_isin
from core.kiid_parser import parse_kiid_generic
from core.classify_utils import (
    detect_strategy        as _detect_strategy,
    detect_benchmark_type  as _detect_benchmark_type,
    detect_theme           as _detect_theme_pipeline,       # ← NUEVO
    apply_post_characterize_normalization,                   # BL-56
    map_theme_to_sector_focus,                              # BL-54
    validate_strategy_replication,                          # BL-61
    detect_currency_hedged_from_kiid,                       # BL-49
    propagate_nature_to_restantes_type_family,              # BL-62
)
try:
    from proyecto1.core.fund_characterizer import characterize_fund
except ImportError:
    from core.fund_characterizer import characterize_fund
from core.sqlite_writer import (
    publish_fund,
    log_ingestion,
    global_post_pipeline_normalize_db,   # BL-53/56/57: barrido global
)
from core._db_utils import EffectiveReader   # BL-49/50: lectura efectiva

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


def _safe_scalar(v):
    """BL-SRRI-GUARD-FULL: coerce a possibly-dict SRRI payload to an int scalar.

    The anomalous CACHED path can yield ``{'SRRI': n, ...}`` instead of an int.
    Root-cause guard (single source): extract the scalar if dict, int-coerce
    (tolerating float/str/NaN like BL-64b), else None. Prevents the
    ``'>=' not supported between instances of 'dict' and 'int'`` crash at every
    downstream SRRI consumer (classify, characterize, fund_master, kiid_record).
    """
    if isinstance(v, dict):
        v = v.get("SRRI")
    if v is None:
        return None
    try:
        return int(float(str(v)))
    except (ValueError, TypeError):
        return None


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
            continue

        df = df.rename(columns={
            isin_col: "ISIN",
            name_col: "Fund_Name",
        })

        df = df[df["ISIN"].notna()].copy()

        # la gestora viene del nombre de la hoja
        df["Management_Company"] = sheet.strip()

        frames.append(df[["ISIN", "Fund_Name", "Management_Company"]])

    if not frames:
        raise ValueError("No se ha encontrado ninguna hoja válida con columna ISIN.")

    master_df = pd.concat(frames, ignore_index=True)
    return master_df


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

def run_block(
    block_module,
    df_master: pd.DataFrame,
    conn,
    master_excel_path: Path,
    sample_size: Optional[int] = None,
    stop_on_error: bool = False,
    #list_isin: Optional[List[str]] = None,
    list_isin: Optional[List[str]] = None,
    kiid_source: str = "auto",
) -> List[Dict[str, Any]]:

    block_name = getattr(block_module, "BLOCK_NAME", block_module.__name__).upper()
    heuristic_core = 0 if block_name == "RESTANTES" else 1

    # BL-KIID-LOCAL-FIRST: traza de la modalidad de carga del binario KIID.
    print(f"[{block_name}] kiid_source={kiid_source}")

    get_universe = dynamic_getattr(
        block_module,
        ["get_universe_isins", "get_heuristic_isins", "get_universe", "get_isins"],
    )
    if not get_universe:
        raise AttributeError(f"{block_module.__name__} no expone función de universo.")

    # -----------------------------
    # Selección de ISINs
    # -----------------------------
    if list_isin:
        isins = list_isin
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
    _cycle_start_ts = datetime.datetime.utcnow().isoformat(timespec="seconds")

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
            kiid_text, kiid_meta = get_kiid_for_isin(isin, str(master_excel_path), conn=conn, kiid_source=kiid_source)
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

            # ── Override universal: Estructurado ─────────────────────────
            # Antes de llamar al bloque, verificar si el fondo es un producto
            # estructurado. Si lo es, la naturaleza es Estructurado
            # independientemente del bloque de entrada.
            _t0 = time.perf_counter()
            _name_l = (fund_name or "").lower()
            _structured_kw = ["autocall", "structured", "estructurado",
                              "capital protec", "guaranteed", "barrier"]
            _is_structured = any(k in _name_l for k in _structured_kw)

            classifier = dynamic_getattr(block_module, ["classify_fund"])
            if classifier:
                # restantes.py acepta benchmark_declared y srri_parsed
                _bench = parsed.get("Benchmark_Declared")
                # BL-SRRI-GUARD-FULL: escalar saneado vía _safe_scalar (single source).
                _srri_for_classify = _safe_scalar(parsed.get("SRRI"))
                try:
                    classification = classifier(fund_name, kiid_text,
                                                benchmark_declared=_bench,
                                                srri_parsed=_srri_for_classify)
                except TypeError:
                    classification = classifier(fund_name, kiid_text)

            else:
                classification = {}

            # ── Override Fund_Nature si es estructurado ──────────────────
            if _is_structured:
                classification["Fund_Nature"] = "Estructurado"

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
                _srri_for_char = _safe_scalar(parsed.get("SRRI")) or classification.get("SRRI")
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

            # BL-65: RESTANTES puede emitir Fund_Nature=None cuando no puede
            # determinar la naturaleza financiera real. En ese caso forzar DQ=WARN
            # para trazabilidad y auditoría manual posterior.
            if block_name == "RESTANTES" and classification.get("Fund_Nature") is None:
                log_ingestion(
                    conn, isin, "BL65_NATURE_UNKNOWN", "WARN",
                    "Fund_Nature no determinable por RESTANTES → Data_Quality_Flag=WARN"
                )


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
                "Geography": classification.get("Geography"),
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
                "Development_Status": classification.get("Development_Status"),
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
                "SRRI": _safe_scalar(parsed.get("SRRI")),
                "Fund_Currency": parsed.get("Fund_Currency"),
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

            # BL-65: si Fund_Nature=None (naturaleza no determinable), forzar DQ=WARN
            # independientemente de la calidad SRRI.
            if fund_master_record.get("Fund_Nature") is None:
                fund_master_record["Data_Quality_Flag"] = "WARN"


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

            # BL-INTER3-WARN: Profile NO se auto-corrige desde SRRI.
            # INTER-3 es warnings-only y vive en classify_utils.validate_profile_srri
            # (R-1, single source). Profile = f(SRRI, Fund_Nature); el remap inline
            # previo (Conservador & SRRI>=5 -> Dinámico) queda retirado.

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
            # Umbrales: Monetario SRRI≥3, RFC SRRI≥4 (alineados con los bloques).
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
                _reclasify44 = (
                    (_nat44 == "Monetario" and _srri44_int >= 3)
                    or (_nat44 == "Renta Fija Corto Plazo" and _srri44_int >= 4)
                )
                if _reclasify44:
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
                fund_master_record = propagate_nature_to_restantes_type_family(
                    fund_master_record,
                    isin,
                    log_fn=print,
                )

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
                    fund_master_record["Investment_Universe"] = "Liquidity"
                elif _geo in ("EEUU", "China", "Japón", "India", "Brasil",
                              "Corea del Sur", "Australia", "Canadá",
                              "México", "Rusia", "Italia", "Alemania",
                              "Francia", "España", "Reino Unido", "Suiza"):
                    fund_master_record["Investment_Universe"] = "Country"
                elif _geo in ("Europa", "Asia", "Emergentes",
                              "Latinoamérica", "Europa del Este",
                              "Asia Pacífico", "Oriente Medio", "África",
                              "Europa Central", "América del Norte"):
                    fund_master_record["Investment_Universe"] = "Regional"
                elif _geo == "Global":
                    fund_master_record["Investment_Universe"] = "Global"

            # BL-52: corrección semántica Investment_Universe='Country' cuando
            # Geography contiene una región (no un país individual).
            # Causa raíz: el clasificador asigna Country pero luego la inferencia
            # de Geography devuelve un valor de región amplia (Latinoamérica,
            # Europa del Este, etc.) que es semánticamente incompatible con Country.
            _REGION_VALUES = {
                "Latinoamérica", "Europa del Este", "Asia Pacífico",
                "Emergentes", "América Latina", "Europa Central",
                "África", "Oriente Medio", "América del Norte",
            }
            _univ_eff = fund_master_record.get("Investment_Universe")
            _geo_eff  = fund_master_record.get("Geography")
            if _univ_eff == "Country" and _geo_eff in _REGION_VALUES:
                fund_master_record["Investment_Universe"] = "Regional"

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
                        fund_master_record["Geography"] = "Europa"
                    elif _curr_liq == "USD":
                        fund_master_record["Geography"] = "EEUU"
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
                        fund_master_record["Geography"] = _inferred_geo_b

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

            # BL-64e: INTER Nature↔Family — RFC no puede tener Family de RF Flexible
            # Causa raiz: RESTANTES detecta Nature=RFC por SRRI bajo y delega a
            # rf_flexible que asigna Family granular (RF Emergentes, RF High Yield...).
            # Esas families son incompatibles con RFC por definicion del schema.
            # 3 fondos afectados: BGF China Bond (LU2267/LU0719/LU0764).
            _RFC_INCOMPATIBLE_FAMILIES = {
                "Emerging Market Debt", "High Yield", "Inflation-Linked",
                "Strategic Allocation", "Flexible Fixed Income",
            }
            if (fund_master_record.get("Fund_Nature") == "Renta Fija Corto Plazo"
                    and fund_master_record.get("Family") in _RFC_INCOMPATIBLE_FAMILIES):
                fund_master_record["Family"] = "Short-Term Fixed Income"
                fund_master_record["Type"]   = fund_master_record.get("Type") or "Short-Term Fixed Income"

            # BL-53/54: Sector_Focus ya se emite en inglés (GICS-EN) desde el
            # emisor único THEME_TO_SECTOR_FOCUS_MAP (classify_utils). El antiguo
            # remap inline _SF_ES_TO_EN (BL-64c) queda retirado (Principio #1/#2).
            # La normalización de saneo vive en classify_utils.normalize_sector_focus.

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

            # BL-31: Currency_Hedged contradice Hedging_Policy → Hedging_Policy prevalece
            # Usar valores efectivos (actual o BD previa) para detectar el conflicto
            _ch_p = fund_master_record.get("Currency_Hedged") or _ch_bd
            _hp_p = fund_master_record.get("Hedging_Policy") or _hp_bd
            if _ch_p and _hp_p:
                _hp_as_ch = "Hedged" if _hp_p == "HEDGED" else "Unhedged"
                if _ch_p != _hp_as_ch:
                    fund_master_record["Currency_Hedged"] = _hp_as_ch

            # BL-45 v24: Hedging_Policy inferida desde Currency_Hedged cuando HP=NULL
            # Si Currency_Hedged está poblado pero Hedging_Policy es NULL, son
            # semánticamente equivalentes → propagar el valor (199 fondos).
            # Se ejecuta tras BL-31 para usar los valores ya validados (_ch_p/_hp_p).
            # Solo actúa si Hedging_Policy sigue NULL tras BL-31.
            if not (fund_master_record.get("Hedging_Policy") or _hp_bd):
                _ch_eff = fund_master_record.get("Currency_Hedged") or _ch_bd
                if _ch_eff == "Hedged":
                    fund_master_record["Hedging_Policy"] = "HEDGED"
                elif _ch_eff == "Unhedged":
                    fund_master_record["Hedging_Policy"] = "UNHEDGED"

            # BL-49/3: propagación inversa HP → CH cuando CH=NULL pero HP poblado.
            # Causa raíz previa: BL-31 solo dispara con AMBOS poblados; BL-45
            # solo cubre CH→HP. Faltaba la simetría HP→CH. Resuelve los 29
            # fondos del export con HP poblado y CH=NULL.
            if not (fund_master_record.get("Currency_Hedged") or _ch_bd):
                _hp_eff_b49 = fund_master_record.get("Hedging_Policy") or _hp_bd
                if _hp_eff_b49 == "HEDGED":
                    fund_master_record["Currency_Hedged"] = "Hedged"
                elif _hp_eff_b49 == "UNHEDGED":
                    fund_master_record["Currency_Hedged"] = "Unhedged"

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
                        fund_master_record["Geography"] = _inferred_geo



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
            _cost_enabled_rt = (
                _COST_EXTRACTORS_AVAILABLE
                and _priips_ext_mod is not None
                and getattr(_priips_ext_mod, 'PRIIPS_COST_EXTRACTION_ENABLED', False)
            )
            if _cost_enabled_rt:
                # Leer Cost_Extraction_Quality ya en BD para skip logic (A-5 S2-C)
                # y Ongoing_Charge/Entry/Exit para comparación mismatch (PC-3 S2-C).
                # SELECT dedicado — no ampliar _v3_row (preserva índice _v3_row[:5]).
                _cost_bd_row = conn.execute(
                    "SELECT Cost_Extraction_Quality, Ongoing_Charge_Recurrent, "
                    "Entry_Fee_Pct_Max, Exit_Fee_Pct_Max "
                    "FROM fund_master WHERE ISIN=?", (isin,)
                ).fetchone()
                _ceq_bd   = _cost_bd_row[0] if _cost_bd_row else None
                _oc_bd    = _cost_bd_row[1] if _cost_bd_row else None
                _entry_bd = _cost_bd_row[2] if _cost_bd_row else None
                _exit_bd  = _cost_bd_row[3] if _cost_bd_row else None

                # v20 (§4.2): el bloque de coste se ejecuta SOLO con PDF en mano
                # (status ∈ refresh/new ⇔ pdf_bytes is not None). En CACHED
                # (pdf_bytes is None) se omite por completo: COALESCE preserva en
                # BD los valores de coste y los veredictos de arbitración previos.
                # Cambio de comportamiento NOMBRADO: antes el extractor de texto se
                # re-ejecutaba en CACHED (skip solo si _ceq_bd=='HIGH'); ahora se
                # cachea y solo recomputa en refresh (justificado por >96% acuerdo
                # / >98% exactitud del extractor).
                if pdf_bytes is not None:
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
                        _schedule_rows = _cost_dict.pop('_cost_schedule_rows', []) or []
                        _oc_mismatch   = _cost_dict.pop('_oc_aci_mismatch', False)

                        # Campos que van a fund_master (11 columnas Sprint 2)
                        _COST_FIELDS = {
                            'KID_Format', 'KID_Currency', 'Cost_Extraction_Quality',
                            'Cost_RHP_Years', 'Entry_Fee_Pct_Max', 'Exit_Fee_Pct_Max',
                            'Management_Fee_Pct', 'Transaction_Cost_Pct',
                            'Performance_Fee_Pct', 'ACI_1Y', 'ACI_RHP',
                            'Ongoing_Charge_Recurrent',   # solo presente si existing_oc is None
                        }
                        for _cf in _COST_FIELDS:
                            if _cf in _cost_dict:
                                fund_master_record[_cf] = _cost_dict[_cf]

                        # Señalizar mismatch OC/ACI para BL-COST-5
                        if _oc_mismatch:
                            log_ingestion(conn, isin, "BL_COST_4C_OC_ACI_MISMATCH",
                                          "WARN", "OC en BD parece ACI; diferido a BL-COST-5")
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
                    }
                    # tabla de mayor fidelidad (si la hubiera) → extractor existente
                    if _arb.get("table_text"):
                        _arb_fields["DLA2_Table_Text"] = _arb["table_text"]
                except Exception as _arb_e:
                    log_ingestion(conn, isin, "DLA2_ARBITRATION", "WARN", str(_arb_e))

            # PDF ya consumido por parse + coste + arbitración: liberar (memoria).
            pdf_bytes = None

            kiid_record = {
                "ISIN": isin,
                "KIID_URL": kiid_meta.get("KIID_URL"),                
                "KIID_Class": 1,
                "SRRI": _safe_scalar(parsed.get("SRRI")),
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

