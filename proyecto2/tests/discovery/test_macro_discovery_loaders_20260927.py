# proyecto2/tests/discovery/test_macro_discovery_loaders_20260927.py
# -*- coding: utf-8 -*-
"""
FND-0066 (2026-09-27): macro_discovery.py's 4 registered source loaders (load_ine_ipc,
load_bce_series, load_fred_series, load_eurostat_series -- AGENTS.md's "Registered macro data
sources" table) had zero test coverage. test_eurostat.py/test_fred_es.py/test_fred_es2.py in this
same directory are NOT pytest tests despite the name: no `def test_*`, no assertions, top-level
`requests.get(...)` calls made as a side effect of collection alone -- exploratory scratch scripts
for probing the real APIs' shape, not regression tests for these functions. This file is the first
real coverage.

Each loader's own HTTP call is mocked at the narrowest point that already exists in the module
(_get_json / requests.get / _fred_fetch) with a response shaped like the real API's documented
format (each function's own docstring, verified against the live exploration scripts above where
they overlap) -- no network calls made by these tests.

R-7 compliant: no pipeline.py / core.io imports.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

_HERE = Path(__file__).parent
_P2_ROOT = _HERE.parent.parent  # proyecto2/
_REPO = _P2_ROOT.parent         # c:/desarrollo/fondos
for _p in (str(_P2_ROOT), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proyecto2.src.discovery import macro_discovery as md  # noqa: E402


# ---------------------------------------------------------------------------
# load_ine_ipc -- single _get_json call, INE's DATOS_TABLA response shape
# ---------------------------------------------------------------------------

def test_load_ine_ipc_parses_the_ine_response_shape():
    ine_response = [{
        "Data": [
            {"Fecha": "2024M01", "Valor": 133.7},
            {"Fecha": "2024M02", "Valor": 134.1},
        ]
    }]
    with patch.object(md, "_get_json", return_value=ine_response):
        rows = md.load_ine_ipc(desde="2000-01")

    assert rows == [
        {"date": "2024-01-01", "geography": "ES", "ipc_index": 133.7, "source": "INE"},
        {"date": "2024-02-01", "geography": "ES", "ipc_index": 134.1, "source": "INE"},
    ]


def test_load_ine_ipc_filters_by_desde_and_drops_null_values():
    ine_response = [{"Data": [
        {"Fecha": "1999M12", "Valor": 100.0},   # before desde -- dropped
        {"Fecha": "2024M01", "Valor": None},    # null value -- dropped
        {"Fecha": "2024M02", "Valor": 134.1},
    ]}]
    with patch.object(md, "_get_json", return_value=ine_response):
        rows = md.load_ine_ipc(desde="2000-01")
    assert len(rows) == 1 and rows[0]["date"] == "2024-02-01"


def test_load_ine_ipc_is_empty_not_an_exception_when_the_source_errors():
    with patch.object(md, "_get_json", side_effect=RuntimeError("timeout")):
        rows = md.load_ine_ipc()
    assert rows == []


# ---------------------------------------------------------------------------
# load_bce_series -- one requests.get + pd.read_csv per configured series
# ---------------------------------------------------------------------------

def _fake_response(csv_text: str):
    resp = MagicMock()
    resp.text = csv_text
    resp.raise_for_status = MagicMock()
    return resp


def test_load_bce_series_parses_a_plain_series_and_a_rate_series(monkeypatch):
    monkeypatch.setattr(md, "_BCE_SERIES", {
        "m3_yoy_EU": {"series_key": "BSI/TEST", "indicator": "m3_yoy",
                      "geography": "EU", "unit": "pct", "write_inflation": False},
        "rate_deposit_EU": {"series_key": "FM/TEST", "indicator": "rate_deposit",
                             "geography": "EU", "unit": "pct", "write_inflation": False},
    })
    plain_csv = "TIME_PERIOD,OBS_VALUE\n2024-01,2.5\n2024-02,2.6\n"
    rate_csv = "TIME_PERIOD,OBS_VALUE\n2024-01-15,3.75\n"

    def _get(url, params=None, timeout=None):
        return _fake_response(rate_csv if "FM/TEST" in url else plain_csv)

    with patch.object(md.requests, "get", side_effect=_get):
        inflation_rows, macro_rows = md.load_bce_series(desde="2024-01")

    plain = [r for r in macro_rows if r["indicator"] == "m3_yoy"]
    assert plain == [
        {"date": "2024-01-01", "indicator": "m3_yoy", "geography": "EU",
         "value": 2.5, "unit": "pct", "source": "BCE"},
        {"date": "2024-02-01", "indicator": "m3_yoy", "geography": "EU",
         "value": 2.6, "unit": "pct", "source": "BCE"},
    ]
    # The rate series is forward-filled to one row per calendar month, not one row per decision.
    rate = [r for r in macro_rows if r["indicator"] == "rate_deposit"]
    assert all(r["value"] == 3.75 for r in rate) and len(rate) >= 1
    assert inflation_rows == []   # neither test series has write_inflation=True


def test_load_bce_series_skips_a_series_whose_request_fails(monkeypatch):
    monkeypatch.setattr(md, "_BCE_SERIES", {
        "m3_yoy_EU": {"series_key": "BSI/TEST", "indicator": "m3_yoy",
                      "geography": "EU", "unit": "pct", "write_inflation": False},
    })
    with patch.object(md.requests, "get", side_effect=md.requests.RequestException("down")):
        inflation_rows, macro_rows = md.load_bce_series(desde="2024-01")
    assert inflation_rows == [] and macro_rows == []


# ---------------------------------------------------------------------------
# load_fred_series -- _fred_fetch already returns a [date, value] DataFrame
# ---------------------------------------------------------------------------

def test_load_fred_series_parses_a_fetched_series_and_tags_inflation(monkeypatch):
    monkeypatch.setattr(md, "_FRED_SERIES", {
        "CPIAUCSL": {"indicator": "ipc_index", "geography": "US", "unit": "index",
                     "write_inflation": True},
    })
    fetched = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-02-01"]),
        "value": [310.3, 311.5],
    })
    with patch.object(md, "_fred_fetch", return_value=fetched):
        inflation_rows, macro_rows = md.load_fred_series(desde="2000-01")

    assert macro_rows == [
        {"date": "2024-01-01", "indicator": "ipc_index", "geography": "US",
         "value": 310.3, "unit": "index", "source": "FRED"},
        {"date": "2024-02-01", "indicator": "ipc_index", "geography": "US",
         "value": 311.5, "unit": "index", "source": "FRED"},
    ]
    assert inflation_rows == [
        {"date": "2024-01-01", "geography": "US", "ipc_index": 310.3, "source": "FRED"},
        {"date": "2024-02-01", "geography": "US", "ipc_index": 311.5, "source": "FRED"},
    ]


def test_load_fred_series_skips_a_series_that_returns_none(monkeypatch):
    monkeypatch.setattr(md, "_FRED_SERIES", {
        "CPIAUCSL": {"indicator": "ipc_index", "geography": "US", "unit": "index",
                     "write_inflation": True},
    })
    with patch.object(md, "_fred_fetch", return_value=None):
        inflation_rows, macro_rows = md.load_fred_series(desde="2000-01")
    assert inflation_rows == [] and macro_rows == []


# ---------------------------------------------------------------------------
# load_eurostat_series -- every call (PIB + gov queries) goes through _get_json
# ---------------------------------------------------------------------------

def test_load_eurostat_series_parses_the_pib_quarterly_shape():
    """Eurostat's JSON-stat shape: `value` keyed by positional index, `dimension.time.category.
    index` giving the period label at that index -- verified against the live exploration script
    in this same directory (test_eurostat.py) for the real dimension/value structure."""
    pib_response = {
        "value": {"0": 3500000.0, "1": 3520000.0},
        "dimension": {"time": {"category": {"index": {"2024-Q1": 0, "2024-Q2": 1}}}},
    }
    gov_response = {"value": {}, "dimension": {"time": {"category": {"index": {}}}}}

    def _get_json(url, params=None):
        return pib_response if "namq_10_gdp" in url else gov_response

    with patch.object(md, "_get_json", side_effect=_get_json):
        rows = md.load_eurostat_series(desde="2000-01")

    pib_rows = [r for r in rows if r["indicator"] == "gdp_nom_eur"]
    assert pib_rows == [
        {"date": "2024-01-01", "indicator": "gdp_nom_eur", "geography": "EU",
         "value": 3500000.0, "unit": "eur_mn", "source": "EUROSTAT"},
        {"date": "2024-04-01", "indicator": "gdp_nom_eur", "geography": "EU",
         "value": 3520000.0, "unit": "eur_mn", "source": "EUROSTAT"},
    ]


def test_load_eurostat_series_survives_a_gov_query_failure():
    """One gov-query geo failing (e.g. EA19 unavailable for a recent year) must not lose the PIB
    rows or the other geos -- each call is wrapped in its own try/except."""
    pib_response = {
        "value": {"0": 3500000.0},
        "dimension": {"time": {"category": {"index": {"2024-Q1": 0}}}},
    }

    def _get_json(url, params=None):
        if "namq_10_gdp" in url:
            return pib_response
        raise RuntimeError("Eurostat 404")

    with patch.object(md, "_get_json", side_effect=_get_json):
        rows = md.load_eurostat_series(desde="2000-01")

    assert len(rows) == 1 and rows[0]["indicator"] == "gdp_nom_eur"


def test_fred_m2_level_units_match_magnitude_fnd0178():
    """FND-0178: FRED MYAGM2CNM189N / MYAGM2JPM189N are plain CNY / JPY (1e13-1e15), not millions; a
    '_mn' label on them mislabelled the magnitude by 1e6. Keep the labels honest and distinct per currency."""
    from proyecto2.src.discovery import macro_discovery as md
    assert md._FRED_SERIES["MYAGM2CNM189N"]["unit"] == "cny"
    assert md._FRED_SERIES["MYAGM2JPM189N"]["unit"] == "jpy"
    assert md._FRED_SERIES["MYAGM2CNM189N"]["unit"] != md._FRED_SERIES["MYAGM2JPM189N"]["unit"]
