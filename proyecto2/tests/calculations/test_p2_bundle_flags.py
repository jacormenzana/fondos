# proyecto2/tests/calculations/test_p2_bundle_flags.py
# -*- coding: utf-8 -*-
"""
P2 recompute bundle flags (FND-0196, FND-0226, FND-0200, FND-0202): every correction is dormant by default and, when its
flag is on, changes exactly what the ticket says. Fake connections, no DB (R-7).

Run from proyecto2/:
    python -m pytest tests/calculations/test_p2_bundle_flags.py -v
"""

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import config
from src.calculations import capture_ratios, macro_sensitivity, persistence
from src.utils.fingerprint import effective_calc_version


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    """execute(sql, params=None) -> rows, whatever the statement."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        return _Result(self._rows)


def _set(monkeypatch, **flags):
    for name, value in flags.items():
        monkeypatch.setattr(config, name, value)


def _off(monkeypatch):
    """Legacy state: every bundle flag False (the production defaults before the 2026-10-04 flip). The tests below state
    the flag values they need instead of relying on the shipped defaults."""
    _set(monkeypatch, **{name: False for name in config.P2_BUNDLE_FLAGS})


# ---------------- the flag set ----------------

def test_bundle_flags_are_listed_and_are_booleans():
    assert config.P2_BUNDLE_FLAGS == ("MACRO_VIF_ITERATIVE_ENABLED", "MACRO_FACTOR_CLEAN_ENABLED",
                                      "PERSISTENCE_FIRST_LAST_NAV_ENABLED", "CAPTURE_MONTH_END_ENABLED",
                                      "ANNUALIZATION_INTERVAL_ENABLED", "DEFLATION_MONTH_ALIGN_ENABLED",
                                      "SORTINO_MIN_DOWNSIDE_COUNT_ENABLED", "FX_CONTRIBUTION_EUR_VIEW_ENABLED")
    assert all(isinstance(getattr(config, name), bool) for name in config.P2_BUNDLE_FLAGS)


# ---------------- fingerprint ----------------

def test_effective_calc_version_is_unchanged_while_every_flag_is_off(monkeypatch):
    _off(monkeypatch)
    assert effective_calc_version("20261003") == "20261003"


def test_effective_calc_version_changes_when_any_flag_is_on(monkeypatch):
    _off(monkeypatch)
    _set(monkeypatch, CAPTURE_MONTH_END_ENABLED=True)
    one = effective_calc_version("20261003")
    _set(monkeypatch, MACRO_FACTOR_CLEAN_ENABLED=True)
    two = effective_calc_version("20261003")
    assert one == "20261003+CAPTURE_MONTH_END_ENABLED"
    assert two == "20261003+CAPTURE_MONTH_END_ENABLED+MACRO_FACTOR_CLEAN_ENABLED" and one != two


# ---------------- FND-0196: iterative VIF options ----------------

def test_vif_options_are_empty_when_off_and_come_from_config_when_enabled(monkeypatch):
    _off(monkeypatch)
    assert macro_sensitivity.vif_options_from_config() == {}
    _set(monkeypatch, MACRO_VIF_ITERATIVE_ENABLED=True)
    o = macro_sensitivity.vif_options_from_config()
    assert o["vif_mode"] == "iterative" and o["extra_priority"] == {"spread_hy", "vix_yoy"}
    assert o["exclude"] == {"spread_ig"} and o["max_factors_per_obs"] == 10.0


# ---------------- FND-0226: macro factor cleaning ----------------

def _cn_cpi_rows(n=40, zero_at=20):
    rows = []
    for k in range(n):
        d = (pd.Timestamp("2018-01-31") + pd.offsets.MonthEnd(k)).date()
        rows.append((d, "ipc_index", "CN", 0.0 if k == zero_at else 100.0 + k))
    return rows


def test_a_zero_cpi_level_gives_infinite_factor_cells_unless_cleaned():
    conn = _FakeConn(_cn_cpi_rows())
    raw = macro_sensitivity.load_macro_factors(conn, clean=False)
    clean = macro_sensitivity.load_macro_factors(conn, clean=True)
    assert np.isinf(raw["ipc_yoy_cn"].to_numpy(dtype=float)).any()
    assert not np.isinf(clean["ipc_yoy_cn"].to_numpy(dtype=float)).any()
    assert clean["ipc_yoy_cn"].isna().sum() > raw["ipc_yoy_cn"].isna().sum()


def test_cleaning_follows_the_config_flag_by_default(monkeypatch):
    _off(monkeypatch)
    conn = _FakeConn(_cn_cpi_rows())
    assert np.isinf(macro_sensitivity.load_macro_factors(conn)["ipc_yoy_cn"].to_numpy(dtype=float)).any()
    _set(monkeypatch, MACRO_FACTOR_CLEAN_ENABLED=True)
    assert not np.isinf(macro_sensitivity.load_macro_factors(conn)["ipc_yoy_cn"].to_numpy(dtype=float)).any()


# ---------------- FND-0200: peer return from first / last NAV ----------------

_PEER_ROWS = [("PEER1", 80.0, 100.0, 36, 100.0, 80.0)]     # min 80, max 100, but the NAV FELL from 100 to 80


def _peer_return():
    return persistence._category_return_in_window(
        _FakeConn(_PEER_ROWS), "Renta Variable", "OWN", pd.Timestamp("2018-01-31"), pd.Timestamp("2021-01-31"))


def test_peer_return_uses_min_max_when_the_flag_is_off_which_reports_a_gain_for_a_falling_fund(monkeypatch):
    _off(monkeypatch)
    assert _peer_return() == pytest.approx((100.0 / 80.0) ** (1 / 3.0) - 1)


def test_peer_return_uses_first_and_last_nav_when_enabled(monkeypatch):
    _set(monkeypatch, PERSISTENCE_FIRST_LAST_NAV_ENABLED=True)
    assert _peer_return() == pytest.approx((80.0 / 100.0) ** (1 / 3.0) - 1)
    assert _peer_return() < 0


# ---------------- FND-0202: capture ratios on a month-end grid ----------------

def _peer_nav_rows(n=90, gap_at=None, seed=2):
    rng = np.random.default_rng(seed)
    nav, rows = 100.0, []
    for k in range(n):
        nav *= 1.0 + rng.normal(0.003, 0.02)
        if k == gap_at:
            continue
        rows.append(("PEER1", (pd.Timestamp("2015-01-31") + pd.offsets.MonthEnd(k)).date(), nav))
    return rows


def test_a_gap_in_a_peer_series_is_a_multi_month_return_unless_the_grid_is_enforced(monkeypatch):
    _off(monkeypatch)
    conn = _FakeConn(_peer_nav_rows(gap_at=10))
    old = capture_ratios.load_peer_benchmark(conn, "Renta Variable", "OWN")
    _set(monkeypatch, CAPTURE_MONTH_END_ENABLED=True)
    new = capture_ratios.load_peer_benchmark(conn, "Renta Variable", "OWN")
    after_gap = pd.Timestamp("2015-01-31") + pd.offsets.MonthEnd(11)
    assert after_gap in old.index and after_gap not in new.index          # the 2-month return is no longer a "1-month" one
    assert len(new) == len(old) - 1


def _fund_nav_mid_month(n=90, seed=5):
    rng = np.random.default_rng(seed)
    dates = [pd.Timestamp("2015-01-15") + pd.DateOffset(months=k) for k in range(n)]     # 15th of every month
    nav = 100.0 * np.cumprod(1.0 + rng.normal(0.003, 0.02, n))
    return pd.DataFrame({"date": dates, "nav": nav})


def test_mid_month_fund_dates_find_no_overlap_with_the_month_end_benchmark_unless_normalised(monkeypatch):
    _off(monkeypatch)
    conn = _FakeConn(_peer_nav_rows())
    nav = _fund_nav_mid_month()
    assert capture_ratios.compute_capture_ratios("OWN", "Renta Variable", nav, conn) == []
    _set(monkeypatch, CAPTURE_MONTH_END_ENABLED=True)
    out = capture_ratios.compute_capture_ratios("OWN", "Renta Variable", nav, conn)
    assert {m for m, _, _ in out} == {"upside_capture", "downside_capture", "capture_ratio"}
