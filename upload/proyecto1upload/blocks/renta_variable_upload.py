from typing import Optional, Dict, List
from core.classify_utils import (
    NAME_SIGNALS_RV,
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
    ]
    exclude_patterns = [
        "money", "monetary", "liquidity", "cash",
        "bond", "fixed income", "renta fija",
        "balanced", "allocation", "multi asset",
        "absolute return", "hedge", "alternative",
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
        result["Type"] = "Indexado"
        result["Subtype"] = "Fondo Indexado"

    if "etf" in text_l or "fondo cotizado" in text_l:
        result["Type"] = "Indexado"
        result["Subtype"] = "ETF"

    if result["Type"] is None:
        result["Type"] = "Gestión Activa"

    # -------------------------------------------------
    # Style_Profile / Exposure_Bias — v2: añadidos de restantes
    #   min vol / min volatil, dividende, wachstum (DE), crecim (ES)
    # -------------------------------------------------
    if any(k in name_l for k in [
        "low vol", "minimum volatility", "minimum vol", "min vol", "min volatil",
    ]):
        result["Style_Profile"] = "Low Volatility"
        result["Exposure_Bias"] = "Low Volatility Bias"
    elif any(k in name_l for k in ["income", "dividend", "dividende", "dividends"]):
        result["Style_Profile"] = "Income"
        result["Exposure_Bias"] = "Income Bias"
    elif "quality" in name_l:
        result["Style_Profile"] = "Quality"
    elif any(k in name_l for k in ["growth", "wachstum", "crecim"]):
        result["Style_Profile"] = "Growth"
    elif "value" in name_l:
        result["Style_Profile"] = "Value"
    elif any(k in name_l for k in [
        "defensive", "risk control", "risk managed", "capital preservation",
    ]):
        result["Style_Profile"] = "Defensivo"

    # -------------------------------------------------
    # Family / Theme — v2: thematic_map ampliado con restantes
    #   añadidos: biotec/biotech, wellcare, digital, robotics/robotech,
    #   water, silver age/silverplus, insurance, global brands, sri
    # -------------------------------------------------
    _theme = _detect_theme(name_l)
    if _theme:
        result["Family"] = "RV Temática"
        result["Theme"] = _theme

    if result["Family"] is None:
        result["Family"] = "RV Core"

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
    if result.get("Style_Profile") == "Defensivo":
        result["Style_Profile"] = None   # Defensivo → Profile, no Style_Profile
    if result.get("Style_Profile") is None:
        result["Style_Profile"] = _detect_style_profile(_name_l)
    if result.get("Exposure_Bias") is None:
        result["Exposure_Bias"] = _detect_exposure_bias(_name_l, "Renta Variable")
    result["Strategy"] = _detect_strategy(
        None, result.get("Subtype"), _name_l
    )
    result["Benchmark_Type"] = _detect_benchmark_type(
        None, None
    )

    return result
