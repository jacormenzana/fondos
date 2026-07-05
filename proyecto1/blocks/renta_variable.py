from typing import Optional, Dict, List
from core.classify_utils import (
    NAME_SIGNALS_RV,
    FAMILY_EQUITY_CORE,
    FAMILY_THEMATIC_EQUITY,
    TYPE_ACTIVE_MANAGEMENT,
    TYPE_INDEX_FUND,
    SUBTYPE_INDEX_FUND,
    SUBTYPE_ETF,
    detect_geography       as _detect_geography,
    detect_theme           as _detect_theme,
    detect_is_esg          as _detect_is_esg,
    detect_style_profile   as _detect_style_profile,
    detect_exposure_bias   as _detect_exposure_bias,
    detect_strategy        as _detect_strategy,
    detect_benchmark_type  as _detect_benchmark_type,
    detect_profile_from_srri as _detect_profile_from_srri,
    detect_kiid_attributes,
    apply_semantic_validation,
)
import re


BLOCK_NAME = "renta_variable"
FUND_NATURE_VALUE = "Renta Variable"


# =====================================================
# Universo del bloque
# =====================================================

def get_universe_isins(df_master) -> List[str]:
    include_patterns = [
        "equity", "equities", "shares", "stock",
        "accion", "acciones",
        "technology", "tech", "health", "healthcare",
        "climate", "clean energy", "renewable",
        "value", "growth", "quality", "income",
        "emerging", "europe", "usa", "global",
        # BL-RV-IN2 (2026-07-04): "ishares" as unconditional include. The
        # BL-RV-EX2 word-boundary fix on "shares" (correctly blocking iShares
        # BOND funds from entering via the "ishares" substring) also orphaned
        # genuine iShares EQUITY index funds ("ISHARES WLD EQ INDX", "ISHARES
        # JAP INDX", "ISHARES US/UK INDEX") whose abbreviated country/asset
        # tags ("EQ", "JAP", "US", "UK") match none of the other include
        # patterns -- confirmed via full-corpus orphan check (16 funds).
        # Safe because exclude_patterns run FIRST: any iShares BOND fund is
        # already excluded upstream via "bnd"/"bd indx"/"corp indx"/
        # "govt indx"/"gvt indx"/"1-5 ind"/"1-5 idx" before this is reached.
        "ishares",
        # BL-RV-IN3 (2026-07-04): "dws esg dynamic opp"/"dws esg dyn
        # opport" (naming variants, same fund family) explicit include.
        # Confirmed equity-primary via KIID text ("invierte especialmente
        # en acciones...Como complemento a las acciones, el fondo invierte
        # en valores de renta fija") -- doesn't match any other RV include
        # pattern by name alone ("Dynamic Opportunities" is generic).
        "dws esg dynamic opp", "dws esg dyn opport",
        # BL-RV-IN4 (2026-07-05): "Thematics" is the Natixis equity-only thematic
        # sub-advisor brand (AI & Robotics, Subscription Economy, Safety, Climate,
        # Health, Water). All sub-funds are pure equity; bare token catches all
        # naming variants. KIID text is unusable for these (stored doc is the
        # Natixis SICAV annual report — SRRI=NULL for all 3 affected ISINs).
        "thematics",
    ]
    exclude_patterns = [
        "money", "monetary", "liquidity", "cash",
        "bond", "fixed income", "renta fija",
        "balanced", "allocation", "multi asset",
        "absolute return", "hedge", "alternative",
        # BL-RV-EX1: bond-index abbreviations used by iShares/Vanguard/PIMCO
        # ("bnd"=bond, "bd indx"=bond index, "corp indx"/"govt indx"=credit/govt index).
        # Without these, "climate" matches PIMCO CLIMATE BND and "global" matches
        # VGD GLOBAL BD INDX — both are bond funds, not equity.
        "bnd", "bd indx", "corp indx", "govt indx",
        # BL-RV-EX3 (2026-07-04): additional iShares short-duration/govt bond
        # abbreviation variants missed by BL-RV-EX1, confirmed orphaned after
        # real pipeline run (excluded here but not caught by rf_flexible's
        # matching patterns, since it used the same narrow set -- see
        # rf_flexible.py BL-RFF-IN2 for the paired include-side fix).
        # "1-5 ind" catches "1-5 indx"/"1-5 index"; "1-5 idx" catches the
        # "idx" spelling (ISHARES GBL 1-5 IDX); "gvt indx" catches the
        # abbreviated "gvt" spelling missed by "govt indx" (ISHARES EUR GVT
        # INDX, no "o").
        "1-5 ind", "1-5 idx", "gvt indx",
        # BL-RV-EX4 (2026-07-04): "high yield" (spelled out). Without this,
        # "global" matches "AB GLOBAL HIGH YIELD PORTFOLIO" -- a genuine
        # high-yield BOND fund ("invertirá principalmente en valores de
        # renta fija con calificación inferior a grado de inversión"), not
        # equity. Confirmed via KIID-text audit of the RENTA_VARIABLE block.
        "high yield", "high yiel",
        # BL-RV-EX6 (2026-07-04): "income"/"divers"-style bare includes also
        # match genuine bond-fund families whose name doesn't contain a
        # literal bond keyword. Confirmed via KIID text: PIMCO Diversified
        # Income ("invierte...principalmente en renta fija"), AB Mortgage
        # Income Portfolio (MBS/ABS specialist), Amundi Strategic Income
        # (Bloomberg US Universal Index, bond-income fund) -- all genuine
        # fixed-income funds that only match RV via bare "income"/"global".
        # "pimco diver" (no trailing "s") catches naming variants
        # "DIVERS"/"DIVER."/"DIVERSF" all seen in the master Excel.
        "pimco diver", "pimco esg income", "ab mort income", "amundi str income",
        # BL-RV-EX7 (2026-07-06): JPM Income funds are multi-asset income
        # funds — not equity. They are claimed by renta_variable via the bare
        # "income" and "global" include patterns, AND simultaneously by
        # rf_flexible and mixtos, firing INTER-13 with 3 different Nature
        # values every cycle. Two patterns needed: "jpm income" catches
        # "JPM INCOME x EUR ACC" (income immediately after jpm); "jpm global
        # income" catches "JPM GLOBAL INCOME x EUR ACC" (global+income, where
        # "global" alone would keep it in the universe). Excluding both reduces
        # triple-claim to dual-claim handled by INTER_DBLCLAIM. Safe: no JPM
        # equity fund uses "income" as its primary name token without a clearer
        # equity signal (they use "equity", "growth", "value" etc.).
        "jpm income", "jpm global income",
    ]
    # BL-RV-EX5 (2026-07-04): "convertible"/"allocation" truncations. Bare
    # "convertible"/"allocation" already generic but the master Excel often
    # truncates fund names ("JPM GLOBAL CONVER.(EUR)", "BGF GLOBAL
    # ALLOCA.F.HED.", "BGF GLOBAL ALLOCAT D2") — none contain the full word,
    # so a plain substring exclude misses them. Confirmed genuine non-equity
    # funds via KIID text: JPM Global Convertibles ("cartera diversificada
    # de valores convertibles"), BGF Global Allocation ("valores de renta
    # variable...Y valores de renta fija"). Prefix regex catches any
    # truncation depth.
    _exclude_prefix_patterns = [r'\bconver', r'\balloc']

    def is_candidate(name: str) -> bool:
        if not isinstance(name, str):
            return False
        n = name.lower()
        if any(p in n for p in exclude_patterns):
            return False
        # BL-RV-EX4b: abbreviated "HY" (e.g. "GS GLOBAL HY E ACC") needs a
        # word boundary -- bare "hy" would false-match unrelated words.
        if re.search(r'\bhy\b', n):
            return False
        if any(re.search(p, n) for p in _exclude_prefix_patterns):
            return False
        for p in include_patterns:
            if p == "shares":
                # BL-RV-EX2: "shares" is a substring of "ishares" (brand name).
                # Use word boundary so iShares bond index ETFs do not enter the
                # equity universe via this pattern.
                if re.search(r'\bshares\b', n):
                    return True
            else:
                if p in n:
                    return True
        return False

    mask = df_master["Fund_Name"].apply(is_candidate)
    return (
        df_master.loc[mask, "ISIN"]
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )


# =====================================================
# Geografía (v2 — EEUU antes de Europa, sin bare "eur")
# =====================================================


# =====================================================
# Clasificación semántica derivada (Renta Variable)
# v2: Profile, Style_Profile y Theme ampliados con restantes.
#     FIX-RV-1: "index" eliminado — solo patrones pasivos inequívocos.
#     FIX-RV-2: temáticos sin geo → Global.
# =====================================================

def classify_fund(
    fund_name: str,
    kiid_text: Optional[str],
) -> Dict[str, Optional[str]]:

    result = {
        "Fund_Nature": FUND_NATURE_VALUE,
        "Profile": None,
        "_signal_type": None,
        "Family": None,
        "Style_Profile": None,
        "Geography": None,
        "Theme": None,
        "Exposure_Bias": None,
        "_signal_subtype": None,
    }

    name_l = fund_name.lower() if isinstance(fund_name, str) else ""
    text_l = kiid_text.lower() if isinstance(kiid_text, str) else ""

    # -------------------------------------------------
    # Profile — v2: añadidos minimum vol / min vol,
    #   dividende / dividends (FR/EN variantes)
    # -------------------------------------------------
    if any(k in name_l for k in [
        "defensive", "low vol", "minimum volatility", "minimum vol", "min vol",
    ]):
        result["Profile"] = "Conservador"
    elif any(k in name_l for k in [
        "income", "dividend", "dividende", "dividends",
    ]):
        result["Profile"] = "Moderado"
    else:
        result["Profile"] = "Dinámico"   # default RV

    # -------------------------------------------------
    # Type / Subtype — FIX-RV-1: solo patrones pasivos inequívocos
    # (eliminado "index" bare — aparece en KIIDs activos con benchmark)
    # -------------------------------------------------
    _passive_kws = [
        "gestión pasiva", "gestiona de forma pasiva", "gestiona de manera pasiva",
        "inversión pasiva", "replica el índice", "replicar el índice",
        "replicar la rentabilidad del índice", "replicación del índice",
        "seguimiento del índice", "index fund", "track the index",
        "replicate the index", "index tracking", "passively managed",
        "passive management",
    ]
    if any(k in text_l for k in _passive_kws):
        result["_signal_type"] = TYPE_INDEX_FUND
        result["_signal_subtype"] = SUBTYPE_INDEX_FUND

    if "etf" in text_l or "fondo cotizado" in text_l:
        result["_signal_type"] = TYPE_INDEX_FUND
        result["_signal_subtype"] = SUBTYPE_ETF

    if result["_signal_type"] is None:
        result["_signal_type"] = TYPE_ACTIVE_MANAGEMENT

    # -------------------------------------------------
    # Style_Profile / Exposure_Bias se derivan de forma centralizada en
    # classify_utils.derive_v20_attributes (engine = fuente única, AUDIT v20).

    # -------------------------------------------------
    # Family / Theme — v2: thematic_map ampliado con restantes
    #   añadidos: biotec/biotech, wellcare, digital, robotics/robotech,
    #   water, silver age/silverplus, insurance, global brands, sri
    # -------------------------------------------------
    _theme = _detect_theme(name_l)
    if _theme:
        result["Family"] = FAMILY_THEMATIC_EQUITY
        result["Theme"] = _theme

    if result["Family"] is None:
        result["Family"] = FAMILY_EQUITY_CORE

    # -------------------------------------------------
    # Geography — FIX-RV-2: temáticos sin geo → Global
    # -------------------------------------------------
    result["Geography"] = _detect_geography(name_l)
    if result["Geography"] is None and result["Theme"] is not None:
        result["Geography"] = "Global"

    # ── Enriquecimiento desde KIID (ventana adaptativa DDF/KIID) ────────
    # Rellena Type, Style_Profile, Geography, Is_ESG, Ongoing_Charge
    # cuando el bloque primario no pudo determinarlos por nombre.
    _kiid_attrs = detect_kiid_attributes(
        kiid_text or "", 
        "Renta Variable",
        result,
    )
    for _k, _v in _kiid_attrs.items():
        if not result.get(_k):
            result[_k] = _v

    # ── Atributos universales canonico v2 ──────────────────────────
    # Se aplican tras la logica especifica del bloque.
    # El bloque puede haber asignado ya algunos — or None los respeta.
    _name_l = fund_name.lower() if isinstance(fund_name, str) else ""
    _text_l = kiid_text.lower() if isinstance(kiid_text, str) else ""
    _srri_m = re.search(r"\b([1-7])\s*/\s*7\b", _text_l)
    _srri   = int(_srri_m.group(1)) if _srri_m else None

    if result.get("Profile") is None:
        result["Profile"] = _detect_profile_from_srri(_srri)
    result["Geography"]    = result.get("Geography") or _detect_geography(_name_l)
    result["Theme"]        = result.get("Theme")     or _detect_theme(_name_l)
    result["Is_ESG"]       = _detect_is_esg(fund_name)
    result["Strategy"] = _detect_strategy(
        None, result.get("_signal_subtype"), _name_l
    )
    result["Benchmark_Type"] = _detect_benchmark_type(
        None, None
    )

    return apply_semantic_validation(result, fund_name)

