# proyecto1/blocks/restantes.py
# -*- coding: utf-8 -*-
"""
Bloque RESTANTES — v3 canonico

Rol
---
Clasifica fondos que ningun bloque primario ha reclamado por heuristica
(nombre opaco o abreviado sin keywords reconocibles).

Estrategia de deteccion en 3 capas:
  Capa 1 — texto KIID (ventana adaptativa DDF/KIID): declaracion explícita del objetivo
  Capa 2 — nombre del fondo: señales de nombre via NAME_SIGNALS_*
  Capa 3 — SRRI: arbitro para fondos sin señal textual suficiente

Una vez determinada la naturaleza, delega la caracterizacion completa
al bloque primario correspondiente (importlib dinamico).
Si la naturaleza no puede determinarse, aplica tipificacion minima
con los atributos universales de classify_utils.

Universo
--------
Escenario A (primera ejecucion): fondos en Excel sin presencia en fund_master
Escenario B (re-ejecucion): fondos con Heuristic_Block='RESTANTES' en fund_master
"""

from __future__ import annotations

import importlib
import re
from typing import Optional, Dict, List

from core.classify_utils import (
    # Señales de nombre
    NAME_SIGNALS_MONETARIO, NAME_SIGNALS_RF_CORTO, NAME_SIGNALS_RF_FLEXIBLE,
    NAME_SIGNALS_MIXTO, NAME_SIGNALS_RV, NAME_SIGNALS_ALTERNATIVO,
    NAME_SIGNALS_ESTRUCTURADO,
    # Deteccion por nombre
    detect_nature_from_name,
    # Deteccion por KIID
    detect_nature_from_kiid, resolve_rf_subtype,
    # Atributos universales
    detect_geography        as _detect_geography,
    detect_theme            as _detect_theme,
    detect_is_esg           as _detect_is_esg,
    detect_style_profile    as _detect_style_profile,
    detect_exposure_bias    as _detect_exposure_bias,
    detect_strategy         as _detect_strategy,
    detect_benchmark_type   as _detect_benchmark_type,
    detect_profile_from_srri as _detect_profile_from_srri,
    detect_kiid_attributes,
    # Dominio
    _NATURE_CANONICAL,
)


BLOCK_NAME = "restantes"
# FUND_NATURE_VALUE no aplica — la naturaleza se determina dinamicamente
# en classify_fund() segun las señales de cada fondo.


# =====================================================
# Universo del bloque
# =====================================================

def get_universe_isins(df_master, conn=None) -> List[str]:
    """
    Universo = fondos en Excel aun sin clasificar.

    Escenario A (primera ejecucion):
        ISINs del Excel que NO estan en fund_master (nunca procesados).

    Escenario B (re-ejecucion con tabla ya poblada):
        ISINs con Heuristic_Block='RESTANTES' en fund_master
        (procesados por restantes en una iteracion previa).

    Universo final = union de ambos.
    """
    all_isins = set(df_master["ISIN"].dropna().astype(str).unique())

    if conn is None:
        return sorted(all_isins)

    rows = conn.execute(
        "SELECT ISIN, Heuristic_Block FROM fund_master"
    ).fetchall()
    in_db                = {r[0] for r in rows if r[0]}
    heuristica_restantes = {r[0] for r in rows if r[0] and r[1] == 'RESTANTES'}

    # Excluir fondos con documento erróneo — no tienen KIID válido para clasificar
    wrong_doc = {
        r[0] for r in conn.execute(
            "SELECT ISIN FROM fund_kiid_metadata WHERE KIID_Status = 'WRONG_DOC'"
        ).fetchall() if r[0]
    }

    not_in_db            = all_isins - in_db - wrong_doc           # Escenario A
    previously_restantes = (all_isins & heuristica_restantes) - wrong_doc  # Escenario B

    return sorted(not_in_db | previously_restantes)


# =====================================================
# Mapeo naturaleza detectada → nombre de módulo
# =====================================================

_NATURE_TO_BLOCK: dict = {
    # Claves canónicas (de _NATURE_CANONICAL)
    "Monetario":              "monetarios",
    "Renta Fija Corto Plazo": "rf_corto",
    "Renta Fija Flexible":    "rf_flexible",
    "Renta Variable":         "renta_variable",
    "Mixtos":                 "mixtos",
    "Alternativo":            "alternativos",
    # Claves internas (de detect_nature_from_name / detect_nature_from_kiid)
    "RF_Corto":               "rf_corto",
    "RF_Flexible":            "rf_flexible",
}


# =====================================================
# FUNCIÓN PRINCIPAL
# =====================================================

def classify_fund(
    fund_name: str,
    kiid_text: Optional[str],
    benchmark_declared: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """
    Clasifica un fondo del bloque Restantes.

    1. Detecta Fund_Nature con las 3 capas (KIID → nombre → SRRI).
    2. Delega al bloque primario para caracterizacion completa.
    3. Si no se puede determinar: tipificacion minima + atributos universales.
    """
    name_l = fund_name.lower() if isinstance(fund_name, str) else ""
    text_l = kiid_text.lower() if isinstance(kiid_text, str) else ""

    # ── Capa 1 — texto KIID (ventana adaptativa DDF/KIID): declaracion explícita del objetivo
    nature_raw = detect_nature_from_kiid(kiid_text or "")

    # ── Capa 2: nombre del fondo ─────────────────────────────────────────────
    if not nature_raw:
        nature_raw = detect_nature_from_name(name_l)

    # ── Capa 3: benchmark declarado ──────────────────────────────────────────
    # Si el benchmark está normalizado, su asset_class determina la naturaleza.
    # Escalable: funciona para cualquier fondo con benchmark en fund_master.
    if not nature_raw and benchmark_declared:
        try:
            from core.benchmark_normalizer import normalize_benchmark
            norm = normalize_benchmark(benchmark_declared)
            if norm:
                _bench_nature_map = {
                    "Equity":       "Renta Variable",
                    "Fixed Income": "RF_Flexible",
                    "Rate":         "Monetario",
                    "Commodity":    "Alternativo",
                    "Mixed":        "Mixtos",
                }
                nature_raw = _bench_nature_map.get(norm.asset_class)
        except Exception:
            pass

    # ── Resolver RF pendiente ─────────────────────────────────────────────────
    if nature_raw == "_RF_pending":
        nature_raw = resolve_rf_subtype(name_l, kiid_text or "")

    # ── Capa 3: SRRI como árbitro ─────────────────────────────────────────────
    if not nature_raw:
        m = re.search(r"\b([1-7])\s*/\s*7\b", text_l)
        srri = int(m.group(1)) if m else None
        if srri == 1:
            nature_raw = "Monetario"
        elif srri is not None and srri >= 5:
            nature_raw = "Renta Variable"
        elif srri == 2:
            nature_raw = "RF_Flexible"

    # ── Naturaleza canónica ───────────────────────────────────────────────────
    nature_canonical = _NATURE_CANONICAL.get(nature_raw, "Restantes") \
                       if nature_raw else "Restantes"

    # ── Delegar al bloque primario ────────────────────────────────────────────
    block_name = _NATURE_TO_BLOCK.get(nature_raw)
    if block_name:
        try:
            block_mod = importlib.import_module(f"blocks.{block_name}")
            result = block_mod.classify_fund(fund_name, kiid_text)
            result["Fund_Nature"] = nature_canonical
            return result
        except Exception:
            pass  # fallback a tipificacion minima

    # ── Fallback: tipificacion minima + universales ───────────────────────────
    result: Dict[str, Optional[str]] = {
        "Fund_Nature":    "Restantes",
        "Profile":        None,
        "Type":           None,
        "Strategy":       None,
        "Family":         None,
        "Style_Profile":  None,
        "Geography":      None,
        "Theme":          None,
        "Is_ESG":         0,
        "Exposure_Bias":  None,
        "Benchmark_Type": None,
        "Subtype":        None,
    }

    # Estructurado con caracterizacion minima
    if nature_raw == "Estructurado":
        result["Type"] = "Estructurado"
        if any(k in name_l for k in ["autocall", "autocallable"]):
            result["Subtype"]       = "Autocallable"
            result["Exposure_Bias"] = "Barrier Risk"
        elif any(k in name_l for k in ["capital protec", "guaranteed"]):
            result["Type"] = "Capital Protegido"
        result["Profile"] = "Moderado"

    elif any(k in name_l for k in ["fund of funds", "fof", "overlay"]):
        result["Type"] = "Fondo de Fondos"

    # Enriquecimiento desde KIID
    kiid_attrs = detect_kiid_attributes(kiid_text or "", "Restantes", result)
    for k, v in kiid_attrs.items():
        if not result.get(k):
            result[k] = v

    # Atributos universales
    m = re.search(r"\b([1-7])\s*/\s*7\b", text_l)
    srri = int(m.group(1)) if m else None
    if not result["Profile"]:
        result["Profile"]       = _detect_profile_from_srri(srri)
    result["Geography"]     = result["Geography"] or _detect_geography(name_l)
    result["Theme"]         = result["Theme"]     or _detect_theme(name_l)
    result["Is_ESG"]        = max(result["Is_ESG"], _detect_is_esg(fund_name))
    if not result["Style_Profile"]:
        result["Style_Profile"] = _detect_style_profile(name_l)
    if not result["Exposure_Bias"]:
        result["Exposure_Bias"] = _detect_exposure_bias(name_l, "Restantes")
    result["Strategy"]      = _detect_strategy(None, result.get("Subtype"), name_l)
    result["Benchmark_Type"] = _detect_benchmark_type(None, None)

    return result
