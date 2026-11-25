"""FND-0441 Step 2: the mandate sentence of the KIID as a FALLBACK signal for Geography, when the KIID extractor and the name say nothing.

Every sentence below was read in the development or KIID-only samples (doc/audit/geo_ground_truth); none comes from the sealed holdout. Labelling rules approved by the owner (2026-11-25): a currency is
not a region; a benchmark or performance objective is not a mandate; 'may invest in emerging markets' is not a mandate; developed plus emerging, OECD plus emerging, domestic and foreign, and US and non-US
issuers are global; one European country is Europe (the catalogue has no country level for it)."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from proyecto1.core import classify_utils as cu

PIPELINE = (Path(__file__).resolve().parent.parent / "core" / "pipeline.py").read_text(encoding="utf-8")


def doc(sentence: str) -> str:
    return "DOCUMENTO DE DATOS FUNDAMENTALES. ¿Qué es este producto? Objetivos " + sentence + " Indicador de riesgo. Costes."


def got(sentence: str):
    return cu.detect_geography_from_mandate(doc(sentence))


@pytest.mark.parametrize("sentence", [
    "El Fondo podrá invertir en todo el mundo, incluidos los mercados emergentes y China continental.",
    "El Fondo invierte principalmente en una amplia gama de materias primas seleccionadas de todo el mundo.",
    "La mayor parte del patrimonio se invierte en títulos de deuda de emisores de cualquier lugar del mundo, incluidos los mercados emergentes.",
    "La SICAV podrá invertir en valores de renta variable de cualquier zona geográfica.",
    "El fondo invierte a escala global en bonos corporativos que no presenten grado de inversión.",
    "Este subfondo de gestión activa invierte a escala global y en diversos sectores.",
    "El fondo invierte especialmente en acciones de emisores internacionales.",
    "El fondo invierte principalmente en acciones de emisores nacionales y extranjeros.",
    "El fondo invierte un mínimo del 25% en fondos de renta variable, tanto nacionales como extranjeros.",
    "Como mínimo el 67% del patrimonio se invierte en títulos de deuda emitidos en mercados desarrollados y emergentes.",
    "Se invertirá entre 20%-50% en renta variable de países OCDE y emergentes de cualquier capitalización.",
    "El Subfondo invierte al menos el 67 % de sus activos en instrumentos de deuda emitidos o garantizados por Gobiernos de la OCDE.",
    "El Subfondo invertirá al menos dos tercios de sus activos en valores de renta variable, sin ninguna limitación en cuanto a la capitalización de mercado o la diversificación geográfica.",
    "El fondo invierte en bonos de titulización de emisores estadounidenses y valores de emisores no estadounidenses denominados en dólares.",
    "The Fund invests globally at least 70% of its total assets in equity securities.",
    "The fund seeks opportunities by investing in common stocks of companies located around the world, which may include emerging markets.",
    "The Fund seeks to invest in companies worldwide that provide technology solutions.",
    "The Fund seeks to achieve long-term capital appreciation through global equity investments.",
    "Crecimiento del capital por encima de la rentabilidad media de los mercados monetarios europeos en euros mediante la inversión en Mercados de bonos mundiales.",
])
def test_a_stated_worldwide_mandate_is_global(sentence):
    assert got(sentence) == "Global"


@pytest.mark.parametrize("sentence, region", [
    ("El fondo invierte principalmente, de manera directa o a través de derivados, en bonos europeos.", "Europa"),
    ("Para lograr este objetivo, el fondo invierte principalmente en acciones de emisores alemanes. También es posible invertir de forma complementaria hasta un 25% en acciones de emisores extranjeros.", "Europa"),
    ("Invierte principalmente en una cartera diversificada de títulos europeos de deuda corporativa.", "Europa"),
    ("The investment fund invests mainly in equities of German companies.", "Europa"),
    ("The Fund invests mainly in euro-denominated bonds issued by European companies.", "Europa"),
    ("El Subfondo invertirá su patrimonio en operaciones de recompra inversa y títulos de deuda pública estadounidense a corto plazo.", "EEUU"),
    ("Al menos el 80 % del Fondo se invierte en acciones de pequeñas empresas con domicilio social o que desarrollan la mayor parte de su actividad en Japón.", "Japón"),
])
def test_a_principal_investment_in_one_region_is_that_region(sentence, region):
    assert got(sentence) == region


@pytest.mark.parametrize("sentence", [
    "El Compartimento invierte principalmente en valores emitidos por empresas que realizan la mayor parte de su actividad de negocio en un mercado emergente.",
    "To achieve a combination of income and growth by investing mostly in shares of companies based in emerging market countries.",
    "The Fund aims to be mainly invested in short term bonds issued by Emerging Market corporate entities.",
])
def test_a_principal_investment_in_emerging_markets_is_emerging(sentence):
    es = got(sentence)
    assert es == "Emergentes"
    assert cu._derive_geography_en(es, "fund x") == "Global" and cu.derive_development_status(es, "Global", "fund x") == "Emerging"


@pytest.mark.parametrize("sentence", [
    "El objetivo es reproducir la rentabilidad del Bloomberg MSCI Euro Corporate 1-5 Year Index.",                                   # benchmark, no mandate
    "El fondo invierte en deuda de gobiernos denominada en euros o cubierta frente a esta moneda.",                                   # currency
    "Al menos el 70 % de los activos se invertirá en valores denominados en dólares estadounidenses.",                              # currency, not the US
    "El Fondo invierte en materias primas a través de derivados.",
    "El fondo puede invertir en mercados emergentes, que son aquellas economías no plenamente desarrolladas.",                        # may invest
    "El Subfondo puede invertir menos del 25 % de sus activos netos en bonos chinos.",                                              # accessory
    "Podrá invertir hasta un 35 % en acciones A de China.",
    "El objetivo del Subfondo es superar la rentabilidad de los mercados mundiales de renta variable.",                              # comparator
    "El fondo trata de superar el índice MSCI World invirtiendo en acciones.",
    "Se considera que el fondo se gestiona por referencia al índice para calcular la exposición global del fondo.",                    # VaR method
    "La Sociedad apoya los principios del Pacto Mundial de las Naciones Unidas al invertir.",
    "El fondo es un subfondo de HSBC Global Investment Funds.",
])
def test_currency_benchmark_comparator_accessory_and_boilerplate_give_nothing(sentence):
    assert got(sentence) is None


@pytest.mark.parametrize("sentence", [
    "El Fondo invierte como mínimo el 70% de sus activos en empresas que realicen la mayor parte de su actividad en Asia (sin Japón).",   # found by the replay (not the holdout)
    "The Fund invests mainly in companies in Asia excluding Japan.",
    "The Fund invests primarily in Asian equities ex-Japan.",
    "El fondo invierte principalmente en empresas de países fuera de los Estados Unidos y no europeas.",
])
def test_a_negated_region_is_not_that_region(sentence):
    assert got(sentence) is None


def test_global_scope_beats_a_mere_emphasis_and_two_regions_are_global():
    assert got("El Compartimento invierte en todo el mundo, con especial énfasis en Europa.") == "Global"
    assert got("El fondo invierte principalmente en acciones de empresas europeas y estadounidenses.") == "Global"


def test_a_principal_region_beats_a_permissive_worldwide_sentence():
    assert got("El fondo invierte principalmente en acciones de emisores alemanes. El Fondo podrá invertir en todo el mundo.") == "Europa"


@pytest.mark.parametrize("sentence", [
    "El indicador de riesgo supone que invierte en todo el mundo durante cinco años.",                      # risk boilerplate that uses the verb
    "Los mercados de todo el mundo han crecido.",                                                            # a world phrase with no investing verb
    "El fondo invierte con el objetivo de superar la rentabilidad de los mercados mundiales de renta variable.",   # comparator wording
    "El fondo puede invertir principalmente en bonos europeos.",                                             # permissive: may
    "El fondo invierte principalmente en acciones de mercados desarrollados, incluidos los europeos.",        # the region is only an inclusion
])
def test_each_guard_alone_stops_a_false_value(sentence):
    assert got(sentence) is None


def test_a_principal_worldwide_statement_beats_a_principal_region_elsewhere():
    assert got("El fondo invierte principalmente en acciones de todo el mundo. El fondo invierte principalmente en bonos europeos.") == "Global"


def test_the_scan_is_bounded_to_the_objective_window():
    far = "Objetivos " + "relleno sin senales. " * 200 + "El fondo invierte en todo el mundo."                 # beyond the 2,500-character window (R-6)
    assert cu.detect_geography_from_mandate(far) is None
    assert cu.detect_geography_from_mandate("Objetivos " + "relleno. " * 20 + "El fondo invierte en todo el mundo.") == "Global"


def test_dollar_denomination_is_not_the_united_states():
    assert got("El fondo invierte principalmente en bonos denominados en dólares estadounidenses.") is None
    assert got("The Fund invests mainly in bonds denominated in U.S. dollars.") is None


def test_no_text_or_no_objective_gives_nothing():
    assert cu.detect_geography_from_mandate("") is None and cu.detect_geography_from_mandate(None) is None
    assert cu.detect_geography_from_mandate("Costes. Este producto cuesta 100 EUR.") is None


# ---- wiring: a fallback behind the KIID extractor and the name, before the index ----------------------------------------------------------------------------------------------
def test_pipeline_uses_the_mandate_only_when_kiid_and_name_are_silent_and_before_the_index():
    base = PIPELINE.index("_geo_es_final = _geo_name if _geo_name_wins")
    call = PIPELINE.index("detect_geography_from_mandate(kiid_text)", base)
    assert re.search(r"if not _geo_es_final and _geo_mandate_fallback_enabled\(\):\s*\n\s*_geo_mandate = detect_geography_from_mandate\(kiid_text\)", PIPELINE[base:call + 80])
    assert call < PIPELINE.index("_geo_es_final = _geo_mandate", base) < PIPELINE.index("detect_geography_from_equity_index(", base), "the mandate value is the one carried on"
    assert call < PIPELINE.index("detect_geography_from_equity_index(", base) < PIPELINE.index("_geo_en_final = _derive_geography_en(_geo_es_final", base)
    assert re.search(r'elif _geouniv_status == "WARNING" and \(_geo_name_wins or _geo_from_index or _geo_mandate\):', PIPELINE), "a country from the mandate must not leave a stale warning"


def test_the_fallback_ships_off_so_no_pass_writes_it_before_the_owner_orders():
    from shared import config
    assert config.GEO_MANDATE_FALLBACK_ENABLED is False                      # owner 2026-11-25: database writes stay frozen; a P1 run reads the working tree
    helper = PIPELINE[PIPELINE.index("def _geo_mandate_fallback_enabled"):][:700]
    assert "GEO_MANDATE_FALLBACK_ENABLED" in helper and "return False" in helper


# ---- the derivation contract (A4): geography -> universe and development status through the existing rules ----------------------------------------------------------------------
@pytest.mark.parametrize("geo_es, name, geo_en, universe, status", [
    ("Global", "fund x", "Global", "Global", "Global/Mixed"),
    ("Emergentes", "fund x", "Global", "Global", "Emerging"),
    ("Global", "fund frontier x", "Global", "Global", "Frontier"),
    ("Europa", "fund x", "Europe", "Regional", "Developed"),
    ("EEUU", "fund x", "North America", "Regional", "Developed"),
    ("Japón", "fund x", "Japan", "Country", "Developed"),
    ("China", "fund x", "China", "Country", "Emerging"),
    ("India", "fund x", "India", "Country", "Emerging"),
    ("Asia", "fund x", "Asia-Pacific", "Regional", "Global/Mixed"),
    ("Latinoamérica", "fund x", "Latin America", "Regional", "Emerging"),
    ("Europa del Este", "fund x", "Eastern Europe", "Regional", "Emerging"),
    ("Europa", "fund emerging x", "Europe", "Regional", "Emerging"),
    ("Asia", "fund frontier x", "Asia-Pacific", "Regional", "Frontier"),
])
def test_derivation_table(geo_es, name, geo_en, universe, status):
    assert cu._derive_geography_en(geo_es, name) == geo_en
    assert cu.derive_development_status(geo_es, geo_en, name) == status
    rec = {"Fund_Nature": "Renta Variable", "Fund_Name": name, "Geography": geo_en, "Investment_Universe": "Global"}
    assert cu.validate_all_semantic_consistency(rec)["corrected_record"]["Investment_Universe"] == universe
