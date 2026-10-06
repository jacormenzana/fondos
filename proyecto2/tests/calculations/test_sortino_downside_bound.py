"""FND-0234: the Sortino/Sharpe consistency checks, validated against the REAL P2 formulas (returns.py), not a restatement.

Random monthly series (including 12-point crisis-length windows, money-market-like tiny volatility, deep negative excess) go
through sharpe_ratio / sortino_ratio / annualized_return / annualized_volatility exactly as run_pipeline computes them, with
the risk-free rate P2 really uses (date-aligned, NOT the 4% config constant). Properties:

* SORTINO_VS_SHARPE_UP (sign taken from the stored Sharpe) never fires on a correct pair;
* SORTINO_DOWNSIDE_BOUND never fires on a correct pair, whatever the shortfall below the risk-free rate;
* the retired premise (sortino <= sharpe when the numerator is negative) IS false on correct data -- so retiring it was right;
* a corrupted downside deviation IS caught.
"""
import numpy as np
import pandas as pd
import pytest

from shared.statistical_audit.catalog_invariants import P2_INVARIANTS
from shared.statistical_audit.invariants import check_invariant
from shared.statistical_audit.ratio_bounds import add_downside_deviation_columns
from src.calculations.returns import annualized_return, annualized_volatility, sharpe_ratio, sortino_ratio

RULES = {r.rule_id: r for r in P2_INVARIANTS}
UP, BOUND = RULES["SORTINO_VS_SHARPE_UP"], RULES["SORTINO_DOWNSIDE_BOUND"]


def _row(returns, rf):
    nav = pd.Series(100.0 * np.cumprod(1.0 + np.asarray(returns)))
    nav = pd.concat([pd.Series([100.0]), nav], ignore_index=True)             # N returns -> N + 1 NAV points
    return {
        "return_ann": annualized_return(nav), "vol_ann": annualized_volatility(nav),
        "sharpe": sharpe_ratio(nav, rf), "sortino": sortino_ratio(nav, rf), "n_obs": len(nav),
    }


def _random_frame(seed, n_series=1500):
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n_series):
        n = int(rng.choice([11, 12, 17, 24, 36, 60, 120, 240]))
        sigma = float(rng.choice([0.0008, 0.002, 0.01, 0.03, 0.06]))           # money-market ... equity-like
        mu = float(rng.choice([-0.04, -0.01, -0.002, 0.0, 0.002, 0.006, 0.02]))    # monthly drift, incl. deep losers
        rf = float(rng.choice([0.0, 0.0075, 0.025, 0.04]))
        rows.append(_row(rng.normal(mu, sigma, n).clip(-0.6, 1.0), rf))
    return pd.DataFrame(rows).dropna()


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_correct_ratios_never_violate_the_up_rule_or_the_downside_bound(seed):
    frame = add_downside_deviation_columns(_random_frame(seed))
    assert check_invariant(frame, UP).n_violations == 0
    assert check_invariant(frame, BOUND).n_violations == 0
    assert check_invariant(frame, BOUND).n_applicable > 100              # the check is exercised, not vacuous


def test_the_retired_premise_is_false_on_correct_data():
    """sortino <= sharpe with a negative numerator fires on perfectly correct values -- the reason it was retired."""
    frame = _random_frame(7)
    negative = frame[(frame["sharpe"] < 0) & (frame["sortino"] < 0)]
    assert (negative["sortino"] > negative["sharpe"] + 1e-4).sum() > 20


def test_a_deep_loser_below_the_risk_free_rate_has_downside_deviation_above_sigma():
    row = _row(np.full(12, -0.03) + np.array([0.004, -0.004] * 6), rf=0.025)
    assert row["sortino"] > row["sharpe"] and row["sharpe"] < 0            # legitimate, and not an alarm
    frame = add_downside_deviation_columns(pd.DataFrame([row]))
    assert check_invariant(frame, BOUND).n_violations == 0


def test_a_corrupted_downside_deviation_is_caught():
    row = _row(np.random.default_rng(5).normal(-0.004, 0.01, 36), rf=0.025)
    assert row["sharpe"] < 0 and row["sortino"] < 0
    corrupted = dict(row, sortino=row["sortino"] / 5.0)                     # downside deviation 5x too large
    frame = add_downside_deviation_columns(pd.DataFrame([row, corrupted]))
    result = check_invariant(frame, BOUND)
    assert result.n_applicable == 2 and result.n_violations == 1


def test_the_up_rule_catches_a_downside_deviation_above_sigma_with_a_positive_numerator():
    row = _row(np.random.default_rng(6).normal(0.01, 0.01, 36), rf=0.025)
    assert row["sharpe"] > 0
    corrupted = dict(row, sortino=row["sharpe"] / 3.0)                      # sortino far below sharpe: dd > sigma
    frame = pd.DataFrame([row, corrupted])
    assert check_invariant(frame, UP).n_violations == 1


def test_the_bound_holds_under_the_interval_correct_annualisation_too():
    """FND-0240: if annualized_return is ever fixed to count intervals, the bound must not start firing."""
    rng = np.random.default_rng(11)
    rows = []
    for _ in range(800):
        n = int(rng.choice([11, 12, 24, 60]))
        rets = rng.normal(float(rng.choice([-0.03, -0.005, 0.004])), float(rng.choice([0.002, 0.02])), n).clip(-0.6, 1.0)
        rf = 0.025
        nav = pd.concat([pd.Series([100.0]), pd.Series(100.0 * np.cumprod(1 + rets))], ignore_index=True)
        total = nav.iloc[-1] / nav.iloc[0]
        ret_ann = total ** (12.0 / n) - 1.0                                  # exponent over the n RETURNS
        vol = annualized_volatility(nav)
        dd = float(np.sqrt(np.mean(np.minimum(rets - rf / 12, 0) ** 2)) * np.sqrt(12))
        if dd < 0.001 or vol == 0:
            continue
        rows.append({"return_ann": ret_ann, "vol_ann": vol, "sharpe": (ret_ann - rf) / vol,
                     "sortino": (ret_ann - rf) / dd, "n_obs": len(nav)})
    frame = add_downside_deviation_columns(pd.DataFrame(rows))
    assert check_invariant(frame, BOUND).n_violations == 0
    assert check_invariant(frame, UP).n_violations == 0


def test_undefined_rows_are_not_applicable_never_violating():
    frame = pd.DataFrame([
        {"return_ann": -1.0, "vol_ann": 0.1, "sharpe": -2.0, "sortino": -1.0, "n_obs": 12},   # 1 + return_ann = 0
        {"return_ann": -0.1, "vol_ann": 0.1, "sharpe": -2.0, "sortino": 0.0, "n_obs": 12},    # sortino 0
        {"return_ann": -0.1, "vol_ann": 0.1, "sharpe": -2.0, "sortino": np.nan, "n_obs": 12},
    ])
    out = add_downside_deviation_columns(frame)
    assert check_invariant(out, BOUND).n_violations == 0


def test_missing_columns_give_nan_columns_not_an_error():
    out = add_downside_deviation_columns(pd.DataFrame({"x": [1, 2]}))
    assert out[["rf_implied", "dd_ann_implied", "dd_ann_max"]].isna().all().all()
