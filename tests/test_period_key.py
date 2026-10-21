"""
tests/test_period_key.py -- the canonical period key and the data contract that rests on it (R-9, FND-0300).

Pure unit tests: no database, no pipeline import (R-7). The behaviour pinned here is the behaviour the static scan
(`tests/test_period_key_scan.py`) tells callers to use and the invariance contracts
(`proyecto2/tests/calculations/test_period_key_invariance_pg.py`) assume.
"""
from __future__ import annotations

import datetime as dt
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import period_key_contract as pkc                                            # noqa: E402
from shared.annualization import month_ordinals                                          # noqa: E402
from shared.deflation_alignment import cpi_lookup_dates                                  # noqa: E402
from shared.period_key import (PARTIAL_TAIL_DAYS, is_period_complete, months_between,    # noqa: E402
                               period_index, period_ordinals, snap_frame, to_period_key)


# --- to_period_key ----------------------------------------------------------------------------------------------

def test_every_stamp_of_a_month_maps_to_the_same_key():
    """The whole point: month-start, last-business-day and mid-month are one period, not three."""
    keys = to_period_key(["2026-02-01", "2026-02-13", "2026-02-27", "2026-02-28"])
    assert list(keys) == [pd.Timestamp("2026-02-28")] * 4


def test_key_is_normalised_and_handles_leap_years_and_year_ends():
    assert to_period_key([dt.datetime(2024, 2, 5, 17, 30)])[0] == pd.Timestamp("2024-02-29")   # leap February
    assert to_period_key([dt.date(2025, 12, 1)])[0] == pd.Timestamp("2025-12-31")
    assert to_period_key(["2026-02-28"])[0].hour == 0


def test_accepts_the_shapes_the_repo_actually_passes_around():
    expected = [pd.Timestamp("2026-01-31"), pd.Timestamp("2026-02-28")]
    raw = ["2026-01-30", "2026-02-27"]
    for value in (raw, pd.Series(raw), pd.DatetimeIndex(pd.to_datetime(raw)),
                  np.array(pd.to_datetime(raw)), [dt.date(2026, 1, 30), dt.date(2026, 2, 27)]):
        assert list(to_period_key(value)) == expected


def test_the_callers_index_is_never_carried_over():
    """A key is a VALUE. Returning it on the caller's index invites the silent re-alignment R-9 exists to stop."""
    s = pd.Series(pd.to_datetime(["2026-01-30", "2026-02-27"]), index=[41, 7])
    assert list(to_period_key(s).to_numpy()) == list(pd.to_datetime(["2026-01-31", "2026-02-28"]))


# --- ordinals and differences -----------------------------------------------------------------------------------

def test_period_ordinals_delegates_to_the_one_implementation():
    """P#11: no second ordinal formula in the repo. Identity with shared.annualization.month_ordinals is the contract."""
    dates = pd.to_datetime(["2019-03-31", "2020-01-01", "2026-02-27"])
    assert list(period_ordinals(dates)) == list(month_ordinals(dates))


def test_months_between_counts_periods_not_days():
    assert months_between("2026-01-31", "2026-02-01") == 1          # one day apart, one month apart
    assert months_between("2026-01-01", "2026-01-31") == 0          # thirty days apart, same month
    assert months_between("2026-02-28", "2026-01-31") == -1
    assert list(months_between(["2025-02-01", "2026-01-31"], ["2026-02-28", "2026-02-01"])) == [12, 1]


def test_period_index_is_dense_and_month_end():
    idx = period_index(3, "2026-02-27")
    assert list(idx) == list(pd.to_datetime(["2025-12-31", "2026-01-31", "2026-02-28"]))
    assert len(period_index(0)) == 0


# --- the partial trailing month (R-9 rule 7) ---------------------------------------------------------------------

@pytest.mark.parametrize("stamp, complete", [
    ("2026-02-28", True),                      # the month-end itself
    ("2026-02-27", True),                      # last business day, 1 day short
    ("2026-02-23", True),                      # exactly PARTIAL_TAIL_DAYS short
    ("2026-02-22", False),                     # 6 days short: the month is still reporting
    ("2026-02-05", False),
])
def test_is_period_complete(stamp, complete):
    assert is_period_complete(stamp) is complete
    assert (pd.Timestamp("2026-02-28") - pd.Timestamp(stamp)).days <= PARTIAL_TAIL_DAYS or not complete


# --- snap_frame -------------------------------------------------------------------------------------------------

def _frame():
    return pd.DataFrame({
        "isin": ["A", "A", "B", "B"],
        "date": pd.to_datetime(["2026-01-30", "2026-01-31", "2026-01-31", "2026-02-27"]),
        "nav": [1.0, 2.0, 3.0, 4.0],
    })


def test_snap_frame_keeps_the_raw_stamp_and_adds_the_key():
    """The key is DERIVED, never destructive -- rule 7 depends on the raw stamp still being there."""
    out = snap_frame(_frame(), group_cols=["isin"])
    assert "date" in out.columns and "period_key" in out.columns
    assert out.loc[out["isin"] == "B", "date"].max() == pd.Timestamp("2026-02-27")
    assert out.loc[out["isin"] == "B", "period_key"].max() == pd.Timestamp("2026-02-28")


def test_snap_frame_collapses_within_a_series_and_not_across_series():
    """The live database has 121 months with two raw dates and ZERO (isin, month) duplicates: two funds reporting the
    same month are two observations. Collapsing them would be the defect, not the fix."""
    out = snap_frame(_frame(), group_cols=["isin"])
    assert len(out) == 3                                           # A's two January rows collapse; B's January survives
    assert out.loc[(out["isin"] == "A") & (out["period_key"] == pd.Timestamp("2026-01-31")), "nav"].tolist() == [2.0]
    assert sorted(out.loc[out["period_key"] == pd.Timestamp("2026-01-31"), "isin"]) == ["A", "B"]


def test_snap_frame_logs_a_collapse_once_per_call(caplog):
    with caplog.at_level(logging.INFO, logger="shared.period_key"):
        snap_frame(_frame(), group_cols=["isin"], label="unit")
    collapse_lines = [r for r in caplog.records if "collapsed to one per period key" in r.getMessage()]
    assert len(collapse_lines) == 1 and "unit" in collapse_lines[0].getMessage()


def test_snap_frame_keeps_last_deterministically_whatever_the_row_order():
    frame = _frame()
    out_a = snap_frame(frame, group_cols=["isin"])
    out_b = snap_frame(frame.iloc[::-1].reset_index(drop=True), group_cols=["isin"])
    pd.testing.assert_frame_equal(out_a, out_b)


def test_snap_frame_partial_tail_modes():
    frame = pd.DataFrame({"date": pd.to_datetime(["2026-01-31", "2026-02-10"]), "nav": [1.0, 2.0]})
    assert len(snap_frame(frame, partial_tail="keep")) == 2
    assert len(snap_frame(frame, partial_tail="snap")) == 2
    dropped = snap_frame(frame, partial_tail="drop")
    assert list(dropped["date"]) == [pd.Timestamp("2026-01-31")]


def test_snap_frame_rejects_a_bad_mode_and_a_missing_column():
    with pytest.raises(ValueError):
        snap_frame(_frame(), partial_tail="whatever")
    with pytest.raises(KeyError):
        snap_frame(_frame(), date_col="fecha")


def test_snap_frame_on_an_empty_frame_keeps_the_shape():
    out = snap_frame(_frame().iloc[0:0], group_cols=["isin"])
    assert out.empty and "period_key" in out.columns


# --- the FND-0241 delegation ------------------------------------------------------------------------------------

def test_cpi_lookup_dates_uses_the_canonical_key_when_aligned_and_is_untouched_otherwise():
    dates = pd.Series(pd.to_datetime(["2026-01-30", "2026-02-27"]))
    assert list(cpi_lookup_dates(dates, align=True)) == list(to_period_key(dates))
    assert list(cpi_lookup_dates(dates, align=False)) == list(dates)


# --- the data contract ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("n, ms, me, expected", [
    (321, 321, 0, pkc.MONTH_START),
    (309, 0, 309, pkc.MONTH_END),
    (6879, 188, 229, pkc.DAILY),                                   # the live fx_d_eur shape
    (100, 60, 30, pkc.MIXED),
    (0, 0, 0, pkc.MIXED),
])
def test_classify_stamp(n, ms, me, expected):
    assert pkc.classify_stamp(n, ms, me) == expected


def test_assess_macro_stamps_separates_ok_unexpected_and_undeclared():
    rows = [
        {"indicator": "ipc_index", "geography": "ES", "n_rows": 321, "n_month_start": 321, "n_month_end": 0},
        {"indicator": "vix", "geography": "GLOBAL", "n_rows": 322, "n_month_start": 0, "n_month_end": 322},
        {"indicator": "m2_yoy", "geography": "CN", "n_rows": 309, "n_month_start": 0, "n_month_end": 309},
        {"indicator": "brand_new", "geography": "EU", "n_rows": 10, "n_month_start": 10, "n_month_end": 0},
    ]
    states = {(r["indicator"], r["geography"]): r["state"] for r in pkc.assess_macro_stamps(rows)}
    assert states[("ipc_index", "ES")] == "ok"
    assert states[("vix", "GLOBAL")] == "unexpected"               # declared month-start, stored month-end
    assert states[("m2_yoy", "CN")] == "ok"                        # declared MIXED: the FND-0293 split is a known state
    assert states[("brand_new", "EU")] == "unknown"                # a new series must declare its stamp


def test_assess_nav_flags_a_per_fund_duplicate_and_a_convention_drift():
    clean = pkc.assess_nav(0.287, 121, 0)
    assert not clean["isin_month_duplicates_unexpected"] and not clean["off_month_end_drifted"]
    assert pkc.assess_nav(0.287, 121, 3)["isin_month_duplicates_unexpected"]
    assert pkc.assess_nav(0.90, 121, 0)["off_month_end_drifted"]
    assert not pkc.assess_nav(0.33, 121, 0)["off_month_end_drifted"]        # ordinary month-to-month movement


def test_attention_lines_are_empty_when_the_contract_holds_and_speak_up_when_it_does_not():
    ok_macro = pkc.assess_macro_stamps(
        [{"indicator": "ipc_index", "geography": "ES", "n_rows": 321, "n_month_start": 321, "n_month_end": 0}])
    assert pkc.attention_lines(pkc.assess_nav(0.287, 121, 0), ok_macro) == []

    bad_macro = pkc.assess_macro_stamps(
        [{"indicator": "vix", "geography": "GLOBAL", "n_rows": 322, "n_month_start": 0, "n_month_end": 322}])
    lines = pkc.attention_lines(pkc.assess_nav(0.90, 121, 5), bad_macro)
    assert len(lines) == 3
    assert any("(isin, mes)" in ln for ln in lines)
    assert any("fuera del fin de mes natural" in ln for ln in lines)
    assert any("vix" in ln for ln in lines)


def test_every_declared_stamp_is_a_known_value():
    assert set(pkc.EXPECTED_STAMPS.values()) <= {pkc.MONTH_START, pkc.MONTH_END, pkc.DAILY, pkc.MIXED}
