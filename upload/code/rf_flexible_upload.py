from typing import Optional, Dict, List
from core.classify_utils import (
    NAME_SIGNALS_RF_FLEXIBLE,
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


BLOCK_NAME = "rf_flexible"
FUND_NATURE_VALUE = "Renta Fija Flexible"


# =====================================================
# Universo del bloque
# =====================================================

def get_universe_isins(df_master) -> List[str]:
    include_patterns = [
        "flexible bond", "dynamic bond", "strategic bond",
        "total return bond", "total return", "unconstrained",
        "absolute return bond", "multi sector bond", "multisector bond",
        "opportunistic bond", "global bond", "income bond",
        "tactical bond", "active bond",
    ]
    exclude_patterns = [
        "money", "monetary", "liquidity", "cash",
        "short duration", "ultra short", "short term", "low duration",
        "floating rate", "equity", "balanced", "allocation",
        "multi asset", "multi-asset",
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
# Clasificación semántica derivada (RF Flexible)
# v2: Profile, Type, Style_Profile y Family alineados con
#     restantes._classify_as_rf_flexible
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

    # -------------------------------------------------
    # Profile — v2: Conservador con keywords de restantes
    #   defensiv / conserv / securite / sécurité → Conservador
    #   (Carmignac Sécurité, fondos defensivos ES/FR)
    #   dinamic → Dinámico (abreviatura castellana en nombres)
    # -------------------------------------------------
    if any(k in name_l for k in ["high yield", "opportunistic", "dynamic", "dinamic"]):
        result["Profile"] = "Dinámico"
    elif any(k in name_l for k in ["defensiv", "conserv", "securite", "sécurité"]):
        result["Profile"] = "Conservador"
    else:
        result["Profile"] = "Moderado"

    # -------------------------------------------------
    # Type — v2: añadidos de restantes
    #   abs ret / absret → Absolute Return
    #   tot ret → Total Return
    #   millesima / millesim / buy&watch / target 20 / credit 20 → Target Maturity
    # -------------------------------------------------
    if "unconstrained" in name_l:
        result["Type"] = "Unconstrained"
        result["Subtype"] = "Flexible Bond"
    elif any(k in name_l for k in ["absolute return", "abs ret", "absret"]):
        result["Type"] = "Absolute Return"
    elif any(k in name_l for k in ["total return", "tot ret"]):
        result["Type"] = "Total Return"
    elif any(k in name_l for k in [
        "millesima", "millesim", "milles select",
        "buy&watch", "buy & watch", "buywat",
        "target 20", "credit 20", "cred 20",
    ]):
        result["Type"] = "Target Maturity"
    else:
        result["Type"] = "Renta Fija Flexible"

    # -------------------------------------------------
    # Style_Profile / Exposure_Bias — v2: añadidos de restantes
    #   Credit Bias: + "hy", "opportunist", "credit opport"
    #   Income Bias: + "rend", "rendement" (FR fondos)
    #   Low Volatility: + "securite"
    # -------------------------------------------------
    if any(k in name_l for k in [
        "high yield", "hy", "opportunistic", "opportunist",
        "credit opport", "yield enhancement",
    ]):
        result["Style_Profile"] = "Income"
        result["Exposure_Bias"] = "Credit Bias"
        result["Subtype"] = "Opportunistic"

    elif any(k in name_l for k in ["income", "rend", "rendement"]):
        result["Style_Profile"] = "Income"
        result["Exposure_Bias"] = "Income Bias"

    elif any(k in name_l for k in [
        "low volatility", "low vol", "capital preservation", "defensiv", "securite",
    ]):
        result["Style_Profile"] = "Low Volatility"
        result["Exposure_Bias"] = "Low Volatility Bias"

    else:
        result["Style_Profile"] = "Defensivo"
        result["Exposure_Bias"] = "Duration Bias"

    # -------------------------------------------------
    # Family — v2: familias granulares de restantes
    #   RF High Yield / RF Emergentes / RF Inflación (con Theme)
    # -------------------------------------------------
    if any(k in name_l for k in ["strategic", "tactical", "opportunist", "millesima"]):
        result["Family"] = "Flexible Estratégico"
    elif any(k in name_l for k in ["high yield", " hy ", " hy bd"]):
        result["Family"] = "RF High Yield"
    elif any(k in name_l for k in ["emerging", "em debt", "em mkt", "emerg mkt", "emergentes", "em bond"]):
        result["Family"] = "RF Emergentes"
    elif any(k in name_l for k in ["inflation", "inflat", "infl link"]):
        result["Family"] = "RF Inflación"
        result["Theme"] = "Inflación"
    else:
        result["Family"] = "Renta Fija Flexible"

    # -------------------------------------------------
    # Subtype adicional (divisa)
    # -------------------------------------------------
    if any(k in name_l for k in ["multicurrency", "multi-currency", "multidivisa"]):
        result["Subtype"] = (
            f"{result['Subtype']} | Multi-Currency" if result["Subtype"]
            else "Multi-Currency"
        )

    # -------------------------------------------------
    # Geography
    # -------------------------------------------------
    result["Geography"] = _detect_geography(name_l)

    # ── Enriquecimiento desde KIID (ventana adaptativa DDF/KIID) ────────
    # Rellena Type, Style_Profile, Geography, Is_ESG, Ongoing_Charge
    # cuando el bloque primario no pudo determinarlos por nombre.
    _kiid_attrs = detect_kiid_attributes(
        kiid_text or "", 
        "Renta Fija Flexible",
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
        result["Exposure_Bias"] = _detect_exposure_bias(_name_l, "Renta Fija Flexible")
    result["Strategy"] = _detect_strategy(
        None, result.get("Subtype"), _name_l
    )
    result["Benchmark_Type"] = _detect_benchmark_type(
        None, None
    )

    return result
