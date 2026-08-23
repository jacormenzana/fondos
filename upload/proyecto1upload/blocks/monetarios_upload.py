from typing import Optional, Dict, List
from core.classify_utils import (
    NAME_SIGNALS_MONETARIO,
    STRONG_MMF_STRUCTURE_MARKERS,
    _prefilter_match_monetario,   # OPT-B2 (R-1): universo = predicado compartido
    FAMILY_MONEY_MARKET,
    TYPE_MONEY_MARKET,
    TYPE_GOVT_MONEY_MARKET,
    TYPE_PRIME_MONEY_MARKET,
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


BLOCK_NAME = "monetarios"
FUND_NATURE_VALUE = "Monetario"


# =====================================================
# Universo del bloque
# =====================================================

def get_universe_isins(df_master) -> List[str]:
    # OPT-B2 (R-1/P#11): los patrones include/exclude viven en classify_utils
    # (`_prefilter_match_monetario`), fuente única compartida con
    # `detect_nature_from_prefilter`. Verificado idéntico al universo previo
    # (0 mismatches / 3.227 fondos).
    mask = df_master["Fund_Name"].apply(
        lambda n: _prefilter_match_monetario(n.lower()) if isinstance(n, str) else False
    )
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
        "_signal_type": None,
        "Family": None,
        "Style_Profile": "Defensivo",
        "Geography": None,
        "Theme": None,
        "Exposure_Bias": "Liquidity Bias",
        "_signal_subtype": None,
        # BL-MON-U1 (2026-07-05): money market funds always use Investment_Universe
        # = 'Global' (v20 §2A.1 #5 eliminated 'Liquidity'). Explicitly setting
        # it here overrides any stale value persisted from an earlier cycle
        # (COALESCE takes the new non-NULL value).
        "Investment_Universe": "Global",
    }

    name_l = fund_name.lower() if isinstance(fund_name, str) else ""

    # -------------------------------------------------
    # Type — v2: añadidas abreviaturas de restantes
    # gov liq / gov prim (BlackRock ICS), crd / corp (fondos privados abreviados)
    # -------------------------------------------------
    if any(k in name_l for k in [
        "government", "treasury", "sovereign", "gov liq", "gov prim", "public",
    ]):
        result["_signal_type"] = TYPE_GOVT_MONEY_MARKET
    elif any(k in name_l for k in [
        "prime", "corporate", "credit", "crd", "corp",
    ]):
        result["_signal_type"] = TYPE_PRIME_MONEY_MARKET
    else:
        result["_signal_type"] = TYPE_MONEY_MARKET

    # -------------------------------------------------
    # Family — BL-48: siempre Money Market.
    # La tipología regulatoria (CNAV/LVNAV/VNAV) va a Subtype, que es
    # el atributo correcto tras BL-43a. Family no debe duplicar esa info.
    # -------------------------------------------------
    result["Family"] = FAMILY_MONEY_MARKET

    # -------------------------------------------------
    # Subtype — BL-48: tipología regulatoria MMF 2017/1131 desde nombre
    # -------------------------------------------------
    if "cnav" in name_l:
        result["_signal_subtype"] = "CNAV"
    elif "lvnav" in name_l:
        result["_signal_subtype"] = "LVNAV"
    elif "vnav" in name_l:
        result["_signal_subtype"] = "VNAV"

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

    # Fix C eliminado (BL-48): Family ya es siempre "Monetario".
    # La propagación CNAV/LVNAV/VNAV a Family era el bug — Subtype es el lugar correcto.

    # ── Atributos universales canonico v2 ──────────────────────────
    # Se aplican tras la logica especifica del bloque.
    # El bloque puede haber asignado ya algunos — or None los respeta.
    _name_l = fund_name.lower() if isinstance(fund_name, str) else ""
    _text_l = kiid_text.lower() if isinstance(kiid_text, str) else ""
    _srri_m = re.search(r"\b([1-7])\s*/\s*7\b", _text_l)
    _srri   = int(_srri_m.group(1)) if _srri_m else None

    # BL-44: validar coherencia SRRI ↔ Fund_Nature=Monetario.
    # Fondos monetarios auténticos tienen SRRI 1-2 (excepcionalmente 3 en Enhanced Cash).
    # SRRI ≥ 3 indica un fondo mal capturado por el universo (nombre con "liquidity",
    # "treasury", etc. pero perfil de riesgo no monetario). Reclasificar a Restantes
    # para que el bloque RESTANTES asigne la naturaleza correcta.
    #
    # C2 (BL-44 hardening 2026-07-11): excepción para fondos cuyo nombre contiene
    # tokens de estructura regulatoria MMF (VNAV/LVNAV/CNAV) o denominaciones MMF
    # explícitas. Para éstos, el nombre es una declaración comercial formal más
    # fiable que el SRRI de un KIID potencialmente contaminado (sub-fondo incorrecto
    # almacenado). En lugar de eyectar, se mantiene Fund_Nature='Monetario' y se
    # pone un flag _bl44_srri_anomaly para que pipeline.py emita un DQ WARN.
    if _srri is not None and _srri >= 3:
        _has_strong_mmf_marker = any(tok in name_l for tok in STRONG_MMF_STRUCTURE_MARKERS)
        if _has_strong_mmf_marker:
            # Mantener clasificación Monetario pero marcar anomalía para DQ WARN
            result["_bl44_srri_anomaly"] = _srri
        else:
            result["Fund_Nature"] = "Restantes"
            result["Profile"] = _detect_profile_from_srri(_srri) or "Moderado"
            result["Family"] = None
            result["_signal_type"] = None
            result["_signal_subtype"] = None
            return apply_semantic_validation(result, fund_name)

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
