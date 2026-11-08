# proyecto2/tests/calculations/test_floor_and_stale_rows_fnd0401.py
# -*- coding: utf-8 -*-
"""FND-0401 (2026-11-08): a minimum sample for the rolling slope, and no stale statistic left behind by a family that skips one.

R-7: imports only rolling_stats, regime_returns and metrics_writer's pure helper (no pipeline, no DB; the real deletion is in test_metrics_writer_pg.py).
Evidence behind it (FND-0393 forensics of the 22 funds with |MAD-Z| >= 50): 11 were `sharpe_slope|rolling_3y` published on 3-7 observations where the nature's norm is 36; and 1,870 of the 1,891 funds
with fewer than REGIME_MIN_OBS expansion months still carried a return_ann_expansion computed when the regime held more months.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for _p in (_ROOT, _ROOT / "proyecto2"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import numpy as np
import pandas as pd

from shared import config
from src.calculations.regime_returns import (
    CRISIS_STAT_NAMES, MIN_OBS_REGIME, REGIME_STAT_BASES, _REGIME_SUFFIX, compute_regime_returns, regime_owned_metrics,
)
from src.calculations.rolling_stats import compute_timeseries_snapshots, slope_metric_keys
from src.writers.metrics_writer import unproduced_pairs


def _ts(n, metric="sharpe", real_flag=0, window="rolling_3y", isin="A1"):
    rng = np.random.default_rng(3)
    return pd.DataFrame({"isin": isin, "metric": metric, "window": window, "real_flag": real_flag,
                         "date": pd.date_range("2020-01-31", periods=n, freq="ME"), "value": rng.normal(0.5, 0.2, n)})


def _slope_rows(df, **kw):
    return [r for r in compute_timeseries_snapshots(df, **kw) if r["metric"].endswith("_slope")]


# ------------------------------------------------------------------------------------------ the slope floor
def test_a_slope_needs_the_minimum_number_of_observations():
    assert config.ROLLING_SLOPE_MIN_OBS == 12
    for n in (3, 5, 7, 11):
        assert _slope_rows(_ts(n)) == [], n                                          # the 3-7 point slopes that read as 50-400 deviations are gone
    r = _slope_rows(_ts(12))
    assert len(r) == 1 and r[0]["metric"] == "sharpe_slope" and r[0]["source_rows"] == 12
    assert _slope_rows(_ts(60))[0]["source_rows"] == 36                              # the window is still capped at 36 points


def test_the_real_return_slope_follows_the_same_floor():
    assert _slope_rows(_ts(8, metric="return_ann", real_flag=1)) == []
    assert [r["metric"] for r in _slope_rows(_ts(20, metric="return_ann", real_flag=1))] == ["return_ann_slope"]


def test_the_floor_does_not_touch_pctile_self_and_the_parameter_overrides_the_config():
    snaps = compute_timeseries_snapshots(_ts(12))
    assert sorted(r["metric"] for r in snaps) == ["sharpe_pctile_self", "sharpe_slope"]          # both appear at exactly 12
    assert [r["metric"] for r in compute_timeseries_snapshots(_ts(11))] == []                      # below both floors
    assert len(_slope_rows(_ts(5), min_slope_obs=3)) == 1                                           # an explicit 3 restores the old behaviour (tests, studies)
    assert _slope_rows(_ts(20), min_slope_obs=25) == []


def test_the_slope_keys_are_the_ones_the_function_publishes():
    assert slope_metric_keys() == [("return_ann_slope", 1), ("sharpe_slope", 0)]
    produced = {(r["metric"], r["real_flag"]) for r in _slope_rows(pd.concat([_ts(30), _ts(30, metric="return_ann", real_flag=1)]))}
    assert produced == set(slope_metric_keys())


# ------------------------------------------------------------------------------------------ what a family owns and may not leave behind
def test_the_regime_family_owns_exactly_its_per_regime_statistics_and_the_crisis_scores():
    owned = regime_owned_metrics()
    assert len(owned) == len(_REGIME_SUFFIX) * len(REGIME_STAT_BASES) + len(CRISIS_STAT_NAMES) == 37
    assert ("return_ann_expansion", 0) in owned and ("max_dd_shock_energetico", 0) in owned and ("crisis_stress_score_ttr", 0) in owned
    assert not any(m.startswith("n_obs_") or m == "regime_coverage_ratio" for m, _ in owned)          # written on every run: never deleted


def _nav(n=120):
    rng = np.random.default_rng(42)
    return pd.DataFrame({"date": pd.date_range("2015-01-31", periods=n, freq="ME"), "nav": 100.0 * np.cumprod(1 + rng.normal(0.005, 0.02, n))})


def _regime_df(counts, n=120):
    labels = [r for r, k in counts.items() for _ in range(k)]
    labels += [list(counts)[0]] * (n - len(labels))
    return pd.DataFrame({"regime": labels[:n]}, index=pd.date_range("2015-01-31", periods=n, freq="ME"))


def test_what_a_run_produces_is_inside_what_the_family_owns_plus_what_it_always_writes():
    regimes = list(_REGIME_SUFFIX)
    counts = {regimes[0]: 40, regimes[1]: 30, regimes[2]: MIN_OBS_REGIME - 1}                       # one regime below the floor
    out = compute_regime_returns(_nav(), _regime_df(counts))
    produced = {(m, rf) for m, _, rf in out}
    always = {(m, rf) for m, rf in produced if m.startswith("n_obs_") or m == "regime_coverage_ratio"}
    assert produced - always <= set(regime_owned_metrics())                                          # nothing is published that the family does not own


def test_a_regime_below_the_floor_leaves_its_statistics_in_the_stale_set_and_only_those():
    regimes = list(_REGIME_SUFFIX)
    below = regimes[2]
    counts = {regimes[0]: 60, regimes[1]: 40, below: MIN_OBS_REGIME - 1}
    out = compute_regime_returns(_nav(), _regime_df(counts))
    stale = unproduced_pairs(regime_owned_metrics(), [(m, rf) for m, _, rf in out])
    suffix = _REGIME_SUFFIX[below]
    assert (f"return_ann_{suffix}", 0) in stale and (f"vol_ann_{suffix}", 0) in stale                # the regime that dropped under 12 months: its old rows must go
    covered = _REGIME_SUFFIX[regimes[0]]
    assert (f"return_ann_{covered}", 0) not in stale                                                 # a covered regime keeps its rows
    assert ("n_obs_" + suffix, 0) not in stale                                                       # n_obs is always rewritten, never owned


def test_unproduced_pairs_is_sorted_deduplicated_and_keyed_by_real_flag():
    assert unproduced_pairs([("b", 0), ("a", 0), ("a", 1), ("a", 0)], [("a", 0)]) == [("a", 1), ("b", 0)]
    assert unproduced_pairs([("a", 0)], [("a", 0), ("z", 1)]) == []
    assert unproduced_pairs([], [("a", 0)]) == []


# ------------------------------------------------------------------------------------------ the wiring
def test_the_pipeline_deletes_what_the_regime_family_and_the_slope_block_no_longer_produce():
    src = (_ROOT / "proyecto2" / "src" / "pipeline" / "run_pipeline.py").read_text(encoding="utf-8")
    assert "owned=regime_owned_metrics," in src                                                      # only the regime family declares ownership
    assert src.count("owned=") == 1 and src.count("_delete_unproduced(") == 2                        # the family runner and the slope block
    assert src.index("spec.writer(") < src.index("spec.owned()")                                     # delete AFTER the write of today's rows
    assert "slope_metric_keys()" in src
