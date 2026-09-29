# proyecto1/tests/test_statistical_audit_timeseries.py
# -*- coding: utf-8 -*-
"""Tests unitarios de shared/statistical_audit/timeseries.py
(check_timeseries_integrity — funcion #11; build_window_deflation_frame — FND-0114
detectors, 2026-09-29, doc/reglas/AUDITORIA_ESTADISTICA.md §4/§5). Los huecos solo se
evaluan cuando el llamador aporta el calendario real (expected_dates); sin el, la
funcion no fabrica huecos falsos.

Cumple R-7: sin importar pipeline.py ni core.io. Los tests de build_window_deflation_frame
tampoco importan proyecto2 -- reimplementan localmente (helpers _correct_deflate_real /
_buggy_deflate_real) la misma matematica que deflation.py::deflate_nav() (el fix, merge_asof
backward + bfill del hueco inicial) y la que rolling_stats.py usaba antes de a483f49 (merge
exacto por fecha + ffill().bfill() posicional, el defecto de FND-0114) para mantener este
archivo autocontenido; ambas replicas estan comentadas linea a linea contra el diff real.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from shared.statistical_audit.invariants import check_invariant
from shared.statistical_audit.catalog_invariants import P2_INVARIANTS
from shared.statistical_audit.timeseries import build_window_deflation_frame, check_timeseries_integrity

_FISHER = next(r for r in P2_INVARIANTS if r.rule_id == "WINDOW_FISHER_IDENTITY")
_STRICT = next(r for r in P2_INVARIANTS if r.rule_id == "WINDOW_DEFLATION_STRICT")
_NOMINAL = next(r for r in P2_INVARIANTS if r.rule_id == "WINDOW_NOMINAL_IDENTITY")


def _roll_return_ann(nav_start: float, nav_end: float, n_obs: int, periods_per_year: int = 12) -> float:
    """Mirrors rolling_stats.py::_roll_return_ann on a 2-point [start, end] window slice --
    the power-annualization formula is the only piece that matters for these tests (interior
    points of the window never enter the invariant, only its endpoints)."""
    years = n_obs / periods_per_year
    return (nav_end / nav_start) ** (1.0 / years) - 1.0


def _correct_deflate_real(nav_df: pd.DataFrame, ipc_df: pd.DataFrame) -> np.ndarray:
    """deflation.py::deflate_nav()'s algorithm (the a483f49 fix), reimplemented inline:
    merge_asof(direction='backward'), bfill() only the genuinely-uncovered leading gap,
    rebase to the first row. Kept in lockstep with the production function so a future
    divergence between them is caught by a live smoke test, not silently trusted here."""
    nav_df = nav_df.sort_values("date").reset_index(drop=True)
    ipc_df = ipc_df[["date", "ipc_index"]].sort_values("date").reset_index(drop=True)
    merged = pd.merge_asof(nav_df, ipc_df, on="date", direction="backward")
    if merged["ipc_index"].isna().any():
        merged["ipc_index"] = merged["ipc_index"].bfill()
    ipc_base = merged["ipc_index"].iloc[0]
    return (merged["nav"].to_numpy() / (merged["ipc_index"].to_numpy() / ipc_base))


def _buggy_deflate_real(nav_df: pd.DataFrame, ipc_df: pd.DataFrame) -> np.ndarray:
    """The pre-a483f49 rolling_stats.py logic, reimplemented inline verbatim from the removed
    code (git show a483f49 -- proyecto2/src/calculations/rolling_stats.py): exact-date LEFT
    merge (no fallback for a non-exact NAV date, e.g. a business-day date vs IPC's
    month-end grid, or a NAV date genuinely before IPC coverage begins) followed by a
    POSITIONAL ffill().bfill() over the whole aligned array and a rebase to position 0 --
    this is the FND-0114 defect class: the leading NaN block (nothing to ffill from) gets
    filled by bfill() with the FIRST LATER available IPC value instead of the correct
    backward-looking one, and since it's the rebase anchor, it distorts every value."""
    nav_df = nav_df.sort_values("date").reset_index(drop=True)
    merged = nav_df[["date"]].merge(
        ipc_df[["date", "ipc_index"]], on="date", how="left",
    )
    ipc_vals = merged["ipc_index"].to_numpy(dtype=float)
    ipc_vals = pd.Series(ipc_vals).ffill().bfill().to_numpy(dtype=float)
    return nav_df["nav"].to_numpy() / (ipc_vals / ipc_vals[0])


def test_duplicate_dates_detected():
    df = pd.DataFrame({
        "isin": ["A", "A", "B"],
        "date": ["2026-01-31", "2026-01-31", "2026-01-31"],
        "value": [1.0, 1.1, 2.0],
    })
    r = check_timeseries_integrity(df, entity_keys=["isin"], date_column="date")
    assert len(r.duplicates) == 2  # both A rows, not the lone B row
    assert r.n_series == 2


def test_no_gaps_reported_without_expected_dates():
    df = pd.DataFrame({
        "isin": ["A", "A"],
        "date": ["2026-01-31", "2026-03-31"],  # a real gap, but undeclared
        "value": [1.0, 1.2],
    })
    r = check_timeseries_integrity(df, entity_keys=["isin"], date_column="date")
    assert r.gaps.empty  # never fabricate a gap from an assumed calendar


def test_gaps_detected_against_supplied_calendar():
    df = pd.DataFrame({
        "isin": ["A", "A"],
        "date": ["2026-01-31", "2026-03-31"],
        "value": [1.0, 1.2],
    })
    expected = pd.DatetimeIndex(["2026-01-31", "2026-02-28", "2026-03-31"])
    r = check_timeseries_integrity(
        df, entity_keys=["isin"], date_column="date", expected_dates=expected,
    )
    assert len(r.gaps) == 1
    assert r.gaps.iloc[0]["date"] == pd.Timestamp("2026-02-28")


def test_per_entity_calendar_mapping():
    df = pd.DataFrame({
        "isin": ["A", "B"],
        "date": ["2026-01-31", "2026-01-31"],
        "value": [1.0, 2.0],
    })
    expected = {
        ("A",): pd.DatetimeIndex(["2026-01-31", "2026-02-28"]),
        ("B",): pd.DatetimeIndex(["2026-01-31"]),
    }
    r = check_timeseries_integrity(
        df, entity_keys=["isin"], date_column="date", expected_dates=expected,
    )
    assert len(r.gaps) == 1
    assert r.gaps.iloc[0]["isin"] == "A"


def test_empty_frame_handled():
    df = pd.DataFrame(columns=["isin", "date", "value"])
    r = check_timeseries_integrity(df, entity_keys=["isin"], date_column="date")
    assert r.n_series == 0
    assert r.duplicates.empty
    assert r.gaps.empty


# ============================================================
# build_window_deflation_frame (FND-0114 detectors, 2026-09-29)
# ============================================================

def _nav_ipc_leading_gap_fixture():
    """Mirrors proyecto2/tests/calculations/test_rolling_stats.py's
    test_ipc_leading_gap_uses_correct_earlier_anchor_not_a_later_one fixture verbatim (same
    dates/values) -- a proven-diverging case: NAV's 2023-09-29 has no exact IPC match (IPC is
    month-end, this NAV date is a business day one day short of it), so the correct backward
    anchor is Aug=95.29 while a naive positional bfill grabs the look-ahead Sep=96.09 instead."""
    nav_df = pd.DataFrame({
        "date": pd.to_datetime(["2023-09-29", "2023-10-31", "2023-11-30"]),
        "nav": [100.0, 102.0, 101.0],
    })
    ipc_df = pd.DataFrame({
        "date": pd.to_datetime(["2023-08-31", "2023-09-30", "2023-10-31", "2023-11-30"]),
        "ipc_index": [95.29, 96.09, 96.09, 95.58],
    })
    return nav_df, ipc_df


def _window_row(isin: str, nav_df: pd.DataFrame, nav_real: np.ndarray, window_label="rolling_3m"):
    """One ts row spanning nav_df's full [first, last] range -- the shape
    build_window_deflation_frame expects (isin, date, window_label, w_return_nominal,
    w_return_real, w_n_obs), built from an already-deflated nav_real array (either the
    correct or the buggy one) via _roll_return_ann."""
    n_obs = len(nav_df)
    nominal = _roll_return_ann(nav_df["nav"].iloc[0], nav_df["nav"].iloc[-1], n_obs)
    real = _roll_return_ann(nav_real[0], nav_real[-1], n_obs)
    return pd.DataFrame([{
        "isin": isin, "date": nav_df["date"].iloc[-1], "window_label": window_label,
        "w_return_nominal": nominal, "w_return_real": real, "w_n_obs": n_obs,
    }])


class TestBuildWindowDeflationFrame:
    def test_correct_deflation_gives_zero_violations(self):
        """A window whose stored real return_ann was computed via the CORRECT (a483f49)
        deflate_nav() contract must satisfy WINDOW_NOMINAL_IDENTITY, WINDOW_DEFLATION_STRICT
        and WINDOW_FISHER_IDENTITY -- including on the exact fixture that proves the FND-0114
        divergence when the WRONG contract is used (see test below)."""
        nav_df, ipc_df = _nav_ipc_leading_gap_fixture()
        nav_real = _correct_deflate_real(nav_df, ipc_df)
        ts = _window_row("CORRECT01", nav_df, nav_real)
        nav_dates = nav_df.rename(columns={}).assign(isin="CORRECT01")[["isin", "date", "nav"]]

        frame = build_window_deflation_frame(ts, nav_dates, ipc_df)

        for rule in (_NOMINAL, _STRICT, _FISHER):
            result = check_invariant(frame, rule)
            assert result.n_applicable == 1, rule.rule_id
            assert result.n_violations == 0, f"{rule.rule_id}: {result.violations.to_dict('records')}"

    def test_fnd0114_reproduction_fails_fisher_identity(self):
        """Same fixture, but the stored real return_ann comes from the PRE-a483f49 logic
        (exact-date merge + positional ffill().bfill(), the FND-0114 defect). The look-ahead
        anchor (96.09 instead of 95.29) must trip WINDOW_FISHER_IDENTITY -- this is the
        detector's whole reason for existing: catching FND-0114-class rows that a literal
        "real==nominal at t=0" rule cannot (deflate_nav() rebases the deflator to 1 at t=0
        under ANY anchor, correct or buggy)."""
        nav_df, ipc_df = _nav_ipc_leading_gap_fixture()
        nav_real_buggy = _buggy_deflate_real(nav_df, ipc_df)
        nav_real_correct = _correct_deflate_real(nav_df, ipc_df)
        # Sanity: the fixture must actually exercise the divergence, not coincide by accident
        # (mirrors the production regression test's own such check).
        assert not np.allclose(nav_real_buggy, nav_real_correct)

        ts = _window_row("BUGGY0001", nav_df, nav_real_buggy)
        nav_dates = nav_df.assign(isin="BUGGY0001")[["isin", "date", "nav"]]

        frame = build_window_deflation_frame(ts, nav_dates, ipc_df)

        fisher = check_invariant(frame, _FISHER)
        assert fisher.n_applicable == 1
        assert fisher.n_violations == 1
        assert fisher.violations.iloc[0]["isin"] == "BUGGY0001"

        # Bonus confirmation (numerically verified, not assumed): on this fixture the buggy
        # anchor also happens to push real ABOVE nominal (0.063 vs 0.041), so
        # WINDOW_DEFLATION_STRICT independently catches the same row too.
        strict = check_invariant(frame, _STRICT)
        assert strict.n_applicable == 1
        assert strict.n_violations == 1

    def test_undecidable_row_excluded_from_n_applicable(self):
        """A ts row whose date isn't a NAV date at all (end_pos unresolved) must be left out
        of n_applicable entirely -- undecidable, never counted as violating (P#1/R-4)."""
        nav_df, ipc_df = _nav_ipc_leading_gap_fixture()
        nav_dates = nav_df.assign(isin="GHOST0001")[["isin", "date", "nav"]]
        ts = pd.DataFrame([{
            "isin": "GHOST0001", "date": pd.Timestamp("2024-03-31"),  # not in nav_dates
            "window_label": "rolling_3m", "w_return_nominal": 0.05, "w_return_real": 0.03,
            "w_n_obs": 3,
        }])

        frame = build_window_deflation_frame(ts, nav_dates, ipc_df)

        for rule in (_NOMINAL, _STRICT, _FISHER):
            result = check_invariant(frame, rule)
            assert result.n_applicable == 0, rule.rule_id
            assert result.n_violations == 0, rule.rule_id

    def test_stale_nav_history_gated_out_not_a_false_fisher_alarm(self):
        """A row whose stored nominal return_ann no longer matches today's fund_nav_monthly
        (a NAV history rewrite after the row was written -- the 2nd external assessment's
        concern) must trip WINDOW_NOMINAL_IDENTITY, and BOTH deflation rules must gate it out
        (0 applicable) rather than risk a false WINDOW_FISHER_IDENTITY alarm from unrelated
        NAV staleness."""
        nav_df, ipc_df = _nav_ipc_leading_gap_fixture()
        nav_real = _correct_deflate_real(nav_df, ipc_df)
        ts = _window_row("STALE0001", nav_df, nav_real)
        # Corrupt only the stored nominal value, as if NAV was reloaded/spliced after this
        # row was written -- everything else (real, n_obs, dates) stays as originally stored.
        ts["w_return_nominal"] = ts["w_return_nominal"] + 0.50
        nav_dates = nav_df.assign(isin="STALE0001")[["isin", "date", "nav"]]

        frame = build_window_deflation_frame(ts, nav_dates, ipc_df)

        nominal_result = check_invariant(frame, _NOMINAL)
        assert nominal_result.n_applicable == 1
        assert nominal_result.n_violations == 1

        for rule in (_STRICT, _FISHER):
            result = check_invariant(frame, rule)
            assert result.n_applicable == 0, (
                f"{rule.rule_id} must be gated out by the stale nominal identity, not "
                f"evaluated against it"
            )

    def test_clipped_nav_history_still_resolves_by_relative_position(self):
        """--from-date/--to-date clips a CONTIGUOUS prefix off run_pipeline.py's nav_df before
        compute_rolling_rows() sees it; the audit's own NAV query has no such clip, so this
        simulates the clipped caller's nav_dates being a later-starting frame while the window
        still starts exactly at its first row (start_pos == 0) -- the position-based lookup
        must still resolve correctly, not silently break at the frame's edge."""
        nav_df, ipc_df = _nav_ipc_leading_gap_fixture()
        clipped = nav_df.iloc[1:].reset_index(drop=True)  # drop the 2023-09-29 leading row
        nav_real = _correct_deflate_real(clipped, ipc_df)
        ts = _window_row("CLIPPED01", clipped, nav_real, window_label="rolling_2m")
        nav_dates = clipped.assign(isin="CLIPPED01")[["isin", "date", "nav"]]

        frame = build_window_deflation_frame(ts, nav_dates, ipc_df)

        # WINDOW_DEFLATION_STRICT is correctly gated OUT here (n_applicable=0): CPI actually
        # fell (95.58 < 96.09) over this specific 2-month sub-window, so its `when` eligibility
        # gate (w_cpi_ann > IPC_ELIGIBILITY_FLOOR) legitimately excludes the row -- this test is
        # about position resolution surviving a contiguous clip, not about CPI-gate coverage.
        for rule in (_NOMINAL, _FISHER):
            result = check_invariant(frame, rule)
            assert result.n_applicable == 1, rule.rule_id
            assert result.n_violations == 0, rule.rule_id
        assert check_invariant(frame, _STRICT).n_applicable == 0
