from typing import Optional, Dict, List
from core.classify_utils import (
    NAME_SIGNALS_ALTERNATIVO,
    detect_geography       as _detect_geography,
    detect_theme           as _detect_theme,
    detect_is_esg          as _detect_is_esg,
    detect_style_profile   as _detect_style_profile,
    detect_exposure_bias   as _detect_exposure_bias,
    detect_strategy        as _detect_strategy,
    detect_benchmark_type  as _detect_benchmark_type,
    detect_profile_from_srri as _detect_profile_from_srri,
    detect_kiid_attributes,
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
        "Type": None,
        "Family": None,
        "Style_Profile": None,
        "Geography": None,
        "Theme": None,
        "Exposure_Bias": None,
        "Subtype": None,
    }

    name_l = fund_name.lower() if isinstance(fund_name, str) else ""
    text_l = kiid_text.lower() if isinstance(kiid_text, str) else ""

    # -------------------------------------------------
    # Type / Subtype / Style_Profile — v2: alineado con restantes
    #   Arbitrage: + "arbit", "arb strat", "arb str" (Lyxor ARB STRAT, Candriam ARBI)
    #   Long/Short: + "long-short" (guión)
    #   Nuevos subtipos de restantes:
    #     "global rates" / "gl rates" → Global Rates (GAM Star)
    #     "adagio" → Global Macro (H2O Adagio)
    #     "abs ret" / "absret" / "st absret" → Total Return Bond (Jupiter)
    # -------------------------------------------------
    if any(k in name_l for k in [
        "relative value", "arbitrage", "arbit", "arb strat", "arb str",
    ]):
        result["Type"] = "Absolute Return"
        result["Subtype"] = "Relative Value / Arbitrage"
        result["Style_Profile"] = "Defensivo"

    elif "market neutral" in name_l:
        result["Type"] = "Absolute Return"
        result["Subtype"] = "Market Neutral"
        result["Style_Profile"] = "Defensivo"

    elif any(k in name_l for k in ["long short", "long/short", "long-short"]):
        result["Type"] = "Absolute Return"
        result["Subtype"] = "Long/Short"
        result["Style_Profile"] = "Defensivo"

    elif any(k in name_l for k in ["global rates", "gl rates"]):
        result["Type"] = "Absolute Return"
        result["Subtype"] = "Global Rates"
        result["Style_Profile"] = "Defensivo"

    elif any(k in name_l for k in ["multi strategy", "multi-strategy", "multiassut"]):
        result["Type"] = "Absolute Return"
        result["Subtype"] = "Multi-Asset"
        result["Style_Profile"] = "Defensivo"

    elif "global macro" in name_l or "adagio" in name_l:
        result["Type"] = "Absolute Return"
        result["Subtype"] = "Global Macro"
        result["Style_Profile"] = "Momentum"

    elif any(k in name_l for k in [
        "managed futures", "cta", "systematic",
    ]):
        result["Type"] = "Systematic"
        result["Subtype"] = "Managed Futures"
        result["Style_Profile"] = "Momentum"

    elif any(k in name_l for k in ["real estate", "property"]):
        result["Type"] = "Real Assets"
        result["Subtype"] = "Real Estate"
        result["Style_Profile"] = "Defensivo"
        result["Exposure_Bias"] = "Real Estate Bias"

    elif "infrastructure" in name_l:
        result["Type"] = "Real Assets"
        result["Subtype"] = "Infrastructure"
        result["Style_Profile"] = "Defensivo"
        result["Exposure_Bias"] = "Infrastructure Bias"

    elif any(k in name_l for k in [
        "commodities", "commodity", "gold", "precious metals",
    ]):
        result["Type"] = "Commodities"
        result["Subtype"] = "Physical / Derivatives"
        result["Exposure_Bias"] = "Commodity Bias"

    elif any(k in name_l for k in ["abs ret", "absret", "st absret", "absolute return"]):
        result["Type"] = "Absolute Return"
        result["Subtype"] = "Total Return Bond"
        result["Style_Profile"] = "Defensivo"

    else:
        result["Type"] = "Absolute Return"
        result["Style_Profile"] = "Defensivo"

    # -------------------------------------------------
    # Family — v2: "Retorno Absoluto" para todos los AR (alineado
    # con restantes). Se mantienen familias especiales del original
    # para Real Assets y Commodities.
    # -------------------------------------------------
    if result["Type"] == "Systematic":
        result["Family"] = "Systematic"
    elif result["Type"] == "Real Assets":
        result["Family"] = "Activos Reales"
    elif result["Type"] == "Commodities":
        result["Family"] = "Activos Reales"
    else:
        result["Family"] = "Retorno Absoluto"

    # Exposure_Bias default para AR
    if result["Exposure_Bias"] is None and result["Type"] == "Absolute Return":
        result["Exposure_Bias"] = "Absolute Return Bias"

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

    _t = result.get("Type", "") or ""
    _s = result.get("Subtype", "") or ""

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
    if result.get("Style_Profile") == "Defensivo":
        result["Style_Profile"] = None   # Defensivo → Profile, no Style_Profile
    if result.get("Style_Profile") is None:
        result["Style_Profile"] = _detect_style_profile(_name_l)
    if result.get("Exposure_Bias") is None:
        result["Exposure_Bias"] = _detect_exposure_bias(_name_l, "Alternativo")
    result["Strategy"] = _detect_strategy(
        None, result.get("Subtype"), _name_l
    )
    result["Benchmark_Type"] = _detect_benchmark_type(
        None, None
    )

    return result
