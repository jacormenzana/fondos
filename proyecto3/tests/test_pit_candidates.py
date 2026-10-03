# proyecto3/tests/test_pit_candidates.py
# -*- coding: utf-8 -*-
"""
PIT scoring of the universe (proyecto3/src/pit_candidates.py) -- FND-0159, Wave B step d2.

End-to-end on a synthetic universe through the real d1 modules (risk, peers, momentum, short gates) and the
real scoring core: what is in the universe at t, staleness, young funds, In_Current_Universe=0 funds, regime
lookup, neutral group B, and -- the point of all of it -- that scores at t do not change when data after t
is added. No DB (R-7).

    python -m pytest proyecto3/tests/test_pit_candidates.py -v
"""

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src import fund_scorer as fs
from proyecto3.src.pit_candidates import ATTRIBUTE_COLUMNS, asof_snapshot, pit_scores
from proyecto3.src.pit_metrics import expanding_risk_metrics
from proyecto3.src.pit_peer_metrics import alpha_persistence, capture_ratios, momentum_rank
from proyecto3.src.pit_short_horizon import short_gate_metrics

N = 130
DATES = pd.date_range("2008-01-31", periods=N, freq=pd.offsets.MonthEnd())
AT = pd.DatetimeIndex([DATES[i] for i in (20, 40, 60, 80, 100, 125)])


def _universe():
    rng = np.random.default_rng(3)
    spec = {}
    for i in range(10):
        spec[f"RV{i}"] = (i * 2, "Renta Variable", 0.005 + 0.0005 * i, 0.035)
    for i in range(6):
        spec[f"MM{i}"] = (i * 3, "Monetario", 0.0015 + 0.0002 * i, 0.003)
    spec["DEAD"] = (0, "Renta Variable", 0.004, 0.03)          # stops reporting at index 70
    spec["YOUNG"] = (112, "Renta Variable", 0.004, 0.03)       # 18 observations at the end
    spec["ORPHAN"] = (5, "Renta Variable", 0.004, 0.03)        # not in fund_master attributes
    cols = {}
    for isin, (start, nat, mu, sd) in spec.items():
        s = pd.Series(100.0 * np.cumprod(1.0 + rng.normal(mu, sd, N)), index=DATES)
        s.iloc[:start] = np.nan
        cols[isin] = s
    panel = pd.DataFrame(cols)
    panel.loc[DATES[70:], "DEAD"] = np.nan
    nature = pd.Series({k: v[1] for k, v in spec.items()})
    attrs = pd.DataFrame({
        "Fund_Name": [f"Fund {k}" for k in spec], "Fund_Nature": nature.to_numpy(), "srri_kiid": 4.0,
        "Investment_Focus": "Global", "Credit_Quality": "Investment Grade", "Ongoing_Charge": 0.01,
        "SRRI_Quality_Flag": "OK", "fund_family_id": [None] * len(spec),
        "In_Current_Universe": [0 if k in ("DEAD", "RV9") else 1 for k in spec],
    }, index=pd.Index(list(spec), name="isin")).drop(index="ORPHAN")
    regimes = pd.Series(["Expansion"] * 60 + ["Crisis_Financiera"] * 30 + ["Shock_Energetico"] * 40, index=DATES)
    return panel, nature, attrs, regimes


def _inputs(panel, nature, at):
    risk = expanding_risk_metrics(panel, None, None)
    peers = {**alpha_persistence(panel, nature), **capture_ratios(panel, nature)}
    mom = momentum_rank(risk["return_ann"], nature, at, max_stale_days=45)
    return risk, peers, mom


@pytest.fixture(scope="module")
def world():
    panel, nature, attrs, regimes = _universe()
    risk, peers, mom = _inputs(panel, nature, AT)
    return panel, nature, attrs, regimes, risk, peers, mom


def _run(world, **kw):
    panel, nature, attrs, regimes, risk, peers, mom = world
    return pit_scores(AT, risk, peers, attrs, regimes, momentum=mom, **kw)


# ---------------- snapshot helper ----------------

def test_asof_snapshot_takes_the_latest_observation_and_drops_stale_ones():
    idx = pd.date_range("2020-01-31", periods=6, freq=pd.offsets.MonthEnd())
    frame = pd.DataFrame({"A": [1.0, 2.0, np.nan, np.nan, np.nan, np.nan], "B": [np.nan, np.nan, 5.0, 6.0, 7.0, 8.0]}, index=idx)
    at = pd.DatetimeIndex([idx[0] + pd.Timedelta(days=3), idx[3], idx[5]])
    vals, last = asof_snapshot(frame, at)
    assert vals.loc[at[0], "A"] == 1.0 and np.isnan(vals.loc[at[0], "B"])                 # nothing for B yet
    assert vals.loc[at[1], "A"] == 2.0 and vals.loc[at[1], "B"] == 6.0
    assert last.loc[at[1], "A"] == idx[1]
    stale, last_s = asof_snapshot(frame, at, max_stale_days=45)
    assert np.isnan(stale.loc[at[2], "A"]) and pd.isna(last_s.loc[at[2], "A"])           # A stopped 4 months ago
    assert stale.loc[at[2], "B"] == 8.0


# ---------------- universe at t ----------------

def test_universe_excludes_unborn_young_stale_and_unknown_funds(world):
    scores, universe = _run(world)
    in_universe = lambda t: set(scores[scores["as_of"] == t]["isin"])
    early = in_universe(AT[0])                                  # index 20: only funds that started <= 20 with >= 12 obs
    assert "YOUNG" not in early and "ORPHAN" not in early
    assert "RV9" not in early                                    # started at index 18: only 3 observations (< 12)
    assert "RV0" in early
    late = in_universe(AT[-1])                                  # index 125
    assert "DEAD" not in late                                   # reported until 69 -> stale by 125
    assert "YOUNG" in late                                      # 14 observations at index 125 >= 12
    assert "ORPHAN" not in late                                 # no fund_master attributes
    assert universe.loc[AT[-1], "stale"] >= 1 and universe.loc[AT[0], "young"] >= 1


def test_in_current_universe_zero_funds_are_included_by_default_and_filterable(world):
    scores_all, _ = _run(world)
    scores_cur, _ = _run(world, current_universe_only=True)
    t = AT[2]                                                   # index 60: DEAD still reporting, RV9 old enough
    assert {"DEAD", "RV9"} <= set(scores_all[scores_all["as_of"] == t]["isin"])
    assert not {"DEAD", "RV9"} & set(scores_cur[scores_cur["as_of"] == t]["isin"])


def test_stale_window_is_configurable(world):
    _, loose = _run(world, max_stale_days=2500)
    _, tight = _run(world)
    assert loose.loc[AT[-1], "stale"] < tight.loc[AT[-1], "stale"]
    assert loose.loc[AT[-1], "entered"] > tight.loc[AT[-1], "entered"]


# ---------------- regime lookup and neutrality ----------------

def test_regime_is_the_latest_label_at_or_before_t(world):
    scores, _ = _run(world)
    labels = scores.groupby("as_of")["regime"].first()
    assert labels.loc[AT[0]] == "Expansion"                     # index 20
    assert labels.loc[AT[3]] == "Crisis_Financiera"             # index 80
    assert labels.loc[AT[5]] == "Shock_Energetico"              # index 125


def test_date_before_the_first_regime_label_is_skipped_and_logged(world, caplog):
    panel, nature, attrs, regimes, risk, peers, mom = world
    late_regimes = regimes.iloc[30:]
    with caplog.at_level(logging.WARNING, logger="proyecto3.src.pit_candidates"):
        scores, universe = pit_scores(AT, risk, peers, attrs, late_regimes, momentum=mom)
    assert AT[0] not in set(scores["as_of"]) and universe.loc[AT[0], "scored"] == 0
    assert any("no regime label" in r.getMessage() for r in caplog.records)


def test_group_b_columns_are_absent_so_multipliers_are_neutral_except_alpha_bonus(world):
    scores, _ = _run(world)
    allowed = {1.0, round(fs.MULT_ALPHA_BONUS, 4)}
    assert set(scores["multiplier"].round(4)) <= allowed
    assert (scores["multiplier"] == 1.0).mean() > 0.5


def test_scores_have_the_documented_shape(world):
    scores, universe = _run(world)
    assert {"as_of", "regime", "isin", "subportfolio", "fund_nature", "score_base", "multiplier", "score_final",
            "eligible", "exclusion_reason"} <= set(scores.columns)
    assert "detail" not in scores.columns
    detailed, _ = _run(world, keep_detail=True)
    assert "detail" in detailed.columns
    assert (universe["scored"] >= universe["eligible"]).all()
    assert list(ATTRIBUTE_COLUMNS)[0] == "Fund_Name"


# ---------------- the point of PIT: no look-ahead through the whole chain ----------------

def test_scores_at_t_do_not_change_when_later_data_is_added(world):
    panel, nature, attrs, regimes, risk, peers, mom = world
    full, _ = pit_scores(AT, risk, peers, attrs, regimes, momentum=mom)
    cut = 85                                                    # keep dates AT[0..3] (indices 20,40,60,80)
    at_cut = AT[AT <= DATES[cut]]
    sub_panel = panel.iloc[: cut + 1]
    r2, p2, m2 = _inputs(sub_panel, nature, at_cut)
    part, _ = pit_scores(at_cut, r2, p2, attrs, regimes, momentum=m2)
    key = ["as_of", "isin", "subportfolio"]
    a = full[full["as_of"].isin(at_cut)].sort_values(key).reset_index(drop=True)
    b = part.sort_values(key).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-12, atol=1e-12)
    assert len(a) > 50


def test_short_gates_flow_into_the_hard_filters():
    # a fund with a 6-month drawdown beyond the Defensiva limit (-8%) is excluded from Defensiva only when its
    # daily data is trusted; with a liquidity flag above 0.20 the gate is bypassed (fail-open)
    panel, nature, attrs, regimes = _universe()
    risk, peers, mom = _inputs(panel, nature, AT)
    t = AT[-1]
    bdays = pd.bdate_range(DATES[0] - pd.Timedelta(days=400), t)
    rng = np.random.default_rng(9)
    rows = []
    for isin in ("MM0", "MM1"):
        nav = 100.0 * np.cumprod(1.0 + rng.normal(0.0001, 0.001, len(bdays)))
        nav[-100:] *= np.linspace(1.0, 0.85, 100)                       # -15% over the last ~5 months
        if isin == "MM1":
            nav[-130:] = np.round(nav[-130:] / 3.0) * 3.0               # step-like: many repeated NAVs -> illiquid flag
        rows.append(pd.DataFrame({"isin": isin, "date": bdays, "nav": nav}))
    metrics, _ = short_gate_metrics(pd.concat(rows), AT)
    scores, _ = pit_scores(AT, risk, peers, attrs, pd.Series("Expansion", index=DATES), momentum=mom, short=metrics)
    s = scores[(scores["as_of"] == t) & (scores["subportfolio"] == "Defensiva")].set_index("isin")
    assert not s.loc["MM0", "eligible"] and "short_max_drawdown_6m" in s.loc["MM0", "exclusion_reason"]
    assert metrics["short_liquidity_flag_6m"].loc[t, "MM1"] > 0.20
    assert s.loc["MM1", "eligible"]                                     # untrusted daily data -> gate bypassed
