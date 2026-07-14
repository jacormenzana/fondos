# proyecto1/tests/test_geography.py
# -*- coding: utf-8 -*-
"""
Tests unitarios de classify_utils.detect_geography() y
detect_geography_from_kiid() (FIX-GEO-NAME-1 / FIX-GEO-KIID-1, 2026-07-05).

Auditoría corpus-wide (crossValidateFundAttribute aplicado a Geography)
encontró bugs de raíz en AMBOS extractores, mucho más severos que los ya
corregidos para Asset_Currency/Fund_Currency:

- Nombre: 3 reglas de fallback confundían la DENOMINACIÓN DE DIVISA de la
  clase de participación (" usd ", "usdh", " eur ") con la geografía de
  inversión -- la misma confusión share-class-vs-asset. Y "us " como
  substring sin límite de palabra cazaba JAN**US** (Janus Henderson) y
  **PLUS** (Amundi) como señal EEUU.
- KIID: fragmentos de índice de referencia ("s&p 500", "dow jones", etc.)
  aparecían como UN componente de un índice compuesto multi-región, nunca
  como el objetivo declarado; y la señal bare "estados unidos"/"norteamerica"
  no filtraba negación, detalle de emisor de instrumento, ni enumeración
  multi-región.

Corpus-wide: 324 -> 25 desacuerdos nombre/KIID (de 981/619 fondos con ambas
señales) tras estos fixes. Ver SESSION_SUMMARY para el detalle.
"""

import os
import sys
import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', 'core'))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from classify_utils import (
    detect_geography,
    detect_geography_from_kiid,
    detect_geography_from_kiid_product_name,
    validate_geography_universe,
)


def _kiid(objective_text):
    """Envuelve un fragmento de texto objetivo con suficiente relleno para
    que caiga dentro de la ventana objetivo por defecto (formato UNKNOWN,
    200-4500) que usa _get_obj_bounds() cuando el texto no contiene ninguna
    de las cabeceras DDF/KIID reconocidas."""
    filler = "x" * 250
    return filler + " " + objective_text


# ---------------------------------------------------------------------------
# detect_geography (nombre) — casos básicos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("BGF JAPAN SMALL COMPANY A2 USD", "Japón"),
    ("UBS CHINA OPPORTUNITY USD ACC", "China"),
    ("ROBECO INDIAN EQUITIES D EUR", "India"),
    ("BGF LATIN AMERICA A2 USD ACC", "Latinoamérica"),
    ("JPM EUROPE STRATEGIC VALUE A", "Europa"),
    ("PICTET GLOBAL MEGATREND SEL P", "Global"),
    ("SOME FUND WITH NO GEO SIGNAL", None),
    (None, None),
    ("", None),
])
def test_basic_geography_extraction(name, expected):
    name_l = (name or "").lower()
    assert detect_geography(name_l) == expected


@pytest.mark.parametrize("name,expected", [
    # FIX-GEO-6 (2026-07-05): países nórdicos individuales en el propio
    # nombre del fondo -- "nordic" ya cubría el bloque regional, pero no
    # el gentilicio/abreviatura de país sueco/noruego (bug real: Nordea 1
    # Swedish/Norwegian Short-Term Bond, Geography quedaba NULL pese a que
    # el nombre lo dice explícitamente).
    ("NORDEA 1 SWED. SHORT-T. BOND E", "Europa"),
    ("NORDEA 1 NORW. SHORT-T. BOND E", "Europa"),
    ("NORDEA 1 SWDISH ST BN BP EU AC", "Europa"),
    ("NORDEA 1 NORW.SHORT-T. BOND BP", "Europa"),
])
def test_swedish_norwegian_country_name_maps_to_europa(name, expected):
    assert detect_geography(name.lower()) == expected


# ---------------------------------------------------------------------------
# FIX-GEO-NAME-1: denominación de divisa de la clase de participación no es
# señal de geografía de inversión (share-class-vs-asset, igual que Currency).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    # Bug real: "MFS EUROPE RESEARCH A1 USD ACC" -- nombre dice
    # explícitamente Europa, pero la clase USD lo desviaba a EEUU.
    ("MFS EUROPE RESEARCH A1 USD ACC", "Europa"),
    ("PIMCO GLOBAL BOND I USD ACC", "Global"),
    ("PIMCO GLOBAL BOND E USD INC", "Global"),
    # "usdh" ya no fuerza Global -- sin otra señal, None.
    ("SOME FUND NAME USDH ACC", None),
])
def test_currency_denomination_is_not_geography_signal(name, expected):
    assert detect_geography(name.lower()) == expected


# ---------------------------------------------------------------------------
# FIX-GEO-NAME-1: "us" requiere límite de palabra real (borde izquierdo) --
# antes "us " como substring cazaba palabras que solo TERMINAN en "us".
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "JANUS HH GL TECH LEAD X2 USD AC",       # Janus Henderson, no EEUU
    "JPMORGAN GLOBAL FOCUS FUND D",          # "focus " contiene "us "
    "AMUNDI REND PLUS RESP P EUR AC",        # "plus " contiene "us "
])
def test_us_word_boundary_does_not_match_inside_other_words(name):
    result = detect_geography(name.lower())
    assert result != "EEUU"


@pytest.mark.parametrize("name,expected", [
    ("JPM US VALUE A USD ACC", "EEUU"),
    ("BGF US BASIC VALUE A2 EUH ACC", "EEUU"),
    ("VGD US INV GDE CR IDX EUR ACC", "EEUU"),
])
def test_us_word_boundary_still_matches_genuine_us_funds(name, expected):
    assert detect_geography(name.lower()) == expected


# ---------------------------------------------------------------------------
# FIX-GEO-NAME-2 (2026-07-12): "\bus\b" (señal débil) se evalúa DESPUÉS
# de los checks europeos para que fondos con "EUROPEAN" + "US AC" en el
# nombre retornen Europa, no EEUU.
# Caso real: MFS EUROPEAN RESEARCH W1 US AC (LU1123736750) -- generaba
# GEOGRAPHY_NAME_KIID_MISMATCH (EEUU vs Europa) por el código de clase
# "W1 US AC", aunque el fondo invierte en renta variable europea.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected,desc", [
    ("MFS EUROPEAN RESEARCH W1 US AC", "Europa",
     "EUROPEAN + 'US AC' share class code → Europa"),
    ("INVESCO PAN EUROPEAN HIGH INC E US ACC", "Europa",
     "PAN EUROPEAN + 'US ACC' class code → Europa"),
    ("ALLIANZ EUROPEAN EQUITY A US ACC", "Europa",
     "'EUROPEAN' + 'US ACC' class code → Europa"),
    ("NORDEA EUROPEAN HIGH YIELD E USDH ACC", "Europa",
     "'EUROPEAN' + USDH class suffix → Europa"),
    ("JPM US EQUITY A EUR ACC", "EEUU",
     "strong 'US EQ' → EEUU (no false positive)"),
    ("PIMCO TOTAL RETURN E US ACC", "EEUU",
     "no European signal, standalone 'US' → EEUU via weak path"),
])
def test_european_signal_beats_weak_us_share_class_code(name, expected, desc):
    """FIX-GEO-NAME-2: señal europea explícita en nombre tiene prioridad sobre
    el código de clase 'US' (equivalente USD) que aparece como sufijo."""
    result = detect_geography(name.lower())
    assert result == expected, (
        f"Expected {expected!r} for '{desc}', got {result!r}. Name: {name!r}"
    )


# ---------------------------------------------------------------------------
# detect_geography_from_kiid — señales explícitas de objetivo
# ---------------------------------------------------------------------------

def test_kiid_explicit_objective_signals():
    assert detect_geography_from_kiid(_kiid(
        "el subfondo invierte principalmente en japón."
    )) == "Japón"
    assert detect_geography_from_kiid(_kiid(
        "el fondo invierte principalmente en europa en renta variable."
    )) == "Europa"
    assert detect_geography_from_kiid(_kiid(
        "el fondo invierte a nivel mundial en renta variable diversificada."
    )) == "Global"


def test_kiid_us_domicile_declaration_still_detected():
    """Declaración explícita de domicilio/actividad económica en EEUU (sin
    verbo 'invierte en') -- señal legítima, debe seguir aceptándose."""
    assert detect_geography_from_kiid(_kiid(
        "el subfondo invierte en acciones de compañías domiciliadas o que "
        "desarrollen la mayor parte de su actividad económica en estados "
        "unidos."
    )) == "EEUU"


# ---------------------------------------------------------------------------
# FIX-GEO-KIID-1: fragmentos de índice de referencia retirados -- aparecen
# como componente de un benchmark compuesto multi-región, no como objetivo.
# ---------------------------------------------------------------------------

def test_benchmark_fragment_no_longer_triggers_eeuu():
    """'JPM Global Allocation': el AI usa un índice compuesto que incluye
    S&P 500 (36%) + FTSE World ex-US (24%) + US Treasury (24%) + FTSE
    Non-USD World -- ningún componente aislado representa el objetivo.
    Antes del fix, "s&p 500"/"us treasury" (retirados de la lista EEUU)
    devolvían 'EEUU'; ahora el único fragmento restante que sí matchea es
    "ftse world" (señal Global legítima, ya existente), nunca 'EEUU'."""
    result = detect_geography_from_kiid(_kiid(
        "el ai se referirá a un índice de referencia compuesto, que "
        "incluirá el s&p 500 (36%), el ftse world (ex-us) (24%), el 5 yr "
        "us treasury note (24%) y el ftse non-usd world (16%)."
    ))
    assert result != "EEUU"
    assert result == "Global"


# ---------------------------------------------------------------------------
# FIX-GEO-KIID-1: negación -- "fuera de estados unidos" es lo opuesto de un
# objetivo EEUU.
# ---------------------------------------------------------------------------

def test_kiid_negation_excluded():
    """Bug real (WCM Select Global Growth Equity): 'empresas... fuera de
    los estados unidos' -- el fondo invierte FUERA de EEUU, no en EEUU."""
    assert detect_geography_from_kiid(_kiid(
        "el fondo selecciona empresas constituidas, con sede o que "
        "desarrollan una parte importante de su negocio fuera de los "
        "estados unidos."
    )) is None


# ---------------------------------------------------------------------------
# FIX-GEO-KIID-1: detalle de emisor/instrumento -- describe la nacionalidad
# del EMISOR de un instrumento, no la geografía declarada del fondo.
# ---------------------------------------------------------------------------

def test_kiid_issuer_detail_excluded():
    """Bug real (JPM Global Bond Opportunities): los MBS pueden haber sido
    'emitidos por agencias (organismos cuasigubernamentales de estados
    unidos)' -- detalle del emisor de un instrumento dentro de un mandato
    global de renta fija, no el objetivo del fondo."""
    assert detect_geography_from_kiid(_kiid(
        "los mbs, que pueden haber sido emitidos por agencias (organismos "
        "cuasigubernamentales de estados unidos) o por otras entidades "
        "distintas de agencias, representan títulos de deuda."
    )) is None


# ---------------------------------------------------------------------------
# FIX-GEO-KIID-1: enumeración multi-región -- 2+ regiones nombradas ==
# mandato Global, no una única región dominante.
# ---------------------------------------------------------------------------

def test_kiid_multi_region_enumeration_maps_to_global():
    """Bug real (GS Global High Yield): 'empresas norteamericanas y
    europeas' -- dos regiones enumeradas, el fondo es Global, no EEUU."""
    assert detect_geography_from_kiid(_kiid(
        "la cartera pondrá el énfasis en valores de renta fija de empresas "
        "norteamericanas y europeas con calificación inferior a grado de "
        "inversión."
    )) == "Global"


# ---------------------------------------------------------------------------
# FIX-GEO-8 (2026-07-13): detect_geography — tres fixes en detect_geography
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected,desc", [
    # "emerging europe" → Europa del Este BEFORE generic "emerging" → Emergentes.
    # Real case: SISF EMERGING EUROPE (LU0106817157/104) was mis-detected as Emergentes.
    ("SISF EMERGING EUROPE A ACC USD", "Europa del Este",
     "SISF EMERGING EUROPE → Europa del Este, not Emergentes"),
    ("AMUNDI EMERG EUROP EQ I EUR ACC", "Europa del Este",
     "compact 'emerg europ' → Europa del Este"),
    # Generic "emerging" still routes to Emergentes when no "europe" qualifier.
    ("PIMCO EMERG MARKETS BOND I EUR", "Emergentes",
     "generic emerging without europe → Emergentes unchanged"),
    # "us top" / "us div" in strong EEUU list — fires before "deutsch"/"german" Europa branch.
    # Real case: DEUTSCHE II US TOP DIVID (LU0781238778/935/743) was mis-detected as Europa.
    ("DEUTSCHE II US TOP DIVID LC ACC", "EEUU",
     "DEUTSCHE II US TOP DIVID → EEUU via 'us top', not Europa via 'deutsch'"),
    ("BGF US DIVID FOCUS A2 EUR ACC", "EEUU",
     "us div strong signal → EEUU"),
    # "deutsch" still maps to Europa when there is no US signal.
    ("DEUTSCHE INVEST EUROP BOND A", "Europa",
     "deutsch without US signal → Europa unchanged"),
    # " euro" hedge-suffix fix: EUROH share-class code must NOT trigger Europa.
    # Real case: PIMCO US HY BND INV EUROH (IE0032593158) was mis-detected as Europa.
    ("PIMCO US HY BND INV EUROH ACC", "EEUU",
     "EUROH suffix → NOT Europa; standalone 'us' wins"),
    ("PIMCO EUROP HY BD EUROH ACC", "Europa",
     "genuine EUROPEAN + EUROH: European signal from 'europ' still fires"),
    # " euro" without hedge suffix still signals Europa (no regression on EUROBOND names).
    ("AMUNDI EURO CORPORATE BOND I", "Europa",
     "' euro' genuine → Europa via 'euro ' keyword"),
])
def test_fix_geo_8_detect_geography(name, expected, desc):
    """FIX-GEO-8: emerging europe, us top/div, euroh hedge-suffix fixes."""
    result = detect_geography(name.lower())
    assert result == expected, f"Expected {expected!r} for '{desc}', got {result!r}"


def test_kiid_returns_none_without_match():
    assert detect_geography_from_kiid(_kiid(
        "el fondo invierte en una amplia gama de activos diversificados "
        "sin ningún mandato geográfico concreto."
    )) is None
    assert detect_geography_from_kiid(None) is None
    assert detect_geography_from_kiid("") is None


# ---------------------------------------------------------------------------
# FIX-GEO-3 (2026-07-05): fallback -- nombre oficial del subfondo declarado
# en la línea "Producto:"/"PRODUCTO" del propio KIID, a menudo en inglés
# aunque el resto del documento esté en español. Solo se invoca cuando la
# ventana objetivo ya agotó su búsqueda sin señal (detect_geography_from_kiid
# se prueba directamente para eso; detect_geography_from_kiid_product_name
# se prueba aislado para los casos de extracción/guardas).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kiid_text,expected", [
    # Bug real: AXA IM FIIS EURP SHOR DUR HY F -- Fund_Name abreviado
    # ("EURP") pierde la señal, pero el KIID declara el nombre completo.
    ("PRODUCTO AXA IM FIIS Europe Short Duration High Yield, un subfondo "
     "de AXA IM Fixed Income Investment Strategies, clase F EUR "
     "(LU0658026603)", "Europa"),
    # Bug real: AXA WF EM MK S D BND A USD INC -- "EM MK" no coincide con
    # "em mkt" (abreviatura sin la "t" final) en ningún otro chequeo.
    ("Producto AXA World Funds - Emerging Markets Short Duration Bonds A "
     "Distribution USD", "Emergentes"),
    ("Producto Global High Yield Portfolio\nun subfondo de AB FCP I",
     "Global"),
    ("PRODUCTO Vanguard Japan Stock Index Fund (el «Fondo») - EUR "
     "Accumulation Shares", "Japón"),
    ("Producto Goldman Sachs Emerging Markets Equity Portfolio (la "
     "«Cartera»), un subfondo de Goldman Sachs Funds SICAV", "Emergentes"),
])
def test_product_line_extraction_basic(kiid_text, expected):
    assert detect_geography_from_kiid_product_name(kiid_text) == expected


def test_product_line_currency_denomination_not_geography():
    """Mismo error de raíz que en el nombre (FIX-GEO-NAME-1), pero en la
    línea Producto: "US Dollar High Yield Bond Fund" es una convención de
    DIVISA BASE de BlackRock/BGF, no un mandato geográfico -- confirmado
    leyendo el KIID real de LU0046676465 (BGF USD HIGH YIELD BOND):
    invierte explícitamente en emisores estadounidenses Y NO
    estadounidenses."""
    result = detect_geography_from_kiid_product_name(
        "Producto US Dollar High Yield Bond Fund (el «Fondo»), Class A2 "
        "USD (la «Clase de Acciones»), ISIN: LU0046676465, está "
        "autorizado en Luxemburgo"
    )
    assert result != "EEUU"


def test_product_line_rejects_boilerplate_continuation():
    """"...este producto de inversión."/"Producto está autorizado..." son
    continuaciones de prosa, no la etiqueta de campo seguida del nombre
    del fondo -- no deben producir un ancla falsa."""
    assert detect_geography_from_kiid_product_name(
        "Este documento le proporciona información fundamental sobre este "
        "producto de inversión. No se trata de material comercial."
    ) is None
    assert detect_geography_from_kiid_product_name(
        "Producto está autorizado en Irlanda. Para obtener más "
        "información sobre este producto, contacte con nosotros."
    ) is None


def test_product_line_multi_region_enumeration_maps_to_global():
    assert detect_geography_from_kiid_product_name(
        "Producto Global Allocation Fund, investing across Europe and "
        "North America equities (el «Fondo»)"
    ) == "Global"


def test_product_line_no_signal_returns_none():
    assert detect_geography_from_kiid_product_name(
        "Producto Strategic Total Return Fund (el «Fondo»)"
    ) is None
    assert detect_geography_from_kiid_product_name(None) is None
    assert detect_geography_from_kiid_product_name("") is None


def test_product_line_only_used_as_last_resort_fallback():
    """Si la ventana objetivo YA dio señal, el fallback de la línea
    Producto: nunca debe anular esa señal aunque contradiga."""
    text = "PRODUCTO Global Balanced Fund\n" + _kiid(
        "el fondo invierte principalmente en japón."
    )
    assert detect_geography_from_kiid(text) == "Japón"


def test_kiid_worldwide_bare_signals_map_to_global():
    """FIX-GEO-5 (2026-07-05): hallado auditando la población de origen
    'restantes' -- "de todo el mundo"/"de cualquier parte del mundo"
    declaran el mandato del propio fondo (renta fija: emisores
    worldwide), variante frecuente no cubierta por las locuciones
    existentes ("a nivel mundial"/"en todo el mundo" tras verbo)."""
    assert detect_geography_from_kiid(_kiid(
        "el fondo trata de alcanzar este objetivo invirtiendo principalmente "
        "en una gama de instrumentos y valores de renta fija emitidos por "
        "empresas o gobiernos de todo el mundo."
    )) == "Global"
    assert detect_geography_from_kiid(_kiid(
        "el fondo invierte en diversas clases de activos, que incluyen "
        "títulos de deuda, renta variable e infraestructuras de cualquier "
        "parte del mundo, incluidos los mercados emergentes."
    )) == "Global"


def test_kiid_worldwide_bare_signal_excludes_manager_scope():
    """Guard: "de todo el mundo" puede describir el ALCANCE DE LA GESTORA
    (oficinas/clientes), no el mandato de inversión del fondo -- no debe
    generar un falso positivo."""
    assert detect_geography_from_kiid(_kiid(
        "la gestora cuenta con oficinas y una red de clientes de todo el "
        "mundo, ofreciendo un servicio personalizado a cada inversor."
    )) is None


# ---------------------------------------------------------------------------
# FIX-GEO-7 (2026-07-12): detect_geography() — variantes de nombre que
# antes no se detectaban y caían al fallback Investment_Universe='Global'
# (InvestmentUniverse-NatureFallback WARN). Root cause: keywords truncados,
# gentilicios EN, y abreviaturas OCR ausentes en la lista.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    # -- Japón --
    ("INVESCO NIPPON EQ B USD ACC",      "Japón"),  # "nippon" (EN demonym)
    # -- Asia: Korea --
    ("JPM KOREA EQUITY A USD ACC",       "Asia"),   # "korea" → Asia
    ("INVESCO KOREAN EQ B EUR ACC",      "Asia"),   # "korean" → Asia
    # -- Emergentes: variantes truncadas/OCR --
    ("CARMIGNAC EMERG.PATRIMOINE F EUR", "Emergentes"),  # "emerg." con punto
    ("JPM EMERGNG MKTS EQ A EUR ACC",   "Emergentes"),  # "emergng"
    ("VONTOBEL EMERGG MKT DEBT A EUR",  "Emergentes"),  # "emergg"
    ("ALLIANZ GI EMERGI MKT DEX A EUR", "Emergentes"),  # "emergi"
    ("GS EMRGNG MKTS EQUITY R USD ACC", "Emergentes"),  # "emrgng"
    ("PIMCO EM DEBT A USD ACC",         "Emergentes"),  # "em debt"
    ("AB EM MKTS CREDIT A USD ACC",     "Emergentes"),  # "em mkts"
    ("FIDELITY EM LOCAL CY A EUR ACC",  "Emergentes"),  # "em local"
    # -- Europa: variantes que antes faltaban --
    ("DWS INVEST GERMAN EQUITS A EUR",  "Europa"),  # "german" (sin 'y')
    ("FIDELITY FUNDS ITALY A ACC EUR",  "Europa"),  # "italy" (EN)
    ("EDM SPAIN EQ A EUR ACC",          "Europa"),  # "spain"
    ("BESTINVER SPANISH EQUITY A EUR",  "Europa"),  # "spanish"
    # -- eurp/eurpe: Emergentes gana si hay señal EM (orden garantizado) --
    ("MS EM EURP MIDEAST AFR A USD",    "Emergentes"),  # EM antes que Europa
    ("AXA IM FIIS EURP SHOR DUR A EUR", "Europa"),      # solo "eurp", sin EM
    ("THREADNEEDLE EURPE SMLR COS A",   "Europa"),      # "eurpe"
    ("BELGRAVIA SWITZERLAN EQUITY A",   "Europa"),      # "switzerlan"
])
def test_fix_geo7_new_keyword_variants(name, expected):
    """FIX-GEO-7: variantes de keyword añadidas a detect_geography() que
    antes dejaban los fondos sin señal → fallback Global (WARN)."""
    result = detect_geography(name.lower())
    assert result == expected, (
        f"Expected {expected!r} for {name!r}, got {result!r}"
    )


@pytest.mark.parametrize("name", [
    # "em eq" explícitamente EXCLUIDO: false-match "prem equilib"
    "CS PREM EQUILIB FI B EUR ACC",
    "AMUNDI PREMIUM EQUILIBRIO A EUR",
])
def test_fix_geo7_em_eq_fp_guard(name):
    """FIX-GEO-7 NEGATIVO: 'em eq' NO debe añadirse porque coincide en
    'prem equilib' (sustrato 'premequilib' → 'em eq' presente).
    Verificado que la lista final NO incluye 'em eq'."""
    result = detect_geography(name.lower())
    assert result != "Emergentes", (
        f"FALSE POSITIVE: {name!r} detected as Emergentes (em eq leak)"
    )


def test_kiid_mundial_adjective_variants_map_to_global():
    """FIX-GEO-5: "mundial(es)" como adjetivo pospuesto ("mercados de renta
    variable mundiales", "a escala mundial"), no solo la locución "a nivel
    mundial" ya existente."""
    assert detect_geography_from_kiid(_kiid(
        "el fondo genera crecimiento del capital mediante la inversión en "
        "mercados de renta variable mundiales diversificados."
    )) == "Global"
    assert detect_geography_from_kiid(_kiid(
        "el subfondo invertirá en una cartera diversificada de activos "
        "alternativos y estrategias de activos alternativos a escala "
        "mundial."
    )) == "Global"


def test_kiid_paises_emergentes_variant_maps_to_emergentes():
    """FIX-GEO-5: "países emergentes" (vs "mercados emergentes") -- bug
    real (GS EM Debt / JPM Emerging Markets Debt): 'renta fija de
    cualquier tipo de emisor de países emergentes' no coincidía con
    ninguna de las locuciones de mercados emergentes existentes."""
    assert detect_geography_from_kiid(_kiid(
        "la cartera invertirá principalmente en valores de renta fija de "
        "cualquier tipo de emisor de países emergentes."
    )) == "Emergentes"


def test_kiid_paises_emergentes_negation_excluded():
    assert detect_geography_from_kiid(_kiid(
        "el fondo invierte en renta fija global, excepto países emergentes, "
        "aplicando criterios de calidad crediticia."
    )) is None


@pytest.mark.parametrize("phrase,desc", [
    ("el subfondo puede estar expuesto a países emergentes como riesgo accesorio",
     "exposición incidental ('puede estar expuesto a')"),
    ("la cartera puede tener exposición a países emergentes en mercados secundarios",
     "exposición incidental ('puede tener exposición a')"),
    ("incluyendo países emergentes, como parte del universo ampliado de inversión",
     "enumeración incidental ('incluyendo')"),
    ("el fondo invierte en renta fija global, así como en países emergentes de forma limitada",
     "conjunción subordinada ('así como')"),
    ("el fondo invierte en bonos investment grade, también puede invertir hasta el 20% en países emergentes",
     "exposición limitada ('también')"),
])
def test_kiid_paises_emergentes_risk_context_not_emergentes(phrase, desc):
    """FIX-GEO-MISMATCH (2026-07-12): 'países emergentes' en contexto de riesgo
    incidental (EDR INCOME EUROPE, DWS EURORENTA, R-CO CREDI EURO) NO debe
    activar Geography='Emergentes'. Estos fondos mencionan EM en secciones de
    riesgo o como exposición secundaria, no como objetivo principal."""
    result = detect_geography_from_kiid(_kiid(phrase))
    assert result != "Emergentes", (
        f"Se esperaba ≠'Emergentes' para contexto '{desc}', obtenido: {result!r}"
    )


def test_kiid_bonos_suecos_maps_to_europa():
    """FIX-GEO-5: país nórdico sin nivel propio en DOMAIN_VALUES (mismo
    criterio que 'Italia'->Europe). Adjetivo explícito sobre el
    instrumento, no el código de divisa (evita la trampa divisa-vs-
    geografía de "US Dollar Fund")."""
    assert detect_geography_from_kiid(_kiid(
        "el fondo invierte principalmente en bonos suecos denominados en "
        "coronas suecas."
    )) == "Europa"


def test_product_line_fallback_fires_when_objective_window_empty():
    """Integración: sin señal en la ventana objetivo, el fallback de la
    línea Producto: sí se usa a través de detect_geography_from_kiid."""
    text = "PRODUCTO AXA IM FIIS Europe Short Duration High Yield\n" + _kiid(
        "el fondo invierte en una amplia gama de activos diversificados "
        "sin ningún mandato geográfico concreto."
    )
    assert detect_geography_from_kiid(text) == "Europa"


# ---------------------------------------------------------------------------
# FIX-GEO-4 (2026-07-05): validate_geography_universe (BL-52/INTER-10) --
# única fuente de verdad para la coherencia Geography <-> Investment_Universe.
# Antes sin tests dedicados pese a ser invocada desde dos sitios (INTER-10
# dentro de cada bloque, y BL-52 en pipeline.py); pipeline.py además
# reimplementaba en local el mismo catálogo de regiones en vez de llamar a
# esta función (violación DRY corregida en el mismo fix).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("geography,universe,expected_status,expected_corrected", [
    # BL-52: Country + región amplia -> auto-corrección a Regional.
    # Bug real corpus: FIDELITY ITALY (Geography=Europe, Investment_Universe
    # ='Country'), FTGF PUT LG CAP VAL (Geography=North America, ídem).
    ("Europe", "Country", "CORRECTED", "Regional"),
    ("North America", "Country", "CORRECTED", "Regional"),
    ("Asia-Pacific", "Country", "CORRECTED", "Regional"),
    ("Eastern Europe", "Country", "CORRECTED", "Regional"),
    ("Latin America", "Country", "CORRECTED", "Regional"),
    ("Middle East & Africa", "Country", "CORRECTED", "Regional"),
    # País individual real (Japan/China/India) + Country -> OK, no se toca.
    ("Japan", "Country", "OK", None),
    ("China", "Country", "OK", None),
    ("India", "Country", "OK", None),
    # Geografía específica de país + Universe=Global -> inusual, solo WARNING
    # (no se auto-corrige -- podría ser un error en cualquiera de los dos
    # campos, no hay una dirección de corrección inequívoca).
    ("China", "Global", "WARNING", None),
    ("Japan", "Global", "WARNING", None),
    # Global + Universe país/región -> inusual, solo WARNING.
    ("Global", "Country", "WARNING", None),
    ("Global", "Regional", "WARNING", None),
    # Combinaciones coherentes -> OK.
    ("Europe", "Regional", "OK", None),
    ("Global", "Global", "OK", None),
    (None, None, "OK", None),
])
def test_validate_geography_universe(geography, universe, expected_status, expected_corrected):
    status, msg, corrected = validate_geography_universe(geography, universe)
    assert status == expected_status
    assert corrected == expected_corrected
    if expected_status != "OK":
        assert msg is not None
    else:
        assert msg is None
