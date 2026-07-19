# proyecto2/tests/calculations/test_nav_scale_repair_20260719.py
# -*- coding: utf-8 -*-
"""
Tests for FIX-P2-NAV-SCALE-1 (2026-07-19).

Two corruption patterns are covered:

  Pattern A — two-source block scale mismatch:
    MORNINGSTAR (sal-service, ALL rows at ~×1000 scale) coexists with
    MORNINGSTAR_CHART (chartservice, normal scale). Corrected by source-median
    rescaling in _normalize_nav_scale().

  Pattern B — isolated spikes within one source:
    MORNINGSTAR returns the accumulated total-return index value on specific
    calendar month-end dates, producing isolated ×100 spikes inside an otherwise
    clean series (e.g. 87.92 on Jan 29 → 9291.90 on Jan 31 → 90.00 on Feb 29).
    Detected and deleted by _find_spike_rows() (repair script) and the Patrón-B
    filter in _normalize_nav_scale().

R-7 compliant: no pipeline.py / core.io imports.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Sys-path setup
# ---------------------------------------------------------------------------
_HERE    = Path(__file__).parent
_P2_ROOT = _HERE.parent.parent  # proyecto2/
_REPO    = _P2_ROOT.parent      # c:/desarrollo/fondos
for _p in [str(_P2_ROOT), str(_REPO)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proyecto2.src.discovery.nav_discovery import _normalize_nav_scale
from proyecto2.src.utils.validators import validate_nav
from proyecto2.src.calculations.srri import compute_srri

# Import the spike-detection helper from the repair script
sys.path.insert(0, str(_REPO / "scripts" / "mig"))
from repair_nav_scale_20260719 import _find_spike_rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(date: str, nav: float, source: str = "MORNINGSTAR_CHART") -> dict:
    return {
        "ISIN": "IE00TEST0001",
        "Date": date,
        "NAV": nav,
        "NAV_Currency": "EUR",
        "NAV_Type": "TotalReturn",
        "Is_Estimated": 0,
        "Data_Source": source,
    }


def _nav_df(navs, dates=None):
    """Build a validate_nav-compatible DataFrame."""
    n = len(navs)
    if dates is None:
        dates = pd.date_range("2020-01-01", periods=n, freq="MS")
    return pd.DataFrame({"date": dates, "nav": navs})


# ---------------------------------------------------------------------------
# Pattern A: two-source block scale mismatch → rescaling
# ---------------------------------------------------------------------------

class TestPatternA_SourceRescaling:
    """_normalize_nav_scale: Patrón A — MORNINGSTAR rows at uniform ×1000 scale
    are rescaled to match MORNINGSTAR_CHART reference."""

    def test_x1000_ms_rows_rescaled_to_chart_scale(self):
        """Historical MORNINGSTAR rows all at ×1000 scale are divided by 1000."""
        rows = [
            _make_row("2023-11-01", 9130.0, "MORNINGSTAR"),
            _make_row("2023-12-01", 9110.0, "MORNINGSTAR"),
            _make_row("2024-01-01", 9.12,   "MORNINGSTAR_CHART"),
            _make_row("2024-02-01", 9.25,   "MORNINGSTAR_CHART"),
        ]
        result = _normalize_nav_scale(rows)
        ms_navs = [r["NAV"] for r in result if r["Data_Source"] == "MORNINGSTAR"]
        assert ms_navs, "MORNINGSTAR rows must not be removed in Pattern A"
        assert all(9.0 <= v <= 10.0 for v in ms_navs), (
            f"MORNINGSTAR rows must be rescaled to ×1 scale after ÷1000; "
            f"got {ms_navs}"
        )

    def test_all_navs_in_same_magnitude_after_rescale(self):
        """After Pattern-A normalization all NAVs must be in the same order of magnitude."""
        rows = [
            _make_row("2024-01-01", 10.0,   "MORNINGSTAR_CHART"),
            _make_row("2024-02-01", 10.2,   "MORNINGSTAR_CHART"),
            _make_row("2023-10-01", 10000.0, "MORNINGSTAR"),
            _make_row("2023-11-01", 10200.0, "MORNINGSTAR"),
            _make_row("2023-12-01",  9800.0, "MORNINGSTAR"),
        ]
        result = _normalize_nav_scale(rows)
        navs = [r["NAV"] for r in result]
        assert max(navs) / min(navs) < 100, (
            f"All NAVs must be in the same order of magnitude after rescale; "
            f"max={max(navs)}, min={min(navs)}"
        )

    def test_idempotent_after_source_rescale(self):
        """Applying _normalize_nav_scale twice yields the same result."""
        rows = [
            _make_row("2023-12-01", 9500.0, "MORNINGSTAR"),
            _make_row("2024-01-01", 9.5,    "MORNINGSTAR_CHART"),
        ]
        once  = _normalize_nav_scale(rows)
        twice = _normalize_nav_scale(once)
        navs_once  = sorted(r["NAV"] for r in once)
        navs_twice = sorted(r["NAV"] for r in twice)
        assert len(navs_once) == len(navs_twice)
        for a, b in zip(navs_once, navs_twice):
            assert abs(a - b) < 1e-9, f"Idempotency violated: {a} != {b}"

    def test_single_source_series_untouched(self):
        """A series with only MORNINGSTAR_CHART rows is returned unchanged."""
        rows = [
            _make_row("2024-01-01", 9.10, "MORNINGSTAR_CHART"),
            _make_row("2024-02-01", 9.25, "MORNINGSTAR_CHART"),
            _make_row("2024-03-01", 9.18, "MORNINGSTAR_CHART"),
        ]
        result = _normalize_nav_scale(rows)
        navs_in  = sorted(r["NAV"] for r in rows)
        navs_out = sorted(r["NAV"] for r in result)
        assert navs_in == navs_out, "Single-source series must not be modified"

    def test_non_clean_10n_ratio_not_rescaled(self):
        """A ×3.7 inter-source ratio (not a clean 10^n) must not be rescaled.
        Only 3 rows → pattern-B filter also skips (< 3 after first check)."""
        rows = [
            _make_row("2024-01-01", 10.0, "MORNINGSTAR_CHART"),
            _make_row("2024-02-01", 10.2, "MORNINGSTAR_CHART"),
            _make_row("2023-12-01", 37.0, "MORNINGSTAR"),  # ratio ≈ 3.7
        ]
        result = _normalize_nav_scale(rows)
        ms_row = next(r for r in result if r["Data_Source"] == "MORNINGSTAR")
        assert ms_row["NAV"] == pytest.approx(37.0), (
            f"Non-10^n ratio must not trigger rescale; got {ms_row['NAV']}"
        )


# ---------------------------------------------------------------------------
# Pattern B: isolated NAV spikes → deletion (repair script + ingestion guard)
# ---------------------------------------------------------------------------

class TestPatternB_IsolatedSpikes:
    """_find_spike_rows (repair) and _normalize_nav_scale (ingestion guard)
    correctly handle calendar-month-end total-return-index spikes."""

    def test_find_spike_rows_detects_interior_spike(self):
        """An interior row with NAV > 8× both neighbors is returned as a spike."""
        rows = [
            _make_row("2016-01-29", 87.92,   "MORNINGSTAR"),
            _make_row("2016-01-31", 9291.90, "MORNINGSTAR"),  # spike ×106
            _make_row("2016-02-29", 90.00,   "MORNINGSTAR"),
        ]
        spikes = _find_spike_rows(rows)
        assert len(spikes) == 1, f"Expected 1 spike; got {len(spikes)}"
        assert spikes[0][1] == "2016-01-31", f"Spike date must be 2016-01-31; got {spikes[0][1]}"

    def test_find_spike_rows_detects_edge_spike(self):
        """A last-row spike (no next neighbor) is detected when there is a preceding
        clean row that confirms the scale (needs ≥3 rows to run; test uses 3)."""
        rows = [
            _make_row("2026-04-30", 308.50,   "MORNINGSTAR"),
            _make_row("2026-05-29", 311.40,   "MORNINGSTAR_CHART"),
            _make_row("2026-05-31", 30645.49, "MORNINGSTAR"),  # edge spike ×98
        ]
        spikes = _find_spike_rows(rows)
        assert len(spikes) == 1, f"Expected 1 edge spike; got {len(spikes)}"
        assert spikes[0][1] == "2026-05-31"

    def test_find_spike_rows_clean_series_no_spikes(self):
        """A clean equity fund series with ~10% annual vol has no spikes."""
        rows = [
            _make_row(f"2020-{m:02d}-01", nav, "MORNINGSTAR")
            for m, nav in enumerate(
                [100, 102, 99, 105, 103, 108, 106, 111, 109, 113, 110, 115], start=1
            )
        ]
        spikes = _find_spike_rows(rows)
        assert spikes == [], f"Clean series must have 0 spikes; got {spikes}"

    def test_normalize_nav_scale_removes_isolated_spike(self):
        """Ingestion guard removes an isolated spike in a ≥3 row batch."""
        rows = [
            _make_row("2016-01-29", 87.92,   "MORNINGSTAR"),
            _make_row("2016-01-31", 9291.90, "MORNINGSTAR"),  # interior spike ×106
            _make_row("2016-02-29", 90.00,   "MORNINGSTAR"),
            _make_row("2016-04-29", 93.08,   "MORNINGSTAR"),
            _make_row("2016-04-30", 9837.24, "MORNINGSTAR"),  # interior spike ×106
            _make_row("2016-05-31", 95.65,   "MORNINGSTAR"),
        ]
        result = _normalize_nav_scale(rows)
        result_navs = sorted(r["NAV"] for r in result)
        assert all(v < 200 for v in result_navs), (
            f"All spikes must be removed; remaining NAVs: {result_navs}"
        )
        assert len(result) == 4, f"Expected 4 rows (2 spikes removed); got {len(result)}"

    def test_normalize_nav_scale_removes_edge_spike(self):
        """Ingestion guard removes a spike at the end of the batch."""
        rows = [
            _make_row("2026-05-29", 311.40,   "MORNINGSTAR_CHART"),
            _make_row("2026-05-31", 30645.49, "MORNINGSTAR"),  # edge spike
            _make_row("2026-06-30", 315.00,   "MORNINGSTAR"),
        ]
        result = _normalize_nav_scale(rows)
        assert len(result) == 2, f"Edge spike must be removed; got {len(result)} rows"
        assert all(r["NAV"] < 1000 for r in result)


# ---------------------------------------------------------------------------
# validate_nav: adjacent-jump guard
# ---------------------------------------------------------------------------

class TestValidateNav:
    """FIX-P2-NAV-SCALE-1: validate_nav scale-jump guard."""

    def test_rejects_series_with_8x_adjacent_jump(self):
        """A series with a ×106 NAV jump must fail validation."""
        df = _nav_df([87.92, 9291.90, 90.00, 93.08])
        ok, msg = validate_nav(df)
        assert not ok, "Series with ×106 NAV jump must not pass validate_nav"
        assert "8x" in msg or "escala" in msg.lower(), (
            f"Error message should mention 8x or scale; got: {msg!r}"
        )

    def test_rejects_series_with_inverse_8x_drop(self):
        """A 1/100 drop (ratio = 0.01 < 0.125) must also fail."""
        df = _nav_df([9100.0, 9200.0, 9.1, 9.3])
        ok, msg = validate_nav(df)
        assert not ok, "Series with 1/100 drop must not pass validate_nav"

    def test_clean_series_passes(self):
        """A realistic monthly NAV series for an equity fund must pass."""
        navs = [100.0, 101.5, 99.8, 103.2, 102.0, 105.5,
                104.1, 107.0, 106.3, 109.2, 108.0, 111.5, 110.0]
        df = _nav_df(navs)
        ok, msg = validate_nav(df)
        assert ok, f"Clean NAV series must pass validate_nav; got: {msg!r}"

    def test_money_market_tight_range_passes(self):
        """Money-market fund with very tight NAV range passes."""
        base = 10000.0
        navs = [base + i * 0.5 for i in range(24)]
        df = _nav_df(navs)
        ok, msg = validate_nav(df)
        assert ok, f"Money-market NAV series must pass validate_nav; got: {msg!r}"


# ---------------------------------------------------------------------------
# compute_srri: anomalous-vol guard
# ---------------------------------------------------------------------------

class TestComputeSrriAnomalousVol:
    """FIX-P2-NAV-SCALE-1: compute_srri anomalous-vol guard."""

    def test_corrupt_series_returns_anomalous_vol_not_srri7(self):
        """A ×100 NAV spike must return method='ANOMALOUS_VOL' and srri=0."""
        navs = [87.92 + i * 0.5 for i in range(24)]
        navs[12] = 9291.90   # isolated spike
        series = pd.Series(navs, dtype=float)
        result = compute_srri(series)
        assert result["method"] == "ANOMALOUS_VOL", (
            f"Corrupt series must return ANOMALOUS_VOL; "
            f"got method={result['method']!r}, vol_ann={result['volatility_ann']:.2f}"
        )
        assert result["srri"] == 0, (
            f"ANOMALOUS_VOL must return srri=0, not {result['srri']}"
        )

    def test_clean_bond_fund_returns_low_srri(self):
        """A clean bond-fund NAV series (low vol) must return srri ≤ 4."""
        np.random.seed(42)
        monthly_returns = np.random.normal(0.003, 0.004, 60)
        nav = [100.0]
        for r in monthly_returns:
            nav.append(nav[-1] * (1 + r))
        result = compute_srri(pd.Series(nav, dtype=float))
        assert result["method"] != "ANOMALOUS_VOL", (
            "Clean bond series must not trigger ANOMALOUS_VOL"
        )
        assert 1 <= result["srri"] <= 4, (
            f"Bond fund expected srri 1-4; got {result['srri']}"
        )

    def test_clean_equity_fund_returns_mid_srri(self):
        """A realistic equity-fund NAV (vol ~12% ann) returns srri 3-6."""
        np.random.seed(99)
        monthly_returns = np.random.normal(0.007, 0.038, 60)
        nav = [100.0]
        for r in monthly_returns:
            nav.append(nav[-1] * (1 + r))
        result = compute_srri(pd.Series(nav, dtype=float))
        assert result["method"] != "ANOMALOUS_VOL"
        assert 3 <= result["srri"] <= 6, (
            f"Equity fund expected srri 3-6; got {result['srri']}"
        )
