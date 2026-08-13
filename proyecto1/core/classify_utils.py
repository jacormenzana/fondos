# proyecto1/core/classify_utils.py
# -*- coding: utf-8 -*-
"""
Utilidades de clasificacion compartidas por todos los bloques de P1.
Version 12 — BL-LANG-EN (2026-05-09)

Cambios v12:
  BL-LANG-EN  Family, Type y Subtype: idioma objetivo cambiado a inglés
              (alineado con Sector_Focus — nomenclatura estándar internacional).
              Cambios:
                Constantes canónicas: sección reescrita con FAMILY_*, TYPE_*,
                  SUBTYPE_* en EN. Todos los bloques deben importar estas
                  constantes en lugar de literales inline.
                TYPE_TRANSLATION_MAP: invertido a pass-through EN + corrección
                  inversa ES→EN (stale BD). Fusión Gobierno CP + Deuda Pública CP
                  → Short-Term Government (misma naturaleza, duplicidad por
                  emisores distintos sin centralización DRY).
                FAMILY_TRANSLATION_MAP: ídem, ES→EN.
                ALLOWED_TYPE_BY_NATURE: valores EN.
                ALLOWED_FAMILY_BY_NATURE: valores EN.
                LEXICAL_FAMILY_INFERENCE_BL62: catálogo actualizado a EN.

Version 11 — BL-62-LEXICAL-EXT (2026-05-09)

Cambios v11:
  BL-62-LEXICAL-EXT  LEXICAL_FAMILY_INFERENCE_BL62: 4 nuevos grupos de
                     patrones para los 11 fondos sin cobertura detectados
                     en ciclo 2026-05-09:
                       - US SH DURAT/SHRT DUR/DOLLAR SH → RFC/RFC
                         (BGF US SHORT DURATION BOND, 4 ISINs)
                       - DOLLAR LIQUID / USD LIQUID → Monetario/Monetario
                         (SISF US DOLLAR LIQUIDITY, 2 ISINs)
                       - MR DEB TOT / E MR DEB → RFF/Total Return
                         (SISF E MR DEB TOT RE, 1 ISIN)
                       - LUXURY BR → RV Temática/Gestión Activa
                         (GAM LUXURY BRAND/BRANDS, 4 ISINs)
                     Orden de inserción: ANTES del grupo Income Oriented
                     (específicos antes que genéricos — primer match gana).

Version 10 — Sprint A.1.b correctivo (2026-04-30)

Cambios v10:
  Revert BL-65  Restituir 'Restantes' como Fund_Nature canónico válido.
                Cambios:
                  _NATURE_CANONICAL: restituida entrada "Restantes": "Restantes".
                  ALLOWED_VALUES_BY_COLUMN["Fund_Nature"]: restituido "Restantes".
                  ALLOWED_TYPE_BY_NATURE: restituida entrada "Restantes": [] (catch-all).
                  ALLOWED_FAMILY_BY_NATURE: restituida entrada "Restantes": [] (catch-all).

  Theme fix     detect_type_from_kiid línea ~1402: "Inflación" → "Inflation"
                (Principio #8 — Theme idioma objetivo: inglés).

  Logging fix   validate_all_semantic_consistency: convertida a función PURA
                (eliminados logger.info/warning internos). El logging vive
                exclusivamente en apply_semantic_validation (punto único).
                Soluciona duplicación [???] + [NOMBRE] en log del ciclo.

  Tags          [BL62] → [BL-62] en propagate_nature_to_restantes_type_family.

  NORM logging  apply_post_characterize_normalization: añadido logger.warning
                cuando una traducción modifica el valor (señal de emisor con
                idioma incorrecto). Normativa sección 7.2c.

Cambios v9:
  BL-65  [REVERTIDO en v10] Corrección semántica: "Restantes" no es una Fund_Nature válida.

Cambios v8:
  BL-62  LEXICAL_FAMILY_INFERENCE_BL62: catálogo léxico canónico con 11
         grupos de patrones (HY, inflación, emergentes, retorno absoluto,
         activos reales, RV temática, orientado a renta, total return,
         RF flexible, mixtos). Pre-compilado en _BL62_COMPILED.
         _infer_family_type_from_name_bl62(fund_name) → (family, type) | (None, None):
         función pública para inferencia léxica Family/Type desde nombre.
         Procesamiento en orden: específicos antes que genéricos (primer match gana).
         propagate_nature_to_restantes_type_family(fund_record, isin, log_fn):
         función pública invocada por pipeline tras BL-44. Re-infiere Family/Type
         para fondos reclasificados a Restantes (valores heredados son falsos por
         construcción). Estrategia: Fase 2 léxica + Fase 3 residual (DQ=WARN).
         Marca flags _bl62_force_overwrite_* para que BL-64 (sqlite_writer) fuerce
         sobrescritura sin COALESCE. Principio #2 DRY: catálogo centralizado aquí,
         invocado desde pipeline; bloques no duplican lógica de inferencia.

Version 7 — BL-49 (2026-04-29)

Cambios v7:
  BL-49  detect_currency_hedged_from_kiid(kiid_text) → (value, pattern_id):
         Función pública de segunda fase para detección de Currency_Hedged
         desde texto completo del KIID/DDF. Implementa catálogo de 10+8
         patrones de alta confianza (H01-H10 Hedged, U01-U08 Unhedged) en
         inglés y español, pre-compilados en _CH_HEDGED_RE/_CH_UNHEDGED_RE.
         Se invoca desde fund_characterizer.detect_currency_hedged() cuando
         la fase 1 (nombre del fondo) no aporta señal.
         Centralizada en classify_utils (Principio #2 DRY) para que también
         pueda invocarse desde pipeline si necesita cobertura adicional.
         La prevalencia Hedging_Policy→Currency_Hedged (BL-31/INTER-12)
         sigue aplicándose en pipeline DESPUÉS de este extractor.
         Logging: cada detección emite 'CH-KIID-<pattern_id>' para trazabilidad.

  BL-54  THEME_TO_SECTOR_FOCUS_MAP: mapa canónico Theme→Sector_Focus.
         Punto único de verdad (Principio #2 DRY). Idioma objetivo: español.
         map_theme_to_sector_focus(theme) → función pública sobre el mapa.
         Reemplaza los dos mapas paralelos (pipeline inline + fund_characterizer).
         Contiene 20 entradas cubriendo todos los themes del catálogo.
         SECTOR_FOCUS_TRANSLATION_MAP: marcado "legacy normalization" — solo
         para sanear valores históricos en inglés que pudieran quedar en BD.
         normalize_sector_focus(): actualizada para consultar primero
         SECTOR_FOCUS_TRANSLATION_MAP y luego actuar como pass-through.

  BL-56  apply_post_characterize_normalization(classification) → dict:
         Función agregadora de normalización lingüística post-characterize.
         Punto único de invocación desde pipeline (Principio #2 DRY).
         Aplica: Sector_Focus (normalize_sector_focus),
                 Type (TYPE_TRANSLATION_MAP),
                 Family (FAMILY_TRANSLATION_MAP),
                 Theme (no traduce — ya está en inglés canónico).
         Solo actúa sobre campos no-None; no sobreescribe NULL deliberado.

  BL-57  FAMILY_TRANSLATION_MAP: entrada 'Income Oriented' → 'Orientado a Renta'.
         Decisión Opción B: traducir a español (Principio #8).
  BL-57v3 FAMILY_INCOME_ORIENTED: constante canónica exportable.
  BL-65b  FAMILY_INCOME_ORIENTED: "Income Oriented" (EN canónico, Principio #8).
          BL-57 v2 había asignado "Orientado a Renta" (ES). Corregido en:
          constante, FAMILY_TRANSLATION_MAP (pass-through + normalizador BD),
          ALLOWED_FAMILY_BY_NATURE (Mixtos, RF Flexible),
          propagate_nature_to_restantes_type_family.
         Elimina emisión inline en bloques. Norma BL-57 v3 (26-abr-2026):
         todo literal Family debe definirse aquí e importarse desde los bloques.
         Antipatrón documentado: BL-57 v2 actualizó validador+SQL sin tocar
         el emisor primario → 104 fondos perdidos silenciosamente.

  TYPE_TRANSLATION_MAP: añadido con excepciones inglesas documentadas.
         Cubre traducciones ES y mapas de paso-through para términos sectoriales
         sin equivalente compacto en español.

Cambios v5:
  BL-52  validate_geography_universe(): auto-corrección Investment_Universe
         'Country'→'Regional' cuando Geography contiene una región geográfica
         amplia (Latinoamérica, Europa del Este, Asia Pacífico, etc.).
         Causa raíz: el clasificador asignaba 'Country' pero la inferencia de
         Geography devolvía valores de región, que son semánticamente incompatibles.
         Firma ampliada a 3-tupla: ('OK'|'WARNING'|'CORRECTED', msg, corrected_val).
         Backward compatible: callers que desempaquetan 2 valores siguen funcionando.
         _REGION_GEOGRAPHIES: catálogo canónico de valores-región (9 entradas).
         _COUNTRY_GEOGRAPHIES: ampliado con Rusia, Italia, Alemania, Francia,
         España, Reino Unido, Suiza (coherencia con catálogo de pipeline.py).
         validate_all_semantic_consistency(): INTER-10 actualizado para aplicar
         la auto-corrección como error crítico (no solo warning).

Cambios v4:
  BL-19  FUND_NATURES y TYPE_BY_NATURE: "Mixto" → "Mixtos" (unificación canónica)
  BL-22  SECTOR_FOCUS_TRANSLATION_MAP + normalize_sector_focus(): idioma objetivo ES
  BL-23  THEMATIC_MAP: añadidos Inflation, Cybersecurity, Megatrends
  BL-24  ALLOWED_VALUES_BY_COLUMN: Credit_Quality en inglés + "Not Applicable";
         Theme con lista completa de valores permitidos
  BL-30  validate_all_semantic_consistency(): INTER-11 — Broad+Sector_Focus→Sector
  BL-31  validate_all_semantic_consistency(): INTER-12 — Currency_Hedged vs Hedging_Policy
  BL-32  validate_accumulation_distribution(): firma 3-tupla + inferencia DISTRIBUTION
  BL-33  validate_all_semantic_consistency(): INTER-13 — Universe fallback por Nature


FUNCIONES:
  Señales de nombre (constantes):
    NAME_SIGNALS_MONETARIO, NAME_SIGNALS_RF_CORTO, NAME_SIGNALS_RF_FLEXIBLE,
    NAME_SIGNALS_MIXTO, NAME_SIGNALS_RV, NAME_SIGNALS_ALTERNATIVO,
    NAME_SIGNALS_ESTRUCTURADO

  Normalización geográfica (ES→EN, fuente única R-1):
    normalize_geography_en(geo_es, name_l="") → str | None

  Deteccion por nombre:
    detect_nature_from_name(name_l)           → str | None
    detect_geography(name_l)                  → str | None
    detect_theme(name_l)                      → str | None
    detect_is_esg(fund_name)                  → int  (0/1)
    detect_style_profile(name_l)              → str | None
    detect_exposure_bias(name_l, fund_nature) → str | None
    detect_strategy(replication_method, subtype, name_l) → str | None
    detect_benchmark_type(benchmark_declared, replication_method) → str | None
    detect_profile_from_srri(srri)            → str | None

  Deteccion por texto KIID (ventana correcta 1200-4500):
    detect_nature_from_kiid(kiid_text)        → str | None
    detect_type_from_kiid(kiid_text, fund_nature) → str | None
    detect_style_from_kiid(kiid_text)         → str | None
    detect_geography_from_kiid(kiid_text)     → str | None
    detect_esg_from_kiid(kiid_text)           → int  (0/1)
    detect_ongoing_charge_from_kiid(kiid_text) → float | None
    detect_kiid_attributes(kiid_text, fund_nature) → dict

  Dominios:
    FUND_NATURES, TYPE_BY_NATURE, BIAS_ALLOWED_NATURES
"""

import re
import math
import logging
from typing import Optional, Tuple


# ============================================================
# Comparador de valores de coste — fuente única (R-1, Principio #2 DRY)
# ------------------------------------------------------------
# INTEGRATED_SPEC_v20_v2 §4.4: sustituye al _TOL=0.011 fijo del prototipo
# de diagnóstico. Tolerancia híbrida ATOL+RTOL vía math.isclose. Usado por:
#   - la arbitración dual bands-X / ruled (core/cost_arbitration.py),
#   - la cross-validation %↔EUR de los extractores de coste.
# Las constantes viven en config (catálogo / dependency leaf); aquí solo la
# función. Import defensivo (el módulo puede ejecutarse con sys.path variable).
try:
    from config import COST_CMP_ABS_TOL, COST_CMP_REL_TOL
except ImportError:  # pragma: no cover
    try:
        from shared.config import COST_CMP_ABS_TOL, COST_CMP_REL_TOL
    except ImportError:
        # Fallback inocuo: mismos valores semilla que config v20. Si esto se
        # dispara, config no está en sys.path; el llamador debe corregirlo.
        COST_CMP_ABS_TOL, COST_CMP_REL_TOL = 0.0002, 0.01


def cost_values_agree(a: Optional[float], b: Optional[float]) -> bool:
    """¿Concuerdan dos valores de coste (en puntos %)?

    Devuelve False si alguno es None (no comparable). En otro caso aplica
    tolerancia híbrida: |a-b| <= max(REL_TOL*max(|a|,|b|), ABS_TOL).
    """
    if a is None or b is None:
        return False
    return math.isclose(a, b, rel_tol=COST_CMP_REL_TOL, abs_tol=COST_CMP_ABS_TOL)


# ============================================================
# Canonical literals — Family, Type, Subtype  (idioma: EN)
# ------------------------------------------------------------
# BL-LANG-EN (2026-05-09): idioma objetivo de Family, Type y Subtype
# cambiado a inglés (alineado con Sector_Focus — nomenclatura estándar).
# Todos los emisores deben importar estas constantes; nunca literales inline.
#
# Norma BL-57 v3 (26-abr-2026): todo literal Family/Type/Subtype
# añadido al catálogo debe registrarse aquí Y aparecer en
# ALLOWED_FAMILY_BY_NATURE / ALLOWED_TYPE_BY_NATURE antes de ser
# emitido por cualquier bloque.
# ============================================================

# --- Family ---
FAMILY_EQUITY_CORE          = "Equity Core"
FAMILY_THEMATIC_EQUITY      = "Thematic Equity"
FAMILY_SHORT_TERM_FI        = "Short-Term Fixed Income"
FAMILY_FLEXIBLE_FI          = "Flexible Fixed Income"
FAMILY_MULTI_ASSET          = "Multi-Asset"
FAMILY_ABSOLUTE_RETURN      = "Absolute Return"
FAMILY_HIGH_YIELD           = "High Yield"
FAMILY_EMERGING_DEBT        = "Emerging Market Debt"
FAMILY_INFLATION_LINKED     = "Inflation-Linked"
FAMILY_MONEY_MARKET         = "Money Market"
FAMILY_REAL_ASSETS          = "Real Assets"
FAMILY_STRUCTURED           = "Structured"
FAMILY_STRATEGIC_ALLOCATION = "Strategic Allocation"
FAMILY_INCOME_ORIENTED      = "Income Oriented"          # BL-65b

# --- Type ---
TYPE_ACTIVE_MANAGEMENT      = "Active Management"
TYPE_INDEX_FUND             = "Index Fund"
TYPE_FLEXIBLE_FI            = "Flexible Fixed Income"
TYPE_SHORT_TERM_FI          = "Short-Term Fixed Income"
TYPE_SHORT_TERM_GOVT        = "Short-Term Government"    # fusión Gobierno CP + Deuda Pública CP
TYPE_SHORT_TERM_CREDIT      = "Short-Term Credit"
TYPE_MONEY_MARKET           = "Money Market"
TYPE_GOVT_MONEY_MARKET      = "Government Money Market"
TYPE_PRIME_MONEY_MARKET     = "Prime Money Market"
TYPE_COMMODITIES            = "Commodities"
TYPE_STRUCTURED             = "Structured"
TYPE_REAL_ASSETS            = "Real Assets"
TYPE_VOLATILITY_TARGET      = "Volatility Target"

# --- Subtype ---
SUBTYPE_INDEX_FUND          = "Index Fund"
SUBTYPE_ETF                 = "ETF"
SUBTYPE_OPPORTUNISTIC       = "Opportunistic"
SUBTYPE_LOW_DURATION        = "Low Duration"
SUBTYPE_FLOATING_RATE_NOTES = "Floating Rate Notes"
SUBTYPE_FIXED_BAND_15       = "Fixed Band 15"
SUBTYPE_FIXED_BAND_50       = "Fixed Band 50"
SUBTYPE_FIXED_BAND_75       = "Fixed Band 75"
SUBTYPE_VOLATILITY_TARGET   = "Volatility Target"
SUBTYPE_REAL_ESTATE         = "Real Estate"
SUBTYPE_REL_VALUE_ARB       = "Relative Value / Arbitrage"
SUBTYPE_PHYSICAL_DERIV      = "Physical / Derivatives"


# ============================================================
# Dominios canónicos v2
# ============================================================

FUND_NATURES: frozenset = frozenset({
    "Monetario", "Renta Fija Corto Plazo", "Renta Fija Flexible",
    "Renta Variable", "Mixtos", "Alternativo",  # BL-19: "Mixtos" (no "Mixto")
})

BIAS_ALLOWED_NATURES: frozenset = frozenset({
    "Renta Fija Corto Plazo", "Renta Fija Flexible",
    "Renta Variable", "Alternativo",
})

TYPE_BY_NATURE: dict = {
    "Monetario":              frozenset({"CNAV","LVNAV","VNAV","Enhanced Cash"}),
    "Renta Fija Corto Plazo": frozenset({"Gobierno CP","Crédito CP","Floating Rate",
                                          "Covered Bond","Ultrashort"}),
    "Renta Fija Flexible":    frozenset({"Corporativo","Gobierno","High Yield","Emergentes",
                                          "Inflación","Convertible","Multisector",
                                          "Unconstrained","Target Maturity"}),
    "Renta Variable":         frozenset({"Active Management","Index Fund","ETF","Smart Beta"}),
    "Mixtos":                 frozenset({"Allocation","Target Volatility","Target Outcome",
                                          "Tactical","Lifecycle"}),  # BL-19: "Mixtos"
    "Alternativo":            frozenset({"Absolute Return","Long/Short","Market Neutral",
                                          "Sistemático/CTA","Commodities","Real Assets",
                                          "Estructurado"}),
}

# Mapeo interno → canónico para _detect_nature
_NATURE_CANONICAL: dict = {
    "Monetario":     "Monetario",
    "RF_Corto":      "Renta Fija Corto Plazo",
    "RF_Flexible":   "Renta Fija Flexible",
    "Renta Variable":"Renta Variable",
    "Mixtos":        "Mixtos",
    "Alternativo":   "Alternativo",
    "Estructurado":  "Estructurado",
    "Restantes":     "Restantes",  # v10: restituido (eliminado erróneamente por BL-65)
                                   # Valor canónico para fondos sin Nature determinable.
                                   # Es valor válido en schema (backlog v3.4: 33 fondos).
}

# OPT-B (2026-07-16): canonical routing Fund_Nature/internal-code → block module name.
# Single source of truth (P#11/R-1): imported by restantes.py and pipeline.py.
# No entry for 'Estructurado'/'Restantes': these fall to restantes minimum classification.
_NATURE_TO_BLOCK: dict = {
    "Monetario":              "monetarios",
    "Renta Fija Corto Plazo": "rf_corto",
    "Renta Fija Flexible":    "rf_flexible",
    "Renta Variable":         "renta_variable",
    "Mixtos":                 "mixtos",
    "Alternativo":            "alternativos",
    "RF_Corto":               "rf_corto",    # internal code alias
    "RF_Flexible":            "rf_flexible",  # internal code alias
}


# ============================================================
# SEÑALES DE NOMBRE — constantes compartidas
# ============================================================
# Cada bloque primario importa su lista.
# restantes.py las usa todas vía detect_nature_from_name().
# Nuevos patrones identificados en análisis de 527 restantes:
#   RFF: 'bond ', 'bonds ', 'bd ', 'bnds ', ' debt ', 'tot ret',
#        'sh durat', 'dur bond', 'ig cred', 'fix inc horizon',
#        'corp bd', 'hy bnd', 'green bond'
#   RFC: 'sh durat bd', 'dur bond ', 'fix inc horizon'

NAME_SIGNALS_MONETARIO: list = [
    # Amundi Liquidity family (LIQ / LQ)
    "liq select", "liq st gov", "liq-rtd", "liq rtd",
    "lq sh trm", "lq sh term", "lq-rtd", "eu liq st",
    # BlackRock ICS (Institutional Cash)
    "ics liq", "ics euro liq", "ics usd liq", "ics gov liq",
    "ics ul sh cor", "ic admin iii", "ics usd liq pr",
    # Money Market nombres directos
    "euro money mkt", "euro money mk",
    "mon mrkt fnd", "lux mon mrkt",
    "st mm vnav", "liqud usd st mm",
    "st money mket", "short-term mm",
    "s-t money mkt", "s-t money mk",
     # DDF — añadido personal detectado en pictet
     "money mkt",  "money mket", 
    # Français
    "tresor court", "court terme",
    "entreprises n ", "entreprises r ",
    # Otros
    "institut liq",
    "euro liq reserv", "liq reserv",
    "inst esq euro money", "geldmarkt",
    # BNP InstiCash / Pictet Sovereign MM / Amundi Cash
    "insticash", "instica eur",
    "pictet sov", "sov st mney", "sover.sho",
    "amundi cash", "amundi fds cash",
    # Nuevos — análisis Restantes (señales específicas sin falso positivo)
    "euro m mkt",                    # JPM EURO M MKT VNAV (evita EM MKT)
    "standard mm vnav",              # JPM STANDARD MM VNAV
    "lqudty lvnav",                  # JPM USD LQUDTY LVNAV (OCR)
    "inscash",                       # BNP PARIBS INSCASH EUR 3M
    "gbp liq lvnav",                 # JPM GBP LIQ LVNAV
    "gbp liq cnav",                  # variante CNAV
    "usd treasur cnav",              # JPM USD TREASURY CNAV
    "usd liq cnav",                  # JPM USD LIQ CNAV
    "fidelity euro cash",            # FIDELITY EURO CASH
    "fidelity fund us cash",         # FIDELITY FUND US CASH
    "fidelity us cash",              # variante
    # FIX-MON-LIQ (2026-07-18): denominaciones genéricas de fondos de liquidez
    # (Schroder SISF EURO LIQUIDITY / SISF US DOLLAR LIQUIDITY) no capturadas por
    # tokens específicos anteriores. "euro liquidity" y "dollar liquidity" son
    # convenciones de nombre MMF; "liquidity" solo (sin prefijo de moneda) se
    # omite deliberadamente por su alta tasa de falso positivo (STRONG_MMF comment).
    "euro liquidity",                # SISF EURO LIQUIDITY (Schroders) + variantes
    "dollar liquidity",              # SISF US DOLLAR LIQUIDITY
    "usd liquidity",                 # alias USD LIQUIDITY
]

# C1 (BL-44 hardening 2026-07-11): subconjunto de NAME_SIGNALS_MONETARIO que
# codifica estructura regulatoria de tipo de NAV (VNAV/LVNAV/CNAV) o clasifi-
# cación MMF regulatoria explícita. Son señales FUERTES: si el nombre contiene
# alguno de estos tokens, la denominación del fondo (una declaración comercial
# formal) es más fiable que un SRRI extraído de un KIID potencialmente conta-
# minado. Se excluyen deliberadamente los tokens DÉBILES / AMBIGUOS del
# universo de monetarios (treasury, cash, tresorerie, liquidity, enhanced) que
# pueden aparecer en fondos de otro tipo.
STRONG_MMF_STRUCTURE_MARKERS: tuple = (
    "vnav", "lvnav", "cnav",
    "mmf",
    "standard mm",
    "m mkt", "m mket",
    "money market", "money mkt", "money mket",
    "inscash",
    "euro m mkt",          # JPM EURO M MKT VNAV
    "eu m mkt",            # DWS ESG EU M MKT IC100
    "lqudty lvnav",        # JPM USD LQUDTY LVNAV (OCR)
    "standard mm vnav",    # JPM STANDARD MM VNAV
    "ucits mmf",
)

NAME_SIGNALS_RF_CORTO: list = [
    # Español explícito
    "corto plazo", "cs corto", "cs duracion 0",
    # Ultrashort / low duration
    "ult sh term", "ul sh cor", "ul sh tr",
    "ult st t bd", "ul shor fix in",
    "invesco ult sh",
    # Bonos flotantes
    "float rate notes", "float rate nt",
    # Covered bonds
    "covered bond", "cov bond", "pfandbrief",
    # Short maturity / short duration names
    "euro bonds short", "eur bonds short",
    "euro st bnd", "eur st bnd",
    "sh durat crd", "short durat crd",
    "crdt vr shrt trm",
    "vontobel euro sh term",
    "ms short mat", "ms sicav short mat",
    "short mat bd",
    "eurozone flexib",
    "duracion 0-",
    "euro sh term b", "eur sh term b",
    "sisf euro st bnd",
    # Allianz floating rate / enhanced short term
    "allianz fl r nt", "allianz flo rate nots",
    "allianz g flt rt", "allianz glb flt",
    "allianz enh sh term", "allianz enhanc.shor",
    # Covered bonds con abreviatura
    "coverd bnd", "cov bnd", "cov bond",
    "nordea low dur europ", "nordea low dur eurp", "nordea lowdur",
    # Nuevos — análisis 527 restantes
    "sh durat bd", "eur sh durat bd",
    "dur bond",                                  # BGF EURO SHORT DUR BOND
    "fix inc horizon",                           # DWS Fixed Income Horizon 2026/2027
    "bgf eur sh durat",
    "euro bonds short",
    "bonds short",
    "esg euro bonds short",
    # Nuevos — análisis Restantes
    "shor.durat",                    # AXA WF EURO CREDI.SHOR.DURAT (OCR con punto)
    "crd shor dur",                  # AXA WF EURO CRD SHOR DUR
    "credi.shor",                    # AXA WF EURO CREDI.SHOR (variante OCR)
    "flo rate nts",                  # CANDRIAM FLO RATE NTS (floating rate notes)
    "flot rate nts",                 # variante OCR
    # Nuevos — análisis Restantes v2
    "dws float rate not",            # DWS FLOAT RATE NOTE (floating rate)
    "dws float r. note",             # variante OCR
    "float rate not",                # genérico floating rate notes
    "jpm euro gvrmnt short dur",     # JPM EURO GOVERNMENT SHORT DURATION
    "jpm eur gvrmnt short",          # variante
    "gl.short.dur.in",               # FIDELITY GL.SHORT.DURATION INCOME (OCR puntos)
    "fidelity gl.short",             # variante
]

NAME_SIGNALS_RF_FLEXIBLE: list = [
    # Bond genérico — patrones cortos (ordenados de más específico a más general)
    "euro bond", "eurobond", "eur bond",
    "corporate bond", "corp bond",
    "government bond", "gov bond", "govt bond",
    "high yield", " hy bond", " hy bd",
    "convertible bond", "convertibles",
    "aggregate bond",
    "emerging market debt", "em debt",
    "total return bond",
    "total ret",           # abbreviated "total return" in fund names (MFS, PIMCO-style)
    "flexible bond", "dynamic bond",
    # Alemán
    "renten",
    # Francés/fondo income
    "rend plus", "rendement",
    "convic crdit", "convic credit",
    # Maturity bond strategy
    "millesima", "millesim", "milles select",
    "cat bond",
    "financial bond", "financ bond",
    "stiftungsfonds",
    "securite eur",
    "global bond", "gl bond", "glob bond",
    "euro corp bond", "eur corp bond",
    "euro gov bond", "eur gov bond",
    # Gestoras específicas
    "pictet eur bond", "pictet eur corp", "pictet chf bond",
    "pictet usd gov", "pictet eur bonds", "pictet glob emrg debt",
    "pictet usd gov bond",
    "pimco euro bond", "pimco gl bond", "pimco tot ret", "pimco total ret",
    "pimco euro bond", "pimco glbl ig", "pimco glb ig", "pimco gl ig",
    "pimco glob ig", "pimco glbl ig credit", "pimco gl grad crd",
    "pimco unconstra", "pimco mtgage", "pimco low aver dur",
    "pimco div inc", "pimco diversif",
    "ishr gv bd", "ishr inv cp bd", "ishr em g bon",
    "vgd eu gov bd", "vgd euro gov bd", "vgd us gov",
    "vgd gbl bd", "vgd gbl bond", "vgd gbl sh term", "vgd gl st corp",
    "vgd us inv crd", "vgd us inv gde",
    "asian tiger bond",
    "sisf euro bond", "sisf euro gov bond", "sisf euro corp",
    "sisf euro high yield", "sisf euro st bnd",
    "sisf glob inflat", "sisf gl inflt", "sisf global infl",
    "sisf strategic", "sisf gl cred", "sisf glob cred", "sisf glbal cred",
    "sisf gl inflt lnkd", "sisf glob credit",
    "bnp euro bond", "bnp euro corp", "bnp euro gov",
    "bnp paribas e c bd", "bnp paribas e cr bd", "bnp paribas e h y",
    "bnp paribas ecbsp",
    "nordea europ covered bond", "nordea low dur europ",
    "nordea 1 europ coverd", "nordea 1 em corpor",
    "nordea 1 norw", "nordea 1 swdish", "nordea 1 swed",
    "nordea 1 swedish", "nordea 1 us corp",
    "robeco high yield",
    "ubam float rate",
    "la francaise",
    "r-co target", "r-co conv crdi",
    "rfmi multig",
    "ostrum sri euro bond",
    "candriam bond",
    "invesco euro bond", "invesco europ bond", "invesco multisect",
    "invesco glob tot ret",
    "invesco euro corp", "invesco european bond",
    "franklin euro high yield", "franklin euro hy",
    "franklin eu tot ret", "franklin euro gov",
    "ftgf west gl m strat", "ftgf wine gl fix",
    "templeton asia", "templeton asian bond",
    "templeton emer mkt bond", "templeton gl bond",
    "templeton gl tot ret", "templeton sust glb",
    "fidelity euro bond", "fidelity em mkt debt",
    "fidelity asian bond", "fidelity eur s-t bond",
    "fidelity euro shor", "fidelity f.eur bond",
    "fidelity f.int bond", "fidelity flex bond",
    "fidelity f.emer mkt debt", "fidelity strat bond",
    "fidelity us dollar bond",
    "bgf euro bond", "bgf gl hy bond", "bgf usd high yield",
    "bgf asian tiger bond", "bgf euro short durat",
    "bgf china bond", "bgf euro corp bond",
    "bgf fixed inc gl opp", "bgf fix inc gl",
    "bgf world bond", "bgf glob f asian tiger",
    "blackrock esg corp b", "blackrock esg f i st",
    "blackrock esg fis", "blackrock esgcorp",
    "blackrock esgfis", "bsf em mk flx",
    "is em g bon index", "ishr em mkt gv",
    "ishr eme mk gv", "ishr gv bd indx",
    "ishr inv cp bd indx",
    "ubs lux bond", "ubs bond eur", "ubs asia flexible",
    "ubs asian high yield", "ubs china fix",
    "ubs bd sicav shrt t",
    "ms sicav euro bond", "ms sicav euro corp",
    "ms sicav euro strat", "ms eu corp bd",
    "ms inv em mkt dbt", "mss emrging mkt debt",
    "ms short maturity",
    "mfs em mkt debt", "mfs m eme mkt debt",
    "mfs global opp bond", "mfs us gov bond",
    "mfs m.emer mkt debt",
    "m&g lux em market bond", "m&g lux euro corp bond",
    "m&g lux glob fr hy", "m&g gl flot rate hy",
    "janus h hf euro corp", "janus hend hor bond",
    "jpm em market debt", "jpm emrg mkt corp bd",
    "jpm us aggre bond", "jpm em markts debt",
    "jpm emrg mkt corp",
    "jupiter m emerg m debt", "jupiter m emerg",
    "gam star cat bond", "gam star gl rates",
    "gam str em mk b op",
    "gs euro long dur bond", "gs euromix bond",
    "gs euromix", "gs gl strat macr bd",
    "gs glob str macr bnd", "gs glob stra macro bnd",
    "gs green bond",
    "edmond rot mil",
    "edr bond alloc", "edr financial bonds",
    "carmignac cred", "carmignac credit",
    "carmignac p flex bon", "carmignac port flex bond",
    "carmignac prt fl bon", "carmignac securite",
    "carmignac pr.scurit",
    "deutsche invest asian bnds", "dws asian bonds",
    "dws asian bnds", "dws china bnds", "dws china bonds",
    "dws covered bond",
    "dws euro corp bnds", "dws euro corp bond",
    "dws euro corporate bond", "dws euro corporate bonds",
    "dws euro hy corp", "dws euro hy corporates",
    "dws inv as bond", "dws inv as bonds",
    "dws inv esg asian", "dws inv esg eu corp",
    "dws invest asian bonds",
    "dws strt esg aloc",
    "dws esg euro bonds",
    "dws esg dyn opport",
    "dws esg eurp smamid",
    "db fix inc opp",
    "fix inc horizon",
    "ssga eu cp bd esg",
    "asteria funds",
    "arcano inc esg", "arcano lowvo",
    "vontobel 24 strat", "vontobel gl act bond",
    "schroder glb crdt", "schroder isf corp",
    "schroder isf euro bond", "schroder isf sec c",
    "schroder isf sec cr",
    "af us sh term bond",
    "ab fix mat",
    "ab mortgage incom",
    "threadn lux cred", "threadneedle cred",
    # DWS Convertibles
    "dws esg convertibles", "dws convertibles",
    # Nuevos — análisis 527 restantes (tokens frecuentes no cubiertos)
    " bond ",                                    # genérico: "FIDELITY EURO BOND FUND"
    " bonds ",                                   # genérico: "AXA WF EUR STRAT BONDS"
    " bd ",                                      # abrev: "BGF CHINA BOND D2"
    " bnds ",                                    # abrev: "AXA WF GL INFLAT BNDS"
    " debt ",                                    # "FIDELITY EM MKT DEBT"
    ".debt",                                     # "PICTET EMERGING LOC.CUR.DEBT" (period-separated abbrev)
    "tot ret bnd",                               # "INVESCO GLOB TOT RET BND"
    " hy bnd ",                                  # "BGF USD HY BND"
    "corp bd",                                   # "JPM EMRG MKT CORP BD"
    "ig cred", " ig cr",                         # "PIMCO GLB IG CRED"
    "inv grade cr", "inv gde cr",                # Vanguard Investment Grade Credit
    "green bond", "act green",                   # AXA WF ACT GREEN (green bond fund)
    "env clim", "environ clim",                  # Invesco Environmental Climate
    "sub bond", "subordi",                       # DWS Corporate Hybrid / Subordinated
    "hybrid bond", "hybrid bnds",
    "buy&watch", "buy & watch", "buywat",
    "amundi rend plus", "amundi ult st t bd",
    "amundi ul sh tr bd", "amundi funds us bond",
    "amundi funds gbl sub",
    # Nuevos — análisis Restantes
    "inflat.bond", "inflat bond",    # AXA WF GLOBAL INFLAT.BONDS (inflation-linked)
    "global inflat",                 # AXA WF GLOBAL INFLATIO.BONDS
    "euro credit plus", "eur credit plus",  # AXA WF EUR CREDIT PLUS
    "medium trm bnd", "medium term bnd",  # BNP P. EUR MEDIUM TRM BND
    "euro corporat bnd",             # BNP P. EURO CORPORAT BND
    "sust eme mk",                   # CANDRIAM SUST EME MK (sustainable EM bond)
    "sust emerg",                    # variante
    "allianz green bond",            # ALLIANZ GREEN BOND
    "allianz euro crd",              # ALLIANZ EURO CRD SRI
    "allianz euro credit",           # ALLIANZ EURO CREDIT SRI
    "us high yie.bond",              # AXA WF US HIGH YIE.BOND (OCR con punto)
    "axa wf e m s d bn",             # AXA WF E M S D BN (EM Short Duration Bond)
    # Nuevos — análisis Restantes v2
    "fidelity china hy",             # FIDELITY CHINA HY (China high yield)
    "ab gb hy pf",                   # AB GLOBAL HIGH YIELD PORTFOLIO
    "dws inv corp gr bon",           # DWS INVEST CORPORATE GROWTH BONDS
    "dws inv esg eu hy",             # DWS ESG EU HIGH YIELD
    "dws eurorenta",                 # DWS EURORENTA (European bond)
    "dws float r",                   # DWS FLOAT RATE → RF_Corto ya cubierto
    "gs us dollar crdt",             # GS US DOLLAR CREDIT
    "gs us dollar credit",           # variante
    "axa sd hy low carbon",          # AXA SHORT DURATION HY LOW CARBON
    "pictet asia loc cur",           # PICTET ASIA LOCAL CURRENCY DEBT
    "pictet asian local cur",        # variante
    "amundi core eur gov bnd",       # AMUNDI CORE EUR GOVERNMENT BOND
    "jpm euro gov st dur",           # JPM EURO GOVERNMENT SHORT DURATION → ya en RF_Corto
    "invesco pan eu hi",             # INVESCO PAN EUROPEAN HIGH INCOME
    "invesco pn eur hi",             # variante OCR
    "carmignac prt credit",          # CARMIGNAC PORTFOLIO CREDIT
    "gam star crd",                  # GAM STAR CREDIT OPPORTUNITIES
    "gam star credit opp",           # variante
    # Análisis Restantes v3
    "ab americ inc portf",           # AB AMERICAN INCOME (Bloomberg US Agg)
    "ab americn inc port",           # variante OCR
    "ab fcp amer incm",              # variante
    "afs buy & wat inc",             # AFS BUY & WATCH INCOME (target maturity)
    "axa wf gib",                    # AXA WF GLOBAL INFLATION BOND REDEX
    "axa wf glb inf bn rdx",         # variante
    "candriam sus bn em m",          # CANDRIAM SUSTAINABLE BOND EM
    "candriam sustain bnd eur",      # CANDRIAM SUSTAINABLE BOND EUR
    "dws inv eurogov bnd",           # DWS INVEST EURO GOVERNMENT BOND
    "dws inv nzt eur c b",           # DWS NET ZERO TARGET CORP BOND
    "edr mill select",               # EDR MILLESIMA SELECT (target maturity credit)
    "fidelity f.europ.high y",       # FIDELITY EUROPE HIGH YIELD
    "fidelity us highyield",         # FIDELITY US HIGH YIELD
    "franklin strt inc",             # FRANKLIN STRATEGIC INCOME
    "ftgf west asian oppo",          # FTGF WESTERN ASSET ASIAN BOND
    "ftgf west asian",               # variante
    "fvs ii rentas",                 # FlossBach RENTAS (income bond)
    "gs em mks corp bnd",            # GS EM CORPORATE BOND
    "gs em mkt hard curr",           # GS EM HARD CURRENCY bond
    "gs gbl hy",                     # GS GLOBAL HIGH YIELD
    "templeton gl.t.ret",            # TEMPLETON GLOBAL TOTAL RETURN
    "templeton glb tot ret",         # variante
    "templeton glo tot ret",         # variante
    "templeton glob.total ret",      # variante
    "bnp paribas e jpm segdctp",     # BNP JPM SECURITIZED
    "axa wf e mk s d b",             # AXA WF EM SHORT DURATION BOND
    # ── Fase 1C: patrones para residuales + corrección typo ──────────────
    "r-co conv credi",                   # R-CO CONV CREDI EURO (fix typo: era "crdi")
    "us aggregate",                      # JPM US AGGREGATE BND
    "euro aggre",                        # JPMORGAN EURO AGGRE
    "srt dr bnd",                        # JPM GBL SRT DR BND (short duration bond)
    "glb bnd opp",                       # JPM GLB BND OPP (global bond opportunities)
    "meridi eur cred",                   # MFS MERIDI EUR CRED
    "em eu m ea",                        # FIDELITY EM EU M EA AF (EMEA multi-asset)    
    # ── Fondo de Rente Fija con SRRI=4
    "jpm gl bond opp",
    # BL-RFF-EX9 (2026-07-13): EM currency / EM sovereign debt patterns whose
    # name alone does not signal FI (no "bond"/"debt"/etc.) but benchmarks are
    # unambiguously Fixed Income (JP Morgan EMBI / Morningstar EM Sovereign Bond).
    # "em mkt currency": GS EM MKT CURRENCY X EURH ACC (EM sovereign + FX carry).
    "em mkt currency",
]

NAME_SIGNALS_MIXTO: list = [
    "patrimoine", "patrimoin",
    "m. expert",
    "conservador fi", "moderado fi", "crecimiento fi",
    "prem equilib",
    "str fund blced", "str fund yield",
    "strat fund blced",
    "patrim bal", "patrim def", "patrim agressiv",
    "glbal balncd",
    "patrimonial def", "patrimonial bal",
    "global resili", "glob resili",
    "us balancd",
    "fidelity ma dyn",
    "templeton gb val",
    "ubs str fund",
    "ab american inc",
    "balancd",                       # JANUS H US BALANCD 2026 (balanced)
    "m&g optimal inc",               # M&G OPTIMAL INCOME (multi-asset income)
    "fvs m asset", "fvs multi", "fvs multiple",
    "fvs ii equilib",
    # DB / Deutsche SAA y multi-opp
    "db cnsrvativ saa", "db balancd saa", "db best all",
    "db sia balanc", "db sia consrvtv", "db sia eur",
    "db sia usd", "db priv markt",
    "deutsche multiopport", "dws multi opp",
    # DWS Kaldemorgen (famous multi-asset)
    "kaldemorgen", "dws concept kalde", "dws cncpt kalde",
    "dws con kalde", "dws con.kal",
    "dws invest cons opp",
    # BGF Multi-Asset
    "bgf esg multiass", "bgf gl m asset inc",
    "bgf dyn high inc",
    "bsf gl event driv",
    # DWS ESG Climate / Real Assets
    "dws inv esg clim op", "dws inv esg real as",
    "dws esg blue eco", "dws c.esg blue",
    "dws strt esg aloc",
    # GS Patrimonial
    "gs patrim bal", "gs em debt",
    "gs glob hy ocs", "gs glob hy",
    # JPM Global Bond / Corp Bond
    "jpm glob corp bond",
    "jpm global corpo", "jpmorgan gl.corp",
    # Otros
    "bsf em cies", "m&g dyn alloc",
    "m&g episode macro", "m&g glob convrtb",
    "allianz dmas sri", "allianz dy st sri",
    "janus.h. us forty", "janus h us forty",
    "r-co valor", "r-co thematic",
    # "guinness gl eq inc" removed 2026-07-13: Guinness Global Equity Income is
    # pure equity (KIID-Capa1 → Renta Variable); "guinness gl eq" already in
    # NAME_SIGNALS_RV as backup for name-only fallback. Stale mixto signal.
    "allianz strategy",
    # Allianz Orient Income / Mixto income-oriented
    "allianz orient inc",
    # AF Pioneer Flexible Opportunities
    "af pioneer flexible",
    # Multicop Sicav (multi-asset CH)
    "multicop sicav",
    # JPM Global Macro (macro multi-asset)
    "jpm glob macro", "jpm us sh duration",
    # Carmignac Patrimoine (multi-asset conservador)
    "carmignac pfl ptr", "carmignac prtfl ptr",
    "carmignac emergi. patrim", "carmignac emerg.patrim",
    "carmignac prt ptr",
    # Nuevos — análisis Restantes
    "carmignac patrim",              # CARMIGNAC PATRIM A USDHDG (variante OCR corta)
    "allianz inc & grow",            # ALLIANZ INC & GROW (income & growth multi-asset)
    "allianz inc & growt",           # variante OCR
    "db cnsrvatv saa",               # DB CNSRVATV SAA (conservative SAA)
    "db sia consvtv",                # DB SIA CONSVTV (conservative)
    "dje gestion patrimon",          # DJE GESTION PATRIMONIAL
    # Análisis Restantes v3
    "allianz dy ma stg",             # ALLIANZ DYNAMIC MULTI-ASSET STRATEGY
    "amundi protect 90",             # AMUNDI PROTECT 90 (capital protection)
    "dws fm esg m.a.def",            # DWS MULTI-ASSET DEFENSIVE 2026
    "dws esg stftngsfds",            # DWS STIFTUNGSFONDS (balanced foundation)
    "dws fund esg garant",           # DWS ESG GARANT (guaranteed mixed)
    "fidelity mltasset inc",         # FIDELITY MULTI-ASSET INCOME
    "fidelity target 202",           # FIDELITY TARGET 2025/2030 (lifecycle)
    "franklin us mangd inc",         # FRANKLIN US MANAGED INCOME
    "gs (l) patrim",                 # GS PATRIMOINE
    "fvs ii rentas rt",
    "balanced",          # JANUS US BALANCED 2026,              # FlossBach RENTAS II (balanced income)
]


NAME_SIGNALS_RV: list = [
    "akkumula", "deutschland", "aktien", "aktn st",
    "wellcare", "smart ind tec",
    "artificial intelligenc",
    "osteuropa", "russia",
    "silver age", "silverplus",
    "thematic silverplus", "thematic real estat",
    "pictet water", "pictet digital",
    "global brands", "glob brands",
    "global focus", "glob focus",
    "carmignac investis", "carmignac invest",
    "carmignac emergent", "carmignac grand",
    "carmignac grand eurp",
    "fidelity america", "fidelity germany",
    "fidelity ital", "fidelity iberia",
    "fidelity world fund", "fidelity greater china",
    "fidelity latin", "fidelity gl finan",
    "fidelity glb consum", "fidelity glob indust",
    "fidelity f jap", "fidelity f asi",
    "gs eur eq", "gs eurozone eq", "gs em mkt eq",
    "gs japn eq", "gs gbl eq",
    "dws india", "dws invest top",
    "dws invest top asia", "dws invest top euroland",
    "dws invst esq tp eurlnd",
    "dws esg eurp smamid cap",
    "r-co valor", "r-co thematic",
    "h2o adagio sp",
    "nordea glob stbl equi",
    "liontrust gf str eq",
    "cpr silver age",
    "guinness gl eq",
    "harris ass glbal eq",
    "polar c gl insur", "polar glob insur",
    "polar cap artif intel", "polar capital bio",
    "polar capital gbl tch",
    "templeton gl clima", "templeton eastern euro",
    "trowe price us", "trowe px glob foc", "trowe px us blue",
    "gqg partner em",
    "robeco emerg market eq",
    "magna fiera cap", "magna mena",
    "optimized eq incom",
    "morgan st glbal brands", "morganstanley us grow",
    "ms em eurp mideast", "ms in f asia",
    "findlay park",
    "janus h emerg mkt",
    "janus h hf peur sm com",
    "janus hh gl tec lead", "janus paneu sm comp",
    "janus h paneu sm comp",
    "janus h hf peur prop",
    "pictet glb env oppts",
    "pictet security",
    # BGF fondos sin keyword equity explícito
    "bgf asian dragon", "bgf latin americ",
    "bgf asian grow lead", "bgf asian grw lead",
    "bgf cont europ flex", "bgf contin.europ",
    "bgf eur eqity trans", "bgf euro market",
    "bgf europ.special", "bgf future transport",
    "bgf jap sm mid", "bgf japan smallmid",
    "bgf next gen tec", "bgf syst glb small",
    "bgf united kingdom", "bgf us basic val",
    "bgf us flexible eq", "bgf us opportunit",
    "bgf us small & mid", "bgf world energy",
    "blackrock gbl uncnst",
    # DWS / Allianz equity sin keyword
    "dws artif intellig", "dws inv artif intel",
    "dws inv artif intell",
    "allianz china a-shr", "allianz china a sh",
    "allianz thematica", "allianz mult ast fut",
    "allianz eu eq grow",
    # Otros equity sin keyword
    "gam m luxury brand", "gam ms luxury brand",
    "janus us sm cap val",
    # HSBC GIF equity (sin keyword equity en nombre)
    "hsbc asia ex jap", "hsbc gif asia ex",
    "hsbc gif brazil", "hsbc gif chinese",
    "hsbc gif euroland", "hsbc gif frontier",
    "hsbc gif hong kong", "hsbc gif indian",
    "hsbc gif idian", "hsbs gif thai",
    "hsbc gif thai",
    # Templeton Japan — equity sin keyword
    "templeton japan",
    # GAM Luxury Brands (typo brnds)
    "gam m luxury brnds", "luxury brnds",
    # BGF variantes con typos OCR
    "bgf futur transport", "bgf future of transport",
    "bgf us flexibl eq",
    # Amundi Polen (growth equity)
    "amundi polen",
    # Azvalor Blue Chips
    "azvalor blue chips",
    # Nuevos — análisis Restantes (señales equity sin keyword explícito)
    "croci",                         # DWS CROCI (factor equity, Deutsche)
    "top div",                       # DWS ESG EU TOP DIV / DEUTSCHE EUROP TOP DIVIDEND
    "top dividend",                  # variante completa
    "world financials",              # BGF WORLD FINANCIALS
    "world tchnlgy",                 # BGF WORLD TCHNLGY (OCR)
    "world healtscnc",               # BGF WORLD HEALTSCNC (OCR)
    "dynam eq",                      # BGF SUST GL DYNAM EQ
    "sust gl dynam",                 # variante
    "glob real estat sec",           # DEUTSCHE GLB REAL ESTAT SEC
    "amundi indx",                   # AMUNDI INDX MSCI WORLD / EU CORP (indexed)
    "amundi ind msci",               # AMUNDI IND MSCI WRLD
    "amundi s&p 500 scr",            # AMUNDI S&P 500 SCRND (screened index)
    "amundi sp500",                  # variante OCR
    "amundi m nam",                  # AMUNDI M NAM ESG (North America equity)
    "amundi msci na",                # AMUNDI MSCI NA ESG
    "amundi core msci",              # AMUNDI CORE MSCI EM MKTS (indexed equity)
    "amundi core msc",               # variante OCR
    "vgd esg em mkt eq",             # VGD ESG EM MKT EQ INDX (Vanguard indexed equity)
    "low vol world",                 # DEUTSCHE QNT LOW VOL WORLD
    "qnt low vol",                   # variante
    "emrging mkt top div",           # DWS EMRGING MKT TOP DIV
    "us top divid",                  # DEUTSCHE II US TOP DIVID
    "us top dividend",               # variante completa
    "glob environment",              # BNP GLOB ENVIRONMENT (clean energy equity)
    "clean en sol",                  # BNP FUND CLEAN EN SOL (clean energy)
    "sust gbl eqy",                  # BNP P. SUST GBL EQY
    "enhan in eq", "enh in eq",      # AXA enhanced index equity
    "us en in eq", "us enh in eq",   # AXA US enhanced index equity
    "us eq alpha",                   # AXA R US EQ ALPHA
    "switzerland eq",                # AXA WF SWITZERLAN EQ
    "switzerland a acc",             # variante
    "candriam eq l",                 # CANDRIAM EQ L ONCO / EURP INN (equity long)
    "europ top div",                 # variante
    "eur mdium trm",                 # no — esto es RF, quitar
    "ab select us eq",               # AB SELECT US EQ (AllianceBernstein US equity)
    "ab low volatlity eq",           # AB LOW VOLATILITY EQ
    "ab sust. gl. thematic",         # AB SUST GL THEMATIC (global equity)
    "deutsche europ top",            # DEUTSCHE EUROP TOP DIVIDEND
    "deutsche ii glb eq",            # DEUTSCHE II GLB EQ
    "deutsche ii us top",            # DEUTSCHE II US TOP DIVID
    "dws emrging mkt top",           # DWS EMRGING MKT TOP DIV
    "dws croci",                     # DWS CROCI (ya cubierto por "croci")
    "dws dje alpha",                 # DWS DJE ALPHA RNTN (absolute return equity)
    "dws esg eu top div",            # DWS ESG EU TOP DIV
    "dws esg gl em eq",              # DWS ESG GL EM EQ
    "dws esg gen infras",            # DWS ESG GEN INFRAST (infrastructure equity)
    "dws critic tec",                # DWS CRITIC TEC (critical tech equity)
    "bgf world",                     # BGF WORLD sector funds (financials, tech, health)
    "bgf sust gl",                   # BGF SUST GL DYNAM EQ
    "azvalor internat",              # AZVALOR INTERNAT (value equity)
    "ct lux sust glb eq",            # CT LUX SUST GLB EQ INC
    "fidelity fast em mkt",          # FIDELITY FAST EM MKT (EM equity)
    "sisf gl em mkt",                # SISF GL EM MKT OPPO (EM equity)
    "gs em mkt currency",            # GS EM MKT CURRENCY (EM currency/bond — RF_Flex)
    "jpm em mkt sma cap",            # JPM EM MKT SMA CAP (EM small cap equity)
    # Fix inconsistencias fund_family_builder
    "amundi euroland eq",            # FAM_000121: RV no Monetario
    "jpm us value",                  # FAM_001697: RV Value no Alternativo
    "thematics",                     # BL-RV-IN4: Natixis Thematics equity brand (all sub-funds: AI & Robotics, Safety, Subscription Economy, etc.)
    "gs us equity",                  # FAM_001385/386: RV con/sin hedge
    "templeton global income",       # FAM_001293: RV Income no Mixtos
    "gs gbl eq income",              # FAM_001343: RV Income no Mixtos
    "smart food",                    # FAM_000513: BNP SMART FOOD → RV temática (food)
    "bnp p. smart food",             # variante con punto OCR
    # Nuevos — análisis Restantes v2 (350+ fondos identificados)
    "trowe px", "trowe price",       # T. Rowe Price equity
    "jpm us select eq", "jpm us slct eq",
    "jpm china a shar",              # JPM China A Shares
    "jpm us small cap grow", "jpm us sm cap",
    "jpm asia pacific eq", "jpm asia pacif",
    "jpm emerg mkt eq", "jpm emerg mkt opp",
    "jpm greater china", "jpm japan esg eq",
    "jpm them gen ther",             # JPM Thematic Genomics
    "fidelity asia pac opp", "fidelity asia small",
    "fidelity china consmr", "fidelity china cons",
    "fidelity f as eq",              # Fidelity Asia ESG Equity
    "fidelity f ftr conn",           # Fidelity Future Connectivity
    "fidelity f glb dv",             # Fidelity Global Dividend Plus
    "fidelity f wt &w", "fidelity f wter",   # Fidelity Water & Waste
    "fidelity japan indx", "fidelity msci world",
    "fast asia",                     # Fidelity FAST Asia
    "robeco sust water",             # Robeco Sustainable Water
    "robeco smart energy",           # Robeco Smart Energy
    "robeco smart mobilit",          # Robeco Smart Mobility
    "robeco bp gl",                  # Robeco BP Global Premium Equity
    "pictet jap eq",                 # Pictet Japan Equity
    "pictet china",                  # Pictet China Equity
    "pictet clean en",               # Pictet Clean Energy Transition
    "pictet nutrition",              # Pictet Nutrition (thematic)
    "pictet premium brands",         # Pictet Premium Brands
    "pictet gl megatr",              # Pictet Global Megatrend
    "nordea 1 em s steqf", "nordea 1 gl s steqf",
    "nordea 1 gl clim",              # Nordea Global Climate & Environment
    "nordea 1 glob stbl equi",       # Nordea Global Stable Equity
    "schroder isf china", "sisf china",
    "sisf asian opp",                # SISF Asian Opportunities
    "sisf frontier mkt eq",          # SISF Frontier Markets Equity
    "sisf us smal&mid cap eq",       # SISF US Small & Mid Cap
    "sisf europ.divid",              # SISF European Dividend
    "schroder isf g a e",            # Schroder ISF Global Alt Energy
    "dws inv esg clm", "dws inv esg dyn",
    "dws inv esg top", "dws inv esg gl",
    "dws concept dje",               # DWS Concept DJE
    "dws gbl infrastr", "dws invest glbl infras",
    "janus h hor gl",                # Janus Henderson Horizon Global
    "janus h hf euroland", "janus h hf peur",
    "janus h gl tec", "janus h gl sm f",
    "ubs lux dig hlth", "ubs lux dig hlt",
    "ubs ai and rob", "ubs lux ai",  # UBS AI & Robotics
    "ubs lux sec eq",                # UBS Security Equity
    "ms em leaders equ",             # MS EM Leaders Equity
    "mss asia opportunity",          # Morgan Stanley Asia Opportunity
    "harris ass. glbal",             # Harris Associates Global Equity
    "first eagle amundi int",        # First Eagle Amundi International
    "first eag amnd int",            # variante OCR (amnd=amundi)
    "first eag amun int",            # variante OCR
    "first eag.amun.int",            # variante OCR con punto
    "ftgf clearbridge", "ftgf r us sm cap",
    "ftgf wine gl", "ftgf put lg cap val",
    "gs gl futur gen eq",            # GS Global Future Generation
    "gs gbl core eqy",               # GS Global Core Equity
    "gs em mrkt eq",                 # GS EM Market Equity
    "invesco gl cons tr",            # Invesco Global Consumer Trends
    "invesco pan eu sys eq",         # Invesco Pan European Systematic
    "invesco pn eur eq",             # Invesco Pan European Equity
    "templeton frntr mkt",           # Templeton Frontier Markets
    "franklin u.s. opp",             # Franklin US Opportunities
    "index msci world",              # Index MSCI World
    "medtch", "medtech",           # Vontobel MedTech equity
    # ── Fase 1C: patrones para 25 residuales ────────────────────────────
    "stk",                               # Vanguard: VANG PAC EXJAP STK
    "stk indx",                          # Vanguard: VGD US 500 STK INDX
    "us 500 st index",                   # Vanguard: VGD US 500 ST INDEX
    "pac exjap",                         # Vanguard: VANG PAC EXJAP
    "ashare",                            # JPM CHINA ASHARE OPP
    "china a-share",                     # variante
    "genetic therap",                    # JPM GENETIC THERAP
    "eur strat grow",                    # JPMORGAN EUR STRAT GROWT
    "strat grow",                        # variante corta
    "gbl div a",                         # FIDELITY GBL DIV A (equity dividend)
    "gl dividend",                       # FIDELITY GL DIVIDEND
    "glo divdnd",                        # FIDELITY GLO DIVDND
    "divdnd",                            # abreviación genérica dividendo
    "glbl infrastr",                     # DWS GLBL INFRASTR
    "glob infrastr",                     # variante
    "emergng mkts opp",                  # JPM EMERGNG MKTS OPP
    "emerg.mark.opport",                 # JPM EMERG.MARK.OPPORT (OCR con puntos)
    "por tc sol",                        # CARMIGNAC POR TC SOL    
    # P08: fondos con nombre inequívoco de RV
    "us forty",
    "euroland eq",
    "smart food",
    "global technology",
    "gbl tech",
    "gbl tch",
    
]


NAME_SIGNALS_ALTERNATIVO: list = [
    "absolute return", "abs ret", "absret",
    "arb strat", "arbit strat",
    "tiede", "tiedm",
    "lyxor t arb", "lyxor t arbit",
    "candriam index arbi",
    "gam star alpha spe",
    "gam star gl rates",
    "h2o adagio r ",
    "jupiter m. glb eq abs ret",
    "jupiter m. emerg m",
    "jupiter st absret",
    "pimco multiassut",
    "pimco bal inc grw",
    "schroder isf sec crd", "schroder isf sec c",
    # BSF / BlackRock absolute return
    "blackrock sf europ",
    "bsf em cies abs ret",
    "bsf europ.opp.ext",
    # EDR Millesima (target maturity bond — Alt border)
    "edr millesim", "edmond rot mil",
    # Carmignac Patrimoine — se mantiene en Mixtos pero la variante
    # "portfolio patrimoine" con exposición absoluta va aquí
    "carmignac pfl ptr", "carmignac prtfl ptr",
    # Nuevos — análisis Restantes
    "enhanc comod",                  # CTHREAD ENHANC COMOD (enhanced commodity)
    "enhanced comod",                # variante completa
    "dws enh commdty",               # DWS ENH COMMDTY STRT (enhanced commodity)
    "bsf europ opp ext",             # BSF EUROP OPP EXTENSION (absolute return)
    "fram dig ecom",                 # AXA WF FRAM DIG ECOM (digital economy long/short)
    "fram dig econ",                 # variante
    # Nuevos — análisis Restantes v2
    "franklin alt st",               # FRANKLIN ALT STRATEGIES (multi-strategy)
    "janus h glob ls",               # JANUS HENDERSON GLOBAL LONG/SHORT
    "janus h hf",                    # JANUS HENDERSON HEDGE FUND long/short
    "nordea 1 alpha 10",             # NORDEA ALPHA 10 MA (vol-target 10%)
    "nordea 1 stable ret",           # NORDEA STABLE RETURN (multi-asset AR)
    "nordea 1 stable retu",          # variante OCR
    "schroder gaia bluetr",          # SCHRODER GAIA BLUETREND (CTA sistemático)
    "schroder gaiablue",             # variante
    "mfs prudent capital",           # MFS PRUDENT CAPITAL → mixtos/AR
    "thread enhanc commod",          # THREAD ENHANC COMMOD (ya en lista)
    "invesco balan.risk",            # INVESCO BALANCED RISK ALLOCATION
    # Análisis Restantes v3
    "gs q bbg comm. index",          # GS Q BLOOMBERG COMMODITY INDEX
    "gs q comm ix prtf",             # variante OCR
    "gs q m st bbg cm",              # variante OCR
    # Análisis Restantes v4
    "nordea 1 active rts opt",       # en RF_Flex pero no en Alt (activo tasas interés)
    "nordea 1 alpha 15",             # NORDEA ALPHA 15 (vol target multi-asset)
    "nordea 1 active",               # variante
    "gamco merger arbit",            # GAMCO MERGER ARBITRAGE (event-driven)
    "thread.gl dy rl re",            # THREADNEEDLE GLOBAL DYNAMIC REAL RETURN
    "thread.glob dynam real",        # variante
    # ── Nuevas señales P1 ──────────────────────────────────────────────────────
    "bsf europ opp ext",             # BSF EUROP OPP EXTENSION (equity extension strategy)
    # P08: fondos de volatilidad (AMUNDI VOLATILITY)
    "volatility",
    "volatilidad",
    "volatilit",  # nombre truncado en AMUNDI VOLATILIT WLD    
]

NAME_SIGNALS_ESTRUCTURADO: list = [
    "autocall", "capital protected",
    "capital protection", "capital guarantee",
]


def _name_match(name_l: str, signals: list) -> bool:
    return any(s in name_l for s in signals)



# ============================================================
# detect_nature_from_kiid — ventana correcta 1200-4500
# ============================================================

_WINDOW_OBJ_START = 1200   # inicio sección objetivo de inversión (KIID clásico)
_WINDOW_OBJ_END   = 4500   # fin sección objetivo / inicio riesgos (KIID clásico)
_WINDOW_COST_START = 9000  # inicio sección costes
_WINDOW_COST_END   = 14000 # fin sección costes

# Ventanas por formato de documento
# KIID clásico (UCITS pre-2023): objetivo en 1200-4500
# DDF/PRIIPs  (post-2023):       objetivo en 200-2000 (sección "Finalidad" temprana)
# UNKNOWN:                        ventana amplia 200-4500 por seguridad
_WINDOWS_BY_FORMAT: dict = {
    "KIID":    (1200, 4500),
    "DDF":     (500,  5000),   # Ampliado a 5000: algunos DDF tienen objetivo en pos 4500-4800
    "UNKNOWN": (200,  4500),
}



# ============================================================
# detect_nature_from_name — fuente única para todos los bloques
# ============================================================

def detect_nature_from_name(name_l: str) -> Optional[str]:
    """
    Detecta la naturaleza del fondo solo desde el nombre (en minúsculas).
    Devuelve el valor interno ('Monetario', 'RF_Corto', 'RF_Flexible',
    'Renta Variable', 'Mixtos', 'Alternativo', 'Estructurado') o None.

    Orden: Estructurado > Alternativo > Monetario > RF_Corto >
           Mixtos > RF_Flexible > Renta Variable
    (RF_Flexible antes de RV para evitar que 'bond' en nombres temáticos
     bloquee la detección de equity)
    """
    if _name_match(name_l, NAME_SIGNALS_ESTRUCTURADO):
        return "Estructurado"
    if _name_match(name_l, NAME_SIGNALS_ALTERNATIVO):
        return "Alternativo"
    if _name_match(name_l, NAME_SIGNALS_MONETARIO):
        return "Monetario"
    if _name_match(name_l, NAME_SIGNALS_RF_CORTO):
        return "RF_Corto"
    if _name_match(name_l, NAME_SIGNALS_MIXTO):
        return "Mixtos"
    if _name_match(name_l, NAME_SIGNALS_RF_FLEXIBLE):
        return "RF_Flexible"
    if _name_match(name_l, NAME_SIGNALS_RV):
        return "Renta Variable"
    return None


# ============================================================
# detect_nature_from_prefilter — patrones de PRE-FILTRO de bloque
# (OPT-B2 2026-07-16, R-1/P#11 DRY)
# ============================================================
# Fuente única de los patrones include/exclude que cada bloque usaba en su
# get_universe_isins() (el "plano heurístico" / Set #1). Estos patrones son
# la familia de señales de nombre FIABLE y calibrada por grupo de fondos
# (a diferencia de NAME_SIGNALS_*, que son abreviaturas raras de nombres
# concretos — Set #2 —, más finas y con menor cobertura de nombres genéricos).
#
# Cada predicado replica EXACTAMENTE el is_candidate() del bloque homónimo.
# Los bloques deben pasar a llamar a estos predicados (paso 1b) para eliminar
# la duplicación; de momento esto es puramente aditivo (riesgo cero).
#
# detect_nature_from_prefilter() recorre los predicados en orden de CLARIDAD
# (patrones más específicos / con excludes más estrictos primero; Mixtos, el
# más genérico y conflictivo, el último) y devuelve la PRIMERA coincidencia.
# Esto implementa la intención de diseño original ("empezar por los grupos de
# patrones más claros, terminar por los más propensos a conflicto") y corrige
# el bug legacy last-wins (COALESCE) por el que Mixtos sobrescribía a Renta
# Variable — el mismo que INTER-DBLCLAIM parcheaba. Es una señal de peso medio
# en el clasificador ponderado por evidencia; el orden no es determinante y se
# valida contra el baseline de volatilidad realizada (srri_nav).

# --- Monetarios ---
_PREFILTER_MON_INCLUDE = [
    "money market", "monetary",
    "money mkt", "money mket",
    "euro m mkt", "eu m mkt", "standard mm vnav", "lqudty lvnav",
    "inscash", "gbp liq lvnav", "gbp liq cnav", "usd treasur cnav",
    "usd liq cnav", "fidelity euro cash", "fidelity fund us cash",
    "fidelity us cash", "cash fund", "cash management", "treasury",
    "tresorerie", "ucits mmf", "mmf",
    # FIX-MON-LIQ (2026-07-18): denominaciones genéricas de fondos de liquidez
    "euro liquidity",                # SISF EURO LIQUIDITY + variantes (vol=1)
    "dollar liquidity",              # SISF US DOLLAR LIQUIDITY (vol=4 en EUR NAV)
    "usd liquidity",                 # alias
]
_PREFILTER_MON_EXCLUDE = [
    "short duration", "ultra short", "short term",
    "bond", "income", "enhanced", "plus",
]

def _prefilter_match_monetario(name_l: str) -> bool:
    if any(p in name_l for p in _PREFILTER_MON_EXCLUDE):
        return False
    return any(p in name_l for p in _PREFILTER_MON_INCLUDE)

# --- RF Corto ---
_PREFILTER_RFC_INCLUDE = [
    "short duration", "ultra short", "short term bond", "short term",
    "low duration", "floating rate", "floating", "money plus", "enhanced cash",
    "ab mort income",
]
_PREFILTER_RFC_EXCLUDE = [
    "money market", "monetary", "liquidity", "cash ",
    "equity", "balanced", "allocation", "multi asset", "multi-asset",
    "absolute return",
]

def _prefilter_match_rf_corto(name_l: str) -> bool:
    if any(p in name_l for p in _PREFILTER_RFC_EXCLUDE):
        return False
    return any(p in name_l for p in _PREFILTER_RFC_INCLUDE)

# --- RF Flexible ---
_PREFILTER_RFF_INCLUDE = [
    "flexible bond", "dynamic bond", "strategic bond",
    "total return bond", "total return", "unconstrained",
    "absolute return bond", "multi sector bond", "multisector bond",
    "opportunistic bond", "global bond", "income bond",
    "tactical bond", "active bond",
    "bnd", "bd indx", "corp indx", "govt indx",
    "1-5 ind", "1-5 idx", "gvt indx",
    "high yield", "high yiel", ".h.y.", "high yie.",
    "ubs glob dynamic", "db fixed income", "bsf em flex dynamic",
    "amundi str income", "jupiter dynamic",
    "pimco diver", "pimco esg income",
]
_PREFILTER_RFF_EXCLUDE = [
    "money", "monetary", "liquidity", "cash",
    "short duration", "ultra short", "short term", "low duration",
    "floating rate", "equity", "balanced", "allocation",
    "multi asset", "multi-asset",
]

def _prefilter_match_rf_flexible(name_l: str) -> bool:
    # BL-RFF-IN4b: "edr bond alloc" antes del exclude "allocation".
    if "edr bond alloc" in name_l:
        return True
    if any(p in name_l for p in _PREFILTER_RFF_EXCLUDE):
        return False
    if re.search(r'\bhy\b', name_l):
        return True
    return any(p in name_l for p in _PREFILTER_RFF_INCLUDE)

# --- Renta Variable ---
_PREFILTER_RV_INCLUDE = [
    "equity", "equities", "shares", "stock",
    "accion", "acciones",
    "technology", "tech", "health", "healthcare",
    "climate", "clean energy", "renewable",
    "value", "growth", "quality", "income",
    "emerging", "europe", "usa", "global",
    "ishares",
    "dws esg dynamic opp", "dws esg dyn opport",
    "thematics",
]
_PREFILTER_RV_EXCLUDE = [
    "money", "monetary", "liquidity", "cash",
    "bond", "fixed income", "renta fija",
    "balanced", "allocation", "multi asset",
    "absolute return", "hedge", "alternative",
    "bnd", "bd indx", "corp indx", "govt indx",
    "1-5 ind", "1-5 idx", "gvt indx",
    "high yield", "high yiel",
    "pimco diver", "pimco esg income", "ab mort income", "amundi str income",
    "jpm income", "jpm global income",
    "debt", "gov idx", "gov index",
]
_PREFILTER_RV_EXCLUDE_PREFIX = [
    r'\bconver', r'\balloc', r'\btempleton.*total',
]

def _prefilter_match_renta_variable(name_l: str) -> bool:
    if any(p in name_l for p in _PREFILTER_RV_EXCLUDE):
        return False
    if re.search(r'\bhy\b', name_l):
        return False
    if any(re.search(p, name_l) for p in _PREFILTER_RV_EXCLUDE_PREFIX):
        return False
    for p in _PREFILTER_RV_INCLUDE:
        if p == "shares":
            if re.search(r'\bshares\b', name_l):
                return True
        else:
            if p in name_l:
                return True
    return False

# --- Mixtos (regex) ---
_PREFILTER_MIXTOS_INCLUDE_RE = re.compile(
    r"""
    balanced|
    multi[\s-]?asset|
    alloc\w*|
    conver\w*|
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
_PREFILTER_MIXTOS_EXCLUDE_RE = re.compile(
    r"""
    templeton\s+(?:asian\s+)?growth|
    templeton\s+growth|
    ms\s+sicav\s+us\s+growth|
    ms\s+invf\s+us\s+growth|
    mss\s+us\s+growth|
    dws\s+esg\s+eq(?:uity)?\s+income|
    dws\s+us\s+growth|
    ubs\s+usa\s+growth|
    dws\s+esg\s+dynamic\s+opp|
    pimco\s+dynamic\s+bond|
    pimco\s+diver|
    pimco\s+esg\s+income|
    candriam\s+bonds\s+total\s+return|
    jupiter\s+dynamic|
    ubs\s+glob\s+dynamic|
    db\s+fixed\s+income|
    ab\s+mort\s+income|
    amundi\s+str\s+income|
    bsf\s+em\s+flex\s+dynamic|
    edr\s+bond\s+alloc|
    pictet\s+fixed\s+income
    """,
    re.IGNORECASE | re.VERBOSE,
)

def _prefilter_match_mixtos(name_l: str) -> bool:
    if not _PREFILTER_MIXTOS_INCLUDE_RE.search(name_l):
        return False
    return not _PREFILTER_MIXTOS_EXCLUDE_RE.search(name_l)

# --- Alternativos ---
_PREFILTER_ALT_INCLUDE = [
    "absolute return", "hedge fund", "long short", "long/short",
    "market neutral", "relative value", "arbitrage", "global macro",
    "glob macro",
    "alpha 10 ma", "alph 10 ma",
    "managed futures", "cta", "systematic", "multi strategy",
    "multi-strategy", "alternative", "real assets", "real estate",
    "property", "infrastructure", "commodities", "commodity",
]
_PREFILTER_ALT_EXCLUDE = [
    "equity", "bond", "fixed income", "renta fija",
    "balanced", "allocation", "multi asset", "multi-asset",
]

def _prefilter_match_alternativo(name_l: str) -> bool:
    # BL-ALT-IN1: "pictet fixed income" antes del exclude "fixed income".
    if "pictet fixed income" in name_l:
        return True
    if any(p in name_l for p in _PREFILTER_ALT_EXCLUDE):
        return False
    return any(p in name_l for p in _PREFILTER_ALT_INCLUDE)

# Orden de claridad (más específico → más genérico). Primera coincidencia gana.
_PREFILTER_ORDER = [
    ("Monetario",              _prefilter_match_monetario),
    ("Alternativo",            _prefilter_match_alternativo),
    ("Renta Fija Corto Plazo", _prefilter_match_rf_corto),
    ("Renta Fija Flexible",    _prefilter_match_rf_flexible),
    ("Renta Variable",         _prefilter_match_renta_variable),
    ("Mixtos",                 _prefilter_match_mixtos),
]


def detect_nature_from_prefilter(name_l: str) -> Optional[str]:
    """
    Naturaleza según los patrones de pre-filtro de bloque (Set #1), en orden
    de claridad, primera coincidencia gana. Devuelve el nombre canónico de
    Fund_Nature ('Monetario', 'Renta Fija Corto Plazo', 'Renta Fija Flexible',
    'Renta Variable', 'Mixtos', 'Alternativo') o None si ningún bloque reclama
    el nombre (equivalente al universo 'restantes' residual).

    name_l debe venir en minúsculas.
    """
    if not name_l:
        return None
    for nature, predicate in _PREFILTER_ORDER:
        if predicate(name_l):
            return nature
    return None


# ============================================================
# detect_nature_from_kiid — ventana correcta 1200-4500
# ============================================================

_WINDOW_OBJ_START = 1200   # inicio sección objetivo de inversión (KIID clásico)
_WINDOW_OBJ_END   = 4500   # fin sección objetivo / inicio riesgos (KIID clásico)
_WINDOW_COST_START = 9000  # inicio sección costes
_WINDOW_COST_END   = 14000 # fin sección costes

# Ventanas por formato de documento
# KIID clásico (UCITS pre-2023): objetivo en 1200-4500
# DDF/PRIIPs  (post-2023):       objetivo en 200-2000 (sección "Finalidad" temprana)
# UNKNOWN:                        ventana amplia 200-4500 por seguridad
_WINDOWS_BY_FORMAT: dict = {
    "KIID":    (1200, 4500),
    "DDF":     (500,  5000),   # Ampliado a 5000: algunos DDF tienen objetivo en pos 4500-4800
    "UNKNOWN": (200,  4500),
}


def _detect_kiid_format(text: str) -> str:
    """
    Detecta el formato del documento KIID.

    Devuelve:
        'DDF'     — formato PRIIPs/DDF (post-2023), sección objetivo en 500-4500
        'KIID'    — formato KIID clásico UCITS, sección objetivo en 1200-4500
        'UNKNOWN' — formato no reconocido, ventana amplia 200-4500
    """
    if not text:
        return "UNKNOWN"
    header = text[:600].lower()

    # DDF/PRIIPs — varias variantes de detección:
    # 1. Cadena continua (caso normal)
    # 2. OCR fusionado sin espacios (JPMorgan/Amundi)
    if ("documento de datos fundamentales" in header
            or "documentodedatosfundamentales" in header):
        return "DDF"
    # DDF partido: "Finalidad" + "Producto" al inicio (JPMorgan OCR por lineas)
    if "finalidad" in header[:150] and "producto" in header[:400]:
        return "DDF"

    # KIID clásico UCITS
    if any(sig in header for sig in [
        "datos fundamentales para el inversor",
        "key investor information document",
        "informações fundamentais destinadas",
        "informações fundamentais ao investidor",
    ]):
        return "KIID"

    return "UNKNOWN"


def _get_obj_bounds(text: str) -> tuple[int, int]:
    """Devuelve (start, end) de la ventana objetivo según el formato del documento."""
    fmt = _detect_kiid_format(text)
    return _WINDOWS_BY_FORMAT[fmt]


def _extract_window(text: str, start: int, end: int) -> str:
    """Extrae ventana segura del texto."""
    return text[start:end] if len(text) > start else ""


def detect_nature_from_kiid(kiid_text: str) -> Optional[str]:
    """
    Detecta la naturaleza del fondo desde el texto KIID.
    Usa la ventana correcta (1200-4500) donde está la sección de objetivo.

    Devuelve el valor interno ('Monetario', 'RF_Corto', 'RF_Flexible',
    'Renta Variable', 'Mixtos', 'Alternativo', 'Estructurado') o None.

    Prioridad: Estructurado > Monetario > Alternativo > RF_Corto >
               RF_Flexible (dominante) > RV (dominante) > Mixtos >
               RF_Flexible (señal débil)
    """
    if not kiid_text:
        return None

    t = kiid_text.lower()
    _obj_start, _obj_end = _get_obj_bounds(kiid_text)
    w = _extract_window(t, _obj_start, _obj_end)
    # FIX-P1-NTC (2026-07-04): normaliza espacios en blanco DENTRO de la
    # ventana ya extraída (no toca _obj_start/_obj_end, que siguen calculados
    # sobre el texto original — evita desplazar ventanas ya calibradas).
    # Causa raíz: el wrapping de línea del PDF puede partir una frase clave
    # a mitad ("valores de renta \nvariable"), haciendo que el substring
    # exacto "renta variable" nunca aparezca aunque la frase esté presente.
    # Confirmado en LU0337786437 (MFS Prudent Wealth): "en valores de renta
    # \nvariable procedentes de emisores..." — texto real, "renta variable"
    # nunca detectado por búsqueda de substring literal.
    w = re.sub(r'\s+', ' ', w)

    # ── Señales en encabezado (nombre del producto) ─────────────────────────
    # El nombre del producto aparece en los primeros 600 chars y puede contener
    # señales de tipo aunque el texto esté fusionado (OCR sin espacios JPMorgan)
    _header = t[:600]
    _has_equity_in_header = (
        "equity" in _header          # "AsiaPacificEquityFund", "EquityFund"
        or "equities" in _header
        or "renta variable" in _header
        # FIX-RV-ARTIFICIAL-INTELLIGENCE-1 (2026-08-13): thematic AI/tech funds
        # declare "artificial intelligence fund" in their KIID product name
        # (first 600 chars).  The abbreviated fund name ("POLAR CAP ART INT")
        # carries no recognisable equity keyword; the KIID header is the only
        # reliable signal.  Guard: only fire when no bond/FI signal is present
        # in the header (rules out hypothetical "AI bond fund" edge cases).
        or ("artificial intelligence" in _header
            and "bond" not in _header and "fixed income" not in _header)
    )
    _has_bond_in_header = (
        "bond" in _header
        or "bonds" in _header
        or "fixed income" in _header
        or "aggregate" in _header    # Bloomberg Aggregate
        or "credit" in _header
        or "renta fija" in _header
    )

    # ── Texto OCR fusionado: buscar señales con regex sin espacios ────────────
    import re as _re
    _ocr_equity = bool(
        _re.search(r'invirtiendo\s*principalmente\s*en\s*compa', t)
        or _re.search(r'invirtiendo\s*en\s*acciones', t)
        or _re.search(r'invierte\s*principalmente\s*en\s*acciones', t)
        or _re.search(r'principalmenteenacciones', t)
        or _re.search(r'medianteinvirtiendoen', t)
    )

    # ── Estructurado ────────────────────────────────────────────────────────
    # FIX-ESTRUCT-NEGATION-1 (2026-07-21): changed from full-text `t` to
    # objective window `w` (R-6 compliance). Root cause: the standard PRIIPs KID
    # risk-disclosure boilerplate ("this product does not include … any capital
    # guarantee" / "investors who do not require a capital guarantee") lives in
    # the risk section, outside the objective, and was triggering 52 % of
    # Estructurado false positives (14/27 funds). Two compounding defects:
    # (1) the old test searched the whole text `t` instead of the bounded
    # objective window `w` already computed (R-6 violation); (2) no negation
    # guard for the capital-family. Fix: scope to `w` + add negation guard for
    # capital-family as defense-in-depth (covers objective text that explicitly
    # says "does not guarantee capital"). Unconditional signals (autocall, barrier,
    # etc.) have no benign negated form — they remain unconditional.
    _STRUCT_NEG_MARKERS = (
        "does not include", "do not require", "not include any",
        "without a capital", "no incluye", "sin garantía de capital",
        "no capital guarantee", "no require",
    )
    # Unconditional structured signals — search objective window (R-6)
    if any(k in w for k in (
        "autocall", "autocallable", "structured note",
        "nota estructurada", "barrier", "knock-in", "knock in",
    )):
        return "Estructurado"
    # Capital-family: objective window + negation guard
    for _sk in ("capital protected", "capital protection",
                 "capital guarantee", "capital garantizado"):
        _idx = w.find(_sk)
        if _idx >= 0:
            _pre = w[max(0, _idx - 120): _idx]
            if not any(m in _pre for m in _STRUCT_NEG_MARKERS):
                return "Estructurado"

    # ── Monetario ────────────────────────────────────────────────────────────
    # FIX-MMF-ENUM-NEGATION-1 (2026-07-25): split include_patterns into STRONG
    # (self-identifying MMF markers, fire unconditionally) and WEAK (list-item-
    # prone, subject to the new bond-enumeration/comparison negation guards).
    # Root cause: CARMIGNAC SÉCURITÉ, EDR BOND ALLOCATION, DWS SHORT DURATION
    # CREDIT mention "instrumentos del mercado monetario" as the trailing item of
    # a bond-mandate enumeration ("bonos, títulos de deuda e instrumentos del
    # mercado monetario"), and THREADNEEDLE CREDIT OPPORTUNITIES uses "mercado
    # monetario" only in a risk comparison ("perfil de riesgo más alto que los
    # valores del mercado monetario").  All 5 have realized SRRI band ≥3
    # (structurally incompatible with MMFR MMF mandate) but landed in Monetario
    # because detect_nature_from_kiid returned 'Monetario' for them — so the
    # vol-veto had no ex-ante RF alt to arbitrate to.  Guard is WEAK-only so
    # that genuine MMFs with a STRONG self-identifying marker (BGF ICS TREASURY,
    # EDR CREDIT VERY SHORT TERM) are never affected even if they happen to also
    # contain an enumeration or risk-comparison sentence.
    _STRONG_MMF_INCLUDE = [
        "money market fund", "fondo del mercado monetario", "fondo monetario",
        "monetary fund", "ucits mmf", "standard money market",
        "short term money market", "low volatility money market",
        "fondsmonétaire", "geldmarktfonds",
        "short-term money market",
        "ftse eur 1-month eurodeposit",
        "vencimiento medio ponderado",
        "weighted average maturity",
    ]
    _WEAK_MMF_INCLUDE = [
        "instrumentos del mercado monetario",
        "activos en instrumentos del mercado",
        "mercados monetarios", "mercado monetario",
        "money market instruments",
    ]
    # Keep the old name for the exclude_patterns reference below; the decision
    # block at the end of this section uses _strong_mmf / _weak_mmf flags.
    include_patterns = _STRONG_MMF_INCLUDE + _WEAK_MMF_INCLUDE

    # FIX-P1-MMF (2026-07-04): "renta fija", "acciones", "ucits", "ocivm" y
    # "colectiva en valores mobiliarios" eliminados de esta lista. Causa raíz:
    # hacían la detección de Monetario autoexcluyente para prácticamente TODO
    # el universo (0% de acuerdo confirmado en auditoría de 30 fondos
    # MONETARIOS). "ucits"/"ocivm"/"colectiva en valores mobiliarios" son
    # texto legal estándar (casi todo fondo europeo regulado ES un UCITS/OICVM,
    # incluidos los monetarios). "acciones" es ambiguo -- en textos DDF/KIID
    # aparece a menudo como "esta clase de ACCIONES" (= share class del
    # fondo), no como valores de renta variable (confirmado: UBS Money Market,
    # "los ingresos de esta clase de acciones se reinvierten"). "renta fija"
    # es demasiado genérico -- un MMF genuino describe sus propias
    # participaciones a corto plazo como "renta fija" (p.ej. "el vencimiento
    # final de una inversión de renta fija no podrá ser superior a 1 año"),
    # lo cual es coherente CON ser monetario, no evidencia en contra.
    # FIX-P1-MMF7 (2026-07-05): "equities"/"debt securities" añadidos junto a
    # "equity"/"fixed income". Causa raíz: "equity" no es substring de
    # "equities" (sufijos distintos: equit-y vs equit-ies), por lo que un KID
    # en inglés que enumera "equities"/"debt securities" (plural) en vez de
    # "equity"/"fixed income" (singular) no disparaba la exclusión, dejando
    # pasar el include "money market instruments" aunque el fondo declarase
    # también renta variable/deuda como asignaciones principales. Confirmado:
    # LU2178498619 Fidelity Global Multi Asset Income ("invests in a range of
    # asset classes Including debt securities, equities, real estate,
    # infrastructure"..."money market instruments: up to 25%") clasificaba
    # Monetario en vez de Mixtos.
    exclude_patterns = [
        "renta variable", "equity", "equities", "fixed income",
        "debt securities",
        "instrumentos financieros derivados", "instrumentos de crédito",
    ]

    # Definimos la ventana de texto (primeros 2000 caracteres) en minúsculas una sola vez
    # para mejorar el rendimiento y asegurar que no haya fallos por mayúsculas
    ventana_texto = t[:4000].lower()

    # FIX-P1-MMF3 (2026-07-04): dos patrones adicionales de falso positivo
    # confirmados vía auditoría full-corpus tras FIX-P1-MMF/MMF2. (a)
    # "mercado monetario" mencionado como colateral/margen derivado de
    # posiciones en derivados ("mantendrá una proporción significativa...
    # en efectivo e instrumentos del mercado monetario COMO RESULTADO DE LA
    # TENENCIA DE DERIVADOS") -- fondos long/short o absolute-return con
    # exposición vía derivados, no fondos monetarios (confirmado: Janus
    # Henderson HF PEur Alpha, Janus Henderson UK Absolute Return). (b)
    # "mercado monetario" describiendo el BENCHMARK/valor de referencia del
    # fondo ("tipo de interés del mercado monetario" como sustituto del
    # euríbor a 3 meses), no las posiciones del fondo (confirmado: Invesco
    # Sustainable Allocation).
    # NOTA: patrón bare "interés del mercado monetario" (sin "sustitutivo")
    # es DEMASIADO amplio -- también aparece en advertencias de riesgo
    # legítimas de fondos monetarios genuinos ("si los tipos de interés del
    # mercado monetario son muy bajos, el rendimiento...podría no ser
    # suficiente" -- Groupama Trésorerie), coherente CON ser monetario, no
    # evidencia en contra. Se exige "sustitutivo de un tipo de interés" para
    # distinguir la descripción de BENCHMARK (Invesco Sustainable Allocation)
    # de la advertencia de riesgo genuina.
    _derivative_collateral = bool(re.search(
        r'result[ao]d[oa]\s+de\s+la\s+tenencia\s+de\s+derivados', ventana_texto
    ))
    _benchmark_rate_mention = bool(re.search(
        r'sustitutivo\s+de\s+un\s+tipo\s+de\s+inter[ée]s\s+del\s+mercado\s+monetario',
        ventana_texto
    ))
    # FIX-P1-MMF4 (2026-07-04): "mercado monetario" dentro de una definición
    # parentética de "equivalentes de efectivo" ("así como equivalentes de
    # efectivo (definidos como depósitos bancarios, instrumentos del
    # mercado monetario...)") describe la porción de LIQUIDEZ/gestión de
    # tesorería de un fondo con otra estrategia primaria (grado de
    # inversión, titulizados, etc. mencionados antes de "así como"), no un
    # fondo puramente monetario. Confirmado: MFS Prudent Wealth (3 ISINs)
    # pasó a clasificar Monetario incorrectamente tras FIX-P1-MMF (BL-44
    # los reclasificó a Restantes como red de seguridad, pero la Nature
    # correcta es Renta Variable, no Restantes).
    _cash_equivalent_definition = bool(re.search(
        r'equivalentes?\s+de\s+efectivo[^.]{0,150}mercado\s+monetario',
        ventana_texto
    ))
    # FIX-P1-MMF5 (2026-07-04): "podrá invertir...en instrumentos/
    # inversiones del mercado monetario" (modo permisivo/condicional) señala
    # una asignación SECUNDARIA/opcional a monetario dentro de un fondo con
    # otra estrategia primaria -- distinto del modo declarativo genuino de
    # un MMF ("el fondo invierte en instrumentos del mercado monetario" /
    # "es un fondo del mercado monetario"). Confirmado en auditoría de 64
    # fondos marcados BL44_NATURE_SRRI_R4 Nature=Monetario: 35/56 falsos
    # positivos restantes son fondos de bonos/macro (Fidelity Euro Bond,
    # Schroder ISF Euro Bond, Pictet Government Bonds, JPM Global Macro)
    # cuya única mención de "mercado monetario" usa "podrá invertir" como
    # asignación auxiliar/limitada ("con carácter auxiliar", "hasta un
    # tercio de sus activos"), nunca como declaración de estrategia. Los 6
    # MMF genuinos confirmados usan modo declarativo ("es un fondo del
    # mercado monetario", "el fondo invierte en...instrumentos del mercado
    # monetario") sin "podrá" en la misma cláusula.
    # FIX-P1-MMF6 (2026-07-04): "puede invertir" -- misma señal permisiva
    # que "podrá invertir" (FIX-P1-MMF5), verbo distinto. Confirmado:
    # Invesco Euro Short Term Bond (2 ISINs) -- "el fondo invertirá
    # principalmente en instrumentos de deuda a corto plazo de alta
    # calidad...el fondo PUEDE invertir en instrumentos del mercado
    # monetario e instrumentos de deuda de todo el mundo" -- mandato
    # primario de renta fija corto plazo, MMF como asignación secundaria
    # explícitamente opcional. Verificado que "puede...mercado monetario"
    # está ausente en los 6 MMF genuinos confirmados esta sesión.
    # FIX-P1-MMF9 (2026-07-17): patrón anterior usaba [^.]{0,250} que se
    # interrumpe en las abreviaciones tipo "ee. uu." (EE. UU. = Estados Unidos),
    # presentes en fondos de renta variable estadounidense (JANUS.H. US FORTY).
    # Cambiado a [\s\S]{0,300} para ignorar puntos dentro de abreviaciones.
    _permissive_secondary_mmf = bool(re.search(
        r'(?:podr[aá]|puede)[\s\S]{0,300}mercados?\s+monetari[oa]s?',
        ventana_texto
    ))

    # FIX-P1-MMF8 (2026-07-17): "hasta un 100% del patrimonio se mantendrá
    # en depósitos en entidades de crédito e instrumentos del mercado
    # monetario" -- cláusula de gestión de tesorería DEFENSIVA habitual en
    # fondos macro/retorno absoluto. Distinto de un MMF genuino que DECLARA
    # esa estrategia como mandato primario. Confirmado: JPM Global Macro
    # Opportunities (LU0917670407/LU0917670829) -- KIID menciona "se
    # mantendrá en depósitos... instrumentos del mercado monetario" como
    # reserva de liquidez, no como estrategia primaria.
    _defensive_cash_clause = bool(re.search(
        r'se\s+mantendr[aá]\s+en\s+dep[oó]sitos[\s\S]{0,150}'
        r'mercados?\s+monetari[oa]s?',
        ventana_texto
    ))

    # FIX-P1-MMF7b (2026-07-05): "hasta un tercio...mercado monetario" --
    # cuantificador de fracción explícita (1/3) sin verbo podrá/puede, misma
    # familia de señal que FIX-P1-MMF5/6 (asignación limitada/minoritaria,
    # no mandato monetario primario). Confirmado: AXA WF Global Inflation
    # Bonds (2 ISINs) -- "hasta un tercio de su patrimonio total en títulos
    # de deuda no ligados a la inflación y en instrumentos del mercado
    # monetario" seguido de "el subfondo invertirá como mínimo el 90%..." en
    # bonos vinculados a la inflación -- mandato primario de renta fija
    # flexible, MMF como límite residual de un tercio.
    _minority_fraction_mmf = bool(re.search(
        r'hasta\s+un\s+tercio[^.]{0,200}mercados?\s+monetari[oa]s?',
        ventana_texto, re.DOTALL
    ))

    # FIX-P1-MMF7c (2026-07-05): "invierte principalmente en bonos...
    # instrumentos del mercado monetario," -- el fondo declara un mandato
    # PRIMARIO de bonos, y "mercado monetario" aparece como uno más de varios
    # instrumentos de deuda enumerados (bonos, MMF, MBS, ABS), no como
    # estrategia monetaria pura. Confirmado: AF US Short Term Bond --
    # "invierte principalmente en bonos y obligaciones del estado y de
    # empresas, instrumentos del mercado monetario, valores respaldados por
    # hipotecas (mbs) y valores respaldados por activos (abs)".
    _bond_primary_enumerated_mmf = bool(re.search(
        r'invierte\s+principalmente\s+en\s+bonos[^.]{0,250}mercados?\s+monetari[oa]s?',
        ventana_texto, re.DOTALL
    ))

    # FIX-MMF-ENUM-NEGATION-1 (2026-07-25): two new guards that suppress only
    # the WEAK MMF signals (see _WEAK_MMF_INCLUDE above).
    #
    # (a) Bond/debt enumeration: "mercado monetario" appears as the trailing
    # item of a list of bond/debt instruments — the primary mandate is bonds.
    # Generalises FIX-P1-MMF7c beyond "invierte principalmente en bonos" to
    # any intro bond/debt term.  Confirmed on CARMIGNAC SÉCURITÉ ("principales-
    # mente por bonos, títulos de deuda e instrumentos del mercado monetario"),
    # EDR BOND ALLOCATION ("valores de deuda y los instrumentos del mercado
    # monetario"), DWS SHORT DURATION CREDIT ("deuda pública, bonos corporativos
    # e instrumentos del mercado monetario").
    _bond_enumerated_mmf = bool(re.search(
        r'(bonos|obligaciones|t[ií]tulos\s+de\s+deuda|valores\s+de\s+deuda'
        r'|deuda\s+p[uú]blica|bonos\s+corporativos)'
        r'[\s\S]{0,70}(?:\by\b|\be\b|,)\s*(?:los\s+)?instrumentos\s+del\s+mercado\s+monetario',
        ventana_texto, re.DOTALL
    ))
    # (b) Risk comparison: "mercado monetario" used to compare the fund's risk
    # profile to that of a MMF — it is NOT describing the fund's mandate.
    # Confirmed on THREADNEEDLE CREDIT OPPORTUNITIES ("perfil de riesgo más
    # alto que los valores del mercado monetario").
    _mmf_comparison = bool(re.search(
        r'(m[aá]s\s+(?:alto|elevado|bajo|alta|baja)|comparaci[oó]n\s+con'
        r'|frente\s+a|que\s+los\s+valores)'
        r'[\s\S]{0,40}mercado\s+monetario',
        ventana_texto
    ))

    # FIX-P1-MMF2 (2026-07-04): "renta fija"/"acciones" enumerados junto a
    # OTROS tipos de instrumento ("valores de renta fija, certificados,
    # fondos, derivados e instrumentos del mercado monetario") indican un
    # fondo FLEXIBLE/MULTI-ACTIVO que también invierte en monetario, no un
    # fondo PURAMENTE monetario -- distinto de "renta fija"/"acciones"
    # aisladas (que tras FIX-P1-MMF ya no excluyen, ver arriba). Confirmado:
    # DWS ESG Dyn Opport ("renta fija, certificados, fondos, derivados e
    # instrumentos del mercado monetario") pasó a clasificar Monetario
    # incorrectamente tras eliminar "renta fija" de exclude_patterns sin
    # este matiz. La coma inmediatamente después distingue enumeración
    # (excluir) de descripción del propio vencimiento del fondo monetario
    # (p.ej. "una inversión de renta fija no podrá ser superior a 1 año" —
    # sin coma tras "renta fija", no excluye).
    _enumerated_other_asset = bool(re.search(
        r'(?:renta fija|acciones)\s*,', ventana_texto
    ))

    # FIX-MMF-TARGET-MATURITY-1 (2026-07-23): buy-and-hold bond funds with a
    # fixed portfolio-maturity year ("bonos y otros títulos de deuda … con
    # vencimiento en 2028", "R-CO TARGET 2028", "LA FRANÇAISE RENDEMENT 2027")
    # phrase their objective in money-market-compatible language and trip the
    # Monetario branch.  Genuine MMFs never declare a fixed portfolio-maturity
    # year — they roll short-dated instruments.  Guard fires on the objective
    # window (ventana_texto, first 4000 chars) to avoid section bleed.
    _target_maturity_bond = bool(re.search(
        r'bonos\s+y\s+otros\s+t[ií]tulos\s+de\s+deuda'
        r'|obligaciones[\s\S]{0,60}vencimiento[\s\S]{0,30}20\d\d'
        r'|t[ií]tulos[\s\S]{0,60}vencimiento[\s\S]{0,30}20\d\d'
        r'|bonos[\s\S]{0,60}vencimiento[\s\S]{0,30}20\d\d'
        r'|target\s+maturity'
        r'|buy.{0,4}and.{0,4}hold[\s\S]{0,100}20\d\d',
        ventana_texto, re.DOTALL
    ))

    # FIX-MMF-COMMODITY-OVERLAY-1 (2026-07-31): synthetic commodity funds hold
    # "short-term money market instruments" as SWAP COLLATERAL while their stated
    # objective is to track a commodity index (VONTOBEL COMMODITY: "aims to
    # participate in the growth of the commodity markets ... exposed to indices
    # from the Bloomberg Commodity Indexes ... swap transactions").  The STRONG
    # marker "short-term money market" matched INSIDE "short-term money market
    # instruments" — a holding/collateral phrase, not the MMFR self-identification
    # "... money market FUND" — so _strong_mmf fired Monetario before control
    # could reach the commodity->Alternativo branch (~line 1943 below).  Suppress
    # the Monetario branch when a commodity-index mandate signal is present so the
    # fund falls through to that branch (which returns Alternativo when no equity
    # mandate is present).  Signals mirror that branch.  Measured blast radius:
    # exactly 1 fund (only Monetario member whose KIID mentions commodities).
    _commodity_overlay = any(k in ventana_texto for k in [
        "bloomberg commodity", "commodity index total return",
        "índice de materias primas bloomberg",
        "growth of the commodity markets", "commodity markets",
    ])

    # Evaluación de la lógica
    # FIX-MMF-ENUM-NEGATION-1 (2026-07-25): STRONG markers fire unconditionally;
    # WEAK markers are suppressed when the bond-enumeration or risk-comparison
    # guard fires.  This ensures genuine MMFs with a STRONG self-identifier are
    # never negated, while the 7 bond funds that only trip a WEAK marker
    # (CARMIGNAC, EDR BOND ALLOC, DWS SHORT DUR CRD, THREADNEEDLE CRED OP,
    # BARINGS EM LOCAL DBT, FVS BOND OPP) correctly leave the Monetario branch.
    _strong_mmf = any(k in ventana_texto for k in _STRONG_MMF_INCLUDE)
    _weak_mmf   = any(k in ventana_texto for k in _WEAK_MMF_INCLUDE)
    _mmf_hit = _strong_mmf or (_weak_mmf and not _bond_enumerated_mmf and not _mmf_comparison)
    if (_mmf_hit
            and not any(e in ventana_texto for e in exclude_patterns)
            and not _enumerated_other_asset
            and not _derivative_collateral
            and not _benchmark_rate_mention
            and not _cash_equivalent_definition
            and not _permissive_secondary_mmf
            and not _minority_fraction_mmf
            and not _bond_primary_enumerated_mmf
            and not _defensive_cash_clause
            and not _target_maturity_bond
            and not _commodity_overlay):
        return "Monetario"

    # ── A partir de aquí usar ventana objetivo ───────────────────────────────

    # Retorno absoluto → Alternativo
    has_ar = any(k in w for k in [
        "absolute return", "retorno absoluto", "rendimiento positivo independientemente",
        "positive return regardless", "en cualquier entorno de mercado",
        "market neutral", "long/short", "long short",
        # FIX-ALTRV-LSBOND-1 (2026-07-25): Spanish long/short phrase used by
        # THREADNEEDLE CREDIT OPPORTUNITIES ("posiciones largas y cortas en
        # bonos").  Deliberately the exact long-before-short order; the reversed
        # order ("posiciones cortas y largas", used by SCHRODER Egerton equities)
        # is NOT included to avoid triggering has_ar on L/S-equity funds that
        # lack a cash/AR-mandate/header signal.
        "posiciones largas y cortas",
        # FIX-ALTRV-HEDGEFUND-1 (2026-07-31): Spanish regulatory term for
        # hedge funds / free-investment funds ("fondos de inversión libre").
        # Fires for hedge-fund tracker funds (GS Absolute Return Tracker) whose
        # KIID objective describes replicating hedge-fund beta without using the
        # phrase "absolute return" in the window.
        "fondos de inversión libre",
    ])
    # FIX-HCASHBENCH-ESTR-1 (2026-07-23): bare "estr" matched inside
    # "estrategia" (Spanish for "strategy"), making has_cash_bench spuriously
    # True for almost any Spanish-language KIID.  Fix: word-bounded \bestr\b.
    # Other terms ("eonia", "sonia", etc.) are already specific enough.
    # FIX-ALTRV-TBILL-1 (2026-07-31): add "treasury bill" as cash-proxy signal.
    # Root cause: Franklin Alternative Strategies and similar T-Bill benchmarked
    # AR funds have the benchmark text in the objective window but not as €STR/
    # EONIA/SOFR. Only fires when has_ar is also True (safeguard).
    has_cash_bench = (
        "€str" in w
        or bool(re.search(r'\bestr\b', w))
        or any(k in w for k in ["eonia", "sonia", "sofr", "overnight",
                                 "tasa libre de riesgo", "treasury bill"])
    )
    # Explicit AR mandate language (first-person, not table-of-contents noise):
    # catches funds that have a genuine AR objective but no explicit cash bench.
    # FIX-ALTRV-LSBOND-1 (2026-07-25): added Spanish "rendimiento positivo …
    # a pesar de los cambios en las condiciones del mercado" pattern used by
    # THREADNEEDLE CREDIT OPPORTUNITIES ("obtener un rendimiento positivo para
    # usted a medio plazo, a pesar de los cambios en las condiciones del mercado").
    # This is the standard Spanish-language "positive return regardless of market
    # conditions" absolute-return mandate phrasing.
    _ar_mandate_explicit = bool(re.search(
        r'independientemente de las condiciones'
        r'|positive.{0,40}absolute return'
        r'|absolute return.{0,60}(all|any|full).{0,30}(market|conditions?)'
        r'|positive return.{0,50}all\s+market'
        r'|neutrali[sz]e.{0,30}risk.{0,20}(equit|market)'
        r'|rendimiento positivo[\s\S]{0,80}a pesar de los cambios en las condiciones del mercado',
        w
    ))
    # AR signal within the KID header (first 350 chars) = fund's own name/type
    # section, not a deep table of other sub-fund names (R-6 guard).
    _ar_in_header = any(k in w[:350] for k in [
        "absolute return", "retorno absoluto", "market neutral",
        "long/short", "long short",
    ])
    # FIX-ALTRV-HEDGEFUND-1 (2026-07-31): AR signal in the pre-window product-
    # name section (first 600 chars of raw text, before the objective window).
    # DDF format puts "Producto [Fund Name]" at ~390-450 chars — just before the
    # 500-char window start — making it invisible to has_ar and _ar_in_header.
    # Guard: _has_bond_in_header suppresses "Absolute Return Bond" RF funds so
    # they don't trigger via the header path.
    _ar_in_pre_header = not _has_bond_in_header and any(k in _header for k in [
        "absolute return", "retorno absoluto", "market neutral",
    ])
    # Alternativo fires when: AR signal + at least one corroborating signal.
    # Corroborators: cash bench, explicit mandate phrasing, AR in the objective
    # window header, or AR in the product-name pre-window (DDF "Producto" line).
    if has_ar and (has_cash_bench or _ar_mandate_explicit or _ar_in_header
                   or _ar_in_pre_header):
        return "Alternativo"

    # FIX-DNCA-ALTRV-1 (2026-07-23): long/short relative-value fixed-income
    # fund with a cash (€STR) hurdle and no equity mandate → Alternativo.
    # Root cause: the has_ar gate above requires "long/short" (English); the
    # Spanish counterpart "larga/corta" / "posiciones largas y cortas" was not
    # in has_ar, so DNCA ALPHA BONDS I EUR ACC (Spanish KID) fell through to
    # FIX-B1-MIXTO-EARLY and matched "diversas clases de activos de renta fija"
    # (within-FI relative-value language, wrongly read as multi-asset).
    # Gate: (long/short OR Spanish equivalents) AND (relative value) AND
    # (real €STR cash hurdle — word-bounded, not the "estr"-in-"estrategia" bug)
    # AND NOT equity mandate.
    # Measured blast radius: exactly 2 DNCA share classes, 0 collateral.
    _ls_signal = bool(re.search(
        r'long/short|long short|larga/corta|posiciones largas y cortas'
        r'|direccional larga',
        w
    ))
    _rv_signal = ("valor relativo" in w) or ("relative value" in w) or ("valor relativa" in w)
    _real_estr = ("€str" in w) or bool(re.search(
        r'\b(ester|eonia|sonia|sofr|tasa libre de riesgo)\b|\bestr\b', w
    ))
    _no_equity = not any(k in w for k in ["renta variable", "equity", "equities", "acciones de"])
    if _ls_signal and _rv_signal and _real_estr and _no_equity:
        return "Alternativo"

    # FIX-B1-COMMODITY-ALT (2026-07-13): fund whose PRIMARY mandate is a broad
    # commodity index → Alternativo. Must be checked BEFORE the bond_dominant/
    # _RF_pending path because commodity instruments look like FI (no equity
    # signal). Confirmed: DWS INVEST ENHANCED COMMODITY STRATEGY uses
    # "bloomberg commodity index total return" in its objective.
    # Guard (2026-07-13): multi-asset funds (e.g. PIMCO Inflation MA) embed a
    # commodity index as ONE of several benchmark components. In those cases the
    # window also contains equity ("renta variable", "acciones") and FI ("renta
    # fija", "bonos") signals. Only fire Alternativo when the commodity signal
    # appears WITHOUT accompanying equity/FI signals → pure commodity mandate.
    # PIMCO DDF window contains "bloomberg commodity total return index" inside
    # a blended 5-index benchmark list alongside equity and real-estate indices;
    # the guard lets it fall through to FIX-B1-MIXTO-ENUM below.
    if any(k in w for k in [
        "bloomberg commodity index", "bloomberg commodity",
        "commodity index total return",
        "índice de materias primas bloomberg",
    ]):
        # Guard: fire Alternativo only if no equity MANDATE signal is present.
        # Pure commodity funds (DWS Enhanced Commodity Strategy) have no "renta
        # variable"/"equity" in their objective — only incidental "acciones" (=
        # share class) and "renta fija" (= cash collateral for derivatives).
        # Multi-asset funds (PIMCO Inflation MA) explicitly mention "renta
        # variable" as one of several mandated asset classes — fall through to
        # FIX-B1-MIXTO-ENUM below.
        _has_eq_mandate = any(k in w for k in [
            "renta variable", "equity", "equities",
        ])
        if not _has_eq_mandate:
            return "Alternativo"

    # FIX-B1-MIXTO-EARLY (2026-07-13): explicit multi-asset/mixed-mandate
    # declarations that must fire BEFORE eq_dominant/bond_dominant to prevent
    # an early "Renta Variable" or "_RF_pending" return from overriding the
    # genuinely mixed nature.
    # — "fondo de activos mixto": Spanish explicit label, Invesco Pan European
    #   High Income KIID text ("el fondo es un fondo de activos mixto
    #   gestionado activamente con una exposición flexible tanto a acciones...").
    # — "diferentes clases de activos" / "diversas clases de activos" /
    #   "varias clases de activos": generic multi-asset mandate phrasing, already
    #   used in the late Mixtos check but needs to fire first. Confirmed:
    #   UBS Systematic Allocation Protection Defensive and DWS Conservative Opp
    #   both have this phrase in their objective windows yet were reaching
    #   bond_dominant/_RF_pending before the late check was evaluated.
    # — "materias primas...renta variable": enumeration of commodities + equity
    #   + FI (PIMCO Inflation MA: "instrumentos relacionados con materias primas,
    #   divisas...y renta variable y valores relacionados con la renta variable").
    if any(k in w for k in [
        "fondo de activos mixto", "activos mixtos",
        "diferentes clases de activos",
        "diversas clases de activos",
        "varias clases de activos",
    ]):
        return "Mixtos"
    # FIX-JANUS-BALANCED-1 (2026-07-19): "balanced fund" in the product name
    # (first 600 chars of the KIID) is an unambiguous Mixtos signal — it is the
    # industry-standard label for a fund holding a 60/40 (or similar) mix of
    # equities and fixed income. Without this check the parenthetical equity
    # phrasing "en acciones (renta variable) y... valores de renta fija" used by
    # JANUS H BALANCED does not match any eq_dominant/has_equity pattern, causing
    # detect_nature_from_kiid() to return "_RF_pending" (→ Renta Fija Flexible)
    # instead of the correct "Mixtos".
    # The check also covers "balanced fund" within the objective window.
    if "balanced fund" in _header or "balanced fund" in w:
        return "Mixtos"
    # FIX-MIXTOS-FOF-1 (2026-07-21): multi-asset fund-of-funds with an objective
    # explicitly allocating to ≥2 different asset-class fund types → Mixtos.
    # Root cause: "fondos de renta variable" / "fondos de renta fija" (fund units)
    # were not counted as equity or mixed signals, so bond mentions dominated and
    # detect_nature_from_kiid() returned "_RF_pending" (→ RFC) for genuinely
    # multi-asset FoFs. Confirmed: DWS MULTI OPP (LU1673812605/LU1673813165):
    # "el fondo invierte un mínimo del 25 % en acciones de fondos de renta
    # variable, fondos mixtos de valores, fondos de renta fija y fondos
    # monetarios" — unambiguous multi-asset FoF, should be Mixtos.
    # Signal: explicit "fondos mixtos" OR ≥2 of {fondos de renta variable,
    # fondos de renta fija, fondos monetarios} co-present in objective window.
    _fof_rv  = "fondos de renta variable" in w
    _fof_rf  = "fondos de renta fija" in w
    _fof_mon = "fondos monetarios" in w
    if "fondos mixtos" in w or (_fof_rv + _fof_rf + _fof_mon >= 2):
        return "Mixtos"
    # FIX-B1-COMMODITY-PRIMAR-1 (2026-07-18): pure commodity fund with a
    # "principalmente en materias primas" primary mandate → Alternativo.
    # Must fire BEFORE FIX-B1-MIXTO-ENUM (below) which catches the same
    # "materias primas + renta variable + renta fija" triple and returns
    # Mixtos -- correct for PIMCO-style multi-asset funds but wrong for
    # pure commodity funds whose "renta variable" and "renta fija" are only
    # secondary/permitted instruments or derivative collateral.
    # Confirmed: NEUBERGER BERMAN COMMODITIES (IE0004O7KK00 / IE000MVZ49F4)
    # -- "invirtiendo principalmente en una amplia gama de materias primas";
    # "renta variable" appears only as a permitted instrument list entry and
    # "renta fija" only for derivative collateral management.
    if re.search(r'principalmente\s+en[^.]{0,80}materias\s+primas', w):
        return "Alternativo"

    # FIX-GRANDEURO-DERIV-1 (2026-07-26): equity-dominant funds may have a
    # derivatives section that lists "renta fija, renta variable...y materias
    # primas" as derivative UNDERLYINGS, not as a multi-asset mandate.
    # Guard: don't fire commodity/mixed-phrase checks when the fund declares an
    # explicit ≥60% minimum equity mandate — genuine multi-asset funds don't do
    # that. Confirmed: CARMIGNAC PORTFOLIO GRANDE EUROPE (LU0099161993) —
    # primary mandate "mínimo del 75%...en renta variable del Espacio Económico
    # Europeo" but KIID also lists "derivados...sobre: divisas, renta fija,
    # renta variable...y materias primas" triggering MIXTO-ENUM and NTC2.
    _has_large_equity_mandate = bool(re.search(
        r'(?:al\s+menos\s+el|un?\s+m[ií]nimo\s+del?|como\s+m[ií]nimo(?:\s+el)?)'
        r'\s+(?:6[0-9]|[7-9]\d|100)\s*%[^.]{0,80}(?:en\s+)?'
        r'(?:renta\s+variable|acciones)\b',
        w
    # FIX-RV-EN-LARGEEQ-MANDATE-1 (2026-08-13): extend the large-equity-mandate
    # guard to English ("at least / at a minimum of X% in equities").
    # Root cause: _has_large_equity_mandate was Spanish-only, so the early
    # Mixtos checks ("equities, bonds" CSV — FIX-MIXTOS-EN-EQUITIES-BONDS-1)
    # could override a ≥60% English equity mandate when "equities" appeared
    # first in a comma-separated list that also mentioned "bonds".
    # English threshold kept at 60% to match the Spanish guard.
    )) or bool(re.search(
        r'(?:at\s+least|at\s+a\s+minimum\s+of)'
        r'\s+(?:6[0-9]|[7-9]\d|100)\s*%[^.]{0,80}'
        r'(?:in\s+)?equit(?:y|ies)\b',
        w
    ))

    # FIX-B1-MIXTO-ENUM (2026-07-13): fund that mentions commodities + equity +
    # FI in the same objective window → genuinely multi-asset. PIMCO Inflation
    # Master Fund: "instrumentos de renta fija vinculados a la inflación...
    # instrumentos relacionados con materias primas... y renta variable y valores
    # relacionados con la renta variable" — no single explicit Mixtos label but
    # the enumeration of 3+ asset classes is unambiguous.
    # Guard: FIX-GRANDEURO-DERIV-1 — don't fire for equity-primary funds whose
    # derivatives section merely lists these asset classes as underlyings.
    if ("materias primas" in w
            and any(k in w for k in ["renta variable", "acciones"])
            and any(k in w for k in ["renta fija", "bonos", "deuda", "inflación"])
            and not _has_large_equity_mandate):
        return "Mixtos"

    # FIX-P1-NTC2 (2026-07-04): enumeración explícita de 3+ clases de activos
    # (renta fija + renta variable + alternativas/monetario) es señal
    # inequívoca de fondo multi-activo -- más fuerte que cualquier mención
    # aislada de una sola clase, y debe ganar ANTES que eq_dominant/
    # bond_dominant más abajo (si no, "cartera de renta variable" clasifica
    # como RV-dominante un fondo que en realidad reparte entre 3 carteras).
    # Confirmado: FAM_000608/612 DB CNSRVATIV SAA -- "el fondo intentará
    # conseguir exposición a tres carteras principales de clases de activos
    # (renta fija, renta variable e inversiones alternativas)" -- mismo texto
    # en ambas clases de acción, pero antes resolvía RV en una y _RF_pending
    # en la otra según qué otra frase incidental apareciera en cada PDF.
    # Solo el ORDEN "renta fija, ... renta variable" (RF primero) faltaba en
    # la lista "Mixto explícito" más abajo (que sí cubre "renta variable, X
    # renta fija"); se añade aquí como chequeo temprano en vez de reordenar
    # los chequeos existentes (menor riesgo de regresión).
    # FIX-P1-DBLCLAIM2 (2026-07-04): "renta variable y de renta fija" --
    # variante con "de" insertado entre la conjunción y la segunda clase de
    # activo, que no coincidía con "renta variable y renta fija" (sin "de").
    # Confirmado: AXA WF Global Optimal Income ("invirtiendo en una
    # combinación de títulos de renta variable y de renta fija emitidos por
    # estados y empresas") -- fondo genuinamente mixto, capturado
    # incorrectamente como Renta Variable en la auditoría de doble-reclamo
    # renta_variable+mixtos.
    # FIX-P1-DBLCLAIM4 (2026-07-04): "renta variable y bonos"/"acciones y
    # bonos" ya existían en la lista "Mixto explícito" más abajo, pero esa
    # lista se evalúa DESPUÉS de eq_dominant/bond_dominant -- si
    # "valores de renta variable" (frase deliberadamente conservada en
    # eq_dominant pese a ser débil, ver nota junto a esa lista: retirarla
    # causó una regresión mayor con Carmignac Patrimoine) dispara
    # eq_dominant=True primero, la función retorna "Renta Variable" sin
    # llegar nunca a evaluar "Mixto explícito". Se promueven aquí como
    # chequeo temprano en vez de tocar eq_dominant de nuevo. Confirmado:
    # UBS Strategic Fund Growth ("invierte con una proporción variable en
    # valores de renta variable y bonos, incluidos instrumentos del
    # mercado monetario").
    # FIX-RV-CONVERTIBLE-1 (2026-07-17): "renta variable y bonos convertibles"
    # is NOT a mixed mandate — convertible bonds are equity-linked instruments
    # (not a primary fixed-income allocation). The substring "renta variable y
    # bonos" matches inside "instrumentos relacionados con la renta variable y
    # bonos convertibles" (M&G Global Listed Infrastructure: "al menos el 80%
    # del fondo se invierte en acciones, instrumentos relacionados con la renta
    # variable y bonos convertibles"). Guard: only fire if the match is NOT
    # "renta variable y bonos convertibles" (convertibles as secondary hybrid
    # instrument in an equity fund → eq_dominant path returns RV correctly).
    # FIX-GRANDEURO-DERIV-2 (2026-07-26): "renta fija, renta variable" in its
    # COMMA-SEPARATED form is a derivative-underlyings list entry (e.g. "sobre
    # los siguientes riesgos: divisas, renta fija, renta variable..."), not a
    # mandate phrase. The CONJUNCTION form "renta fija y renta variable" is a
    # genuine multi-asset mandate and stays unguarded. When the fund also has a
    # large equity mandate, the comma form is almost certainly a derivatives list.
    _rv_fija_csv_fires = (
        "renta fija, renta variable" in w
        and not _has_large_equity_mandate
    )
    # FIX-MIXTOS-ACCIONES-BONOS-CSV-1 (2026-08-13): "acciones, bonos" (comma-
    # separated Spanish multi-asset enumeration) is the same signal as
    # "acciones y bonos" but with a comma instead of "y".  Confirmed:
    # FLOSSBACH VON STORCH MULTIPLE OPPORTUNITIES II (LU1038809395/LU1280372688)
    # — "entre ellos acciones, bonos e instrumentos del mercado monetario" —
    # genuine multi-asset fund that fell through to Restantes because the
    # existing pattern required the conjunction "y".
    _acciones_bonos_csv = "acciones, bonos" in w
    if _rv_fija_csv_fires or _acciones_bonos_csv or any(k in w for k in [
        "renta fija y renta variable",
        "renta variable y de renta fija",
        "renta variable y de bonos",
        "acciones y bonos",
    ]) or (
        "renta variable y bonos" in w
        and "renta variable y bonos convertibles" not in w
    ):
        return "Mixtos"

    # FIX-P1-NTC7 (2026-07-05): "invests in a range of asset classes" (KID
    # en inglés, formato Fidelity/PRIIPs con lista de porcentajes por clase
    # de activo) -- misma familia que FIX-P1-NTC2/NTC3 (enumeración
    # multi-activo explícita que debe ganar ANTES que eq_dominant/
    # bond_dominant, si no la mención aislada de "debt securities" dispara
    # bond_dominant y retorna _RF_pending sin llegar nunca a evaluar el
    # Mixto explícito de más abajo). Confirmado: Fidelity Global Multi Asset
    # Income -- "The fund invests in a range of asset classes Including
    # debt securities, equities, real estate, infrastructure".
    if "range of asset classes" in w:
        return "Mixtos"

    # FIX-MIXTOS-EN-EQUITIES-BONDS-1 (2026-08-13): English comma-separated
    # enumeration "equities, bonds and money market instruments" (or similar)
    # is the English equivalent of "acciones, bonos" — an unambiguous multi-
    # asset declaration in the objective window.  Must fire before
    # eq_dominant/bond_dominant so neither lone-equity nor lone-bond
    # dominance overrides the genuinely mixed mandate.
    # Confirmed: FFG GLB FLXBL CONVICTIONS (LU1697917083) — "investments …
    # are … made in equities, bonds and money market instruments or in cash."
    # Guard: only fire when "equities, bonds" appears WITHOUT a large equity
    # mandate (≥60% minimum equity) which would make it genuinely equity-
    # dominant despite the enumeration.
    if "equities, bonds" in w and not _has_large_equity_mandate:
        return "Mixtos"

    # FIX-P1-DBLCLAIM3 (2026-07-04): "instrumentos de deuda...y en acciones"
    # co-ocurrencia -- mandato dual deuda+equity explícito. NO se añade
    # "instrumentos de deuda" a has_bonds/bond_dominant (ver comentarios
    # existentes más abajo: esa frase también aparece en fondos de renta
    # variable pura describiendo warrants/pagarés vinculados a RV, y ya fue
    # retirada de esas listas por esa razón). Este chequeo es más estrecho
    # -- exige la mención EXPLÍCITA de "en acciones" cerca, señal de mandato
    # mixto genuino en vez de mención incidental de instrumentos de deuda.
    # Confirmado: Invesco Global Income ("invertir principalmente en
    # instrumentos de deuda...y en acciones de sociedades en todo el
    # mundo") -- fondo mixto capturado incorrectamente como Renta Variable
    # (vía "en acciones de" en eq_dominant) en la auditoría de doble-reclamo.
    if re.search(r'instrumentos de deuda[^.]{0,150}en acciones', w):
        return "Mixtos"

    # FIX-P1-NTC3 (2026-07-04): benchmark compuesto/blended con pesos
    # explícitos por clase de activo ("compuesto de 40% MSCI ... 40% ICE
    # BofA Government ... 20% €STR") es señal inequívoca de fondo
    # multi-activo -- un fondo de renta variable pura o de renta fija pura
    # no se referencia contra un índice mixto ponderado. Debe ganar ANTES
    # que eq_dominant/bond_dominant (si no, frases como "valores de renta
    # variable" en la descripción de la porción equity del blend clasifican
    # como RV-dominante un fondo genuinamente balanceado). Confirmado:
    # FR0010135103/LU0306142/etc. Carmignac Patrimoine -- benchmark
    # "40% MSCI AC World NR Index, 40% ICE BofA Global Government Index,
    # 20% €STR Capitalized Index".
    _composite_bench_m = re.search(
        r'(?:indicador|[ií]ndice)\s+de\s+referencia[^.]{0,80}'
        r'compuest[ao]\s+(?:de|por)([^.]{0,250})',
        w
    )
    if _composite_bench_m:
        _bench_zone = _composite_bench_m.group(1)
        _has_eq_idx = bool(re.search(
            r'msci|ftse|s&p|stoxx|russell|nikkei|dax\b|cac\s*40', _bench_zone
        ))
        _has_bond_idx = bool(re.search(
            r'bofa|bloomberg|govern?ment|credit|aggregate'
            r'|€str|\bestr\b|eonia|sofr|sonia|euribor',
            _bench_zone
        ))
        if _has_eq_idx and _has_bond_idx:
            return "Mixtos"

    # ── Señales de presencia (no dominantes) — declaradas antes de usarlas ──
    has_equity = any(k in w for k in [
        "equity securities", "acciones y otros valores",
        "invierte en acciones", "invests in equities",
        "acciones ordinarias",
        "primarily in equities", "mainly in equities",
        # Señales DDF genéricas — frases que indican inversión PRIMARIA en RV
        "acciones de empresas", "acciones emitidas",
        "valores de renta variable", "cartera de acciones",
        "mediante la inversión en acciones",
        "invierte en valores de renta variable",
        "acciones y otros valores de renta variable",
        "en acciones y otros", "shares of companies",
        "company shares", "common shares",
        "reproduce", "replica la rentabilidad",  # fondos indexados equity
        "seguimiento del índice",
        "fondo de renta variable",               # mención explícita como tipo de fondo
        "invertir en renta variable",             # intención de inversión en RV
        "inversiones de renta variable",          # cartera de RV
    ])
    # Nota: "renta variable" sola se eliminó — demasiado amplia con ventana [500:4500]
    # Aparece en textos de bonos/mixtos como mención incidental de activos alternativos
    # En su lugar: "renta variable de" (seguido de "empresas", geografía, etc.)
    # distingue "inversión EN renta variable" de "renta variable Y bonos"
    has_equity = has_equity or any(k in w for k in [
        "renta variable de",             # "RV de empresas cotizadas", "RV de todo el mundo"
        "renta variable global",          # "global equity"
        "acciones de compañías",
        "acciones de sociedades",
        # FIX-P1-NTC (2026-07-04): "en renta variable, bonos" — mandato
        # explícito con RV enumerada PRIMERO junto a otras clases de activo
        # (LU0907915168 Amundi Global Perspectives: "invierte al menos el
        # 67% de sus activos en renta variable, bonos..."). Distinto de la
        # nota anterior: aquí "renta variable" va seguida de una coma y otra
        # clase de activo, no de un calificador de bonos que la subordine.
        "en renta variable, bonos",
        "renta variable, bonos",
    ])
    # FIX-P1-NTC4 (2026-07-05): "instrumentos relacionados con la renta
    # variable" -- fraseo genérico usado por fondos multi-activo para la
    # porción de exposición a renta variable (vía derivados/depositary
    # receipts, no solo acciones directas) que ninguno de los patrones
    # anteriores captura. Confirmado: Amundi Multiasset Income 11/27 (2
    # ISINs) -- "el compartimento podrá invertir hasta el 47,5% de su
    # patrimonio neto en renta variable e instrumentos relacionados con la
    # renta variable" -- ausencia de esta señal hacía que el fondo
    # (bono-primario + hasta 47,5% RV) se clasificase _RF_pending en vez de
    # Mixtos (has_equity+has_bonds).
    # Guard: EXCLUYE asignaciones menores expresadas como fracción pequeña
    # ("hasta una décima parte"/"hasta un décimo" = 10%) inmediatamente
    # antes de la frase -- señal de asignación SECUNDARIA/residual, no de
    # presencia de renta variable digna de nota. Confirmado: AXA WF Euro
    # Credit Plus (2 ISINs, RESTANTES) -- "hasta una décima parte de su
    # patrimonio total en renta variable e instrumentos relacionados con la
    # renta variable" en un fondo genuinamente bond-dominante (RF Corto
    # Plazo) -- sin este guard, has_equity=True desviaba el fondo de la
    # resolución _RF_pending->resolve_rf_subtype (correcta, RF_Corto) hacia
    # el camino "mención incidental->None", que Capa 2 no resolvía igual.
    _minor_equity_fraction = bool(re.search(
        r'hasta\s+una?\s+d[eé]cim[ao]\s+parte[^.]{0,80}'
        r'(?:renta\s+variable\s+e\s+instrumentos\s+relacionados|'
        r'instrumentos\s+relacionados\s+con\s+la\s+renta\s+variable)',
        w
    ))
    if not _minor_equity_fraction and any(k in w for k in [
        "instrumentos relacionados con la renta variable",
        "renta variable e instrumentos relacionados",
    ]):
        has_equity = True
    has_bonds = any(k in w for k in [
        "valores de renta fija", "fixed income securities",
        "invierte en bonos", "inverts in bonds",
        "renta fija", "invierte principalmente en bonos",
        "primarily in bonds", "debt securities",
        "invierte en valores de deuda",
        # FIX-P1-NTC (2026-07-04): "invierte al menos" eliminado de aquí —
        # es un cuantificador agnóstico de clase de activo ("invierte al
        # menos el 67% en renta variable, bonos..."); disparaba has_bonds=True
        # en fondos donde ese "al menos X%" se refería a RENTA VARIABLE, no a
        # bonos (confirmado: LU0907915168 Amundi Global Perspectives, 67% RV).
        # Señales DDF genéricas adicionales
        "títulos de deuda",
        # "instrumentos de deuda" eliminado — aparece en equity funds en contexto
        # de warrants/pagarés ("instrumentos de deuda vinculados a RV")
        "deuda soberana", "deuda corporativa",
        "bonos corporativos", "bonos soberanos",
        "bonos y otros", "bonos (incluidos",
        "valores de deuda",
        # FIX-P1-RV1 (2026-07-04): "obligaciones" (bare) eliminada. Causa
        # raíz: es boilerplate de riesgo de depositario/contraparte
        # ("¿qué ocurre si [depositario] no puede pagar?" -- "incumplan sus
        # obligaciones", "obligaciones contractuales", "obligaciones de
        # custodia"), presente en prácticamente TODO KIID sin relación con
        # bonos. Confirmado corpus-wide: 25/26 ocurrencias en fondos RF
        # genuinos eran esta boilerplate, solo 1/26 usaba "obligaciones"
        # como sinónimo real de bonos ("bonos u obligaciones del estado").
        # Causaba 13 fondos de renta variable pura (Franklin Technology,
        # DWS Critical Technology, MS Global Brands/Opportunity/Insight)
        # mal clasificados como Mixtos vía has_equity+has_bonds.
        "renta fija y", "en bonos y",
        "bond securities", "fixed rate", "floating rate notes",
        "high yield bonds", "investment grade",
        "grado de inversión", "calificación crediticia",
    ])

    # RF dominante (declaración explícita de objetivo)
    # FIX-P1-NTC (2026-07-04): "renta fija", "fixed income" y "grado de
    # inversión" (bare, sin calificador "principalmente"/"mayormente")
    # eliminados de esta lista. Auditoría 12 fondos RESTANTES mal
    # clasificados (LU1244893696 EDR Big Data: mandato 75-110% renta
    # variable, pero "renta fija" aparece en una cláusula de gestión de
    # efectivo aparte — "hasta el 25%... para gestión de efectivo" — y
    # bastaba para forzar bond_dominant=True, anulando el mandato real de
    # RV). "grado de inversión" (investment grade) es una calificación
    # crediticia que aparece también en fondos RV/mixtos que mencionan
    # bonos complementarios; no indica objetivo dominante por sí sola.
    # Ambos términos permanecen en has_bonds (señal de presencia, no de
    # dominancia) para el camino has_equity+has_bonds→Mixtos.
    bond_dominant = any(k in w for k in [
        "primarily in bonds", "mainly in bonds", "principally in bonds",
        "invierte principalmente en bonos", "invierte en bonos",
        "fixed income securities", "fixed income fund",
        "bond fund", "fondo de bonos",
        "invierte en valores de renta fija",
        "invierte principalmente en instrumentos de renta fija",
        "debt securities", "debt fund", "inverts in debt securities",
        "valores de renta fija",
        # Señales DDF adicionales — frases de política de inversión
        "títulos de deuda",
        # "instrumentos de deuda" eliminado — aparece en equity funds en contexto
        # de warrants/pagarés ("instrumentos de deuda vinculados a RV")
        "deuda soberana", "deuda corporativa",
        "bonos corporativos", "bonos y otros títulos",
        "principalment en bonos", "principalement en obligat",
        "investment grade bonds",
        "bonos de alto rendimiento",
        "activos principales: bonos", "principales activos: bonos",
        "principales activos negociados: bonos",
        # FIX-B1-REST-RF-1 (2026-07-17): "el resto en activos de renta fija"
        # -- mandato explícito de que la fracción NO-equity se invierte en RF.
        # Confirmado: RFMI MULTIGESTION FI (ES0122762000) -- "máximo del 20%
        # de la exposición total en Renta Variable y el resto en activos de
        # Renta Fija". Sin este fix: bond_dominant=False → eq_dominant=True
        # (falso positivo de "en acciones de baja capitalización" en disclamer
        # de riesgo) → devuelve RV en vez de Mixtos/RF Flexible.
        "el resto en activos de renta fija",
        "el resto en renta fija",
        "el resto en activos de renta fija y",
    ])
    # FIX-B1-BOND-PRINCIPALMENTE-2 (2026-07-17): "invierte principalmente,
    # directa o indirectamente a través de derivados, en bonos" -- la cláusula
    # instrumental "directa o indirectamente a través de derivados" rompe el
    # match exacto "invierte principalmente en bonos" de la lista anterior.
    # Regex con margen de 120 chars entre "principalmente" y "en bonos".
    # Confirmado: Franklin Euro High Yield Fund -- "El Fondo invierte
    # principalmente, directa o indirectamente a través de derivados, en
    # bonos del Estado y corporativos con calificación inferior a investment
    # grade". Sin este fix: bond_dominant=False → eq_dominant=True (por
    # "valores de renta variable" en la cláusula secundaria) → devuelve RV.
    bond_dominant = bond_dominant or bool(re.search(
        r'invierte\s+principalmente[^.]{0,120}en\s+bonos', w
    ))

    # RV dominante (declaración explícita de objetivo)
    # NOTA (2026-07-04): se evaluó demover "valores de renta variable" y
    # "en acciones de" de esta lista (ambas pueden aparecer en fondos
    # mixtos con tope de exposición, p.ej. FR0010135103 Carmignac
    # Patrimoine: "expondrá como máximo el 50%... a valores de renta
    # variable"). Revertido: la eliminación causó una regresión mucho
    # mayor (82 fondos Mixtos->None y 33 Renta Variable->None, incl.
    # fondos de renta variable genuinos como DWS Osteuropa/DWS India que
    # dependen de estas frases como única señal). Sin una comprobación de
    # proximidad más quirúrgica (detectar "máximo X%"/"hasta un" cerca de
    # la frase para distinguir tope de dominancia), mantener ambas frases
    # es la opción de menor daño neto — deja el caso Carmignac como falso
    # positivo conocido y documentado en vez de introducir uno mayor.
    eq_dominant = any(k in w for k in [
        "primarily in equities", "mainly in equities", "principally in equities",
        "invierte principalmente en acciones", "invest in shares",
        "invierte en acciones", "equity securities",
        "acciones y otros valores de renta variable",
        "invests mainly in shares", "fondo de renta variable",
        "acciones y otros valores",
        # Señales DDF adicionales
        "acciones de empresas", "acciones emitidas por",
        "en acciones de", "valores de renta variable",
        "principalment en acciones", "principalmente en acciones",
        "acciones ordinarias y otros",
        "invirtiendo en acciones",
        # Fondos indexados equity (passive)
        "reproduce la rentabilidad del", "replica la rentabilidad del",
        "reproduce (con un error", "réplica del índice",
        "seguimiento del índice de renta variable",
        "inversión pasiva en acciones",
        # FIX-P1-NTC (2026-07-04): declaraciones explícitas de mandato RV
        # confirmadas en fondos RESTANTES mal clasificados. "cartera de
        # renta variable" (FR0010836163 CPR Silver Age: "invierte en una
        # cartera de renta variable europea" — sin bonos en la misma
        # cláusula). "mercados...de renta variable" (LU1244893696 EDR Big
        # Data: "entre el 75% y el 110%...expuesto...a los mercados
        # internacionales de renta variable y otros valores similares").
        "cartera de renta variable",
        "mercados internacionales de renta variable",
        "mercados de renta variable",
        # FIX-P1-NTC5 (2026-07-05): "invierte fundamentalmente en acciones"
        # -- sinónimo de "principalmente" no cubierto por los patrones
        # existentes ("invierte principalmente en acciones"/"invirtiendo en
        # acciones"). Confirmado: DWS ESG Europe Small-Mid Cap (2 ISINs) --
        # "el fondo invierte fundamentalmente en acciones de emisores
        # europeos de pequeño y mediano tamaño" -- ausencia de esta señal
        # dejaba bond_dominant (vía "valores de renta fija" en la cláusula
        # secundaria) como único voto, resolviendo _RF_pending en vez de RV.
        "invierte fundamentalmente en acciones",
        "fundamentalmente en acciones de",
        # FIX-B1-EN-STOCKPICK-1 (2026-07-18): English equity management
        # signal. "stock picking" is exclusively used for equity selection
        # strategies -- never appears in bond fund objectives. Without this,
        # English equity funds using quality-credit language ("investment
        # grade") set has_bonds=True with no has_equity → _RF_pending.
        # Confirmed: ECHIQUIER SPACE FUND (LU2466448532/LU2466449001) --
        # "active and discretionary management based on a rigorous stock
        # picking process" yet classified RFF due to "investment grade"
        # (credit quality constraint on equity holdings, not a bond mandate).
        "stock picking",
        "stock selection process",
    ]) or _has_equity_in_header or _ocr_equity or bool(re.search(
        # FIX-P1-NTC6 (2026-07-05): "al menos el X% en acciones
        # internacionales/globales" -- declaración de mandato mayoritario de
        # RV que no usa ninguna de las frases "renta variable" ya cubiertas.
        # Confirmado: EDR SICAV Global Resilience -- "el producto invertirá
        # en todo momento al menos el 75% en acciones internacionales" (sin
        # mención de bonos en la cláusula de objetivo).
        # FIX-B1-RV-MINIMO-1 (2026-07-17): "un mínimo del X% en acciones"
        # -- sinónimo de "al menos el X% en acciones", forma usada por DWS.
        # Confirmado: DWS Invest Focus Europe -- "el fondo invierte un mínimo
        # del 75% en acciones de emisores con sede principal en un Estado
        # miembro de la UE". Patrón existente solo cubría "al menos el".
        # FIX-B1-TRUEVAL-1 (2026-07-18): Extend to cover "en renta variable"
        # (not just "en acciones") and allow text between % and "en" (e.g.
        # "75%de la exposición total en renta variable"). Without this,
        # TRUE VALUE COMPOUNDERS (ES0180783013) -- "se invierte como mínimo
        # el 75%de la exposición total en renta variable" -- was returning
        # _RF_pending because "renta variable" ≠ "acciones" and %de ≠ % en.
        r'(?:al\s+menos\s+el|un?\s+m[ií]nimo\s+del?|como\s+m[ií]nimo(?:\s+el)?)'
        r'\s+\d+\s*%[^.]{0,80}en\s+(?:acciones|renta\s+variable)\b', w
    # FIX-B1-EN-TWOTHIRDS-1 (2026-07-18): English equity fund with explicit
    # minimum proportion mandate ("invest at least two thirds/X% in equity").
    # Without this, ASHOKA WO INDIA OPPT (IE00BDR0R792) -- "the fund will
    # invest at least two thirds of its net assets in equity and equity
    # related transferable securities" -- was returning _RF_pending because
    # "equity and equity related" was not in has_equity and "investment grade"
    # (quality constraint on equity holdings) spuriously set has_bonds=True.
    )) or bool(re.search(
        r'invest(?:s|ing)?\s+at\s+least[^.]{0,80}in\s+equit(?:y|ies)\b', w
    # FIX-NAT-EN-SHARES-1 (2026-07-19): "invest(s/ing) primarily in … shares" —
    # English primary-equity declaration with an instrument qualifier between
    # "primarily in" and "shares" (e.g. "investing primarily in listed or traded
    # shares"). The existing FIX-B1-EN-TWOTHIRDS-1 requires "at least"; this
    # pattern covers funds that declare primacy without a minimum percentage.
    # Confirmed: MAN GLG JAPAN COREALPHA (IE00B5648R31/IE00BYVDZH74) — "the fund
    # seeks long term gains by investing primarily in listed or traded shares (or
    # related instruments) of issuers in japan" — none of the eq_dominant keyword
    # list entries matched ("shares of companies" ≠ "in … shares"), causing
    # bond_dominant=True (from "debt securities" secondary allowance) with
    # eq_dominant=False → function returned _RF_pending at line 2352 instead of
    # "Renta Variable".
    )) or bool(re.search(
        r'invest(?:s|ing)?\s+primarily\s+in[^.]{0,50}shares\b', w
    # FIX-RV-EN-PRIMARILY-EQUITIES-1 (2026-08-13): "primarily/mainly/
    # principally in [geographic/style modifier] equities" — English equity
    # mandate where a geographic or style qualifier (or even a parenthetical
    # percentage) sits between the adverb and "equities", breaking the exact-
    # substring match "primarily in equities".
    # The existing patterns cover "primarily in equities" (bare) and
    # "invest at least X% in equities" but not the gap form.
    # Confirmed:
    #   EVLI NORDIC B EUR (FI0008810908) — "invests its assets primarily in
    #     nordic equities" — Nordic equity fund falling through to Restantes.
    #   LUX M SIC FIN GL DEF R EUR ACC (LU1822851884) — "mainly (at least
    #     51%) in listed equities in security/defence sectors" — defence equity
    #     fund with a parenthetical percentage between "mainly" and "in".
    # Regex: allows up to 40 non-period chars between the adverb and "in",
    # then up to 60 non-period chars between "in" and "equities" — tight enough
    # to stay within a single clause, wide enough to bridge the gap forms above.
    )) or bool(re.search(
        r'(?:primarily|mainly|principally)(?:[^.]{0,40})in[^.]{0,60}equities\b', w
    ))

    # FIX-P1-RV2 (2026-07-04): "en menor medida... podrá invertir en
    # bonos/deuda/renta fija" -- declaración explícita de asignación
    # SECUNDARIA/menor a renta fija, que no debe contar como bond_dominant
    # cuando eq_dominant ya ganó por una declaración PRIMARIA explícita
    # ("invierte principalmente en valores de renta variable"). Mismo
    # patrón que el fix "podrá invertir" de Monetario (FIX-P1-MMF5), pero
    # aplicado aquí al conflicto eq_dominant/bond_dominant. Confirmado:
    # Franklin Technology (6 ISINs restantes) -- "en menor medida, el
    # fondo podrá invertir en bonos corporativos" tras el mandato primario
    # "invierte principalmente en valores de renta variable de empresas...
    # sectores tecnológicos". Verificado que "en menor medida" NO aparece
    # en ningún fondo genuinamente mixto confirmado esta sesión (BGF Global
    # Allocation, JPM Global Balanced/Income, Invesco Global Income, UBS
    # Strategic Fund Growth, AXA Global Optimal Income, JPM Global
    # Convertibles) -- señal segura y específica.
    _minor_secondary_bond = bool(re.search(
        r'en\s+menor\s+medida[^.]{0,100}podr[aá][^.]{0,80}'
        r'(?:bonos|deuda|renta\s+fija)',
        w
    ))
    # FIX-P1-RV3 (2026-07-04): "como complemento a las acciones" -- misma
    # clase de señal que "en menor medida...podrá" (secundaria/no primaria),
    # redacción distinta. Confirmado: DWS ESG Dynamic Opportunities (4
    # ISINs, 3 clasificados Mixtos + 1 Renta Fija Flexible según share
    # class -- inconsistencia entre clases del mismo fondo) -- "el fondo
    # invierte especialmente en acciones...Como COMPLEMENTO a las acciones,
    # el fondo invierte en valores de renta fija" -- mandato equity-primario
    # explícito, bonos declarados como complemento, no como parte dominante.
    _minor_secondary_bond = _minor_secondary_bond or bool(re.search(
        r'como\s+complemento\s+a\s+las\s+acciones', w
    ))
    # FIX-P1-RV4 (2026-07-05): "Además, el patrimonio del fondo puede
    # invertirse en renta fija/bonos/deuda" -- misma familia de señal
    # secundaria/no-dominante que "en menor medida...podrá" (FIX-P1-RV2) y
    # "como complemento a las acciones" (FIX-P1-RV3), redacción distinta
    # ("además" en vez de "en menor medida"/"como complemento"). Confirmado:
    # DWS ESG Europe Small-Mid Cap (2 ISINs) -- "el fondo invierte
    # fundamentalmente en acciones de emisores europeos...Además, el
    # patrimonio del fondo puede invertirse en valores de renta fija e
    # instrumentos del mercado monetario" -- mandato equity-primario
    # explícito (ver FIX-P1-NTC5), bonos declarados como asignación
    # adicional opcional, no como parte dominante.
    _minor_secondary_bond = _minor_secondary_bond or bool(re.search(
        r'además[^.]{0,60}(?:puede|podr[aá])\s+invertirse[^.]{0,120}'
        r'(?:renta\s+fija|bonos|deuda)',
        w
    ))
    # FIX-P1-RV5 (2026-07-05): "podrá invertir...en títulos de deuda...con
    # fines de gestión de tesorería" -- asignación a deuda explícitamente
    # enmarcada como gestión de TESORERÍA/liquidez operativa, no como
    # estrategia de inversión. Señal distinta pero de la misma familia que
    # las anteriores (secundaria/no-dominante). Confirmado: EDR SICAV Global
    # Resilience -- "el producto podrá invertir hasta el 25% de su
    # patrimonio neto en títulos de deuda (investment grade) con fines de
    # gestión de tesorería" tras el mandato primario "invertirá en todo
    # momento al menos el 75% en acciones internacionales" (ver FIX-P1-NTC6).
    _minor_secondary_bond = _minor_secondary_bond or bool(re.search(
        r'podr[aá]\s+invertir[^.]{0,100}(?:t[ií]tulos\s+de\s+deuda|bonos|deuda)'
        r'[^.]{0,60}gesti[oó]n\s+de\s+tesorer[ií]a',
        w
    ))
    # FIX-B1-RV-LIMITEDBOND (2026-07-13): "de forma limitada" before a bond/
    # debt/FI mention = limited/secondary bond allocation, same family as
    # FIX-P1-RV2..RV5 above. Confirmed: Guinness Global Equity Income
    # ("cartera de renta variable" primary mandate; "el fondo también puede
    # invertir, de forma limitada, en otros instrumentos como los bonos
    # soberanos y los valores de renta fija corporativa") — without this fix,
    # bond_dominant=True (via "valores de renta fija") blocked the eq_dominant
    # return and the function fell to _RF_pending. Also fixes Threadneedle UK
    # Select Real Interest which uses the same phrasing.
    _minor_secondary_bond = _minor_secondary_bond or bool(re.search(
        r'de\s+forma\s+limitada[^.]{0,200}(?:bonos|deuda|renta\s+fija|valores)',
        w
    ))
    # FIX-B1-PORDRINVERTIRSE-1 (2026-07-17): "podrá invertirse/invertir hasta
    # un/el X% en valores de renta fija/renta fija/bonos" -- asignación
    # secundaria a RF con tope porcentual explícito. El cuantificador "hasta"
    # (= up to/at most) junto a un % indica claramente que RF es SECUNDARIA
    # (límite máximo, no mandato primario). Confirmado: DWS Invest Focus Europe
    # -- "podrá invertirse hasta un 25% en valores de renta fija, instrumentos
    # del mercado monetario y saldos bancarios" tras el mandato primario "el
    # fondo invierte un mínimo del 75% en acciones". Sin este fix:
    # bond_dominant=True (vía "valores de renta fija") → eq_dominant y
    # _minor_secondary_bond no consiguen devolver RV → _RF_pending incorrecto.
    _minor_secondary_bond = _minor_secondary_bond or bool(re.search(
        r'podr[aá]\s+invertirse?\s+hasta\s+(?:un?\s+|el\s+)?\d+\s*%\s+en\s+'
        r'(?:valores\s+de\s+renta\s+fija|renta\s+fija|bonos)',
        w
    ))

    # FIX-B1-EN-UPTO-BONDS-1 (2026-07-18): English secondary bond allocation
    # with explicit cap ("invest up to X% in debt securities/bonds/fixed income").
    # Symmetrical to FIX-B1-PORDRINVERTIRSE-1 (Spanish). A capped bond position
    # cannot be the primary mandate of a fund whose objective declares a large
    # minimum equity allocation. Without this, English equity funds that mention
    # a secondary bond allowance trigger bond_dominant (via "debt securities"),
    # blocking the eq_dominant → "Renta Variable" return.
    # Confirmed: ASHOKA WO INDIA OPPT (IE00BDR0R792) -- "the fund may also
    # invest up to 20% in fixed or floating rate government and corporate
    # investment grade debt securities" triggers bond_dominant but is clearly
    # a secondary/capped allocation in a primary India equity fund.
    _minor_secondary_bond = _minor_secondary_bond or bool(re.search(
        r'invest\s+up\s+to\s+\d+\s*%[^.]{0,100}'
        r'(?:debt\s+securities|fixed[^.]{0,30}securities|bonds\b|fixed\s+income)',
        w
    ))

    # FIX-NAT-EN-SHARES-1 (2026-07-19): "may also invest … debt securities /
    # bonds / fixed income" — English secondary bond allowance NOT expressed
    # as a percentage cap (complements FIX-B1-EN-UPTO-BONDS-1 which requires
    # a number). "may also invest" signals an ADDITIONAL / OPTIONAL allocation,
    # not the primary mandate. Confirmed: MAN GLG JAPAN COREALPHA (IE00B5648R31/
    # IE00BYVDZH74) — "it may also invest in other asset classes, including debt
    # securities, currencies, deposits and other funds and in other regions" —
    # "debt securities" set bond_dominant=True while the primary equity mandate
    # was not matched. Without this fix the function returns _RF_pending at
    # line 2352 (bond_dominant + not eq_dominant + not has_equity).
    _minor_secondary_bond = _minor_secondary_bond or bool(re.search(
        r'may\s+also\s+invest[^.]{0,150}'
        r'(?:debt\s+securities|bonds?\b|fixed\s+income)',
        w
    ))

    # FIX-NAT-ES-SECBOND-1 (2026-07-19): "también podrá invertir en bonos /
    # renta fija / deuda" — Spanish optional/secondary bond allocation in a
    # fund whose primary mandate is equity. "también" (= also) signals an
    # additional/optional allocation. Confirmed: FTGF CLEARBRIDGE US VALUE
    # (IE00B19Z3920) — "el fondo invierte principalmente en valores de renta
    # variable de empresas estadounidenses... el fondo también podrá invertir
    # en bonos corporativos, bonos del estado y valores a corto plazo." —
    # "bonos corporativos" set bond_dominant=True; eq_dominant=True (from
    # "valores de renta variable"); but _minor_secondary_bond=False blocked the
    # eq_dominant → "Renta Variable" return at line 2341, and _minor_secondary_
    # equity=True (from "en menor medida, el fondo podrá invertir en valores de
    # renta variable de fuera de ee. uu.") caused line 2348 to return
    # _RF_pending instead of the correct "Renta Variable".
    # FIX-GS-EM-DEBT-1 (2026-07-19): guard against firing when the primary mandate
    # is ALREADY bonds ("invertirá principalmente en... renta fija"). In that case
    # "también podrá invertir en renta fija [de otros emisores]" extends the primary
    # bond mandate rather than adding a secondary bond allowance to an equity fund.
    # Confirmed: GS EM DEBT / GS GLOBAL HY portfolios — "La Cartera invertirá
    # principalmente en valores de renta fija... La Cartera también podrá invertir
    # en valores de renta fija cuyo emisor tenga su sede en cualquier parte del mundo"
    # — "también podrá" was incorrectly setting _minor_secondary_bond=True, causing
    # the fund to resolve as "Renta Variable" via line 2390 instead of "_RF_pending".
    _bond_primary_declared = bool(re.search(
        r'invertir[aá]\s+principalmente\s+en\s+(?:valores\s+de\s+)?renta\s+fija', w
    ))
    _minor_secondary_bond = _minor_secondary_bond or (
        bool(re.search(
            r'también\s+podr[aá]\s+invertir[^.]{0,150}'
            r'(?:bonos|renta\s+fija|deuda)',
            w
        ))
        and not _bond_primary_declared
    )

    # FIX-B1-MINOREQ-1 (2026-07-17): simétrico a _minor_secondary_bond.
    # "en menor medida, el Fondo podrá invertir en valores de renta variable/
    # acciones" -- equity EXPLÍCITAMENTE declarada como asignación SECUNDARIA
    # en un fondo cuya inversión primaria es en bonos. Cuando bond_dominant=True
    # (vía FIX-B1-BOND-PRINCIPALMENTE-2 o señales directas) y este flag activa,
    # el fondo resuelve como _RF_pending (RF Flexible/Corto) en vez de Mixtos.
    # Confirmado: Franklin Euro High Yield -- "invierte principalmente...en bonos
    # del Estado y corporativos...con calificación inferior a investment grade.
    # En menor medida, el Fondo podrá invertir en valores de renta variable".
    # Sin este fix: after BOND-PRINCIPALMENTE-2 sets bond_dominant=True AND
    # "valores de renta variable" sets eq_dominant=True → has_equity+has_bonds
    # → Mixtos (incorrecto: fondo de bonos HY con allowance minoritaria de RV).
    _minor_secondary_equity = bool(re.search(
        r'en\s+menor\s+medida[^.]{0,100}podr[aá][^.]{0,100}'
        r'(?:valores\s+de\s+renta\s+variable|renta\s+variable\b|acciones\b)',
        w
    ))

    # RV dominante sin RF → Renta Variable
    if eq_dominant and (not bond_dominant or _minor_secondary_bond):
        return "Renta Variable"

    # FIX-B1-MINOREQ-1: RF dominante + equity EXPLÍCITAMENTE secundaria
    # ("en menor medida podrá invertir en RV/acciones"). Resolver como
    # _RF_pending (RF Flexible/Corto) en vez de caer en has_equity+has_bonds
    # → Mixtos que sería incorrecto para un fondo primariamente de bonos.
    if bond_dominant and _minor_secondary_equity:
        return "_RF_pending"

    # RF dominante sin equity en absoluto → pendiente corto/flexible
    if bond_dominant and not eq_dominant and not has_equity:
        return "_RF_pending"

    # RF dominante + equity presente (mención incidental en fondos RV/Mixtos) →
    # Devolver None: la Capa 2 (nombre del fondo) lo resolverá correctamente
    if bond_dominant and not eq_dominant and has_equity:
        # FIX-NAT-BLENDED-6040-1 (2026-07-19): CNMV product-type label
        # "renta variable mixta" in the "Tipo de producto:" field of a DDF/
        # PRIIPs document is an unambiguous Mixtos declaration — it is the
        # official Spanish CNMV category for funds that hold 30-75% equity +
        # rest in fixed income. Without this guard, bond_dominant=True (from
        # the split-mandate clause "el resto en renta fija") AND has_equity=True
        # (from incidental "renta variable de" in the risk-disclaimer section)
        # cause this branch to return None — the name-based Capa 2 resolver then
        # fails to assign the correct nature. Confirmed: GEST BOUTIQUE VI BAELO
        # EUR ACC (ES0110407097) — "tipo de producto: fondo de inversión. renta
        # variable mixta internacional" + policy "30-75% de la exposición total
        # en renta variable y el resto en renta fija pública/privada".
        if "renta variable mixta" in w:
            return "Mixtos"
        return None


    # Mixto explícito
    # FIX-MIXTOS-BENCHROLE-1 (2026-07-23): "asignación de activos" /
    # "asset allocation" are overloaded: they appear as genuine multi-asset
    # mandate declarations AND as Robeco's standard benchmark-role boilerplate
    # "utiliza el índice de referencia para la asignación de activos" (how the
    # fund uses its benchmark, NOT a multi-asset mandate).  Guard the two weak
    # bare phrases with a benchmark-role negation; all other compound phrases
    # (multiactivo, renta variable y renta fija, etc.) stay unconditional.
    # Measured blast radius: exactly 4 Robeco credit/financial bond FPs flip
    # from Mixtos → _RF_pending → RFF/RFC; 22 other Robeco funds with the same
    # phrase already exit via eq_dominant/bond_dominant before reaching here.
    _BENCHROLE_CTX = re.compile(
        r'(índice de referencia para|indice de referencia para'
        r'|utiliza el índice|utiliza el indice'
        r'|uses the index|benchmark for|index for asset allocation'
        r'|referencia para la asignac)'
    )
    def _aa_not_benchrole(phrase: str) -> bool:
        j = w.find(phrase)
        if j < 0:
            return False
        pre = w[max(0, j - 80): j]
        return not _BENCHROLE_CTX.search(pre)

    if any(k in w for k in [
        "tanto acciones como bonos", "both equities and bonds",
        "equities and bonds", "stocks and bonds", "acciones y bonos",
        "renta variable y renta fija", "multiactivo", "multi-asset",
        "múltiples clases de activos",
        "varias clases de activos", "multiple asset class",
        # Señales DDF adicionales
        "renta variable y de bonos", "renta variable y bonos",
        "amplia gama de clases de activos",
        "diversas clases de activos", "diferentes clases de activos",
        "acciones y bonos y", "renta variable, renta fija",
        "volatilidad del 3", "volatilidad del 5", "volatilidad del 7",
        "volatilidad comprendida", "rango de volatilidad",
        "protección parcial permanente",  # Amundi Protect 90
        "valor liquidativo mínimo",        # capital protection
        "floor de capital", "capital floor",
    ]) or _aa_not_benchrole("asset allocation") or _aa_not_benchrole("asignación de activos"):
        return "Mixtos"

    if has_equity and has_bonds:
        if has_ar:
            return "Alternativo"
        return "Mixtos"

    if has_bonds and not has_equity:
        return "_RF_pending"
    if has_equity and not has_bonds:
        return "Renta Variable"

    # ── Multi-asset en texto completo (DDF con layout de dos columnas) ─────
    # Cubre fondos cuyo objetivo aparece a partir de pos 3500-5000 (OCR de
    # columnas dobles desplaza el texto fuera de la ventana estándar).
    # Solo frases compuestas inequívocas — no términos genéricos.
    if any(k in t for k in [
        "invierte en títulos de renta variable y en instrumentos de",
        "renta variable y en instrumentos de deuda",
        "invierte en renta variable y en renta fija",
        "equity and fixed income",
    ]):
        return "Mixtos"

    # SRRI como árbitro (ventana completa)
    m = re.search(r"\b([1-7])\s*/\s*7\b", t)
    srri = int(m.group(1)) if m else None
    if srri == 1:
        return "Monetario"
    if srri is not None and srri >= 5:
        return "Renta Variable"
    if srri == 2:
        return "_RF_pending"

    return None


def resolve_rf_subtype(name_l: str, kiid_text: str) -> str:
    """
    Decide si un fondo marcado como '_RF_pending' es RF_Corto o RF_Flexible.
    Devuelve claves INTERNAS ('RF_Corto', 'RF_Flexible') para que
    _NATURE_CANONICAL pueda mapearlas correctamente.
    Fuente única para restantes.py y detect_nature_from_kiid.

    Regla canónica (RF-RFF-POLICY-2026-07-16, estándar industrial):
        Duración máxima mandatada ≤ 3 años  →  'RF_Corto'    (Renta Fija Corto Plazo)
        Sin restricción / duración > 3 años  →  'RF_Flexible'  (Renta Fija Flexible)

    Alineamiento industrial:
      - ICE BofA Fixed Income: bucket 1-3y ("Short-Term").
      - Morningstar: categoría "Short-Term Bond" (dur. efectiva ~1-3.5y).
      - Duration_Profile canónico: 'Ultra-Short' (< 1y) y 'Short' (1-3y) → RF_Corto;
        'Intermediate' (3-7y), 'Long' (> 7y), 'Flexible' → RF_Flexible.
    """
    t = kiid_text.lower() if kiid_text else ""
    _obj_start, _obj_end = _get_obj_bounds(kiid_text or "")
    w = _extract_window(t, _obj_start, _obj_end)
    # FIX-P1-NTC (2026-07-04): ver detect_nature_from_kiid — normaliza
    # espacios en blanco dentro de la ventana para que el wrapping de línea
    # del PDF no rompa frases clave a mitad.
    w = re.sub(r'\s+', ' ', w)

    # Inflation-linked bonds: siempre RF_Flexible (indexados a inflación no son corto plazo)
    if any(k in w for k in [
        "inflation-linked", "bonos indexados a la inflación",
        "inflation linked bond", "tips ",
        "ligado a la inflación", "linked to inflation",
        "replicar la rentabilidad del", "replicación de la rentabilidad",
    ]) and any(k in w for k in [
        "bonos", "deuda", "renta fija", "bond", "fixed income", "índice", "index",
    ]):
        return "RF_Flexible"

    # FIX-P1-RFF1 (2026-07-04): "corto plazo" (bare) es demasiado genérico
    # cuando aparece en dos contextos boilerplate confirmados vía auditoría
    # del bloque RF_FLEXIBLE (68 fondos con disagreement RFC/RFF): (a)
    # definición de "instrumentos del mercado monetario" ("...instrumentos
    # del mercado monetario (es decir, títulos de deuda con vencimientos
    # a corto plazo)" -- describe QUÉ ES un instrumento monetario, no la
    # duración del fondo); (b) definición de un tipo de interés de
    # referencia ("el €str representa el tipo de interés a corto plazo en
    # euros" -- describe el BENCHMARK, no el fondo). Ambos son boilerplate
    # presente en muchos fondos RFF genuinos independientemente de su
    # propia duración. Otras señales de esta lista (p.ej. "short duration"
    # en inglés, "duración inferior") no se tocan -- se mantienen como
    # señales fiables.
    _corto_plazo_mmf_definition = bool(re.search(
        r'instrumentos del mercado monetario[^.]{0,100}corto plazo'
        r'|corto plazo[^.]{0,100}instrumentos del mercado monetario',
        w
    ))
    _corto_plazo_benchmark_definition = bool(re.search(
        r'(?:representa|refleja)[^.]{0,50}tipo de inter[eé]s a corto plazo',
        w
    ))
    _corto_plazo_reliable = (
        "corto plazo" in w
        and not _corto_plazo_mmf_definition
        and not _corto_plazo_benchmark_definition
    )

    # FIX-P1-RFC1 (2026-07-05): "duración...no (será) superior a 12 meses"
    # -- límite de duración explícito en meses. Alineado con la regla canónica
    # ≤ 3 años (RF-RFF-POLICY-2026-07-16). Confirmado:
    # AF US Short Term Bond -- "la duración media de los tipos de interés
    # del subfondo no será superior a 12 meses".
    # Extendido a 24 y 36 meses (= 2 y 3 años, ambos ≤ 3 años → RF_Corto).
    _duracion_meses_corta = bool(re.search(
        r'duraci[oó]n[^.]{0,60}no\s+(?:(?:ser[aá]|podr[aá]\s+ser|puede\s+ser)\s+)?'
        r'superior\s+a\s+(?:6|9|12|24|36)\s+meses',
        w
    ))

    # RF-RFF-POLICY-2026-07-16: límite de duración explícito en años ≤ 3.
    # Cubre:
    #   ES: "no será superior a 3 años" / "no es superior a tres años" /
    #       "no excederá de 2 años" / "inferior a 3 años" / "menor de dos años"
    #   EN: "not exceed(ing) 3 years" / "no more than 2 years" /
    #       "up to 3 years" (standalone, broader than keyword above)
    # FIX-P1-RFC-TRES-1 (2026-07-31): added "es" to verb alternation and
    #   (?:[1-3]|un|dos|tres) to cover word-form Spanish numbers alongside digits.
    _N_ANIOS = r'(?:[1-3]|un|dos|tres)'
    _duracion_anios_corta = bool(re.search(
        # ES: no (es/será/podrá ser) superior/excederá (a/de) N años;
        #     inferior/menor (a/de) N años
        r'duraci[oó]n[^.]{0,80}no\s+(?:(?:es|ser[aá]|podr[aá]\s+ser|puede\s+ser)\s+)?'
        r'(?:superior|exceder[aá])\s+(?:a\s+|de\s+)?' + _N_ANIOS + r'\s+a[ñn]'
        r'|(?:inferior|menor)\s+(?:a|de)\s+' + _N_ANIOS + r'\s+a[ñn]'
        # EN: not exceed(ing) / no more than / up to N years
        r'|duration[^.]{0,80}(?:not\s+exceed(?:ing)?|no\s+more\s+than)'
        r'\s+[1-3]\s+year',
        w
    ))

    # FIX-P1-RFC2 (2026-07-05): "fecha de vencimient" (bare) falsely matches
    # the extremely common open-ended-fund legal boilerplate "el fondo NO
    # tiene fecha de vencimiento" ("the fund has NO maturity date") --
    # exactly the OPPOSITE of a target-maturity/short-duration signal.
    # Confirmed corpus-wide (46 funds where this was the *only* corto-plazo
    # signal, all generic Corporate/Horizon/Emerging-Debt bond funds with no
    # actual short-duration language -- e.g. GAMCO Merger Arbitrage I,
    # Invesco Euro Corporate Bond, Janus Henderson Horizon Bond). Requires
    # the phrase NOT be preceded by a negation ("no tiene"/"sin"/"no
    # tendrá") within the same clause; "target maturity"/"vencimiento
    # fijo"/"fixed maturity" (genuine target-maturity fund descriptors, not
    # boilerplate) are unaffected.
    # FIX-RFC-EN-NEGATION-1 (2026-07-20): extend to English negation patterns.
    # "The Fund does not have a fixed maturity" is open-ended-fund boilerplate
    # (same semantic as Spanish "el fondo no tiene fecha de vencimiento"),
    # but "fixed maturity" alone triggered _has_vencimiento_signal=True and
    # _vencimiento_negated=False (guard was ES-only) → spurious RF_Corto for
    # EN-KIID bond funds (e.g. MS INV FD EMERG DEBT, LU0057132697).
    _vencimiento_negated = bool(re.search(
        r'(?:no\s+tiene|sin|no\s+tendr[aá])\s+fecha\s+de\s+vencimient'
        r'|does\s+not\s+have\s+a\s+fixed\s+maturit'
        r'|has\s+no\s+fixed\s+maturit'
        r'|no\s+fixed\s+maturit',
        w
    ))
    _has_vencimiento_signal = (
        ("fecha de vencimient" in w and not _vencimiento_negated)
        or "target maturity" in w
        or "vencimiento fijo" in w
        or ("fixed maturity" in w and not _vencimiento_negated)
    )

    # FIX-P1-RFC3 (2026-07-05): "short term"/"short-term" bare is too
    # generic to add as a general signal (corpus-wide check found it also
    # appears in benchmark references -- "€str (euro short term rate)" --
    # and umbrella-prospectus sibling-fund-name lists, neither describing
    # THIS fund's own duration). Narrowed to the specific pattern where the
    # document is declaring the fund's OWN legal name right before the
    # "(el «fondo»)" marker -- e.g. "Pictet - Short Term Emerging Corporate
    # Bonds (el «fondo»))", "CT (Lux) Global Emerging Market Short-Term
    # Bonds (el "fondo")". Needed as a companion to FIX-P1-RFC2: without
    # it, removing the false "no tiene fecha de vencimiento" signal would
    # have flipped these genuinely short-term-named funds (whose only
    # other corto-plazo signal was that same false trigger) from
    # `Renta Fija Corto Plazo` to `Renta Fija Flexible`. Confirmed 6 corpus
    # matches, all correct (4 preserve already-correct RFC, 2 fix a
    # separate pre-existing RFF misclassification -- Vanguard Global
    # Short-Term Bond Index Fund).
    _short_term_own_name = bool(re.search(
        r'short[\s-]term[^()]{0,60}\(el\s*[«"]fondo[»"]', w
    ))

    # Señales explícitas de corto plazo / duración ≤ 3 años en el objetivo
    # (RF-RFF-POLICY-2026-07-16: toda señal en este bloque debe ser coherente
    # con una duración máxima mandatada ≤ 3 años).
    if (_corto_plazo_reliable or _duracion_meses_corta or _duracion_anios_corta
            or _has_vencimiento_signal or _short_term_own_name or any(k in w for k in [
        "duración inferior", "duration below", "duration less than",
        "duration of less", "short duration", "ultra short", "ultrashort",
        "baja duración", "low duration", "court terme",
        "0 a 2 año", "0 a 3 año", "0 to 2 year", "0 to 3 year",
        "1 a 3 año", "1 to 3 year", "menos de 3 años", "below 3 year",
        "menos de 2 años", "below 2 year", "short-term bond",
        "horizon 202", "credit 202", "bond 202",
        # RF-RFF-POLICY-2026-07-16: señales de límite explícito ≤ 3 años
        "hasta 3 años",                   # ES: "hasta 3 años de duración"
        "up to 3 year",                   # EN: "up to 3 years" / "up to 3-year"
        "superior a 3 año",               # ES: "no (será) superior a 3 años"
        "within 3 year",                  # EN: "within 3 years"
        "no more than 3 year",            # EN: explicit 3-year cap
    ])):
        return "RF_Corto"

    # Señales en nombre
    if _name_match(name_l, NAME_SIGNALS_RF_CORTO):
        return "RF_Corto"

    # SRRI muy bajo (1/7) — último recurso cuando ninguna señal textual de
    # duración ha disparado. Nota P#6: SRRI no debería derivar Fund_Nature
    # (P#6 "SRRI ≠ classification"); este fallback persiste solo mientras
    # existan fondos sin señales textuales de duración suficientes. Eliminar
    # en Phase B (Option B) una vez el pool de señales ≤3y sea completo (R-2).
    m = re.search(r"\b([1-7])\s*/\s*7\b", t)
    if m and int(m.group(1)) == 1:
        return "RF_Corto"

    return "RF_Flexible"


# FIX-P1-DBLCLAIM (2026-07-04): tiebreaker para fondos reclamados por
# nombre a la vez por renta_variable y mixtos (u otro bloque posterior en
# el orden de ejecución). Causa raíz: mixtos.get_universe_isins() usa
# patrones de nombre muy genéricos ("growth", "income", "dynamic",
# "moderate", "conservative") que son también descriptores de ESTILO muy
# comunes en fondos de renta variable pura (p.ej. "Growth investing",
# "Income/dividend equity"). Como mixtos se ejecuta DESPUÉS de
# renta_variable en el pipeline, sobrescribe silenciosamente la
# clasificación correcta vía COALESCE. Confirmado en auditoría de 149
# fondos con doble-reclamo renta_variable+mixtos: AB American Growth
# Portfolio ("invierte...mínimo un 80%...en valores de renta variable"),
# Allianz EU EQ Growth ("mínimo del 70%...en valores de renta variable")
# -- ambos fondos de renta variable pura, mal clasificados como Mixtos.
_EQUITY_MAJORITY_PATTERN = re.compile(
    r'm[ií]nimo,?\s*(?:de|del)?\s*(?:un|el)?\s*(\d{2,3})\s*%[^.]{0,150}?'
    r'(?:renta\s*variable|acciones)',
    re.IGNORECASE,
)

# FIX-P1-EQMAJ-FRAC: algunos KIID expresan el umbral mayoritario como
# fracción en palabras ("dos terceras partes", "dos tercios") en vez de
# un porcentaje numérico. El patrón numérico de arriba no las detecta,
# lo que deja al fondo sin protección del desempate INTER-DBLCLAIM y
# permite que un bloque posterior (p.ej. mixtos) lo reclame de nuevo en
# cada ciclo (caso detectado: LU2382957772, "invierte...mínimo dos
# terceras partes de sus activos en valores de renta variable").
_EQUITY_MAJORITY_FRACTIONS = [
    (re.compile(r'dos\s+tercer[ao]s?\s+partes|dos\s+tercios', re.IGNORECASE), 66.67),
    (re.compile(r'tres\s+cuart[ao]s?\s+partes|tres\s+cuartos', re.IGNORECASE), 75.0),
    (re.compile(r'tres\s+quint[ao]s?\s+partes|tres\s+quintos', re.IGNORECASE), 60.0),
    (re.compile(r'cuatro\s+quint[ao]s?\s+partes|cuatro\s+quintos', re.IGNORECASE), 80.0),
]


# FIX-P1-EQMAJ-COMBINED (2026-07-04): "mínimo del 70%...en valores de
# renta variable O BONOS" declara un umbral para un CUBO COMBINADO
# (equity+bonos indistintamente), no una mayoría exclusiva de renta
# variable -- una frase distinta e inmediatamente posterior ("un máximo
# del 70%...puede invertirse en valores de renta variable") es la que
# realmente acota la asignación a renta variable. Confirmado: familia
# Allianz Inc&Growth / Capital Plus (11 ISINs) -- el 70% "mínimo"
# aplica a renta variable+bonos combinados (el fondo puede tener 0-70%
# en RV y 0-100% en bonos), un mandato flexible/Mixtos genuino, no
# equity-mayoritario. Causó una reclasificación incorrecta a Renta
# Variable vía INTER-DBLCLAIM anteriormente esta sesión. Se exige que
# "renta variable"/"acciones" NO esté seguido inmediatamente de "o/y
# bonos/deuda/renta fija" (cubo combinado).
_combined_bucket_tail = re.compile(
    r'^\s*(?:o|y)\s*(?:bonos|deuda|renta\s*fija)', re.IGNORECASE
)


def detect_explicit_equity_majority(kiid_text: str) -> Optional[float]:
    """
    Busca una declaración explícita de asignación MAYORITARIA a renta
    variable ("invierte...mínimo un 80%...en valores de renta variable"),
    incluyendo fracciones expresadas en palabras ("dos terceras partes").

    Devuelve el porcentaje (0-100) si se encuentra, o None. Usado como
    señal de desempate cuando un fondo es reclamado por nombre tanto por
    renta_variable como por otro bloque (mixtos/alternativos) — una
    declaración explícita de umbral mayoritario de renta variable en el
    KIID pesa más que un patrón de nombre genérico.
    """
    if not kiid_text:
        return None
    t = kiid_text.lower()
    _obj_start, _obj_end = _get_obj_bounds(kiid_text)
    w = re.sub(r'\s+', ' ', _extract_window(t, _obj_start, _obj_end))
    m = _EQUITY_MAJORITY_PATTERN.search(w)
    if m and not _combined_bucket_tail.match(w[m.end():m.end() + 40]):
        try:
            pct = float(m.group(1))
        except (ValueError, TypeError):
            pct = None
        if pct is not None and 0 < pct <= 100:
            return pct
    for _frac_re, _frac_pct in _EQUITY_MAJORITY_FRACTIONS:
        fm = _frac_re.search(w)
        if fm:
            _tail_start = fm.end()
            _renta_m = re.search(r'(?:renta\s*variable|acciones)', w[_tail_start:_tail_start + 180], re.IGNORECASE)
            if _renta_m and not _combined_bucket_tail.match(
                    w[_tail_start + _renta_m.end():_tail_start + _renta_m.end() + 40]):
                return _frac_pct
    return None


# FIX-P1-BENCH-VOTE (2026-07-04): tercera señal independiente para el
# desempate INTER-DBLCLAIM, junto a Nombre (patrón del bloque) y
# Texto-KIID (detect_explicit_equity_majority). El índice de referencia
# declarado no depende del nombre del fondo ni de cómo el KIID redacta
# el objetivo de inversión -- un fondo benchmarked contra un índice de
# renta variable puro (MSCI World, S&P 500...) es evidencia fuerte de
# Renta Variable incluso cuando el KIID no declara un umbral % explícito
# (ni numérico ni en palabras). Único uso actual: desempate en
# INTER-DBLCLAIM cuando detect_explicit_equity_majority() devuelve None.
_BENCHMARK_EQUITY_KW = [
    "msci world", "msci acwi", "msci europe", "msci emerging",
    "s&p 500", "stoxx europe", "euro stoxx", "ftse 100", "dax",
    "nasdaq 100", "russell 2000", "nikkei 225", "topix",
    "ftse all-world", "msci usa", "msci japan", "cac 40",
]
_BENCHMARK_BOND_KW = [
    "bond", "aggregate", "treasury", "gilt", "bund", "obligaciones",
    "corporate bond", "government bond", "credit index",
    "convertible bond", "high yield",
]
_BENCHMARK_MONEY_KW = [
    "estr", "€str", "eonia", "euribor", "sofr", "libor", "t-bill",
    "money market",
    # FIX-P1-BENCH-TBILL: "treasury bill" (con o sin guion, cualquier
    # vencimiento corto tipo "3-month"/"3 month") es un benchmark típico de
    # fondos monetarios VNAV/LVNAV bajo EU MMFR, no un índice de bonos --
    # distinto de "treasury" a secas (sin "bill"), que sí indica un índice
    # de bonos soberanos de duración larga (Bloomberg Aggregate Treasury
    # 1-10y, etc.). Confirmado: JPM Standard MM VNAV (3 ISINs), benchmark
    # "ICE BofA 3-month German Treasury Bill Index" -- fondo monetario
    # genuino, mal reclasificado a Renta Fija Corto Plazo antes de este fix.
    "treasury bill",
]


def detect_nature_from_benchmark(benchmark_declared: Optional[str]) -> Optional[str]:
    """
    Infiere una Fund_Nature aproximada a partir del índice de referencia
    declarado (Benchmark_Declared). Señal independiente del nombre del
    fondo y del texto del objetivo de inversión del KIID.

    No distingue subtipos de renta fija (RFC/RFF/genérica) -- devuelve
    "Renta Fija" sin más precisión; para subtipo se usan otras señales.
    Devuelve None si el benchmark no está declarado o no es reconocible.
    """
    if not benchmark_declared:
        return None
    b = benchmark_declared.lower()
    if any(k in b for k in _BENCHMARK_MONEY_KW):
        return "Monetario"
    if any(k in b for k in _BENCHMARK_EQUITY_KW):
        return "Renta Variable"
    if any(k in b for k in _BENCHMARK_BOND_KW):
        return "Renta Fija"
    return None


def resolve_nature_vote(
    name_l: str,
    kiid_text: str,
    benchmark_declared: Optional[str] = None,
) -> Tuple[Optional[str], dict]:
    """
    OPT-B (2026-07-16): 2-of-3 vote across name / KIID-text / benchmark.

    RF subtypes coarsened to 'Renta Fija' for majority matching; winning
    subtype restored from KIID (most specific) > name > resolve_rf_subtype.

    Returns:
        (nature_canonical, vote_detail)
        nature_canonical: canonical Fund_Nature string, or None if all abstain.
        vote_detail: {name, kiid, benchmark, winner_coarse, reason}
    """
    v_name_raw = detect_nature_from_name(name_l)
    v_name_can = _NATURE_CANONICAL.get(v_name_raw) if v_name_raw else None

    v_kiid_raw = detect_nature_from_kiid(kiid_text or "")
    if v_kiid_raw == "_RF_pending":
        v_kiid_raw = resolve_rf_subtype(name_l, kiid_text or "")
    v_kiid_can = _NATURE_CANONICAL.get(v_kiid_raw) if v_kiid_raw else None

    # detect_nature_from_benchmark already returns coarse "Renta Fija" (not RFC/RFF)
    v_bench = detect_nature_from_benchmark(benchmark_declared)

    def _coarse(n: Optional[str]) -> Optional[str]:
        if n in ("Renta Fija Corto Plazo", "Renta Fija Flexible"):
            return "Renta Fija"
        return n

    cn, ck, cb = _coarse(v_name_can), _coarse(v_kiid_can), v_bench

    winner_coarse: Optional[str] = None
    reason = ""
    if cn and ck and cn == ck:
        winner_coarse, reason = cn, "name+kiid"
    elif cn and cb and cn == cb:
        winner_coarse, reason = cn, "name+benchmark"
    elif ck and cb and ck == cb:
        winner_coarse, reason = ck, "kiid+benchmark"
    elif cn:
        winner_coarse, reason = cn, "name-only"
    elif ck:
        winner_coarse, reason = ck, "kiid-only"
    else:
        reason = "all-abstain"

    detail: dict = {
        "name": v_name_can, "kiid": v_kiid_can,
        "benchmark": v_bench, "winner_coarse": winner_coarse, "reason": reason,
    }

    if not winner_coarse:
        return None, detail

    if winner_coarse == "Renta Fija":
        if v_kiid_can in ("Renta Fija Corto Plazo", "Renta Fija Flexible"):
            return v_kiid_can, detail
        if v_name_can in ("Renta Fija Corto Plazo", "Renta Fija Flexible"):
            return v_name_can, detail
        rf_raw = resolve_rf_subtype(name_l, kiid_text or "")
        return _NATURE_CANONICAL.get(rf_raw, "Renta Fija Flexible"), detail

    return winner_coarse, detail


# ============================================================
# resolve_nature_evidence — clasificador ponderado por evidencia
# (OPT-B3 2026-07-16)
# ============================================================
# Sustituye la lógica de "voto 2-de-3 con pesos iguales" (resolve_nature_vote)
# por una combinación de evidencia ponderada por la FIABILIDAD MEDIDA de cada
# fuente POR NATURALEZA (la fiabilidad no es un escalar: p.ej. para Monetario
# el nombre acierta 100% pero el KIID sólo 64.8%; para Renta Variable el KIID
# acierta 97.5% y el nombre ~87%). Los pesos se siembran con los porcentajes
# de acuerdo medidos contra la volatilidad realizada (srri_nav) el 2026-07-16
# y se recalibran en el paso 4. La volatilidad realizada actúa como
# restricción/desempate (veto de bandas imposibles), no como voto puntual.
#
# Devuelve (nature_canonical, confidence, evidence_trace). confidence =
# (top - second)/top del score combinado; baja confianza -> DQ flag (lo decide
# el pipeline en la integración, paso 5). NO reescribe pipeline todavía.

# Matriz de fiabilidad weight[fuente][naturaleza] = P(cierto=N | fuente dice N),
# sembrada con el % in-band medido vs srri_nav (2026-07-16).
_W_KIID_BY_NATURE: dict = {
    "Monetario":              0.65,
    "Renta Fija Corto Plazo": 0.93,
    "Renta Fija Flexible":    0.92,
    "Mixtos":                 0.83,
    "Renta Variable":         0.98,
    "Alternativo":            0.91,
    "Estructurado":           1.00,
}
_W_NAME_BY_NATURE: dict = {
    "Monetario":              1.00,
    "Renta Fija Corto Plazo": 1.00,
    "Renta Fija Flexible":    0.89,
    "Mixtos":                 0.88,
    "Renta Variable":         0.87,
    "Alternativo":            0.87,
}
# Benchmark: señal gruesa (asset-class). "Renta Fija" reparte a RFC+RFF (la
# volatilidad desempata el subtipo). Pesos recalibrados contra srri_nav
# (2026-07-16): RF 97.9%, RV 91.3%, Monetario SÓLO 22.1% -- los benchmarks de
# tipo cash/overnight (€STR, EONIA) se usan como hurdle rate por fondos de
# retorno absoluto y corto plazo, NO son indicador de monetario. Por eso el
# benchmark=Monetario se trata como NO informativo (ver resolve_nature_evidence).
_W_BENCH_BY_NATURE: dict = {
    "Monetario":       0.22,   # no informativo — hurdle rate, no naturaleza
    "Renta Variable":  0.91,
    "Renta Fija":      0.98,
}

# FIX-NLC-MSBENCH-1 (2026-07-21): map Morningstar asset_class values to the
# coarse nature understood by resolve_nature_evidence. Treated symmetrically
# with the KIID-benchmark vote (same machinery: corroboration + arbitration
# candidate). "Rate" → None: cash/overnight = hurdle, not nature (mirrors the
# existing v_bench=="Monetario" discard). Mixtos stays Mixtos (no RF→RFF
# mapping needed for this asset class value).
_MS_ASSET_CLASS_TO_NATURE: dict = {
    "Equity":        "Renta Variable",
    "Fixed Income":  "Renta Fija",   # coarse; handled as RF→RFF by v_msbench_rf
    "Mixed":         "Mixtos",
    # "Rate" intentionally absent → maps to None
}

# Naturalezas donde el NOMBRE es de ALTA PRECISIÓN y medible-mente más fiable
# que el KIID (override guardado). Se deriva de las matrices -> se auto-mantiene
# al recalibrar. Requiere precisión de nombre alta (>=0.95) y margen claro
# sobre KIID (>0.03) para NO incluir naturalezas donde el nombre sobre-reclama
# (p.ej. Mixtos 0.88, con "growth/income/dynamic" genéricos). Con los pesos
# actuales = {'Monetario' (1.00 vs 0.65), 'Renta Fija Corto Plazo' (1.00 vs 0.93)}.
# Validado ex-ante 92.6% (vs KIID-solo 92.7%) con este conjunto.
_NAME_DOMINANT_NATURES: set = {
    n for n, wn in _W_NAME_BY_NATURE.items()
    if wn >= 0.95 and wn > _W_KIID_BY_NATURE.get(n, 1.0) + 0.03
}

# Bandas de SRRI realizado (srri_nav) esperadas por naturaleza — bandas de
# volatilidad CESR/ESMA, confirmadas por la medición del corpus.
_NATURE_VOL_BANDS: dict = {
    "Monetario":              {1, 2},
    "Renta Fija Corto Plazo": {2, 3, 4},
    "Renta Fija Flexible":    {3, 4},
    "Mixtos":                 {4, 5},
    "Renta Variable":         {5, 6, 7},
    "Alternativo":            {3, 4, 5},
    "Estructurado":           {3, 4, 5, 6},
}
# Bandas de volatilidad INEQUÍVOCAS: el nivel de volatilidad realizada sólo
# admite una clase de activo, sin solape entre naturalezas.
#   1  -> vol < 0.5% : sólo Monetario
#   6  -> vol 15-25% : sólo Renta Variable (territorio equity)
#   7  -> vol > 25%  : sólo Renta Variable
# En estas bandas la volatilidad SÍ corrige una primaria inconsistente aunque
# la distancia de banda sea 1 (no es circular: a SRRI 6-7 un fondo se comporta
# como equity, es un hecho de mercado, no un solape difuso como en bandas 3-5).
_VOL_UNAMBIGUOUS_BANDS: set = {1, 6, 7}

# (P#6 scope 2026-07-17): se eliminó `_VOL_BAND_FALLBACK` — derivar Fund_Nature
# de la banda de volatilidad cuando todas las señales ex-ante abstienen violaba
# P#6 ("SRRI/volatilidad ≠ clasificación"). Ahora, sin señal documental ->
# None (Restantes). La volatilidad sólo veta/arbitra entre candidatos propuestos.


def _vol_consistency_factor(nature: str, band: Optional[int]) -> float:
    """
    Multiplicador de consistencia con la volatilidad realizada.
    band=None -> 1.0 (sin información, neutro).
    en-banda -> 1.0 · adyacente (±1) -> 0.6 · lejana (>=2) -> 0.25 (veto de facto).
    """
    if band is None:
        return 1.0
    bands = _NATURE_VOL_BANDS.get(nature)
    if not bands:
        return 1.0
    if band in bands:
        return 1.0
    dist = min(abs(band - b) for b in bands)
    if dist == 1:
        return 0.6
    return 0.25


def resolve_nature_evidence(
    name_l: str,
    kiid_text: str,
    benchmark_declared: Optional[str] = None,
    srri_nav_band: Optional[int] = None,
    ext_asset_class: Optional[str] = None,
) -> Tuple[Optional[str], float, dict]:
    """
    Clasificador de Fund_Nature ponderado por evidencia (OPT-B3).

    Fuentes (peso por fiabilidad medida por naturaleza):
      - KIID raw text    (detect_nature_from_kiid)        — señal ex-ante primaria
      - Name prefilter   (detect_nature_from_prefilter)   — precisa pero de baja cobertura
      - Benchmark        (detect_nature_from_benchmark)   — gruesa (asset-class, KIID)
      - MS asset_class   (_MS_ASSET_CLASS_TO_NATURE)      — Morningstar categorization
      - Volatilidad realizada (srri_nav_band 1-7)         — restricción/desempate

    Args:
        name_l: nombre en minúsculas.
        kiid_text: texto KIID crudo.
        benchmark_declared: benchmark declarado (o None).
        srri_nav_band: SRRI realizado desde NAV (1-7) o None si no hay histórico.
        ext_asset_class: Morningstar asset_class string (e.g. "Equity", "Mixed",
            "Fixed Income", "Rate") o None. FIX-NLC-MSBENCH-1 (2026-07-21):
            tratado simétricamente con el voto del benchmark KIID — añade
            corroboración y candidato de arbitraje, NUNCA anula el primario KIID.

    Returns:
        (nature_canonical, confidence, evidence_trace)
        nature_canonical: nombre canónico de Fund_Nature, o None si no hay
            ninguna evidencia (ni ex-ante ni volatilidad).
        confidence: (top-second)/top del score combinado, en [0,1].
        evidence_trace: dict con votos por fuente, scores por naturaleza,
            banda de volatilidad, ganador, confianza y motivo.
    """
    # ── Votos ex-ante ────────────────────────────────────────────────────────
    v_name = detect_nature_from_prefilter(name_l or "")            # ya canónico o None

    v_kiid_raw = detect_nature_from_kiid(kiid_text or "")
    if v_kiid_raw == "_RF_pending":
        v_kiid_raw = resolve_rf_subtype(name_l or "", kiid_text or "")
    v_kiid = _NATURE_CANONICAL.get(v_kiid_raw) if v_kiid_raw else None

    v_bench = detect_nature_from_benchmark(benchmark_declared)     # Monetario/RV/"Renta Fija"/None
    # benchmark=Monetario NO es informativo (22.1% vs vol: cash/overnight es
    # hurdle rate, no naturaleza) -> se descarta para primary/corroboración.
    if v_bench == "Monetario":
        v_bench = None
    v_bench_rf = "Renta Fija Flexible" if v_bench == "Renta Fija" else v_bench

    # FIX-NLC-MSBENCH-1 (2026-07-21): Morningstar asset_class → coarse nature.
    # "Rate" (cash/overnight hurdle) → None, same logic as v_bench=="Monetario".
    v_msbench = _MS_ASSET_CLASS_TO_NATURE.get(ext_asset_class) if ext_asset_class else None
    # RF→RFF for arbitration (mirrors v_bench_rf), Mixed stays Mixtos
    v_msbench_rf = "Renta Fija Flexible" if v_msbench == "Renta Fija" else v_msbench

    trace: dict = {
        "name": v_name, "kiid": v_kiid, "benchmark": v_bench,
        "msbench": v_msbench,
        "srri_nav_band": srri_nav_band,
        "primary": None, "primary_source": None,
        "winner": None, "confidence": 0.0, "reason": "",
    }

    # ── Selección PRIMARIA — arquitectura KIID-primary con override guardado ──
    # (calibrada 2026-07-16: la mezcla simétrica (sum 90.6% / argmax 91.6%) es
    #  INFERIOR a KIID-solo (92.7%); KIID-primary + override de las naturalezas
    #  donde el nombre es medible-mente más fiable (Monetario/RFC = 100%) iguala
    #  el techo ex-ante con cobertura completa. Ex-ante medido: 92.6%.)
    primary: Optional[str] = None
    primary_source: Optional[str] = None
    if v_name in _NAME_DOMINANT_NATURES:          # nombre domina para esta naturaleza
        primary, primary_source = v_name, "name"
    elif v_kiid:                                  # KIID = fuente primaria
        primary, primary_source = v_kiid, "kiid"
    elif v_name:                                  # cobertura: nombre
        primary, primary_source = v_name, "name"
    elif v_bench_rf:                              # cobertura: benchmark KIID (RF->RFF)
        primary, primary_source = v_bench_rf, "benchmark"
    elif v_msbench_rf:                            # cobertura: Morningstar asset_class
        primary, primary_source = v_msbench_rf, "msbench"

    # ── Sin evidencia ex-ante: NO se deriva naturaleza de la volatilidad ─────
    # P#6 (scope 2026-07-17): la volatilidad realizada NUNCA deriva Fund_Nature;
    # sólo veta/arbitra entre naturalezas ya propuestas por señales documentales
    # (KIID/nombre/benchmark). Si todas abstienen -> None (el pipeline lo lleva a
    # la clasificación mínima 'Restantes'), nunca una conjetura por banda de vol.
    if primary is None:
        trace["reason"] = "all-abstain"
        return None, 0.0, trace

    trace["primary"], trace["primary_source"] = primary, primary_source

    # ── Corroboración entre fuentes (para la confianza) ──────────────────────
    def _agrees(vote: Optional[str]) -> bool:
        if not vote:
            return False
        if vote == primary:
            return True
        # benchmark grueso "Renta Fija" corrobora cualquier subtipo RF
        return vote == "Renta Fija" and primary in (
            "Renta Fija Corto Plazo", "Renta Fija Flexible")
    corroborators = sum(_agrees(v) for v in (
        v_kiid   if primary_source != "kiid"      else None,
        v_name   if primary_source != "name"      else None,
        v_bench  if primary_source != "benchmark" else None,
        v_msbench if primary_source != "msbench"  else None,  # FIX-NLC-MSBENCH-1
    ))

    # ── Veto/ARBITRAJE por volatilidad realizada (P#6-compliant) ─────────────
    # La volatilidad realizada VETA una naturaleza primaria incompatible con la
    # banda y ARBITRA entre las naturalezas YA PROPUESTAS por otras señales
    # (v_kiid / v_name / v_bench_rf). NUNCA introduce una naturaleza que ninguna
    # señal documental propuso (eso sería derivar Nature de la volatilidad ->
    # P#6). Si la primaria es incompatible pero NINGÚN candidato ex-ante es
    # consistente con la banda, se MANTIENE la primaria con confianza baja (queda
    # marcada NATURE_LOW_CONFIDENCE para revisión) — no se fabrica una respuesta.
    #
    # Umbral: vf<=0.25 (banda IMPOSIBLE) siempre; en bandas INEQUÍVOCAS {1,6,7}
    # (vol admite una sola clase de activo) también inconsistencia adyacente
    # (vf<1.0). En bandas 3-5 (solape difuso) sólo lo imposible, para no dejar
    # que la vol domine la discriminación fina ex-ante (RFC vs RFF, Mixtos vs Alt).
    winner = primary
    vf = _vol_consistency_factor(primary, srri_nav_band)
    reason = primary_source
    # FIX-MMF-VOL-VETO-1 (2026-07-23): a Monetario primary at realized band ≥3
    # is structurally incompatible with a money-market mandate (MMFR/SRRI
    # standard: genuine MMF ≤ band 2).  The existing vf path leaves band-3 at
    # vf=0.6 (adjacent, not vetoed) — add an explicit Monetario clause so band
    # ≥3 always triggers the arbitration pass.  P#6-compliant: vol only
    # VETOES the primary and ARBITRATES among existing ex-ante candidates;
    # when no RF alt exists the winner stays Monetario (graceful degradation).
    _vol_correct = (
        vf <= 0.25
        or (srri_nav_band in _VOL_UNAMBIGUOUS_BANDS and vf < 1.0)
        or (primary == "Monetario" and srri_nav_band is not None and srri_nav_band >= 3)
    )
    if _vol_correct and srri_nav_band is not None:
        # Sólo candidatos EX-ANTE (propuestos por señales documentales), nunca
        # una naturaleza fabricada desde la banda de volatilidad.
        alts = [v for v in (v_kiid, v_name, v_bench_rf, v_msbench_rf)  # FIX-NLC-MSBENCH-1
                if v and v != primary
                and _vol_consistency_factor(v, srri_nav_band) >= 1.0]
        if alts:
            winner = alts[0]
            reason = f"{primary_source}->vol-arbitrated"
            vf = 1.0

    # ── Confianza ────────────────────────────────────────────────────────────
    base_w = (_W_NAME_BY_NATURE if primary_source == "name"
              else _W_KIID_BY_NATURE if primary_source == "kiid"
              else _W_BENCH_BY_NATURE).get(winner, 0.70)
    confidence = min(1.0, base_w + 0.10 * corroborators) * vf
    if corroborators:
        reason += f"+{corroborators}corrob"
    if srri_nav_band is not None:
        reason += f"|vol{srri_nav_band}"

    trace.update(winner=winner, confidence=round(confidence, 3), reason=reason)
    return winner, round(confidence, 3), trace


# BL-44-FX / Asset_Currency (2026-07-05): mapa nombre->divisa. Cubre las 6
# divisas realmente presentes en Fund_Currency en este corpus (EUR 2343,
# USD 771, GBP 22, JPY 12, CHF 11, CNH 8). Generalizado a CUALQUIER par de
# divisas (no solo "divisa extranjera declarada vs. EUR implícito"): el
# mismo riesgo cambiario aplica igual a un fondo "Euro Bonds" con clase de
# participación USD que a un fondo "US Dollar" con clase de participación
# EUR.
_ASSET_CURRENCY_NAME_MAP = [
    (re.compile(r'\bus\s*dollar\b|\busd\b', re.IGNORECASE), 'USD'),
    (re.compile(r'\beuro\b|\beur\b', re.IGNORECASE), 'EUR'),
    (re.compile(r'\blibra\b|\bsterling\b|\bgbp\b', re.IGNORECASE), 'GBP'),
    (re.compile(r'\byen\b|\bjpy\b', re.IGNORECASE), 'JPY'),
    (re.compile(r'franco\s+suizo|swiss\s+franc|\bchf\b', re.IGNORECASE), 'CHF'),
    (re.compile(r'\byuan\b|renminbi|\bcny\b|\bcnh\b', re.IGNORECASE), 'CNH'),
]

# El sufijo final "<código de clase> <divisa> ACC/INC/DIS/CAP" es una
# etiqueta de CLASE DE PARTICIPACIÓN (casi siempre coincide con
# Fund_Currency por construcción), no una declaración de la divisa de los
# ACTIVOS del fondo. Sin enmascararlo, un nombre como "PICTET USD GOV BDS I
# EUR ACC" (activos en USD, clase en EUR -- Fund_Currency=EUR, descalce
# genuino) resolvía la coincidencia de "EUR ACC" al final ANTES que "USD"
# al principio, ocultando el descalce real. Se enmascara antes de buscar.
_TRAILING_SHARE_CLASS_SUFFIX = re.compile(
    r'\b(?:eur|usd|gbp|chf|jpy|cnh|cny)\s*(?:acc|inc|dis|cap)\.?\s*$',
    re.IGNORECASE,
)

# FIX-ASSET-CCY-1 (2026-07-05): una divisa adyacente a "HDG"/"HEDGE(D)" (en
# cualquier orden, en cualquier posición del nombre, no solo al final) es
# el DESTINO de cobertura de la clase de participación, no la divisa de los
# activos -- p.ej. "JPM US VALUE A EUR HDG ACC" (fondo de renta variable de
# EE.UU., clase cubierta a EUR) o "BGF EURO BOND D2 (USD HDG) ACC" (fondo
# de bonos en EUR, clase cubierta a USD). Confirmado corpus-wide: sin este
# enmascarado, "EUR HDG" en un fondo "US Value"/"Emerging"/"Global Macro"
# devolvía 'EUR' (activo genuino desconocido o multi-divisa, no EUR), y
# "(USD HDG)" en "BGF EURO BOND" ganaba sobre "EURO" (la señal real) solo
# por el orden de comprobación de patrones -- ver también el cambio a
# "coincidencia más a la izquierda gana" más abajo.
# FIX-ASSET-CCY-2 (2026-07-06): extend with abbreviated hedge suffixes seen
# in fund names. Previously only "HDG"/"HEDGE"/"HEDGED" were stripped; funds
# using truncated forms returned wrong Asset_Currency from the name extractor:
#   "EUR HED"  → FIDELITY F.INT.BOND A EUR HED  (truncated "hedged")
#   "EUR HGD"  → JPM GLOBAL MACRO (EUR HGD) A   (transposed H-G-D vs H-D-G)
#   "EUR HE"   → SISF STRATEGIC BO.EUR HE.B ACC  (two-char truncation)
#   "EURH"     → GS PATRIM BAL SUST EURH ACC      (fused, no space)
#   "EUR H"    → PIMCO INCOME "INV" EUR H ACC     (bare H, standalone word)
# All these are share-class hedge designators, not asset currencies. After
# stripping, the name extractor returns None → KIID text determines the actual
# asset currency (which is None or USD for global/multi-currency funds).
# Safe: \b guards prevent matching currency prefixes inside longer words;
# the standalone-H pattern \s+h\b only matches "H" as a word by itself.
_HEDGE_TARGET_CURRENCY = re.compile(
    r'\b(?:eur|usd|gbp|chf|jpy|cnh|cny)\s*(?:h(?:dg|gd|edg(?:e|ed)?|ed|e))\b'
    r'|\b(?:h(?:dg|gd|edg(?:e|ed)?|ed|e))\s*(?:eur|usd|gbp|chf|jpy|cnh|cny)\b'
    # fused form: EURH, USDH etc. (currency immediately followed by bare H)
    r'|\b(?:eur|usd|gbp|chf|jpy|cnh|cny)h\b'
    # space-separated bare H: "EUR H ACC" (H as standalone word after currency)
    r'|\b(?:eur|usd|gbp|chf|jpy|cnh|cny)\s+h\b',
    re.IGNORECASE,
)

# Techo de plausibilidad para detect_fx_share_class_mismatch -- el riesgo
# puramente cambiario entre divisas mayores (EUR/USD/GBP/JPY/CHF/CNH)
# explica una volatilidad adicional moderada (típicamente SRRI 3-4 sobre
# una base casi nula), no una volatilidad extrema. Un SRRI por encima de
# este techo indica que algo más (no solo la divisa) está impulsando el
# riesgo, y NO debe eximirse. En la práctica BL-44 nunca observa SRRI>=5 en
# sus dos condiciones de disparo (Monetario/RF Corto) porque una guarda
# previa -- P08 en restantes.py -- ya fuerza esos casos a Renta Variable
# antes de llegar aquí (confirmado: 0 fondos con Nature Monetario/RF Corto
# y SRRI>=5 en todo el corpus) -- este techo es una defensa explícita
# adicional, no un cambio de comportamiento actual.
_FX_PLAUSIBLE_SRRI_CEILING = 4

# ─────────────────────────────────────────────────────────────────────────────
# Valor centinela "indeterminado por naturaleza" para Asset_Currency
# (BL-ASSET-CCY-MULTI, 2026-07-11)
# ------------------------------------------------------------------------------
# Distingue dos poblaciones que ANTES colapsaban ambas en NULL:
#   MCY  = el fondo declara EXPLÍCITAMENTE un mandato multi-divisa ("euros u
#          otras divisas", "dólares estadounidenses o divisas locales", o un
#          nombre "MULTICURRENCY"/"MULTIDIVISA") -> indeterminación REAL, no
#          un dato faltante. Población identificable y aislable.
#   None = no hay señal de divisa alguna -> desconocido/no descubierto.
# Código de 3 letras (misma forma que EUR/USD/...); NO es un código ISO-4217
# asignado, por lo que no colisiona con ninguna divisa real. Ver el principio
# de diseño "Indeterminado categórico vs NULL" en PRINCIPIOS_DISENO.md.
# Consumidores (p.ej. detect_fx_share_class_mismatch) deben tratarlo como
# "no es una divisa única", NUNCA compararlo como si fuera EUR/USD.
ASSET_CURRENCY_MULTI = "MCY"

# Señal EXPLÍCITA de multi-divisa en el propio nombre del fondo -> MCY
# (analogía en nombre de la continuación multi-divisa que detecta el extractor
# de texto KIID). Solo tokens inequívocos; no infiere multi por ausencia.
_NAME_MULTI_CURRENCY = re.compile(
    r'\b(?:multi[\s\-]?currency|multi[\s\-]?divisa|multidivisa)\b',
    re.IGNORECASE,
)


def detect_asset_currency_from_name(fund_name: Optional[str]) -> Optional[str]:
    """
    Infiere la divisa de los ACTIVOS/estrategia del fondo (distinta de
    Fund_Currency, la divisa de la CLASE DE PARTICIPACIÓN) a partir de su
    nombre -- p.ej. "SISF US DOLLAR LIQUIDITY" -> 'USD', "PICTET EUR BONDS"
    -> 'EUR'. Enmascara el sufijo final "<divisa> ACC/INC/DIS/CAP" antes de
    buscar, ya que ese sufijo es la etiqueta de la clase de participación
    (coincide con Fund_Currency por construcción), no la divisa de los
    activos -- p.ej. "PICTET USD GOV BDS I EUR ACC" declara activos en USD
    con una clase de participación EUR; sin el enmascarado, la búsqueda
    encontraría "EUR" (del sufijo) antes que "USD" (la señal real).

    Solo es un concepto bien definido para fondos con mandato de divisa
    ÚNICA dominante (monetarios, bonos gubernamentales de una sola divisa);
    fondos diversificados/globales no declaran una única divisa en el
    nombre y correctamente devuelven None -- NULL en Asset_Currency para
    esos fondos es semánticamente correcto (múltiples divisas), no un dato
    faltante. Ver Portfolio_Currency (eliminado en schema v20, 98.7% NULL)
    para el precedente de un extractor de texto KIID demasiado literal que
    intentaba resolver este mismo concepto sin éxito -- esta función usa el
    nombre del fondo en su lugar, señal de mucha mayor cobertura para
    exactamente esta población de fondos.

    También enmascara menciones de divisa-destino-de-cobertura ("EUR HDG",
    "(USD HDG)") en cualquier posición (ver FIX-ASSET-CCY-1) y, de entre
    las menciones de divisa restantes, toma la que aparece MÁS A LA
    IZQUIERDA en el nombre (no la primera del orden arbitrario del mapa) --
    la divisa que define la estrategia del fondo casi siempre se menciona
    antes que cualquier artefacto de clase de participación.
    """
    if not fund_name:
        return None
    _name_for_search = _TRAILING_SHARE_CLASS_SUFFIX.sub('', fund_name)
    _name_for_search = _HEDGE_TARGET_CURRENCY.sub('', _name_for_search)
    # Señal EXPLÍCITA de multi-divisa en el nombre -> MCY (indeterminado por
    # naturaleza, no dato faltante). Ver ASSET_CURRENCY_MULTI. Se comprueba
    # antes del barrido de divisas únicas: un nombre "MULTICURRENCY EUR ACC"
    # es multi-divisa, aunque el sufijo de clase mencione una divisa.
    if _NAME_MULTI_CURRENCY.search(_name_for_search):
        return ASSET_CURRENCY_MULTI
    best_match = None
    best_pos = None
    for pattern, currency_code in _ASSET_CURRENCY_NAME_MAP:
        m = pattern.search(_name_for_search)
        if m and (best_pos is None or m.start() < best_pos):
            best_pos = m.start()
            best_match = currency_code
    return best_match


# FIX-FUNDCCY-2 (2026-07-05): extractor de Fund_Currency desde el sufijo de
# clase de participación en el propio nombre del fondo ("...A EUR ACC" ->
# 'EUR') -- la MISMA señal que _TRAILING_SHARE_CLASS_SUFFIX enmascara para
# Asset_Currency (porque ahí es ruido de clase de participación), pero aquí
# es precisamente la señal que se busca (Fund_Currency = divisa de la clase
# de participación). Usado como CROSS-VALIDACIÓN de la extracción basada en
# texto KIID (kiid_parser._detect_fund_currency), no como sustituto: tras
# encontrar y corregir FIX-FUNDCCY-1 (bug real en el extractor KIID, familia
# JPM, 118 fondos), una comprobación de los restantes casos encontró
# igualmente 2 falsos positivos de ESTA señal (el nombre sugería una divisa
# pero la propia tabla de costes del KIID confirmaba otra) -- ninguna de las
# dos señales es fuente de verdad absoluta, así que un desacuerdo debe
# marcarse para revisión, no resolverse automáticamente a favor de una u
# otra.
_FUND_CURRENCY_NAME_SUFFIX = re.compile(
    r'\b(EUR|USD|GBP|CHF|JPY|CNH|CNY)\s*(?:ACC|INC|DIS|CAP)\.?\s*\Z',
    re.IGNORECASE,
)


def detect_fund_currency_from_name(fund_name: Optional[str]) -> Optional[str]:
    """
    Extrae la divisa de la clase de participación desde el sufijo final del
    nombre del fondo ("...A EUR ACC" -> 'EUR', "...D USD INC" -> 'USD').
    Devuelve None si el nombre no termina en ese patrón (fondos con nombre
    truncado, sufijo distinto, o sin sufijo de divisa reconocible).

    Pensado para CROSS-VALIDAR el resultado de kiid_parser.
    _detect_fund_currency() (extraído del texto KIID), no para sustituirlo
    automáticamente -- ver FIX-FUNDCCY-2.
    """
    if not fund_name:
        return None
    m = _FUND_CURRENCY_NAME_SUFFIX.search(fund_name)
    if not m:
        return None
    code = m.group(1).upper()
    return "CNH" if code == "CNY" else code


# FIX-ASSET-CCY-2 (2026-07-05): fallback a texto KIID cuando el nombre del
# fondo no declara divisa. A diferencia de la Portfolio_Currency eliminada
# en v20 (frases literales tipo "the reference currency of the portfolio
# is EUR" que casi nunca aparecen), este extractor busca el verbo
# "denominado(s)/denominada(s)/expresado(s)/expresada(s) en <divisa
# concreta>" (ambos géneros -- "bonos denominados"/"deuda denominada") y
# clasifica cada coincidencia según la palabra-sujeto MÁS CERCANA al verbo
# dentro de la ventana previa: si es un sujeto de ACTIVO ("activos",
# "bonos", "valores", "títulos", "deuda", "renta fija", "instrumentos del
# mercado monetario") se acepta; si es un sujeto de CLASE DE PARTICIPACIÓN
# u OBJETIVO/RENTABILIDAD ("acciones", "participaciones", "clase",
# "capital", "apreciación", "rentabilidad", "cuota de inversión") se
# descarta. Usar el sujeto MÁS CERCANO (no "aparece en algún punto de la
# ventana") es necesario -- p.ej. "el índice mide la RENTABILIDAD de los
# VALORES denominados en euros" tiene ambas palabras en la ventana, pero
# "valores" (más cercana) es el sujeto gramatical real, no "rentabilidad".
# Confirmado corpus-wide contra los ~2.372 fondos sin señal de nombre (147
# coincidencias limpias tras excluir falsos positivos).
_KIID_CURRENCY_NAME_MAP = [
    (r'd[oó]lares?\s+estadounidenses', 'USD'),
    (r'euros', 'EUR'),
    (r'libras?\s+esterlinas', 'GBP'),
    (r'yenes?(?:\s+japoneses)?', 'JPY'),
    (r'francos?\s+suizos', 'CHF'),
    (r'yuanes|renminbi', 'CNH'),
]
_KIID_CURRENCY_VERB = re.compile(
    r'(?:denominad[oa]s?|expresad[oa]s?)\s+en\s+('
    + '|'.join(cur_re for cur_re, _ in _KIID_CURRENCY_NAME_MAP) + r')',
    re.IGNORECASE,
)
_KIID_ASSET_SUBJECT = re.compile(
    r'\b(?:activos?|bonos?|valores|t[ií]tulos'
    r'|instrumentos?\s+del\s+mercado\s+monetario|deuda|renta\s+fija)\b',
    re.IGNORECASE,
)
# "acciones"/"participaciones"/"clase" = clase de participación, no activos.
# "capital"/"apreciación"/"rentabilidad" = objetivo/rendimiento medido en
# esa divisa (marco de referencia del retorno, no divisa de los activos).
# "cuota de inversión" = mecanismo de acceso específico (p.ej. QFII/RQFII),
# demasiado estrecho para representar la divisa global del fondo.
_KIID_EXCLUDE_SUBJECT = re.compile(
    r'\b(?:acciones|participaci[oó]n(?:es)?|clase|capital|apreciaci[oó]n'
    r'|rentabilidad|cuota\s+de\s+inversi[oó]n)\b',
    re.IGNORECASE,
)
_KIID_NEGATION = re.compile(r'\bno\s*$')
# Descalifica una coincidencia si, tras la divisa encontrada, el texto
# continúa con una segunda divisa/enumeración ("...euros u otras divisas",
# "...dólares estadounidenses, otras monedas del g7", "...o divisas
# locales") -- señal de mandato MULTI-divisa, no de una única divisa
# dominante. Sin este guard: CARMIGNAC ("euros u otras divisas"), PICTET
# GL SUS CRED HI ("euros (eur) o dólares estadounidenses"), UBS ASIA
# FLEXIBLE ("dólares estadounidenses o divisas locales") y varios fondos
# DWS/Deutsche Invest Asian Bonds ("dólares estadounidenses, otras
# monedas del g7 y diversas monedas de la región asia-pacífico") habrían
# quedado incorrectamente etiquetados con una sola divisa de una cartera
# explícitamente multi-divisa.
# FIX-ASSET-CCY-3 (2026-07-06): two bugs fixed:
# Bug 1 — `^\s*` consumed leading space, then `\s+[ouy]` required another
#   space that no longer existed → "o en otras monedas" never matched.
#   Fix: changed `\s+[ouy]` to `\s*[ouy]` (zero-or-more spaces before conjunction).
# Bug 2 — Spanish "o en otras monedas" ("or in other currencies") has preposition
#   "en" between the conjunction and the currency phrase, which the original
#   pattern didn't account for. `(?:en\s+)?` added after the conjunction group
#   handles both "o otras monedas" and "o en otras monedas".
# Confirmed fix on M&G (LU) OPTIMAL INCOME AH (LU1670724373): tail
# " o en otras monedas con cobertura en eur" now recognized as multi-currency
# → detect_asset_currency_from_kiid_text returns None instead of wrong EUR.
_KIID_MULTI_CCY_CONTINUATION = re.compile(
    r'^\s*(?:\([a-z]{3}\)\s*)?(?:,|\s*[ouy]\s+)(?:en\s+)?\s*(?:otr[ao]s?\s+)?'
    r'(?:divisas?|monedas?'
    r'|d[oó]lares?(?:\s+estadounidenses)?|euros|libras?(?:\s+esterlinas)?'
    r'|yenes?(?:\s+japoneses)?|francos?(?:\s+suizos)?|yuanes|renminbi)',
    re.IGNORECASE,
)
# Descalifica una coincidencia introducida por lenguaje permisivo/opcional
# ("los activos denominados en renminbis PODRÁN ser invertidos") -- misma
# familia de señal que las guardas "podrá"/"puede" ya usadas en
# detect_nature_from_kiid (asignación secundaria/opcional, no mandato
# primario). Confirmado: DWS China Bonds (RMB mencionado como asignación
# opcional tras un mandato primario en USD/dólares).
_KIID_PERMISSIVE_CONTINUATION = re.compile(
    r'\b(?:podr[aá]n?|puede[n]?)\s+ser\s+invertid', re.IGNORECASE
)


def _kiid_closest_currency_subject(before_text: str) -> Optional[str]:
    """Devuelve 'asset'/'exclude' según cuál de los dos vocabularios
    (sujeto de activo vs. sujeto de clase/objetivo) aparece MÁS CERCA
    (más a la derecha) del verbo "denominado(s) en"/"expresado(s) en"
    dentro de la ventana previa. None si ninguno aparece."""
    last_pos, last_kind = -1, None
    for m in _KIID_ASSET_SUBJECT.finditer(before_text):
        if m.end() > last_pos:
            last_pos, last_kind = m.end(), 'asset'
    for m in _KIID_EXCLUDE_SUBJECT.finditer(before_text):
        if m.end() > last_pos:
            last_pos, last_kind = m.end(), 'exclude'
    return last_kind


def detect_asset_currency_from_kiid_text(kiid_text: Optional[str]) -> Optional[str]:
    """
    Fallback de detect_asset_currency_from_name(): cuando el nombre del
    fondo no declara una divisa dominante, busca en el texto KIID (ventana
    objetivo) una declaración explícita de divisa de los ACTIVOS (no de la
    clase de participación, ni del objetivo/rendimiento) -- p.ej. "estos
    activos siempre estarán denominados en dólares estadounidenses",
    "instrumentos del mercado monetario denominados en euros" o "el 50%
    en renta fija denominada en euros".

    Deliberadamente estricto: exige que el sujeto gramatical MÁS CERCANO al
    verbo sea de tipo activo (no "acciones"/"participaciones"/"clase" →
    clase de participación; no "capital"/"apreciación"/"rentabilidad" →
    objetivo medido en esa divisa; no "cuota de inversión" → mecanismo de
    acceso específico) y descarta negaciones ("deuda NO denominada en
    euros"), continuaciones multi-divisa ("...u otras divisas", "...otras
    monedas del g7") y lenguaje permisivo/opcional ("podrán ser
    invertidos"). Devuelve (BL-ASSET-CCY-MULTI):
      - la divisa única (EUR/USD/...) si hay una coincidencia limpia;
      - el centinela MCY (ASSET_CURRENCY_MULTI) si el único hallazgo válido
        fue descalificado por la continuación multi-divisa -- el fondo
        declara EXPLÍCITAMENTE un mandato multi-divisa (indeterminado por
        naturaleza, población identificable/aislable, NO dato faltante);
      - None si no hay señal de divisa alguna (desconocido/no descubierto),
        o si el hallazgo fue una negación o una asignación permisiva/opcional.

    Cuando hay divisa única, devuelve la que aparece MÁS A LA IZQUIERDA en la
    ventana entre las coincidencias válidas (misma lógica que la versión
    basada en nombre).
    """
    if not kiid_text:
        return None
    t = kiid_text.lower()
    _obj_start, _obj_end = _get_obj_bounds(kiid_text)
    w = _extract_window(t, _obj_start, _obj_end)
    w = re.sub(r'\s+', ' ', w)

    best_match = None
    best_pos = None
    # BL-ASSET-CCY-MULTI (2026-07-11): rastrea si un verbo de divisa VÁLIDO
    # (sujeto=activo, no negado) fue descalificado SOLO por la continuación
    # multi-divisa. En ese caso el fondo declara explícitamente un mandato
    # multi-divisa -> devolver el centinela MCY (indeterminado por
    # naturaleza), NO None (que se reserva para "sin señal alguna"). Una
    # coincidencia limpia de divisa única, si existe, tiene prioridad.
    multi_ccy_seen = False
    for m in _KIID_CURRENCY_VERB.finditer(w):
        before = w[max(0, m.start() - 80):m.start()]
        if _KIID_NEGATION.search(before):
            continue
        if _kiid_closest_currency_subject(before) != 'asset':
            continue
        tail = w[m.end():m.end() + 50]
        if _KIID_MULTI_CCY_CONTINUATION.search(tail):
            multi_ccy_seen = True
            continue
        after = w[m.end():m.end() + 30]
        if _KIID_PERMISSIVE_CONTINUATION.search(after):
            continue
        if best_pos is None or m.start() < best_pos:
            best_pos = m.start()
            for cur_re, code in _KIID_CURRENCY_NAME_MAP:
                if re.fullmatch(cur_re, m.group(1), re.IGNORECASE):
                    best_match = code
                    break
    if best_match is not None:
        return best_match
    return ASSET_CURRENCY_MULTI if multi_ccy_seen else None


def detect_fx_share_class_mismatch(
    asset_currency: Optional[str],
    fund_currency: Optional[str],
    hedging_policy: Optional[str] = None,
    srri: Optional[int] = None,
) -> bool:
    """
    Detecta si la divisa de los activos del fondo (Asset_Currency, ver
    detect_asset_currency_from_name) difiere de la divisa de la clase de
    participación (Fund_Currency) -- en cualquier combinación
    (EUR/USD/GBP/JPY/CHF/CNH). Esta capa de descalce divisa-clase-de-
    participación explica de forma legítima un SRRI elevado (riesgo
    cambiario) sin que la naturaleza real del fondo (p.ej. Monetario/RF
    Corto genuino) cambie.

    Si la clase de participación está declarada como cubierta
    (hedging_policy='Hedged'), el descalce de divisa NO explica un SRRI
    elevado -- la cobertura neutraliza precisamente ese riesgo cambiario --
    así que se devuelve False en ese caso.

    Si se proporciona `srri` y supera `_FX_PLAUSIBLE_SRRI_CEILING`, tampoco
    se exime: el riesgo cambiario por sí solo no explica una volatilidad
    tan alta, así que el conflicto Nature/SRRI probablemente tiene otra
    causa (activo genuinamente más arriesgado, Nature mal asignada, etc.)
    que BL-44 debe seguir marcando para revisión.

    Usado por BL-44 (pipeline.py) como excepción al forzado a 'Restantes':
    si Asset_Currency está poblada Y difiere de Fund_Currency (y no está
    cubierta, y el SRRI está dentro del rango plausible), el conflicto
    Nature/SRRI puede tener una explicación cambiaria legítima en vez de
    indicar una Nature mal asignada. Si Asset_Currency es None (fondo
    diversificado/sin mandato de divisa única, o Fund_Currency ausente),
    devuelve False -- el conflicto permanece sin explicar y BL-44 sigue
    aplicando su comportamiento defensivo habitual.
    """
    if not asset_currency or not fund_currency:
        return False
    # BL-ASSET-CCY-MULTI: el centinela MCY NO es una divisa única -- un fondo
    # multi-divisa no tiene un descalce divisa-clase limpio que explique un
    # SRRI elevado, así que se trata como Asset_Currency indeterminada (misma
    # semántica que None aquí): el conflicto Nature/SRRI permanece sin explicar.
    if asset_currency.upper() == ASSET_CURRENCY_MULTI:
        return False
    if hedging_policy and hedging_policy.strip().lower() == "hedged":
        return False
    if srri is not None and srri > _FX_PLAUSIBLE_SRRI_CEILING:
        return False
    return asset_currency.upper() != fund_currency.upper()


# ============================================================
# detect_kiid_attributes — enriquecimiento completo desde KIID
# ============================================================

def detect_type_from_kiid(kiid_text: str, fund_nature: str) -> Optional[str]:
    """
    Infiere Type desde el texto KIID (ventana objetivo).
    Solo cuando el bloque primario no ha podido asignarlo por nombre.
    """
    if not kiid_text:
        return None
    _obj_start, _obj_end = _get_obj_bounds(kiid_text)
    w = _extract_window(kiid_text.lower(), _obj_start, _obj_end)

    if fund_nature == "Renta Fija Flexible":
        if any(k in w for k in ["high yield","alto rendimiento","bono de alto rendimiento"]):
            return "High Yield"
        # Emergentes: exigir señal dominante, no mención incidental
        if any(k in w for k in [
            "invierte principalmente en mercados emergentes",
            "primarily in emerging markets",
            "deuda de mercados emergentes como objetivo",
            "emerging market debt fund",
            "emerging market bond fund",
            "mercados emergentes como objetivo principal",
        ]):
            return "Emergentes"
        # Señal moderada: inversión significativa aunque no exclusiva en EM
        if any(k in w for k in [
            "emerging market debt","deuda emergente",
            "bonos de mercados emergentes",
            "considerablemente en los mercados emergentes",
            "significantly in emerging markets",
            "invertir en mercados emergentes",
        ]):
            return "Emergentes"
        if any(k in w for k in ["inflación","inflation-linked","vinculado a la inflación","tips"]):
            return "Inflation"  # v10: idioma objetivo inglés (Principio #8); era "Inflación"
        if any(k in w for k in [
            "invierte principalmente en covered bond",
            "primarily in covered bond",
            "bonos garantizados como objetivo",
            "pfandbrief","covered bond fund",
            "fondo de covered bond",
        ]):
            return "Covered Bond"
        if any(k in w for k in ["convertible bond","bono convertible","obligaciones convertibles"]):
            return "Convertible"
        if any(k in w for k in [
            "invierte principalmente en bonos de gobierno",
            "primarily in government bond",
            "invierte en deuda pública","deuda del estado",
            "bonos soberanos","sovereign bond fund",
            "government bond fund","fondo de bonos gubernamentales",
        ]):
            return "Gobierno"
        # Corporativo: señal explícita de objetivo, no mención incidental
        if any(k in w for k in [
            "invierte principalmente en bonos corporativos",
            "primarily in corporate bond",
            "corporate bond fund","fondo de crédito corporativo",
            "invierte en crédito corporativo","corporate credit fund",
        ]):
            return "Corporativo"
        # Señal moderada: menciona IG o HY en contexto de política de inversión
        if any(k in w for k in [
            "cartera de bonos corporativos","bonos corporativos investment grade",
            "crédito con grado de inversión","investment grade corporate",
            "grado de inversión como objetivo",
        ]):
            return "Corporativo"
        if any(k in w for k in ["target maturity","vencimiento fijo","fixed maturity",
                                  "fecha objetivo"]):
            return "Target Maturity"
        if any(k in w for k in ["total return","rentabilidad total","unconstrained",
                                  "multi-sector","multisector"]):
            return "Unconstrained"

    elif fund_nature == "Renta Fija Corto Plazo":
        if any(k in w for k in ["floating rate","tipo flotante","bonos flotantes","frn"]):
            return "Floating Rate"
        if any(k in w for k in ["covered bond","bonos garantizados","pfandbrief"]):
            return "Covered Bond"
        if any(k in w for k in ["gobierno","government","treasury","sovereign","tesoro"]):
            return "Gobierno CP"
        if any(k in w for k in ["corporate","corporativo","crédito","credit"]):
            return "Crédito CP"

    elif fund_nature == "Monetario":
        if any(k in w for k in ["cnav","constant nav","valor liquidativo constante"]):
            return "CNAV"
        if any(k in w for k in ["lvnav","baja volatilidad del valor"]):
            return "LVNAV"
        if any(k in w for k in ["vnav","variable net asset"]):
            return "VNAV"
        if any(k in w for k in ["enhanced cash","monetario plus","rendimiento adicional"]):
            return "Enhanced Cash"

    elif fund_nature == "Renta Variable":
        if any(k in w for k in ["replica","tracks","sigue el índice","seguimiento del índice",
                                  "index fund","fondo índice"]):
            return "Indexado"
        if any(k in w for k in ["smart beta","factor investing","quality factor","value factor"]):
            return "Smart Beta"

    elif fund_nature == "Mixtos":  # BL-19: "Mixtos"
        if any(k in w for k in ["target volatility","volatilidad objetivo"]):
            return "Target Volatility"
        if any(k in w for k in ["tactical","táctica","gestión táctica"]):
            return "Tactical"
        if any(k in w for k in ["lifecycle","ciclo de vida","target date"]):
            return "Lifecycle"
        return "Allocation"  # default para mixtos

    elif fund_nature == "Alternativo":
        if any(k in w for k in ["absolute return","retorno absoluto"]):
            return "Absolute Return"
        if any(k in w for k in ["long/short","long short","posiciones largas y cortas"]):
            return "Long/Short"
        if any(k in w for k in ["market neutral","neutral al mercado"]):
            return "Market Neutral"
        if any(k in w for k in ["systematic","sistemático","cta","managed futures"]):
            return "Sistemático/CTA"
        if any(k in w for k in ["commodities","materias primas","commodity"]):
            return "Commodities"
        if any(k in w for k in ["real assets","activos reales","real estate","inmobiliario"]):
            return "Real Assets"

    return None


def detect_style_from_kiid(kiid_text: str) -> Optional[str]:
    """
    Detecta Style_Profile desde la ventana objetivo del KIID.
    Complementa detect_style_profile (que solo usa el nombre).
    Usa señales explícitas de política de inversión, no menciones incidentales.
    """
    if not kiid_text:
        return None
    _obj_start, _obj_end = _get_obj_bounds(kiid_text)
    w = _extract_window(kiid_text.lower(), _obj_start, _obj_end)

    if any(k in w for k in [
        "baja volatilidad","low volatility","volatilidad reducida",
        "preservación del capital","capital preservation",
        "minimum variance","minimum volatility","mínima varianza",
        "gestión del riesgo absoluto","control de volatilidad",
    ]):
        return "Low Volatility"

    if any(k in w for k in [
        "generación de rentas","income distribution","generar rentas",
        "reparte dividendos","distribuye dividendos","ingresos regulares",
        "rendimientos periódicos","distribución periódica",
        "distributing shares","clase de distribución",
        "objetivo de rentas","income objective","orientado a rentas",
        "income fund","income oriented",
    ]):
        return "Income"

    if any(k in w for k in [
        "valor intrínseco","infravaloradas","infravalorados",
        "value investing","estrategia value","análisis fundamental de valor",
        "acciones de valor","cotización inferior a su valor",
    ]):
        return "Value"

    if any(k in w for k in [
        "crecimiento de beneficios","empresas de alto crecimiento",
        "potencial de crecimiento","crecimiento sostenido",
        "growth investing","growth stocks","growth equity",
        "crecimiento del capital a largo plazo",
    ]):
        return "Growth"

    if any(k in w for k in [
        "momentum","seguimiento de tendencias","trend following",
        "impulso de precios","estrategia de momentum",
    ]):
        return "Momentum"

    if any(k in w for k in [
        "risk control","riesgo controlado","control de riesgo",
        "volatility target","objetivo de volatilidad",
        "paridad de riesgo","risk parity","volatilidad objetivo",
    ]):
        return "Risk Control"

    return None


# FIX-GEO-KIID-1 (2026-07-05): guardas para las dos únicas señales EEUU sin
# verbo de objetivo ("estados unidos", "norteamerica") -- las demás frases del
# grupo EEUU ya declaran explícitamente el objetivo ("invierte principalmente
# en...") y no las necesitan. Tres trampas confirmadas leyendo texto KIID real:
#   NEGATED:        "...empresas... fuera de los estados unidos" (WCM Select
#                    Global) -- negación, es lo contrario de un objetivo EEUU.
#   ISSUER_DETAIL:  "...MBS...emitidos por agencias (organismos cuasi-
#                    gubernamentales de estados unidos)..." (JPM Global Bond
#                    Opportunities) -- detalle del emisor de un instrumento,
#                    no la geografía declarada del fondo.
#   MULTI_VALUE:    "...empresas norteamericanas y europeas..." (GS Global
#                    High Yield) -- enumeración de 2+ regiones: el fondo es
#                    Global, no EEUU.
_GEO_NEGATION_MARKERS = ["fuera de","excluyendo","distintos de","distintas de",
                          "distinta de","salvo","excepto","no incluye","sin incluir",
                          # FIX-GEO-10a (2026-07-14): Regulation S disclaimer
                          # "no abierto a residentes de los estados unidos" is a
                          # sales-restriction clause, not an investment geography signal.
                          "residentes de"]
_GEO_ISSUER_DETAIL_MARKERS = ["emitidos por","organismos","instituciones privadas","emisores"]
_GEO_OTHER_REGION_MARKERS = ["europ","asia","china","japón","japon","india",
                             "latinoam","mercados emergentes","emergent"]


def _geo_bare_match_guard(w: str, idx: int) -> str:
    """Clasifica el contexto de un match bare 'estados unidos'/'norteamerica'."""
    _pre = w[max(0, idx - 60):idx]
    if any(neg in _pre for neg in _GEO_NEGATION_MARKERS):
        return "NEGATED"
    if any(det in _pre for det in _GEO_ISSUER_DETAIL_MARKERS):
        return "ISSUER_DETAIL"
    _post = w[idx:idx + 100]
    if any(r in _post for r in _GEO_OTHER_REGION_MARKERS):
        return "MULTI_VALUE"
    return "OK"


# FIX-GEO-5 (2026-07-05): hallado auditando la población de origen
# 'restantes' (fondos con nombre/KIID más opacos, ver
# project_fix_master_load_1 / sesión 2026-07-05) -- "de todo el mundo"/
# "de cualquier parte del mundo" declaran el mandato del PROPIO fondo en
# la inmensa mayoría de casos reales del corpus ("títulos... emitidos por
# empresas o gobiernos de todo el mundo"), pero la misma frase puede
# describir en cambio el ALCANCE DE LA GESTORA (oficinas/clientes/
# presencia mundial) -- no el mandato de inversión. A diferencia de
# _geo_bare_match_guard, "emitidos por" aquí NO es ISSUER_DETAIL a
# excluir: para un fondo de renta fija, describir el mandato como
# "emitido por emisores de todo el mundo" ES precisamente la forma
# correcta de declarar Global (los bonos siempre se describen por su
# emisor). El único riesgo real es el contexto de OFICINAS/CLIENTES de
# la gestora, no el de instrumento/emisor.
_GEO_MANAGER_SCOPE_MARKERS = ["oficinas","sucursales","clientes","presencia",
                              "red de","empleados","profesionales"]


def _geo_worldwide_guard(w: str, idx: int) -> str:
    """Clasifica el contexto de un match bare 'de todo el mundo'/'de
    cualquier parte del mundo': distingue el mandato de inversión del
    fondo del alcance geográfico de la GESTORA (oficinas/clientes)."""
    _pre = w[max(0, idx - 60):idx]
    if any(m in _pre for m in _GEO_MANAGER_SCOPE_MARKERS):
        return "MANAGER_SCOPE"
    if any(neg in _pre for neg in _GEO_NEGATION_MARKERS):
        return "NEGATED"
    return "OK"


# ============================================================
# FIX-GEO-3 (2026-07-05): fallback -- nombre oficial del subfondo
# declarado en la línea "Producto:"/"PRODUCTO" del propio KIID.
# ============================================================
# Hallazgo: ni detect_geography (Fund_Name abreviado del maestro, p.ej.
# "EURP", "EM MK") ni detect_geography_from_kiid (vocabulario ES dentro de
# la ventana objetivo) leían nunca esta línea -- que declara el nombre
# COMPLETO del subfondo, a menudo en inglés aunque el resto del KIID esté
# en español (p.ej. "PRODUCTO AXA IM FIIS Europe Short Duration High
# Yield", "Producto AXA World Funds - Emerging Markets Short Duration
# Bonds"). Auditoría de corpus (1.618 fondos con Geography='Global'):
# 37 recuperan una región concreta (9 Japón, 17 Europa, 11 EEUU) y 78 más
# recuperan Development_Status='Emerging' vía Geography='Emergentes' que
# antes se perdía por la misma razón (nombre abreviado sin "Emerging").
# Solo se invoca como ÚLTIMO fallback, tras agotar la ventana objetivo
# (ver wiring al final de detect_geography_from_kiid) -- nunca sustituye
# una señal ya encontrada.
_PRODUCT_LINE_PATTERN = re.compile(
    r'(?m)(?<![A-Za-zÁÉÍÓÚÑáéíóúñ])(?:PRODUCTO|Producto)\s*:?\s+'
    r'([A-ZÁÉÍÓÚÑ0-9][^\n]{3,160})'
)
# Continuaciones de prosa que indican que "Producto"/"PRODUCTO" no era una
# etiqueta de campo seguida del nombre del fondo, sino parte de una frase
# ("...este producto de inversión.", "Producto está autorizado en...").
_PRODUCT_LINE_STOP_STARTS = re.compile(
    r'^(?:de\b|del\b|est[aá]\b|es\b|se\b|y\b|PRIIP|que\b|para\b|no\b|Ha\b)',
    re.IGNORECASE
)
# Sufijo de entidad legal pegado a la palabra-región (p.ej. "...Europe
# SAS", "UBS Europe SE") -> domicilio de la GESTORA, no la geografía de
# inversión del fondo. Mismo error de "detalle de emisor" que
# _geo_bare_match_guard ya filtra para el vocabulario ES.
_PRODUCT_LINE_ENTITY_SUFFIX_GUARD = re.compile(
    r'^\s*(?:SAS|SA|SE|S\.A\.|GmbH|Ltd|plc|S\.à\s?r\.l\.|N\.V\.)\b'
)
# "US Dollar"/"U.S. Dollar" en el nombre del subfondo es una convención de
# DENOMINACIÓN DE DIVISA BASE (frecuente en BlackRock/BGF: "US Dollar High
# Yield Bond Fund", "US Dollar Short Duration Bond Fund"), no un mandato
# geográfico -- confirmado leyendo el KIID real de LU0046676465 (BGF USD
# HIGH YIELD BOND): invierte explícitamente en emisores estadounidenses Y
# NO estadounidenses. Mismo error de raíz que el ya corregido en
# detect_geography (nombre): confundir divisa con geografía.
_PRODUCT_LINE_US_CURRENCY_GUARD = re.compile(r'^\s*Dollar\b', re.IGNORECASE)
_PRODUCT_LINE_NEGATION_PREFIX = re.compile(r'ex[-\s]?$|excl(?:uding)?\s*$', re.IGNORECASE)

# Vocabulario de salida ES, igual que _GEO_OBJ_PATTERNS -- señales en
# INGLÉS porque el nombre propio del subfondo suele declararse en inglés.
_PRODUCT_LINE_CONCRETE_REGION_PATTERNS = [
    (re.compile(r'\bJapan(?:ese)?\b'), "Japón"),
    (re.compile(r'\bChin(?:a|ese)\b'), "China"),
    (re.compile(r'\bIndia[n]?\b'), "India"),
    (re.compile(r'\bLatin\s+America[n]?\b'), "Latinoamérica"),
    (re.compile(r'\bEurope(?:an)?\b'), "Europa"),
    (re.compile(r'\bAsia(?:n|-Pacific)?\b'), "Asia"),
    (re.compile(r'\b(?:US|U\.S\.|North\s+America[n]?|United\s+States)\b'), "EEUU"),
]
_PRODUCT_LINE_EMERGING_MARKETS_PATTERN = re.compile(r'\bEmerging\s+Markets?\b')
_PRODUCT_LINE_GLOBAL_PATTERN = re.compile(r'\bGlobal\b|\bWorld\b')


def _extract_kiid_product_name(kiid_text: str) -> Optional[str]:
    """Extrae la línea "Producto:"/"PRODUCTO" del KIID (nombre oficial del
    subfondo, anterior a la sección de objetivo de inversión). Devuelve
    None si no hay un ancla plausible (rechaza continuaciones de prosa)."""
    if not kiid_text:
        return None
    for m in _PRODUCT_LINE_PATTERN.finditer(kiid_text[:3000]):
        candidate = m.group(1).strip()
        if _PRODUCT_LINE_STOP_STARTS.match(candidate):
            continue
        return candidate
    return None


def detect_geography_from_kiid_product_name(kiid_text: str) -> Optional[str]:
    """FIX-GEO-3: geografía inferida desde el nombre oficial del subfondo
    declarado en la línea "Producto:" del propio KIID. Fallback de última
    instancia -- ver wiring en detect_geography_from_kiid."""
    candidate = _extract_kiid_product_name(kiid_text)
    if not candidate:
        return None

    concrete_hits = []
    for pat, geo in _PRODUCT_LINE_CONCRETE_REGION_PATTERNS:
        m = pat.search(candidate)
        if not m:
            continue
        before = candidate[max(0, m.start() - 8):m.start()]
        after = candidate[m.end():m.end() + 12].strip()
        if _PRODUCT_LINE_NEGATION_PREFIX.search(before):
            continue
        if _PRODUCT_LINE_ENTITY_SUFFIX_GUARD.match(after):
            continue
        if geo == "EEUU" and _PRODUCT_LINE_US_CURRENCY_GUARD.match(after):
            continue
        concrete_hits.append(geo)

    # Enumeración multi-región (2+ regiones concretas nombradas) -> Global,
    # mismo criterio que _geo_bare_match_guard MULTI_VALUE.
    if len(concrete_hits) >= 2:
        return "Global"
    if len(concrete_hits) == 1:
        return concrete_hits[0]

    if _PRODUCT_LINE_EMERGING_MARKETS_PATTERN.search(candidate):
        return "Emergentes"
    if _PRODUCT_LINE_GLOBAL_PATTERN.search(candidate):
        return "Global"
    return None


def detect_geography_from_kiid(kiid_text: str) -> Optional[str]:
    """
    Detecta Geography desde la ventana objetivo del KIID.
    Solo se usa cuando detect_geography (por nombre) devuelve None.

    Usa señales EXPLÍCITAS de objetivo de inversión (no menciones incidentales).
    Orden: específicas primero, globales al final.

    FIX-GEO-KIID-1 (2026-07-05): se retiraron los fragmentos de índice de
    referencia ("s&p 500", "russell 1000/2000", "dow jones", "nasdaq",
    "bloomberg us aggregate") de las señales EEUU -- verificado en corpus
    (244 fondos) que aparecen como UN componente de un índice compuesto
    multi-región en fondos Global (p.ej. 36% S&P 500 + 24% FTSE World ex-US +
    24% US Treasury + ... como referencia compuesta de un fondo de asignación
    global), nunca como declaración de objetivo del propio fondo. Las dos
    señales EEUU sin verbo de objetivo restantes ("estados unidos",
    "norteamerica") pasan por `_geo_bare_match_guard` antes de aceptarse.
    """
    if not kiid_text:
        return None
    _obj_start, _obj_end = _get_obj_bounds(kiid_text)
    w = _extract_window(kiid_text.lower(), _obj_start, _obj_end)

    # Señales explícitas de objetivo — frases que declaran la geo principal
    _GEO_OBJ_PATTERNS = [
        # Japón
        (["invierte principalmente en japón","invierte en japón",
          "japanese equities","renta variable japonesa",
          "japanese government","mercado japonés"], "Japón"),
        # China
        # Note: "a-shares" has a permissive-sleeve guard below (FIX-GEO-ASHARES-1)
        (["invierte principalmente en china","chinese equities",
          "renta variable china","mercado chino",
          "a-shares","gran china"], "China"),
        # Asia
        (["asia-pacífico","asia pacific","invierte en asia",
          "mercados asiáticos","asian equities"], "Asia"),
        # India
        (["invierte en india","mercado indio","indian equities"], "India"),
        # Latinoamérica
        (["latinoamérica","latin america","invierte en brasil"], "Latinoamérica"),
        # EEUU — ANTES que Emergentes para evitar falso positivo
        (["invierte principalmente en estados unidos",
          "invierte en estados unidos","us equities",
          "renta variable estadounidense","mercado estadounidense",
          "bonos gubernamentales y corporativos de estados unidos",
          "valores de estados unidos","norteamerica",
          # Mención directa sin prefijo -- pasa por _geo_bare_match_guard
          "estados unidos"], "EEUU"),
        # Europa — ANTES que Emergentes
        (["invierte principalmente en europa","invierte en europa",
          "zona euro","eurozona","valores europeos",
          "renta variable europea","mercado europeo",
          "european equities","european bonds",
          # FIX-GEO-5: país nórdico sin nivel propio en DOMAIN_VALUES
          # (mismo criterio que "Italia"→Europe en _GEO_ES_TO_EN) --
          # adjetivo explícito sobre el instrumento, no solo el código de
          # divisa (evita la trampa divisa-vs-geografía de "US Dollar Fund").
          "bonos suecos"], "Europa"),
        # Global — ANTES que Emergentes: fondos globales mencionan EM incidentalmente
        (["invierte a nivel mundial","invierte en todo el mundo",
          "mercados de todo el mundo","globally diversified",
          "diversificación global","cartera global",
          # Índices de referencia globales como proxy fiable
          "jp morgan global government bond","global government bond index",
          "world government bond","bloomberg global aggregate",
          "msci world","msci acwi","ftse world",
          "global bond fund","global equity fund",
          # FIX-GEO-5 (2026-07-05): variantes adicionales encontradas
          # auditando la población 'restantes' -- "mundial(es)" como
          # adjetivo pospuesto, no solo la locución "a nivel mundial".
          "a escala mundial","mercados de renta variable mundiales",
          "renta variable mundial","mercados mundiales",
          # Mención directa sin prefijo -- pasa por _geo_worldwide_guard
          "de cualquier parte del mundo","de todo el mundo"], "Global"),
        # Emergentes — señal dominante requerida
        (["invierte principalmente en mercados emergentes",
          "mercados emergentes como objetivo principal",
          "emerging market debt","emerging market equities",
          "deuda de mercados emergentes",
          "renta variable de mercados emergentes",
          # FIX-GEO-5: "países emergentes" (vs "mercados emergentes") --
          # variante frecuente en fondos de deuda EM ("emisor de países
          # emergentes"), no cubierta por los patrones anteriores.
          # Mención directa sin prefijo -- pasa por chequeo de negación.
          "países emergentes"], "Emergentes"),
    ]

    for signals, geo in _GEO_OBJ_PATTERNS:
        for s in signals:
            idx = w.find(s)
            if idx == -1:
                continue
            if s in ("estados unidos", "norteamerica"):
                verdict = _geo_bare_match_guard(w, idx)
                if verdict == "MULTI_VALUE":
                    return "Global"
                if verdict in ("NEGATED", "ISSUER_DETAIL"):
                    continue
            elif s in ("de todo el mundo", "de cualquier parte del mundo"):
                verdict = _geo_worldwide_guard(w, idx)
                if verdict in ("NEGATED", "MANAGER_SCOPE"):
                    continue
            elif geo == "Latinoamérica":
                # FIX-GEO-10b (2026-07-14): "latinoamérica" appearing in a
                # conjunction with "estados unidos"/"canadá" (within 150 chars
                # before the match) describes multi-region coverage, not a
                # LatAm-focused mandate (e.g. "mercados de estados unidos, canadá
                # y latinoamérica"). Skip → loop continues; EEUU or Global fires.
                _pre_latam = w[max(0, idx - 150):idx]
                if ("estados unidos" in _pre_latam
                        or "canadá" in _pre_latam or "canada" in _pre_latam):
                    continue
            elif s == "a-shares":
                # FIX-GEO-ASHARES-1 (2026-07-19): "a-shares" in a permissive
                # MINORITY-SLEEVE context must NOT assign Geography=China.
                # "the fund may invest up to 10%/20% of its assets in China
                # A-Shares via Stock Connect" describes an optional/minor
                # satellite allocation in a global/multi-region mandate, not
                # a China-focused fund. Guard: skip if the 80-char pre-window
                # contains a percentage cap ("up to", "hasta un", "%"), a
                # "may invest"/"puede invertir" qualifier, or "stock connect"
                # (the mainland access vehicle used for A-share sleeves).
                _pre_ash = w[max(0, idx - 80):idx]
                _ash_permissive = any(m in _pre_ash for m in [
                    "up to", "hasta un", "hasta el", "may invest",
                    "puede invertir", "via stock connect", "a través de",
                    "through stock connect",
                ]) or "%" in _pre_ash
                if _ash_permissive:
                    continue
            elif s == "países emergentes":
                # FIX-GEO-EDR-1 (2026-07-20): umbrella SICAV description uses
                # "vehículo que tiene por objeto, en particular, la inversión en
                # empresas registradas predominantemente en países emergentes" to
                # describe the SICAV's general scope — not the subfund's mandate.
                # Phrase appears ~103 chars before "países emergentes"; widening
                # the pre-window to 150 chars captures it.
                _pre = w[max(0, idx - 150):idx]
                if any(neg in _pre for neg in _GEO_NEGATION_MARKERS):
                    continue
                # Contexto de riesgo / exposición incidental ≠ objetivo principal.
                # "exposición a países emergentes", "puede estar expuesto a ...",
                # "incluyendo países emergentes" aparecen en secciones de riesgo
                # de fondos Europa/Global y no declaran el objetivo del fondo.
                # FIX-GEO-EM-2 (2026-07-19): extend with enumeration/permissive
                # prefixes found in audited FP cases: "podrá invertir en" (pre-
                # enum list), "incluidos/incluidas" (trailing inclusion clause),
                # "ocde o" (multi-region enumeration where EM is one alternative).
                _RISK_CONTEXT_MARKERS = [
                    "exposición a ", "puede estar expuesto", "puede tener exposición",
                    "puede invertir hasta", "incluyendo ", "incluido ", "incluida ",
                    "entre los que se incluyen", "como pueden ser ", "tales como ",
                    "acceso a ", "así como ", "también ",
                    # FIX-GEO-EM-2 additions
                    "podrá invertir en ", "puede invertir en ",
                    "incluidos ", "incluidas ", "ocde o ",
                    # FIX-GEO-EDR-1 additions
                    "vehículo que tiene por objeto",
                    "vehiculo que tiene por objeto",
                ]
                if any(m in _pre for m in _RISK_CONTEXT_MARKERS):
                    continue
            return geo

    # FIX-GEO-3 (2026-07-05): la ventana objetivo no dio señal -- último
    # fallback, el nombre oficial del subfondo en la línea "Producto:".
    return detect_geography_from_kiid_product_name(kiid_text)


def detect_esg_from_kiid(kiid_text: str) -> int:
    """
    Detecta política ESG desde el texto KIID.
    Complementa detect_is_esg (que solo usa el nombre).
    Detecta referencias Art. 8/9 SFDR y criterios ASG explícitos.
    """
    if not kiid_text:
        return 0
    _obj_start, _obj_end = _get_obj_bounds(kiid_text)
    w = _extract_window(kiid_text.lower(), _obj_start, _obj_end)
    if any(k in w for k in [
        "artículo 8", "artículo 9", "article 8", "article 9",
        "sfdr", "reglamento de divulgación",
        "características medioambientales y sociales",
        "environmental and social characteristics",
        "sustainable investment", "inversión sostenible",
        "promueve características medioambientales",
        "integra el riesgo y los factores esg",
        "criterios ambientales, sociales y de gobernanza",
        "esg criteria", "criterios esg",
        "objetivo de inversión sostenible",
    ]):
        return 1
    return 0


def detect_ongoing_charge_from_kiid(kiid_text: str) -> Optional[float]:
    """
    Extrae Ongoing_Charge (v19: Ongoing_Charge_Recurrent) desde la ventana de costes del KIID (9000-14000).

    NOTA: La posición de la sección de costes varía según la gestora y el
    formato del KIID (UCITS vs PRIIPs). Validación con datos reales muestra
    que la ventana fija 9000-14000 captura con frecuencia la sección de
    riesgos (no costes), generando valores incorrectos.

    Esta función queda reservada para uso futuro cuando se implemente
    detección dinámica de la posición de la sección de costes.
    El parser principal (kiid_parser.py) sigue siendo la fuente de OC.
    """
    return None  # Deshabilitado — ver docstring


def detect_kiid_attributes(
    kiid_text: str,
    fund_nature: str,
    current_attrs: Optional[dict] = None,
) -> dict:
    """
    Extrae atributos clasificatorios desde el texto KIID usando las
    ventanas correctas. Solo rellena atributos que no han sido asignados
    por el bloque (principio: bloque tiene precedencia).

    Parametros:
        kiid_text:     texto completo del KIID
        fund_nature:   naturaleza ya asignada al fondo
        current_attrs: dict con atributos ya asignados por el bloque
                       (si se pasa, solo rellena los NULL/None)

    Devuelve dict con: Type, Style_Profile, Geography, Is_ESG,
                       Exposure_Bias
    Solo incluye valores detectados — no sobreescribe nada.
    """
    cur = current_attrs or {}
    result = {}

    # Type (signal transitorio → derive_v20_attributes lo finaliza en Vehicle_Structure)
    if not cur.get("_signal_type") or cur.get("_signal_type") == fund_nature:
        t = detect_type_from_kiid(kiid_text, fund_nature)
        if t:
            result["_signal_type"] = t

    # Style_Profile
    if not cur.get("Style_Profile") or cur.get("Style_Profile") == "Defensivo":
        s = detect_style_from_kiid(kiid_text)
        if s:
            result["Style_Profile"] = s

    # Geography
    if not cur.get("Geography"):
        g = detect_geography_from_kiid(kiid_text)
        if g:
            result["Geography"] = g

    # Is_ESG — combina nombre (detect_is_esg ya en el bloque) y KIID
    esg_kiid = detect_esg_from_kiid(kiid_text)
    if esg_kiid:
        result["Is_ESG"] = 1

    # BL-53/54: Normalizar Sector_Focus al idioma objetivo (inglés, GICS-EN)
    if "Sector_Focus" in result:
        result["Sector_Focus"] = normalize_sector_focus(result["Sector_Focus"])

    # Nota: Ongoing_Charge_Recurrent (v19) NO se incluye aqui.
    # Se extrae en pipeline.py directamente via detect_ongoing_charge_from_kiid()
    # porque es un campo del parser (Grupo 4), no de clasificacion (Grupo 2).

    return result


# ============================================================
# Resto de funciones universales (sin cambios respecto a v2)
# ============================================================

# FIX-GEO-NAME-1 (2026-07-05): "us" requiere límite de palabra real (no basta
# con `\b`, que no distingue letra-espacio de letra-letra en todos los casos
# de interés aquí -- el problema real es la ausencia de comprobación de borde
# IZQUIERDO). Sin esto, "us " como substring cazaba JAN**US** (Janus Henderson)
# y **PLUS** (Amundi "Rend Plus") como señal EEUU. Verificado en corpus: sube
# la concordancia nombre/KIID de 657/981 a 594/619 tras esta + la fix de abajo.
_US_STANDALONE_WORD = re.compile(r"\bus\b")


def detect_geography(name_l: str) -> Optional[str]:
    """Detecta geografía desde el nombre del fondo (en minúsculas).

    FIX-GEO-NAME-1 (2026-07-05): se retiraron 3 reglas de fallback que
    usaban la DENOMINACIÓN DE DIVISA de la clase de participación (" usd ",
    "usdh", " eur ") como si fuera señal de geografía de inversión -- la
    misma confusión share-class-vs-asset ya corregida para Asset_Currency/
    Fund_Currency. Confirmado en corpus: 'MFS EUROPE RESEARCH A1 USD ACC'
    (nombre dice explícitamente Europa) se clasificaba como EEUU solo por
    tener clase USD. Retirar estas 3 reglas + fijar "us " con borde de
    palabra bajó el desacuerdo nombre/KIID de 324 a 79 casos (de 981 fondos
    con ambas señales); ver SESSION_SUMMARY para el detalle del corpus check.
    """
    # FIX-GEO-7 (2026-07-12): added "nippon" (EN demonym used in fund names).
    # FIX-GEO-9 (2026-07-14): added "jpn" (standard 3-letter abbrev used in
    # abbreviated fund names: "FIDELITY F JPN EQ ESG"). Plain substring is safe
    # ("jpn" does not appear as interior of other common finance words).
    # FIX-GEO-9: ex-Japan negation guard — "Asia ex Japan" / "MSCI AC Asia ex
    # Japan" → Asia, not Japan. Without this guard, "japan" fires at line 3298
    # before "asia" at line 3306, wrongly returning Japón for ex-Japan indices
    # and funds (affects both detect_geography on fund names AND _bmk_geography
    # in the benchmark-consistency audit tool which calls the same function).
    if any(k in name_l for k in ["japan","japanese","japon","nippon","jpn"]):
        if not any(neg in name_l for neg in ["ex japan","ex-japan","ex jpn"]):
            return "Japón"
    if "jpy" in name_l:
        return "Japón"
    if any(k in name_l for k in ["china","chinese","a-shares","greater china","gran china","hong kong"]):
        return "China"
    if any(k in name_l for k in ["asia pacific","asia-pacific","apac","asean","pacific"]):
        return "Asia"
    if any(k in name_l for k in ["asia","asian","asia ex"]):
        return "Asia"
    # FIX-GEO-9 (2026-07-14): "asi" as the standalone 3-letter Asia abbreviation
    # used in abbreviated fund names ("FIDELITY F ASI EQ ESG", "SISF ASI EQ YIELD").
    # Requires word boundary (re.search) — plain "asi" substring is unsafe
    # ("basil"/"casual"/etc. contain "asi" as interior characters).
    if re.search(r'\basi\b', name_l):
        return "Asia"
    # FIX-GEO-12 (2026-07-14): "f as eq" is the Fidelity abbreviated naming
    # convention for Asia Equity ("FIDELITY F AS EQ ESG ..."). "as" alone is too
    # short for safe word-boundary matching (high false-positive risk); the compound
    # "f as eq" (8 chars, Fidelity-specific) is safe in all fund names tested.
    if "f as eq" in name_l:
        return "Asia"
    if any(k in name_l for k in ["india","indian"]):
        return "India"
    # FIX-GEO-7: Korea maps to Asia (Development_Status handles developed/emerging axis).
    if any(k in name_l for k in ["korea","korean"]):
        return "Asia"
    if any(k in name_l for k in ["brazil","brasil","latin","latam"]):
        return "Latinoamérica"
    # FIX-GEO-7: added "mideast" (compact OCR form of "Middle East"; safe:
    # "mideast" in a fund name always means MENA / Emerging region).
    if any(k in name_l for k in ["mena","middle east","mideast"]):
        return "Emergentes"
    # FIX-GEO-8 (2026-07-13): "Emerging Europe" (and compact variants) → Eastern Europe,
    # evaluated BEFORE the generic "emerging" → Emergentes check.
    # Real case: SISF EMERGING EUROPE (LU0106817157/104) was mis-detected as Emergentes.
    if any(k in name_l for k in ["emerging europe","emerging euro","emerg europ"]):
        return "Europa del Este"
    # FIX-GEO-7: added truncated/abbreviated emerging variants found in real fund names:
    # "emerg." (CARMIGNAC EMERG.PATRIMOINE), "emergng"/"emergg" (JPM EMERGNG, VONTOBEL
    # EMERGG), "emergi"/"emergin" (typos), "emrgng", "emer." (punctuated abbrev),
    # plus safe multi-token EM abbreviations ("em cies", "em debt", "em mkts", "em local").
    # NOT added: bare "em eq" → false-matches "pr​em eq​uilib" (CS PREM EQUILIB, confirmed FP).
    if any(k in name_l for k in ["emerging","emergentes","emergent","em mkt","emerg mkt",
                                   "emerg ","emrg","emer mkt","emer ","frontier",
                                   "emerg.","emergi","emergin","emergng","emergg",
                                   "emer.","emrgng","em cies","em debt","em mkts","em local"]):
        return "Emergentes"
    # Señales US fuertes (términos geográficos explícitos) -- antes de Europa.
    # FIX-GEO-NAME-2 (2026-07-12): "\bus\b" (señal débil -- también aparece en
    # códigos de clase tipo "W1 US AC") se desplaza a DESPUÉS de los checks
    # europeos para que "MFS EUROPEAN RESEARCH W1 US AC" → Europa, no EEUU.
    # FIX-GEO-8 (2026-07-13): added "us top"/"us div" — fires before "deutsch"/"german"
    # in the Europa branch (DEUTSCHE II US TOP DIVID: manager name triggers Europa).
    # FIX-GEO-9 (2026-07-14): removed bare "treasury" from this list.
    # "treasury" alone is NOT a reliable US signal: "Morningstar Eurozone Treasury
    # Bond", "Bloomberg Euro Aggregate Treasury 3-5Y" are European government bond
    # indices whose names include "treasury" as a generic fixed-income term, causing
    # the audit tool's _bmk_geography() to return North America for European bond
    # funds. US Treasury funds in the corpus all have "us" or "usd" nearby too, so
    # replacing "treasury" with the compound "us treasury" loses nothing while
    # eliminating the false positive.
    if any(k in name_l for k in [
            "usa","u.s.","united states","america","american",
            "us eq","us sm","us sel","us treasury","t-bill","us govt",
            "us dollar","us money","us top","us div"]):
        return "EEUU"
    if any(k in name_l for k in [" uk ","uk eq","uk inc","uk sit","uk sc","uk ag",
                                   "united kingdom","british","britain"," gbp ","gbp ac",
                                   "gbp in","gbphdg","sterling"]):
        return "Europa"
    if any(k in name_l for k in ["swiss","switzerland"," chf ","chf ac","chf p ","chfhdg"]):
        return "Europa"
    if any(k in name_l for k in ["russia","osteuropa","eastern euro","east europ"]):
        return "Europa del Este"
    if (any(k in name_l for k in ["europe","european","euro ","euroland","eurozone",
                                    "europ","europa","euroz","emu","deutsch","germany",
                                    "italia","italian","iberia","nordic","france","french",
                                    # FIX-GEO-6 (2026-07-05): países nórdicos individuales en el
                                    # propio nombre del fondo ("nordic" ya cubría el bloque
                                    # regional, pero no los gentilicios/abreviaturas de país
                                    # sueco/noruego -- ver NORDEA 1 SWED./NORW. SHORT-T. BOND).
                                    "swed","swdish","norw",
                                    # FIX-GEO-7 (2026-07-12): variants found in real fund names
                                    # that the prior list missed:
                                    # "german" (no trailing y) → DWS INVEST GERMAN EQUITS
                                    # "italy" (EN) → FIDELITY ITALY
                                    # "spain"/"spanish" → EDM SPAIN EQ, etc.
                                    # "eurp"/"eurpe" → MS EM EURP MIDEAST (NOTE: Emerging check
                                    #   runs first so EM-EURP funds already exit as Emergentes)
                                    # "switzerlan" → truncated "switzerland" (OCR artifact)
                                    "german","italy","spain","spanish",
                                    "eurp","eurpe","switzerlan"])
            # FIX-GEO-8 (2026-07-13): " euro" is a valid Europa signal (AMUNDI EUROBOND,
            # XYZ EURO FUND) BUT it is also a substring of the hedge-currency share-class
            # suffix " euroh"/" eurhdg"/" eurhgd" (e.g. PIMCO US HY BND EUROH).
            # Count " euro" as Europa only when not explained by hedge-class codes.
            or (" euro" in name_l
                and "euroh" not in name_l
                and "eurhdg" not in name_l
                and "eurhgd" not in name_l)):
        return "Europa"
    # Señal US débil: "\bus\b" solo -- puede ser código de clase (p.ej. "W1 US AC").
    # Se evalúa después de Europa para no sobreescribir señales europeas fuertes.
    if _US_STANDALONE_WORD.search(name_l):
        return "EEUU"
    if any(k in name_l for k in ["global","glob ","globl"," glb "," gbl ","glbl","glbal",
                                   " gl ","world","wrld","wld ","international","intl",
                                   "worldwide","multi-region","multiregion"]):
        return "Global"
    return None


THEMATIC_MAP: dict = {
    "technology": "Technology", "tech": "Technology",
    "smart ind tec": "Technology",
    "artificial intelligence": "Artificial Intelligence",
    "artificial intelligenc": "Artificial Intelligence",
    " ai ": "Artificial Intelligence",
    "digital": "Digital", "robotics": "Robotics", "robotech": "Robotics",
    "healthcare": "Healthcare", "health": "Healthcare", "wellcare": "Healthcare",
    "biotec": "Biotechnology", "biotech": "Biotechnology",
    "climate": "Climate / Clean Energy", "clean energy": "Climate / Clean Energy",
    "renewable": "Climate / Clean Energy",
    "water": "Water", "pictet water": "Water",
    "energy": "Energy",
    "real estate": "Real Estate", "real estat": "Real Estate", "property": "Real Estate",
    "silver age": "Silver Economy", "silverplus": "Silver Economy",
    "insurance": "Insurance",
    "global brands": "Consumer Brands", "glob brands": "Consumer Brands",
    "financial": "Financials", "financials": "Financials",
    "mining": "Mining", "gold": "Gold",
    "infrastructure": "Infrastructure", "infraestructura": "Infrastructure",
    # BL-23: Inflation — prevenir Theme en español "Inflación"
    "inflation": "Inflation", "inflacion": "Inflation", "inflación": "Inflation",
    "inflat": "Inflation",
    # BL-23: Cybersecurity — cubrir tema detectado en datos
    "cyber": "Cybersecurity", "cybersecurity": "Cybersecurity",
    # BL-23: Megatrends
    "megatrend": "Megatrends",
}


# ============================================================
# BL-54: THEME_TO_SECTOR_FOCUS_MAP — mapa canónico Theme → Sector_Focus
# Punto ÚNICO de verdad (Principio #2 DRY).
# BL-53/54 (idioma objetivo: INGLÉS, GICS-EN — v20 §2A.1 #6). El emisor único
# produce ya las 8 etiquetas canónicas en inglés; las conversiones ES→EN aguas
# abajo (pipeline._SF_ES_TO_EN, sqlite_writer CASE) quedan obsoletas (Principio #1/#2).
# Invocado desde fund_characterizer.detect_sector_focus() y pipeline.py.
# Cualquier nuevo Theme se añade SOLO aquí — no en otros módulos.
# ============================================================
THEME_TO_SECTOR_FOCUS_MAP: dict = {
    # Technology
    "Technology":              "Technology & Innovation",
    "Artificial Intelligence": "Technology & Innovation",
    "Digital":                 "Technology & Innovation",
    "Robotics":                "Technology & Innovation",
    "Cybersecurity":           "Technology & Innovation",
    # Healthcare
    "Healthcare":              "Healthcare & Life Sciences",
    "Healthcare / MedTech":    "Healthcare & Life Sciences",
    "Biotechnology":           "Healthcare & Life Sciences",
    "Silver Economy":          "Healthcare & Life Sciences",
    # Energy / climate
    "Energy":                  "Energy & Resources",
    "Climate / Clean Energy":  "Energy & Resources",
    # Utilities / water
    "Water":                   "Utilities & Environment",
    # Materials
    "Gold":                    "Materials & Mining",
    "Mining":                  "Materials & Mining",
    # Real assets
    "Real Estate":             "Real Assets",
    "Infrastructure":          "Real Assets",
    # Financial services (v20: 'Financials & Insurance' colapsado en 'Financial Services')
    "Insurance":               "Financial Services",
    "Financials":              "Financial Services",
    "Financial Services":      "Financial Services",  # FIX-SECTOR-FINSERV-1
    # Consumer
    "Consumer Brands":              "Consumer",
    "Consumer / Food & Beverage":   "Consumer",
}


def map_theme_to_sector_focus(theme: Optional[str]) -> Optional[str]:
    """
    Mapeo canónico Theme → Sector_Focus. Punto único de verdad (BL-54).

    Devuelve el Sector_Focus en INGLÉS (GICS-EN canónico) correspondiente al
    Theme, o None si el Theme no tiene mapeo (p.ej. Core/General, Megatrends,
    Inflation — que son Thematic sin foco sectorial concreto).
    """
    if not theme:
        return None
    return THEME_TO_SECTOR_FOCUS_MAP.get(theme)


# ============================================================
# BL-53/54: SECTOR_FOCUS_TRANSLATION_MAP — idioma objetivo: INGLÉS (GICS-EN).
# Saneo legacy: cualquier etiqueta ES (o variante EN antigua) → canónico v20
# (§2A.1 #6, 8 valores). Sustituye al antiguo mapa EN→ES (BL-22).
# ============================================================
SECTOR_FOCUS_TRANSLATION_MAP: dict = {
    # ES legacy → EN canónico v20
    "Tecnología e Innovación":      "Technology & Innovation",
    "Salud y Ciencias de la Vida":  "Healthcare & Life Sciences",
    "Energía y Recursos":           "Energy & Resources",
    "Materiales y Minería":         "Materials & Mining",
    "Utilities y Medio Ambiente":   "Utilities & Environment",
    "Servicios Financieros":        "Financial Services",
    "Consumo":                      "Consumer",
    "Consumo y Retail":             "Consumer",
    "Activos Reales":               "Real Assets",
    "Infraestructura":              "Real Assets",
    "Inmobiliario":                 "Real Assets",
    # Variantes EN antiguas → canónico v20
    "Financials & Insurance":       "Financial Services",
    "Consumer Discretionary":       "Consumer",
    "Real Estate & Infrastructure": "Real Assets",
    "Real Estate":                  "Real Assets",
    "Infrastructure":               "Real Assets",
    # Identidad EN canónica v20 (pass-through)
    "Technology & Innovation":      "Technology & Innovation",
    "Healthcare & Life Sciences":   "Healthcare & Life Sciences",
    "Energy & Resources":           "Energy & Resources",
    "Materials & Mining":           "Materials & Mining",
    "Utilities & Environment":      "Utilities & Environment",
    "Financial Services":           "Financial Services",
    "Consumer":                     "Consumer",
    "Real Assets":                  "Real Assets",
}


def normalize_sector_focus(value: Optional[str]) -> Optional[str]:
    """Normaliza Sector_Focus al idioma objetivo (INGLÉS, GICS-EN). BL-53/54.

    Cualquier etiqueta ES o variante EN antigua se sanea al canónico v20.
    Valor desconocido → pass-through.
    """
    if value is None:
        return None
    translated = SECTOR_FOCUS_TRANSLATION_MAP.get(value)
    if translated:
        return translated
    return value


# ============================================================
# BL-56/BL-57: TYPE_TRANSLATION_MAP — idioma objetivo: español
# Las excepciones inglesas (Allocation, Absolute Return, etc.) se mantienen
# porque carecen de equivalente compacto en español y son terminología
# sectorial consolidada (decisión BL-53).
# ============================================================
TYPE_TRANSLATION_MAP: dict = {
    # BL-LANG-EN (2026-05-09): idioma objetivo EN. Pass-through valores EN canónicos.
    # Corrección inversa: stale ES → EN para sanear BD de ciclos anteriores.
    # --- Pass-through EN canónicos (identidad) ---
    "Active Management":        "Active Management",
    "Index Fund":               "Index Fund",
    "Money Market":             "Money Market",
    "Government Money Market":  "Government Money Market",
    "Prime Money Market":       "Prime Money Market",
    "Short-Term Fixed Income":  "Short-Term Fixed Income",
    "Flexible Fixed Income":    "Flexible Fixed Income",
    "Short-Term Government":    "Short-Term Government",
    "Short-Term Credit":        "Short-Term Credit",
    "Commodities":              "Commodities",
    "Real Assets":              "Real Assets",
    "Volatility Target":        "Volatility Target",
    "Structured":               "Structured",
    "Allocation":               "Allocation",
    "Absolute Return":          "Absolute Return",
    "Total Return":             "Total Return",
    "Tactical Allocation":      "Tactical Allocation",
    "Target Maturity":          "Target Maturity",
    "Floating Rate CP":         "Floating Rate CP",
    "Unconstrained":            "Unconstrained",
    # --- Corrección inversa: stale ES → EN canónico ---
    "Gestión Activa":           "Active Management",
    "Indexado":                 "Index Fund",
    "Monetario":                "Money Market",
    "Monetario Público":        "Government Money Market",
    "Monetario Privado":        "Prime Money Market",
    "Renta Fija Corto Plazo":   "Short-Term Fixed Income",
    "Renta Fija Flexible":      "Flexible Fixed Income",
    "Gobierno CP":              "Short-Term Government",
    "Deuda Pública CP":         "Short-Term Government",   # fusión
    "Crédito CP":               "Short-Term Credit",
    "Materias Primas":          "Commodities",
    "Activos Reales":           "Real Assets",
    "Objetivo de Volatilidad":  "Volatility Target",
    "Estructurado":             "Structured",
}


# ============================================================
# BL-LANG-EN (2026-05-09): FAMILY_TRANSLATION_MAP — idioma objetivo EN.
# Pass-through valores EN canónicos. Corrección inversa ES→EN para BD.
# ============================================================
FAMILY_TRANSLATION_MAP: dict = {
    # --- Pass-through EN canónicos (identidad) ---
    "Equity Core":              "Equity Core",
    "Thematic Equity":          "Thematic Equity",
    "Multi-Asset":              "Multi-Asset",
    "Short-Term Fixed Income":  "Short-Term Fixed Income",
    "Flexible Fixed Income":    "Flexible Fixed Income",
    "Money Market":             "Money Market",
    "Absolute Return":          "Absolute Return",
    "Real Assets":              "Real Assets",
    "High Yield":               "High Yield",
    "Emerging Market Debt":     "Emerging Market Debt",
    "Inflation-Linked":         "Inflation-Linked",
    "Strategic Allocation":     "Strategic Allocation",
    "Income Oriented":          "Income Oriented",
    "Structured":               "Structured",
    "LVNAV":                    "LVNAV",
    "VNAV":                     "VNAV",
    "CNAV":                     "CNAV",
    # --- Corrección inversa: stale ES → EN canónico ---
    "RV Core":                  "Equity Core",
    "RV Temática":              "Thematic Equity",
    "Mixtos":                   "Multi-Asset",
    "Renta Fija Corto Plazo":   "Short-Term Fixed Income",
    "Renta Fija Flexible":      "Flexible Fixed Income",
    "Monetario":                "Money Market",
    "Retorno Absoluto":         "Absolute Return",
    "Activos Reales":           "Real Assets",
    "RF High Yield":            "High Yield",
    "RF Emergentes":            "Emerging Market Debt",
    "RF Inflación":             "Inflation-Linked",
    "Flexible Estratégico":     "Strategic Allocation",
    "Estructurado":             "Structured",
    "Orientado a Renta":        "Income Oriented",
}


def detect_theme(name_l: str) -> Optional[str]:
    """Detecta temática solo desde el nombre del fondo (canónico v2)."""
    for keyword, theme in THEMATIC_MAP.items():
        if keyword in name_l:
            return theme
    return None


_ESG_NAME_KEYWORDS = [
    "esg","sustainable","sustainability","sri","responsible",
    "green bond","climate aware","impact","paris aligned",
    "low carbon","carbon","socially","net zero","transition",
]


def detect_is_esg(fund_name: str) -> int:
    """Detecta política ESG desde el nombre del fondo."""
    if not fund_name or not isinstance(fund_name, str):
        return 0
    name_l = fund_name.lower()
    return 1 if any(k in name_l for k in _ESG_NAME_KEYWORDS) else 0


def detect_style_profile(name_l: str) -> Optional[str]:
    """Detecta estilo de gestión desde el nombre del fondo."""
    if any(k in name_l for k in ["low vol","low volatility","minimum volatility",
                                   "minimum vol","min vol","min volatil",
                                   "low risk","capital preservation"]):
        return "Low Volatility"
    if any(k in name_l for k in ["income","dividend","dividende","dividends",
                                   "rend","rendement","high yield"]):
        return "Income"
    if "quality" in name_l:
        return "Quality"
    if any(k in name_l for k in ["growth","wachstum","crecim","crecimiento"]):
        return "Growth"
    if "value" in name_l and "relative value" not in name_l:
        return "Value"
    if any(k in name_l for k in ["momentum","trend","trend follow"]):
        return "Momentum"
    if any(k in name_l for k in ["risk control","risk managed","risk parity",
                                   "risk target","volatility target"]):
        return "Risk Control"
    return None


def detect_exposure_bias(name_l: str, fund_nature: Optional[str] = None) -> Optional[str]:
    """Detecta sesgo estructural de cartera. NULL obligatorio en Monetario y Mixto."""
    if fund_nature in ("Monetario","Mixtos"):  # BL-19: "Mixtos"
        return None
    if any(k in name_l for k in ["barrier","autocall","knock-in"]):
        return "Barrier Risk"
    if any(k in name_l for k in ["commodit","commodity","gold","precious metal",
                                   "energy","oil","mining","copper"]):
        return "Commodity Bias"
    if any(k in name_l for k in ["real estate","property","reit","epra"]):
        return "Real Estate Bias"
    if any(k in name_l for k in ["absolute return","total return","market neutral",
                                   "long short","long/short"]):
        return "Absolute Return Bias"
    if any(k in name_l for k in ["low vol","minimum volatility","min vol","low risk"]):
        return "Low Volatility Bias"
    if any(k in name_l for k in ["income","dividend","dividende"]):
        return "Income Bias"
    if any(k in name_l for k in ["credit","crédito","high yield","hy ","corporate",
                                   "corp bond","opportunistic"]):
        return "Credit Bias"
    if any(k in name_l for k in ["float","floating rate","frn","variable rate"]):
        return "Rate Reset Bias"
    if any(k in name_l for k in ["liquid","liquidity","money market","cash"]):
        return "Liquidity Bias"
    if fund_nature in ("Renta Fija Corto Plazo","Renta Fija Flexible"):
        return "Duration Bias"
    return None


def detect_strategy(
    replication_method: Optional[str],
    subtype: Optional[str],
    name_l: str = "",
) -> Optional[str]:
    """Consolida la estrategia de gestión."""
    sub_l = (subtype or "").lower()
    rep_l = (replication_method or "").lower()
    if any(k in sub_l for k in ["fondo indexado","etf","index fund"]):
        return "Indexado"
    if any(k in name_l for k in ["etf","index fund","tracker"]):
        return "Indexado"
    if any(k in sub_l for k in ["systematic","cta","quant"]):
        return "Activo"          # v20: sistemático/quant = gestión activa
    if any(k in name_l for k in ["systematic","quant ","cta ","managed future"]):
        return "Activo"
    if any(k in name_l for k in ["smart beta","factor","multi-factor","multifactor",
                                   "quality factor","value factor"]):
        return "Indexado"        # v20: factor/smart-beta = réplica basada en reglas
    if rep_l == "passive":
        return "Pasivo"
    if any(k in name_l for k in ["passive","passiv","replica"]):
        return "Pasivo"
    if rep_l == "active":
        return "Activo"
    return None


def detect_benchmark_type(
    benchmark_declared: Optional[str],
    replication_method: Optional[str] = None,
) -> Optional[str]:
    """Infiere el tipo de relación con el benchmark."""
    if benchmark_declared == "NO_BENCHMARK":
        return "NO_BENCHMARK"
    if not benchmark_declared:
        return None
    rep_l = (replication_method or "").upper()
    if rep_l == "PASSIVE":
        return "TARGET_INDEX"
    bench_l = benchmark_declared.lower()
    if any(k in bench_l for k in ["replica","track","tracks","replicat"]):
        return "TARGET_INDEX"
    return "REFERENCE_INDEX"


def detect_profile_from_srri(srri: Optional[int]) -> Optional[str]:
    """Deriva Profile desde SRRI con precedencia absoluta."""
    # BL-SRRI-GUARD: extract_srri (srri_text.py) puede devolver dict en algunos
    # formatos DDF; otros callers pasan str. Coercer a int antes de comparar
    # evita "'<=' not supported between 'dict' and 'int'" (crash RESTANTES).
    if isinstance(srri, dict):
        srri = srri.get("SRRI")
    if isinstance(srri, str):
        srri = int(srri) if srri.strip().isdigit() else None
    if srri is None:
        return None
    if srri <= 2:
        return "Conservador"
    if srri <= 4:
        return "Moderado"
    return "Dinámico"


# ============================================================
# BL-49 — Detección Currency_Hedged desde texto KIID
# ============================================================
#
# Causa raíz: detect_currency_hedged() en fund_characterizer.py admite
# kiid_text en su firma desde v18, pero el cuerpo no implementa extracción
# sobre ese texto. Solo examina el nombre del fondo (fase 1). Los 535 NULLs
# residuales de Currency_Hedged corresponden en su mayoría a fondos
# denominados en USD/GBP/CHF/JPY/CNH (divisa ≠ EUR) donde la señal de
# cobertura no aparece en el nombre sino en la sección de "Share class
# characteristics" o "Objetivos y política de inversión" del KIID.
#
# Solución DRY: implementar _detect_ch_from_kiid_text() aquí (classify_utils)
# para que fund_characterizer la importe como segunda fase, en lugar de
# duplicar patrones en fund_characterizer. Principio #2.
#
# Restricción de aplicación: solo actúa cuando la fase basada en nombre
# (fund_characterizer) no aportó señal. La prevalencia de Hedging_Policy
# sobre Currency_Hedged (BL-31/INTER-12) sigue aplicándose en pipeline
# DESPUÉS de este extractor.

# Patrones de alta confianza. Orden: primero específicos, luego genéricos.
# CH_ID se usa en logging: "CH-KIID-<CH_ID>".
_CH_HEDGED_PATTERNS: list[tuple[str, str]] = [
    # Inglés — share class explícita
    ("H01", r"\bcurrency[- ]hedged\s+share\s+class\b"),
    ("H02", r"\bhedged\s+share\s+class\b"),
    ("H03", r"\bcurrency\s+risk\s+is\s+hedged\b"),
    ("H04", r"\bfully\s+hedged\b"),
    ("H05", r"\bhedge[d]?\s+against\s+(?:eur|usd|gbp|chf|jpy|cnh)\b"),
    ("H06", r"\bthis\s+share\s+class\s+is\s+hedged\b"),
    # Español — clase cubierta
    ("H07", r"\bclase\s+(?:de\s+)?(?:acciones|participaciones)\s+(?:con\s+)?cobertura\s+(?:de\s+divisa|cambiaria)\b"),
    ("H08", r"\bcobertura\s+(?:total|íntegra)\s+del?\s+(?:riesgo|tipo)\s+de\s+cambio\b"),
    ("H09", r"\briesgo\s+de\s+(?:cambio|divisa)\s+est[áa]\s+cubierto\b"),
    ("H10", r"\besta\s+clase\s+est[áa]\s+cubierta\s+(?:contra|frente\s+a)\b"),
]

_CH_UNHEDGED_PATTERNS: list[tuple[str, str]] = [
    # Inglés — sin cobertura explícita
    ("U01", r"\b(?:unhedged|not\s+hedged|without\s+(?:currency\s+)?hedging)\s+share\s+class\b"),
    ("U02", r"\bno\s+currency\s+hedging\b"),
    ("U03", r"\bcurrency\s+risk\s+is\s+not\s+hedged\b"),
    ("U04", r"\bno\s+hedging\s+of\s+currency\s+risk\b"),
    # Español — sin cobertura explícita
    ("U05", r"\bsin\s+cobertura\s+(?:de\s+divisa|cambiaria|del?\s+riesgo\s+de\s+cambio)\b"),
    ("U06", r"\bno\s+(?:se\s+)?cubre\s+el\s+(?:riesgo\s+de\s+)?(?:cambio|divisa)\b"),
    ("U07", r"\bno\s+aplica\s+cobertura\s+de\s+divisa\b"),
    ("U08", r"\besta\s+clase\s+no\s+est[áa]\s+cubierta\b"),
]

# Pre-compilar (se importa una vez en arranque del pipeline)
_CH_HEDGED_RE: list[tuple[str, re.Pattern]] = [
    (pid, re.compile(pat, re.IGNORECASE)) for pid, pat in _CH_HEDGED_PATTERNS
]
_CH_UNHEDGED_RE: list[tuple[str, re.Pattern]] = [
    (pid, re.compile(pat, re.IGNORECASE)) for pid, pat in _CH_UNHEDGED_PATTERNS
]


def detect_currency_hedged_from_kiid(
    kiid_text: str,
) -> tuple[Optional[str], Optional[str]]:
    """Detecta Currency_Hedged desde el texto completo del KIID.

    Segunda fase de detección (se invoca cuando la detección por nombre
    no aportó señal). Solo patrones de alta confianza — declaración
    explícita de share class hedged/unhedged.

    Args:
        kiid_text: texto completo extraído del KIID/DDF.

    Returns:
        (value, pattern_id) donde value ∈ {'Hedged', 'Unhedged', None}.
        pattern_id identifica el patrón que disparó la detección
        (para logging en pipeline: 'CH-KIID-<pattern_id>').
        Si no hay señal, retorna (None, None).
    """
    if not kiid_text:
        return None, None

    t = kiid_text  # patrones usan re.IGNORECASE, no hace falta lower()

    for pid, compiled in _CH_HEDGED_RE:
        if compiled.search(t):
            return "Hedged", pid

    for pid, compiled in _CH_UNHEDGED_RE:
        if compiled.search(t):
            return "Unhedged", pid

    return None, None


# Validadores inter/intra-atributo + función maestra
# Añadir al final de classify_utils.py
# ============================================================

logger = logging.getLogger(__name__)


# ============================================================
# 12. ALLOWED_VALUES_BY_COLUMN
# ============================================================

ALLOWED_VALUES_BY_COLUMN: dict = {}
# §Y-1 (DRY root-cause): NO duplicar vocabularios. Se DERIVAN de
# config.DOMAIN_VALUES (capa de intención de diseño = fuente única). Se filtran
# a las columnas categóricas (casing TITLE/UPPER_SNAKE); las numéricas (SRRI,
# Sfdr_Article, RHP) y códigos ISO (Fund_Currency) no entran en el chequeo de
# allowed-values. Import defensivo (sys.path variable en distintos entrypoints).
try:
    import config as _cfg_catalog  # type: ignore
except ImportError:  # pragma: no cover
    try:
        from shared import config as _cfg_catalog  # type: ignore
    except ImportError:
        # Entry-points como run_block.py importan core.pipeline -> classify_utils
        # ANTES de insertar el project-root (parents[1]) en sys.path, por lo que
        # `shared` aún no es importable en module-load. Inyectar el project-root
        # (core -> proyecto1 -> root = parents[2]) y reintentar. Sin esto,
        # ALLOWED_VALUES_BY_COLUMN y _CASING_LOOKUP quedan vacíos y el
        # normalizador de casing se vuelve un no-op silencioso.
        try:
            import sys as _sys
            from pathlib import Path as _P
            _root = str(_P(__file__).resolve().parents[2])
            if _root not in _sys.path:
                _sys.path.insert(0, _root)
            from shared import config as _cfg_catalog  # type: ignore
        except Exception:
            _cfg_catalog = None

if _cfg_catalog is not None:
    _dv = getattr(_cfg_catalog, "DOMAIN_VALUES", {})
    _casing = getattr(_cfg_catalog, "ATTRIBUTE_CASING", {})
    ALLOWED_VALUES_BY_COLUMN = {
        col: list(vals)
        for col, vals in _dv.items()
        if _casing.get(col) in ("TITLE", "UPPER_SNAKE")
    }
else:  # config no importable: degradar sin corromper (warnings-only consumer)
    logging.getLogger(__name__).warning(
        "config no importable: ALLOWED_VALUES_BY_COLUMN vacío "
        "(el chequeo allowed-values quedará inactivo)."
    )


# ============================================================
# Casing normalizer — UNA función (§Y-2, R-1, Principio #2 DRY)
# ------------------------------------------------------------
# Canonicaliza el CASING de un valor categórico al canónico de
# config.DOMAIN_VALUES, vía lookup insensible a mayúsculas/separadores.
# NO inventa remaps de valor (eso es el reprocess); solo arregla casing/drift
# tipográfico (ACCUMULATION→Accumulation, HEDGED→Hedged). Preserva acrónimos
# (CNAV, ETF, VNAV) porque copia la forma canónica del catálogo, nunca .title().
# Si el valor no casa con ningún canónico, se devuelve intacto (el chequeo de
# allowed-values lo señalará como WARNING). Aplicar pre-persist en
# sqlite_writer._normalize_record (los valores no mutan a mitad de flujo).
# ============================================================
def _casefold_key(s: str) -> str:
    """Clave de comparación: minúsculas, espacios/guiones-bajos colapsados."""
    return re.sub(r"[\s_]+", " ", s.strip().casefold())


# Cache de lookups canónicos por columna {col: {casefold_key: canonical}}.
_CASING_LOOKUP: dict = {
    col: {_casefold_key(v): v for v in vals}
    for col, vals in ALLOWED_VALUES_BY_COLUMN.items()
}


def normalize_casing(column: str, value):
    """Devuelve `value` con el casing canónico de `column` (o intacto si no
    aplica / no casa). None y no-str pasan tal cual."""
    if value is None or not isinstance(value, str):
        return value
    lookup = _CASING_LOOKUP.get(column)
    if not lookup:
        return value
    return lookup.get(_casefold_key(value), value)


# ============================================================
# 13. ALLOWED_TYPE_BY_NATURE
# ============================================================

ALLOWED_TYPE_BY_NATURE: dict = {
    "Renta Variable": [
        # BL-LANG-EN-FIX (2026-05-18): "Gestión Activa"→"Active Management",
        # "Indexado"→"Index Fund". Los stale ES se mantenían para no romper
        # validaciones, pero ahora _DEFAULT_TYPE_BY_NATURE emite EN → coherencia.
        "Active Management", "Index Fund", "Total Return",
        "Absolute Return", "Tactical Allocation",
    ],
    "Renta Fija Flexible": [
        "Flexible Fixed Income", "Active Management", "Total Return",
        "Absolute Return", "Index Fund",
        "Target Maturity",
    ],
    "Renta Fija Corto Plazo": [
        "Short-Term Fixed Income", "Short-Term Credit", "Short-Term Government",
        "Floating Rate CP", "Target Maturity",
    ],
    "Monetario": [
        "Money Market", "Government Money Market", "Prime Money Market",
    ],
    "Mixtos": [
        "Allocation", "Tactical Allocation", "Active Management",
        "Volatility Target",
    ],
    "Alternativo": [
        "Absolute Return", "Commodities", "Total Return",
        "Active Management", "Index Fund",
        "Real Assets",
    ],
    "Estructurado": [
        "Structured",
    ],
    "Restantes": [],  # catch-all: Type puede ser None o cualquier valor válido
                      # v10: restituido (eliminado erróneamente por BL-65)
}


# ============================================================
# 14. ALLOWED_FAMILY_BY_NATURE
# ============================================================

ALLOWED_FAMILY_BY_NATURE: dict = {
    "Renta Variable": [
        "Equity Core", "Thematic Equity", "Real Assets",
    ],
    "Renta Fija Flexible": [
        "Flexible Fixed Income", "High Yield", "Emerging Market Debt",
        "Inflation-Linked", "Income Oriented",
        "Strategic Allocation",
    ],
    "Renta Fija Corto Plazo": [
        "Short-Term Fixed Income",
    ],
    "Monetario": [
        "Money Market", "LVNAV", "VNAV", "CNAV",
    ],
    "Mixtos": [
        "Multi-Asset", "Income Oriented", "Strategic Allocation",
    ],
    "Alternativo": [
        "Absolute Return", "Real Assets",
    ],
    "Estructurado": [
        "Structured",
    ],
    "Restantes": [],  # catch-all: Family puede ser None o cualquier valor válido
                      # v10: restituido (eliminado erróneamente por BL-65)
}

# Families that are valid for RFF but NOT for RFC.  Used by BL-64e (pipeline.py),
# BL-64E-INLINE post-BL-44 (pipeline.py), and fund_family_builder.py to correct
# RFC funds carrying an incompatible family inherited from a BL-62 inference.
# Derived from ALLOWED_FAMILY_BY_NATURE to guarantee a single source of truth (P#11).
RFC_INCOMPATIBLE_FAMILIES: frozenset = frozenset(
    ALLOWED_FAMILY_BY_NATURE["Renta Fija Flexible"]
) - frozenset(ALLOWED_FAMILY_BY_NATURE["Renta Fija Corto Plazo"])


# ============================================================
# 15. THEME_SECTOR_MAPPING
# ============================================================

THEME_SECTOR_MAPPING: dict = {
    # Cluster Technology & Innovation
    "Technology":            "Technology & Innovation",
    "Artificial Intelligence": "Technology & Innovation",
    "Digital":               "Technology & Innovation",
    "Robotics":              "Technology & Innovation",
    "Cybersecurity":         "Technology & Innovation",
    # Cluster Healthcare & Life Sciences
    "Healthcare":            "Healthcare & Life Sciences",
    "Healthcare / MedTech":  "Healthcare & Life Sciences",
    "Biotechnology":         "Healthcare & Life Sciences",
    "Silver Economy":        "Healthcare & Life Sciences",
    # Cluster Energy & Resources
    "Energy":                "Energy & Resources",
    "Climate / Clean Energy": "Energy & Resources",
    # Cluster Utilities & Environment
    "Water":                 "Utilities & Environment",
    # Cluster Materials & Mining
    "Gold":                  "Materials & Mining",
    "Mining":                "Materials & Mining",
    # Cluster Real Assets (v20: 'Real Assets' en lugar de 'Real Estate & Infrastructure')
    "Real Estate":           "Real Assets",
    "Infrastructure":        "Real Assets",
    # Cluster Financial Services (v20: 'Financial Services' en lugar de 'Financials & Insurance')
    "Financials":            "Financial Services",
    "Insurance":             "Financial Services",
    # Cluster Consumer (v20: 'Consumer' en lugar de 'Consumer Discretionary')
    "Consumer Brands":       "Consumer",
    # Temas cross-sector (Thematic ONLY) — sin Sector_Focus canónico.
    # No se incluyen aquí para que validate_theme_sector_coherence no genere
    # un falso WARN cuando Sector_Focus=NULL (el caso correcto).
    # Gestionados por INTER-15 en validate_all_semantic_consistency.
    # "Megatrends": None,
    # "Inflation": None,
}

# Temas que por definición son cross-sector y NUNCA pueden corresponder a
# un único sector industria. Investment_Focus debe ser siempre 'Thematic' y
# Sector_Focus debe ser NULL. Referencia: SEMANTIC_MODEL_CLASSIFICATION.md SC-B1/B2.
_THEMATIC_ONLY_THEMES: frozenset = frozenset({"Megatrends", "Inflation"})

# ============================================================
# 14b. DEFAULT TYPE/FAMILY BY NATURE (P07 — auto-corrección)
# ============================================================

_DEFAULT_TYPE_BY_NATURE: dict = {
    # BL-LANG-EN-FIX (2026-05-18): valores EN canónicos.
    # Los 4 valores ES stale ("Gestión Activa", "Renta Fija Flexible",
    # "Renta Fija Corto Plazo", "Monetario", "Estructurado") no se actualizaron
    # en BL-LANG-EN (v12) — causa raíz del [NORM-Type] WARNING masivo en ciclo.
    "Renta Variable":         TYPE_ACTIVE_MANAGEMENT,       # "Active Management"
    "Renta Fija Flexible":    TYPE_FLEXIBLE_FI,             # "Flexible Fixed Income"
    "Renta Fija Corto Plazo": TYPE_SHORT_TERM_FI,           # "Short-Term Fixed Income"
    "Monetario":              TYPE_MONEY_MARKET,             # "Money Market"
    "Mixtos":                 "Allocation",                  # sin constante (término sectorial)
    "Alternativo":            "Absolute Return",             # sin constante (término sectorial)
    "Estructurado":           TYPE_STRUCTURED,               # "Structured"
}

_DEFAULT_FAMILY_BY_NATURE: dict = {
    # BL-LANG-EN-FIX (2026-05-18): valores EN canónicos.
    # Los 6 valores ES stale no se actualizaron en BL-LANG-EN (v12).
    "Renta Variable":         FAMILY_EQUITY_CORE,           # "Equity Core"
    "Renta Fija Flexible":    FAMILY_FLEXIBLE_FI,           # "Flexible Fixed Income"
    "Renta Fija Corto Plazo": FAMILY_SHORT_TERM_FI,         # "Short-Term Fixed Income"
    "Monetario":              FAMILY_MONEY_MARKET,           # "Money Market"
    "Mixtos":                 FAMILY_MULTI_ASSET,            # "Multi-Asset"
    "Alternativo":            FAMILY_ABSOLUTE_RETURN,        # "Absolute Return"
    "Estructurado":           FAMILY_STRUCTURED,             # "Structured"
}

# ============================================================
# 1. INTER-1: Strategy ↔ Replication_Method
# ============================================================

def validate_strategy_replication(
    strategy: Optional[str],
    replication: Optional[str],
) -> tuple:
    """v20: Replication_Method = TÉCNICA de réplica (Physical/Synthetic/Sampling)
    para gestión pasiva; 'Not Applicable' para gestión activa. El eje activo/pasivo
    vive ahora en Strategy (§2A.1 #9). Auto-corrige incoherencias.

    Returns:
        (corrected_replication, error_msg_or_None)
    """
    _TECH = ("Physical", "Synthetic", "Sampling")
    if strategy in ("Indexado", "Pasivo"):
        if replication in _TECH:
            return replication, None
        return "Physical", (
            f"Replication_Method→'Physical' (técnica por defecto; "
            f"Strategy='{strategy}' es pasiva)"
        )
    if strategy == "Activo":
        if replication != "Not Applicable":
            return "Not Applicable", (
                "Replication_Method→'Not Applicable' (Strategy='Activo')"
            )
        return replication, None
    return replication, None


# ============================================================
# 2. INTER-2: Accumulation_Policy ↔ Distribution_Frequency
# ============================================================

def validate_accumulation_distribution(
    acc_policy: Optional[str],
    dist_freq: Optional[str],
) -> tuple:
    """Auto-corrige Distribution_Frequency/Accumulation_Policy por coherencia.

    Reglas:
      ACCUMULATION + dist_freq poblado  → eliminar dist_freq (crítico)
      DISTRIBUTION + dist_freq NULL     → warning
      NULL + dist_freq poblado          → inferir ACCUMULATION_POLICY=DISTRIBUTION (BL-32)

    Returns:
        (corrected_acc_policy, corrected_dist_freq, error_msg_or_None)

    NOTA: La firma retorna ahora 3 valores. validate_all_semantic_consistency
    actualiza ambos campos.
    """
    if acc_policy == "ACCUMULATION" and dist_freq is not None:
        return acc_policy, None, (
            "Eliminado Distribution_Frequency "
            "(coherencia con ACCUMULATION)"
        )
    if acc_policy == "DISTRIBUTION" and dist_freq is None:
        # FIX-ACCDIST-3 (2026-07-16): distribution frequency lives in the full prospectus,
        # not the 2-page KID. A DISTRIBUTION fund with NULL Distribution_Frequency simply
        # means "not stated in the KID" — not an unresolved inconsistency. Per P#10
        # (NULL = "not discovered") downgrade from WARN to INFO by dropping the "WARNING:"
        # prefix; validate_all_semantic_consistency routes "WARNING:"-prefixed messages to
        # critical_errors (→ DQ WARN) and all others to warnings (→ DQ INFO).
        return acc_policy, dist_freq, (
            "DISTRIBUTION sin Distribution_Frequency (frecuencia no consta en KID)"
        )
    # BL-32: Distribution_Frequency presente implica política distribución
    if acc_policy is None and dist_freq is not None:
        return "DISTRIBUTION", dist_freq, (
            f"Inferido Accumulation_Policy='DISTRIBUTION' "
            f"desde Distribution_Frequency='{dist_freq}'"
        )
    return acc_policy, dist_freq, None


# ============================================================
# 3. INTER-3: Profile ↔ SRRI
# ============================================================

def _assign_profile_from_srri(srri: int) -> Optional[str]:
    """Mapeo estricto SRRI → Profile (incluye Agresivo para SRRI=7)."""
    if srri <= 2:
        return "Conservador"
    if srri <= 4:
        return "Moderado"
    if srri <= 6:
        return "Dinámico"
    if srri == 7:
        return "Agresivo"
    return None


def validate_profile_srri(
    profile: Optional[str],
    srri: Optional[int],
) -> tuple:
    """Valida coherencia Profile-SRRI. BL-INTER3-WARN: WARNINGS-ONLY.

    INTER-3 ya NO auto-corrige Profile desde SRRI: Profile es co-determinado por
    Fund_Nature (Profile = f(SRRI, Fund_Nature)); el remap por bandas estrictas era
    empíricamente erróneo. Devuelve siempre el profile original y, en las colas
    genuinas, un mensaje 'WARNING:' (el caller lo enruta a warnings, nunca a
    critical/auto-correct).

    Returns:
        (profile_unchanged, warning_msg_or_None)
    """
    if profile is None or srri is None:
        return profile, None

    if profile == "Conservador" and srri >= 6:
        return profile, f"WARNING: Conservador con SRRI={srri} es anómalo (máx. observado 5)"
    if profile == "Moderado" and srri in (1, 7):
        return profile, f"WARNING: Moderado con SRRI={srri} en extremo"
    if profile == "Dinámico" and srri <= 2:
        return profile, f"WARNING: Dinámico con SRRI={srri} es inusual"
    if profile == "Agresivo" and srri <= 4:
        return profile, f"WARNING: Agresivo con SRRI={srri} es bajo (revisar)"

    return profile, None


# ============================================================
# 4. INTER-4: Nature → Type  —  RETIRADO en v20 (§8-bis Q3 / §6-bis #1)
# ------------------------------------------------------------
# Con Type → Vehicle_Structure (eje ortogonal jurídico-estructural del vehículo),
# la restricción Fund_Nature→Type (vocabulario de clase de activo) pierde sentido.
# Se retira: el stub devuelve siempre (True, None) para no romper llamadores.
# ALLOWED_TYPE_BY_NATURE/_DEFAULT_TYPE_BY_NATURE quedan como referencia histórica
# (útiles para el reprocess que remapea los Type antiguos), pero NO se cablean.
# INTER-5 (Nature→Family) SÍ se mantiene: Family sigue siendo taxonomía de activo.
# ============================================================

def validate_nature_type_coherence(
    nature: Optional[str],
    type_val: Optional[str],
) -> tuple:
    """RETIRADO (v20). No-op: siempre (True, None). Ver cabecera de sección."""
    return True, None


# ============================================================
# 5. INTER-5: Nature → Family
# ============================================================

def validate_nature_family_coherence(
    nature: Optional[str],
    family: Optional[str],
) -> tuple:
    """Valida que Family sea permitida para la Nature dada.

    Returns:
        (is_valid: bool, error_msg_or_None)
    """
    if nature is None or family is None:
        return True, None
    allowed = ALLOWED_FAMILY_BY_NATURE.get(nature)
    if allowed is None:
        return True, None
    if family not in allowed:
        return False, (
            f"Family '{family}' no es válida para Nature '{nature}'. "
            f"Permitidos: {allowed}"
        )
    return True, None


# ============================================================
# 6. INTER-6: Investment_Universe → Sector_Focus / Geography
# ============================================================

def validate_universe_completeness(
    universe: Optional[str],
    sector_focus: Optional[str],
    geography: Optional[str],
) -> tuple:
    """Valida completitud de Sector_Focus/Geography según Universe.

    Returns:
        (is_valid: bool, issues: list[str])
    """
    issues: list = []
    if universe is None:
        return True, issues

    if universe == "Sector" and sector_focus is None:
        issues.append(
            "Investment_Universe='Sector' requiere Sector_Focus poblado"
        )
    if universe in ("Regional", "Country") and geography is None:
        issues.append(
            f"Investment_Universe='{universe}' requiere Geography poblado"
        )
    return len(issues) == 0, issues


# ============================================================
# 7. INTER-7: Leverage_Used ↔ Profile (WARNING)
# ============================================================

def validate_leverage_profile(
    profile: Optional[str],
    leverage: Optional[str],
) -> tuple:
    """Returns ('OK'|'WARNING', message_or_None)."""
    if profile == "Conservador" and leverage == "YES":
        return "WARNING", "Perfil Conservador con Leverage=YES es inusual"
    return "OK", None


# ============================================================
# 8. INTER-8: Is_ESG ↔ Sfdr_Article (WARNING)
# ============================================================

def validate_esg_sfdr(
    is_esg: Optional[int],
    sfdr_article: Optional[int],
) -> tuple:
    """Returns ('OK'|'WARNING', message_or_None)."""
    if is_esg == 1 and sfdr_article not in (8, 9, None):
        return "WARNING", (
            f"Is_ESG=1 con Sfdr_Article={sfdr_article} (esperado 8 o 9)"
        )
    return "OK", None


# ============================================================
# 9. INTER-9: Theme ↔ Sector_Focus (WARNING)
# ============================================================

def validate_theme_sector_coherence(
    theme: Optional[str],
    sector_focus: Optional[str],
) -> tuple:
    """Returns ('OK'|'WARNING', message_or_None)."""
    if theme and sector_focus:
        expected = THEME_SECTOR_MAPPING.get(theme)
        if expected and sector_focus != expected:
            return "WARNING", (
                f"Theme '{theme}' normalmente mapea a '{expected}', "
                f"no '{sector_focus}'"
            )
    return "OK", None


# ============================================================
# 10. INTER-10: Geography ↔ Investment_Universe (WARNING + BL-52 auto-corrección)
# ============================================================

_COUNTRY_GEOGRAPHIES = frozenset({
    # v20: vocabulario EN del catálogo (config.DOMAIN_VALUES['Geography']).
    # Valores "país" del set v20: Japan, China, India.
    "Japan", "China", "India",
})

# BL-52: valores de Geography que representan regiones (no países individuales).
# Universe='Country' con estos valores es semánticamente incorrecto → auto-corregir a 'Regional'.
_REGION_GEOGRAPHIES = frozenset({
    # v20: regiones del set EN (excluye 'Global', que no es región ni país).
    "Europe", "North America", "Asia-Pacific",
    "Latin America", "Eastern Europe", "Middle East & Africa",
})


def validate_geography_universe(
    geography: Optional[str],
    universe: Optional[str],
) -> tuple:
    """Returns ('OK'|'WARNING'|'CORRECTED', message_or_None, corrected_universe_or_None).

    BL-52: si Universe='Country' y Geography es una región → auto-corrección a 'Regional'.
    Firma ampliada a 3-tupla para transportar el valor corregido; los callers que
    esperan 2-tupla siguen funcionando si solo desempaquetan los dos primeros elementos.
    """
    # BL-52: AUTO-CORRECCIÓN — Country + región es imposible semánticamente
    if universe == "Country" and geography in _REGION_GEOGRAPHIES:
        msg = (
            f"Investment_Universe corregido 'Country'→'Regional' "
            f"porque Geography='{geography}' es una región, no un país"
        )
        return "CORRECTED", msg, "Regional"

    # Warnings existentes (sin cambio)
    if geography in _COUNTRY_GEOGRAPHIES and universe == "Global":
        return "WARNING", (
            f"Geography específica '{geography}' con "
            f"Universe='Global' es inusual"
        ), None
    if geography == "Global" and universe in ("Country", "Regional"):
        return "WARNING", (
            f"Geography='Global' con Universe='{universe}' es inusual"
        ), None
    return "OK", None, None


# ============================================================
# SC-H: Benchmark semantic-consistency vocabulary  (R-1 / P#11)
# ============================================================
# Single source of truth for all benchmark-comparison constants and helpers,
# shared between the in-pipeline SC-H validator rules (below, inside
# validate_all_semantic_consistency) and the read-only audit tool
# (audit_benchmark_consistency.py). The audit tool imports from here and
# drops its local copies.  Promoted from audit_benchmark_consistency.py
# 2026-07-15.
#
# Public names (importable by the audit tool and tests):
#   BMK_CONSISTENT, BMK_TOLERATED, BMK_BENIGN_SOURCE_PAIRS
#   BMK_GEO_BENIGN_PAIRS, BMK_SECTOR_BENIGN_PAIRS, BMK_CAP_BENIGN_PAIRS
#   bmk_tok_credit(), bmk_tok_duration(), bmk_tok_cap()
#   bmk_geography(), bmk_sector(), bmk_severity_nature()

# Fund_Nature → (consistent_ac_set, tolerated_ac_set)
# consistent = expected class → OK; tolerated = soft mismatch → INFO;
# everything else → CRITICAL (down-weighted to WARN at LOW/MEDIUM confidence).
BMK_CONSISTENT: dict[str, frozenset] = {
    "Renta Variable":         frozenset({"Equity"}),
    "Renta Fija Flexible":    frozenset({"Fixed Income"}),
    "Renta Fija Corto Plazo": frozenset({"Fixed Income", "Rate"}),
    "Monetario":              frozenset({"Money Market", "Rate"}),
    "Mixtos":                 frozenset({"Mixed", "Equity", "Fixed Income"}),
    "Alternativo":            frozenset({"Rate", "Commodity", "Mixed",
                                         "Fixed Income", "Equity"}),
    "Estructurado":           frozenset({"Equity", "Fixed Income", "Mixed",
                                         "Rate", "Commodity", "Money Market"}),
    "Restantes":              frozenset({"Equity", "Fixed Income", "Mixed",
                                         "Rate", "Commodity", "Money Market"}),
}
BMK_TOLERATED: dict[str, frozenset] = {
    "Renta Variable":         frozenset({"Commodity", "Mixed"}),
    "Renta Fija Flexible":    frozenset({"Rate", "Mixed"}),
    "Renta Fija Corto Plazo": frozenset({"Money Market", "Mixed"}),
    "Monetario":              frozenset({"Fixed Income"}),
    "Mixtos":                 frozenset(),
    "Alternativo":            frozenset(),
    "Estructurado":           frozenset(),
    "Restantes":              frozenset(),
}
# Benign category-proxy divergences between two data sources (Exercise-A, A1).
BMK_BENIGN_SOURCE_PAIRS: frozenset = frozenset({
    frozenset({"Equity",       "Mixed"}),
    frozenset({"Fixed Income", "Mixed"}),
    frozenset({"Rate",         "Mixed"}),
    frozenset({"Rate",         "Fixed Income"}),
    frozenset({"Money Market", "Rate"}),
    frozenset({"Money Market", "Fixed Income"}),
})

# ── Benchmark credit / duration / cap token tables ────────────────────────
_BMK_CREDIT_TOKENS: list[tuple[str, str]] = [
    ("high yield",       "High Yield"),
    (" hy ",             "High Yield"),
    (" hy$",             "High Yield"),
    ("subordinated",     "Subordinated"),
    ("sub financials",   "Subordinated"),
    ("investment grade", "Investment Grade"),
    (" ig ",             "Investment Grade"),
    ("aaa",              "AAA"),
    ("a-bbb",            "Investment Grade"),
    ("bbb",              "Investment Grade"),
    ("corporate",        "Corporate"),
    ("corp ",            "Corporate"),
    ("corp$",            "Corporate"),
    ("government",       "Government"),
    ("govt",             "Government"),
    ("treasury",         "Government"),
    ("sovereign",        "Government"),
    (" gbi",             "Government"),   # JPM GBI = Government Bond Index
    ("inflation",        "Inflation-Linked"),
    ("tips",             "Inflation-Linked"),
    ("convertible",      "Convertible"),
    ("convert",          "Convertible"),
    ("securitised",      "Securitised"),
    ("securitized",      "Securitised"),
    ("abs ",             "Securitised"),
    ("mbs ",             "Securitised"),
    ("aggregate",        "Aggregate"),
    ("agg ",             "Aggregate"),
    ("multiverse",       "Aggregate"),
]

_BMK_DURATION_TOKENS: list[tuple[str, str]] = [
    ("0-1y",        "Ultra-Short"),
    ("1-3y",        "Short"),
    ("1-5y",        "Short"),
    ("3-5y",        "Short"),
    ("5-7y",        "Medium"),
    ("7-10y",       "Long"),
    ("10y+",        "Long"),
    ("ultrashort",  "Ultra-Short"),
    ("ultra short", "Ultra-Short"),
    ("short dur",   "Short"),
    ("short term",  "Short"),
    ("short-term",  "Short"),
]

_BMK_CAP_TOKENS: list[tuple[str, str]] = [
    # Compound (range) tokens must appear before their constituent single-tier
    # tokens so "Morningstar US Large/Mid Cap" → "Large/Mid Cap", not "Mid Cap".
    ("small/mid",  "Small/Mid Cap"),
    ("mid/small",  "Small/Mid Cap"),
    ("large/mid",  "Large/Mid Cap"),
    ("mid/large",  "Large/Mid Cap"),
    ("small cap",  "Small Cap"),
    ("small",      "Small Cap"),
    ("mid cap",    "Mid Cap"),
    ("mid-cap",    "Mid Cap"),
    (" mid ",      "Mid Cap"),
    ("large cap",  "Large Cap"),
    ("large-cap",  "Large Cap"),
    (" mega",      "Large Cap"),
]

# ── Benign-pair suppression sets (noise reduction) ────────────────────────
# Benign geographic generalizations: sub-region benchmarked to a broader proxy.
# (FIX-BMK-AUDIT-1 2026-07-14: LatAm/EastEurope/China/India pairs added.)
BMK_GEO_BENIGN_PAIRS: frozenset = frozenset({
    frozenset({"North America",        "Global"}),
    frozenset({"Europe",               "Global"}),
    frozenset({"Asia-Pacific",         "Global"}),
    frozenset({"Japan",                "Global"}),
    frozenset({"China",                "Global"}),
    frozenset({"India",                "Global"}),
    frozenset({"Emerging Markets",     "Global"}),
    frozenset({"Latin America",        "Emerging Markets"}),
    frozenset({"Latin America",        "Global"}),
    frozenset({"China",                "Emerging Markets"}),
    frozenset({"India",                "Emerging Markets"}),
    frozenset({"Eastern Europe",       "Emerging Markets"}),
    frozenset({"Eastern Europe",       "Global"}),
    frozenset({"Middle East & Africa", "Emerging Markets"}),
    frozenset({"Japan",                "Asia-Pacific"}),
    frozenset({"China",                "Asia-Pacific"}),
    frozenset({"Asia-Pacific",         "India"}),
})

# Sector pairs that are semantically equivalent or sub/super-set relationships.
# FIX-B3-2 (2026-07-15): Healthcare ↔ Technology divergence is a taxonomy
# disagreement (Morningstar classifies Biotech and Medtech under different
# super-sectors than P1). Energy ↔ Technology divergence covers clean-energy /
# "Energy Transition" funds (Morningstar=Energy & Resources; P1=Technology &
# Innovation). Neither direction is a real classification error.
BMK_SECTOR_BENIGN_PAIRS: frozenset = frozenset({
    frozenset({"Inflation-Linked",           "Inflation"}),
    frozenset({"Real Assets",                "Real Estate"}),
    frozenset({"Healthcare & Life Sciences", "Technology & Innovation"}),
    frozenset({"Energy & Resources",         "Technology & Innovation"}),
    # FIX-B3-CRITICAL-MATERIALS-1 (2026-07-20): "critical materials" funds invest in
    # mining/production of materials (lithium, rare earths, cobalt) essential for
    # technology/batteries → Morningstar classifies as Materials, fund self-describes
    # as Technology & Innovation; both are correct from their perspective.
    frozenset({"Technology & Innovation",    "Materials"}),
})

# Benign cap-tier generalizations: a composite range benchmark (Large/Mid Cap,
# Small/Mid Cap) is compatible with either constituent single tier.
# Used by the B4 check in audit_benchmark_consistency.py (R-1 / 2026-07-26).
BMK_CAP_BENIGN_PAIRS: frozenset = frozenset({
    frozenset({"Large Cap", "Large/Mid Cap"}),
    frozenset({"Mid Cap",   "Large/Mid Cap"}),
    frozenset({"Small Cap", "Small/Mid Cap"}),
    frozenset({"Mid Cap",   "Small/Mid Cap"}),
    # SMID Cap (fund mandate spans small+mid) is routinely benchmarked against
    # a pure Small Cap or Mid Cap index — both directions are industry-normal.
    # "SMID Cap" and "Small/Mid Cap" are also the same concept in two notations.
    frozenset({"SMID Cap",  "Small Cap"}),
    frozenset({"SMID Cap",  "Mid Cap"}),
    frozenset({"SMID Cap",  "Small/Mid Cap"}),
    frozenset({"SMID Cap",  "Large/Mid Cap"}),
})

# Extra sector keywords not covered by THEMATIC_MAP (used by bmk_sector).
_BMK_EXTRA_SECTOR: list[tuple[str, str]] = [
    ("financ",       "Financials"),
    ("bank",         "Financials"),
    ("real estate",  "Real Estate"),
    ("reit",         "Real Estate"),
    ("infrastruc",   "Infrastructure"),
    ("utilities",    "Utilities"),
    ("consumer",     "Consumer"),
    ("industri",     "Industrials"),
    ("material",     "Materials"),
    ("energy",       "Energy"),
    ("telecom",      "Communication"),
    ("communic",     "Communication"),
    ("health",       "Healthcare"),
    ("pharma",       "Healthcare"),
    ("technolog",    "Technology"),
    ("info tech",    "Technology"),
    ("informat",     "Technology"),
]


def bmk_tok_credit(name_l: str) -> Optional[str]:
    """Extract credit-quality pole from lowercased benchmark name.
    Returns a canonical label: 'High Yield' | 'Investment Grade' | 'Corporate' |
    'Government' | 'Inflation-Linked' | 'Aggregate' | etc., or None.
    Single source of truth (R-1): used by SC-H2 and audit_benchmark_consistency.
    """
    import re
    for kw, val in _BMK_CREDIT_TOKENS:
        pattern = kw.replace("$", "\\b")
        if re.search(pattern, name_l):
            return val
    return None


def bmk_tok_duration(name_l: str) -> Optional[str]:
    """Extract duration bucket from lowercased benchmark name.
    Returns 'Ultra-Short' | 'Short' | 'Medium' | 'Long' or None.
    Single source of truth (R-1): used by the audit tool.
    """
    for kw, val in _BMK_DURATION_TOKENS:
        if kw in name_l:
            return val
    return None


def bmk_tok_cap(name_l: str) -> Optional[str]:
    """Extract market-cap tier from lowercased benchmark name, or None."""
    for kw, val in _BMK_CAP_TOKENS:
        if kw in name_l:
            return val
    return None


def bmk_geography(benchmark_name: Optional[str]) -> Optional[str]:
    """Extract EN-canonical geography from a benchmark display name.

    Uses the canonical P1 geography detector + ES→EN normalizer so the result
    is directly comparable to fund_master.Geography values.
    Single source of truth (R-1): used by SC-H3 and audit_benchmark_consistency.
    """
    if not benchmark_name:
        return None
    name_l = benchmark_name.lower()
    raw = detect_geography(name_l)
    if raw is None:
        return None
    return _derive_geography_en(raw, name_l)


def bmk_sector(benchmark_name: Optional[str]) -> Optional[str]:
    """Extract sector/theme label from a benchmark display name.
    Delegates to THEMATIC_MAP (single source of truth per P#11) then falls
    back to _BMK_EXTRA_SECTOR for categories not covered there.
    Used by audit_benchmark_consistency (B3 check).
    """
    if not benchmark_name:
        return None
    name_l = benchmark_name.lower()
    theme = detect_theme(name_l)
    if theme:
        return map_theme_to_sector_focus(theme) or theme
    for kw, sec in _BMK_EXTRA_SECTOR:
        if kw in name_l:
            return sec
    return None


def bmk_severity_nature(
    fund_nature: str,
    ac: Optional[str],
    confidence: str,
) -> str:
    """Return CRITICAL / WARN / INFO / OK severity for a BMK_CONSISTENT check.

    CRITICAL → asset_class is outside both consistent and tolerated sets.
    INFO     → asset_class is in the tolerated set (soft mismatch).
    WARN     → CRITICAL down-weighted because confidence is LOW or MEDIUM.
    OK       → asset_class is in the consistent set.

    Single source of truth (R-1): used by SC-H1 (validate_all_semantic_consistency)
    and read-only by audit_benchmark_consistency._severity_b1 / Exercise-B1.
    """
    if ac is None:
        return "INFO"
    consistent = BMK_CONSISTENT.get(fund_nature, frozenset())
    tolerated  = BMK_TOLERATED.get(fund_nature,  frozenset())
    if ac in consistent:
        return "OK"
    sev = "INFO" if ac in tolerated else "CRITICAL"
    if confidence in ("LOW", "MEDIUM") and sev == "CRITICAL":
        sev = "WARN"
    return sev


# ============================================================
# 18. INTER-18: Benchmark-Composition ↔ Fund_Nature (WARNING)
# ============================================================
# Phase 3 (BL-BENCH-NATURE). Reconciliación CORROBORATIVA contra una fuente
# externa de clase de activo (Morningstar). NO es un validador in-pipeline:
# classify_utils no ve Morningstar al clasificar. Se alimenta desde el driver
# de reconciliación (scripts/diag/inter18_reconciliation.py) que aporta
# ext_asset_class (asset_class Morningstar) y ext_role (benchmark_role).
#
# Detecta los SG1b (índice equity/RF "puro" declarado en KIID sobre un fondo
# que Morningstar categoriza como allocation) — invisibles al benchmark propio
# del fondo porque benchmark y Fund_Nature coinciden internamente; solo la
# fuente externa revela el desajuste.
#
# WARNING-ONLY (Principio: Morningstar es corroborante, NO ground-truth; sus
# buckets de allocation son gruesos). Nunca auto-corrige Fund_Nature.

# Clase de activo externa esperada por naturaleza (None = no restringir).
EXPECTED_EXT_ASSET_CLASS_BY_NATURE: dict = {
    "Renta Variable":          "Equity",
    "Renta Fija Flexible":     "Fixed Income",
    "Renta Fija Corto Plazo":  "Fixed Income",
    "Monetario":               "Rate",
}
# Naturalezas que admiten allocation/Mixed sin warning.
_INTER18_ALLOC_NATURES = frozenset({"Mixtos", "Alternativo"})
# Naturalezas demasiado flexibles para corroborar (no warn).
_INTER18_SKIP_NATURES = frozenset({"Alternativo", "Estructurado", "Restantes"})


def validate_benchmark_nature(
    nature: Optional[str],
    ext_asset_class: Optional[str],
    ext_role: Optional[str] = None,
) -> tuple:
    """INTER-18: corrobora Fund_Nature contra la clase de activo externa.

    Args:
        nature:          Fund_Nature interno.
        ext_asset_class: clase de activo de la fuente externa (Morningstar):
                         'Equity' | 'Fixed Income' | 'Rate' | 'Money Market' | 'Mixed'.
        ext_role:        benchmark_role de la fuente externa; 'hurdle_rate' se omite.

    Returns:
        ('WARNING', msg) | ('OK', None). NUNCA corrige.
    """
    if nature is None or ext_asset_class is None:
        return "OK", None
    if ext_role == "hurdle_rate":
        return "OK", None  # una tasa hurdle no es proxy de clase de activo
    if nature in _INTER18_SKIP_NATURES:
        return "OK", None

    # Caso allocation: la fuente externa dice Mixed pero la naturaleza es mono-activo.
    if ext_asset_class == "Mixed" and nature not in _INTER18_ALLOC_NATURES:
        return "WARNING", (
            f"WARNING: fuente externa clasifica como allocation/Mixed pero "
            f"Fund_Nature='{nature}' es mono-activo "
            f"(posible sleeve equity/RF declarado en KIID). Revisar."
        )

    # Contradicción dura equity↔renta fija (no se penaliza el límite RF↔Rate,
    # que es granularidad ultra-corto plazo, ni Money Market).
    expected = EXPECTED_EXT_ASSET_CLASS_BY_NATURE.get(nature)
    if (expected and ext_asset_class in ("Equity", "Fixed Income")
            and ext_asset_class != expected
            and expected in ("Equity", "Fixed Income")):
        return "WARNING", (
            f"WARNING: fuente externa clase '{ext_asset_class}' contradice "
            f"Fund_Nature='{nature}' (esperado '{expected}'). Revisar."
        )

    return "OK", None


# ============================================================
# 11. validate_all_semantic_consistency() — FUNCIÓN MAESTRA
# ============================================================

def validate_all_semantic_consistency(
    fund_record: dict,
    ext_asset_class: Optional[str] = None,
    ext_role: Optional[str] = None,
    ext_benchmark_name: Optional[str] = None,   # SC-H2/H3: benchmark display name
    ext_confidence: Optional[str] = None,        # SC-H: HIGH / MEDIUM / LOW
) -> dict:
    """Valida TODAS las reglas de consistencia semántica.

    PURA — no emite logging. El logging es responsabilidad exclusiva del wrapper
    apply_semantic_validation. Ver SPRINT_A1.b sección 5.2 (logging duplicado).

    Args:
        fund_record: dict con todos los atributos del fondo.

    Returns:
        {
            'is_valid': bool,
            'critical_errors': list[dict],
            'warnings': list[dict],
            'corrected_record': dict,
        }
    """
    critical_errors: list = []
    warnings: list = []
    cr = fund_record.copy()

    # PURA: isin no se usa para logging interno (ver docstring)

    # --- CRÍTICAS (auto-corrección) ---

    # INTER-1: Strategy ↔ Replication_Method
    val, msg = validate_strategy_replication(
        cr.get("Strategy"), cr.get("Replication_Method")
    )
    if msg:
        critical_errors.append({"rule": "Strategy-Replication", "message": msg})
        cr["Replication_Method"] = val

    # INTER-2: Accumulation ↔ Distribution (BL-32: nueva firma 3-tupla)
    val_ap, val_df, msg = validate_accumulation_distribution(
        cr.get("Accumulation_Policy"), cr.get("Distribution_Frequency")
    )
    if msg:
        if msg.startswith("WARNING"):
            # FIX-SEM-WARN-INFO (2026-07-15): "DISTRIBUTION sin Distribution_Frequency"
            # is an unresolved inconsistency → WARN (critical_errors bucket).
            critical_errors.append({"rule": "Accumulation-Distribution", "message": msg})
        else:
            # Successful auto-correction ("Eliminado..."/"Inferido...") → INFO.
            warnings.append({"rule": "Accumulation-Distribution", "message": msg})
            cr["Accumulation_Policy"] = val_ap
            cr["Distribution_Frequency"] = val_df

    # INTER-3: Profile ↔ SRRI
    val, msg = validate_profile_srri(
        cr.get("Profile"), cr.get("SRRI")
    )
    if msg:
        if "WARNING" in msg:
            warnings.append({"rule": "Profile-SRRI", "message": msg})
        else:
            critical_errors.append({"rule": "Profile-SRRI", "message": msg})
            cr["Profile"] = val

    # INTER-4 (Nature → Type): RETIRADO en v20 (§8-bis Q3). Type se repropuso a
    # Vehicle_Structure (eje ortogonal); la restricción ya no aplica.

    # INTER-5: Nature → Family (con auto-corrección P07)
    ok, msg = validate_nature_family_coherence(
        cr.get("Fund_Nature"), cr.get("Family")
    )
    if not ok:
        critical_errors.append({"rule": "Nature-Family", "message": msg})
        # P07: Auto-corrección — asignar Family por defecto de la Nature
        _default_family = _DEFAULT_FAMILY_BY_NATURE.get(cr.get("Fund_Nature"))
        if _default_family:
            cr["Family"] = _default_family
            critical_errors[-1]["message"] += f" -> corregido a '{_default_family}'"

    # INTER-6: Universe → Sector/Geography
    ok, issues = validate_universe_completeness(
        cr.get("Investment_Universe"),
        cr.get("Sector_Focus"),
        cr.get("Geography"),
    )
    if not ok:
        for issue in issues:
            warnings.append({"rule": "Universe-Completeness", "message": issue})

    # --- WARNINGS (no auto-corrección) ---

    # INTER-7
    status, msg = validate_leverage_profile(
        cr.get("Profile"), cr.get("Leverage_Used")
    )
    if status == "WARNING":
        warnings.append({"rule": "Leverage-Profile", "message": msg})

    # INTER-8
    status, msg = validate_esg_sfdr(
        cr.get("Is_ESG"), cr.get("Sfdr_Article")
    )
    if status == "WARNING":
        warnings.append({"rule": "ESG-SFDR", "message": msg})

    # INTER-9 (SC-B6): Theme → Sector_Focus coherence.
    # Upgraded to auto-correct: si el mapa canónico define un Sector_Focus esperado
    # para el Theme actual y el valor en el registro difiere, se corrige Sector_Focus.
    # Solo aplica cuando Investment_Focus='Sector' (los temas Thematic-Only son
    # gestionados por INTER-15 más adelante).
    status, msg = validate_theme_sector_coherence(
        cr.get("Theme"), cr.get("Sector_Focus")
    )
    if status == "WARNING":
        _expected_sf = THEME_SECTOR_MAPPING.get(cr.get("Theme"))
        if _expected_sf is not None and cr.get("Sector_Focus") is not None:
            cr["Sector_Focus"] = _expected_sf
            critical_errors.append({
                "rule": "Theme-Sector",
                "message": msg + f" → Sector_Focus corregido a '{_expected_sf}' (SC-B6)",
            })
        else:
            warnings.append({"rule": "Theme-Sector", "message": msg})

    # ----------------------------------------------------------------
    # BL-30: INTER-11 — Investment_Focus vs Sector_Focus (auto-corrección)
    # Si Sector_Focus está poblado, Investment_Focus no puede ser 'Broad'.
    # Root cause: ambas columnas asignadas en rutas independientes sin cruce.
    # Acción: si Sector_Focus presente → Investment_Focus='Sector'.
    # ----------------------------------------------------------------
    _sf = cr.get("Sector_Focus")
    _if = cr.get("Investment_Focus")
    if _sf is not None and _if == "Broad":
        cr["Investment_Focus"] = "Sector"
        critical_errors.append({
            "rule": "InvestmentFocus-SectorFocus",
            "message": (
                f"Investment_Focus corregido 'Broad'→'Sector' "
                f"porque Sector_Focus='{_sf}' está poblado"
            ),
        })

    # ----------------------------------------------------------------
    # BL-31: INTER-12 — Currency_Hedged vs Hedging_Policy (auto-corrección)
    # Si ambos están poblados y son contradictorios, Hedging_Policy prevalece
    # (extraída del texto KIID, más fiable que el nombre).
    # ----------------------------------------------------------------
    _ch = cr.get("Currency_Hedged")
    _hp = cr.get("Hedging_Policy")
    if _ch is not None and _hp is not None:
        _hp_as_ch = "Hedged" if _hp == "HEDGED" else "Unhedged"
        if _ch != _hp_as_ch:
            cr["Currency_Hedged"] = _hp_as_ch
            critical_errors.append({
                "rule": "CurrencyHedged-HedgingPolicy",
                "message": (
                    f"Currency_Hedged corregido '{_ch}'→'{_hp_as_ch}' "
                    f"por coherencia con Hedging_Policy='{_hp}'"
                ),
            })

    # ----------------------------------------------------------------
    # BL-33: INTER-13 — Investment_Universe NULL por naturaleza (fallback)
    # Para naturalezas con universo inequívoco cuando no hay señal de nombre/KIID.
    # Solo se aplica si Investment_Universe es NULL después de todas las capas.
    # ----------------------------------------------------------------
    _DEFAULT_UNIVERSE_BY_NATURE: dict = {
        # v20 (§2A.1 #5): 'Liquidity' eliminado. Monetario/RF Corto sin señal
        # geográfica → 'Global' (liquidez indiferenciada). La clase MMF vive en
        # MMF_Structure y la duración en Duration_Profile.
        "Monetario":              "Global",
        "Renta Fija Corto Plazo": "Global",
    }
    if cr.get("Investment_Universe") is None:
        _nature = cr.get("Fund_Nature")
        _default_universe = _DEFAULT_UNIVERSE_BY_NATURE.get(_nature)
        if _default_universe:
            cr["Investment_Universe"] = _default_universe
            critical_errors.append({
                "rule": "InvestmentUniverse-NatureFallback",
                "message": (
                    f"Investment_Universe='{_default_universe}' inferido "
                    f"por defecto desde Fund_Nature='{_nature}'"
                ),
            })
        # Para RV, Mixtos y RF Flexible sin señal → 'Global' como fallback
        elif _nature in ("Renta Variable", "Mixtos", "Renta Fija Flexible",
                         "Alternativo"):
            # Solo aplicar si Geography es NULL también (sin info de ningún tipo)
            if cr.get("Geography") is None and cr.get("Sector_Focus") is None:
                # BL-LANG-EN-FIX (2026-05-18): antes de asumir Global, intentar
                # inferir desde el nombre del fondo (cubre OCR con puntos como
                # "EMERG.MARKETS" que detect_geography() no captura por el punto).
                #
                # FIX-GEO-7 (2026-07-12): NOTA DE ALCANZABILIDAD — esta sub-rama
                # lee cr.get("Fund_Name"), que los bloques clasificadores NUNCA
                # incluyen en su dict de resultado (solo lo recibe como argumento
                # separado de classify_fund, no se copia al dict). Por tanto
                # _fname_inter13 es siempre "" a nivel de bloque → la rama
                # `if any(sig ...)` nunca se cumple. A nivel de pipeline.py,
                # BL-33 tampoco se alcanza porque Investment_Universe ya es no-NULL
                # (el bloque lo habrá fijado en Global). Esta sub-rama es código
                # efectivamente muerto; se conserva por si un futuro caller pasa
                # Fund_Name en el dict. FIX-GEO-7 resuelve el caso emergentes
                # directamente en detect_geography() (ver arriba), que sí recibe
                # el nombre real del fondo vía el caller del bloque.
                _fname_inter13 = (cr.get("Fund_Name") or "").lower()
                _emerg_signals = [
                    "emerg", "emerging", "emergentes", "emergent",
                    "frontier", "em mkt", "em mark", "em eq",
                ]
                if any(sig in _fname_inter13 for sig in _emerg_signals):
                    # v20: 'Emergentes' no es geografía espacial → Global espacial
                    # + eje desarrollo Emerging. Universe Global (coherente con
                    # Geography=Global por INTER-10).
                    cr["Investment_Universe"] = "Global"
                    cr["Geography"] = "Global"
                    cr["Development_Status"] = "Emerging"
                    warnings.append({
                        "rule": "InvestmentUniverse-NatureFallback",
                        "message": (
                            f"Geography='Global' / Development_Status='Emerging' "
                            f"inferidos desde nombre del fondo (señal emergentes)"
                        ),
                    })
                else:
                    cr["Investment_Universe"] = "Global"
                    # FIX-INTER13-MSG (2026-07-05): este chequeo corre a nivel de
                    # bloque (sin acceso a BD/EffectiveReader por diseño, R-7:
                    # los bloques deben ser testables sin pipeline.py/core.io) y
                    # solo ve el `record` de ESTE ciclo. Para fondos CACHED sin
                    # KIID nuevo, "sin Geography ni Sector_Focus" no significa
                    # ausencia permanente -- significa "sin señal fresca en este
                    # ciclo"; pipeline.py reconcilia Investment_Universe contra el
                    # valor efectivo (BD si no hay fresco) vía BL-52 antes de
                    # persistir. El mensaje anterior sugería una ausencia
                    # definitiva de dato incluso cuando el valor final en BD es
                    # correcto -- confirmado 0 desacuerdos finales en auditoría de
                    # 135 fondos (ver memoria FIX-GEO-6 / INTER-13 2026-07-05).
                    # FIX-GEO-7 (2026-07-12): corregido mensaje falso que afirmaba que
                    # "pipeline.py reconciliará con el valor efectivo si difiere". Eso
                    # es incorrecto: BL-50 (pipeline.py:~1627) solo actúa cuando
                    # Investment_Universe IS NULL; como esta rama ya lo fijó en 'Global'
                    # (truthy), BL-50 se salta y 'Global' es el valor definitivo.
                    warnings.append({
                        "rule": "InvestmentUniverse-NatureFallback",
                        "message": (
                            f"Investment_Universe='Global' asignado como centinela "
                            f"P#10 (sin señal positiva de Geography ni Sector_Focus "
                            f"en nombre ni KIID para Nature='{_nature}') -- valor "
                            f"definitivo; BL-50 de pipeline.py no reconcilia porque "
                            f"ya es no-NULL"
                        ),
                    })


    # ----------------------------------------------------------------
    # INTER-18: Benchmark-Composition ↔ Fund_Nature (WARNING, corroborativa)
    # Solo dispara si el driver de reconciliación aporta clase externa
    # (Morningstar). En el flujo in-pipeline ext_asset_class es None → no-op.
    # ----------------------------------------------------------------
    status, msg = validate_benchmark_nature(
        cr.get("Fund_Nature"), ext_asset_class, ext_role
    )
    if status == "WARNING":
        warnings.append({"rule": "Benchmark-Nature", "message": msg})

    # ----------------------------------------------------------------
    # SC-H2 (2026-07-15): Credit_Quality ↔ benchmark credit pole (WARN/INFO)
    # Fires only for FI natures where Credit_Quality is semantically meaningful
    # and the benchmark name signals a clear credit pole.
    # HIGH confidence benchmark + clear IG↔HY opposition → critical_error (→ WARN DQ).
    # LOW/MEDIUM confidence → warning (→ INFO DQ).
    # Never auto-corrects Credit_Quality; triggers the FORCE_REFRESH remediation
    # path in the next cycle.
    # EM-sovereign suppression: a "Government" token on an EM sovereign fund
    # that is correctly classified HY is not a contradiction (FIX-B6-AUDIT).
    # ----------------------------------------------------------------
    _ext_bmk_l = ext_benchmark_name.lower() if ext_benchmark_name else None
    if _ext_bmk_l and ext_role != "hurdle_rate":
        _bmk_credit = bmk_tok_credit(_ext_bmk_l)
        _fm_credit  = cr.get("Credit_Quality")
        _is_fi_h2   = cr.get("Fund_Nature") in (
            "Renta Fija Flexible", "Renta Fija Corto Plazo", "Monetario"
        )
        if _is_fi_h2 and _bmk_credit and _fm_credit:
            _IG_LABELS_H2 = {"Investment Grade", "High Grade", "IG"}
            _HY_LABELS_H2 = {"High Yield", "Speculative", "HY"}
            _bmk_is_hy_h2 = _bmk_credit == "High Yield"
            _bmk_is_ig_h2 = _bmk_credit in (
                "Investment Grade", "Corporate", "Aggregate",
                "AAA", "Government", "Inflation-Linked", "Securitised"
            )
            _fm_is_hy_h2  = _fm_credit in _HY_LABELS_H2
            _fm_is_ig_h2  = _fm_credit in _IG_LABELS_H2
            _h2_conflict  = (_bmk_is_hy_h2 and _fm_is_ig_h2) or (
                             _bmk_is_ig_h2 and _fm_is_hy_h2)
            if _h2_conflict:
                # Suppress EM-sovereign false positive (FIX-B6-AUDIT / FIX-B6-AUDIT-2).
                # Many EM sovereign bond indices are correctly rated "High Yield" even
                # though "Government" appears in the benchmark name. Match both
                # "sovereign" (iShares EM Sovereign Bond) and "govt"/"gov" (Morningstar
                # EM Govt Bond, JPM GBI-EM) naming conventions.
                _is_em_sov_h2 = (
                    _bmk_credit == "Government"
                    and any(tok in _ext_bmk_l for tok in ("sovereign", "em govt", "em gov"))
                    and any(em in _ext_bmk_l for em in ("em ", "emerg", "mercados em"))
                )
                # FIX-B6-TBILL-1 (2026-07-20): Government cash/rate instruments
                # (T-Bills, overnight rates) are cash-hurdle performance targets used by
                # credit funds to express a "return X% above cash" objective.  They carry
                # no credit-quality signal for the portfolio → SC-H2 does not apply.
                # Confirmed: NB SHORT DURATION EM DEBT KIID reads
                # "rentabilidad 3% superior a la del efectivo" and uses ICE BofA 3M
                # US Treasury Bill as that cash reference.
                _is_govt_cash_bmk_h2 = (
                    _bmk_credit == "Government"
                    and any(tok in _ext_bmk_l for tok in (
                        "treasury bill", "t-bill", "tbill",
                        "3-month", "3 month", "3m us", "3m eur",
                        "overnight", "sofr", "estr", "euribor", "libor",
                        "cash", "liquidity", "money market",
                    ))
                )
                if not _is_em_sov_h2 and not _is_govt_cash_bmk_h2:
                    _h2_msg = (
                        f"SC-H2: Credit_Quality='{_fm_credit}' contradicts "
                        f"benchmark credit pole '{_bmk_credit}' "
                        f"(benchmark: '{ext_benchmark_name}'). "
                        f"Remediation: FORCE_REFRESH then re-classify."
                    )
                    # FIX-B6-MS-CAT-1 (2026-07-20): Morningstar category benchmarks
                    # reflect risk/return-based performance attribution, not portfolio
                    # holdings. A conservative IG low-vol fund can legitimately appear
                    # in a Morningstar HY category → downgrade to INFO (warnings) so
                    # the signal survives but does not trigger FORCE_REFRESH.
                    _ext_is_ms_cat_hy_vs_ig = (
                        "morningstar" in _ext_bmk_l
                        and _bmk_is_hy_h2 and _fm_is_ig_h2
                    )
                    if ext_confidence in ("LOW", "MEDIUM") or _ext_is_ms_cat_hy_vs_ig:
                        warnings.append({"rule": "Benchmark-Credit-SC-H2",
                                         "message": _h2_msg})
                    else:
                        critical_errors.append({"rule": "Benchmark-Credit-SC-H2",
                                                "message": _h2_msg})

    # ----------------------------------------------------------------
    # SC-H3 (2026-07-15): Geography ↔ benchmark geography (INFO/WARN)
    # Fires when both fund_master.Geography and the benchmark-derived geography
    # are known, non-global, and conflict outside the benign-pair list.
    # Always emits into warnings (INFO DQ level) — geography conflicts are
    # common from Morningstar using a broader regional proxy for sub-regional
    # funds; benign pairs suppress the most frequent false positives.
    # ----------------------------------------------------------------
    if _ext_bmk_l and ext_role != "hurdle_rate":
        _bmk_geo_h3 = bmk_geography(ext_benchmark_name)
        _fm_geo_h3  = cr.get("Geography")
        _GEO_GLOBAL_SENTINELS = {"Global", "Mercados Emergentes", "Global EM", None}
        if (_bmk_geo_h3 and _fm_geo_h3
                and _fm_geo_h3 not in _GEO_GLOBAL_SENTINELS
                and _bmk_geo_h3 not in _GEO_GLOBAL_SENTINELS
                and _fm_geo_h3 != _bmk_geo_h3
                and frozenset({_fm_geo_h3, _bmk_geo_h3}) not in BMK_GEO_BENIGN_PAIRS):
            warnings.append({
                "rule": "Benchmark-Geography-SC-H3",
                "message": (
                    f"SC-H3: Geography='{_fm_geo_h3}' conflicts with benchmark "
                    f"geography '{_bmk_geo_h3}' "
                    f"(benchmark: '{ext_benchmark_name}'). "
                    f"Review: possible mis-detected geography."
                ),
            })

    # ----------------------------------------------------------------
    # INTER-14 (2026-07-11): Market_Cap_Focus solo aplica a Renta Variable.
    # Para naturalezas no-equity (RF Flexible, Monetario, Alternativo, etc.)
    # Market_Cap_Focus es semánticamente incoherente y debe anularse. Detecta
    # y corrige valores espurios (p.ej. 'All Cap' stale en fondos RF Flexible
    # heredado de un ciclo donde la función no guardaba Fund_Nature correcta).
    # ----------------------------------------------------------------
    _mcf_14 = cr.get("Market_Cap_Focus")
    _nature_14 = cr.get("Fund_Nature")
    _MCF_NON_EQUITY_NATURES = {
        "Monetario", "Renta Fija Corto Plazo", "Renta Fija Flexible",
        "Alternativo", "Restantes", "Estructurado",
    }
    if _mcf_14 is not None and _nature_14 in _MCF_NON_EQUITY_NATURES:
        cr["Market_Cap_Focus"] = None
        warnings.append({
            "rule": "MarketCapFocus-Nature",
            "message": (
                f"Market_Cap_Focus='{_mcf_14}' → NULL "
                f"(no aplica para Fund_Nature='{_nature_14}')"
            ),
        })

    # ----------------------------------------------------------------
    # INTER-17 (2026-07-11): SC-D1/D2 — Credit_Quality y Duration_Profile
    # solo aplican a fondos de renta fija. Para natures puramente equity
    # (Renta Variable) o productos estructurados/alternativos, cualquier valor
    # distinto de 'Not Applicable' es una contaminación del clasificador
    # (p.ej. KIID erróneo que aportó atributos de un sub-fondo de RF).
    # Mixtos EXCLUIDOS: pueden tener componente FI, 'Not Applicable' ya es correcto.
    # ----------------------------------------------------------------
    _CQ_DP_EQUITY_NATURES = {"Renta Variable", "Alternativo", "Estructurado"}
    _nature_17 = cr.get("Fund_Nature")
    if _nature_17 in _CQ_DP_EQUITY_NATURES:
        _cq_17 = cr.get("Credit_Quality")
        if _cq_17 is not None and _cq_17 != "Not Applicable":
            cr["Credit_Quality"] = "Not Applicable"
            warnings.append({
                "rule": "CreditQuality-Nature",
                "message": (
                    f"Credit_Quality='{_cq_17}' → 'Not Applicable' "
                    f"(atributo RF no aplica a Fund_Nature='{_nature_17}') (SC-D1)"
                ),
            })
        _dp_17 = cr.get("Duration_Profile")
        if _dp_17 is not None and _dp_17 != "Not Applicable":
            cr["Duration_Profile"] = "Not Applicable"
            warnings.append({
                "rule": "DurationProfile-Nature",
                "message": (
                    f"Duration_Profile='{_dp_17}' → 'Not Applicable' "
                    f"(atributo RF no aplica a Fund_Nature='{_nature_17}') (SC-D2)"
                ),
            })

    # ----------------------------------------------------------------
    # MIG-1 (2026-07-11): Distribution_Frequency='BIANNUAL' → 'Semi-Annual'
    # Valor legado pre-MODIFY. BIANNUAL = dos veces al año = Semi-Annual.
    # Mapeo inequívoco, auto-corrección segura.
    # ----------------------------------------------------------------
    if cr.get("Distribution_Frequency") == "BIANNUAL":
        cr["Distribution_Frequency"] = "Semi-Annual"
        warnings.append({
            "rule": "Allowed-Values:Distribution_Frequency",
            "message": "Distribution_Frequency='BIANNUAL' → 'Semi-Annual' (valor legado; auto-migrado)",
        })

    # ----------------------------------------------------------------
    # MIG-2 (2026-07-11): Hedging_Policy='PARTIAL' → 'Partially Hedged'
    # Valor legado pre-MODIFY (nombre abreviado vs nombre completo v20).
    # ----------------------------------------------------------------
    if cr.get("Hedging_Policy") == "PARTIAL":
        cr["Hedging_Policy"] = "Partially Hedged"
        warnings.append({
            "rule": "Allowed-Values:Hedging_Policy",
            "message": "Hedging_Policy='PARTIAL' → 'Partially Hedged' (valor legado; auto-migrado)",
        })

    # ----------------------------------------------------------------
    # MIG-3 (2026-07-11): Derivatives_Usage legado (YES/NO/LIMITED) → v20
    # MODIFY #12 cambió la semántica de binario (¿usa derivados?) a propósito
    # (¿para qué usa derivados?). Mapeos seguros:
    #   'NO'      → 'None'          (no usa derivados; inequívoco)
    #   'LIMITED' → 'Hedging Only'  (uso limitado = solo cobertura; muy probable)
    #   'YES'     → WARN            (ambiguo: puede ser Investment, Both o Hedging Only)
    # ----------------------------------------------------------------
    _DU_MIGRATION: dict = {"NO": "None", "LIMITED": "Hedging Only"}
    _du_mig = cr.get("Derivatives_Usage")
    if _du_mig in _DU_MIGRATION:
        _du_new = _DU_MIGRATION[_du_mig]
        cr["Derivatives_Usage"] = _du_new
        warnings.append({
            "rule": "Allowed-Values:Derivatives_Usage",
            "message": (
                f"Derivatives_Usage='{_du_mig}' → '{_du_new}' "
                f"(valor legado MODIFY #12; auto-migrado)"
            ),
        })
    elif _du_mig == "YES":
        # Heurística por Fund_Nature: mapeo best-effort del legado 'YES' al
        # propósito más probable. No es inequívoco — confirmar en prospecto.
        _DU_NATURE_HEURISTIC: dict[str, str] = {
            "Monetario":              "Hedging Only",   # MMF: derivados sólo para cobertura
            "Renta Fija Corto Plazo": "Hedging Only",   # ídem RF corto
            "Renta Variable":         "Investment",     # RV: derivados para gestión de cartera
            "Renta Fija Flexible":    "Both",           # RF flexible: cobertura + inversión
            "Mixtos":                 "Both",           # multi-activo: ambos propósitos
            "Alternativo":            "Investment",     # alt: derivados como estrategia core
            "Estructurado":           "Both",           # estructurados: derivados esenciales
        }
        _du_heuristic = _DU_NATURE_HEURISTIC.get(cr.get("Fund_Nature"))
        if _du_heuristic:
            cr["Derivatives_Usage"] = _du_heuristic
            warnings.append({
                "rule": "Allowed-Values:Derivatives_Usage",
                "message": (
                    f"Derivatives_Usage='YES' → '{_du_heuristic}' "
                    f"(heurística Fund_Nature='{cr.get('Fund_Nature')}'; "
                    f"confirmar en prospecto)"
                ),
            })
        else:
            warnings.append({
                "rule": "Allowed-Values:Derivatives_Usage",
                "message": (
                    "Derivatives_Usage='YES' es valor legado MODIFY #12 — "
                    "Fund_Nature no permite inferencia segura; "
                    "revisar: puede ser 'Investment', 'Both' o 'Hedging Only'"
                ),
            })

    # ----------------------------------------------------------------
    # MIG-4 (2026-07-11): Liquidity_Profile legado (T1/T5) → v20
    # Valores de frecuencia de dealing en formato OLD:
    #   'T1' → 'Daily'   (liquidez diaria, T+1 settlement; inequívoco)
    #   'T5' → WARN      (T+5 puede significar 'Weekly' o 'Bi-Weekly';
    #                      sin confirmación documental, no auto-corregir)
    # ----------------------------------------------------------------
    _lp_mig = cr.get("Liquidity_Profile")
    if _lp_mig == "T1":
        cr["Liquidity_Profile"] = "Daily"
        warnings.append({
            "rule": "Allowed-Values:Liquidity_Profile",
            "message": "Liquidity_Profile='T1' → 'Daily' (valor legado; auto-migrado)",
        })
    elif _lp_mig == "T5":
        warnings.append({
            "rule": "Allowed-Values:Liquidity_Profile",
            "message": (
                "Liquidity_Profile='T5' es valor legado — "
                "requiere revisión: posiblemente 'Weekly' o 'Bi-Weekly'"
            ),
        })

    # ----------------------------------------------------------------
    # MIG-5 (2026-07-12): Investment_Universe='Liquidity' → 'Global'
    # 'Liquidity' fue valor pre-v20 (schema v19) para fondos monetarios.
    # En v20 (§2A.1 #5) fue eliminado; la liquidez se codifica en
    # MMF_Structure / Liquidity_Profile. Para cualquier naturaleza,
    # el universo geográfico correcto es 'Global' como mínimo.
    # ----------------------------------------------------------------
    if cr.get("Investment_Universe") == "Liquidity":
        cr["Investment_Universe"] = "Global"
        warnings.append({
            "rule": "Allowed-Values:Investment_Universe",
            "message": (
                "Investment_Universe='Liquidity' migrado→'Global' "
                "(valor pre-v20; eliminado en schema v20 §2A.1 #5)"
            ),
        })

    # ----------------------------------------------------------------
    # INTER-18 (2026-07-11): SC-E1/E2 — MMF_Structure ↔ Fund_Nature
    # MMF_Structure codifica la estructura regulatoria MMFR (EU 2017/1131):
    # CNAV/LVNAV/VNAV/Standard MMF. Solo aplica a fondos Monetarios; para
    # cualquier otra naturaleza el valor correcto es 'Not Applicable'.
    # SC-E1: no-Monetario con MMF_Structure ≠ 'Not Applicable' → auto-correct.
    # SC-E2: Monetario con MMF_Structure = 'Not Applicable' → WARN (falta
    #         clasificación regulatoria). MMF_Structure=NULL no genera WARN porque
    #         puede ser un fondo recién incorporado sin KIID procesado.
    # ----------------------------------------------------------------
    _mmf_18 = cr.get("MMF_Structure")
    _nature_18 = cr.get("Fund_Nature")
    if _nature_18 is not None and _nature_18 != "Monetario":
        if _mmf_18 is not None and _mmf_18 != "Not Applicable":
            cr["MMF_Structure"] = "Not Applicable"
            warnings.append({
                "rule": "MMFStructure-Nature",
                "message": (
                    f"MMF_Structure='{_mmf_18}' → 'Not Applicable' "
                    f"(estructura MMFR no aplica a Fund_Nature='{_nature_18}') (SC-E1)"
                ),
            })
    elif _nature_18 == "Monetario" and _mmf_18 == "Not Applicable":
        warnings.append({
            "rule": "MMFStructure-Nature",
            "message": (
                "MMF_Structure='Not Applicable' en fondo Monetario — "
                "debería tener estructura MMFR explícita (CNAV/LVNAV/VNAV/Standard MMF) (SC-E2)"
            ),
        })

    # ----------------------------------------------------------------
    # INTER-19 (2026-07-11): SC-E3 — Style_Profile stale en Restantes
    # Restantes es categoría residual: no tiene perfil de estilo definido.
    # Cualquier Style_Profile != NULL/'Not Applicable' es un artefacto COALESCE
    # de una clasificación anterior (p.ej. fondo que era Renta Variable y se
    # reclasificó como Restantes conservando el Style_Profile previo).
    # → WARN sin auto-corrección (requiere confirmar que la reclasificación es
    #    permanente antes de anular un atributo que podría ser correcto si el fondo
    #    vuelve a su categoría original en el siguiente ciclo).
    # ----------------------------------------------------------------
    _sp_19 = cr.get("Style_Profile")
    _nature_19 = cr.get("Fund_Nature")
    if (_nature_19 == "Restantes" and _sp_19 is not None
            and _sp_19 != "Not Applicable"):
        warnings.append({
            "rule": "StyleProfile-Nature",
            "message": (
                f"Style_Profile='{_sp_19}' en Fund_Nature='Restantes' — "
                f"posible artefacto COALESCE de clasificación anterior (SC-E3)"
            ),
        })

    # INTER-13-LIQ (2026-07-11, updated FIX-NORDEA-IU-1): Investment_Universe=
    # 'Liquidity' es valor legado (eliminado en schema MODIFY #5, v20). Para
    # fondos Monetarios y RFC, migrar automáticamente a 'Global'.
    # FIX-NORDEA-IU-1: ampliado para incluir RFC (NORDEA fondos).
    if cr.get("Investment_Universe") == "Liquidity":
        if cr.get("Fund_Nature") in ("Monetario", "Renta Fija Corto Plazo"):
            cr["Investment_Universe"] = "Global"
            warnings.append({
                "rule": "Allowed-Values:Investment_Universe",
                "message": (
                    "Investment_Universe='Liquidity' → 'Global' "
                    "(valor legado MODIFY #5; auto-migrado)"
                ),
            })
        # Otras naturalezas: MIG-5 (arriba) ya lo corrige; el ALLOWED_VALUES
        # loop lo detectará con código SEM_ALLOWED_VALUES_INVESTMENT_UNIVERSE.

    # ----------------------------------------------------------------
    # INTER-20 (2026-07-12): SC-G1 — Geography → Investment_Universe
    # El ámbito geográfico declarado en Geography debe reflejarse en
    # Investment_Universe:
    #   Geography ∈ _REGION_GEOGRAPHIES  → IU debe ser 'Regional'
    #   Geography ∈ _COUNTRY_GEOGRAPHIES → IU debe ser 'Country'
    #
    # Se ejecuta DESPUÉS de INTER-13 (BL-33) y MIG-5 para interceptar el
    # IU='Global' que BL-33 asigna por defecto a fondos Monetario/RF Corto
    # independientemente de la Geography conocida. Esta sobreescritura es la
    # raíz de los ~137 fondos con IU='Global' + Geography específica en la BD.
    #
    # Precedencia: Geography gana sobre el fallback de natura de BL-33.
    # Geography es derivada de nombre + texto KIID (señal positiva explícita);
    # BL-33 es una inferencia por defecto que no debe prevalecer cuando hay
    # señal geográfica concreta. La corrección se propaga a fund_master_record
    # via A2-merge en pipeline.py y se persiste via COALESCE en la BD.
    # ----------------------------------------------------------------
    _geo_20 = cr.get("Geography")
    _iu_20  = cr.get("Investment_Universe")
    if _geo_20 is not None and _iu_20 == "Global":
        if _geo_20 in _REGION_GEOGRAPHIES:
            cr["Investment_Universe"] = "Regional"
            # FIX-SEM-WARN-INFO (2026-07-15): successful auto-correction → INFO.
            warnings.append({
                "rule": "Geography-Universe-SC-G1",
                "message": (
                    f"Investment_Universe corregido 'Global'→'Regional' "
                    f"por coherencia con Geography='{_geo_20}' "
                    f"(geografía de región, no global) (SC-G1)"
                ),
            })
        elif _geo_20 in _COUNTRY_GEOGRAPHIES:
            cr["Investment_Universe"] = "Country"
            # FIX-SEM-WARN-INFO (2026-07-15): successful auto-correction → INFO.
            warnings.append({
                "rule": "Geography-Universe-SC-G1",
                "message": (
                    f"Investment_Universe corregido 'Global'→'Country' "
                    f"por coherencia con Geography='{_geo_20}' "
                    f"(geografía de país individual, no global) (SC-G1)"
                ),
            })

    # INTER-10 (BL-52): movido aquí (2026-07-13) para evaluar el IU ya corregido
    # por SC-G1/INTER-20. Antes estaba antes de INTER-20 y emitía WARN redundante
    # "Geography específica 'Japan' con Universe='Global' es inusual" para fondos
    # como PICTET S-T MONEY MKT JPY, cuyo IU='Global' SC-G1 corrige inmediatamente
    # a 'Country'. Moverlo aquí hace que INTER-10 vea el IU definitivo → retorna
    # OK, sin WARN falso. La rama BL-52 (Country→Regional) y la WARN inversa
    # (Geography='Global' + Universe=Country/Regional) permanecen intactas — SC-G1
    # nunca los toca; INTER-10 sigue siendo su fallback de defensa.
    status, msg, corrected_univ = validate_geography_universe(
        cr.get("Geography"), cr.get("Investment_Universe")
    )
    if status == "CORRECTED":
        cr["Investment_Universe"] = corrected_univ
        # FIX-SEM-WARN-INFO (2026-07-15): auto-correction → INFO.
        warnings.append({"rule": "Geography-Universe", "message": msg})
    elif status == "WARNING":
        warnings.append({"rule": "Geography-Universe", "message": msg})

    # ----------------------------------------------------------------
    # INTER-15 (2026-07-11): SC-B1/B2 — Themes macroeconómicos cross-sector
    # implican Investment_Focus='Thematic' y Sector_Focus=NULL.
    # Temas en _THEMATIC_ONLY_THEMES (Megatrends, Inflation) no mapean a ningún
    # sector industria: cualquier asignación de Investment_Focus='Sector' o
    # Sector_Focus poblado es un error del clasificador.
    # Ref: SEMANTIC_MODEL_CLASSIFICATION.md §2.3
    # ----------------------------------------------------------------
    _theme_15 = cr.get("Theme")
    if _theme_15 in _THEMATIC_ONLY_THEMES:
        _if_15 = cr.get("Investment_Focus")
        if _if_15 is not None and _if_15 != "Thematic":
            cr["Investment_Focus"] = "Thematic"
            # FIX-SEM-WARN-INFO (2026-07-15): successful auto-correction → INFO, not WARN.
            warnings.append({
                "rule": "InvestmentFocus-ThematicOnlyTheme",
                "message": (
                    f"Investment_Focus corregido '{_if_15}'→'Thematic': "
                    f"Theme='{_theme_15}' es tema macro cross-sector, "
                    f"no puede ser '{_if_15}' (SC-B1/B2)"
                ),
            })
        _sf_15 = cr.get("Sector_Focus")
        if _sf_15 is not None:
            cr["Sector_Focus"] = None
            # FIX-SEM-WARN-INFO (2026-07-15): successful auto-correction → INFO, not WARN.
            warnings.append({
                "rule": "SectorFocus-ThematicOnlyTheme",
                "message": (
                    f"Sector_Focus='{_sf_15}' → NULL: "
                    f"Theme='{_theme_15}' no tiene Sector_Focus canónico (SC-B2/B5)"
                ),
            })

    # ----------------------------------------------------------------
    # INTER-16 (2026-07-11): SC-B5 — Investment_Focus='Thematic' con
    # Sector_Focus poblado (no cubierto por INTER-15 porque el Theme no es
    # Thematic-Only). Ejemplo: Investment_Focus='Thematic', Theme='Healthcare',
    # Sector_Focus='Healthcare & Life Sciences'. Puede indicar que el fondo
    # debería ser Investment_Focus='Sector' — requiere revisión en el clasificador.
    # → WARN sin auto-corrección (ambiguo).
    # ----------------------------------------------------------------
    _if_16 = cr.get("Investment_Focus")
    _sf_16 = cr.get("Sector_Focus")
    if _if_16 == "Thematic" and _sf_16 is not None:
        warnings.append({
            "rule": "SectorFocus-Thematic",
            "message": (
                f"Investment_Focus='Thematic' con Sector_Focus='{_sf_16}' poblado — "
                f"si el fondo se concentra en un único sector industria debería "
                f"ser Investment_Focus='Sector' (SC-B5; requiere revisión clasificador)"
            ),
        })

    # ----------------------------------------------------------------
    # SC-C1 (2026-07-11): Family='Thematic Equity' → Investment_Focus ∈ Sector/Thematic.
    # Un fondo de renta variable temático no puede tener un mandato Broad.
    # → WARN sin auto-corrección (determinar Sector vs Thematic requiere el clasificador).
    # ----------------------------------------------------------------
    _family_c1 = cr.get("Family")
    _if_c1 = cr.get("Investment_Focus")
    if _family_c1 == "Thematic Equity" and _if_c1 == "Broad":
        warnings.append({
            "rule": "Family-InvestmentFocus",
            "message": (
                f"Family='Thematic Equity' con Investment_Focus='Broad' es inconsistente — "
                f"debe ser 'Sector' (sector industria) o 'Thematic' (tema cross-sector) "
                f"(SC-C1)"
            ),
        })

    # ----------------------------------------------------------------
    # SC-C2 (2026-07-11): Family='Equity Core' → Investment_Focus ≠ 'Thematic'.
    # Un fondo core diversificado no tiene un mandato temático cross-sector.
    # Si tiene Investment_Focus='Thematic' debería reclasificarse como 'Thematic Equity'.
    # → WARN sin auto-corrección.
    # ----------------------------------------------------------------
    _family_c2 = cr.get("Family")
    _if_c2 = cr.get("Investment_Focus")
    if _family_c2 == "Equity Core" and _if_c2 == "Thematic":
        warnings.append({
            "rule": "Family-InvestmentFocus",
            "message": (
                f"Family='Equity Core' con Investment_Focus='Thematic' es inconsistente — "
                f"un fondo core no tiene mandato cross-sector; "
                f"considerar Family='Thematic Equity' (SC-C2)"
            ),
        })

    # ── Normalización de casing (silenciosa) — antes del loop ALLOWED_VALUES ──
    # Los bloques emiten Leverage_Used/Accumulation_Policy/Hedging_Policy en
    # MAYÚSCULAS (convención heredada del extractor KIID). sqlite_writer ya
    # normaliza el casing al escribir en BD, pero validate_all_semantic_consistency
    # corre ANTES de esa escritura. Las INTER rules superiores (INTER-2/INTER-7/
    # INTER-12) ya evaluaron los valores originales correctamente; esta paso sólo
    # limpia corrected_record para que el loop ALLOWED_VALUES no emita 6 700+
    # INFO espurios por ciclo. No se genera warning: el dato en BD ya es correcto.
    _CASING_NORM: dict[str, dict[str, str]] = {
        "Leverage_Used":       {"YES": "Yes", "NO": "No", "LIMITED": "Limited"},
        "Accumulation_Policy": {"ACCUMULATION": "Accumulation", "DISTRIBUTION": "Distribution"},
        "Hedging_Policy":      {"HEDGED": "Hedged", "UNHEDGED": "Unhedged"},
    }
    for _cn_col, _cn_map in _CASING_NORM.items():
        _cn_val = cr.get(_cn_col)
        if _cn_val in _cn_map:
            cr[_cn_col] = _cn_map[_cn_val]

    # Comprobación de valores fuera de catálogo. IMPORTANTE: iterar sobre cr
    # (corrected_record) en lugar de fund_record para que las correcciones de
    # los INTER rules anteriores sean visibles aquí y no generen falsos warnings.
    # La regla usa f"Allowed-Values:{col}" para que cada columna produzca un
    # check_code único (SEM_ALLOWED_VALUES_<COL>) — necesario porque
    # fund_data_quality_issues tiene UNIQUE constraint en (ISIN, check_code)
    # y una misma regla genérica "Allowed-Values" duplicada causa IntegrityError.
    #
    # MIG-3/MIG-4 emiten "Allowed-Values:{col}" para valores ambiguos (p.ej.
    # Derivatives_Usage='YES', Liquidity_Profile='T5') SIN corregir cr[col].
    # Para evitar duplicar el check_code cuando el loop inferiría el mismo aviso,
    # se excluyen las columnas ya flaggeadas por cualquier regla "Allowed-Values:".
    _av_already_flagged = {
        item["rule"][len("Allowed-Values:"):]
        for item in warnings + critical_errors
        if item.get("rule", "").startswith("Allowed-Values:")
    }
    for col, value in cr.items():
        if col in _av_already_flagged:
            continue
        if col in ALLOWED_VALUES_BY_COLUMN and value is not None:
            if value not in ALLOWED_VALUES_BY_COLUMN[col]:
                warnings.append({
                    "rule": f"Allowed-Values:{col}",
                    "message": f"{col}='{value}' no está en valores permitidos",
                })

    # FUNCIÓN PURA — sin logging interno (Sprint A.1.b sección 5.2).
    # El logging es responsabilidad exclusiva del wrapper apply_semantic_validation.
    # Eliminar las líneas de logger.info/warning que causaban duplicación ([???] + [NOMBRE]).

    return {
        "is_valid": len(critical_errors) == 0,
        "critical_errors": critical_errors,
        "warnings": warnings,
        "corrected_record": cr,
    }


# ============================================================
# A3 (2026-07-11): semantic_validation_to_dq_tuples — helper DQ persistence
# ============================================================

def semantic_validation_to_dq_tuples(val_result: dict) -> list:
    """Convierte el resultado de validate_all_semantic_consistency en una lista
    de 4-tuplas (check_code, dq_level, log_status, message) compatibles con
    la interfaz _dq_issues de pipeline.py (flush vía _finalize_data_quality_issues).

    Hace posible que TODO fondo (no solo los clasificados por restantes.py) tenga
    sus inconsistencias semánticas persistidas en fund_data_quality_issues cada ciclo.

    Formato de salida: ("SEM_RULE", "WARN"|"INFO", "WARN"|"INFO", msg)
      - critical_errors → dq_level="WARN", log_status="WARN"
      - warnings        → dq_level="INFO", log_status="INFO"

    Args:
        val_result: dict devuelto por validate_all_semantic_consistency.

    Returns:
        list de 4-tuplas; vacía si no hay errores ni warnings.
    """
    def _rule_to_code(rule: str) -> str:
        raw = rule.upper().replace("-", "_").replace(":", "_").replace(" ", "_")
        return ("SEM_" + raw)[:50]

    tuples: list = []
    for item in val_result.get("critical_errors", []):
        tuples.append((
            _rule_to_code(item.get("rule", "UNKNOWN")),
            "WARN",
            "WARN",      # FIX-LOG-WARN-NORM (2026-07-19): normalized from "WARNING"
            item.get("message", ""),
        ))
    for item in val_result.get("warnings", []):
        tuples.append((
            _rule_to_code(item.get("rule", "UNKNOWN")),
            "INFO",
            "INFO",
            item.get("message", ""),
        ))
    return tuples


# =====================================================
# BL-56: Normalización post-characterize (Principio #2 DRY)
# =====================================================

def apply_post_characterize_normalization(classification: dict) -> dict:
    """
    Aplica TODAS las normalizaciones lingüísticas centralizadas
    post-characterize. Punto único de invocación desde pipeline (BL-56).

    Cumple con Principio #2 (DRY): un único punto donde se ejecutan
    todas las traducciones a idioma objetivo (Principio #8).
    Solo actúa sobre campos no-None; no sobreescribe NULL deliberado.

    Normaliza:
      - Sector_Focus  → normalize_sector_focus()  (LEGACY + pass-through ES)
      - Type          → TYPE_TRANSLATION_MAP       (EN→ES + excepciones)
      - Family        → FAMILY_TRANSLATION_MAP     (EN→ES + excepciones)
      - Theme         → no se traduce (ya en inglés canónico por diseño)
      - Subtype       → no se traduce (multi-idioma por diseño, BL-53)

    Logging (Sprint A.1.b sección 7.2c): emite WARNING cuando una traducción
    modifica el valor — señal de que un emisor anterior dejó valor en idioma
    incorrecto (bug latente).
    """
    isin = classification.get("ISIN", "???")

    if classification.get("Sector_Focus"):
        original = classification["Sector_Focus"]
        normalized = normalize_sector_focus(original)
        if normalized != original:
            logger.warning(
                "[%s] [NORM-Sector_Focus] Traducción aplicada: '%s' → '%s' "
                "(emisor anterior dejó valor en idioma incorrecto)",
                isin, original, normalized,
            )
        classification["Sector_Focus"] = normalized

    if classification.get("Type"):
        original = classification["Type"]
        translated_type = TYPE_TRANSLATION_MAP.get(original)
        if translated_type is not None and translated_type != original:
            logger.warning(
                "[%s] [NORM-Type] Traducción aplicada: '%s' → '%s' "
                "(emisor anterior dejó valor en idioma incorrecto)",
                isin, original, translated_type,
            )
        if translated_type is not None:
            classification["Type"] = translated_type

    if classification.get("Family"):
        original = classification["Family"]
        translated_family = FAMILY_TRANSLATION_MAP.get(original)
        if translated_family is not None and translated_family != original:
            logger.warning(
                "[%s] [NORM-Family] Traducción aplicada: '%s' → '%s' "
                "(emisor anterior dejó valor en idioma incorrecto)",
                isin, original, translated_family,
            )
        if translated_family is not None:
            classification["Family"] = translated_family

    return classification


# =====================================================
# Validación semántica obligatoria (Principio #9)
# =====================================================

# ============================================================
# v20 — DERIVACIÓN CENTRALIZADA DE ATRIBUTOS DE DOMINIO
# (root-cause #1 + DRY #2 + R-1). Punto ÚNICO: se invoca al inicio de
# apply_semantic_validation, que TODOS los bloques (incluida la delegación de
# restantes y sus paths de fallback) ejecutan al final. Lee las señales legacy
# que los bloques ya emiten (Type, Subtype, Geography-ES, Family, Profile) + el
# nombre, y deriva las columnas v20:
#   Geography(EN) · Development_Status · Vehicle_Structure · MMF_Structure ·
#   Alt_Strategy · Payoff_Profile · Duration_Profile · Credit_Quality ·
#   Liquidity_Profile · Profile (refinado por eje Fund_Nature).
# Idempotente (re-ejecutar produce el mismo resultado → seguro en re-runs, §C-3).
# NO inventa: aplica estándares de gestión de activos (UCITS KIID / PRIIPs KID).
# ============================================================

# --- Geografía espacial ES→EN (el eje desarrollo va a Development_Status) ---
_GEO_ES_TO_EN: dict = {
    "Europa": "Europe", "Global": "Global", "EEUU": "North America",
    "Asia": "Asia-Pacific", "China": "China", "Japón": "Japan",
    "India": "India", "Latinoamérica": "Latin America",
    "Europa del Este": "Eastern Europe",
    # FIX-GEO-1 (2026-07-05): "Italia" no tenía entrada aquí -- el fallback
    # de inferencia por benchmark (FTSE Italia) en pipeline.py lo persistía
    # tal cual, un valor ES sin traducir que además NO es un valor válido de
    # DOMAIN_VALUES['Geography'] (shared/config.py) en ningún idioma (no hay
    # nivel de país en ese catálogo, solo continente/región). Italia es
    # inequívocamente Europa -- se traduce ahí en vez de perder el dato.
    "Italia": "Europe",
    # "Emergentes" NO es geografía espacial → Global (o MEA si el nombre lo indica)
}
_EN_GEOGRAPHIES = frozenset({
    "Global", "Europe", "North America", "Asia-Pacific", "Japan", "China",
    "India", "Latin America", "Eastern Europe", "Middle East & Africa",
})
_GEO_EMERGING = frozenset({"China", "India", "Latin America",
                           "Eastern Europe", "Middle East & Africa"})
_GEO_DEVELOPED = frozenset({"Europe", "North America", "Japan"})

_PROFILE_ORDINAL = {"Conservador": 0, "Moderado": 1, "Dinámico": 2, "Agresivo": 3}
_ORDINAL_PROFILE = {0: "Conservador", 1: "Moderado", 2: "Dinámico", 3: "Agresivo"}
# Eje Fund_Nature: (floor, cap) ordinal del perfil de riesgo (institutional baseline)
_PROFILE_BOUNDS_BY_NATURE = {
    "Monetario":              (0, 0),   # capital preservation puro
    "Renta Fija Corto Plazo": (0, 1),
    "Renta Fija Flexible":    (1, 2),
    "Mixtos":                 (0, 2),
    "Renta Variable":         (1, 3),   # min-vol → Moderado; SRRI7 → Agresivo
    "Alternativo":            (1, 2),
    "Estructurado":           (1, 2),
    "Restantes":              (0, 3),   # sin restricción
}

_ALT_STRATEGY_MAP = {
    "Long/Short":                 "Long/Short",
    "Market Neutral":             "Market Neutral",
    "Global Macro":               "Global Macro",
    "Relative Value / Arbitrage": "Relative Value/Arbitrage",
    "Volatility Target":          "Volatility Target",
}

# §2A.1 #11: Exposure_Bias v20 = eje DIRECCIONAL puro. Los factores de RF
# (Duration/Credit/Income/Liquidity/Rate Reset Bias) ya viven en
# Duration_Profile/Credit_Quality → colapsan a 'Long Only'.
# Style_Profile (KEEP): 'Risk Control'/'Tactical' no están en el set v20 →
# remap a 'Strategic Allocation'.
_STYLE_LEGACY_REMAP = {
    "Risk Control": "Strategic Allocation",
    "Tactical":     "Strategic Allocation",
    "Defensivo":    None,   # 'Defensivo' es perfil de riesgo, no estilo → None
}

# AUDIT v20: Family ahora gobernada. Remap de literales legacy emitidos inline
# por los bloques hacia el set estándar (single source = classify_utils).
_FAMILY_LEGACY_REMAP = {
    "Systematic":  "Absolute Return",   # estrategia sistemática = familia AR
    "Lifecycle":   "Target Date",
    "Retirement":  "Target Date",
}


def _derive_geography_en(geo_es, name_l):
    """ES→EN espacial. 'Emergentes' → MEA si el nombre lo indica, si no Global."""
    if geo_es in _EN_GEOGRAPHIES:          # ya EN (idempotencia)
        return geo_es
    if geo_es == "Emergentes":
        if any(k in name_l for k in ["mena", "middle east", "africa", "gcc", "gulf"]):
            return "Middle East & Africa"
        return "Global"
    return _GEO_ES_TO_EN.get(geo_es)       # None si geo_es es None


def normalize_geography_en(geo_es: Optional[str], name_l: str = "") -> Optional[str]:
    """Public single-source-of-truth for ES→EN geography normalization (R-1 / P#11).

    Delegates to the internal _derive_geography_en. Use this instead of any
    local ES→EN map — duplicating _GEO_ES_TO_EN anywhere else is an R-1
    violation.  The audit tool and any other consumer that previously carried
    its own copy should import and call this function.

    Rules (inherits from _derive_geography_en):
      • Already-EN values are returned unchanged (idempotent).
      • "Emergentes" → "Middle East & Africa" if name signals MENA/Gulf;
        otherwise → "Global" (Emerging is not a canonical spatial geography).
      • All other ES labels mapped via _GEO_ES_TO_EN.
      • Unknown / None input → None.
    """
    return _derive_geography_en(geo_es, name_l)


def derive_development_status(geo_es, geo_en, name_l):
    """Eje de desarrollo (Developed/Emerging/Frontier/Global/Mixed)."""
    if any(k in name_l for k in ["frontier", "frontera"]):
        return "Frontier"
    if geo_es == "Emergentes" or any(k in name_l for k in [
        "emerging", "emergentes", "emergent", "em mkt", "emerg mkt",
        "emrg", "emer mkt", "mercados emergentes",
    ]):
        return "Emerging"
    if geo_en in _GEO_EMERGING:
        return "Emerging"
    if geo_en in _GEO_DEVELOPED:
        return "Developed"
    return "Global/Mixed"                  # Global, Asia-Pacific (mixto) o desconocido


def derive_vehicle_structure(nature, legacy_type, legacy_subtype, name_l):
    """Forma legal/estructural del vehículo (no clase de activo)."""
    st = legacy_subtype or ""
    ty = legacy_type or ""
    if st == "ETF" or "etf" in name_l:
        return "ETF"
    if nature == "Monetario":
        return "Money Market Fund"
    if nature == "Estructurado" or ty in ("Structured", "Capital Protegido"):
        return "Structured Product"
    if ty == "Fondo de Fondos" or any(k in name_l for k in [
        "fund of funds", "fof", "fondo de fondos",
    ]):
        return "Fund of Funds"
    return "Open-End UCITS"


def derive_mmf_structure(nature, legacy_subtype):
    """Clase regulatoria MMF 2017/1131. Solo aplica a Monetario."""
    if nature != "Monetario":
        return "Not Applicable"
    st = legacy_subtype or ""
    if st in ("CNAV", "LVNAV", "VNAV"):
        return st
    return "Standard MMF"


def derive_alt_strategy(nature, family, legacy_subtype):
    """Estrategia alternativa. Solo aplica a Alternativo."""
    if nature != "Alternativo":
        return "Not Applicable"
    st = legacy_subtype or ""
    if st in _ALT_STRATEGY_MAP:
        return _ALT_STRATEGY_MAP[st]
    if (family or "") in ("Real Assets",):  # Real Assets/Commodities no son estrategia
        return "Not Applicable"
    return "Opportunistic"                  # AR sin estrategia específica


def derive_payoff_profile(nature, legacy_type, legacy_subtype, name_l):
    """Perfil de payoff estructurado."""
    if "autocall" in name_l or legacy_subtype == "Autocallable":
        return "Autocallable"
    if (legacy_type == "Capital Protegido"
            or any(k in name_l for k in ["capital protec", "guaranteed",
                                         "capital guarant", "protected"])):
        return "Capital Protected"
    if any(k in name_l for k in ["fixed coupon", "fixed cpn", "fixed band", "cpn band"]):
        return "Fixed Coupon Band"
    return "Not Applicable"


def derive_duration_profile(nature, name_l):
    """Banda de duración de renta fija (años, baseline Morningstar/sector).
    Ultra-Short<1 · Short 1–3.5 · Intermediate 3.5–6 · Long>6 · Flexible(unconstrained).
    Solo FI/Monetario; el resto Not Applicable."""
    if nature == "Monetario":
        return "Ultra-Short"               # WAM < 1 año
    if nature == "Renta Fija Corto Plazo":
        if any(k in name_l for k in ["ultra short", "ult sh", "ul sh", "0-1",
                                     "enhanced cash", "money plus"]):
            return "Ultra-Short"
        return "Short"
    if nature == "Renta Fija Flexible":
        if any(k in name_l for k in ["unconstrained", "flexible", "dynamic", "dinamic",
                                     "strategic", "total return", "absolute return",
                                     "opportunistic", "tactical"]):
            return "Flexible"
        if any(k in name_l for k in ["long dur", "long-term", "long term"]):
            return "Long"
        if any(k in name_l for k in ["short", "low dur", "ultra"]):
            return "Short"
        if any(k in name_l for k in ["intermediate", "aggregate", "core"]):
            return "Intermediate"
        return "Flexible"                  # mandato flexible por defecto
    return "Not Applicable"


def derive_credit_quality(nature, name_l):
    """Calidad crediticia (baseline institucional por rating medio de cartera):
    IG = media ≥ BBB-/Baa3 · HY = media ≤ BB+/Ba1 ·
    Mixed = mandato cruza el umbral (crossover/flexible, sin sleeve ≥ ~80%).
    Aproximada por señales de nombre/mandato. Devuelve None para Restantes."""
    if nature in ("Renta Variable", "Mixtos", "Estructurado", "Alternativo"):
        return "Not Applicable"
    if nature == "Monetario":
        return "Investment Grade"          # MMF: alta calidad por regulación
    if nature == "Renta Fija Corto Plazo":
        # BL-B6-HY (2026-07-13): broadened HY name-token set.
        # Added "h.y." (JPM GLOB.H.Y.BOND FUND A), "hi.yie"/"hig.yie" (AXA WF
        # GLOB.HIG.YIE.BO., AXA WF US HIGH YIE.BOND. — OCR punctuated abbrevs),
        # "high yie" (partial match on "high yield" truncations in master Excel),
        # "hy bond"/"hy bnd" (common HY bond abbreviations).
        if any(k in name_l for k in ["high yield", "high-yield", " hy ",
                                      "h.y.", "hi.yie", "hig.yie", "high yie",
                                      "hy bond", "hy bnd", "alto rendimiento"]):
            return "High Yield"
        return "Investment Grade"          # crédito corto predominante IG
    if nature == "Renta Fija Flexible":
        if any(k in name_l for k in ["high yield", "high-yield", " hy ",
                                      "h.y.", "hi.yie", "hig.yie", "high yie",
                                      "hy bond", "hy bnd", "alto rendimiento"]):
            return "High Yield"
        if any(k in name_l for k in ["crossover", "flexible", "strategic",
                                     "unconstrained", "total return", "opportunistic",
                                     "multi sector", "multi-sector"]):
            return "Mixed"
        if any(k in name_l for k in ["investment grade", " ig ", "government",
                                     "sovereign", "govt", "aggregate", "core bond"]):
            return "Investment Grade"
        return "Mixed"                     # crédito flexible por defecto cruza el umbral
    return None                            # Restantes / no determinable → NULL


def derive_liquidity_profile(name_l):
    """Frecuencia de contratación (dealing). UCITS retail (KIID) → Daily por norma
    (liquidez mínima reglamentaria; >95% diaria). Degradar solo con señal explícita."""
    if any(k in name_l for k in ["weekly", "semanal"]):
        return "Weekly"
    if any(k in name_l for k in ["fortnight", "bi-weekly", "biweekly", "quincenal"]):
        return "Bi-Weekly"
    if any(k in name_l for k in ["monthly dealing", "monthly liquidity", "mensual"]):
        return "Monthly"
    return "Daily"


def _rv_style_cascade(name_l):
    if any(k in name_l for k in ["low vol", "minimum volatility", "minimum vol",
                                 "min vol", "min volatil"]):
        return "Low Volatility"
    if any(k in name_l for k in ["income", "dividend", "dividende", "dividends"]):
        return "Income"
    if "quality" in name_l:
        return "Quality"
    if any(k in name_l for k in ["growth", "wachstum", "crecim"]):
        return "Growth"
    if "value" in name_l:
        return "Value"
    if any(k in name_l for k in ["defensive", "risk control", "risk managed",
                                 "capital preservation"]):
        return "Defensivo"
    return None


def _rff_style_cascade(name_l):
    if any(k in name_l for k in ["high yield", "hy", "opportunistic", "opportunist",
                                 "credit opport", "yield enhancement"]):
        return "Income"
    if any(k in name_l for k in ["income", "rend", "rendement"]):
        return "Income"
    if any(k in name_l for k in ["low volatility", "low vol", "capital preservation",
                                 "defensiv", "securite"]):
        return "Low Volatility"
    return "Defensivo"


def _alt_style_cascade(name_l):
    if any(k in name_l for k in ["relative value", "arbitrage", "arbit",
                                 "arb strat", "arb str"]):
        return "Defensivo"
    if "market neutral" in name_l:
        return "Defensivo"
    if any(k in name_l for k in ["long short", "long/short", "long-short"]):
        return "Defensivo"
    if any(k in name_l for k in ["global rates", "gl rates"]):
        return "Defensivo"
    if any(k in name_l for k in ["multi strategy", "multi-strategy", "multiassut"]):
        return "Defensivo"
    if "global macro" in name_l or "adagio" in name_l:
        return "Momentum"
    if any(k in name_l for k in ["managed futures", "cta", "systematic"]):
        return "Momentum"
    if any(k in name_l for k in ["real estate", "property"]):
        return "Defensivo"
    if "infrastructure" in name_l:
        return "Defensivo"
    if any(k in name_l for k in ["commodities", "commodity", "gold", "precious metals"]):
        return None
    if any(k in name_l for k in ["abs ret", "absret", "st absret", "absolute return"]):
        return "Defensivo"
    return "Defensivo"


def derive_style_profile(nature, name_l, kiid_style=None):
    """Style_Profile centralizado (AUDIT v20). Réplica exacta de las cascadas que
    antes vivían inline en los bloques, con precedencia:
        cascada-por-naturaleza(nombre) > estilo-KIID > detect_style_profile(nombre).
    El remap final (Defensivo→None, Risk Control/Tactical→Strategic Allocation) se
    aplica aquí (single source). Mixtos → siempre 'Strategic Allocation' tras remap."""
    if nature == "Mixtos":
        return "Strategic Allocation"
    if nature == "Renta Variable":
        s = _rv_style_cascade(name_l)
    elif nature == "Renta Fija Flexible":
        s = _rff_style_cascade(name_l)
    elif nature == "Renta Fija Corto Plazo":
        s = "Income" if any(k in name_l for k in
                            ["income", "enhanced cash", "money plus"]) else None
    elif nature == "Alternativo":
        s = _alt_style_cascade(name_l)
    else:
        s = None   # Monetario / Estructurado / Restantes → fallback
    if s is None or s == "Defensivo":
        if kiid_style and kiid_style != "Defensivo":
            s = kiid_style
        else:
            s = detect_style_profile(name_l)
    return _STYLE_LEGACY_REMAP.get(s, s)


def derive_exposure_bias(nature, alt_strategy, legacy_exposure, name_l):
    """Eje direccional v20 (Long Only/Long-Short/Market Neutral/Net Short/N-A).
    Los sesgos de RF/liquidez legacy colapsan a 'Long Only' (su info ya está en
    Duration_Profile/Credit_Quality)."""
    le = legacy_exposure or ""
    if any(k in name_l for k in ["bear ", "net short", "short bias"]):
        return "Net Short"
    if (alt_strategy == "Long/Short" or le == "Long/Short"
            or any(k in name_l for k in ["long short", "long/short", "long-short"])):
        return "Long/Short"
    if alt_strategy == "Market Neutral" or "market neutral" in name_l:
        return "Market Neutral"
    if nature == "Estructurado":
        return "Not Applicable"
    if nature == "Alternativo" and alt_strategy == "Global Macro":
        return "Long/Short"
    return "Long Only"


def _refine_profile(profile, nature):
    """Profile = clamp(profile_del_bloque, floor_nature, cap_nature). Dos ejes
    (SRRI ya codificado por el bloque en `profile`; Nature aporta floor/cap)."""
    bounds = _PROFILE_BOUNDS_BY_NATURE.get(nature)
    if bounds is None:
        return profile
    floor, cap = bounds
    base = _PROFILE_ORDINAL.get(profile, floor)   # None/desconocido → floor
    base = max(floor, min(cap, base))
    return _ORDINAL_PROFILE[base]


def derive_v20_attributes(record: dict, fund_name: str) -> dict:
    """Deriva las columnas de dominio v20 desde las señales legacy de los
    bloques + el nombre. Idempotente. Único punto de verdad (R-1, #2)."""
    name_l = (fund_name or "").lower()
    nature = record.get("Fund_Nature")
    # Señales transitorias de bloque (namespace _signal_*); fallback a las columnas
    # legacy Type/Subtype para rutas externas que aún las usan (p.ej. BL-62).
    legacy_type = record.get("_signal_type") or record.get("Type")
    legacy_subtype = record.get("_signal_subtype") or record.get("Subtype")
    family = record.get("Family")

    # Geografía ES→EN + eje de desarrollo (split de la antigua Geography)
    geo_es = record.get("Geography")
    geo_en = _derive_geography_en(geo_es, name_l)
    record["Geography"] = geo_en
    record["Development_Status"] = derive_development_status(geo_es, geo_en, name_l)

    # Estructura de vehículo + columnas estructurales especializadas
    record["Vehicle_Structure"] = derive_vehicle_structure(
        nature, legacy_type, legacy_subtype, name_l)
    record["MMF_Structure"] = derive_mmf_structure(nature, legacy_subtype)
    record["Alt_Strategy"] = derive_alt_strategy(nature, family, legacy_subtype)
    record["Payoff_Profile"] = derive_payoff_profile(
        nature, legacy_type, legacy_subtype, name_l)

    # Renta fija: duración + calidad crediticia
    record["Duration_Profile"] = derive_duration_profile(nature, name_l)
    _cq = derive_credit_quality(nature, name_l)
    if _cq is not None:
        record["Credit_Quality"] = _cq

    # Liquidez de contratación (UCITS → Daily por defecto)
    record["Liquidity_Profile"] = derive_liquidity_profile(name_l)

    # Exposure_Bias direccional (§2A.1 #11) + remap legacy de Style_Profile
    record["Exposure_Bias"] = derive_exposure_bias(
        nature, record.get("Alt_Strategy"), record.get("Exposure_Bias"), name_l)
    _sp = record.get("Style_Profile")
    if _sp in _STYLE_LEGACY_REMAP:
        record["Style_Profile"] = _STYLE_LEGACY_REMAP[_sp]
    # Style_Profile centralizado (AUDIT v20): el engine es la fuente única; la
    # cascada por naturaleza vive aquí, no en los bloques.
    record["Style_Profile"] = derive_style_profile(nature, name_l, _sp)
    # Family finalizada centralmente (AUDIT v20): remap de literales legacy.
    _fam = record.get("Family")
    if _fam in _FAMILY_LEGACY_REMAP:
        record["Family"] = _FAMILY_LEGACY_REMAP[_fam]

    # Profile refinado por eje Nature (floor/cap institucional)
    record["Profile"] = _refine_profile(record.get("Profile"), nature)

    return record


def apply_semantic_validation(
    record: dict[str, str | None],
    fund_name: str,
) -> dict[str, str | None]:
    """Aplica validate_all_semantic_consistency con logging exhaustivo.

    Punto único de logging para validación semántica (post-fix Sprint A.1.b).
    La función master validate_all_semantic_consistency es pura (sin logging).
    """
    isin = record.get("ISIN", fund_name)  # fallback al fund_name si falta ISIN
    # v20: derivación centralizada de atributos de dominio ANTES de validar,
    # para que el validador vea Geography(EN) + las columnas nuevas (R-1, #2).
    record = derive_v20_attributes(record, fund_name)
    validation = validate_all_semantic_consistency(record)

    for err in validation["critical_errors"]:
        logger.info(
            "[%s] AUTO-CORRECCIÓN %s: %s",
            isin, err["rule"], err["message"],
        )
    # Siempre aplicar corrected_record: contiene correcciones de critical_errors
    # Y de warnings (e.g., SC-G1 Geography→Investment_Universe).
    # FIX-APPLY-CORRECTIONS-1 (2026-07-19): antes solo se aplicaba cuando
    # is_valid=False; warnings como SC-G1 eran logueados pero no propagados.
    record = validation["corrected_record"]

    for warn in validation["warnings"]:
        logger.warning("[%s] %s: %s", isin, warn["rule"], warn["message"])

    return record

# =====================================================
# BL-62: Inferencia léxica Family/Type post-BL-44
# Cuando BL-44 reclasifica Nature → 'Restantes', los
# valores heredados de Type/Family son falsos por
# construcción (reflejan la naturaleza errónea original).
# Estas funciones re-infieren Family/Type desde cero
# usando el nombre del fondo.
#
# Restricciones aplicadas:
#   R-1: catálogo centralizado aquí (DRY — un único punto de verdad)
#   R-4: actúa sobre fund_record post-corrección, no sobre BD
#   R-7: tests en tests/test_bl44_bl62_bl64_sprint_a1.py
# =====================================================

# Catálogo léxico canónico (orden importa: específicos ANTES que genéricos)
# Cada entrada: (regex_str, target_family, target_type)
# BL-LANG-EN (2026-05-09): Family/Type en inglés (idioma objetivo).
LEXICAL_FAMILY_INFERENCE_BL62: list[tuple[str, str, str]] = [
    # === High Yield ===
    (r'HIGH\s*YI|\bHY\b|GBL?\s*HY',
     'High Yield', 'Flexible Fixed Income'),
    # === Inflation-Linked ===
    (r'INFL',
     'Inflation-Linked', 'Flexible Fixed Income'),
    # === Emerging Market Debt: China, EM genérico, Asia ===
    (r'CHINA\s+(BOND|FIX)|CHINA\.?\s*BON',
     'Emerging Market Debt', 'Flexible Fixed Income'),
    (r'\bEM\s+(BOND|DEBT|MARK|CURR|MK|G\s+BON|MKT|DURAT)|EME\s+MK|EMERG\s+M|'
     r'EMERGING\s+M|EMERG\s+DBT|EMRG|EMER\.?\s*M|EMER\.MKT|\bBN\s+EM\b',
     'Emerging Market Debt', 'Flexible Fixed Income'),
    (r'ASIA[NS]?\s+(BOND|BON|LOC|FLEX|OPPO|TIGER)|ASIAN?\s+LOC|ASIA\s+LOC|'
     r'GBL?.*EM\b|TEMPLETON.*BON\b|TEMPLETON\s+ASIA|TEMPLETON\s+EMER|'
     r'GAM\s+STR\s+EM|GL?\s+RATES',
     'Emerging Market Debt', 'Flexible Fixed Income'),
    # === Absolute Return ===
    (r'ABS\s+R|ABSOLUTE\s+R|EVENT\s+DRIV|GLOBAL\s+MACRO|\bALPHA\b|'
     r'GS\s+AB\s+RTRN|AB\s+RTRN|RTRN\s+TRCK',
     'Absolute Return', 'Absolute Return'),
    # === Real Assets ===
    (r'COMMOD|VONTOBEL\s+COMMOD',
     'Real Assets', 'Commodities'),
    # === Thematic Equity ===
    (r'MEDTCH|MEDTECH|SMART\s+FOOD',
     'Thematic Equity', 'Active Management'),
    # === BL-62-LEXICAL-EXT (2026-05-09) ===
    # BGF US SHORT DURATION BOND (LU0171/LU0172/LU2624/LU2812)
    # LU0172420597: "BGF USD SHRT DUR BND" — USD pegado, no US+espacio
    (r'USD?\s+(SH\s+DURAT|SH\s+DUR|SHRT?\s+DUR|DOLLAR\s+SH)',
     'Short-Term Fixed Income', 'Short-Term Fixed Income'),
    # SISF US DOLLAR LIQUIDITY (LU1133x2)
    (r'DOLLAR\s+LIQUID|USD\s+LIQUID',
     'Money Market', 'Money Market'),
    # SISF E MR DEB TOT RE (LU0177)
    (r'MR\s+DEB\s+TOT|E\s+MR\s+DEB',
     'Flexible Fixed Income', 'Total Return'),
    # GAM LUXURY BRAND/BRANDS (LU0329x4)
    (r'LUXURY\s+BR',
     'Thematic Equity', 'Active Management'),
    # === Income Oriented: ANTES que Multi-Asset genérico ===
    (r'AMERIC.{0,6}INC|AMER\s+INC|INC\s+P\.|INCM\s+P\.|DYN\s+HIGH\s+INC|'
     r'INC.*GROW|GLOBAL\s+OPP\s+BOND|MFS\s+GL.*OPP|US\s+SH\s+TERM\s+BOND|'
     r'DFNSIV.*INC|DEFENSIVE.*INC|MULTI.*INC|MULTIINC|'
     r'BALANCED\s+INC|GL.*INC\s+PORT|GLOBAL.*INC\s+PORT',
     'Income Oriented', 'Allocation'),
    # === Total Return (RF Flexible) ===
    (r'TOT\s+RET|TOTAL\s+RET|TOTAL\s+RETURN',
     'Flexible Fixed Income', 'Total Return'),
    # === Flexible Fixed Income genérico ===
    (r'EURO\s+BOND|EUROBOND|GLOBAL\s+BOND|GL\s+BOND|'
     r'AGGREGATE|AGGR\b|CORE\s+BOND|CORE\+|INVEST.*GRADE\s+BOND',
     'Flexible Fixed Income', 'Flexible Fixed Income'),
    # === Multi-Asset ===
    (r'PRDNT\s+WLTH|PRUDENT\s+WEALTH|MULTASST\s+INC|MULT\s+ASST|MULTI\s+ASS|'
     r'MULTIOPP|MULTI\s+OPP|MULTIOPPORT|GLO\s+RESILI|RESILIENT|EQUILIB|'
     r'GLO?\.?\s*PERSPECTIVES|GLOBAL\s+PERSPECTIVES|GLO\s+MA|GLOBAL\s+MA|'
     r'FLEX\s+OPP|FLEX\s+PROP|PIONEER\s+FLEX|'
     r'BAL.*N\s+EUR|BLCED|BALANC|STRATEGY\s+\d|'
     r'STIFTUNG|STIFT|PATR(IM)?|GL\s+OPTIM|GLOBAL\s+OPTIM',
     'Multi-Asset', 'Allocation'),
]

# Pre-compilar para rendimiento
_BL62_COMPILED: list[tuple[re.Pattern, str, str]] = [
    (re.compile(pat, re.IGNORECASE), fam, typ)
    for pat, fam, typ in LEXICAL_FAMILY_INFERENCE_BL62
]


def _infer_family_type_from_name_bl62(
    fund_name: str,
) -> tuple[str | None, str | None]:
    """
    Infiere (Family, Type) desde el nombre del fondo usando el catálogo
    LEXICAL_FAMILY_INFERENCE_BL62. Procesa los patrones en orden: el primer
    match gana (específicos antes que genéricos).

    Args:
        fund_name: nombre del fondo (mayúsculas o minúsculas — regex es IGNORECASE)

    Returns:
        (family, type_val) si hay match; (None, None) si no hay patrón identificable.
    """
    if not fund_name:
        return None, None
    name_u = fund_name.upper()
    for pattern, family, type_val in _BL62_COMPILED:
        if pattern.search(name_u):
            return family, type_val
    return None, None


def propagate_nature_to_restantes_type_family(
    fund_record: dict,
    isin: str,
    log_fn=None,
) -> dict:
    """
    BL-62: re-infiere la Fund_Nature real (y Family/Type) para fondos que
    BL-44 detectó como incompatibles con su Nature original. El objetivo ya
    no es asignar Fund_Nature='Restantes' (que no es una naturaleza válida)
    sino determinar la naturaleza financiera correcta, o dejarla NULL con
    DQ=WARN si no es determinable.

    Estrategia (según decisión usuario 29-abr-2026, opción A):
      Fase 2: inferencia léxica desde nombre (LEXICAL_FAMILY_INFERENCE_BL62).
      Fase 3: residual sin patrón → Family=None, Type=None, DQ_Flag=WARN.

    Marca flags de sobrescritura forzada (_bl62_force_overwrite_*) para que
    BL-64 en sqlite_writer los aplique sin COALESCE.

    Restricciones:
      R-2: triple acción documentada (Fase 1 placeholder / Fase 2 léxica / Fase 3 residual).
      R-4: opera sobre fund_record post-corrección Nature.
      R-7: tests en tests/test_bl44_bl62_bl64_sprint_a1.py.
    """
    fund_name = fund_record.get('Fund_Name', '')

    # Fase 1: re-invocación de bloque (placeholder — requiere refactorización futura)
    # La arquitectura actual no permite re-invocar el clasificador de bloque
    # con garantía de idempotencia desde aquí. Se deja como TODO para un sprint
    # posterior de refactorización que desacople el clasificador del contexto de pipeline.
    # Para este sprint: comenzar con Fase 2 + Fase 3.

    # Fase 2: inferencia léxica
    inferred_family, inferred_type = _infer_family_type_from_name_bl62(fund_name)

    if inferred_family is not None:
        fund_record['Family'] = inferred_family
        fund_record['Type'] = inferred_type
        # BL-64: forzar sobrescritura en sqlite_writer (sin COALESCE)
        fund_record['_bl62_force_overwrite_family'] = True
        fund_record['_bl62_force_overwrite_type'] = True
        if log_fn:
            log_fn(
                f"  [BL-62] {isin} Family={inferred_family} Type={inferred_type} "
                f"inferidos léxicamente tras BL-44 → Restantes"
            )
        return fund_record

    # Fase 3: residual sin patrón léxico identificable
    fund_record['Family'] = None
    fund_record['Type'] = None
    fund_record['_bl62_force_overwrite_family'] = True
    fund_record['_bl62_force_overwrite_type'] = True
    if fund_record.get('Data_Quality_Flag') != 'WARN':
        fund_record['Data_Quality_Flag'] = 'WARN'
    if log_fn:
        log_fn(
            f"  [BL-62] {isin} sin patrón léxico identificable; "
            f"Family/Type=NULL; Data_Quality_Flag=WARN"
        )
    return fund_record
