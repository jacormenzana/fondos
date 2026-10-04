# tests/test_dq_guards.py
"""FND-0208: shared/dq_guards.py -- pure numpy, no DB, no pipeline imports (R-7)."""
import math
import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.dq_guards import bound_or_none, most_collinear_column, scaled_condition_number  # noqa: E402


def _design(n=80, seed=0):
    rng = np.random.default_rng(seed)
    return np.column_stack([np.ones(n), rng.normal(0, 0.05, n), rng.normal(0, 3.0, n)])


def test_well_conditioned_design_has_small_index_regardless_of_scale():
    assert scaled_condition_number(_design()) < 30


def test_near_constant_column_is_collinear_with_intercept():
    X = _design()
    X[:, 2] = 0.4 + 1e-7 * np.random.default_rng(1).normal(size=len(X))
    assert scaled_condition_number(X) > 1e4
    assert most_collinear_column(X) == 2


def test_exactly_singular_or_non_finite_is_infinite():
    X = _design()
    X[:, 2] = 2 * X[:, 1]
    assert scaled_condition_number(X) > 1e8
    X[:, 2] = 0.0
    assert math.isinf(scaled_condition_number(X))
    X[0, 1] = np.nan
    assert math.isinf(scaled_condition_number(X))


def test_intercept_is_never_selected():
    X = _design()
    X[:, 1] = 1.0 + 1e-9 * np.random.default_rng(2).normal(size=len(X))
    assert most_collinear_column(X) in (1, 2)


def test_bound_or_none():
    assert bound_or_none(1.5, 5.0) == (1.5, False)
    assert bound_or_none(-5.0, 5.0) == (-5.0, False)
    assert bound_or_none(5.0001, 5.0) == (None, True)
    assert bound_or_none(-1127.0, 5.0) == (None, True)
    assert bound_or_none(float("nan"), 5.0) == (None, True)
    assert bound_or_none(float("inf"), 5.0) == (None, True)
    assert bound_or_none(None, 5.0) == (None, True)
