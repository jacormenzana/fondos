# proyecto1/tests/test_kiid_official_name_fnd0246.py
# -*- coding: utf-8 -*-
"""FND-0246: the official sub-fund name read from the head of a KIID (core/kiid_official_name.py). The headers are real public KID text, one per layout
(`_fnd0246_kid_heads`); the expected names were read off the documents. No pipeline imports (R-7)."""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.normpath(os.path.join(_HERE, "..", "core"))
for _p in (_CORE, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import _fnd0246_kid_heads as H  # noqa: E402
from kiid_official_name import (WINDOW, clean_official_name, extract_official_fund_name, official_name_key)  # noqa: E402


def _head(layout):
    return getattr(H, "HEAD_" + layout), H.ISIN_OF[layout]


@pytest.mark.parametrize("layout, name, rule", [
    ("BGF_ES", "US Dollar Short Duration Bond Fund", "LABEL"),               # "Producto <name> (el «Fondo»), Class D2 USD"
    ("BGF_EN", "World Mining Fund", "LABEL"),                                # "Product <name> (the “Fund”), Class S2 USD"
    ("DWS", "DWS Invest Global Bonds", "LABEL"),                             # "Producto <name> Clase de acciones: LD, ISIN"
    ("INVESCO", "Invesco Global Income Fund", "LABEL"),                      # "Producto Denominación del producto: <name> (el «Fondo»)"
    ("POLAR", "Global Technology Fund", "LABEL"),                            # "Nombre del fondo: <name> Nombre de la Clase de Acciones"
    ("FIDELITY", "FIDELITY FUNDS - GLOBAL TECHNOLOGY FUND", "LABEL"),        # "<name> A-EURO Sociedad Gestora": the A-EURO class tail is cut
    ("PICTET", "Pictet - Japanese Equity Opportunities", "LABEL"),           # "<name> - I EUR ISIN": the "- I EUR" class tail is cut
    ("ALLIANZ", "Allianz Thematica", "LABEL"),                               # "PRODUCTO <name> (el «Compartimento»)"
    ("TEMPLETON", "Templeton Global Bond Fund", "LABEL"),                    # "Producto <name> ¿Qué es este producto?"
    ("MG", "M&G (Lux) Euro Corporate Bond Fund", "ISIN_TRAIL"),              # "con n.º ISIN <isin> <name> El «Fondo»"
    ("CAPITAL", "Capital Group Global High Income Opportunities (LUX)", "SUBFUND_CLAUSE"),   # '<name> (the "fund"), a sub-fund of'
    ("ALGEBRIS", "Algebris Financial Income Fund", "SUBFUND_CLAUSE"),        # "<name> Class R EUR, a sub-fund of"
    ("JPM", "JPM Global Bond Opportunities", "SUBFUND_CLAUSE"),              # "<name> A (acc) - EUR (hedged) <isin> una clase de acciones de"
])
def test_the_official_name_of_each_header_layout(layout, name, rule):
    text, isin = _head(layout)
    assert extract_official_fund_name(text, isin) == (name, rule)


def test_prose_after_a_label_is_not_a_name():
    # "Producto se gestiona de manera pasiva ..." sits later in this text; the name is the BNP sub-fund clause before it
    text, isin = _head("PROSE_PASSIVE")
    assert extract_official_fund_name(text, isin) == ("MSCI World SRI PAB", "SUBFUND_CLAUSE")
    for prose in ("Producto se gestiona de manera pasiva. El Producto pretende reproducir (con un error de seguimiento) la rentabilidad del indice, ISIN",
                  "Producto: carácter fundamental y utiliza la información proporcionada por especialistas, ISIN",
                  "Product This document was published on 15/02/2024. What is this product? Type This fund is an open-ended",
                  "Producto es un fondo de inversión, ISIN"):
        assert extract_official_fund_name(prose) == (None, "NONE")


def test_currency_words_inside_the_name_are_kept_so_money_market_sub_funds_stay_apart():
    names = {c: extract_official_fund_name(*_head("PICTET_MM_" + c))[0] for c in ("USD", "CHF", "EUR")}
    assert names == {"USD": "Pictet - Short-Term Money Market USD", "CHF": "Pictet - Short-Term Money Market CHF", "EUR": "Pictet - Short-Term Money Market EUR"}
    assert len({official_name_key(n) for n in names.values()}) == 3


def test_classes_of_one_fund_share_one_key():
    # two real classes of the same Pictet sub-fund: the class code after the dash is not part of the name
    a = extract_official_fund_name(*_head("PICTET_MM_USD"))[0]
    assert official_name_key(a) == official_name_key(clean_official_name("Pictet - Short-Term Money Market USD - P"))
    assert official_name_key(clean_official_name("JPM Global Income A (acc) - EUR")) == official_name_key(clean_official_name("JPM Global Income C (div)"))
    assert official_name_key(clean_official_name("FIDELITY FUNDS - GLOBAL TECHNOLOGY FUND A-EURO")) == official_name_key(
        clean_official_name("FIDELITY FUNDS - GLOBAL TECHNOLOGY FUND A-ACC-USD"))


@pytest.mark.parametrize("raw, expected", [
    ("Denominación del producto: Invesco Global Income Fund", "Invesco Global Income Fund"),
    ("Producto: The Fund ¿Qué es este producto?", "The Fund"),
    ("DWS Invest Top Dividend", "DWS Invest Top Dividend"),
    ("Euro Bond Fund (el «Fondo»)", "Euro Bond Fund"),
    ("Capital Group New Perspective Fund (LUX)", "Capital Group New Perspective Fund (LUX)"),
    ("Pictet - Short-Term Money Market USD", "Pictet - Short-Term Money Market USD"),          # no marker: nothing is cut, USD is the sub-fund's
    ("US Equity Fund A-EURO", "US Equity Fund"),
    ("", ""),
    (None, ""),
])
def test_clean_official_name(raw, expected):
    assert clean_official_name(raw) == expected


@pytest.mark.parametrize("raw, expected", [                       # stacked / manager-specific explicit class tails, each measured on the stored texts
    ("Janus Henderson Balanced Fund A2 HEUR", "Janus Henderson Balanced Fund"),
    ("Janus Henderson Balanced Fund A5m USD", "Janus Henderson Balanced Fund"),
    ("Robeco BP US Premium Equities F EUR", "Robeco BP US Premium Equities"),
    ("Aberdeen Global - Asia Pacific Equity Fund A2 Hedged EUR Acc LU0566486402", "Aberdeen Global - Asia Pacific Equity Fund"),
    ("Wellington Global Health Care Equity Fund EUR D AccU", "Wellington Global Health Care Equity Fund"),
    ("Goldman Sachs Patrimonial Balanced - P Cap EUR Goldman Sachs Patrimonial Balanced", "Goldman Sachs Patrimonial Balanced"),
    ("Oncology, C - Capitalización", "Oncology"),
    ("FIDELITY FUNDS - ASIA EQUITY ESG FUND A-ACC-EURO (EURO/USD HEDGED)", "FIDELITY FUNDS - ASIA EQUITY ESG FUND"),
    ("Pictet - Water - P dy EUR", "Pictet - Water"),
    ("AMUNDI FUNDS EUROLAND EQUITY - A USD Hgd", "AMUNDI FUNDS EUROLAND EQUITY"),
    ("DB CORTO PLAZO CL.A, FI", "DB CORTO PLAZO"),
    ("DB CRECIMIENTO B, FI", "DB CRECIMIENTO"),
    ("29 de junio de 2026 BNY Mellon Mobility Innovation Fund", "BNY Mellon Mobility Innovation Fund"),
    ("LU1251922891 All China Equity Fund", "All China Equity Fund"),
    ("Seilern America EUR U R", "Seilern America EUR U R"),            # a class currency that is NOT marked as a class stays: under-merge, never a wrong merge
])
def test_explicit_class_tails_are_cut_and_unmarked_currency_stays(raw, expected):
    assert clean_official_name(raw) == expected


def test_plausibility_rejects_prose_and_accepts_lowercase_brand_names():
    assert extract_official_fund_name("Producto El Producto es un fondo, ISIN") == (None, "NONE")
    assert extract_official_fund_name("Producto LU1251922891 ISIN: LU1251922891") == (None, "NONE")
    assert extract_official_fund_name("Producto iShares Developed World Index Fund (IE) (el «Fondo»), Class D Accu EUR") == (
        "iShares Developed World Index Fund (IE)", "LABEL")


def test_class_of_rule_reads_the_sub_fund_after_the_class_clause():
    text = "Producto Producto: Clase de participaciones RT (Divisa: EUR) del Flossbach von Storch - Multiple Opportunities II, un fondo OICVM Del productor del PRIIP"
    assert extract_official_fund_name(text) == ("Flossbach von Storch - Multiple Opportunities II", "CLASS_OF")


def test_parse_kiid_generic_exposes_the_official_name_and_its_trace_tag():
    from kiid_parser import parse_kiid_generic
    text, isin = _head("BGF_ES")
    parsed = parse_kiid_generic(text, isin=isin)
    assert parsed["Official_Fund_Name"] == "US Dollar Short Duration Bond Fund"
    assert "OFFICIAL_NAME[LABEL]" in parsed["Inference_Trace"].split("|")


def test_parse_kiid_generic_leaves_the_name_none_when_the_head_has_none():
    from kiid_parser import parse_kiid_generic
    parsed = parse_kiid_generic("Este documento le proporciona información fundamental sobre este producto de inversión.", isin="LU0000000001")
    assert parsed["Official_Fund_Name"] is None
    assert "OFFICIAL_NAME" not in (parsed["Inference_Trace"] or "")


def test_the_key_folds_accents_case_and_punctuation():
    assert official_name_key("Fondo Renta Fija – Crédito (EUR)") == "fondo renta fija credito eur"
    assert official_name_key(None) == ""


def test_a_name_past_the_window_is_not_read():
    text = "Documento de datos fundamentales. " + "x " * (WINDOW // 2 + 50) + " Producto Euro Bond Fund (el «Fondo»), Clase A"
    assert extract_official_fund_name(text) == (None, "NONE")


def test_no_label_and_no_isin_gives_none():
    assert extract_official_fund_name("Este documento le proporciona información fundamental sobre este producto de inversión.") == (None, "NONE")
    assert extract_official_fund_name("") == (None, "NONE")
    assert extract_official_fund_name(None) == (None, "NONE")


def test_the_own_isin_anchors_the_head_rule_only_near_the_start():
    near = "JPM Global Income A (acc) - EUR LU0000000001 una clase de acciones de JPMorgan Funds"
    assert extract_official_fund_name(near, "LU0000000001") == ("JPM Global Income", "ISIN_HEAD")
    far = "x" * 400 + " JPM Global Income A (acc) - EUR LU0000000001 una clase de acciones de JPMorgan Funds"
    assert extract_official_fund_name(far, "LU0000000001") == (None, "NONE")
