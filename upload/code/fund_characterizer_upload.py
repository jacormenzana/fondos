# -*- coding: utf-8 -*-
"""
proyecto1/core/fund_characterizer.py
=====================================
Módulo genérico de caracterización secundaria de fondos. v1.0

Responsabilidad única: dado (fund_name, kiid_text, fund_nature, srri),
devolver el dict completo de atributos cualitativos:
    Profile, Type, Family, Subtype, Style_Profile, Exposure_Bias,
    Geography, Theme, Is_ESG, Strategy, Benchmark_Type,
    Market_Cap_Focus, Sector_Focus, Currency_Hedged

Principios de diseño:
  1. Independiente del bloque de entrada — mismo resultado para el mismo
     fondo independientemente de si entró por RV, RESTANTES u otro bloque.
  2. Dispatch por Fund_Nature para atributos dependientes de la naturaleza
     (Profile, Type, Family) — la lógica específica se concentra aquí.
  3. Los bloques primarios conservan SOLO la heurística de clasificación
     de naturaleza (Fund_Nature). Toda caracterización secundaria se
     delega a este módulo.
  4. Nunca sobreescribe un valor ya asignado si no es None — el bloque
     puede pasar pre-asignaciones que tienen precedencia.

Cambios respecto a arquitectura anterior:
  - Elimina la sección "atributos universales" duplicada de los 6 bloques
  - Añade Market_Cap_Focus, Sector_Focus, Currency_Hedged como atributos nuevos
  - Amplía THEMATIC_MAP con variantes en español y nuevos temas (MedTech,
    Cybersecurity, Megatrends, Sustainability)
  - Añade detect_theme_from_kiid para cobertura via texto (objetivo: >20%)
  - Override universal de Fund_Nature='Estructurado' previo a todo bloque
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
    )


# =============================================================
# OVERRIDE UNIVERSAL — ESTRUCTURADO
# Debe ejecutarse ANTES de cualquier lógica de bloque.
# Si el texto KIID indica un producto estructurado, la naturaleza
# es Estructurado independientemente del bloque de entrada.
# =============================================================

_STRUCTURED_SIGNALS = [
    "autocallable", "autocall", "auto-callable",
    "barrier", "knock-in", "knock in",
    "nota estructurada", "structured note",
    "capital protected", "capital garantizado",
    "producto estructurado", "structured product",
    "certificado de inversión",
]


def is_structured_product(fund_name: str, kiid_text: Optional[str]) -> bool:
    """
    Detecta si el fondo es un producto estructurado.
    Override universal: precede a cualquier clasificación de bloque.
    """
    name_l = (fund_name or "").lower()
    text_l = (kiid_text or "")[:2000].lower()   # solo cabecera del KIID
    for sig in _STRUCTURED_SIGNALS:
        if sig in name_l or sig in text_l:
            return True
    return False


# =============================================================
# THEMATIC_MAP AMPLIADO
# Extiende el original de classify_utils con:
#   - Variantes en español
#   - MedTech / Medical Technology
#   - Cybersecurity / Ciberseguridad
#   - Megatrends / Megatendencias
#   - Sustainability (genérico)
#   - Consumer Staples / Food & Beverage
#   - Mobility / Transportation
# =============================================================

THEMATIC_MAP_EXTENDED: Dict[str, str] = {
    # Technology
    "technology": "Technology", "tech": "Technology",
    "tecnología": "Technology", "tecnologia": "Technology",
    "smart ind tec": "Technology", "information tech": "Technology",
    # Artificial Intelligence
    "artificial intelligence": "Artificial Intelligence",
    "artificial intelligenc": "Artificial Intelligence",
    "inteligencia artificial": "Artificial Intelligence",
    " ai ": "Artificial Intelligence",
    # Digital / Robotics
    "digital": "Digital",
    "robotics": "Robotics", "robotech": "Robotics",
    "robótica": "Robotics", "robotica": "Robotics",
    # MedTech / Healthcare
    "medtech": "Healthcare / MedTech",
    "medical tech": "Healthcare / MedTech",
    "medical technology": "Healthcare / MedTech",
    "healthcare": "Healthcare / MedTech",
    "health": "Healthcare / MedTech",
    "wellcare": "Healthcare / MedTech",
    "salud": "Healthcare / MedTech",
    "ciencias de la salud": "Healthcare / MedTech",
    # Biotechnology
    "biotec": "Biotechnology", "biotech": "Biotechnology",
    "biotecnología": "Biotechnology",
    # Cybersecurity
    "cybersecurity": "Cybersecurity",
    "ciberseguridad": "Cybersecurity",
    "cyber security": "Cybersecurity",
    "digital security": "Cybersecurity",
    "safety": "Cybersecurity",      # Thematics Safety
    # Climate / Clean Energy
    "climate": "Climate / Clean Energy",
    "clean energy": "Climate / Clean Energy",
    "renewable": "Climate / Clean Energy",
    "energía limpia": "Climate / Clean Energy",
    "transición energética": "Climate / Clean Energy",
    "net zero": "Climate / Clean Energy",
    "low carbon": "Climate / Clean Energy",
    # Water
    # "water" solo válido en nombre — señal demasiado genérica en texto KIID
    "pictet water": "Water",
    " water ": "Water",     # con espacios en nombre (evita "waterfall", "underwater")
    "water fund": "Water",
    "water eq": "Water",    # water equity
    "global water": "Water",
    "clean water": "Water",
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
    # Consumer / Brands
    "global brands": "Consumer Brands", "glob brands": "Consumer Brands",
    "consumer brand": "Consumer Brands",
    "food": "Consumer / Food & Beverage",
    "food & beverage": "Consumer / Food & Beverage",
    "alimentación": "Consumer / Food & Beverage",
    # Financials
    "financial": "Financials", "financials": "Financials",
    "finanzas": "Financials",
    # Mining / Gold
    "mining": "Mining", "gold": "Gold", "oro": "Gold",
    "precious metals": "Gold", "metales preciosos": "Gold",
    # Infrastructure
    "infrastructure": "Infrastructure",
    "infraestructura": "Infrastructure",
    # Megatrends
    "megatrend": "Megatrends", "megatendencia": "Megatrends",
    "disruptive": "Megatrends",
    # Genetic / Biomedical
    "genetic": "Biotechnology", "genomic": "Biotechnology",
    # Subscription Economy
    "subscription": "Digital", "subscr": "Digital",
    # Mobility
    "mobility": "Mobility", "movilidad": "Mobility",
    "autonomous": "Mobility", "electric vehicle": "Mobility",
}


def detect_theme_extended(name_l: str) -> Optional[str]:
    """Detecta tema usando THEMATIC_MAP_EXTENDED (más amplio que el original)."""
    for keyword, theme in THEMATIC_MAP_EXTENDED.items():
        if keyword in name_l:
            return theme
    return None


def detect_theme_from_kiid(kiid_text: Optional[str]) -> Optional[str]:
    """
    Detecta tema desde el texto KIID (ventana de objetivos).
    Complementa detect_theme_extended cuando el nombre no es suficiente.
    """
    if not kiid_text:
        return None
    s, e = _get_obj_bounds(kiid_text)
    w = _extract_window(kiid_text.lower(), s, e)

    # PRINCIPIO: solo frases compuestas inequívocas.
    # Señales de una sola palabra (tecnología, salud, oro, agua) son demasiado
    # genéricas — aparecen en contextos no temáticos.
    theme_text_signals = [
        # Technology — frases compuestas que implican temática tecnológica
        (["sector tecnológico", "empresas tecnológicas", "compañías tecnológicas",
          "technology companies", "tech sector", "technology sector",
          "tecnología de la información", "information technology",
          "empresas de tecnología"], "Technology"),
        # Healthcare — frases que implican inversión en salud como temática
        (["sector sanitario", "sector salud", "empresas del sector salud",
          "healthcare companies", "medical companies", "health sector",
          "compañías sanitarias", "empresas de salud",
          "sector de la salud"], "Healthcare / MedTech"),
        # MedTech
        (["tecnología médica", "medical technology", "medtech",
          "ciencias de la salud", "health sciences", "dispositivos médicos",
          "medical devices"], "Healthcare / MedTech"),
        # Cybersecurity — señales específicas y poco ambiguas
        (["ciberseguridad", "cybersecurity", "seguridad digital",
          "digital security", "cyber security"], "Cybersecurity"),
        # AI
        (["inteligencia artificial", "artificial intelligence",
          "machine learning", "aprendizaje automático",
          "deep learning"], "Artificial Intelligence"),
        # Climate / Clean Energy — frases compuestas
        (["energías renovables", "energía limpia", "clean energy", "energía verde",
          "transición energética", "energy transition",
          "energía sostenible", "sustainable energy"], "Climate / Clean Energy"),
        # Infrastructure — compuesta en español puede ser incidental; exigir contexto
        (["activos de infraestructura", "infrastructure assets",
          "infraestructuras cotizadas", "listed infrastructure",
          "inversión en infraestructura"], "Infrastructure"),
        # Gold — "metales preciosos" y "precious metals" son suficientemente específicos
        (["metales preciosos", "precious metals",
          "fondos de oro", "gold fund",
          "lingotes de oro", "gold bullion",
          "physical gold", "oro físico"], "Gold"),
        # Real Estate — frases compuestas
        (["sector inmobiliario", "real estate sector", "bienes raíces",
          "empresas inmobiliarias", "real estate companies",
          "mercado inmobiliario", "real estate market"], "Real Estate"),
        # Biotechnology
        (["biotecnología", "biotechnology", "ciencias de la vida",
          "life sciences", "empresas biotecnológicas"], "Biotechnology"),
        # Water — frases compuestas específicas (agua sola es demasiado genérica)
        (["recursos hídricos", "water resources", "water companies",
          "sector del agua", "water sector", "servicios de agua",
          "gestión del agua", "water management", "water utilities",
          "acceso al agua", "agua potable", "water fund",
          "tecnología del agua", "water technology",
          "tratamiento del agua", "water treatment",
          "infraestructura del agua", "water infrastructure"], "Water"),
    ]
    for signals, theme in theme_text_signals:
        if any(sig in w for sig in signals):
            return theme
    return None


# =============================================================
# NUEVOS ATRIBUTOS
# =============================================================

def detect_market_cap_focus(name_l: str, kiid_text: Optional[str] = None) -> Optional[str]:
    """Detecta enfoque de capitalización de mercado."""
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
        "blue chip", "grande capitalización",
        "grandes compañías",
    ]):
        return "Large Cap"
    # Detección por texto KIID
    if kiid_text:
        s, e = _get_obj_bounds(kiid_text)
        w = _extract_window(kiid_text.lower(), s, e)
        if any(k in w for k in [
            "pequeña capitalización", "small capitalisation",
            "small-cap companies", "small cap companies",
        ]):
            return "Small Cap"
        if any(k in w for k in [
            "gran capitalización", "large capitalisation",
            "large-cap companies",
        ]):
            return "Large Cap"
    return None


def detect_sector_focus(name_l: str, kiid_text: Optional[str] = None) -> Optional[str]:
    """
    Detecta foco sectorial (más granular que Theme, sin excluirlo).
    Aplica principalmente a fondos temáticos de RV.
    """
    # Usar tema detectado como punto de partida
    theme = detect_theme_extended(name_l)
    if theme in ("Technology", "Artificial Intelligence", "Digital",
                 "Robotics", "Cybersecurity"):
        return "Technology & Innovation"
    if theme in ("Healthcare / MedTech", "Biotechnology"):
        return "Healthcare & Life Sciences"
    if theme in ("Climate / Clean Energy", "Energy"):
        return "Energy & Resources"
    if theme in ("Infrastructure", "Real Estate"):
        return "Real Assets"
    if theme == "Financials":
        return "Financial Services"
    if theme in ("Consumer Brands", "Consumer / Food & Beverage"):
        return "Consumer"
    if theme in ("Gold", "Mining"):
        return "Materials & Mining"
    if theme == "Water":
        return "Utilities & Environment"
    return None


def detect_currency_hedged(name_l: str) -> Optional[str]:
    """Detecta política de cobertura de divisa."""
    # Cobertura explícita
    if any(k in name_l for k in [
        "eurhdg", "eurh", "usdhdg", "usdh", "chfhdg",
        "hedged", "hedg", "hdg", "currency hedged",
        "divisa cubierta", "cobertura divisa",
        "(h)", "h acc", "h inc", "h eur", "h usd",
    ]):
        return "Hedged"
    # Sin cobertura explícita — señal negativa
    if any(k in name_l for k in [
        "unhedged", "no hedge", "sin cobertura",
    ]):
        return "Unhedged"
    return None   # No determinable desde el nombre


# =============================================================
# LÓGICA ESPECÍFICA POR NATURALEZA (dispatch)
# Extraída literalmente de los bloques primarios, sin modificar
# la semántica — solo consolidada en un módulo único.
# =============================================================

def _char_renta_variable(name_l: str, text_l: str, srri: Optional[int]) -> Dict:
    r = {}
    # Profile
    if any(k in name_l for k in [
        "defensive", "low vol", "minimum volatility", "minimum vol", "min vol",
    ]):
        r["Profile"] = "Conservador"
    elif any(k in name_l for k in ["income", "dividend", "dividende", "dividends"]):
        r["Profile"] = "Moderado"
    else:
        r["Profile"] = "Dinámico"

    # Type / Subtype
    _passive_kws = [
        "gestión pasiva", "gestiona de forma pasiva", "gestiona de manera pasiva",
        "inversión pasiva", "replica el índice", "replicar el índice",
        "replicar la rentabilidad del índice", "replicación del índice",
        "seguimiento del índice", "index fund", "track the index",
        "replicate the index", "index tracking", "passively managed",
        "passive management",
        # Vanguard / pasivos con OCR
        "replicación de la rentabilidad", "replicar la rentabilidad del",
    ]
    if any(k in text_l for k in _passive_kws):
        r["Type"] = "Indexado"
        r["Subtype"] = "Fondo Indexado"
    if "etf" in text_l or "fondo cotizado" in text_l:
        r["Type"] = "Indexado"
        r["Subtype"] = "ETF"
    if not r.get("Type"):
        r["Type"] = "Gestión Activa"

    # Style_Profile / Exposure_Bias
    if any(k in name_l for k in ["low vol", "minimum volatility", "minimum vol", "min vol", "min volatil"]):
        r["Style_Profile"] = "Low Volatility"
        r["Exposure_Bias"] = "Low Volatility Bias"
    elif any(k in name_l for k in ["income", "dividend", "dividende", "dividends"]):
        r["Style_Profile"] = "Income"
        r["Exposure_Bias"] = "Income Bias"
    elif "quality" in name_l:
        r["Style_Profile"] = "Quality"
    elif any(k in name_l for k in ["growth", "wachstum", "crecim"]):
        r["Style_Profile"] = "Growth"
    elif "value" in name_l:
        r["Style_Profile"] = "Value"

    # Family
    theme = detect_theme_extended(name_l)
    r["Family"] = "RV Temática" if theme else "RV Core"
    return r


def _char_mixtos(name_l: str, text_l: str, srri: Optional[int]) -> Dict:
    r = {}
    # Profile
    if any(k in name_l for k in [
        "conservative", "defensive", "defensiv", "conservador", "def ",
    ]):
        r["Profile"] = "Conservador"
    elif any(k in name_l for k in [
        "balanced", "moderate", "moderado", "blced", "equilib", "yield p",
    ]):
        r["Profile"] = "Moderado"
    elif any(k in name_l for k in ["growth", "dynamic", "dinamic", "agresiv", "crecimiento"]):
        r["Profile"] = "Dinámico"
    else:
        r["Profile"] = "Moderado"

    # Type
    if any(k in name_l for k in [
        "target volatility", "risk controlled", "risk control", "volatility control",
    ]):
        r["Type"] = "Target Volatility"
    elif any(k in name_l for k in ["target outcome", "outcome"]):
        r["Type"] = "Target Outcome"
    elif any(k in name_l for k in ["tactical", "macro", "strategy"]):
        r["Type"] = "Tactical Allocation"
    else:
        r["Type"] = "Allocation"

    # Family
    if any(k in name_l for k in ["lifecycle", "life cycle", "target date"]):
        r["Family"] = "Lifecycle"
    elif "retirement" in name_l:
        r["Family"] = "Retirement"
    elif "income" in name_l:
        r["Family"] = "Income Oriented"
    else:
        r["Family"] = "Mixtos"

    # Style_Profile
    if r["Type"] == "Target Volatility":
        r["Style_Profile"] = "Risk Control"
    elif r["Type"] == "Tactical Allocation":
        r["Style_Profile"] = "Tactical"
    else:
        r["Style_Profile"] = "Strategic Allocation"
    return r


def _char_alternativo(name_l: str, text_l: str, srri: Optional[int]) -> Dict:
    r = {}
    # Type / Subtype / Style_Profile
    if any(k in name_l for k in ["relative value", "arbitrage", "arbit", "arb strat", "arb str"]):
        r.update({"Type": "Absolute Return", "Subtype": "Relative Value / Arbitrage", "Style_Profile": "Defensivo"})
    elif "market neutral" in name_l:
        r.update({"Type": "Absolute Return", "Subtype": "Market Neutral", "Style_Profile": "Defensivo"})
    elif any(k in name_l for k in ["long short", "long/short", "long-short"]):
        r.update({"Type": "Absolute Return", "Subtype": "Long/Short", "Style_Profile": "Defensivo"})
    elif any(k in name_l for k in ["global rates", "gl rates"]):
        r.update({"Type": "Absolute Return", "Subtype": "Global Rates", "Style_Profile": "Defensivo"})
    elif any(k in name_l for k in ["multi strategy", "multi-strategy", "multiassut"]):
        r.update({"Type": "Absolute Return", "Subtype": "Multi-Asset", "Style_Profile": "Defensivo"})
    elif "global macro" in name_l or "adagio" in name_l:
        r.update({"Type": "Absolute Return", "Subtype": "Global Macro", "Style_Profile": "Momentum"})
    elif any(k in name_l for k in ["managed futures", "cta", "systematic"]):
        r.update({"Type": "Systematic", "Subtype": "Managed Futures", "Style_Profile": "Momentum"})
    elif any(k in name_l for k in ["real estate", "property"]):
        r.update({"Type": "Real Assets", "Subtype": "Real Estate",
                  "Style_Profile": "Defensivo", "Exposure_Bias": "Real Estate Bias"})
    elif "infrastructure" in name_l:
        r.update({"Type": "Real Assets", "Subtype": "Infrastructure",
                  "Style_Profile": "Defensivo", "Exposure_Bias": "Infrastructure Bias"})
    elif any(k in name_l for k in ["commodities", "commodity", "gold", "precious metals"]):
        r.update({"Type": "Commodities", "Subtype": "Physical / Derivatives",
                  "Exposure_Bias": "Commodity Bias"})
    elif any(k in name_l for k in ["abs ret", "absret", "st absret", "absolute return"]):
        r.update({"Type": "Absolute Return", "Subtype": "Total Return Bond", "Style_Profile": "Defensivo"})
    else:
        r.update({"Type": "Absolute Return", "Style_Profile": "Defensivo"})

    # Family
    t = r.get("Type", "")
    r["Family"] = "Systematic" if t == "Systematic" else \
                  "Activos Reales" if t in ("Real Assets", "Commodities") else \
                  "Retorno Absoluto"

    # Exposure_Bias default
    if not r.get("Exposure_Bias") and t == "Absolute Return":
        r["Exposure_Bias"] = "Absolute Return Bias"

    # Profile — basado en SRRI + Type
    s = r.get("Subtype", "") or ""
    if t == "Commodities" or s.startswith("Physical"):
        r["Profile"] = "Dinámico"
    elif srri and srri >= 5:
        r["Profile"] = "Dinámico"
    elif t == "Real Assets":
        r["Profile"] = "Moderado"
    elif srri and srri in (3, 4):
        r["Profile"] = "Moderado"
    elif srri and srri <= 2:
        r["Profile"] = "Conservador"
    else:
        r["Profile"] = "Moderado"
    return r


def _char_monetario(name_l: str, text_l: str, srri: Optional[int]) -> Dict:
    r = {"Profile": "Conservador", "Style_Profile": "Defensivo", "Exposure_Bias": "Liquidity Bias"}
    # Type
    if any(k in name_l for k in ["government", "treasury", "sovereign", "gov liq", "gov prim", "public"]):
        r["Type"] = "Monetario Público"
    elif any(k in name_l for k in ["prime", "corporate", "credit", "crd", "corp"]):
        r["Type"] = "Monetario Privado"
    else:
        r["Type"] = "Monetario"
    # Family
    if "cnav" in name_l:
        r["Family"] = "CNAV"
    elif "lvnav" in name_l:
        r["Family"] = "LVNAV"
    elif "vnav" in name_l:
        r["Family"] = "VNAV"
    elif any(k in name_l for k in ["enhanced", "plus", "rend"]):
        r["Family"] = "Enhanced Cash"
    else:
        r["Family"] = "Monetario"
    # Fix: si Family=Monetario y Type=CNAV/LVNAV/VNAV, sincronizar
    if r.get("Family") == "Monetario" and r.get("Type") in ("CNAV", "LVNAV", "VNAV", "Enhanced Cash"):
        r["Family"] = r["Type"]
    return r


def _char_rf_flexible(name_l: str, text_l: str, srri: Optional[int]) -> Dict:
    r = {}
    # Profile
    if any(k in name_l for k in ["high yield", "opportunistic", "dynamic", "dinamic"]):
        r["Profile"] = "Dinámico"
    elif any(k in name_l for k in ["defensiv", "conserv", "securite", "sécurité"]):
        r["Profile"] = "Conservador"
    else:
        r["Profile"] = "Moderado"

    # Type / Subtype
    if "unconstrained" in name_l:
        r["Type"] = "Unconstrained"; r["Subtype"] = "Flexible Bond"
    elif any(k in name_l for k in ["absolute return", "abs ret", "absret"]):
        r["Type"] = "Absolute Return"
    elif any(k in name_l for k in ["total return", "tot ret"]):
        r["Type"] = "Total Return"
    elif any(k in name_l for k in ["millesima", "millesim", "milles select",
                                    "buy&watch", "buy & watch", "buywat",
                                    "target 20", "credit 20", "cred 20"]):
        r["Type"] = "Target Maturity"
    else:
        r["Type"] = "Renta Fija Flexible"

    # Style_Profile / Exposure_Bias
    if any(k in name_l for k in ["high yield", "hy", "opportunistic", "opportunist",
                                   "credit opport", "yield enhancement"]):
        r["Style_Profile"] = "Income"; r["Exposure_Bias"] = "Credit Bias"; r["Subtype"] = "Opportunistic"
    elif any(k in name_l for k in ["income", "rend", "rendement"]):
        r["Style_Profile"] = "Income"; r["Exposure_Bias"] = "Income Bias"
    elif any(k in name_l for k in ["low volatility", "low vol", "capital preservation",
                                    "defensiv", "securite"]):
        r["Style_Profile"] = "Low Volatility"; r["Exposure_Bias"] = "Low Volatility Bias"
    else:
        r["Style_Profile"] = "Defensivo"; r["Exposure_Bias"] = "Duration Bias"

    # Family
    if any(k in name_l for k in ["strategic", "tactical", "opportunist", "millesima"]):
        r["Family"] = "Flexible Estratégico"
    elif any(k in name_l for k in ["high yield", " hy ", " hy bd"]):
        r["Family"] = "RF High Yield"
    elif any(k in name_l for k in ["emerging", "em debt", "em mkt", "emerg mkt",
                                    "emergentes", "em bond"]):
        r["Family"] = "RF Emergentes"
    elif any(k in name_l for k in ["inflation", "inflat", "infl link"]):
        r["Family"] = "RF Inflación"; r["Theme"] = "Inflación"
    else:
        r["Family"] = "Renta Fija Flexible"

    # Subtype divisa
    if any(k in name_l for k in ["multicurrency", "multi-currency", "multidivisa"]):
        r["Subtype"] = f"{r.get('Subtype')} | Multi-Currency" if r.get("Subtype") else "Multi-Currency"
    return r


def _char_rf_corto(name_l: str, text_l: str, srri: Optional[int]) -> Dict:
    r = {"Profile": "Conservador", "Family": "Renta Fija Corto Plazo",
         "Style_Profile": "Defensivo", "Exposure_Bias": "Duration Bias"}
    # Profile
    if any(k in name_l for k in ["enhanced cash", "money plus", "income"]):
        r["Profile"] = "Moderado"

    # Type / Subtype
    if any(k in name_l for k in ["floating rate", "floating", "floater", " frn "]):
        r["Type"] = "Floating Rate CP"; r["Subtype"] = "Floating Rate Notes"
        r["Exposure_Bias"] = "Rate Reset Bias"
    elif any(k in name_l for k in ["government", "treasury", "sovereign", "gov bd", "gov bond"]):
        r["Type"] = "Deuda Pública CP"
    elif any(k in name_l for k in ["corporate", "credit", "corp", "crd", "crdt",
                                    "covered", "pfandbrief"]):
        r["Type"] = "Crédito CP"
    else:
        r["Type"] = "Renta Fija Corto Plazo"

    # Subtype low duration
    if any(k in name_l for k in ["ultra short", "ult sh", "ul sh", "low dur",
                                   "low duration", "0-1", "0-2", "0-3"]):
        r["Subtype"] = "Low Duration"; r["Exposure_Bias"] = "Duration Bias"

    # Guardrail: High Yield no en RF_Corto
    if "high yield" in name_l or "high-yield" in name_l:
        r["Type"] = None; r["Subtype"] = None; r["Exposure_Bias"] = None

    # Style_Profile
    if any(k in name_l for k in ["income", "enhanced cash", "money plus"]):
        r["Style_Profile"] = "Income"
        if not r.get("Exposure_Bias"):
            r["Exposure_Bias"] = "Income Bias"
    return r


def _char_estructurado(name_l: str, text_l: str, srri: Optional[int]) -> Dict:
    r = {"Profile": "Moderado"}
    if any(k in name_l + text_l for k in ["autocallable", "autocall", "auto-callable"]):
        r["Type"] = "Autocallable"
        r["Subtype"] = "Barrier / Digital"
        r["Exposure_Bias"] = "Barrier Risk"
    elif any(k in name_l + text_l for k in [
        "capital protected", "capital garantizado", "capital protegido"
    ]):
        r["Type"] = "Capital Protegido"
        r["Exposure_Bias"] = "Capital Protection"
    else:
        r["Type"] = "Estructurado"
    r["Family"] = "Estructurados"
    r["Style_Profile"] = "Defensivo"
    return r


def _char_default(name_l: str, text_l: str, srri: Optional[int]) -> Dict:
    """Fallback: sin lógica específica por naturaleza."""
    return {"Profile": detect_profile_from_srri(srri)}


_NATURE_DISPATCH = {
    "Renta Variable":         _char_renta_variable,
    "Mixtos":                 _char_mixtos,
    "Alternativo":            _char_alternativo,
    "Monetario":              _char_monetario,
    "Renta Fija Flexible":    _char_rf_flexible,
    "Renta Fija Corto Plazo": _char_rf_corto,
    "Estructurado":           _char_estructurado,
}



# =============================================================
# INVESTMENT_UNIVERSE
# Consolida la dimensión de amplitud del universo de inversión,
# hoy fragmentada entre Geography y Theme.
# Derivado de atributos ya calculados — no requiere texto KIID.
# =============================================================

def detect_investment_universe(
    fund_nature: str,
    geography: Optional[str],
    theme: Optional[str],
    fund_type: Optional[str] = None,
) -> Optional[str]:
    """
    Deriva el universo de inversión a partir de la combinación de
    Fund_Nature, Geography y Theme ya establecidos.

    Valores:
        Global      — universo amplio sin restricción geográfica ni sectorial
        Regional    — región geográfica específica (Europa, Asia, EM...)
        Country     — un único país o mercado (China, Japón, EEUU)
        Sector      — sector específico con cobertura geográfica amplia
        Thematic    — temático transversal (Water, Climate, Megatrends)
        Liquidity   — instrumentos de mercado monetario / muy corto plazo
    """
    # Liquidez: fondos de muy corto plazo no tienen "universo" de inversión
    if fund_nature in ("Monetario", "Renta Fija Corto Plazo"):
        return "Liquidity"

    # Temático: universo definido por un tema, no por geografía
    _SECTOR_THEMES = {
        "Technology", "Artificial Intelligence", "Digital", "Robotics",
        "Healthcare / MedTech", "Biotechnology", "Cybersecurity",
        "Climate / Clean Energy", "Energy", "Water",
        "Consumer Brands", "Consumer / Food & Beverage",
        "Financials", "Mining", "Gold", "Insurance",
        "Silver Economy", "Mobility", "Megatrends",
    }
    _CROSS_THEMATIC = {
        "Infrastructure", "Real Estate",
    }
    if theme and theme in _SECTOR_THEMES:
        return "Sector"
    if theme and theme in _CROSS_THEMATIC:
        return "Thematic"
    if theme:
        return "Thematic"   # tema no clasificado → Thematic genérico

    # Country: geografías de un solo país
    _COUNTRY_GEOS = {
        "China", "Japón", "India", "Latinoamérica",
        "Europa del Este", "Rusia",
    }
    if geography in _COUNTRY_GEOS:
        return "Country"

    # Regional: regiones sin restricción sectorial
    _REGIONAL_GEOS = {
        "Europa", "Asia", "Emergentes", "EEUU",
    }
    if geography in _REGIONAL_GEOS:
        return "Regional"

    # Global: cobertura mundial
    if geography == "Global":
        return "Global"

    return None   # No determinable


# =============================================================
# ACCUMULATION_POLICY — AMPLIACIÓN DE COBERTURA
# Detecta política de distribución desde el nombre del fondo.
# Complementa lo ya extraído por kiid_parser desde el texto KIID.
# Cobertura actual: 15.9% (510/3204). Estimado tras mejora: ~78%.
# =============================================================

def detect_accumulation_policy(
    fund_name: str,
    kiid_text: Optional[str] = None,
) -> Optional[str]:
    """
    Detecta si el fondo acumula o distribuye rentas.
    Opera principalmente sobre el nombre del fondo (sufijos de clase).

    Valores:
        ACCUMULATION  — fondo de acumulación (reinvierte rentas)
        DISTRIBUTION  — fondo de distribución (reparte rentas)
    """
    name_l = (fund_name or "").lower()

    # Señales de distribución — precedencia sobre acumulación
    # "inc" ambiguo (también en "income equity") → solo cuando es sufijo de clase
    _DIST_SIGNALS = [
        " inc",        # clase Inc (al final del nombre o antes del acc)
        " dist",       # clase Dist
        " distribution",
        " distribut",
        "rend",        # fondos FR de distribución (rendement)
        "ausschütt",   # alemán: ausschüttend
        " in ",        # clase " In " como sufijo de clase (no en "income")
        " in$",        # termina en " in"
        "distribu",
        "pay out",     # EN payout
        " yd ",        # yield distribution
        " id ",        # income distribution
    ]
    import re
    for sig in _DIST_SIGNALS:
        if sig.startswith(" ") and sig.endswith("$"):
            if re.search(sig, name_l):
                return "DISTRIBUTION"
        elif sig in name_l:
            return "DISTRIBUTION"

    # Señales de acumulación
    _ACC_SIGNALS = [
        " acc",        # clase Acc
        " ac ",        # variante
        "acum",        # español/portugués
        "capitaliz",   # "capitalización"
        "thesaur",     # FR: thésaurisation
        "kapital",     # DE
        "reinvest",    # reinversión explícita
    ]
    for sig in _ACC_SIGNALS:
        if sig in name_l:
            return "ACCUMULATION"

    return None   # No determinable desde el nombre


# =============================================================
# FUNCIÓN PRINCIPAL
# =============================================================

def characterize_fund(
    fund_name: str,
    kiid_text: Optional[str],
    fund_nature: str,
    srri: Optional[int] = None,
    pre_assigned: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Caracterización secundaria completa de un fondo.

    Parámetros:
        fund_name:    nombre del fondo (tal como aparece en fund_master)
        kiid_text:    texto completo del KIID/DDF (Raw_KIID_Text)
        fund_nature:  naturaleza ya clasificada (Fund_Nature)
        srri:         valor SRRI ya extraído (opcional — para árbitro de Profile)
        pre_assigned: dict con valores ya asignados que tienen precedencia
                      (el módulo solo rellena los None)

    Devuelve dict con todos los atributos cualitativos. Los valores None
    indican que no fue posible determinar el atributo.
    """
    pre = pre_assigned or {}
    name_l = (fund_name or "").lower()
    text_l = (kiid_text or "").lower()

    result: Dict[str, Any] = {
        "Profile":              None,
        "Type":                 None,
        "Family":               None,
        "Subtype":              None,
        "Style_Profile":        None,
        "Exposure_Bias":        None,
        "Geography":            None,
        "Theme":                None,
        "Is_ESG":               0,
        "Strategy":             None,
        "Benchmark_Type":       None,
        "Market_Cap_Focus":     None,
        "Sector_Focus":         None,
        "Currency_Hedged":      None,
        "Investment_Universe":  None,
        "Accumulation_Policy":  None,
    }

    # ── 1. Atributos independientes de la naturaleza ─────────────────
    result["Geography"]       = (pre.get("Geography") or
                                  detect_geography(name_l) or
                                  detect_geography_from_kiid(kiid_text))

    result["Theme"]           = (pre.get("Theme") or
                                  detect_theme_extended(name_l) or
                                  detect_theme_from_kiid(kiid_text))

    result["Is_ESG"]          = max(
                                    int(pre.get("Is_ESG") or 0),
                                    detect_is_esg(fund_name),
                                    int(detect_esg_from_kiid(kiid_text) or 0),
                                )

    result["Currency_Hedged"] = pre.get("Currency_Hedged") or detect_currency_hedged(name_l)

    result["Market_Cap_Focus"]= pre.get("Market_Cap_Focus") or detect_market_cap_focus(name_l, kiid_text)

    result["Sector_Focus"]    = pre.get("Sector_Focus") or detect_sector_focus(name_l, kiid_text)

    # Accumulation_Policy: ampliar cobertura desde nombre
    result["Accumulation_Policy"] = (
        pre.get("Accumulation_Policy") or
        detect_accumulation_policy(fund_name, kiid_text)
    )

    # ── 2. Atributos dependientes de la naturaleza (dispatch) ────────
    fn = _NATURE_DISPATCH.get(fund_nature, _char_default)
    nat_attrs = fn(name_l, text_l, srri)

    for k, v in nat_attrs.items():
        if result.get(k) is None and v is not None:
            result[k] = v

    # Aplicar pre_assigned con precedencia
    for k, v in pre.items():
        if v is not None:
            result[k] = v

    # ── 3. Enriquecimiento desde texto KIID (fallback) ───────────────
    kiid_attrs = detect_kiid_attributes(kiid_text or "", fund_nature, result)
    for k, v in kiid_attrs.items():
        if not result.get(k) and v:
            result[k] = v

    # ── 4. Árbitros finales ──────────────────────────────────────────
    if result.get("Profile") is None:
        result["Profile"] = detect_profile_from_srri(srri)

    # Style_Profile: "Defensivo" solo aplica a Profile, no se almacena en Style_Profile
    if result.get("Style_Profile") == "Defensivo":
        result["Style_Profile"] = None
    if result.get("Style_Profile") is None:
        sp = detect_style_profile(name_l) or detect_style_from_kiid(kiid_text)
        if sp and sp != "Defensivo":
            result["Style_Profile"] = sp

    if result.get("Exposure_Bias") is None:
        result["Exposure_Bias"] = detect_exposure_bias(name_l, fund_nature)

    # Strategy y Benchmark_Type
    result["Strategy"]        = detect_strategy(
        None, result.get("Subtype"), name_l
    )
    result["Benchmark_Type"]  = detect_benchmark_type(None, None)

    # Geography fallback: fondos temáticos sin geo → Global
    if result.get("Geography") is None and result.get("Theme") is not None:
        result["Geography"] = "Global"

    # Investment_Universe: derivado de Geography + Theme + Fund_Nature
    # Se calcula al final cuando Geography y Theme ya están resueltos
    if not result.get("Investment_Universe"):
        result["Investment_Universe"] = detect_investment_universe(
            fund_nature=fund_nature,
            geography=result.get("Geography"),
            theme=result.get("Theme"),
            fund_type=result.get("Type"),
        )

    return result
