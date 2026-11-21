"""FND-0435 D1 / D2: proyecto3/src/pit_crisis_decompose.py. Synthetic frames, no database (R-7). Invariants pinned: (1) the per-metric components ARE the scorer's base score (equal to
`compute_base_scores`, with missing metrics, an all-missing nature and a nature without bonus); (2) the gaps add up; (3) the independent filters and the funnel are hand-computed and fail open on a
missing metric; (4) trailing and forward NAV numbers are hand-computed and a fund with a hole in the window is reported, not dropped silently."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from proyecto3.src import fund_scorer as fs  # noqa: E402
from proyecto3.src import pit_crisis_decompose as X  # noqa: E402


def frame(n=24, seed=5):
    rng = np.random.default_rng(seed)
    nat = (["Monetario"] * 6 + ["Renta Fija Corto Plazo"] * 6 + ["Renta Fija Flexible"] * 6 + ["Renta Variable"] * 6)[:n]
    df = pd.DataFrame({"Fund_Nature": nat, "return_ann_real": rng.normal(0.0, 0.03, n), "sharpe": rng.normal(0.2, 0.4, n), "max_dd": -np.abs(rng.normal(0.08, 0.06, n)),
                       "alpha_persistence": rng.uniform(0, 1, n), "capture_ratio": rng.uniform(0.2, 1.5, n), "momentum_rank": rng.uniform(0, 1, n),
                       "srri_nav": rng.integers(1, 7, n).astype(float), "Credit_Quality": ["Investment Grade"] * n}, index=[f"F{i:02d}" for i in range(n)])
    df.loc["F01", "sharpe"] = np.nan                       # a fund without a metric
    df["capture_ratio"] = np.where(df["Fund_Nature"] == "Renta Fija Flexible", np.nan, df["capture_ratio"])     # a whole nature without a metric
    df.loc["F20", "Credit_Quality"] = "High Yield"
    return df


@pytest.mark.parametrize("sub", ["Defensiva", "Equilibrada", "Dinamica"])
def test_components_are_exactly_the_scorers_base_score(sub):
    df = frame()
    comp = X.score_components(df, sub)
    want = fs.compute_base_scores(X.sub_frame(df, sub), sub)
    pd.testing.assert_series_equal(comp["base"].sort_index(), want.sort_index(), check_names=False, atol=1e-12)
    assert set(comp.index) == set(X.sub_frame(df, sub).index)


def test_a_missing_metric_contributes_zero_and_a_nature_without_bonus_gets_one():
    comp = X.score_components(frame(), "Defensiva")
    assert comp.loc["F01", "sharpe"] == 0.0                                   # E-5: the missing metric adds nothing
    assert (comp.loc[comp.index[12:18], "capture_ratio"] == 0.0).all()        # Renta Fija Flexible: nobody has it
    assert comp.loc["F00", "bonus"] == 1.25 and comp.loc["F12", "bonus"] == 1.0
    assert X.score_components(frame(), "Dinamica")["bonus"].isin([1.1, 1.05, 0.95, 1.0]).all()


def test_the_gap_shares_add_up_and_the_total_row_is_the_base_score():
    df = frame()
    comp = X.score_components(df, "Defensiva")
    pool = list(comp.index)
    selected = list(comp.sort_values("base", ascending=False).index[:4])
    g = X.contribution_gap(df, "Defensiva", selected, pool)
    metrics = g[g["metric"] != "total"]
    assert metrics["share_of_gap"].sum() == pytest.approx(1.0)
    assert metrics["gap"].sum() == pytest.approx(metrics["selected_mean"].sum() - metrics["pool_median"].sum())
    assert g.loc[g["metric"] == "total", "selected_mean"].iloc[0] == pytest.approx(comp.loc[selected, "base"].mean())
    assert metrics["selected_mean"].sum() == pytest.approx(comp.loc[selected, "base"].mean())           # the per-metric contributions of the selected add up to their score
    assert (g.loc[g["metric"] == "total", "gap"].iloc[0] > 0)                                          # the best-scored funds are above the pool median


def test_nature_bonus_effect_is_zero_when_all_selected_have_bonus_one():
    df = frame()
    flex = list(df.index[df["Fund_Nature"] == "Renta Fija Flexible"])
    e = X.nature_bonus_effect(df, "Defensiva", flex)
    assert e["with_bonus"] == pytest.approx(e["without_bonus"])
    mon = list(df.index[df["Fund_Nature"] == "Monetario"])
    e2 = X.nature_bonus_effect(df, "Defensiva", mon)
    assert e2["with_bonus"] == pytest.approx(e2["without_bonus"] * 1.25)


def test_independent_filters_are_hand_computed_and_fail_open():
    df = pd.DataFrame({"Fund_Nature": ["Monetario"] * 4, "max_dd": [-0.30, -0.10, np.nan, -0.10], "return_ann_real": [0.01, -0.10, 0.0, 0.0], "srri_nav": [3.0, 3.0, 6.0, 3.0],
                       "Credit_Quality": ["Investment Grade", "Investment Grade", "Investment Grade", "High Yield"]}, index=list("ABCD"))
    f = X.independent_filters(df, "Defensiva")
    assert f.loc["A"].to_dict() == {"f_max_drawdown": True, "f_return_real": False, "f_srri": False, "f_high_yield": False}       # limit -0.20
    assert f.loc["B"].to_dict() == {"f_max_drawdown": False, "f_return_real": True, "f_srri": False, "f_high_yield": False}       # limit -0.05
    assert f.loc["C"].to_dict() == {"f_max_drawdown": False, "f_return_real": False, "f_srri": True, "f_high_yield": False}       # NaN drawdown does not fail
    assert f.loc["D"].to_dict() == {"f_max_drawdown": False, "f_return_real": False, "f_srri": False, "f_high_yield": True}
    assert list(X.independent_filters(df.assign(Fund_Nature="Renta Variable"), "Dinamica").columns) == ["f_max_drawdown", "f_return_real"]   # srri / HY apply to Defensiva only


def test_funnel_table_separates_sole_cause_from_independent_failure():
    df = pd.DataFrame({"Fund_Nature": ["Monetario"] * 4, "max_dd": [-0.30, -0.30, -0.10, -0.10], "return_ann_real": [0.01, -0.10, 0.01, 0.01], "srri_nav": [3.0] * 4,
                       "Credit_Quality": ["Investment Grade"] * 4}, index=list("ABCD"))
    sealed_ok = pd.Series([False, False, True, False], index=list("ABCD"))
    reason = pd.Series(["max_drawdown=-0.30", "max_drawdown=-0.30", None, "short_max_drawdown_6m=-0.2"], index=list("ABCD"))
    t = X.funnel_table(df, "Defensiva", sealed_ok, reason).loc["Monetario"]
    assert t["n"] == 4 and t["f_max_drawdown"] == 0.5 and t["f_return_real"] == 0.25
    assert t["only_f_max_drawdown"] == 0.25                               # A fails the drawdown only; B fails drawdown AND return
    assert t["pass_metric_filters"] == 0.5 and t["eligible_sealed"] == 0.25 and t["short_gate_first"] == 0.25


def test_trailing_and_forward_numbers_are_hand_computed_and_holes_are_reported():
    idx = pd.date_range("2005-01-31", periods=60, freq="ME")
    nav = pd.DataFrame({"A": 100.0 * 1.01 ** np.arange(60), "B": 100.0 * 1.00 ** np.arange(60), "H": 100.0 * 1.02 ** np.arange(60)}, index=idx)
    nav.loc[idx[40], "H"] = np.nan                                         # a hole inside the forward window of date 36
    t = idx[36]
    tr = X.trailing_return(nav, t, ["A", "B"], 36)
    assert tr["A"] == pytest.approx(1.01 ** 36 - 1) and tr["B"] == pytest.approx(0.0)
    assert np.isnan(X.trailing_return(nav, idx[10], ["A"], 36)["A"])      # not enough history
    fp = X.forward_path(nav, t, pd.Series({"A": 0.5, "B": 0.5, "H": 0.0}), 12)
    assert fp["ret_12m"] == pytest.approx(0.5 * 1.01 ** 12 + 0.5 - 1) and fp["valid_share"] == 1.0
    fp2 = X.forward_path(nav, t, pd.Series({"A": 0.5, "H": 0.5}), 12)
    assert fp2["valid_share"] == pytest.approx(0.5)                        # H has a hole: half the weight had no complete path, said so
    assert fp2["ret_12m"] == pytest.approx(1.01 ** 12 - 1)
    assert np.isnan(X.forward_path(nav, idx[55], pd.Series({"A": 1.0}), 12)["ret_12m"])      # the window runs past the data
    dd = X.forward_path(nav.assign(A=[100.0] * 37 + [90.0] + [95.0] * 22), t, pd.Series({"A": 1.0}), 12)
    assert dd["max_dd"] == pytest.approx(-0.10)


def test_the_module_reads_no_database_and_fills_nothing_silently():
    text = (ROOT / "proyecto3" / "src" / "pit_crisis_decompose.py").read_text(encoding="utf-8")
    for banned in ("psycopg", "get_connection", "shared.db", ".fillna(", "to_markdown"):
        assert banned not in text, banned


# ---- counterfactuals (frozen definitions) --------------------------------------------------------------------------------

def sealed_of(df, regime="Expansion"):
    """What the real scorer says about a frame (no short-gate columns), the stand-in for a sealed block."""
    return fs.score_funds_from_df(df, regime, verbose=False)


def test_the_frozen_variant_list_is_pinned():
    assert X.FROZEN_VARIANTS == ("w0_return_ann_real", "w0_sharpe", "w0_max_dd", "w0_alpha_persistence", "w0_capture_ratio", "w0_momentum_rank", "bonus_1", "roll36", "roll60",
                                 "dd_halflife36", "short_gates_off", "dd_filter_off")
    with pytest.raises(ValueError):
        X.variant_setup("mystery", "Defensiva", frame(), "2008-10-31")


@pytest.mark.parametrize("sub", ["Defensiva", "Equilibrada", "Dinamica"])
def test_the_baseline_variant_reproduces_the_scorer_row_by_row(sub):
    df = frame()
    sealed = sealed_of(df)
    block = sealed[sealed["subportfolio"] == sub]
    rows = X.variant_rows(df, block, sub, "baseline", "2008-10-31").set_index("isin")
    ref = block.set_index("isin")
    assert (rows["eligible"].reindex(ref.index) == ref["eligible"]).all()
    pd.testing.assert_series_equal(rows["score_base"].reindex(ref.index).round(4), ref["score_base"], check_names=False)
    pd.testing.assert_series_equal(rows["score_final"].reindex(ref.index).round(4), ref["score_final"], check_names=False)


def test_weight_zero_and_bonus_one_variants():
    df = frame()
    block = sealed_of(df).query("subportfolio == 'Defensiva'")
    base = X.variant_rows(df, block, "Defensiva", "baseline", "2008-10-31").set_index("isin")["score_base"]
    w0 = X.variant_rows(df, block, "Defensiva", "w0_sharpe", "2008-10-31").set_index("isin")["score_base"]
    comp = X.score_components(df, "Defensiva")
    pd.testing.assert_series_equal(w0.sort_index(), ((comp.drop(columns=["bonus", "base", "sharpe"]).sum(axis=1)) * comp["bonus"]).sort_index(), check_names=False)
    assert not np.allclose(base.sort_index(), w0.sort_index())
    b1 = X.variant_rows(df, block, "Defensiva", "bonus_1", "2008-10-31").set_index("isin")["score_base"]
    pd.testing.assert_series_equal(b1.sort_index(), comp.drop(columns=["bonus", "base"]).sum(axis=1).sort_index(), check_names=False)


def test_dd_filter_off_frees_only_the_funds_that_fail_that_filter_alone():
    df = pd.DataFrame({"Fund_Nature": ["Monetario"] * 4, "Fund_Name": list("abcd"), "fund_family_id": [None] * 4, "max_dd": [-0.30, -0.30, -0.10, -0.10],
                       "return_ann_real": [0.01, -0.10, 0.01, 0.01], "sharpe": [0.5] * 4, "alpha_persistence": [0.5] * 4, "capture_ratio": [0.5] * 4, "momentum_rank": [0.5] * 4,
                       "srri_nav": [3.0] * 4, "Credit_Quality": ["Investment Grade"] * 4}, index=list("ABCD"))
    block = sealed_of(df).query("subportfolio == 'Defensiva'")
    base = X.variant_rows(df, block, "Defensiva", "baseline", "2008-10-31").set_index("isin")["eligible"]
    off = X.variant_rows(df, block, "Defensiva", "dd_filter_off", "2008-10-31").set_index("isin")["eligible"]
    assert base.to_dict() == {"A": False, "B": False, "C": True, "D": True}
    assert off.to_dict() == {"A": True, "B": False, "C": True, "D": True}        # B also fails the return filter: stays out


def test_short_gates_off_lifts_only_the_short_gate_and_never_a_non_metric_reason():
    df = frame()
    block = sealed_of(df).query("subportfolio == 'Defensiva'").copy()
    ok_ids = list(block.index[block["eligible"]])
    block.loc[ok_ids[0], ["eligible", "exclusion_reason"]] = [False, "short_max_drawdown_6m=-0.10 < -0.08 (Defensiva)"]
    block.loc[ok_ids[1], ["eligible", "exclusion_reason"]] = [False, "data_suspect: flagged"]
    a, b = block.loc[ok_ids[0], "isin"], block.loc[ok_ids[1], "isin"]
    base = X.variant_rows(df, block, "Defensiva", "baseline", "2008-10-31").set_index("isin")["eligible"]
    off = X.variant_rows(df, block, "Defensiva", "short_gates_off", "2008-10-31").set_index("isin")["eligible"]
    assert not base[a] and off[a]                                              # the short gate is lifted
    assert not base[b] and not off[b]                                          # a data-suspicion reason is not re-derivable and never lifted


def test_halflife_drawdown_is_hand_computed():
    idx = pd.date_range("2005-01-31", periods=40, freq="ME")
    nav = pd.DataFrame({"A": [100.0, 80.0] + [100.0] * 38, "B": [100.0] * 39 + [90.0], "C": [100.0] * 40}, index=idx)
    hl = X.halflife_drawdown(nav, idx[37], 36)            # month 1 is 36 months old at index 37: depth -0.20 counts half; the 80 -> 100 recovery leaves depth 0 afterwards
    assert hl["A"] == pytest.approx(-0.10) and hl["C"] == 0.0
    hl_recent = X.halflife_drawdown(nav, idx[39], 36)
    assert hl_recent["B"] == pytest.approx(-0.10)         # a fresh drawdown counts in full
    assert hl_recent["A"] == pytest.approx(-0.20 * 0.5 ** (38 / 36))


def test_variant_outcome_is_hand_computed():
    idx = pd.date_range("2008-01-31", periods=14, freq="ME")
    nav = pd.DataFrame({"RV": 100.0 * 1.0 ** np.arange(14), "MM": 100.0 * 1.01 ** np.arange(14)}, index=idx)
    attrs = pd.DataFrame({"Fund_Nature": ["Renta Variable", "Monetario"]}, index=["RV", "MM"])
    rows = pd.DataFrame({"subportfolio": ["Defensiva"] * 4, "fund_nature": ["Renta Fija Flexible"] * 2 + ["Renta Fija Corto Plazo"] * 2, "eligible": [True, False, True, True]})
    o = X.variant_outcome({"RV": 0.25, "MM": 0.75}, attrs, rows, nav, idx[0])
    assert o["high_beta_share"] == 0.25 and o["monetario_share"] == 0.75 and o["credit_eligible_share"] == 0.75 and o["n_funds"] == 2
    assert o["fwd12_HINDSIGHT"] == pytest.approx(0.25 * 1.0 + 0.75 * 1.01 ** 12 - 1)


def test_the_fast_family_dedupe_equals_the_scorers_function():
    rng = np.random.default_rng(11)
    n = 400
    rows = pd.DataFrame({"isin": [f"X{i:04d}" for i in range(n)], "subportfolio": rng.choice(["Defensiva", "Equilibrada"], n),
                         "fund_family_id": rng.choice([None] + [f"FAM{j}" for j in range(60)], n), "score_final": rng.random(n)})
    rows.loc[rng.choice(n, 40, replace=False), "score_final"] = 0.0                       # ineligible rows carry 0: the scorer breaks those ties arbitrarily, so compare the eligible ones
    want = fs.deduplicate_by_family(rows.copy(), verbose=False)
    got = X.dedupe_by_family(rows)
    w, g = want[want["score_final"] > 0], got[got["score_final"] > 0]
    assert set(w["isin"]) == set(g["isin"])
    assert len(want) == len(got)                                                          # the same number of representatives, zero-score ties included
