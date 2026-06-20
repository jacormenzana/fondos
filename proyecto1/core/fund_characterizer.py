# -*- coding: utf-8 -*-
"""
proyecto1/core/fund_characterizer.py  — v21

Cambios v21 (2026-04-29):
  BL-49/DRY  Consolidación DRY de detect_currency_hedged() (Principio #2).
             Causa raíz: la fase 2 de detección desde texto KIID estaba
             implementada dos veces de forma independiente:
               - fund_characterizer.detect_currency_hedged() líneas 557-590
                 (10 patrones de substring sobre lower())
               - classify_utils.detect_currency_hedged_from_kiid()
                 (18 patrones pre-compilados con re.compile, H01-H10 / U01-U08)
             Fix: la fase 2 (KIID) de detect_currency_hedged() se sustituye
             por delegación en detect_currency_hedged_from_kiid() de
             classify_utils. Un único punto de verdad para patrones KIID.
             La fase 1 (detección por nombre, patrones _HEDGED/_UNHEDGED)
             permanece intacta — no tiene equivalente en classify_utils.
             Import classify_utils ampliado: detect_currency_hedged_from_kiid.

Cambios v20 (2026-04-25):
  BL-53  detect_sector_focus(): eliminado hardcode 'Real Assets' (inglés).
         El mapa completo de themes→sectores ahora reside en
         classify_utils.THEME_TO_SECTOR_FOCUS_MAP (BL-54). Esta función
         delega en map_theme_to_sector_focus() importada desde classify_utils.
         Efecto: todos los valores de Sector_Focus emitidos desde este módulo
         están ahora en español canónico — cumple Principio #8.
  BL-54  detect_sector_focus() refactorizada para usar el mapa canónico
         único (Principio #2 DRY). La lógica if/elif previa con 14 entradas
         queda reemplazada por una delegación a map_theme_to_sector_focus().
         La firma permanece igual: (name_l, kiid_text=None, theme=None).

Cambios v19 (2026-04-19):
  BL-49  detect_currency_hedged(): fallback al texto KIID cuando el nombre
         del fondo no aporta señal de cobertura. Solo activa si kiid_text
         disponible y name_l da None. Prioridad: Unhedged > Hedged para
         evitar falsos positivos con "hedged" en frases negativas del KIID.
         Nuevo parámetro opcional kiid_text=None — backward compatible.
         characterize_fund() propaga kiid_text al llamar detect_currency_hedged().

Cambios v18 (2026-04-13):
  BL-26  detect_currency_hedged(): "Yes"→"Hedged", "No"→"Unhedged" (Principio #8).
         +variantes OCR hedge (hgd, eurhdg, usdhdg, gbphdg).
         +detección explícita "not hedged" / "no hedged".
  BL-27  detect_market_cap_focus(): +Mid/SMID desde KIID texto, +inferencia
         desde benchmark_declared. Nuevo parámetro propagado en characterize_fund().
  BL-28  detect_credit_quality(): "No aplica"→"Not Applicable" (Principio #8).
         _THEMATIC_THEMES: "Inflación"→"Inflation".
         THEMATIC_MAP_EXTENDED: "Inflación"→"Inflation".
  BL-29  _char_rv(): segunda capa detect_style_from_kiid(text_l).

Módulo genérico de caracterización secundaria de fondos.

Cambios v17 respecto a v16:
  INV-FOCUS-1  Nueva función detect_investment_focus(): dimensión de exposición
               ortogonal a Investment_Universe (Broad | Sector | Thematic).
               Investment_Universe pasa a ser puramente geográfico.

  INV-UNI-1    detect_investment_universe() refactorizada: elimina la lógica
               basada en Theme (Sector/Thematic). Solo deriva de Geography
               y Fund_Nature.

  SECTOR-P14   detect_sector_focus() recibe `theme` ya calculado como parámetro
               opcional. Fix al bug P14 donde fondos con tema detectado solo
               en KIID (no en nombre) obtenían Sector_Focus=NULL.

  SECTOR-GICS  Valores de Sector_Focus renombrados a nomenclatura GICS-ES:
               "Technology & Innovation" → "Tecnología e Innovación", etc.

  CREDIT-1     Nueva función detect_credit_quality(): detecta calidad crediticia
               solo para fondos de RF y Mixtos.

  THEME-CG     Theme=NULL semántico reemplazado por "Core/General" para fondos
               no temáticos procesados. NULL queda reservado para "no procesado".

  PROFILE-4    Profile añade valor "Agresivo" para SRRI=7.

  CHAR-V17     characterize_fund() actualizado: nuevo orden de operaciones,
               Investment_Focus y Credit_Quality añadidos al dict de resultado.
"""

import re
from typing import Optional, Dict, Any

try:
    from proyecto1.core.classify_utils import (
        detect_geography, detect_theme, detect_is_esg,
        detect_style_profile, detect_exposure_bias,
        detect_strategy, detect_benchmark_type,
        detect_profile_from_srri, detect_kiid_attributes,
        detect_geography_from_kiid, detect_esg_from_kiid,
        detect_style_from_kiid, detect_type_from_kiid,
        _get_obj_bounds, _extract_window,
        map_theme_to_sector_focus,   # BL-54: mapa canónico Theme→Sector_Focus
        detect_currency_hedged_from_kiid,  # BL-49/DRY: patrones KIID centralizados
    )
except ImportError:
    from core.classify_utils import (
        detect_geography, detect_theme, detect_is_esg,
        detect_style_profile, detect_exposure_bias,
        detect_strategy, detect_benchmark_type,
        detect_profile_from_srri, detect_kiid_attributes,
        detect_geography_from_kiid, detect_esg_from_kiid,
        detect_style_from_kiid, detect_type_from_kiid,
        _get_obj_bounds, _extract_window,
        map_theme_to_sector_focus,   # BL-54: mapa canónico Theme→Sector_Focus
        detect_currency_hedged_from_kiid,  # BL-49/DRY: patrones KIID centralizados
    )


# =============================================================
# CONSTANTES DE DOMINIO
# =============================================================

# Temas que implican foco sectorial (inversión en un sector concreto)
_SECTOR_THEMES: frozenset = frozenset({
    "Technology", "Artificial Intelligence", "Digital", "Robotics",
    "Cybersecurity", "Climate / Clean Energy", "Energy", "Water",
    "Financials", "Mining", "Gold", "Insurance", "Silver Economy",
    "Mobility", "Consumer Brands", "Consumer / Food & Beverage",
    "Megatrends", "Healthcare / MedTech", "Biotechnology",
})

# Temas transversales (no sectoriales — cruzan sectores y geografías)
_THEMATIC_THEMES: frozenset = frozenset({
    "Infrastructure", "Real Estate", "Inflation", "Healthcare",  # BL-28: "Inflación" → "Inflation"
})

# Geografías de un solo país
_COUNTRY_GEOS: frozenset = frozenset({
    "China", "Japón", "India", "Latinoamérica", "Europa del Este", "Rusia",
    "Italia", "Alemania", "España",
})

# Geografías regionales
_REGIONAL_GEOS: frozenset = frozenset({
    "Europa", "Asia", "Emergentes", "EEUU", "Asia-Pacífico", "Norteamérica",
})

# Naturalezas de corto plazo / liquidez
_LIQUIDITY_NATURES: frozenset = frozenset({
    "Monetario", "Renta Fija Corto Plazo",
})

# Naturalezas de renta fija donde Credit_Quality es relevante
_RF_NATURES: frozenset = frozenset({
    "Renta Fija Flexible", "Renta Fija Corto Plazo", "Monetario", "Mixtos", "Alternativo",
})


# =============================================================
# THEMATIC MAP EXTENDIDO
# (igual que v16 — mantenido aquí para independencia del módulo)
# =============================================================

THEMATIC_MAP_EXTENDED: Dict[str, str] = {
    # Technology
    "technology": "Technology", "tecnología": "Technology", "tech": "Technology",
    "tecnologia": "Technology",
    # AI
    "artificial intelligence": "Artificial Intelligence",
    "inteligencia artificial": "Artificial Intelligence",
    "ai fund": "Artificial Intelligence", "a.i.": "Artificial Intelligence",
    # Digital
    "digital": "Digital", "digitalization": "Digital",
    "digitalización": "Digital",
    # Robotics
    "robotics": "Robotics", "robótica": "Robotics", "automation": "Robotics",
    "automatización": "Robotics",
    # Healthcare / MedTech
    "medtech": "Healthcare / MedTech", "medical technology": "Healthcare / MedTech",
    "healthcare": "Healthcare / MedTech", "health": "Healthcare / MedTech",
    "wellcare": "Healthcare / MedTech", "salud": "Healthcare / MedTech",
    "ciencias de la salud": "Healthcare / MedTech",
    # Biotechnology
    "biotec": "Biotechnology", "biotech": "Biotechnology",
    "biotecnología": "Biotechnology", "genetic": "Biotechnology",
    "genomic": "Biotechnology",
    # Cybersecurity
    "cybersecurity": "Cybersecurity", "ciberseguridad": "Cybersecurity",
    "cyber security": "Cybersecurity", "digital security": "Cybersecurity",
    "safety": "Cybersecurity",
    # Climate / Clean Energy
    "climate": "Climate / Clean Energy", "clean energy": "Climate / Clean Energy",
    "renewable": "Climate / Clean Energy", "energía limpia": "Climate / Clean Energy",
    "transición energética": "Climate / Clean Energy",
    "net zero": "Climate / Clean Energy", "low carbon": "Climate / Clean Energy",
    # Water
    "pictet water": "Water", " water ": "Water", "water fund": "Water",
    "water eq": "Water", "global water": "Water", "clean water": "Water",
    # Energy
    "energy": "Energy", "energía": "Energy", "energia": "Energy",
    # Real Estate
    "real estate": "Real Estate", "real estat": "Real Estate",
    "property": "Real Estate", "inmobiliario": "Real Estate",
    # Silver Economy
    "silver age": "Silver Economy", "silverplus": "Silver Economy",
    "silver economy": "Silver Economy",
    # Insurance
    "insurance": "Insurance", "seguros": "Insurance",
    # Consumer
    "global brands": "Consumer Brands", "glob brands": "Consumer Brands",
    "consumer brand": "Consumer Brands",
    "food": "Consumer / Food & Beverage",
    "food & beverage": "Consumer / Food & Beverage",
    "alimentación": "Consumer / Food & Beverage",
    # Financials
    "financial": "Financials", "financials": "Financials", "finanzas": "Financials",
    # Mining / Gold
    "mining": "Mining", "gold": "Gold", "oro": "Gold",
    "precious metals": "Gold", "metales preciosos": "Gold",
    # Infrastructure
    "infrastructure": "Infrastructure", "infraestructura": "Infrastructure",
    # Megatrends
    "megatrend": "Megatrends", "megatendencia": "Megatrends",
    "disruptive": "Megatrends",
    # Mobility
    "mobility": "Mobility", "movilidad": "Mobility",
    "autonomous": "Mobility", "electric vehicle": "Mobility",
    # Inflation  # BL-28: valor en inglés, coherente con ALLOWED_VALUES_BY_COLUMN
    "inflación": "Inflation", "inflation": "Inflation",
}


def detect_theme_extended(name_l: str) -> Optional[str]:
    """Detecta tema usando THEMATIC_MAP_EXTENDED (sobre nombre del fondo)."""
    for signal, theme in THEMATIC_MAP_EXTENDED.items():
        if signal in name_l:
            return theme
    return None


def detect_theme_from_kiid(kiid_text: Optional[str]) -> Optional[str]:
    """Detecta tema desde texto objetivo del KIID. Segunda capa tras nombre."""
    if not kiid_text:
        return None
    _obj_start, _obj_end = _get_obj_bounds(kiid_text)
    w = _extract_window(kiid_text.lower(), _obj_start, _obj_end)

    theme_text_signals = [
        (["inteligencia artificial", "artificial intelligence",
          "machine learning", "aprendizaje automático",
          "deep learning"], "Artificial Intelligence"),
        (["energías renovables", "energía limpia", "clean energy",
          "energía verde", "transición energética",
          "energy transition", "energía sostenible"], "Climate / Clean Energy"),
        (["activos de infraestructura", "infrastructure assets",
          "infraestructuras cotizadas", "listed infrastructure",
          "inversión en infraestructura"], "Infrastructure"),
        (["metales preciosos", "precious metals", "fondos de oro",
          "gold fund", "lingotes de oro", "gold bullion",
          "physical gold", "oro físico"], "Gold"),
        (["sector inmobiliario", "real estate sector", "bienes raíces",
          "empresas inmobiliarias", "real estate companies",
          "mercado inmobiliario"], "Real Estate"),
        (["biotecnología", "biotechnology", "ciencias de la vida",
          "life sciences", "empresas biotecnológicas"], "Biotechnology"),
        (["recursos hídricos", "water resources", "water companies",
          "sector del agua", "water sector", "gestión del agua",
          "water management", "water utilities", "water fund",
          "tecnología del agua", "tratamiento del agua",
          "infraestructura del agua"], "Water"),
    ]
    for signals, theme in theme_text_signals:
        if any(sig in w for sig in signals):
            return theme
    return None


# =============================================================
# DETECT_INVESTMENT_UNIVERSE  (v17 — solo dimensión geográfica)
# =============================================================

def detect_investment_universe(
    fund_nature: str,
    geography: Optional[str],
    theme: Optional[str] = None,    # mantenido para compatibilidad, ignorado
    fund_type: Optional[str] = None,
) -> Optional[str]:
    """
    Deriva el ámbito geográfico del mandato de inversión.

    v17: puramente geográfico. Los valores 'Sector' y 'Thematic' se han
    trasladado al nuevo atributo Investment_Focus.

    Valores:
        Liquidity  — monetarios y RF corto plazo
        Global     — cobertura mundial
        Regional   — región geográfica (Europa, Asia, Emergentes, EEUU)
        Country    — un único país o mercado (China, Japón, India...)
    """
    if fund_nature in _LIQUIDITY_NATURES:
        return "Liquidity"
    if geography in _COUNTRY_GEOS:
        return "Country"
    if geography in _REGIONAL_GEOS:
        return "Regional"
    if geography == "Global":
        return "Global"
    return None


# =============================================================
# DETECT_INVESTMENT_FOCUS  (v17 — NUEVO)
# =============================================================

def detect_investment_focus(
    fund_nature: str,
    theme: Optional[str],
) -> Optional[str]:
    """
    Detecta el tipo de exposición del fondo: mercado amplio, sector o temática.

    Dimensión ortogonal a Investment_Universe. Permite cruzar ámbito geográfico
    con tipo de exposición sin colapsarlos en un único campo.

    Valores:
        Broad     — mercado amplio sin restricción sectorial/temática
        Sector    — foco en un sector económico específico
        Thematic  — exposición temática transversal (cruza sectores)
        None      — Liquidity (no aplica para monetarios/RF corto)
    """
    if fund_nature in _LIQUIDITY_NATURES:
        return None   # Liquidez no tiene "tipo de exposición"

    if theme is None or theme == "Core/General":
        return "Broad"
    if theme in _SECTOR_THEMES:
        return "Sector"
    if theme in _THEMATIC_THEMES:
        return "Thematic"
    # Tema detectado pero no clasificado → Thematic genérico
    return "Thematic"


# =============================================================
# DETECT_SECTOR_FOCUS  (v20 — BL-53/BL-54: refactor DRY)
# =============================================================

def detect_sector_focus(
    name_l: str,
    kiid_text: Optional[str] = None,
    theme: Optional[str] = None,    # v17: recibe tema ya calculado
) -> Optional[str]:
    """
    Detecta foco sectorial bajo nomenclatura GICS-ES.

    v20 BL-54: delega en map_theme_to_sector_focus() (classify_utils),
    que contiene el mapa canónico ÚNICO Theme→Sector_Focus (Principio #2 DRY).
    Todos los valores emitidos están en español (Principio #8 BL-53).
    La firma permanece compatible con v17 (name_l, kiid_text, theme).

    v17 fix P14: recibe `theme` ya calculado por el llamador, evitando
    re-ejecutar detect_theme_extended() sobre el nombre cuando el tema
    fue detectado desde el KIID (no desde el nombre).

    Valores (GICS-ES, idioma español):
        Tecnología e Innovación
        Salud y Ciencias de la Vida
        Energía y Recursos
        Activos Reales
        Servicios Financieros
        Consumo
        Materiales y Minería
        Utilities y Medio Ambiente
    """
    # Usar tema provisto o detectar desde nombre como fallback
    resolved_theme = theme or detect_theme_extended(name_l)
    # Delegar en el mapa canónico único (BL-54)
    return map_theme_to_sector_focus(resolved_theme)


# =============================================================
# DETECT_CREDIT_QUALITY  (v17 — NUEVO)
# =============================================================

_HY_NAME_SIGNALS = [
    "high yield", "high-yield", "hy ", " hy ", "alto rendimiento",
    "bonos hy", "high yield bond",
]
_IG_NAME_SIGNALS = [
    "investment grade", "investment-grade", "ig bond",
    "grado de inversión",
]
_HY_TEXT_SIGNALS = [
    "high yield", "alto rendimiento", "high-yield",
    "sub-investment grade", "non-investment grade",
    "speculative grade", "junk bond", "bonos de alto rendimiento",
]
_IG_TEXT_SIGNALS = [
    "investment grade", "grado de inversión", "investment-grade",
    "bonos con grado de inversión", "emisores con grado de inversión",
    "calificación crediticia mínima de", "rated at least",
]


def detect_credit_quality(
    fund_nature: str,
    name_l: str,
    kiid_text: Optional[str] = None,
) -> Optional[str]:
    """
    Detecta la calidad crediticia de la cartera del fondo.

    Solo relevante para fondos de RF y Mixtos. Renta Variable → 'No aplica'.

    Valores:
        Investment Grade  — cartera con calificación ≥ BBB-
        High Yield        — cartera con calificación < BBB- / sin grado
        Mixed             — mezcla explícita de IG y HY
        No aplica         — Renta Variable, no extraíble
    """
    if fund_nature == "Renta Variable":
        return "Not Applicable"   # BL-28: era "No aplica" (Principio #8 — inglés)
    if fund_nature not in _RF_NATURES:
        return None   # Restantes, Estructurado → indeterminado

    # Señales en nombre
    hy_name = any(s in name_l for s in _HY_NAME_SIGNALS)
    ig_name = any(s in name_l for s in _IG_NAME_SIGNALS)
    if hy_name and not ig_name:
        return "High Yield"
    if ig_name and not hy_name:
        return "Investment Grade"

    # Señales en texto KIID (ventana objetivo)
    if kiid_text:
        s, e = _get_obj_bounds(kiid_text)
        w = _extract_window(kiid_text.lower(), s, e)
        hy_count = sum(1 for sig in _HY_TEXT_SIGNALS if sig in w)
        ig_count = sum(1 for sig in _IG_TEXT_SIGNALS if sig in w)
        if hy_count > 0 and ig_count == 0:
            return "High Yield"
        if ig_count > 0 and hy_count == 0:
            return "Investment Grade"
        if hy_count > 0 and ig_count > 0:
            return "Mixed"

    # Reglas de dominio por naturaleza
    if fund_nature == "Monetario":
        return "Investment Grade"   # regulatoriamente obligatorio
    if fund_nature == "Renta Fija Corto Plazo":
        return "Investment Grade"   # por definición del bloque

    return None   # RF Flexible, Mixtos, Alternativo sin señal clara


# =============================================================
# DETECT_MARKET_CAP_FOCUS
# =============================================================

def detect_market_cap_focus(
    name_l: str,
    kiid_text: Optional[str] = None,
    benchmark_declared: Optional[str] = None,  # BL-27: nuevo parámetro
) -> Optional[str]:
    """Detecta enfoque de capitalización de mercado.

    BL-27: ampliado con detección KIID para Mid/SMID Cap y con inferencia
    por benchmark declarado (p.ej. MSCI World Small Cap → Small Cap).
    """
    if any(k in name_l for k in [
        "small cap", "small-cap", "smallcap", "small co",
        "micro cap", "micro-cap", "small companies",
        "pequeña capitalización", "pequeñas compañías",
    ]):
        return "Small Cap"
    if any(k in name_l for k in [
        "mid cap", "mid-cap", "midcap", "mid co",
        "mediana capitalización", "medianas compañías",
    ]):
        return "Mid Cap"
    if any(k in name_l for k in [
        "smid", "small & mid", "small and mid",
        "small to mid", "pequeñas y medianas",
    ]):
        return "SMID Cap"
    if any(k in name_l for k in [
        "large cap", "large-cap", "largecap",
        "blue chip", "grande capitalización", "grandes compañías",
    ]):
        return "Large Cap"

    if kiid_text:
        s, e = _get_obj_bounds(kiid_text)
        w = _extract_window(kiid_text.lower(), s, e)
        if any(k in w for k in [
            "pequeña capitalización", "small capitalisation",
            "small-cap companies", "small cap companies",
            "pequeñas empresas", "small companies",
        ]):
            return "Small Cap"
        if any(k in w for k in [
            "gran capitalización", "large capitalisation",
            "large-cap companies", "grandes empresas",
            "blue chip companies", "blue-chip",
        ]):
            return "Large Cap"
        # BL-27: detección Mid y SMID desde texto KIID
        if any(k in w for k in [
            "mediana capitalización", "mid capitalisation",
            "mid-cap companies", "medianas empresas",
        ]):
            return "Mid Cap"
        if any(k in w for k in [
            "pequeñas y medianas", "small and mid", "small- and mid-",
            "small to mid-cap",
        ]):
            return "SMID Cap"

    # BL-27: inferencia desde benchmark declarado
    if benchmark_declared:
        b = benchmark_declared.lower()
        if any(k in b for k in ["small cap", "small-cap", "smallcap", "sc index"]):
            return "Small Cap"
        if any(k in b for k in ["mid cap", "mid-cap", "midcap"]):
            return "Mid Cap"
        if any(k in b for k in ["smid", "small & mid", "small and mid"]):
            return "SMID Cap"
        if any(k in b for k in ["large cap", "blue chip"]):
            return "Large Cap"
        # Si el benchmark es un índice global de gran capitalización → Large Cap por defecto
        if any(k in b for k in ["msci world", "msci acwi", "s&p 500", "euro stoxx 50",
                                  "stoxx europe 600", "ftse 100", "dax"]):
            return "Large Cap"

    return None


# =============================================================
# DETECT_CURRENCY_HEDGED
# =============================================================

def detect_currency_hedged(name_l: str, kiid_text: Optional[str] = None) -> Optional[str]:
    """Detecta política de cobertura de divisa desde el nombre de la clase.

    BL-26: corregidos valores a "Hedged"/"Unhedged" (Principio #8).
    BL-26: añadida detección explícita de "Unhedged" (antes solo detectaba Hedged).
    BL-49: fallback al texto KIID cuando el nombre no aporta señal. Solo se activa
           cuando kiid_text está disponible y name_l no detectó nada. Prioridad:
           UNHEDGED antes que HEDGED (evitar falsos positivos de "hedged" en contextos
           negativos del texto KIID, ej: "no está cubierta").
    BL-49/2 (2026-04-25): añadidas variantes EURH/USDH/GBPH/CHFH (truncamiento del
           sufijo HDG común en iShares, Candriam, GAM, Goldman Sachs). Estas
           variantes tienen una "H" pegada al código de divisa sin "DG" final
           (ej: "EM MK GV INDX A2 EURH ACC"). Patrón previo solo capturaba
           "EURHDG", causando 4 fondos en regresión Hedged→Unhedged.
    """
    _HEDGED = [
        "hedged", "(h)", "- h)", " h eur", "eur hedged", "usd hedged",
        "gbp hedged", "chf hedged", "hdg", "currency hedged",
        "cubierto", "cubierta divisa", "cubierto divisa",
        "hgd", "eurhdg", "usdhdg", "gbphdg",  # BL-26: variantes OCR de hedge
        # BL-49/2: variantes truncadas (EURH, USDH, GBPH, CHFH) — el sufijo
        # H solo (sin DG) aparece en iShares/Candriam/GAM/GS. Solo se acepta
        # si va seguido de espacio + clase (ACC/INC/DIST) o final del nombre,
        # para evitar falsos positivos como "EURHIGH" (alta calificación).
        " eurh ", " eurh\t", " usdh ", " usdh\t",
        " gbph ", " gbph\t", " chfh ", " chfh\t",
        " eurh acc", " eurh inc", " eurh dist",
        " usdh acc", " usdh inc", " usdh dist",
        " gbph acc", " gbph inc", " gbph dist",
        " chfh acc", " chfh inc", " chfh dist",
    ]
    _UNHEDGED = [
        "unhedged", "sin cobertura", "no cubierto",
        "no hedged", "not hedged",               # BL-26: variantes EN explícitas
    ]
    # BL-26: Unhedged ANTES que Hedged — "unhedged" contiene "hedged" como substring
    if any(s in name_l for s in _UNHEDGED):
        return "Unhedged"
    if any(s in name_l for s in _HEDGED):
        return "Hedged"

    # BL-49/DRY: fallback al texto KIID delegado en classify_utils (Principio #2).
    # detect_currency_hedged_from_kiid() es el único punto de verdad para
    # patrones KIID — 18 patrones pre-compilados (H01-H10 / U01-U08).
    # Los patrones inline previos (v19) quedan eliminados aquí.
    if kiid_text:
        _ch_kiid, _ = detect_currency_hedged_from_kiid(kiid_text)
        if _ch_kiid:
            return _ch_kiid

    return None


# =============================================================
# DETECT_ACCUMULATION_POLICY
# =============================================================

def detect_accumulation_policy(
    fund_name: str,
    kiid_text: Optional[str] = None,
) -> Optional[str]:
    """Detecta política de distribución de rentas."""
    name_l = (fund_name or "").lower()

    _ACC_SIGNALS = [
        "acc", "accumulation", "acumulación", "acumulacion",
        "capitalización", "capitalizacion", "cap ", "(c)",
        "thesaurisant", "thesaurisierung", "re-invest",
    ]
    _DIST_SIGNALS = [
        "dist", "distribution", "distribución", "distribucion",
        "income", "dividend", "rte", "reparto",
        "ausschüttend", "distributing",
    ]

    for s in _ACC_SIGNALS:
        if s in name_l:
            return "Accumulation"
    for s in _DIST_SIGNALS:
        if s in name_l:
            return "Distribution"

    if kiid_text:
        kl = kiid_text.lower()
        if any(s in kl for s in [
            "acumula los rendimientos", "reinvierte los rendimientos",
            "accumulates income", "reinvests income",
        ]):
            return "Accumulation"
        if any(s in kl for s in [
            "distribuye los rendimientos", "reparte los rendimientos",
            "distributes income", "pays dividends",
        ]):
            return "Distribution"

    return None


# =============================================================
# PROFILE DERIVADO DEL SRRI  (v17 — añade Agresivo para SRRI=7)
# =============================================================

def derive_profile_from_srri(srri: Optional[int]) -> Optional[str]:
    """
    Mapeo SRRI → Profile.

    v17: SRRI=7 → Agresivo (diferenciado de SRRI 5-6 → Dinámico).
    El salto cualitativo de riesgo en SRRI=7 justifica categoría propia.
    """
    if srri is None:
        return None
    if srri <= 2:
        return "Conservador"
    if srri <= 4:
        return "Moderado"
    if srri <= 6:
        return "Dinámico"
    return "Agresivo"   # SRRI=7


# =============================================================
# DISPATCH POR NATURALEZA
# =============================================================

def _char_monetario(name_l: str, text_l: str, srri: Optional[int]) -> Dict[str, Any]:
    return {
        "Profile": derive_profile_from_srri(srri) or "Conservador",
        "Type": "Money Market",
        "Family": "Monetario",
        "Subtype": None,
        "Style_Profile": None,
        "Exposure_Bias": "Long Only",
    }


def _char_rf_corto(name_l: str, text_l: str, srri: Optional[int]) -> Dict[str, Any]:
    return {
        "Profile": derive_profile_from_srri(srri) or "Conservador",
        "Type": "Fixed Income",
        "Family": "Renta Fija Corto",
        "Subtype": None,
        "Style_Profile": None,
        "Exposure_Bias": "Long Only",
    }


def _char_rf_flexible(name_l: str, text_l: str, srri: Optional[int]) -> Dict[str, Any]:
    profile = derive_profile_from_srri(srri) or "Moderado"
    subtype = None
    if any(k in name_l for k in ["absolute return", "retorno absoluto", "total return"]):
        subtype = "Absolute Return"
    elif any(k in name_l for k in ["high yield", "hy "]):
        subtype = "High Yield"
    elif any(k in name_l for k in ["convertible", "convertibles"]):
        subtype = "Convertibles"
    return {
        "Profile": profile,
        "Type": "Fixed Income",
        "Family": None,
        "Subtype": subtype,
        "Style_Profile": None,
        "Exposure_Bias": None,
    }


def _char_rv(name_l: str, text_l: str, srri: Optional[int]) -> Dict[str, Any]:
    profile = derive_profile_from_srri(srri) or "Dinámico"
    # BL-29: detect_style_profile solo usa nombre; añadir KIID como segunda capa
    style = detect_style_profile(name_l)
    if style is None and text_l:
        style = detect_style_from_kiid(text_l)
    bias  = detect_exposure_bias(name_l) or "Long Only"
    t = "Index" if any(k in name_l for k in [
        "index fund", "fondo índice", "passive", "tracker",
        "etf", "ucits etf",
    ]) else "Equity"
    return {
        "Profile": profile,
        "Type": t,
        "Family": None,
        "Subtype": None,
        "Style_Profile": style,
        "Exposure_Bias": bias,
    }


def _char_mixtos(name_l: str, text_l: str, srri: Optional[int]) -> Dict[str, Any]:
    profile = derive_profile_from_srri(srri) or "Moderado"
    return {
        "Profile": profile,
        "Type": "Balanced",
        "Family": None,
        "Subtype": None,
        "Style_Profile": None,
        "Exposure_Bias": None,
    }


def _char_alternativo(name_l: str, text_l: str, srri: Optional[int]) -> Dict[str, Any]:
    profile = derive_profile_from_srri(srri) or "Dinámico"
    subtype = None
    bias    = None
    if any(k in name_l for k in ["long short", "long/short", "long-short"]):
        subtype = "Long/Short"
        bias    = "Long/Short"
    elif any(k in name_l for k in ["market neutral", "market-neutral"]):
        subtype = "Market Neutral"
        bias    = "Market Neutral"
    elif any(k in name_l for k in ["global macro", "macro"]):
        subtype = "Global Macro"
        bias    = "Macro"
    elif any(k in name_l for k in [
        "real estate", "real asset", "infrastructure", "commodity", "commodities",
    ]):
        subtype = "Real Assets"
    elif any(k in name_l for k in ["absolute return", "retorno absoluto", "total return"]):
        subtype = "Absolute Return"
    return {
        "Profile": profile,
        "Type": "Absolute Return",
        "Family": None,
        "Subtype": subtype,
        "Style_Profile": None,
        "Exposure_Bias": bias or "Directional",
    }


def _char_restantes(name_l: str, text_l: str, srri: Optional[int]) -> Dict[str, Any]:
    return {
        "Profile": derive_profile_from_srri(srri),
        "Type": None,
        "Family": None,
        "Subtype": None,
        "Style_Profile": None,
        "Exposure_Bias": None,
    }


def _char_default(name_l: str, text_l: str, srri: Optional[int]) -> Dict[str, Any]:
    return _char_restantes(name_l, text_l, srri)


_NATURE_DISPATCH = {
    "Monetario":              _char_monetario,
    "Renta Fija Corto Plazo": _char_rf_corto,
    "Renta Fija Flexible":    _char_rf_flexible,
    "Renta Variable":         _char_rv,
    "Mixtos":                 _char_mixtos,
    "Alternativo":            _char_alternativo,
    "Restantes":              _char_restantes,
    "Estructurado":           _char_restantes,
}


# =============================================================
# FUNCIÓN PRINCIPAL: characterize_fund()
# =============================================================

def characterize_fund(
    fund_name: str,
    kiid_text: Optional[str],
    fund_nature: str,
    srri: Optional[int] = None,
    pre_assigned: Optional[Dict[str, Any]] = None,
    benchmark_declared: Optional[str] = None,   # BL-27: para inferir Market_Cap_Focus
) -> Dict[str, Any]:
    """
    Caracterización secundaria completa de un fondo.

    v17: añade Investment_Focus y Credit_Quality al dict de resultado.
         Orden de operaciones revisado para que Sector_Focus reciba
         el Theme ya calculado (fix P14).

    Parámetros:
        fund_name:    nombre del fondo (fund_master.Fund_Name)
        kiid_text:    texto Raw_KIID_Text completo
        fund_nature:  Fund_Nature ya clasificada por el bloque
        srri:         SRRI extraído (opcional)
        pre_assigned: atributos ya asignados con precedencia

    Devuelve dict con todos los atributos cualitativos de v17.
    None indica que no fue posible determinar el atributo.
    """
    pre = pre_assigned or {}
    name_l = (fund_name or "").lower()
    text_l = (kiid_text or "").lower()

    result: Dict[str, Any] = {
        # ── identificación ────────────────────────────────────────
        "Profile":              None,
        "Type":                 None,
        "Family":               None,
        "Subtype":              None,
        "Style_Profile":        None,
        "Exposure_Bias":        None,
        # ── exposición ───────────────────────────────────────────
        "Geography":            None,
        "Theme":                None,
        "Investment_Universe":  None,
        "Investment_Focus":     None,   # v17 NUEVO
        "Market_Cap_Focus":     None,
        "Sector_Focus":         None,
        "Credit_Quality":       None,   # v17 NUEVO
        # ── estrategia ───────────────────────────────────────────
        "Is_ESG":               0,
        "Strategy":             None,
        "Benchmark_Type":       None,
        # ── estructura ───────────────────────────────────────────
        "Currency_Hedged":      None,
        "Accumulation_Policy":  None,
    }

    # ── 1. Geography (base para IU) ──────────────────────────────
    result["Geography"] = (
        pre.get("Geography")
        or detect_geography(name_l)
        or detect_geography_from_kiid(kiid_text)
    )

    # ── 2. Theme (base para IF y Sector_Focus) ───────────────────
    #    v17: si no hay tema → "Core/General" (no temático confirmado)
    #         NULL queda para "no procesado"
    detected_theme = (
        pre.get("Theme")
        or detect_theme_extended(name_l)
        or detect_theme_from_kiid(kiid_text)
    )
    if detected_theme is None and fund_nature not in _LIQUIDITY_NATURES:
        detected_theme = "Core/General"
    result["Theme"] = detected_theme

    # ── 3. Investment_Universe (v17 — solo geográfico) ───────────
    result["Investment_Universe"] = (
        pre.get("Investment_Universe")
        or detect_investment_universe(fund_nature, result["Geography"])
    )

    # ── 4. Investment_Focus (v17 NUEVO) ──────────────────────────
    result["Investment_Focus"] = (
        pre.get("Investment_Focus")
        or detect_investment_focus(fund_nature, result["Theme"])
    )

    # ── 5. Sector_Focus — recibe Theme ya calculado (fix P14) ────
    result["Sector_Focus"] = (
        pre.get("Sector_Focus")
        or detect_sector_focus(name_l, kiid_text, theme=result["Theme"])
    )

    # ── 6. Market_Cap_Focus (solo RV) ────────────────────────────
    if fund_nature == "Renta Variable":
        result["Market_Cap_Focus"] = (
            pre.get("Market_Cap_Focus")
            or detect_market_cap_focus(name_l, kiid_text, benchmark_declared)  # BL-27
        )

    # ── 7. Credit_Quality (v17 NUEVO) ────────────────────────────
    result["Credit_Quality"] = (
        pre.get("Credit_Quality")
        or detect_credit_quality(fund_nature, name_l, kiid_text)
    )

    # ── 8. Atributos independientes de la naturaleza ─────────────
    result["Is_ESG"] = max(
        int(pre.get("Is_ESG") or 0),
        detect_is_esg(fund_name),
        int(detect_esg_from_kiid(kiid_text) or 0),
    )
    result["Currency_Hedged"] = (
        pre.get("Currency_Hedged") or detect_currency_hedged(name_l, kiid_text)
    )
    result["Accumulation_Policy"] = (
        pre.get("Accumulation_Policy")
        or detect_accumulation_policy(fund_name, kiid_text)
    )

    # ── 9. Dispatch por naturaleza (Profile, Type, Subtype...) ───
    fn = _NATURE_DISPATCH.get(fund_nature, _char_default)
    nat_attrs = fn(name_l, text_l, srri)
    for k, v in nat_attrs.items():
        if result.get(k) is None and v is not None:
            result[k] = v

    # ── 10. pre_assigned con precedencia máxima ──────────────────
    for k, v in pre.items():
        if v is not None:
            result[k] = v

    # ── 11. Style_Profile: solo para RV ──────────────────────────
    if fund_nature != "Renta Variable" and result.get("Style_Profile") is None:
        result["Style_Profile"] = None   # explícito: NULL por diseño

    return result
