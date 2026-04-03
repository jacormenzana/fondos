from typing import Optional, Dict, List
from core.classify_utils import (
    NAME_SIGNALS_MONETARIO,
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


BLOCK_NAME = "monetarios"
FUND_NATURE_VALUE = "Monetario"


# =====================================================
# Universo del bloque
# =====================================================

def get_universe_isins(df_master) -> List[str]:
    include_patterns = [
        "money market", "monetary", "liquidity", "liquid",
        "cash fund", "cash management", "treasury",
        "tresorerie", "ucits mmf", "mmf",
    ]
    exclude_patterns = [
        "short duration", "ultra short", "short term",
        "bond", "income", "enhanced", "plus",
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
# Geografía (v2 — EEUU antes de Europa para evitar
# falso-positivo por sufijo de clase 'EUR')
# =====================================================


# =====================================================
# Clasificación semántica derivada (Monetarios)
# v2: Type y Family alineados con restantes._classify_as_monetario
# =====================================================

def classify_fund(
    fund_name: str,
    kiid_text: Optional[str],
) -> Dict[str, Optional[str]]:

    result = {
        "Fund_Nature": FUND_NATURE_VALUE,
        "Profile": "Conservador",
        "Type": None,
        "Family": None,
        "Style_Profile": "Defensivo",
        "Geography": None,
        "Theme": None,
        "Exposure_Bias": "Liquidity Bias",
        "Subtype": None,
    }

    name_l = fund_name.lower() if isinstance(fund_name, str) else ""

    # -------------------------------------------------
    # Type — v2: añadidas abreviaturas de restantes
    # gov liq / gov prim (BlackRock ICS), crd / corp (fondos privados abreviados)
    # -------------------------------------------------
    if any(k in name_l for k in [
        "government", "treasury", "sovereign", "gov liq", "gov prim", "public",
    ]):
        result["Type"] = "Monetario Público"
    elif any(k in name_l for k in [
        "prime", "corporate", "credit", "crd", "corp",
    ]):
        result["Type"] = "Monetario Privado"
    else:
        result["Type"] = "Monetario"

    # -------------------------------------------------
    # Family — v2: añadidos vnav (JPM VNAV), plus / rend (Amundi Rendement+)
    # -------------------------------------------------
    if "cnav" in name_l:
        result["Family"] = "CNAV"
    elif "lvnav" in name_l:
        result["Family"] = "LVNAV"
    elif "vnav" in name_l:
        result["Family"] = "VNAV"
    elif any(k in name_l for k in ["enhanced", "plus", "rend"]):
        result["Family"] = "Enhanced Cash"
    else:
        result["Family"] = "Monetario"

    # -------------------------------------------------
    # Geography
    # -------------------------------------------------
    result["Geography"] = _detect_geography(name_l)

    # ── Enriquecimiento desde KIID (ventana 1200-4500) ─────────────────
    # Rellena Type, Style_Profile, Geography, Is_ESG, Ongoing_Charge
    # cuando el bloque primario no pudo determinarlos por nombre.
    _kiid_attrs = detect_kiid_attributes(
        kiid_text or "", 
        "Monetario",
        result,
    )
    for _k, _v in _kiid_attrs.items():
        if not result.get(_k):
            result[_k] = _v

    # Fix C: si Family es "Monetario" (default por nombre), intentar
    # derivarlo del Type detectado desde el texto KIID
    # Type CNAV/LVNAV/VNAV → Family igual (son equivalentes en monetarios)
    if result.get("Family") == "Monetario":
        _type_from_kiid = result.get("Type")
        if _type_from_kiid in ("CNAV", "LVNAV", "VNAV", "Enhanced Cash"):
            result["Family"] = _type_from_kiid

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
        result["Exposure_Bias"] = _detect_exposure_bias(_name_l, "Monetario")
    result["Strategy"] = _detect_strategy(
        None, result.get("Subtype"), _name_l
    )
    result["Benchmark_Type"] = _detect_benchmark_type(
        None, None
    )

    return result
