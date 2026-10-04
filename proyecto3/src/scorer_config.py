# proyecto3/src/scorer_config.py
# -*- coding: utf-8 -*-
"""
Loader of the P3 scorer parameters -- FND-0192.

Every tunable number of fund_scorer.py lives in proyecto3/config/scorer_params.yaml. This module reads it, validates it
and exposes (a) the parameters under their historical constant names (fund_scorer assigns them at import) and (b) a
content hash that feeds the PIT cache keys, so a changed value can never serve stale cached scores.

Validation on load (a typo must fail loudly, never silently fall back to a default):
  * the key set is EXACTLY REQUIRED_KEYS (no missing key, no unknown key);
  * scalars are numbers, the dict-shaped parameters are dicts;
  * BASE_WEIGHTS and each SUBPORTFOLIO_WEIGHTS set sum to 1;
  * the per-sub-portfolio tables cover exactly the sub-portfolios of SUBPORTFOLIO_WEIGHTS.

No DB; R-7.
"""

import hashlib
import json
from pathlib import Path

import yaml

PARAMS_PATH = Path(__file__).resolve().parents[1] / "config" / "scorer_params.yaml"

DICT_KEYS = ("BASE_WEIGHTS", "SUBPORTFOLIO_WEIGHTS", "SUBPORTFOLIO_MAPPING", "NATURE_PROFILE_BONUS", "MAX_DRAWDOWN_BY_SUB",
             "MIN_REAL_RETURN_BY_SUB", "SHORT_DD_LIMIT_BY_SUB", "SHORT_VOL_LIMIT_BY_SUB", "SHORT_HORIZON_WEIGHTS")
SCALAR_KEYS = (
    "MAX_DRAWDOWN_LIMIT", "MIN_REAL_RETURN", "MAX_SRRI_DEFENSIVE", "FX_CONTRIBUTION_LIMIT", "ALPHA_PERS_THRESHOLD", "MULT_FX_MALUS",
    "MULT_ALPHA_BONUS", "CRISIS_MDD_SHALLOW", "CRISIS_MDD_DEEP", "CRISIS_TTR_FAST", "CRISIS_TTR_SLOW", "MULT_CRISIS_MDD_BONUS",
    "MULT_CRISIS_MDD_MALUS", "MULT_CRISIS_TTR_BONUS", "MULT_CRISIS_TTR_MALUS", "REGIME_PERCENTILE_LOW", "REGIME_PERCENTILE_HIGH",
    "MULT_REGIME_RETURN_BONUS", "MULT_REGIME_RETURN_MALUS", "MULT_REGIME_SHARPE_BONUS", "MULT_REGIME_SHARPE_MALUS",
    "MULT_REGIME_SORTINO_BONUS", "MULT_REGIME_SORTINO_MALUS", "MULT_REGIME_MAXDD_BONUS", "MULT_REGIME_MAXDD_MALUS",
    "MIN_OBS_REGIME_SCORING", "REGIME_COVERAGE_MIN", "REGIME_COVERAGE_DAMP", "SLOPE_IMPROVING_THRESHOLD",
    "SLOPE_DETERIORATING_THRESHOLD", "MULT_SLOPE_BONUS", "MULT_SLOPE_MALUS", "SHORT_LIQUIDITY_TRUST_THRESHOLD",
)
REQUIRED_KEYS = DICT_KEYS + SCALAR_KEYS
SUB_TABLES = ("MAX_DRAWDOWN_BY_SUB", "MIN_REAL_RETURN_BY_SUB", "SHORT_DD_LIMIT_BY_SUB", "SHORT_VOL_LIMIT_BY_SUB",
              "SHORT_HORIZON_WEIGHTS", "NATURE_PROFILE_BONUS")


def validate_params(params: dict) -> dict:
    """Raise ValueError on any structural problem; return the same dict."""
    keys = set(params)
    missing, unknown = sorted(set(REQUIRED_KEYS) - keys), sorted(keys - set(REQUIRED_KEYS))
    if missing or unknown:
        raise ValueError(f"scorer params: missing keys {missing}, unknown keys {unknown}")
    for k in DICT_KEYS:
        if not isinstance(params[k], dict):
            raise ValueError(f"scorer params: {k} must be a mapping")
    for k in SCALAR_KEYS:
        v = params[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"scorer params: {k} must be a number, got {v!r}")
    for name, w in [("BASE_WEIGHTS", params["BASE_WEIGHTS"])] + [(f"SUBPORTFOLIO_WEIGHTS[{s}]", w)
                                                                 for s, w in params["SUBPORTFOLIO_WEIGHTS"].items()]:
        total = sum(w.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"scorer params: {name} sums to {total}, expected 1")
    subs = set(params["SUBPORTFOLIO_WEIGHTS"])
    for t in SUB_TABLES:
        if set(params[t]) != subs:
            raise ValueError(f"scorer params: {t} covers {sorted(params[t])}, expected the sub-portfolios {sorted(subs)}")
    if set(params["SUBPORTFOLIO_MAPPING"]) != subs:
        raise ValueError("scorer params: SUBPORTFOLIO_MAPPING must cover the same sub-portfolios as SUBPORTFOLIO_WEIGHTS")
    if not 0.0 <= params["REGIME_PERCENTILE_LOW"] < params["REGIME_PERCENTILE_HIGH"] <= 1.0:
        raise ValueError("scorer params: need 0 <= REGIME_PERCENTILE_LOW < REGIME_PERCENTILE_HIGH <= 1")
    return params


def load_scorer_params(path=None) -> dict:
    """Read and validate the YAML (default: proyecto3/config/scorer_params.yaml)."""
    p = Path(path) if path is not None else PARAMS_PATH
    with open(p, encoding="utf-8") as fh:
        return validate_params(yaml.safe_load(fh))


def params_hash(params: dict) -> str:
    """SHA-1 of the canonical JSON of the parameters (key order and formatting do not matter, values do)."""
    return hashlib.sha1(json.dumps(params, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


SCORER_CONFIG_HASH = params_hash(load_scorer_params())
