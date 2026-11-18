"""FND-0430 / P3-1.4: the decision benchmarks of checkpoint C1 (proyecto3/src/pit_benchmarks.py). No database, synthetic world (R-7).

Pins: the mandate-matched pool keeps the sub-portfolio weights and the non-EUR budget; the persistent random selection (R1) changes holdings only when a holding loses
eligibility, while the monthly redraw (R2) churns; the turnover split is exact in the total and isolates the regime jumps; net = gross - bps x turnover; the rolling-window
statistics; and the paired bootstrap of a gap."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from proyecto3.src import pit_benchmarks as B  # noqa: E402

NATURES = ["Renta Variable", "Mixtos", "Monetario", "Renta Fija Flexible"]
SUBS = ["Defensiva", "Equilibrada", "Dinamica"]


def world(n_dates=12, per_sub=25, weights=(0.5, 0.3, 0.2)):
    at = pd.date_range("2020-01-31", periods=n_dates, freq="ME")
    rows, attrs = [], {}
    for si, sub in enumerate(SUBS):
        for k in range(per_sub):
            isin = f"{sub[:3]}{k:03d}"
            attrs[isin] = f"M-{isin}"                                   # one manager per fund: the manager cap never binds
            for t in at:
                rows.append((t, "Expansion", isin, f"name {isin}", NATURES[k % 4], f"F-{isin}", sub, 0.5 + 0.01 * k, True))
    scores = pd.DataFrame(rows, columns=["as_of", "regime", "isin", "fund_name", "fund_nature", "fund_family_id", "subportfolio", "score_final", "eligible"])
    hist = pd.DataFrame({"regime": "Expansion", "weight_defensive": weights[0], "weight_balanced": weights[1], "weight_dynamic": weights[2]}, index=at)
    return scores, pd.DataFrame({"Management_Company": pd.Series(attrs)}), hist, at


# ---------------------------------------------------------------- the pools
def test_the_pool_keeps_the_sub_portfolio_weights_and_is_equal_weighted_inside():
    scores, _a, hist, at = world(n_dates=3)
    W = B.pool_weights(scores, hist, at)
    row = W.iloc[0].dropna()
    assert row.sum() == pytest.approx(1.0)
    by_sub = {s: row[[i for i in row.index if i.startswith(s[:3])]].sum() for s in SUBS}
    assert by_sub == pytest.approx({"Defensiva": 0.5, "Equilibrada": 0.3, "Dinamica": 0.2})
    assert row[[i for i in row.index if i.startswith("Def")]].nunique() == 1


def test_the_matched_pool_caps_the_exposed_weight_of_every_sub_portfolio():
    scores, _a, hist, at = world(n_dates=2)
    isins = scores["isin"].unique()
    fx = pd.Series({i: (i.startswith("Def") and int(i[3:]) < 15) for i in isins})          # 15 of the 25 Defensiva funds are exposed: 60 % of the pool
    free = B.pool_weights(scores, hist, at, fx_exposed=fx)
    capped = B.pool_weights(scores, hist, at, fx_exposed=fx, exposed_budget=0.30)
    exposed_cols = [i for i in isins if fx[i]]
    assert free.iloc[0][exposed_cols].sum() == pytest.approx(0.5 * 0.6)
    assert capped.iloc[0][exposed_cols].sum() == pytest.approx(0.5 * 0.30)                   # 30 % of the sub-portfolio weight 0.5
    assert capped.iloc[0].dropna().sum() == pytest.approx(1.0)                                # the remainder goes to the non-exposed funds


def test_a_date_without_scores_gives_an_empty_portfolio():
    scores, _a, hist, at = world(n_dates=3)
    W = B.pool_weights(scores[scores["as_of"] != at[1]], hist, at)
    assert W.loc[at[1]].isna().all() and W.loc[at[0]].notna().any()


# ---------------------------------------------------------------- random selection
def test_persistent_random_selection_holds_until_a_holding_loses_eligibility():
    scores, attrs, hist, at = world(n_dates=12)
    W1, _c = B.random_selection_weights(scores, attrs, hist, at, seed=1, persistent=True)
    held0 = set(W1.iloc[0].dropna().index)
    assert len(held0) == 30                                                                   # 10 per sub-portfolio
    assert all(set(W1.iloc[i].dropna().index) == held0 for i in range(12))                     # nothing changes while everything stays eligible
    gone = sorted(held0)[0]
    s2 = scores.copy()
    s2.loc[(s2["isin"] == gone) & (s2["as_of"] >= at[6]), "eligible"] = False
    W2, _c = B.random_selection_weights(s2, attrs, hist, at, seed=1, persistent=True)
    before, after = set(W2.iloc[5].dropna().index), set(W2.iloc[6].dropna().index)
    assert before == held0 and gone not in after and len(after - before) == 1 and len(before - after) == 1     # exactly the slot of the lost fund is replaced


def test_a_newly_eligible_fund_does_not_displace_a_holding_in_the_persistent_variant(monkeypatch):
    """The incumbent bonus is what makes R1 persistent: with constant eligibility a fixed random score gives the same selection every month WITHOUT it, so
    only a fund that BECOMES eligible later can tell the two apart."""
    scores, attrs, hist, at = world(n_dates=12)
    late = scores["isin"].str[3:].astype(int) % 2 == 1                                          # half of the funds are not eligible until month 6
    s2 = scores.assign(eligible=~(late & (scores["as_of"] < at[6])))
    W, _c = B.random_selection_weights(s2, attrs, hist, at, seed=3, persistent=True)
    assert set(W.iloc[5].dropna().index) == set(W.iloc[6].dropna().index) == set(W.iloc[11].dropna().index)
    monkeypatch.setattr(B, "HOLD_BAND", 0.0)                                                    # without the bonus the same world DOES churn: the test can fail
    W0, _c = B.random_selection_weights(s2, attrs, hist, at, seed=3, persistent=True)
    assert set(W0.iloc[5].dropna().index) != set(W0.iloc[6].dropna().index)


def test_the_monthly_redraw_churns_and_the_persistent_one_does_not():
    scores, attrs, hist, at = world(n_dates=12)
    parts1 = B.turnover_parts(B.random_selection_weights(scores, attrs, hist, at, seed=2, persistent=True)[0], hist, at)
    parts2 = B.turnover_parts(B.random_selection_weights(scores, attrs, hist, at, seed=2, persistent=False)[0], hist, at)
    assert parts1["holdings"].iloc[1:].abs().max() < 1e-9
    assert parts2["holdings"].iloc[1:].mean() > 0.3


def test_the_same_seed_gives_the_same_portfolio():
    scores, attrs, hist, at = world(n_dates=6)
    a = B.random_selection_weights(scores, attrs, hist, at, seed=7, persistent=True)[0]
    b = B.random_selection_weights(scores, attrs, hist, at, seed=7, persistent=True)[0]
    pd.testing.assert_frame_equal(a, b)


# ---------------------------------------------------------------- turnover and series
def test_the_turnover_split_is_exact_and_isolates_the_regime_jumps():
    scores, _a, hist, at = world(n_dates=8)
    hist.loc[at[5]:, ["weight_defensive", "weight_balanced", "weight_dynamic"]] = [0.2, 0.5, 0.3]        # the label jumps at month 5, holdings do not
    W = B.pool_weights(scores, hist, at)
    p = B.turnover_parts(W, hist, at)
    assert (p["total"] - p["regime"] - p["holdings"]).abs().max() < 1e-12
    assert p["total"].iloc[0] == pytest.approx(1.0) and p["regime"].iloc[0] == pytest.approx(1.0)       # the first month enters from cash
    assert p["regime"].iloc[5] == pytest.approx(0.3 + 0.2 + 0.1) and p["holdings"].iloc[5] == pytest.approx(0.0, abs=1e-9)
    assert p["total"].iloc[1:5].abs().max() < 1e-12


def test_net_is_gross_minus_bps_times_turnover():
    scores, _a, hist, at = world(n_dates=6)
    W = B.pool_weights(scores, hist, at)
    R1 = pd.DataFrame(0.01, index=at, columns=W.columns)
    cash = pd.Series(0.0, index=at)
    s25, s0 = B.benchmark_series(W, R1, cash, 25.0), B.benchmark_series(W, R1, cash, 0.0)
    assert (s0["net"] - s0["gross"]).abs().max() < 1e-12
    assert (s25["net"] - (s25["gross"] - 0.0025 * s25["turnover"])).abs().max() < 1e-12
    assert s25["net"].iloc[0] == pytest.approx(0.01 - 0.0025)                                          # the first month pays the full entry


# ---------------------------------------------------------------- mandate-level statistics
def test_rolling_excess_over_the_target_and_its_summary():
    idx = pd.date_range("2010-01-31", periods=60, freq="ME")
    r, t = pd.Series(0.010, index=idx), pd.Series(0.004, index=idx)
    ex = B.rolling_excess(r, t, 36)
    assert len(ex) == 60 - 36 + 1 and ex.nunique() == 1
    assert ex.iloc[0] == pytest.approx((1.010 ** 12 - 1) - (1.004 ** 12 - 1))
    s = B.window_summary(ex)
    assert s["n"] == 25 and s["median"] == pytest.approx(s["p25"]) and s["share_above"] == 1.0
    assert B.window_summary(pd.Series(dtype=float))["n"] == 0


def _noise(seed, n=240, mu=0.003, sd=0.01):
    return pd.Series(np.random.default_rng(seed).normal(mu, sd, n), index=pd.date_range("2000-01-31", periods=n, freq="ME"))


def test_the_bootstrap_gap_sees_a_real_difference_and_not_a_null_one():
    b = _noise(1)
    better = b + 0.004
    g = B.bootstrap_gap([better], b, "ann_return", n_boot=400)
    assert g["point"] > 0.04 and g["ci_low"] > 0 and g["n"] == 240
    null = B.bootstrap_gap([b], b, "ann_return", n_boot=400)
    assert null["point"] == pytest.approx(0.0, abs=1e-12) and null["ci_low"] <= 0 <= null["ci_high"]


def test_the_sharpe_gap_and_the_draw_average():
    b = _noise(3)
    draws = [b + 0.003, b + 0.001]
    g = B.bootstrap_gap(draws, b, "sharpe", cash=pd.Series(0.001, index=b.index), n_boot=300)
    assert g["n_draws"] == 2 and g["point"] > 0 and g["draw_sd"] > 0 and g["ci_low"] < g["point"] < g["ci_high"]
    with pytest.raises(ValueError):
        B.bootstrap_gap(draws, b, "median")
