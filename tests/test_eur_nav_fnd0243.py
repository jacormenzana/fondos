# tests/test_eur_nav_fnd0243.py
# -*- coding: utf-8 -*-
"""
FND-0243 step 3: shared/eur_nav.py -- the EUR view of a share-class NAV (pure functions + the dormant switch).
Rates are units of currency per 1 EUR (ECB), so NAV_EUR = NAV / rate, taken from the latest rate on or before the
NAV date. No DB: rates and currencies are passed in, or a fake connection serves them.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from shared import config  # noqa: E402
from shared import eur_nav as en  # noqa: E402


def _rates(**series):
    return {c: en._norm_rates(pd.DataFrame(rows, columns=["date", "rate"])) for c, rows in series.items()}


USD = [("2026-07-31", 1.10), ("2026-08-28", 1.16), ("2026-09-30", 1.135)]   # 2026-08-28 = Friday


def _nav(rows):
    df = pd.DataFrame(rows, columns=["date", "nav"])
    df["date"] = pd.to_datetime(df["date"])
    return df


# --- to_eur ----------------------------------------------------------------------------------------------------

def test_eur_class_is_returned_unchanged():
    nav = _nav([("2026-08-31", 100.0)])
    out, status, dropped = en.to_eur(nav, "EUR", _rates(USD=USD))
    assert out is nav and status == en.STATUS_EUR and dropped == 0


def test_usd_class_divided_by_the_latest_rate_on_or_before_each_date():
    # The rate series here has no 2026-08-31 observation, so the 08-31 NAV takes the latest earlier one (08-28).
    nav = _nav([("2026-07-31", 110.0), ("2026-08-31", 116.0), ("2026-09-30", 113.5)])
    out, status, dropped = en.to_eur(nav, "usd", _rates(USD=USD))
    assert status == en.STATUS_CONVERTED and dropped == 0
    assert out["nav"].round(10).tolist() == [100.0, 100.0, 100.0]
    assert list(out.columns) == ["date", "nav"]
    assert out["date"].tolist() == nav["date"].tolist()


def test_open_intra_month_row_uses_the_last_available_rate_within_the_gap():
    nav = _nav([("2026-10-05", 113.5)])
    out, status, _ = en.to_eur(nav, "USD", _rates(USD=USD))
    assert status == en.STATUS_CONVERTED and out["nav"].iloc[0] == pytest.approx(100.0)


def test_rows_without_a_rate_within_the_gap_are_dropped_not_extrapolated():
    nav = _nav([("2026-06-30", 105.0), ("2026-07-31", 110.0), ("2026-10-20", 113.5)])
    out, status, dropped = en.to_eur(nav, "USD", _rates(USD=USD))
    assert status == en.STATUS_PARTIAL and dropped == 2
    assert out["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-07-31"]


def test_unknown_currency_is_excluded_never_assumed_eur():
    nav = _nav([("2026-08-31", 100.0)])
    out, status, dropped = en.to_eur(nav, None, _rates(USD=USD))
    assert out.empty and list(out.columns) == ["date", "nav"] and status == en.STATUS_UNKNOWN_CCY and dropped == 1


def test_currency_without_rates_is_excluded():
    out, status, _ = en.to_eur(_nav([("2026-08-31", 100.0)]), "SEK", _rates(USD=USD))
    assert out.empty and status == en.STATUS_NO_FX


# --- convert_frame (many funds, one merge) -------------------------------------------------------------------------

def test_convert_frame_mixes_eur_usd_and_drops_excluded_funds():
    df = pd.DataFrame([
        ("E1", "2026-08-31", 50.0), ("U1", "2026-07-31", 110.0), ("U1", "2026-08-31", 116.0),
        ("N1", "2026-08-31", 9.0), ("S1", "2026-08-31", 7.0), ("E1", "2026-07-31", 49.0),
    ], columns=["isin", "date", "nav"])
    out = en.convert_frame(df, {"E1": "EUR", "U1": "USD", "N1": None, "S1": "SEK"}, _rates(USD=USD))
    assert set(out["isin"]) == {"E1", "U1"}
    assert out[out["isin"] == "E1"]["nav"].tolist() == [49.0, 50.0]                 # sorted by date, untouched
    assert out[out["isin"] == "U1"]["nav"].round(10).tolist() == [100.0, 100.0]
    assert list(out.columns) == ["isin", "date", "nav"]


def test_convert_frame_keeps_extra_columns_and_works_on_date_objects():
    import datetime as dt
    df = pd.DataFrame([("U1", dt.date(2026, 8, 31), 116.0, "f")], columns=["isin", "date", "nav", "pt"])
    out = en.convert_frame(df, {"U1": "USD"}, _rates(USD=USD))
    assert out.iloc[0]["pt"] == "f" and out.iloc[0]["nav"] == pytest.approx(100.0)


# --- the switch ------------------------------------------------------------------------------------------------------

class _FakeConn:
    def __init__(self):
        self.calls = 0

    def execute(self, sql, params=None):
        self.calls += 1
        class _R:
            def __init__(self, rows): self._rows = rows
            def fetchall(self): return self._rows
        if "FROM fund_master" in sql:
            return _R([("U1", "USD"), ("N1", None)])
        if params and params[0] == config.EUR_FX_DAILY_INDICATOR:
            return _R([("USD", d, r) for d, r in USD])
        return _R([])


@pytest.fixture(autouse=True)
def _fresh_cache():
    en.reset_cache()
    yield
    en.reset_cache()


def test_switch_off_is_an_identity_and_touches_no_table(monkeypatch):
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", False)
    conn = _FakeConn()
    nav = _nav([("2026-08-31", 116.0)])
    out = en.apply_eur_view(conn, "U1", nav)
    assert out is nav and out.attrs[en.ATTR] == en.STATUS_DISABLED and not en.is_excluded(out)
    frame = pd.DataFrame([("U1", "2026-08-31", 116.0)], columns=["isin", "date", "nav"])
    assert en.apply_eur_view_frame(conn, frame) is frame
    assert conn.calls == 0


def test_switch_on_converts_and_flags_exclusion(monkeypatch):
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", True)
    conn = _FakeConn()
    out = en.apply_eur_view(conn, "U1", _nav([("2026-08-31", 116.0)]))
    assert out["nav"].iloc[0] == pytest.approx(100.0) and out.attrs[en.ATTR] == en.STATUS_CONVERTED
    excl = en.apply_eur_view(conn, "N1", _nav([("2026-08-31", 1.0)]))
    assert excl.empty and en.is_excluded(excl)
    calls = conn.calls
    en.apply_eur_view(conn, "U1", _nav([("2026-08-31", 116.0)]))
    assert conn.calls == calls          # rates and currencies are read once per process


def test_monthly_end_of_period_series_is_the_fallback_without_daily_rates(monkeypatch):
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", True)

    class _MonthlyOnly(_FakeConn):
        def execute(self, sql, params=None):
            class _R:
                def __init__(self, rows): self._rows = rows
                def fetchall(self): return self._rows
            if "FROM fund_master" in sql:
                return _R([("G1", "GBP")])
            if params and params[0] == config.EUR_FX_MONTHLY_INDICATOR:
                return _R([("GBP", "2026-08-31", 0.85)])
            return _R([])
    out = en.apply_eur_view(_MonthlyOnly(), "G1", _nav([("2026-08-31", 85.0)]))
    assert out["nav"].iloc[0] == pytest.approx(100.0)
