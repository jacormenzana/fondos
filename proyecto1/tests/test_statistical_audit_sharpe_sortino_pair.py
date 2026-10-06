# proyecto1/tests/test_statistical_audit_sharpe_sortino_pair.py
# -*- coding: utf-8 -*-
"""FND-0234: SHARPE_EQUALS_SORTINO is eligible only where both ratios are clearly positive. R-7: pure."""
import os
import sys

import pandas as pd

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.statistical_audit.catalog_pairs import P2_PAIRS, _positive_ratio_eligibility  # noqa: E402
from shared.statistical_audit.comparisons import compare_pairs  # noqa: E402
from shared.statistical_audit.tolerances import RATIO_ELIGIBILITY_FLOOR  # noqa: E402

RULE = P2_PAIRS["SHARPE_EQUALS_SORTINO"]


def _frame(values):
    return pd.DataFrame({"sharpe": [v for v, _ in values], "sortino": [s for _, s in values]})


def _run(values):
    df = _frame(values)
    return compare_pairs(df, "sharpe", "sortino", RULE)


def test_equal_positive_ratios_trigger_the_rule_once_there_are_enough_of_them():
    """The defect signature: downside deviation collapsed to total volatility on a positive numerator."""
    result = _run([(0.5 + i * 0.1, 0.5 + i * 0.1 + 1e-6) for i in range(10)])
    assert result.n_matches == 10 and result.triggered


def test_numerators_near_zero_are_not_eligible():
    """17 of the 25 live matches: return_ann within 0.1 pp of the risk-free rate, both ratios ~0, equal by arithmetic."""
    result = _run([(i * 1e-5, i * 1e-5 + 1e-6) for i in range(12)])
    assert result.n_eligible == 0 and result.n_matches == 0 and not result.triggered


def test_negative_numerators_are_not_eligible_even_when_equal():
    """8 live matches: dd/sigma legitimately crosses 1 as the shortfall grows -- a coincidence, not a defect."""
    result = _run([(-1.2 - i * 0.05, -1.2 - i * 0.05 - 1e-5) for i in range(10)])
    assert result.n_eligible == 0 and not result.triggered


def test_the_floor_is_the_named_constant_and_both_ratios_must_clear_it():
    just_above = RATIO_ELIGIBILITY_FLOOR * 1.01
    just_below = RATIO_ELIGIBILITY_FLOOR * 0.99
    elig = _positive_ratio_eligibility(_frame([(just_above, just_above), (just_above, just_below),
                                               (just_below, just_above), (-just_above, -just_above)]))
    assert elig.tolist() == [True, False, False, False]


def test_without_the_columns_the_rule_fails_closed():
    assert _positive_ratio_eligibility(pd.DataFrame({"x": [1, 2]})).tolist() == [False, False]


def test_a_dozen_equal_pairs_spread_across_all_signs_trigger_only_through_the_positive_ones():
    values = [(0.4 + i * 0.05, 0.4 + i * 0.05) for i in range(8)] + [(-1.0 - i * 0.1, -1.0 - i * 0.1) for i in range(20)] \
             + [(i * 1e-5, i * 1e-5) for i in range(20)]
    result = _run(values)
    assert result.n_eligible == 8 and result.n_matches == 8 and result.triggered
