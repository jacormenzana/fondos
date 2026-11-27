# proyecto2/tests/calculations/test_window_coverage_ytd_fnd0426_0427.py
# -*- coding: utf-8 -*-
"""
FND-0427 (window coverage rule) and FND-0426 (ytd return): hermetic tests of the pure functions in `returns.py`.

R-7: no pipeline.py, no core.io, no database. The thresholds that live in shared.config are passed explicitly where a case depends on them, and the
switch behaviour of `rolling_window_admissible` is tested by monkeypatching the config at call time (like every P2 kill-switch).
"""
import numpy as np
import pandas as pd
import pytest

from src.calculations.returns import (
    rolling_window_admissible,
    trailing_window,
    window_coverage_ok,
    window_span_ok,
    ytd_return,
)


def _monthly(first: str, last: str, drop=()) -> pd.DataFrame:
    """Month-end NAV rows from `first` to `last` (inclusive), nav growing 0.5% a month; `drop` = month-end dates to remove."""
    dates = pd.date_range(first, last, freq="ME")
    dates = dates[~dates.isin(pd.to_datetime(list(drop)))] if len(drop) else dates
    nav = 100.0 * (1.005 ** np.arange(len(dates)))
    return pd.DataFrame({"date": dates, "nav": nav})


# ---------------------------------------------------------------- span (a)
class TestWindowSpan:
    END = pd.Timestamp("2026-10-31")

    def test_exactly_the_horizon_passes(self):
        assert window_span_ok(self.END - pd.DateOffset(years=10), self.END, 120, tolerance_days=35)

    def test_tolerance_edge(self):
        edge = self.END - pd.DateOffset(months=120) + pd.Timedelta(days=35)
        assert window_span_ok(edge, self.END, 120, tolerance_days=35)
        assert not window_span_ok(edge + pd.Timedelta(days=1), self.END, 120, tolerance_days=35)

    def test_zero_tolerance_is_the_strict_rule(self):
        # momentum's former inline check: first NAV <= last - N months
        assert window_span_ok(self.END - pd.DateOffset(months=36), self.END, 36, tolerance_days=0)
        assert not window_span_ok(self.END - pd.DateOffset(months=36) + pd.Timedelta(days=1), self.END, 36, tolerance_days=0)

    def test_default_tolerance_comes_from_the_config(self):
        from shared import config
        assert config.ROLLING_WINDOW_COVERAGE_TOLERANCE_DAYS == 35
        edge = self.END - pd.DateOffset(months=60) + pd.Timedelta(days=35)
        assert window_span_ok(edge, self.END, 60)


# ----------------------------------------------------------- coverage (a)+(b)
class TestWindowCoverage:
    def test_a_three_year_fund_has_no_rolling_10y_but_has_rolling_3y(self):
        nav = _monthly("2023-10-31", "2026-10-31")                     # 37 month-ends
        assert not window_coverage_ok(nav, 120, tolerance_days=35)
        assert not window_coverage_ok(nav, 60, tolerance_days=35)
        assert window_coverage_ok(nav, 36, tolerance_days=35)
        assert window_coverage_ok(nav, 12, tolerance_days=35)

    def test_long_dense_history_covers_every_window(self):
        nav = _monthly("2010-01-31", "2026-10-31")
        for months in (12, 24, 36, 60, 120):
            assert window_coverage_ok(nav, months, tolerance_days=35)

    def test_one_missing_month_inside_the_window_is_continuous_enough(self):
        nav = _monthly("2014-01-31", "2026-10-31", drop=["2020-06-30"])
        assert window_coverage_ok(nav, 120, tolerance_days=35)

    def test_a_long_hole_inside_the_window_fails_the_continuity_condition(self):
        # 7 consecutive missing month-ends (longest gap 7 >= the 6-month limit) inside the 5-year window
        holes = pd.date_range("2022-03-31", periods=7, freq="ME")
        nav = _monthly("2014-01-31", "2026-10-31", drop=list(holes))
        assert window_span_ok(nav["date"].min(), nav["date"].max(), 60, 35)          # the span alone would pass
        assert not window_coverage_ok(nav, 60, tolerance_days=35)
        assert window_coverage_ok(nav, 24, tolerance_days=35)                         # the 2-year window sits after the hole

    def test_a_hole_outside_the_window_does_not_matter(self):
        holes = pd.date_range("2016-03-31", periods=8, freq="ME")
        nav = _monthly("2014-01-31", "2026-10-31", drop=list(holes))
        assert window_coverage_ok(nav, 60, tolerance_days=35)

    def test_empty_and_none_are_not_covered(self):
        assert not window_coverage_ok(pd.DataFrame(columns=["date", "nav"]), 12)
        assert not window_coverage_ok(None, 12)

    def test_the_rule_is_the_one_trailing_window_defines(self):
        nav = _monthly("2016-01-31", "2026-10-31")
        win = trailing_window(nav, 60)
        assert win["date"].min() > nav["date"].max() - pd.DateOffset(months=60)


# ------------------------------------------------------------ the switch
class TestAdmissibleSwitch:
    def test_switch_off_is_the_legacy_gate_rows_only(self, monkeypatch):
        from shared import config
        monkeypatch.setattr(config, "ROLLING_WINDOW_COVERAGE_ENABLED", False, raising=False)
        young = _monthly("2023-10-31", "2026-10-31")
        assert rolling_window_admissible(young, 120, min_rows=12)           # 37 rows inside the (partial) window: legacy writes it
        assert not rolling_window_admissible(young.head(8), 12, min_rows=12)

    def test_switch_on_adds_the_coverage_rule(self, monkeypatch):
        from shared import config
        monkeypatch.setattr(config, "ROLLING_WINDOW_COVERAGE_ENABLED", True, raising=False)
        young = _monthly("2023-10-31", "2026-10-31")
        assert not rolling_window_admissible(young, 120, min_rows=12)
        assert rolling_window_admissible(young, 36, min_rows=12)

    def test_the_new_switches_are_on_since_the_recompute_and_registered_bundle_flags(self):
        from shared import config
        from src.utils.family_versions import FLAG_FAMILIES
        for flag in ("ROLLING_WINDOW_COVERAGE_ENABLED", "YTD_METRIC_ENABLED"):
            assert getattr(config, flag) is True                                       # ON with CALC_VERSION 20261116 (owner order 2026-11-16)
            assert flag in config.P2_BUNDLE_FLAGS and flag in FLAG_FAMILIES


# --------------------------------------------- the timeseries engine follows
class TestRollingTimeseriesFollowsTheSameRule:
    WINDOWS = {"rolling_1y": 12, "rolling_3y": 36, "rolling_10y": 120}

    @staticmethod
    def _dates_by_window(rows):
        out = {}
        for r in rows:
            out.setdefault(r["window"], set()).add(r["date"])
        return out

    def test_off_is_the_legacy_gate_and_writes_the_partial_windows(self, monkeypatch):
        from shared import config
        from src.calculations.rolling_stats import compute_rolling_rows
        monkeypatch.setattr(config, "ROLLING_WINDOW_COVERAGE_ENABLED", False, raising=False)
        nav = _monthly("2023-10-31", "2026-10-31")                                  # 37 month-ends
        by_window = self._dates_by_window(compute_rolling_rows("X", nav, self.WINDOWS, min_obs=12))
        assert "rolling_10y" in by_window                                          # the defect: a 10y window for a 3-year-old fund

    def test_on_drops_the_points_whose_window_the_history_does_not_cover(self, monkeypatch):
        from shared import config
        from src.calculations.rolling_stats import compute_rolling_rows
        monkeypatch.setattr(config, "ROLLING_WINDOW_COVERAGE_ENABLED", True, raising=False)
        nav = _monthly("2023-10-31", "2026-10-31")
        by_window = self._dates_by_window(compute_rolling_rows("X", nav, self.WINDOWS, min_obs=12))
        assert "rolling_10y" not in by_window                                      # 37 months cannot cover 10 years
        assert min(by_window["rolling_3y"]) == "2026-09-30"                        # first NAV 2023-10-31: the 2026-09-30 window starts 31 days early, inside the 35-day tolerance
        assert "2024-10-31" in by_window["rolling_1y"]                             # a 1y window is covered from month 12 on

    def test_the_last_timeseries_point_agrees_with_the_scalar_rule(self, monkeypatch):
        from shared import config
        from src.calculations.rolling_stats import compute_rolling_rows
        monkeypatch.setattr(config, "ROLLING_WINDOW_COVERAGE_ENABLED", True, raising=False)
        for first in ("2023-10-31", "2021-10-31", "2016-10-31"):
            nav = _monthly(first, "2026-10-31")
            by_window = self._dates_by_window(compute_rolling_rows("X", nav, self.WINDOWS, min_obs=12))
            for name, months in self.WINDOWS.items():
                has_last_point = nav["date"].max().date().isoformat() in by_window.get(name, set())
                assert has_last_point == window_coverage_ok(nav, months), (first, name)

    def test_daily_series_keep_their_own_minimum(self, monkeypatch):
        from shared import config
        from src.calculations.rolling_stats import compute_rolling_rows
        monkeypatch.setattr(config, "ROLLING_WINDOW_COVERAGE_ENABLED", True, raising=False)
        days = pd.bdate_range("2026-01-02", "2026-10-30")
        nav = pd.DataFrame({"date": days, "nav": 100.0 * (1.0002 ** np.arange(len(days)))})
        rows = compute_rolling_rows("X", nav, {"rolling_6m": 6}, min_obs=15, periods_per_year=252)
        assert rows                                                                # the daily windows are not gated by the monthly coverage rule


# ------------------------------------------------------------------- ytd
class TestYtd:
    def _series(self, rows):
        return pd.DataFrame({"date": pd.to_datetime([d for d, _ in rows]), "nav": [v for _, v in rows]})

    def test_normal_fund_matches_the_nav_derived_value(self):
        nav = self._series([("2025-11-28", 135.02), ("2025-12-31", 135.80), ("2026-06-30", 139.94), ("2026-11-05", 139.99)])
        out = ytd_return(nav)
        assert out is not None and not out["partial"]
        assert out["value"] == pytest.approx(139.99 / 135.80 - 1.0)
        assert out["base_date"] == pd.Timestamp("2025-12-31") and out["end_date"] == pd.Timestamp("2026-11-05")

    def test_base_within_five_business_days_is_accepted(self):
        nav = self._series([("2025-12-26", 100.0), ("2026-03-31", 101.0)])      # Friday 26 Dec: 3 business days before 31 Dec
        assert ytd_return(nav)["value"] == pytest.approx(0.01)

    def test_a_stale_base_writes_no_row(self):
        nav = self._series([("2025-12-17", 100.0), ("2026-03-31", 101.0)])      # 10 business days before 31 Dec
        assert ytd_return(nav) is None
        assert ytd_return(nav, max_staleness_bdays=10) is not None

    def test_launched_this_year_is_partial_and_cumulative_since_the_first_nav(self):
        nav = self._series([("2026-02-27", 100.0), ("2026-06-30", 104.0), ("2026-10-31", 110.0)])
        out = ytd_return(nav)
        assert out["partial"] is True
        assert out["value"] == pytest.approx(0.10)
        assert out["base_date"] == pd.Timestamp("2026-02-27")

    def test_it_is_cumulative_never_annualized(self):
        nav = self._series([("2025-12-31", 100.0), ("2026-04-30", 110.0)])      # 4 months: +10% cumulative (annualizing would give ~34%)
        assert ytd_return(nav)["value"] == pytest.approx(0.10)

    def test_a_single_observation_or_nothing_has_no_return(self):
        assert ytd_return(self._series([("2026-05-29", 100.0)])) is None
        assert ytd_return(pd.DataFrame(columns=["date", "nav"])) is None
        assert ytd_return(None) is None

    def test_as_of_cuts_the_series_and_picks_that_years_base(self):
        nav = self._series([("2024-12-31", 90.0), ("2025-06-30", 95.0), ("2025-12-31", 100.0), ("2026-05-29", 110.0)])
        out = ytd_return(nav, as_of="2025-06-30")
        assert out["value"] == pytest.approx(95.0 / 90.0 - 1.0) and out["end_date"] == pd.Timestamp("2025-06-30")

    def test_non_positive_base_is_rejected(self):
        nav = self._series([("2025-12-31", 0.0), ("2026-03-31", 1.0)])
        assert ytd_return(nav) is None

    def test_the_real_series_uses_the_same_function(self):
        real = self._series([("2025-12-31", 100.0), ("2026-10-31", 101.5)])
        assert ytd_return(real)["value"] == pytest.approx(0.015)


# ------------------------------------------- FND-0443: which windows did the coverage rule reject? (the pruner needs them) ------------------------------------------
class TestUncoveredWindows:
    WINDOWS = {"rolling_1y": 12, "rolling_10y": 120}

    def _run(self, nav, monkeypatch, on=True, **kw):
        from shared import config
        from src.calculations.rolling_stats import compute_rolling_rows
        monkeypatch.setattr(config, "ROLLING_WINDOW_COVERAGE_ENABLED", on, raising=False)
        uncovered: set = set()
        rows = compute_rolling_rows("X", nav, self.WINDOWS, min_obs=12, uncovered_windows=uncovered, **kw)
        return rows, uncovered

    def test_a_window_with_no_covered_point_is_reported_although_it_writes_no_row(self, monkeypatch):
        rows, uncovered = self._run(_monthly("2023-10-31", "2026-10-31"), monkeypatch)
        assert "rolling_10y" in uncovered and not [r for r in rows if r["window"] == "rolling_10y"]

    def test_reporting_changes_no_row(self, monkeypatch):
        from src.calculations.rolling_stats import compute_rolling_rows
        nav = _monthly("2023-10-31", "2026-10-31")
        rows, _ = self._run(nav, monkeypatch)
        assert rows == compute_rolling_rows("X", nav, self.WINDOWS, min_obs=12)

    def test_too_few_observations_is_not_a_coverage_rejection(self, monkeypatch):
        rows, uncovered = self._run(_monthly("2026-05-31", "2026-10-31"), monkeypatch)           # 6 rows: nothing reaches the coverage rule
        assert rows == [] and uncovered == set()                                                   # a short computation never wipes a series

    def test_switch_off_reports_nothing(self, monkeypatch):
        _rows, uncovered = self._run(_monthly("2023-10-31", "2026-10-31"), monkeypatch, on=False)
        assert uncovered == set()

    def test_a_daily_series_is_not_judged(self, monkeypatch):
        _rows, uncovered = self._run(_monthly("2023-10-31", "2026-10-31"), monkeypatch, periods_per_year=252)
        assert uncovered == set()
