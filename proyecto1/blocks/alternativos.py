from typing import Optional, Dict, List
from core.classify_utils import (
    NAME_SIGNALS_ALTERNATIVO,
    FAMILY_ABSOLUTE_RETURN,
    FAMILY_REAL_ASSETS,
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


BLOCK_NAME = "alternativos"
FUND_NATURE_VALUE = "Alternativo"


# =====================================================
# Universo del bloque
# =====================================================

def get_universe_isins(df_master) -> List[str]:
    include_patterns = [
        "absolute return", "hedge", "long short", "long/short",
        "market neutral", "relative value", "arbitrage", "global macro",
        "managed futures", "cta", "systematic", "multi strategy",
        "multi-strategy", "alternative", "real assets", "real estate",
        "property", "infrastructure", "commodities", "commodity",
        "gold", "precious metals",
    ]
    exclude_patterns = [
        "equity", "bond", "fixed income", "renta fija",
        "balanced", "allocation", "multi asset", "multi-asset",
    ]

    def is_candidate(name: str) -> bool:
        if not isinstance(name, str):
            return False
        n = name.lower()
        if any(p in n for p in exclude_patterns):
            return False
        return any(p in n for p in include_patterns)

    mask = df_master["Fund_Name"].apply(is_candidate)
    return (
        df_master.loc[mask, "ISIN"]
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )


# =====================================================
# Geografía (v2 — EEUU antes de Europa)
# =====================================================


# =====================================================
# Clasificación semántica derivada (Alternativos)
# v2: Subtype/Type ampliados con restantes._classify_as_alternativo.
#     Se mantienen Real Assets y Commodities del original (superiores
#     en esas categorías vs restantes que no las diferencia).
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
    # Type / Subtype (_signal_*) — Style_Profile y Exposure_Bias se derivan de
    # forma centralizada en classify_utils.derive_v20_attributes (engine, AUDIT v20).
    # -------------------------------------------------
    if any(k in name_l for k in [
        "relative value", "arbitrage", "arbit", "arb strat", "arb str",
    ]):
        result["_signal_type"] = "Absolute Return"
        result["_signal_subtype"] = "Relative Value / Arbitrage"

    elif "market neutral" in name_l:
        result["_signal_type"] = "Absolute Return"
        result["_signal_subtype"] = "Market Neutral"

    elif any(k in name_l for k in ["long short", "long/short", "long-short"]):
        result["_signal_type"] = "Absolute Return"
        result["_signal_subtype"] = "Long/Short"

    elif any(k in name_l for k in ["global rates", "gl rates"]):
        result["_signal_type"] = "Absolute Return"
        result["_signal_subtype"] = "Global Rates"

    elif any(k in name_l for k in ["multi strategy", "multi-strategy", "multiassut"]):
        result["_signal_type"] = "Absolute Return"
        result["_signal_subtype"] = "Multi-Asset"

    elif "global macro" in name_l or "adagio" in name_l:
        result["_signal_type"] = "Absolute Return"
        result["_signal_subtype"] = "Global Macro"

    elif any(k in name_l for k in [
        "managed futures", "cta", "systematic",
    ]):
        result["_signal_type"] = "Systematic"
        result["_signal_subtype"] = "Managed Futures"

    elif any(k in name_l for k in ["real estate", "property"]):
        result["_signal_type"] = "Real Assets"
        result["_signal_subtype"] = "Real Estate"

    elif "infrastructure" in name_l:
        result["_signal_type"] = "Real Assets"
        result["_signal_subtype"] = "Infrastructure"

    elif any(k in name_l for k in [
        "commodities", "commodity", "gold", "precious metals",
    ]):
        result["_signal_type"] = "Commodities"
        result["_signal_subtype"] = "Physical / Derivatives"

    elif any(k in name_l for k in ["abs ret", "absret", "st absret", "absolute return"]):
        result["_signal_type"] = "Absolute Return"
        result["_signal_subtype"] = "Total Return Bond"

    else:
        result["_signal_type"] = "Absolute Return"

    # -------------------------------------------------
    # Family — v2: "Retorno Absoluto" para todos los AR (alineado
    # con restantes). Se mantienen familias especiales del original
    # para Real Assets y Commodities.
    # -------------------------------------------------
    if result["_signal_type"] == "Systematic":
        result["Family"] = "Systematic"
    elif result["_signal_type"] == "Real Assets":
        result["Family"] = FAMILY_REAL_ASSETS
    elif result["_signal_type"] == "Commodities":
        result["Family"] = FAMILY_REAL_ASSETS
    else:
        result["Family"] = FAMILY_ABSOLUTE_RETURN

    # -------------------------------------------------
    # Theme (explícito)
    # -------------------------------------------------
    if "gold" in name_l:
        result["Theme"] = "Gold"
    elif "infrastructure" in name_l:
        result["Theme"] = "Infrastructure"

    # -------------------------------------------------
    # Profile — basado en SRRI extraído del texto y en Type/Subtype
    # Regla:
    #   Commodities / Physical Derivatives  → Dinámico
    #   SRRI >= 5                           → Dinámico
    #   Real Assets                         → Moderado
    #   SRRI IN (3, 4)                      → Moderado
    #   SRRI <= 2                           → Conservador
    #   Default (sin SRRI)                  → Moderado
    # -------------------------------------------------
    _srri = None
    if kiid_text:
        _m = re.search(r'\b([1-7])\s*/\s*7\b', text_l)
        _srri = int(_m.group(1)) if _m else None

    _t = result.get("_signal_type", "") or ""
    _s = result.get("_signal_subtype", "") or ""

    if _t == "Commodities" or _s.startswith("Physical"):
        result["Profile"] = "Dinámico"
    elif _srri and _srri >= 5:
        result["Profile"] = "Dinámico"
    elif _t == "Real Assets":
        result["Profile"] = "Moderado"
    elif _srri and _srri in (3, 4):
        result["Profile"] = "Moderado"
    elif _srri and _srri <= 2:
        result["Profile"] = "Conservador"
    else:
        result["Profile"] = "Moderado"  # default razonable para AR

    # -------------------------------------------------
    # Geography
    # -------------------------------------------------
    result["Geography"] = _detect_geography(name_l)

    # ── Enriquecimiento desde KIID (ventana adaptativa DDF/KIID) ────────
    # Rellena Type, Style_Profile, Geography, Is_ESG, Ongoing_Charge
    # cuando el bloque primario no pudo determinarlos por nombre.
    _kiid_attrs = detect_kiid_attributes(
        kiid_text or "", 
        "Alternativo",
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

