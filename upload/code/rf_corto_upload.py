from typing import Optional, Dict, List
from core.classify_utils import (
    NAME_SIGNALS_RF_CORTO,
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


BLOCK_NAME = "rf_corto"
FUND_NATURE_VALUE = "Renta Fija Corto Plazo"


# =====================================================
# Universo del bloque
# =====================================================

def get_universe_isins(df_master) -> List[str]:
    include_patterns = [
        "short duration", "ultra short", "short term bond", "short term",
        "low duration", "floating rate", "floating", "money plus", "enhanced cash",
    ]
    exclude_patterns = [
        "money market", "monetary", "liquidity", "cash ",
        "equity", "balanced", "allocation", "multi asset", "multi-asset",
        "absolute return",
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
# Clasificación semántica derivada (RF Corto Plazo)
# v2: Type y Subtype alineados con restantes._classify_as_rf_corto
# =====================================================

def classify_fund(
    fund_name: str,
    kiid_text: Optional[str],
) -> Dict[str, Optional[str]]:

    result = {
        "Fund_Nature": FUND_NATURE_VALUE,
        "Profile": "Conservador",
        "Type": None,
        "Family": "Renta Fija Corto Plazo",
        "Style_Profile": "Defensivo",
        "Geography": None,
        "Theme": None,
        "Exposure_Bias": "Duration Bias",
        "Subtype": None,
    }

    name_l = fund_name.lower() if isinstance(fund_name, str) else ""

    # -------------------------------------------------
    # Profile
    # -------------------------------------------------
    if any(k in name_l for k in ["ultra short", "low duration", "short duration"]):
        result["Profile"] = "Conservador"
    elif any(k in name_l for k in ["enhanced cash", "money plus", "income"]):
        result["Profile"] = "Moderado"
    else:
        result["Profile"] = "Conservador"

    # -------------------------------------------------
    # Type — v2: añadidas abreviaturas de restantes
    #   floating: " frn " con espacios (más preciso que bare "frn")
    #   government: "gov bd", "gov bond" (BlackRock abrev)
    #   credit: "corp","crd","crdt","covered","pfandbrief" (Covered bonds DWS/EDR)
    # -------------------------------------------------
    if any(k in name_l for k in [
        "floating rate", "floating", "floater", " frn ",
    ]):
        result["Type"] = "Floating Rate CP"
        result["Subtype"] = "Floating Rate Notes"
        result["Exposure_Bias"] = "Rate Reset Bias"

    elif any(k in name_l for k in [
        "government", "treasury", "sovereign", "gov bd", "gov bond",
    ]):
        result["Type"] = "Deuda Pública CP"

    elif any(k in name_l for k in [
        "corporate", "credit", "corp", "crd", "crdt", "covered", "pfandbrief",
    ]):
        result["Type"] = "Crédito CP"

    else:
        result["Type"] = "Renta Fija Corto Plazo"

    # -------------------------------------------------
    # Subtype low duration — v2: añadidos "ult sh","ul sh","0-1","0-2","0-3"
    # (Amundi UL SH TR BD, DWS 0-2 Duration)
    # -------------------------------------------------
    if any(k in name_l for k in [
        "ultra short", "ult sh", "ul sh", "low dur", "low duration",
        "0-1", "0-2", "0-3",
    ]):
        result["Subtype"] = "Low Duration"
        result["Exposure_Bias"] = "Duration Bias"
        result["Style_Profile"] = "Defensivo"

    # Guardrail: High Yield no permitido en RF Corto
    if "high yield" in name_l or "high-yield" in name_l:
        result["Type"] = None
        result["Subtype"] = None
        result["Exposure_Bias"] = None

    # -------------------------------------------------
    # Style_Profile / Exposure_Bias
    # -------------------------------------------------
    if any(k in name_l for k in ["income", "enhanced cash", "money plus"]):
        result["Style_Profile"] = "Income"
        if result["Exposure_Bias"] is None:
            result["Exposure_Bias"] = "Income Bias"
    elif not result["Exposure_Bias"]:
        result["Style_Profile"] = "Defensivo"
        result["Exposure_Bias"] = "Duration Bias"

    # -------------------------------------------------
    # Geography
    # -------------------------------------------------
    result["Geography"] = _detect_geography(name_l)

    # ── Enriquecimiento desde KIID (ventana adaptativa DDF/KIID) ────────
    # Rellena Type, Style_Profile, Geography, Is_ESG, Ongoing_Charge
    # cuando el bloque primario no pudo determinarlos por nombre.
    _kiid_attrs = detect_kiid_attributes(
        kiid_text or "", 
        "Renta Fija Corto Plazo",
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
        result["Exposure_Bias"] = _detect_exposure_bias(_name_l, "Renta Fija Corto Plazo")
    result["Strategy"] = _detect_strategy(
        None, result.get("Subtype"), _name_l
    )
    result["Benchmark_Type"] = _detect_benchmark_type(
        None, None
    )

    return result
