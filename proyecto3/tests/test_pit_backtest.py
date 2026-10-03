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
