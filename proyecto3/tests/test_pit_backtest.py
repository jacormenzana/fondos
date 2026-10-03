# proyecto3/tests/test_pit_backtest.py
# -*- coding: utf-8 -*-
"""
PIT backtest (proyecto3/src/pit_backtest.py, pit_run.py) -- FND-0159 / FND-0191 / FND-0189 / FND-0197, step d3.

* the vectorized return layer is checked against the ORIGINAL loop (backtesting._portfolio_return /
  _cash_return) on random data -- same numbers, no loops;
* weights/benchmark/cash behaviour on small hand-checkable cases;
* the full chain on a synthetic universe: cache hit on the second pass with identical output, and weights at
  t unchanged when later data is added (no look-ahead end to end).
No DB (R-7).

    python -m pytest proyecto3/tests/test_pit_backtest.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src import backtesting as old
from proyecto3.src.pit_backtest import (
    build_weights, candidates_by_sub, cash_index, forward_returns, pool_benchmark, portfolio_returns,
    run_pit_backtest,
)
from proyecto3.src.pit_cache import ParquetCache
from proyecto3.src.pit_run import PitInputs, compute_pit_scores

N = 130
MONTHS = pd.date_range("2008-01-31", periods=N, freq=pd.offsets.MonthEnd())


# ---------------- return layer == original loop ----------------

def _nav(seed=1, n_funds=12):
    rng = np.random.default_rng(seed)
    nav = pd.DataFrame({f"F{i}": 100.0 * np.cumprod(1 + rng.normal(0.004, 0.03, N)) for i in range(n_funds)}, index=MONTHS)
    nav.iloc[:20, 3] = np.nan                                  # F3 starts late
    nav.iloc[90:, 5] = np.nan                                  # F5 dies
    return nav


def _rate(level_pct=2.4):
    r = np.where(np.arange(N + 30) < 60, level_pct, -0.5) / 100.0         # decimal, includes a negative stretch
    return pd.DataFrame({"date": pd.date_range("2008-01-31", periods=N + 30, freq=pd.offsets.MonthEnd()), "rate": r})


@pytest.mark.parametrize("w", [1, 3, 12])
def test_vectorized_returns_equal_the_original_loop(w):
    nav = _nav()
    rate = _rate()
    rate_pct = pd.Series(rate["rate"].to_numpy() * 100.0, index=pd.DatetimeIndex(rate["date"]))      # old loader: percent
    rng = np.random.default_rng(w)
    at = MONTHS[10:100:3]
    weights = {}
    rows = {}
    for t in at:
        held = rng.choice(nav.columns, size=rng.integers(3, 9), replace=False)
        raw = rng.random(len(held))
        total = rng.choice([1.0, 0.8, 0.55])                                # sometimes under-invested (cash residue)
        wd = dict(zip(held, raw / raw.sum() * total))
        weights[t] = wd
        rows[t] = wd
    W = pd.DataFrame.from_dict(rows, orient="index").reindex(at)
    R = forward_returns(nav, at, w, max_stale_days=45)
    cidx = cash_index(rate, pd.date_range(at.min(), at.max() + pd.offsets.MonthEnd(12), freq=pd.offsets.MonthEnd()))
    ends = pd.DatetimeIndex([t + pd.offsets.MonthEnd(w) for t in at])
    cash_ret = pd.Series(cidx.reindex(ends).to_numpy() / cidx.reindex(at).to_numpy() - 1.0, index=at)
    ret, share = portfolio_returns(W, R, cash_ret)
    compared = 0
    for t in at:
        exp_ret, exp_share = old._portfolio_return(nav, weights[t], t, w, rate_pct)
        if exp_ret is None:
            assert np.isnan(ret[t])
        else:
            assert ret[t] == pytest.approx(exp_ret, abs=2e-6), f"{t.date()} w={w}"
            assert share[t] == pytest.approx(exp_share, abs=1e-9)
            compared += 1
    assert compared > 20


def test_cash_index_equals_the_original_cash_return_including_negative_rates():
    rate = _rate()
    rate_pct = pd.Series(rate["rate"].to_numpy() * 100.0, index=pd.DatetimeIndex(rate["date"]))
    grid = MONTHS[:60]
    idx = cash_index(rate, grid)
    for a, b in [(0, 12), (40, 55), (55, 59)]:
        assert idx.iloc[b] / idx.iloc[a] - 1.0 == pytest.approx(old._cash_return(rate_pct, grid[a], grid[b]), abs=1e-12)
    neg = cash_index(pd.DataFrame({"date": MONTHS[:6], "rate": -0.005}), MONTHS[:6])
    assert neg.is_monotonic_decreasing and np.isfinite(neg).all()


def test_cash_index_without_series_is_flat_and_warns(caplog):
    with caplog.at_level("WARNING", logger="proyecto3.src.pit_backtest"):
        idx = cash_index(None, MONTHS[:5])
    assert (idx == 1.0).all() and any("no rate_deposit" in r.getMessage() for r in caplog.records)


def test_forward_returns_use_as_of_nav_and_nan_when_unusable():
    nav = _nav()
    at = pd.DatetimeIndex([MONTHS[15], MONTHS[30], MONTHS[85], MONTHS[95], MONTHS[N - 2]])
    R = forward_returns(nav, at, 3, max_stale_days=45)
    assert R.loc[MONTHS[30], "F0"] == pytest.approx(nav.loc[MONTHS[33], "F0"] / nav.loc[MONTHS[30], "F0"] - 1.0)
    assert not np.isnan(R.loc[MONTHS[30], "F3"])                                 # F3 alive at 30 (started at 20)
    assert np.isnan(R.loc[MONTHS[15], "F3"])                                     # ... but not yet at 15
    assert R.loc[MONTHS[85], "F5"] == pytest.approx(nav.loc[MONTHS[88], "F5"] / nav.loc[MONTHS[85], "F5"] - 1.0)  # alive until 89
    assert np.isnan(R.loc[MONTHS[N - 2], "F0"])                                   # window runs past the data
    # F5 stopped at index 89: at 95 both ends would resolve to the SAME stale NAV (a fake 0% return) -- it must be NaN
    assert np.isnan(R.loc[MONTHS[95], "F5"])


def test_dead_fund_weight_falls_back_to_cash():
    nav = _nav()
    t = MONTHS[88]                                             # F5 dies at index 90: 3-month window needs MONTHS[91]
    W = pd.DataFrame({"F5": [0.5], "F0": [0.5]}, index=pd.DatetimeIndex([t]))
    R = forward_returns(nav, pd.DatetimeIndex([t]), 3, 45)
    cash_ret = pd.Series([0.01], index=W.index)
    ret, share = portfolio_returns(W, R, cash_ret)
    assert share.iloc[0] == pytest.approx(0.5)
    assert ret.iloc[0] == pytest.approx(0.5 * R.loc[t, "F0"] + 0.5 * 0.01, abs=1e-6)


def test_empty_portfolio_is_nan_not_cash():
    W = pd.DataFrame({"F0": [np.nan]}, index=pd.DatetimeIndex([MONTHS[40]]))
    ret, share = portfolio_returns(W, forward_returns(_nav(), W.index, 1, 45), pd.Series([0.01], index=W.index))
    assert np.isnan(ret.iloc[0]) and np.isnan(share.iloc[0])


# ---------------- selection and benchmark ----------------

def _scores_one_date(t, spec):
    rows = []
    for sub, funds in spec.items():
        for isin, score, elig in funds:
            rows.append(dict(as_of=t, regime="Expansion", isin=isin, subportfolio=sub, fund_name=isin,
                             fund_nature="Renta Variable", fund_family_id=None, score_base=score, multiplier=1.0,
                             score_final=score, eligible=elig, exclusion_reason=None))
    return pd.DataFrame(rows)


def _attrs(isins):
    return pd.DataFrame({"Management_Company": [f"M{i}" for i in range(len(isins))]}, index=pd.Index(isins, name="isin"))


def _hist(t0, weights=(0.2, 0.5, 0.3)):
    return pd.DataFrame({"regime": "Expansion", "weight_defensive": weights[0], "weight_balanced": weights[1],
                         "weight_dynamic": weights[2]}, index=pd.DatetimeIndex([t0 - pd.offsets.MonthEnd(1)]))


def test_candidates_keep_only_eligible_positive_scores():
    t = MONTHS[50]
    sc = _scores_one_date(t, {"Defensiva": [("A", 0.9, True), ("B", 0.8, False), ("C", 0.0, True)]})
    c = candidates_by_sub(sc, _attrs(["A", "B", "C"]))
    assert list(c["Defensiva"]["isin"]) == ["A"] and c["Equilibrada"].empty and c["Dinamica"].empty


def test_build_weights_blends_with_regime_weights_and_leaves_missing_subs_in_cash():
    t = MONTHS[50]
    only_def = {"Defensiva": [(f"D{i}", 1.0 - 0.05 * i, True) for i in range(6)]}
    sc = _scores_one_date(t, only_def)
    W, cash = build_weights(sc, _attrs([f"D{i}" for i in range(6)]), _hist(t), pd.DatetimeIndex([t]))
    assert W.loc[t].sum() == pytest.approx(0.2, abs=1e-3)                 # Defensiva weight only
    assert cash[t] == pytest.approx(0.8, abs=1e-3)
    W2, cash2 = build_weights(sc.iloc[0:0], _attrs([]), _hist(t), pd.DatetimeIndex([t]))
    assert W2.loc[t].dropna().empty and np.isnan(cash2[t])


def test_pool_benchmark_is_the_equal_weighted_eligible_pool_and_missing_sub_uses_cash():
    t = MONTHS[50]
    sc = _scores_one_date(t, {"Defensiva": [("A", 0.9, True), ("B", 0.5, True), ("X", 0.1, False)],
                              "Equilibrada": [("C", 0.7, True)]})
    R = pd.DataFrame({"A": [0.10], "B": [0.02], "C": [0.05], "X": [-0.5]}, index=pd.DatetimeIndex([t]))
    cash = pd.Series([0.01], index=R.index)
    b = pool_benchmark(sc, R, cash, _hist(t), pd.DatetimeIndex([t]))
    expected = 0.2 * np.mean([0.10, 0.02]) + 0.5 * 0.05 + 0.3 * 0.01       # Dinamica empty -> cash; X ineligible
    assert b[t] == pytest.approx(expected, abs=1e-6)


# ---------------- full chain ----------------

def _world():
    rng = np.random.default_rng(4)
    spec = {}
    for i in range(14):
        spec[f"RV{i}"] = (i * 2, "Renta Variable", 0.005 + 0.0004 * i, 0.035)
    for i in range(10):
        spec[f"MM{i}"] = (i * 3, "Monetario", 0.0015 + 0.0002 * i, 0.003)
    for i in range(8):
        spec[f"MX{i}"] = (i * 2, "Mixtos", 0.003 + 0.0003 * i, 0.02)
    cols = {}
    for isin, (start, nat, mu, sd) in spec.items():
        s = pd.Series(100.0 * np.cumprod(1.0 + rng.normal(mu, sd, N)), index=MONTHS)
        s.iloc[:start] = np.nan
        cols[isin] = s
    nav = pd.DataFrame(cols)
    attrs = pd.DataFrame({
        "Fund_Name": list(spec), "Fund_Nature": [v[1] for v in spec.values()], "srri_kiid": 4.0,
        "Investment_Focus": "Global", "Credit_Quality": "Investment Grade", "Ongoing_Charge": 0.01,
        "SRRI_Quality_Flag": "OK", "fund_family_id": [None] * len(spec),
        "Management_Company": [f"M{i // 2}" for i in range(len(spec))],
        "In_Current_Universe": 1,
    }, index=pd.Index(list(spec), name="isin"))
    regime_hist = pd.DataFrame({"regime": ["Expansion"] * 70 + ["Contraccion"] * 60,
                                "weight_defensive": [0.2] * 70 + [0.6] * 60,
                                "weight_balanced": [0.45] * 70 + [0.35] * 60,
                                "weight_dynamic": [0.35] * 70 + [0.05] * 60}, index=MONTHS)
    return nav, attrs, regime_hist


AT = MONTHS[60:110:5]


def _inputs(nav, attrs, daily=True):
    ipc = pd.DataFrame({"date": pd.date_range("2006-01-31", periods=N + 36, freq=pd.offsets.MonthEnd()),
                        "ipc_index": 100.0 * np.cumprod(np.full(N + 36, 1.002))})
    bd = pd.bdate_range(MONTHS[0] - pd.Timedelta(days=200), MONTHS[-1])
    rng = np.random.default_rng(8)
    long = pd.concat([pd.DataFrame({"isin": i, "date": bd, "nav": 100.0 * np.cumprod(1 + rng.normal(0.0002, 0.004, len(bd)))})
                      for i in ("RV0", "MM0", "MX0")])
    return PitInputs(nav=nav, attrs=attrs, ipc=ipc, rate=_rate(),
                     daily_chunks=(lambda: [long]) if daily else None)


@pytest.fixture(scope="module")
def chain(tmp_path_factory):
    nav, attrs, regime_hist = _world()
    inputs = _inputs(nav, attrs)
    cache = ParquetCache(tmp_path_factory.mktemp("pitcache"))
    run1 = compute_pit_scores(inputs, AT, regime_hist["regime"], cache)
    return nav, attrs, regime_hist, inputs, cache, run1


def test_chain_second_pass_hits_the_cache_and_returns_identical_scores(chain):
    nav, attrs, regime_hist, inputs, cache, run1 = chain
    run2 = compute_pit_scores(inputs, AT, regime_hist["regime"], cache)
    assert run1.cache_hits["risk"] is False and run2.cache_hits["risk"] is True
    assert run2.cache_hits["peers"] is True and run2.cache_hits["short"] == "1/1 chunks"
    assert run1.cache_hits["scoring"] is False and run2.cache_hits["scoring"] is True
    pd.testing.assert_frame_equal(run1.scores.reset_index(drop=True), run2.scores.reset_index(drop=True))
    pd.testing.assert_frame_equal(run1.universe, run2.universe, check_freq=False)
    assert run2.timings["risk"] < run1.timings["risk"] + 1.0
    assert {"risk", "peers", "momentum", "short", "scoring", "total"} <= set(run1.timings)
    assert run1.short_coverage is not None and (run1.short_coverage["funds_with_daily"] >= 0).all()


def test_no_cache_flag_recomputes_every_time(chain, tmp_path):
    nav, attrs, regime_hist, inputs, _, _ = chain
    off = ParquetCache(tmp_path / "off", enabled=False)
    r1 = compute_pit_scores(inputs, AT, regime_hist["regime"], off)
    r2 = compute_pit_scores(inputs, AT, regime_hist["regime"], off)
    assert r1.cache_hits["risk"] is False and r2.cache_hits["risk"] is False


def test_historical_nav_correction_invalidates_the_cache(chain, tmp_path):
    nav, attrs, regime_hist, inputs, cache, _ = chain
    edited = nav.copy()
    edited.iloc[40, 3] *= 1.0001                                # a correction far from the last row
    changed = PitInputs(nav=edited, attrs=attrs, ipc=inputs.ipc, rate=inputs.rate, daily_chunks=None)
    run = compute_pit_scores(changed, AT, regime_hist["regime"], cache)
    assert run.cache_hits["risk"] is False and run.cache_hits["peers"] is False


def test_run_pit_backtest_output_shape_and_values(chain):
    nav, attrs, regime_hist, inputs, cache, run1 = chain
    table = run_pit_backtest(run1, inputs, regime_hist, AT)
    for w in (1, 3, 12):
        assert {f"ret_{w}m", f"bench_{w}m", f"excess_{w}m", f"cov_{w}m"} <= set(table.columns)
    assert {"regime", "n_funds", "cash_weight"} <= set(table.columns)
    assert (table["n_funds"] > 0).all()
    assert table["regime"].iloc[0] == "Expansion" and table["regime"].iloc[-1] == "Contraccion"
    assert table["ret_1m"].notna().all() and table["bench_1m"].notna().all()
    assert np.allclose(table["excess_1m"], table["ret_1m"] - table["bench_1m"])
    assert (table["cov_1m"].dropna() <= 1.0 + 1e-9).all()


def test_weights_at_t_do_not_change_when_later_data_is_added(chain):
    nav, attrs, regime_hist, inputs, cache, run_full = chain
    cut = 85
    at_cut = AT[AT <= MONTHS[cut]]
    cut_inputs = PitInputs(nav=nav.iloc[: cut + 1], attrs=attrs, ipc=inputs.ipc, rate=inputs.rate, daily_chunks=None)
    run_cut = compute_pit_scores(cut_inputs, at_cut, regime_hist["regime"], ParquetCache(Path("."), enabled=False))
    full_inputs_nodaily = PitInputs(nav=nav, attrs=attrs, ipc=inputs.ipc, rate=inputs.rate, daily_chunks=None)
    run_full_nd = compute_pit_scores(full_inputs_nodaily, AT, regime_hist["regime"], ParquetCache(Path("."), enabled=False))
    W_full, _ = build_weights(run_full_nd.scores, attrs, regime_hist, at_cut)
    W_cut, _ = build_weights(run_cut.scores, attrs, regime_hist, at_cut)
    pd.testing.assert_frame_equal(W_full.sort_index(axis=1), W_cut.reindex(columns=W_full.columns).sort_index(axis=1),
                                  check_exact=False, atol=1e-12)
    assert W_full.notna().sum().sum() > 20


def test_forward_returns_tolerate_a_short_gap_but_not_a_long_one():
    nav = _nav()[["F0"]].copy()
    nav["SHORT_GAP"] = nav["F0"]
    nav["LONG_GAP"] = nav["F0"]
    nav.iloc[43, 1] = np.nan                                        # one month missing at the start date
    nav.iloc[40:45, 2] = np.nan                                     # five months missing around the start date
    t = pd.DatetimeIndex([MONTHS[43]])
    R = forward_returns(nav, t, 3, max_stale_days=45)
    # one-month gap: the previous month's NAV (31 days old) is still a usable start
    assert R.loc[MONTHS[43], "SHORT_GAP"] == pytest.approx(nav.loc[MONTHS[46], "SHORT_GAP"] / nav.loc[MONTHS[42], "SHORT_GAP"] - 1.0)
    # five-month gap: the last NAV is ~4 months old -> not a usable start, even though the end NAV exists
    assert np.isnan(R.loc[MONTHS[43], "LONG_GAP"])


def test_scoring_cache_key_covers_regimes_and_parameters(chain):
    nav, attrs, regime_hist, inputs, cache, run1 = chain
    other_regimes = regime_hist["regime"].where(regime_hist.index < MONTHS[80], "Estanflacion")
    changed_regime = compute_pit_scores(inputs, AT, other_regimes, cache)
    assert changed_regime.cache_hits["risk"] is True and changed_regime.cache_hits["scoring"] is False
    changed_param = compute_pit_scores(inputs, AT, regime_hist["regime"], cache, max_stale_days=60)
    assert changed_param.cache_hits["scoring"] is False
    same = compute_pit_scores(inputs, AT, regime_hist["regime"], cache)
    assert same.cache_hits["scoring"] is True


def test_keep_detail_bypasses_the_scoring_cache(chain):
    nav, attrs, regime_hist, inputs, cache, run1 = chain
    run = compute_pit_scores(inputs, AT, regime_hist["regime"], cache, keep_detail=True)
    assert run.cache_hits["scoring"] is False and "detail" in run.scores.columns


# ---------------- Backtester.run_pit wiring ----------------

class _FakeClf:
    def __init__(self, hist):
        self._hist = hist

    def classify_historical(self):
        return self._hist.copy()


def _patched_backtester(monkeypatch, nav, attrs, regime_hist, seen):
    from proyecto3.src import pit_inputs
    from proyecto3.src.backtesting import Backtester
    inputs = _inputs(nav, attrs, daily=False)

    def _nav(conn, isins=None):
        seen["isins"] = isins
        return nav if isins is None else nav[list(isins)]

    monkeypatch.setattr(pit_inputs, "load_nav_panel", _nav)
    monkeypatch.setattr(pit_inputs, "load_attributes", lambda conn, isins=None: attrs if isins is None else attrs.loc[list(isins)])
    monkeypatch.setattr(pit_inputs, "load_ipc", lambda conn, geography="ES": inputs.ipc)
    monkeypatch.setattr(pit_inputs, "load_rate_deposit", lambda conn: inputs.rate)
    monkeypatch.setattr(pit_inputs, "iter_daily_chunks", lambda conn, isins, chunk_size=300: iter(()))
    bt = object.__new__(Backtester)
    bt.conn = None
    bt._clf = _FakeClf(regime_hist)
    return bt


def test_backtester_run_pit_matches_the_direct_runner_and_summary_accepts_it(chain, monkeypatch, tmp_path, capsys):
    nav, attrs, regime_hist, inputs, cache, run1 = chain
    seen = {}
    bt = _patched_backtester(monkeypatch, nav, attrs, regime_hist, seen)
    table = bt.run_pit(start_date="2013-01-31", end_date="2017-06-30", cache_dir=tmp_path)
    at = pd.date_range("2013-01-31", "2017-06-30", freq=pd.offsets.MonthEnd())
    assert list(table.index) == list(at) and seen["isins"] is None
    no_daily = PitInputs(nav=nav, attrs=attrs, ipc=inputs.ipc, rate=inputs.rate, daily_chunks=None)
    direct = run_pit_backtest(compute_pit_scores(no_daily, at, regime_hist["regime"], ParquetCache(tmp_path / "d")),
                              no_daily, regime_hist, at)
    pd.testing.assert_frame_equal(table, direct)
    text = bt.summary(table)
    assert "BACKTESTING P3" in text and "GLOBAL" in text
    assert set(bt.last_pit_run.timings) >= {"risk", "peers", "scoring", "total"}
    second = bt.run_pit(start_date="2013-01-31", end_date="2017-06-30", cache_dir=tmp_path)
    assert bt.last_pit_run.cache_hits["scoring"] is True
    pd.testing.assert_frame_equal(table, second)


def test_backtester_run_pit_scopes_the_universe_by_isin_list(chain, monkeypatch, tmp_path):
    nav, attrs, regime_hist, inputs, cache, run1 = chain
    seen = {}
    bt = _patched_backtester(monkeypatch, nav, attrs, regime_hist, seen)
    subset = [c for c in nav.columns if c.startswith(("RV", "MM"))]
    table = bt.run_pit(start_date="2014-01-31", end_date="2015-12-31", isins=subset, cache_dir=tmp_path, use_cache=False)
    assert seen["isins"] == subset and len(table) == 24
    held = bt.last_pit_run.scores["isin"].unique()
    assert set(held) <= set(subset)
    assert not list(tmp_path.glob("*.parquet"))                      # use_cache=False wrote nothing


# ============================================================
# N4 costs, IPC+M3 target, series statistics (FND-0190 / FND-0189)
# ============================================================

from proyecto3.src.pit_backtest import (
    COST_GRID_BPS, assemble_table, cost_sensitivity, prepare_pit_backtest, series_stats, table_stats,
    target_window_return,
)
from proyecto3.src.pit_metrics import load_p2_calc
from proyecto3.src.regime_classifier import RegimeClassifier


def _one_date_case(invested=0.6, r=(0.10, -0.02), cash=0.01):
    t = MONTHS[40]
    W = pd.DataFrame({"A": [invested * 0.5], "B": [invested * 0.5]}, index=pd.DatetimeIndex([t]))
    R = pd.DataFrame({"A": [r[0]], "B": [r[1]]}, index=W.index)
    return W, R, pd.Series([cash], index=W.index)


def test_zero_cost_is_exactly_the_frictionless_return():
    W, R, c = _one_date_case()
    assert portfolio_returns(W, R, c, tx_cost=0.0)[0].iloc[0] == portfolio_returns(W, R, c)[0].iloc[0]


def test_entry_cost_is_charged_on_the_invested_sleeve_only_in_closed_form():
    W, R, c = _one_date_case(invested=0.6)
    gross = portfolio_returns(W, R, c)[0].iloc[0]
    cost = 25 / 1e4                                                         # 25 bps, one side
    net = portfolio_returns(W, R, c, tx_cost=cost)[0].iloc[0]
    weighted = 0.3 * 0.10 + 0.3 * -0.02
    invested_value = 0.6 + weighted                                         # end value of the invested sleeve
    assert gross == pytest.approx(weighted + 0.4 * 0.01, abs=1e-6)
    assert gross - net == pytest.approx(cost * invested_value, abs=1e-6)    # the 40% in cash pays nothing
    W1, R1, c1 = _one_date_case(invested=1.0)
    gross_full = portfolio_returns(W1, R1, c1)[0].iloc[0]
    net_full = portfolio_returns(W1, R1, c1, tx_cost=cost)[0].iloc[0]
    assert gross_full - net_full == pytest.approx(cost * (1.0 + 0.5 * 0.10 + 0.5 * -0.02), abs=1e-6)


def test_cost_is_never_charged_on_funds_without_a_usable_nav_or_on_an_empty_portfolio():
    t = MONTHS[40]
    W = pd.DataFrame({"A": [0.5], "DEAD": [0.5]}, index=pd.DatetimeIndex([t]))
    R = pd.DataFrame({"A": [0.04], "DEAD": [np.nan]}, index=W.index)
    zero_cash = pd.Series([0.0], index=W.index)
    gross = portfolio_returns(W, R, zero_cash)[0].iloc[0]
    net = portfolio_returns(W, R, zero_cash, tx_cost=0.01)[0].iloc[0]
    assert gross - net == pytest.approx(0.01 * (0.5 + 0.5 * 0.04), abs=1e-6)        # only A was bought
    empty = pd.DataFrame({"A": [np.nan]}, index=W.index)
    assert np.isnan(portfolio_returns(empty, R[["A"]], zero_cash, tx_cost=0.01)[0].iloc[0])


def test_assembled_table_cost_grid_is_monotone_and_benchmark_is_frictionless(chain):
    nav, attrs, regime_hist, inputs, cache, run1 = chain
    prep = prepare_pit_backtest(run1, inputs, regime_hist, AT)
    t0, t25, t50 = (assemble_table(prep, b) for b in COST_GRID_BPS)
    for w in (1, 3, 12):
        assert (t0[f"cost_{w}m"].dropna() == 0).all()
        assert (t25[f"cost_{w}m"].dropna() > 0).all()
        assert (t50[f"cost_{w}m"] > t25[f"cost_{w}m"] * 1.9).all()                  # ~2x the cost for 2x the bps
        assert (t0[f"ret_{w}m"] >= t25[f"ret_{w}m"]).all() and (t25[f"ret_{w}m"] >= t50[f"ret_{w}m"]).all()
        pd.testing.assert_series_equal(t0[f"bench_{w}m"], t50[f"bench_{w}m"])      # the benchmark pays no costs
        assert np.allclose(t25[f"gross_{w}m"], t0[f"ret_{w}m"], equal_nan=True)
        assert np.allclose(t25[f"gross_{w}m"] - t25[f"cost_{w}m"], t25[f"ret_{w}m"], equal_nan=True)
    # absolute scale: 25 bps on at most ~the whole capital -> a 1m cost between 0 and ~0.25% (catches a unit error)
    assert (t25["cost_1m"].dropna() < 0.0025 * 1.3).all() and (t25["cost_1m"].dropna() > 0.0025 * 0.05).all()
    assert t25["cost_1m"].mean() == pytest.approx(0.0025 * (1.0 - t25["cash_weight"].mean()), rel=0.35)
    both = assemble_table(prep, 25, entry_sides=2)
    assert np.allclose(both["cost_1m"], 2 * t25["cost_1m"], rtol=1e-3, equal_nan=True)   # round trip = twice the entry cost
    assert t25.attrs["tx_cost_bps"] == 25 and both.attrs["entry_sides"] == 2


def test_cost_sensitivity_rows_and_ordering(chain):
    nav, attrs, regime_hist, inputs, cache, run1 = chain
    grid = cost_sensitivity(run1, inputs, regime_hist, AT)
    assert set(grid["bps_per_side"]) == {0.0, 25.0, 50.0} and set(grid["window_m"]) == {1, 3, 12}
    assert len(grid) == 9
    for w, g in grid.groupby("window_m"):
        g = g.sort_values("bps_per_side")
        assert g["mean_ret"].is_monotonic_decreasing and g["mean_excess"].is_monotonic_decreasing
        assert g["mean_cost"].iloc[0] == 0 and g["mean_cost"].iloc[-1] > g["mean_cost"].iloc[1] > 0


def test_target_window_return_is_the_compounded_annual_objective_known_at_entry():
    ann = pd.Series([0.05, 0.08], index=pd.DatetimeIndex([MONTHS[10], MONTHS[20]]))
    at = pd.DatetimeIndex([MONTHS[5], MONTHS[10], MONTHS[15], MONTHS[25]])
    r12 = target_window_return(ann, at, 12)
    r3 = target_window_return(ann, at, 3)
    assert np.isnan(r12[MONTHS[5]])                                         # nothing known yet
    assert r12[MONTHS[10]] == pytest.approx(0.05) and r12[MONTHS[15]] == pytest.approx(0.05)      # carried forward
    assert r12[MONTHS[25]] == pytest.approx(0.08)
    assert r3[MONTHS[25]] == pytest.approx(1.08 ** 0.25 - 1)
    assert target_window_return(None, at, 12).isna().all()


def test_classifier_absolute_target_is_ipc_plus_m3_with_ipc_only_fallback():
    idx = pd.date_range("2020-01-31", periods=6, freq=pd.offsets.MonthEnd())
    clf = object.__new__(RegimeClassifier)
    clf._macro = pd.DataFrame({"ipc_yoy_avg": [0.02, 0.025, np.nan, 0.03, np.nan, 0.035],
                               "m3_yoy": [4.0, 5.0, 6.0, np.nan, np.nan, np.nan]}, index=idx)
    tgt = clf.absolute_target_annual()
    assert tgt.iloc[0] == pytest.approx(0.02 + 0.04) and tgt.iloc[1] == pytest.approx(0.025 + 0.05)
    assert tgt.iloc[2] == pytest.approx(0.025 + 0.06)                       # IPC carried forward (ffill) + M3 of the month
    assert tgt.iloc[3] == pytest.approx(0.03 + 0.06)                        # M3 carried forward
    assert tgt.iloc[5] == pytest.approx(0.035 + 0.06)
    clf._macro = pd.DataFrame({"ipc_yoy_avg": [0.02, 0.03], "m3_yoy": [np.nan, 5.0]}, index=idx[:2])
    early = clf.absolute_target_annual()
    assert early.iloc[0] == pytest.approx(0.02) and early.iloc[1] == pytest.approx(0.03 + 0.05)   # M3 not yet published
    clf._macro = pd.DataFrame({"ipc_yoy_avg": [0.02, 0.03]}, index=idx[:2])  # no M3 column at all
    assert list(clf.absolute_target_annual()) == pytest.approx([0.02, 0.03])
    clf._macro = pd.DataFrame({"m3_yoy": [4.0]}, index=idx[:1])
    assert clf.absolute_target_annual().empty


def test_series_stats_match_hand_computation_and_p2_volatility():
    rng = np.random.default_rng(2)
    idx = pd.date_range("2010-01-31", periods=60, freq=pd.offsets.MonthEnd())
    r = pd.Series(rng.normal(0.006, 0.02, 60), index=idx)
    cash = pd.Series(0.002, index=idx)
    s = series_stats(r, cash)
    ex = r - 0.002
    assert s["sharpe"] == pytest.approx(ex.mean() / ex.std(ddof=1) * np.sqrt(12))
    assert s["ann_return"] == pytest.approx((1 + r).prod() ** (12 / 60) - 1)
    nav = pd.Series(100.0 * np.concatenate([[1.0], (1 + r).cumprod().to_numpy()]))
    returns_mod = load_p2_calc("returns")
    assert s["ann_vol"] == pytest.approx(returns_mod.annualized_volatility(nav))       # same vol as the P2 definition
    cum = (1 + r).cumprod()
    assert s["max_drawdown"] == pytest.approx((cum / cum.cummax() - 1).min()) and s["max_drawdown"] <= 0
    assert s["n_months"] == 60


def test_series_stats_edge_cases():
    idx = pd.date_range("2010-01-31", periods=12, freq=pd.offsets.MonthEnd())
    flat = series_stats(pd.Series(0.01, index=idx))
    assert np.isnan(flat["sharpe"]) and flat["ann_vol"] == pytest.approx(0.0, abs=1e-12)
    assert series_stats(pd.Series([0.01], index=idx[:1]))["n_months"] == 1
    with_gaps = pd.Series([0.01, np.nan, 0.02, np.nan, -0.01], index=idx[:5])
    assert series_stats(with_gaps)["n_months"] == 3
    neg_cash = series_stats(pd.Series(0.01, index=idx) + np.linspace(-0.001, 0.001, 12), pd.Series(-0.0004, index=idx))
    assert np.isfinite(neg_cash["sharpe"])                                  # negative cash rates are fine


class _FakeClfTarget(_FakeClf):
    def __init__(self, hist, target):
        super().__init__(hist)
        self._target = target

    def absolute_target_annual(self):
        return self._target


def test_run_pit_table_carries_costs_target_and_summary_sections(chain, monkeypatch, tmp_path):
    nav, attrs, regime_hist, inputs, cache, run1 = chain
    seen = {}
    bt = _patched_backtester(monkeypatch, nav, attrs, regime_hist, seen)
    target = pd.Series(0.06, index=MONTHS)
    bt._clf = _FakeClfTarget(regime_hist, target)
    free = bt.run_pit(start_date="2013-01-31", end_date="2017-06-30", cache_dir=tmp_path)
    paid = bt.run_pit(start_date="2013-01-31", end_date="2017-06-30", cache_dir=tmp_path, tx_cost_bps=50)
    assert {"cash_ret_1m", "gross_1m", "cost_1m", "target_12m", "vs_target_12m"} <= set(paid.columns)
    assert (free["ret_1m"] - paid["ret_1m"]).dropna().gt(0).all()
    assert free["target_12m"].dropna().iloc[0] == pytest.approx(0.06)
    text = bt.summary(paid)
    assert "SERIE SIMULADA" in text and "Sharpe" in text and "objetivo IPC+M3" in text and "50 pb/lado" in text
    grid = bt.pit_cost_sensitivity()
    assert len(grid) == 9 and "hit_vs_target" in grid.columns
    stats = table_stats(free)
    assert stats["portfolio"]["n_months"] > 30 and np.isfinite(stats["portfolio"]["sharpe"])


def test_pit_cost_sensitivity_requires_a_prior_run():
    from proyecto3.src.backtesting import Backtester
    with pytest.raises(RuntimeError):
        Backtester.pit_cost_sensitivity(object.__new__(Backtester))


# ---------------- chained-series cost = real turnover (not 100% monthly entry) ----------------

from proyecto3.src.pit_backtest import monthly_turnover


def test_monthly_turnover_counts_entries_and_exits_and_restarts_from_cash_after_an_empty_month():
    idx = pd.date_range("2015-01-31", periods=6, freq=pd.offsets.MonthEnd())
    W = pd.DataFrame({"A": [0.8, 0.5, 0.5, np.nan, 1.0, 1.0],
                      "B": [0.2, np.nan, np.nan, np.nan, np.nan, np.nan],
                      "C": [np.nan, 0.5, 0.5, np.nan, np.nan, np.nan]}, index=idx)
    t = monthly_turnover(W)
    assert t.iloc[0] == pytest.approx(1.0)                  # first month: everything is bought from cash
    assert t.iloc[1] == pytest.approx(0.3 + 0.2 + 0.5)      # trim A by 0.3, sell B (0.2), buy C (0.5)
    assert t.iloc[2] == pytest.approx(0.0)                  # unchanged portfolio trades nothing
    assert np.isnan(t.iloc[3])                              # empty portfolio: no turnover, no return
    assert t.iloc[4] == pytest.approx(1.0)                  # after an empty month it starts again from cash
    assert t.iloc[5] == pytest.approx(0.0)


def test_chained_series_is_net_of_turnover_and_a_static_portfolio_pays_almost_nothing(chain):
    nav, attrs, regime_hist, inputs, cache, run1 = chain
    prep = prepare_pit_backtest(run1, inputs, regime_hist, AT)
    free = assemble_table(prep, 0.0)
    paid = assemble_table(prep, 25.0)
    assert np.allclose(free["net_turnover_1m"], free["gross_1m"], equal_nan=True)            # no cost, no difference
    assert np.allclose(paid["net_turnover_1m"], paid["gross_1m"] - 0.0025 * paid["turnover"], equal_nan=True)
    assert (paid["turnover"].dropna() >= 0).all() and paid["turnover"].dropna().iloc[0] > 0.5   # first month enters from cash
    st = table_stats(paid)["portfolio"]
    from_ret = series_stats(paid["ret_1m"], paid["cash_ret_1m"])
    from_net = series_stats(paid["net_turnover_1m"], paid["cash_ret_1m"])
    assert st["sharpe"] == pytest.approx(from_net["sharpe"]) and st["sharpe"] != pytest.approx(from_ret["sharpe"])
    assert table_stats(free)["portfolio"]["sharpe"] == pytest.approx(series_stats(free["ret_1m"], free["cash_ret_1m"])["sharpe"])


def test_summary_reports_turnover_based_cost_for_the_chained_series(chain, monkeypatch, tmp_path):
    nav, attrs, regime_hist, inputs, cache, run1 = chain
    bt = _patched_backtester(monkeypatch, nav, attrs, regime_hist, {})
    bt._clf = _FakeClfTarget(regime_hist, pd.Series(0.06, index=MONTHS))
    table = bt.run_pit(start_date="2013-01-31", end_date="2017-06-30", cache_dir=tmp_path, tx_cost_bps=25)
    text = bt.summary(table)
    assert "neta de la rotacion real" in text and "rotacion media mensual" in text and "coste por rotacion" in text
