# mixtos.py — v6 (2026-05-09): BL-LANG-EN — FAMILY_MULTI_ASSET importada, literal "Mixtos" eliminado
from typing import Optional, Dict, List
from core.classify_utils import (
    NAME_SIGNALS_MIXTO,
    FAMILY_INCOME_ORIENTED,   # BL-65b: constante EN canónica
    FAMILY_MULTI_ASSET,       # BL-LANG-EN
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


BLOCK_NAME = "mixtos"
FUND_NATURE_VALUE = "Mixtos"


# =====================================================
# Universo del bloque
# =====================================================

def get_universe_isins(df_master) -> List[str]:
    pattern = re.compile(
        r"""
        balanced|
        multi[\s-]?asset|
        allocation|
        diversified|
        total\s+return|
        conservative|
        moderate|
        growth|
        dynamic|
        target\s+volatility|
        target\s+outcome|
        risk\s+control|
        income
        """,
        re.IGNORECASE | re.VERBOSE,
    )
    mask = df_master["Fund_Name"].astype(str).str.contains(pattern, regex=True)
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
# Clasificación semántica derivada (Mixtos)
# v2: Profile y Type alineados con restantes._classify_as_mixtos
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

    # -------------------------------------------------
    # Profile — v2: añadidos keywords de restantes
    #   español: "conservador", "def " (GS Patrimonial DEF)
    #   abrev UBS Strategy: "blced" (balanced), "equilib" (equilibrio)
    #   dinámico: "dinamic" (ES), "agresiv", "crecimiento"
    #   "yield p" (UBS Strategy Yield Plus → Moderado)
    # -------------------------------------------------
    if any(k in name_l for k in [
        "conservative", "defensive", "defensiv", "conservador", "def ",
    ]):
        result["Profile"] = "Conservador"
    elif any(k in name_l for k in [
        "balanced", "moderate", "moderado", "blced", "equilib", "yield p",
    ]):
        result["Profile"] = "Moderado"
    elif any(k in name_l for k in [
        "growth", "dynamic", "dinamic", "agresiv", "crecimiento",
    ]):
        result["Profile"] = "Dinámico"
    else:
        result["Profile"] = "Moderado"   # default razonable en mixtos

    # -------------------------------------------------
    # Type — v2: añadidos de restantes
    #   "volatility control" (variante de target volatility)
    #   "macro" → Tactical Allocation
    # -------------------------------------------------
    if any(k in name_l for k in [
        "target volatility", "risk controlled", "risk control", "volatility control",
    ]):
        result["_signal_type"] = "Target Volatility"
    elif any(k in name_l for k in ["target outcome", "outcome"]):
        result["_signal_type"] = "Target Outcome"
    elif any(k in name_l for k in ["tactical", "macro", "strategy"]):
        result["_signal_type"] = "Tactical Allocation"
    else:
        result["_signal_type"] = "Allocation"

    # -------------------------------------------------
    # Family
    # -------------------------------------------------
    if any(k in name_l for k in ["lifecycle", "life cycle", "target date"]):
        result["Family"] = "Lifecycle"
    elif "retirement" in name_l:
        result["Family"] = "Retirement"
    elif "income" in name_l:
        result["Family"] = FAMILY_INCOME_ORIENTED   # BL-65b: constante EN
    else:
        result["Family"] = FAMILY_MULTI_ASSET

    # Style_Profile se deriva de forma centralizada en
    # classify_utils.derive_v20_attributes (Mixtos → 'Strategic Allocation').

    # -------------------------------------------------
    # Geography
    # -------------------------------------------------
    result["Geography"] = _detect_geography(name_l)

    # ── Enriquecimiento desde KIID (ventana adaptativa DDF/KIID) ────────
    # Rellena Type, Style_Profile, Geography, Is_ESG, Ongoing_Charge
    # cuando el bloque primario no pudo determinarlos por nombre.
    _kiid_attrs = detect_kiid_attributes(
        kiid_text or "", 
        "Mixtos",
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
