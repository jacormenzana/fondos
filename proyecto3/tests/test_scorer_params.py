# proyecto3/tests/test_scorer_params.py
# -*- coding: utf-8 -*-
"""
Scorer parameters in one YAML (FND-0192): zero behaviour change, loud validation, hash feeding the cache keys.
R-7: no DB.

    python -m pytest proyecto3/tests/test_scorer_params.py -v
"""

import copy
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src import fund_scorer as fs
from proyecto3.src.scorer_config import (PARAMS_PATH, REQUIRED_KEYS, SCORER_CONFIG_HASH, load_scorer_params, params_hash,
                                         validate_params)

GOLDEN = json.loads((Path(__file__).parent / "golden_scorer_params.json").read_text(encoding="utf-8"))


def _params():
    return copy.deepcopy(load_scorer_params())


# ---------------- zero behaviour change ----------------

def test_every_loaded_value_equals_the_snapshot_of_the_previous_in_code_constants():
    assert set(GOLDEN) == set(REQUIRED_KEYS)
    differing = [k for k, v in GOLDEN.items() if getattr(fs, k) != v]
    assert not differing, differing


def test_the_yaml_content_equals_the_snapshot():
    assert load_scorer_params() == GOLDEN


def test_scoring_runs_and_is_deterministic_on_a_fixed_universe():
    rng = np.random.default_rng(5)
    n = 60
    df = pd.DataFrame({
        "Fund_Name": [f"F{i}" for i in range(n)],
        "Fund_Nature": rng.choice(["Renta Variable", "Mixtos", "Monetario", "Renta Fija Flexible"], n),
        "return_ann_real": rng.normal(0.03, 0.03, n), "sharpe": rng.normal(0.5, 0.4, n), "max_dd": -rng.uniform(0.01, 0.2, n),
        "srri_nav": rng.integers(1, 7, n).astype(float), "alpha_persistence": rng.uniform(0, 1, n),
        "capture_ratio": rng.uniform(0.4, 1.6, n), "momentum_rank": rng.uniform(0, 1, n),
        "fx_contribution_pct": rng.normal(0, 0.5, n), "fund_family_id": [None] * n,
    }, index=pd.Index([f"ISIN{i:03d}" for i in range(n)], name="isin"))
    a = fs.score_funds_from_df(df.copy(), "Expansion", verbose=False)
    b = fs.score_funds_from_df(df.copy(), "Expansion", verbose=False)
    pd.testing.assert_frame_equal(a.drop(columns=["detail"], errors="ignore"), b.drop(columns=["detail"], errors="ignore"))
    assert len(a) > 0


# ---------------- the inline numbers are really read from the config ----------------

def test_regime_percentile_cutoffs_come_from_the_config(monkeypatch):
    from proyecto3.src.regime_classifier import _REGIME_SUFFIX
    suffix = _REGIME_SUFFIX["Expansion"]
    df = pd.DataFrame({f"return_ann_{suffix}": np.arange(101.0)})
    base = fs.compute_regime_percentiles(df, "Expansion", verbose=False)
    assert base["regime_return_p25"] == pytest.approx(25.0) and base["regime_return_p75"] == pytest.approx(75.0)
    monkeypatch.setattr(fs, "REGIME_PERCENTILE_LOW", 0.10)
    monkeypatch.setattr(fs, "REGIME_PERCENTILE_HIGH", 0.90)
    moved = fs.compute_regime_percentiles(df, "Expansion", verbose=False)
    assert moved["regime_return_p25"] == pytest.approx(10.0) and moved["regime_return_p75"] == pytest.approx(90.0)


def test_short_horizon_weights_come_from_the_config(monkeypatch):
    monkeypatch.setattr(fs, "SHORT_HORIZON_SCORING_ENABLED", True)
    n = 20
    rng = np.random.default_rng(1)
    df = pd.DataFrame({"Fund_Nature": ["Mixtos"] * n, "return_ann_real": rng.normal(size=n), "sharpe": rng.normal(size=n),
                       "max_dd": -rng.uniform(size=n), "alpha_persistence": rng.uniform(size=n),
                       "capture_ratio": rng.uniform(size=n), "momentum_rank": rng.uniform(size=n),
                       "short_return_cum__rolling_3m": rng.normal(size=n)}, index=[f"I{i}" for i in range(n)])
    monkeypatch.setattr(fs, "SHORT_HORIZON_WEIGHTS", {"Equilibrada": {"short_return_cum__rolling_3m": 0.5}})
    with_short = fs.compute_base_scores(df, "Equilibrada")
    monkeypatch.setattr(fs, "SHORT_HORIZON_WEIGHTS", {})
    without = fs.compute_base_scores(df, "Equilibrada")
    assert not np.allclose(with_short.to_numpy(), without.to_numpy())


# ---------------- validation fails loudly ----------------

def test_a_missing_key_is_rejected():
    p = _params()
    del p["MULT_FX_MALUS"]
    with pytest.raises(ValueError, match="missing keys"):
        validate_params(p)


def test_an_unknown_key_is_rejected():
    p = _params()
    p["MULT_TYPO_BONUS"] = 1.2
    with pytest.raises(ValueError, match="unknown keys"):
        validate_params(p)


def test_a_non_numeric_scalar_is_rejected():
    p = _params()
    p["MULT_ALPHA_BONUS"] = "1.15"
    with pytest.raises(ValueError, match="must be a number"):
        validate_params(p)


def test_weights_that_do_not_sum_to_one_are_rejected():
    p = _params()
    p["SUBPORTFOLIO_WEIGHTS"]["Defensiva"]["sharpe"] += 0.05
    with pytest.raises(ValueError, match="sums to"):
        validate_params(p)


def test_a_sub_portfolio_missing_from_a_table_is_rejected():
    p = _params()
    del p["MAX_DRAWDOWN_BY_SUB"]["Dinamica"]
    with pytest.raises(ValueError, match="covers"):
        validate_params(p)


def test_inverted_percentile_cutoffs_are_rejected():
    p = _params()
    p["REGIME_PERCENTILE_LOW"], p["REGIME_PERCENTILE_HIGH"] = 0.8, 0.2
    with pytest.raises(ValueError, match="REGIME_PERCENTILE"):
        validate_params(p)


# ---------------- hash and loading ----------------

def test_the_hash_is_stable_and_changes_with_any_value():
    assert params_hash(load_scorer_params()) == SCORER_CONFIG_HASH == params_hash(_params())
    p = _params()
    p["MULT_ALPHA_BONUS"] = 1.16
    assert params_hash(p) != SCORER_CONFIG_HASH


def test_the_hash_ignores_key_order_and_file_formatting(tmp_path):
    p = _params()
    reordered = dict(reversed(list(p.items())))
    f = tmp_path / "other.yaml"
    f.write_text(yaml.safe_dump(reordered, sort_keys=False), encoding="utf-8")
    assert params_hash(load_scorer_params(f)) == SCORER_CONFIG_HASH


def test_another_file_can_be_loaded_and_validated(tmp_path):
    p = _params()
    p["MULT_ALPHA_BONUS"] = 1.30
    f = tmp_path / "trial.yaml"
    f.write_text(yaml.safe_dump(p), encoding="utf-8")
    assert load_scorer_params(f)["MULT_ALPHA_BONUS"] == 1.30
    assert PARAMS_PATH.exists()
