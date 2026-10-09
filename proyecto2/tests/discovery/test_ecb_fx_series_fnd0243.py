# proyecto2/tests/discovery/test_ecb_fx_series_fnd0243.py
# -*- coding: utf-8 -*-
"""
FND-0243 step 1: ECB reference rates (units of <CCY> per 1 EUR) for the EUR conversion of non-EUR share-class NAVs.

The payloads below are the real ECB SDW `csvdata` rows captured on 2026-10-09 (EXR.M.USD.EUR.SP00.E 2026-08/09 and
EXR.D.USD.EUR.SP00.A 2026-08-03/04), trimmed to the columns the loader reads plus the series key. No network calls.

R-7 compliant: no pipeline.py / core.io imports.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

_HERE = Path(__file__).parent
_P2_ROOT = _HERE.parent.parent  # proyecto2/
_REPO = _P2_ROOT.parent         # c:/desarrollo/fondos
for _p in (str(_P2_ROOT), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proyecto2.src.discovery import macro_discovery as md  # noqa: E402
from shared.config import (  # noqa: E402
    EUR_FX_CURRENCIES, EUR_FX_DAILY_INDICATOR, EUR_FX_MONTHLY_INDICATOR,
    EUR_NAV_CONVERSION_ENABLED, FX_CONTRIBUTION_EUR_VIEW_ENABLED,
)

_CSV_M_USD = (
    "KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n"
    "EXR.M.USD.EUR.SP00.E,M,USD,EUR,SP00,E,2026-08,1.1596,A\n"
    "EXR.M.USD.EUR.SP00.E,M,USD,EUR,SP00,E,2026-09,1.1355,A\n"
)
_CSV_D_USD = (
    "KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n"
    "EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-08-03,1.1535,A\n"
    "EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-08-04,1.1515,A\n"
)


def _fake_get(payloads):
    """requests.get stand-in: returns the payload registered for the URL's series key; records the calls."""
    calls = []

    def _get(url, params=None, timeout=None):
        calls.append((url, dict(params or {})))
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.text = payloads[url.rsplit("/data/", 1)[1]]
        return resp
    return _get, calls


def test_every_conversion_currency_has_a_monthly_end_of_period_and_a_daily_ecb_series():
    for ccy in EUR_FX_CURRENCIES:
        m = md._BCE_SERIES[f"fx_eom_eur_{ccy}"]
        d = md._BCE_SERIES[f"fx_d_eur_{ccy}"]
        # SP00.E = end of period (the month's last business day), never the monthly average SP00.A of the FRED fx_* series
        assert m["series_key"] == f"EXR/M.{ccy}.EUR.SP00.E" and m["stamp"] == "month_end"
        assert d["series_key"] == f"EXR/D.{ccy}.EUR.SP00.A" and "stamp" not in d
        assert (m["indicator"], d["indicator"]) == (EUR_FX_MONTHLY_INDICATOR, EUR_FX_DAILY_INDICATOR)
        assert m["geography"] == d["geography"] == ccy
        assert not m["write_inflation"] and not d["write_inflation"]


def test_the_new_indicators_do_not_collide_with_any_existing_series():
    taken = {(c["indicator"], c["geography"]) for k, c in md._BCE_SERIES.items() if not k.startswith(("fx_eom_eur_", "fx_d_eur_"))}
    assert not {(EUR_FX_MONTHLY_INDICATOR, c) for c in EUR_FX_CURRENCIES} & taken
    assert not {(EUR_FX_DAILY_INDICATOR, c) for c in EUR_FX_CURRENCIES} & taken
    assert EUR_FX_MONTHLY_INDICATOR != EUR_FX_DAILY_INDICATOR
    assert "EUR" not in EUR_FX_CURRENCIES


def test_monthly_end_of_period_rate_is_stamped_on_the_calendar_month_end(monkeypatch):
    monkeypatch.setattr(md, "_BCE_SERIES", {"fx_eom_eur_USD": md._BCE_SERIES["fx_eom_eur_USD"]})
    get, calls = _fake_get({"EXR/M.USD.EUR.SP00.E": _CSV_M_USD})
    monkeypatch.setattr(md.requests, "get", get)

    inf_rows, mac_rows = md.load_bce_series(desde="2026-08")

    assert inf_rows == []
    assert mac_rows == [
        {"date": "2026-08-31", "indicator": "fx_eom_eur", "geography": "USD", "value": 1.1596, "unit": "ccy_per_eur", "source": "BCE"},
        {"date": "2026-09-30", "indicator": "fx_eom_eur", "geography": "USD", "value": 1.1355, "unit": "ccy_per_eur", "source": "BCE"},
    ]
    assert calls[0][1]["startPeriod"] == "2026-08"


def test_month_end_stamping_respects_desde_on_the_period_not_on_the_stamped_date(monkeypatch):
    # 2026-08 is before desde=2026-09 and must be dropped although its stamped date (08-31) is a later day of the calendar
    monkeypatch.setattr(md, "_BCE_SERIES", {"fx_eom_eur_USD": md._BCE_SERIES["fx_eom_eur_USD"]})
    get, _ = _fake_get({"EXR/M.USD.EUR.SP00.E": _CSV_M_USD})
    monkeypatch.setattr(md.requests, "get", get)
    _, mac_rows = md.load_bce_series(desde="2026-09")
    assert [r["date"] for r in mac_rows] == ["2026-09-30"]


def test_daily_rate_keeps_its_business_day(monkeypatch):
    monkeypatch.setattr(md, "_BCE_SERIES", {"fx_d_eur_USD": md._BCE_SERIES["fx_d_eur_USD"]})
    get, _ = _fake_get({"EXR/D.USD.EUR.SP00.A": _CSV_D_USD})
    monkeypatch.setattr(md.requests, "get", get)
    _, mac_rows = md.load_bce_series(desde="2026-08")
    assert [(r["date"], r["indicator"], r["geography"], r["value"]) for r in mac_rows] == [
        ("2026-08-03", "fx_d_eur", "USD", 1.1535), ("2026-08-04", "fx_d_eur", "USD", 1.1515)]


def test_month_end_helper_handles_february_and_leap_years():
    assert md._month_end("2024-02-01") == "2024-02-29"
    assert md._month_end("2026-02-01") == "2026-02-28"
    assert md._month_end("2026-12-01") == "2026-12-31"


def test_conversion_ships_dormant_and_coupled_with_the_fx_eur_view():
    # Owner directive 2026-10-07: neither switch may be enabled alone (the startup assertion arrives in step 4).
    assert EUR_NAV_CONVERSION_ENABLED is False
    assert EUR_NAV_CONVERSION_ENABLED == FX_CONTRIBUTION_EUR_VIEW_ENABLED
