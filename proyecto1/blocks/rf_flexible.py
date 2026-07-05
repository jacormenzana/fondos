from typing import Optional, Dict, List
from core.classify_utils import (
    NAME_SIGNALS_RF_FLEXIBLE,
    FAMILY_FLEXIBLE_FI,
    FAMILY_HIGH_YIELD,
    FAMILY_EMERGING_DEBT,
    FAMILY_INFLATION_LINKED,
    FAMILY_STRATEGIC_ALLOCATION,
    TYPE_FLEXIBLE_FI,
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
        # BL-RFF-IN1: bond-index abbreviations (iShares/Vanguard/PIMCO naming).
        # "bnd" and "bd indx" are bond abbreviations; "corp indx"/"govt indx"
        # are corporate/government bond index trackers.
        # These were previously captured by renta_variable due to "shares"→"ishares",
        # "climate"→PIMCO BND, and "global"→VGD BD INDX false positives (BL-RV-EX1/EX2).
        "bnd", "bd indx", "corp indx", "govt indx",
        # BL-RFF-IN2 (2026-07-04): paired with renta_variable's BL-RV-EX3.
        # Without these, funds excluded from RV by BL-RV-EX3 become orphaned
        # (claimed by no block, stale Fund_Nature persists) -- confirmed via
        # real pipeline run for 7 iShares "1-5 INDX/IDX"/"GVT INDX" funds.
        "1-5 ind", "1-5 idx", "gvt indx",
        # BL-RFF-IN3 (2026-07-04): paired with renta_variable's BL-RV-EX4.
        # Without "high yield" here, funds excluded from RV via BL-RV-EX4
        # become orphaned (e.g. "AB GLOBAL HIGH YIELD PORTFOLIO", "GS
        # GLOBAL HY") -- confirmed genuine high-yield BOND funds via
        # KIID-text audit, previously claimed by RV only via the generic
        # "global" name-pattern collision. "high yiel" (no "d") matches
        # truncated naming ("AB GLOBAL HIGH YIEL.PORTF.C2 H").
        "high yield", "high yiel",
        # BL-RFF-IN4 (2026-07-04): paired with mixtos's BL-MX-EX1. These
        # fund-family fragments were confirmed genuine bond funds via KIID
        # text this session but don't match any include pattern above by
        # name alone (generic "Dynamic"/"Fixed Income"/"Flex Dynamic"/
        # "Bond Alloc" naming without a bond-specific keyword this list
        # already covers).
        "ubs glob dynamic", "db fixed income", "bsf em flex dynamic",
        "amundi str income", "jupiter dynamic",
        # "pimco diver" (no trailing "s") catches "DIVERS"/"DIVER."/"DIVERSF"
        # naming variants, paired with renta_variable's exclude of the same.
        "pimco diver", "pimco esg income",
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
        # BL-RFF-IN4b (2026-07-04): "edr bond alloc" checked BEFORE the
        # generic "allocation" exclude -- EDR Bond Allocation is a genuine
        # bond fund (>=90% en valores de deuda) despite the "Allocation"
        # naming that would otherwise route it away (that exclude exists
        # to keep genuine multi-asset "Allocation" funds out, which this
        # isn't).
        if "edr bond alloc" in n:
            return True
        if any(p in n for p in exclude_patterns):
            return False
        if re.search(r'\bhy\b', n):
            return True
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
        result["_signal_type"] = "Unconstrained"
        result["_signal_subtype"] = "Flexible Bond"
    elif any(k in name_l for k in ["absolute return", "abs ret", "absret"]):
        result["_signal_type"] = "Absolute Return"
    elif any(k in name_l for k in ["total return", "tot ret"]):
        result["_signal_type"] = "Total Return"
    elif any(k in name_l for k in [
        "millesima", "millesim", "milles select",
        "buy&watch", "buy & watch", "buywat",
        "target 20", "credit 20", "cred 20",
    ]):
        result["_signal_type"] = "Target Maturity"
    else:
        result["_signal_type"] = TYPE_FLEXIBLE_FI

    # -------------------------------------------------
    # Style_Profile / Exposure_Bias se derivan de forma centralizada en
    # classify_utils.derive_v20_attributes (engine = fuente única, AUDIT v20).
    # Se conserva solo la señal de subtipo 'Opportunistic'.
    if any(k in name_l for k in [
        "high yield", "hy", "opportunistic", "opportunist",
        "credit opport", "yield enhancement",
    ]):
        result["_signal_subtype"] = "Opportunistic"

    # -------------------------------------------------
    # Family — v2: familias granulares de restantes
    #   RF High Yield / RF Emergentes / RF Inflación (con Theme)
    # -------------------------------------------------
    if any(k in name_l for k in ["strategic", "tactical", "opportunist", "millesima"]):
        result["Family"] = FAMILY_STRATEGIC_ALLOCATION
    elif any(k in name_l for k in ["high yield", " hy ", " hy bd"]):
        result["Family"] = FAMILY_HIGH_YIELD
    elif any(k in name_l for k in ["emerging", "em debt", "em mkt", "emerg mkt", "emergentes", "em bond"]):
        result["Family"] = FAMILY_EMERGING_DEBT
    elif any(k in name_l for k in ["inflation", "inflat", "infl link"]):
        result["Family"] = FAMILY_INFLATION_LINKED
        result["Theme"] = "Inflation"   # v3: idioma objetivo inglés (Principio #8)
    else:
        result["Family"] = FAMILY_FLEXIBLE_FI

    # -------------------------------------------------
    # Subtype adicional (divisa)
    # -------------------------------------------------
    if any(k in name_l for k in ["multicurrency", "multi-currency", "multidivisa"]):
        result["_signal_subtype"] = (
            f"{result['Subtype']} | Multi-Currency" if result["_signal_subtype"]
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
    result["Strategy"] = _detect_strategy(
        None, result.get("_signal_subtype"), _name_l
    )
    result["Benchmark_Type"] = _detect_benchmark_type(
        None, None
    )

    return apply_semantic_validation(result, fund_name)

