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
from typing import Optional


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
]

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
    "guinness gl eq inc",
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
    "thematics safety",              # FAM_001897: RV temática (seguridad)
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

    # ── Señales en encabezado (nombre del producto) ─────────────────────────
    # El nombre del producto aparece en los primeros 600 chars y puede contener
    # señales de tipo aunque el texto esté fusionado (OCR sin espacios JPMorgan)
    _header = t[:600]
    _has_equity_in_header = (
        "equity" in _header          # "AsiaPacificEquityFund", "EquityFund"
        or "equities" in _header
        or "renta variable" in _header
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

    # ── Estructurado (buscar en todo el texto) ───────────────────────────────
    if any(k in t for k in [
        "autocall", "autocallable", "capital protected", "capital protection",
        "capital guarantee", "capital garantizado", "structured note",
        "nota estructurada", "barrier", "knock-in", "knock in",
    ]):
        return "Estructurado"

    # ── Monetario ────────────────────────────────────────────────────────────
    '''
    if any(k in t[:2000] for k in [
        "money market fund", "fondo del mercado monetario", "fondo monetario",
        "monetary fund", "ucits mmf", "standard money market",
        "short term money market", "low volatility money market",
        "fondsmonétaire", "geldmarktfonds",
        # DDF — añadido personal detectado en pictet
        "instrumentos del mercado monetario",
        "short-term money market",        
        "ftse eur 1-month eurodeposit",
        # DDF — señales de mercado monetario en formato PRIIPs
        "instrumentos del mercado monetario",
        "vencimiento medio ponderado",
        "activos en instrumentos del mercado",
        "mercados monetarios",
        "money market instruments",
        "weighted average maturity",
    ]):
        return "Monetario"
    '''

    include_patterns = [
        "money market fund", "fondo del mercado monetario", "fondo monetario",
        "monetary fund", "ucits mmf", "standard money market",
        "short term money market", "low volatility money market",
        "fondsmonétaire", "geldmarktfonds",
        "instrumentos del mercado monetario",
        "short-term money market",        
        "ftse eur 1-month eurodeposit",
        "vencimiento medio ponderado",
        "activos en instrumentos del mercado",
        "mercados monetarios",
        "money market instruments",
        "weighted average maturity",
    ]

    exclude_patterns = [
        "renta variable", "renta fija", "acciones", "equity", "fixed income", "instrumentos financieros derivados", "instrumentos de crédito", "ucits","ocivm","colectiva en valores mobiliarios"
    ]

    # Definimos la ventana de texto (primeros 2000 caracteres) en minúsculas una sola vez
    # para mejorar el rendimiento y asegurar que no haya fallos por mayúsculas
    ventana_texto = t[:4000].lower()

    # Evaluación de la lógica
    if any(k in ventana_texto for k in include_patterns) and not any(e in ventana_texto for e in exclude_patterns):
        return "Monetario"

    # ── A partir de aquí usar ventana objetivo ───────────────────────────────

    # Retorno absoluto con benchmark monetario → Alternativo
    has_ar = any(k in w for k in [
        "absolute return", "retorno absoluto", "rendimiento positivo independientemente",
        "positive return regardless", "en cualquier entorno de mercado",
        "market neutral", "long/short", "long short",
    ])
    has_cash_bench = any(k in w for k in [
        "€str", "estr", "eonia", "sonia", "sofr", "overnight",
        "tasa libre de riesgo",
    ])
    if has_ar and has_cash_bench:
        return "Alternativo"

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
    ])
    has_bonds = any(k in w for k in [
        "valores de renta fija", "fixed income securities",
        "invierte en bonos", "inverts in bonds",
        "renta fija", "invierte principalmente en bonos",
        "primarily in bonds", "debt securities",
        "invierte al menos", "invierte en valores de deuda",
        # Señales DDF genéricas adicionales
        "títulos de deuda",
        # "instrumentos de deuda" eliminado — aparece en equity funds en contexto
        # de warrants/pagarés ("instrumentos de deuda vinculados a RV")
        "deuda soberana", "deuda corporativa",
        "bonos corporativos", "bonos soberanos",
        "bonos y otros", "bonos (incluidos",
        "valores de deuda", "obligaciones",
        "renta fija y", "en bonos y",
        "bond securities", "fixed rate", "floating rate notes",
        "high yield bonds", "investment grade",
        "grado de inversión", "calificación crediticia",
    ])

    # RF dominante (declaración explícita de objetivo)
    bond_dominant = any(k in w for k in [
        "primarily in bonds", "mainly in bonds", "principally in bonds",
        "invierte principalmente en bonos", "invierte en bonos",
        "fixed income securities", "fixed income fund",
        "renta fija", "fixed income", "bond fund", "fondo de bonos",
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
        "grado de inversión", "investment grade bonds",
        "bonos de alto rendimiento",
        "activos principales: bonos", "principales activos: bonos",
        "principales activos negociados: bonos",
    ])

    # RV dominante (declaración explícita de objetivo)
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
    ]) or _has_equity_in_header or _ocr_equity

    # RV dominante sin RF → Renta Variable
    if eq_dominant and not bond_dominant:
        return "Renta Variable"

    # RF dominante sin equity en absoluto → pendiente corto/flexible
    if bond_dominant and not eq_dominant and not has_equity:
        return "_RF_pending"

    # RF dominante + equity presente (mención incidental en fondos RV/Mixtos) →
    # Devolver None: la Capa 2 (nombre del fondo) lo resolverá correctamente
    if bond_dominant and not eq_dominant and has_equity:
        return None


    # Mixto explícito
    if any(k in w for k in [
        "tanto acciones como bonos", "both equities and bonds",
        "equities and bonds", "stocks and bonds", "acciones y bonos",
        "renta variable y renta fija", "multiactivo", "multi-asset",
        "asset allocation", "asignación de activos", "múltiples clases de activos",
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
    ]):
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
    """
    t = kiid_text.lower() if kiid_text else ""
    _obj_start, _obj_end = _get_obj_bounds(kiid_text or "")
    w = _extract_window(t, _obj_start, _obj_end)

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

    # Señales explícitas de corto plazo en el objetivo
    if any(k in w for k in [
        "duración inferior", "duration below", "duration less than",
        "duration of less", "short duration", "ultra short", "ultrashort",
        "baja duración", "low duration", "corto plazo", "court terme",
        "0 a 2 año", "0 a 3 año", "0 to 2 year", "0 to 3 year",
        "1 a 3 año", "1 to 3 year", "menos de 3 años", "below 3 year",
        "menos de 2 años", "below 2 year", "short-term bond",
        "target maturity", "vencimiento fijo", "fixed maturity",
        "fecha de vencimient", "horizon 202", "credit 202", "bond 202",
    ]):
        return "RF_Corto"

    # Señales en nombre
    if _name_match(name_l, NAME_SIGNALS_RF_CORTO):
        return "RF_Corto"

    # SRRI muy bajo
    m = re.search(r"\b([1-7])\s*/\s*7\b", t)
    if m and int(m.group(1)) == 1:
        return "RF_Corto"

    return "RF_Flexible"


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


def detect_geography_from_kiid(kiid_text: str) -> Optional[str]:
    """
    Detecta Geography desde la ventana objetivo del KIID.
    Solo se usa cuando detect_geography (por nombre) devuelve None.

    Usa señales EXPLÍCITAS de objetivo de inversión (no menciones incidentales).
    Orden: específicas primero, globales al final.
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
          # Señales indirectas fiables (benchmark, índice)
          "bloomberg us aggregate","s&p 500","russell 1000",
          "russell 2000","dow jones","nasdaq",
          # Mención directa sin prefijo
          "estados unidos"], "EEUU"),
        # Europa — ANTES que Emergentes
        (["invierte principalmente en europa","invierte en europa",
          "zona euro","eurozona","valores europeos",
          "renta variable europea","mercado europeo",
          "european equities","european bonds"], "Europa"),
        # Global — ANTES que Emergentes: fondos globales mencionan EM incidentalmente
        (["invierte a nivel mundial","invierte en todo el mundo",
          "mercados de todo el mundo","globally diversified",
          "diversificación global","cartera global",
          # Índices de referencia globales como proxy fiable
          "jp morgan global government bond","global government bond index",
          "world government bond","bloomberg global aggregate",
          "msci world","msci acwi","ftse world",
          "global bond fund","global equity fund"], "Global"),
        # Emergentes — señal dominante requerida
        (["invierte principalmente en mercados emergentes",
          "mercados emergentes como objetivo principal",
          "emerging market debt","emerging market equities",
          "deuda de mercados emergentes",
          "renta variable de mercados emergentes"], "Emergentes"),
    ]

    for signals, geo in _GEO_OBJ_PATTERNS:
        if any(s in w for s in signals):
            return geo

    return None


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

def detect_geography(name_l: str) -> Optional[str]:
    """Detecta geografía desde el nombre del fondo (en minúsculas)."""
    if any(k in name_l for k in ["japan","japanese","japon"]):
        return "Japón"
    if "jpy" in name_l:
        return "Japón"
    if any(k in name_l for k in ["china","chinese","a-shares","greater china","gran china","hong kong"]):
        return "China"
    if any(k in name_l for k in ["asia pacific","asia-pacific","apac","asean","pacific"]):
        return "Asia"
    if any(k in name_l for k in ["asia","asian","asia ex"]):
        return "Asia"
    if any(k in name_l for k in ["india","indian"]):
        return "India"
    if any(k in name_l for k in ["brazil","brasil","latin","latam"]):
        return "Latinoamérica"
    if any(k in name_l for k in ["mena","middle east"]):
        return "Emergentes"
    if any(k in name_l for k in ["emerging","emergentes","emergent","em mkt","emerg mkt",
                                   "emerg ","emrg","emer mkt","emer ","frontier"]):
        return "Emergentes"
    if any(k in name_l for k in ["us ","usa","u.s.","united states","america","american",
                                   "us eq","us sm","us sel","treasury","t-bill","us govt",
                                   "us dollar","us money"]):
        return "EEUU"
    if " usd " in name_l:
        return "EEUU"
    if any(k in name_l for k in [" uk ","uk eq","uk inc","uk sit","uk sc","uk ag",
                                   "united kingdom","british","britain"," gbp ","gbp ac",
                                   "gbp in","gbphdg","sterling"]):
        return "Europa"
    if any(k in name_l for k in ["swiss","switzerland"," chf ","chf ac","chf p ","chfhdg"]):
        return "Europa"
    if any(k in name_l for k in ["russia","osteuropa","eastern euro","east europ"]):
        return "Europa del Este"
    if any(k in name_l for k in ["europe","european","euro "," euro","euroland","eurozone",
                                   "europ","europa","euroz","emu","deutsch","germany",
                                   "italia","italian","iberia","nordic","france","french"]):
        return "Europa"
    if any(k in name_l for k in ["global","glob ","globl"," glb "," gbl ","glbl","glbal",
                                   " gl ","world","wrld","wld ","international","intl",
                                   "worldwide","multi-region","multiregion"]):
        return "Global"
    if "usdh" in name_l:
        return "Global"
    if " eur " in name_l:
        return "Europa"
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


# ============================================================
# 15. THEME_SECTOR_MAPPING
# ============================================================

THEME_SECTOR_MAPPING: dict = {
    "Technology": "Technology & Innovation",
    "Artificial Intelligence": "Technology & Innovation",
    "Digital": "Technology & Innovation",
    "Robotics": "Technology & Innovation",
    "Healthcare": "Healthcare & Life Sciences",
    "Healthcare / MedTech": "Healthcare & Life Sciences",
    "Biotechnology": "Healthcare & Life Sciences",
    "Energy": "Energy & Resources",
    "Climate / Clean Energy": "Energy & Resources",
    "Water": "Utilities & Environment",
    "Gold": "Materials & Mining",
    "Mining": "Materials & Mining",
    "Real Estate": "Real Estate & Infrastructure",
    "Infrastructure": "Real Estate & Infrastructure",
    "Insurance": "Financials & Insurance",
    "Financials": "Financials & Insurance",
    "Consumer Brands": "Consumer Discretionary",
    "Silver Economy": "Healthcare & Life Sciences",
}

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
        return acc_policy, dist_freq, (
            "WARNING: DISTRIBUTION sin Distribution_Frequency poblado"
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
            warnings.append({"rule": "Accumulation-Distribution", "message": msg})
        else:
            critical_errors.append({"rule": "Accumulation-Distribution", "message": msg})
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

    # INTER-9
    status, msg = validate_theme_sector_coherence(
        cr.get("Theme"), cr.get("Sector_Focus")
    )
    if status == "WARNING":
        warnings.append({"rule": "Theme-Sector", "message": msg})

    # INTER-10 (BL-52: auto-corrección Country→Regional cuando Geography es región)
    status, msg, corrected_univ = validate_geography_universe(
        cr.get("Geography"), cr.get("Investment_Universe")
    )
    if status == "CORRECTED":
        cr["Investment_Universe"] = corrected_univ
        critical_errors.append({"rule": "Geography-Universe", "message": msg})
    elif status == "WARNING":
        warnings.append({"rule": "Geography-Universe", "message": msg})

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
                    warnings.append({
                        "rule": "InvestmentUniverse-NatureFallback",
                        "message": (
                            f"Investment_Universe='Global' inferido por defecto "
                            f"(sin Geography ni Sector_Focus) para Nature='{_nature}'"
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

    for col, value in fund_record.items():
        if col in ALLOWED_VALUES_BY_COLUMN and value is not None:
            if value not in ALLOWED_VALUES_BY_COLUMN[col]:
                warnings.append({
                    "rule": "Allowed-Values",
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
        if any(k in name_l for k in ["high yield", "high-yield", " hy "]):
            return "High Yield"
        return "Investment Grade"          # crédito corto predominante IG
    if nature == "Renta Fija Flexible":
        if any(k in name_l for k in ["high yield", "high-yield", " hy "]):
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

    if not validation["is_valid"]:
        for err in validation["critical_errors"]:
            logger.info(
                "[%s] AUTO-CORRECCIÓN %s: %s",
                isin, err["rule"], err["message"],
            )
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
