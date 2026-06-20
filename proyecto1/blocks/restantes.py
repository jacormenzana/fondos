# proyecto1/blocks/restantes.py
# -*- coding: utf-8 -*-
"""
Bloque RESTANTES — v9 (2026-05-09)

Cambios v9:
  BL-DLA-RESTANTES-1  Detector RF Emergentes OICVM para fondos forzados a
                      RESTANTES por BL-44 (incompatibilidad Nature/SRRI).
                      Con texto DLA limpio (dla_extractor.py v1.2), el patrón
                      "OICVM + mercados emergentes + (bonos|deuda|renta fija)"
                      ya es estable y permite clasificar correctamente como
                      Fund_Nature='Renta Fija Flexible', Family='RF Emergentes',
                      Type='Gestión Activa', Strategy='Activo'.
                      Posición: fallback path, antes de tipificación mínima.
                      Fondo piloto: LU0177592218 (Schroder EM Debt Total Return).

Cambios v8:
  BL-LANG-EN  Type y Family 'Estructurado': sustituidos por constantes
              TYPE_STRUCTURED y FAMILY_STRUCTURED importadas desde
              classify_utils. Elimina únicos literales ES de Family/Type
              en este módulo.

Cambios v7 (2026-05-08):
  BL-64a  Eliminado result["Data_Quality_Flag"] = "WARN" en la rama de baja
          confianza (línea anterior ~357). Causa raiz del ERROR B en log:
          validate_classification_contract rechaza claves no-canónicas.
          Data_Quality_Flag es responsabilidad exclusiva de pipeline.py;
          la señal de baja confianza ya se emite via logger.warning.

Cambios v6:
  Revert BL-65  Fund_Nature='Restantes' restituido como fallback canónico
                cuando las 3 capas no detectan naturaleza.
                nature_canonical = "Restantes" cuando nature_raw es None
                (antes: None, que causaba NOT NULL constraint failed en BD).
                Condición baja-confianza actualizada: usa == "Restantes"
                en vez de is None.

  Logging Ola1  Añadido logging por capa de detección:
                  [BL-RESTANTES-Capa1]: Nature detectada via KIID.
                  [BL-RESTANTES-Capa2]: Nature detectada via nombre.
                  [BL-RESTANTES-Capa3]: Nature detectada via SRRI.
                  [BL-RESTANTES-NoDetect]: Las 3 capas fallaron → fallback.
                  [BL-RESTANTES-Delegate]: Delegación a bloque primario.
                  [BL-RESTANTES-NoDetect] baja confianza con tag normalizado.

Cambios v5:
  BL-65  [REVERTIDO en v6] Corrección semántica: Fund_Nature="Restantes"
         eliminado como valor de fallback.

Cambios v4:
  BL-09  Capa 3 fallback SRRI: regex "\b([1-7])/7\b" reemplazado por
         extract_srri() de srri_text.py, que cubre KIID clásico Y DDF/PRIIPs.
         Mismo fix aplicado al segundo regex en path de tipificación mínima.
  BL-20  P08 logging: guardar nature_raw previo antes de sobreescribir para
         que el log refleje el valor real que fue corregido.
  BL-21  Delegación al bloque primario: intentar propagar srri_parsed
         (try/except TypeError para compatibilidad con bloques sin ese param).

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

import logging

logger = logging.getLogger(__name__)

from core.classify_utils import (
    # Señales de nombre
    NAME_SIGNALS_MONETARIO, NAME_SIGNALS_RF_CORTO, NAME_SIGNALS_RF_FLEXIBLE,
    NAME_SIGNALS_MIXTO, NAME_SIGNALS_RV, NAME_SIGNALS_ALTERNATIVO,
    NAME_SIGNALS_ESTRUCTURADO,
    # Constantes canónicas EN (BL-LANG-EN)
    TYPE_STRUCTURED,
    FAMILY_STRUCTURED,
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
    # Validación semántica (Principio #9)
    validate_all_semantic_consistency,
    apply_semantic_validation,
)


BLOCK_NAME = "restantes"
# FUND_NATURE_VALUE no aplica — la naturaleza se determina dinamicamente
# en classify_fund() segun las señales de cada fondo.


# =====================================================
# BL-DLA-RESTANTES-1: Detector RF Emergentes OICVM
# =====================================================
# Fondos forzados a RESTANTES por BL-44 (incompatibilidad Nature/SRRI)
# que el texto DLA limpio permite clasificar como Renta Fija Flexible /
# RF Emergentes. Expuesto tras la mejora upstream BL-DLA-1.
#
# Condición de activación: TODAS las señales required + ≥1 señal bonus.
# Las señales required son necesarias y suficientes para afirmar que el
# fondo es un OICVM de deuda/bonos en mercados emergentes.
# Las señales bonus confirman que es RF (no RV emergente, no monetario).

_RF_EMERGENTES_REQUIRED = [
    r'(?:fondo\s+)?OICVM\b',
    r'mercados?\s+emergentes?',
]
_RF_EMERGENTES_BONUS = [
    r'bonos?\b',
    r'renta\s+fija',
    r'deuda\b',
    r'total\s+return',
    r'deuda\s+(?:soberana|corporativa|p[uú]blica)',
    r'renta\s+fija\s+emergentes?',
    r'emerging\s+markets?\s+debt',
]


def _detect_rf_emergentes_oicvm(kiid_text: str) -> bool:
    """
    Devuelve True si el texto KIID contiene las señales de un fondo
    RF Emergentes OICVM que ha sido enrutado a RESTANTES por BL-44.

    Criterio: todas las señales required presentes + ≥1 señal bonus.
    El umbral de bonus evita falsos positivos en fondos RV Emergentes
    que también mencionan "OICVM" y "mercados emergentes".
    """
    if not kiid_text:
        return False
    required_hits = sum(
        1 for p in _RF_EMERGENTES_REQUIRED
        if re.search(p, kiid_text, re.IGNORECASE)
    )
    if required_hits < len(_RF_EMERGENTES_REQUIRED):
        return False
    bonus_hits = sum(
        1 for p in _RF_EMERGENTES_BONUS
        if re.search(p, kiid_text, re.IGNORECASE)
    )
    return bonus_hits >= 1


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
    srri_parsed: Optional[int] = None,            # ← NUEVO (Fase 1B)
) -> Dict[str, Optional[str]]:
    """
    Clasifica un fondo del bloque Restantes.

    1. Detecta Fund_Nature con las 3 capas (KIID → nombre → SRRI).
    2. Delega al bloque primario para caracterizacion completa.
    3. Si no se puede determinar: tipificacion minima + atributos universales.
    """
    name_l = fund_name.lower() if isinstance(fund_name, str) else ""
    text_l = kiid_text.lower() if isinstance(kiid_text, str) else ""

    # ── Capa 1 — texto KIID (ventana adaptativa DDF/KIID)
    nature_raw = detect_nature_from_kiid(kiid_text or "")
    if nature_raw:
        logger.info("[%s] [BL-RESTANTES-Capa1] Nature='%s' detectada via KIID", fund_name, nature_raw)

    # ── Capa 2: nombre del fondo
    if not nature_raw:
        nature_raw = detect_nature_from_name(name_l)
        if nature_raw:
            logger.info("[%s] [BL-RESTANTES-Capa2] Nature='%s' detectada via nombre", fund_name, nature_raw)

    # ── Capa 2.5: benchmark declarado
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

    # ── Resolver RF pendiente
    if nature_raw == "_RF_pending":
        nature_raw = resolve_rf_subtype(name_l, kiid_text or "")

    # ── Capa 3: SRRI como árbitro — USAR SRRI PARSEADO (Fase 1B) ────────
    if not nature_raw:
        # Primero intentar srri_parsed (de kiid_parser, más fiable)
        srri = srri_parsed
        # BL-09: Fallback — extract_srri() cubre KIID clásico Y DDF/PRIIPs
        # (el regex "\b([1-7])\s*/\s*7\b" solo cubría formato KIID con barra)
        if srri is None and kiid_text:
            try:
                from core.srri_text import extract_srri as _extract_srri_text
                srri = _extract_srri_text(kiid_text)
            except Exception:
                pass
        # BL-SRRI-GUARD: _extract_srri_text puede devolver dict en algunos
        # formatos DDF → coercer a escalar antes de comparaciones (>=).
        if isinstance(srri, dict):
            srri = srri.get("SRRI")
        if isinstance(srri, str) and srri.isdigit():
            srri = int(srri)
        if srri == 1:
            nature_raw = "Monetario"
        elif srri is not None and srri >= 5:
            nature_raw = "Renta Variable"
        elif srri == 2:
            nature_raw = "_RF_pending"
        if nature_raw:
            logger.info("[%s] [BL-RESTANTES-Capa3] Nature='%s' detectada via SRRI=%s", fund_name, nature_raw, srri)
        else:
            logger.warning(
                "[%s] [BL-RESTANTES-NoDetect] Las 3 capas no detectaron Nature → fallback 'Restantes'",
                fund_name,
            )

    # P08: Guardia SRRI vs Nature — SRRI prevalece sobre nombre
    # Un fondo con SRRI≥5 NO puede ser Monetario ni RF Corto Plazo
    if (srri_parsed is not None and srri_parsed >= 5
            and nature_raw in ("Monetario", "Renta Fija Corto Plazo",
                               "RF_Corto", None)):
        _nature_before_p08 = nature_raw          # BL-20: guardar antes de sobreescribir
        nature_raw = "Renta Variable"
        logger.info("[%s] P08: SRRI=%d fuerza Nature=RV (era '%s')",
                    fund_name, srri_parsed, _nature_before_p08)

    # ── Resolver RF pendiente (puede venir de Capa 3)
    if nature_raw == "_RF_pending":
        nature_raw = resolve_rf_subtype(name_l, kiid_text or "")

    # ── Naturaleza canónica
    # v6: revertir BL-65. Cuando las 3 capas no detectan naturaleza,
    # asignar 'Restantes' (Fund_Nature canónica que indica "no determinable
    # por heurística"). Es valor válido en schema y backlog (33 fondos en v3.4).
    # El bloque puede emitir 'Restantes' para que BL-62 actúe después.
    nature_canonical = _NATURE_CANONICAL.get(nature_raw) \
                       if nature_raw else "Restantes"

    # ── Delegar al bloque primario ────────────────────────────────────────
    block_name = _NATURE_TO_BLOCK.get(nature_raw)
    if not block_name:
        # Intentar también con la clave canónica
        block_name = _NATURE_TO_BLOCK.get(nature_canonical)

    if block_name:
        try:
            block_mod = importlib.import_module(f"blocks.{block_name}")
            logger.info("[%s] [BL-RESTANTES-Delegate] Delegando a bloque '%s'", fund_name, block_name)
            # BL-21: intentar propagar srri_parsed al bloque primario.
            # Si el bloque no acepta srri_parsed (TypeError), llamar sin él.
            try:
                result = block_mod.classify_fund(fund_name, kiid_text,
                                                  srri_parsed=srri_parsed)
            except TypeError:
                result = block_mod.classify_fund(fund_name, kiid_text)
            result["Fund_Nature"] = nature_canonical

            # ── Enriquecer Theme si el bloque no lo asigna (Fase 1D) ─────
            if not result.get("Theme"):
                result["Theme"] = _detect_theme(name_l) or "Core/General"

            return apply_semantic_validation(result, fund_name)
        except Exception as exc:
            logger.error(
                "[%s] Delegación a bloque '%s' fallida: %s: %s",
                fund_name, block_name, type(exc).__name__, exc,
            )

    # ── Fallback: tipificacion minima + universales ───────────────────────
    # v6: nature_canonical ya garantiza ser 'Restantes' como mínimo.

    # ── BL-DLA-RESTANTES-1: detector RF Emergentes OICVM ─────────────────
    # Actúa antes de la tipificación mínima para fondos en RESTANTES que
    # tienen texto DLA limpio con señales identificables de deuda emergente.
    # Caso documentado: fondos con Nature_efectivo=Monetario pero SRRI≥3
    # forzados aquí por BL-44, cuyo texto contiene "OICVM + mercados
    # emergentes + bonos/deuda/total return".
    if nature_canonical == "Restantes" and _detect_rf_emergentes_oicvm(kiid_text or ""):
        logger.info(
            "[%s] [BL-DLA-RESTANTES-1] RF Emergentes OICVM detectado → "
            "Fund_Nature='Renta Fija Flexible', Family='RF Emergentes', "
            "Type='Gestión Activa', Strategy='Activo'",
            fund_name,
        )
        result_rfe: Dict[str, Optional[str]] = {
            "Fund_Nature":   "Renta Fija Flexible",
            "Family":        "RF Emergentes",
            "_signal_type":          "Gestión Activa",
            "Strategy":      "Activo",
            "Profile":       None,
            "Style_Profile": None,
            "Geography":     "Emergentes",
            "Theme":         None,
            "Is_ESG":        0,
            "Exposure_Bias": None,
            "Benchmark_Type": None,
            "_signal_subtype":        None,
        }
        # Enriquecimiento universal sobre la clasificación base
        srri_rfe = srri_parsed
        if srri_rfe is None and kiid_text:
            try:
                from core.srri_text import extract_srri as _extract_srri_text
                srri_rfe = _extract_srri_text(kiid_text)
            except Exception:
                pass
        if not result_rfe["Profile"]:
            result_rfe["Profile"]       = _detect_profile_from_srri(srri_rfe)
        result_rfe["Theme"]         = _detect_theme(name_l) or "Core/General"
        result_rfe["Is_ESG"]        = max(result_rfe["Is_ESG"], _detect_is_esg(fund_name))
        if not result_rfe["Style_Profile"]:
            result_rfe["Style_Profile"] = _detect_style_profile(name_l)
        if not result_rfe["Exposure_Bias"]:
            result_rfe["Exposure_Bias"] = _detect_exposure_bias(name_l, "Renta Fija Flexible")
        result_rfe["Benchmark_Type"] = _detect_benchmark_type(None, None)
        return apply_semantic_validation(result_rfe, fund_name)

    result: Dict[str, Optional[str]] = {
        "Fund_Nature":    nature_canonical,   # 'Restantes' como fallback canónico
        "Profile":        None,
        "_signal_type":           None,
        "Strategy":       None,
        "Family":         None,
        "Style_Profile":  None,
        "Geography":      None,
        "Theme":          None,
        "Is_ESG":         0,
        "Exposure_Bias":  None,
        "Benchmark_Type": None,
        "_signal_subtype":        None,
    }

    # Estructurado con caracterizacion minima
    if nature_raw == "Estructurado":
        result["_signal_type"] = TYPE_STRUCTURED
        result["Family"] = FAMILY_STRUCTURED
        if any(k in name_l for k in ["autocall", "autocallable"]):
            result["_signal_subtype"]       = "Autocallable"
            result["Exposure_Bias"] = "Barrier Risk"
        elif any(k in name_l for k in ["capital protec", "guaranteed"]):
            result["_signal_type"] = "Capital Protegido"
        result["Profile"] = "Moderado"

    elif any(k in name_l for k in ["fund of funds", "fof", "overlay"]):
        result["_signal_type"] = "Fondo de Fondos"

    # Enriquecimiento desde KIID
    kiid_attrs = detect_kiid_attributes(kiid_text or "", nature_canonical, result)
    for k, v in kiid_attrs.items():
        if not result.get(k):
            result[k] = v

    # Atributos universales
    srri_val = srri_parsed                      # ← Usar srri_parsed
    # BL-09: Fallback — extract_srri() cubre KIID clásico Y DDF/PRIIPs
    if srri_val is None and kiid_text:
        try:
            from core.srri_text import extract_srri as _extract_srri_text
            srri_val = _extract_srri_text(kiid_text)
        except Exception:
            pass

    if not result["Profile"]:
        result["Profile"]       = _detect_profile_from_srri(srri_val)
    result["Geography"]     = result["Geography"] or _detect_geography(name_l)
    result["Theme"]         = result["Theme"]     or _detect_theme(name_l) or "Core/General"
    result["Is_ESG"]        = max(result["Is_ESG"], _detect_is_esg(fund_name))
    if not result["Style_Profile"]:
        result["Style_Profile"] = _detect_style_profile(name_l)
    if not result["Exposure_Bias"]:
        result["Exposure_Bias"] = _detect_exposure_bias(name_l, nature_canonical)
    result["Strategy"]      = _detect_strategy(None, result.get("_signal_subtype"), name_l)
    result["Benchmark_Type"] = _detect_benchmark_type(None, None)

    # ── Baja confianza: fallback conservador
    populated = sum(1 for k, v in result.items() if v is not None and k != "Is_ESG")
    # v6: condición actualizada — nature_canonical='Restantes' indica no-determinado
    if result["Fund_Nature"] == "Restantes" and populated <= 3:
        logger.warning(
            "[%s] [BL-RESTANTES-NoDetect] Clasificación con baja confianza (%d atributos poblados). "
            "Fund_Nature='Restantes' (no determinable); Data_Quality_Flag=WARN.",
            result.get("ISIN", fund_name), populated,
        )
        result["_signal_type"]   = None
        result["Family"] = None
        # BL-64a-fix: Data_Quality_Flag es calculado por pipeline.py, no por bloques.
        # Emitirlo aquí causa ValueError en validate_classification_contract.
        # La señal de baja confianza se comunica via logging (ya hecho arriba);
        # pipeline.py asigna DQ=WARN cuando Fund_Nature='Restantes' (BL-65/BL-62).

    return apply_semantic_validation(result, fund_name)
