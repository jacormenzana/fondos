# proyecto1/tests/test_statistical_audit_invariants.py
# -*- coding: utf-8 -*-
"""Tests unitarios de shared/statistical_audit/invariants.py (check_invariant,
check_bounds — funciones #9-#10, doc/reglas/AUDITORIA_ESTADISTICA.md §4).

Incluye la prueba de regresion clave del documento (§2.4): la version
incondicional "sortino >= sharpe" produce un falso positivo con
excess_return<0 (contraejemplo citado en el analisis externo), y la version
corregida, condicionada por signo, no lo hace.

Cumple R-7: sin importar pipeline.py ni core.io.
"""

import os
import sys

import pandas as pd

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from shared.statistical_audit.invariants import BoundRule, InvariantRule, check_bounds, check_invariant


class TestCheckInvariantBasics:
    def test_hard_invariant_violation_detected(self):
        df = pd.DataFrame({"max_dd": [-0.5, -1.2, 0.1]})  # rows 1,2 violate [-1,0]
        rule = InvariantRule("MAX_DD_RANGE", "-1 <= max_dd <= 0")
        r = check_invariant(df, rule)
        assert r.n_applicable == 3
        assert r.n_violations == 2

    def test_no_false_positive_on_clean_data(self):
        df = pd.DataFrame({"max_dd": [-0.5, -0.1, 0.0]})
        rule = InvariantRule("MAX_DD_RANGE", "-1 <= max_dd <= 0")
        r = check_invariant(df, rule)
        assert r.n_violations == 0

    def test_missing_operand_is_not_applicable_not_violated(self):
        # P#1/R-4: NULL is designed, not lost — a row missing an operand
        # cannot be asserted as violating the invariant.
        df = pd.DataFrame({"max_dd": [-0.5, None, -1.5]})
        rule = InvariantRule("MAX_DD_RANGE", "-1 <= max_dd <= 0")
        r = check_invariant(df, rule)
        assert r.n_applicable == 2  # the NULL row is excluded, not counted
        assert r.n_violations == 1  # only -1.5 violates

    def test_when_guard_restricts_applicability(self):
        df = pd.DataFrame({
            "value_a": [1.0, 2.0, 3.0],
            "value_b": [1.0, 1.0, 1.0],
            "flag": [1, 0, 1],
        })
        rule = InvariantRule("A_EQUALS_B", "value_a == value_b", when="flag == 1")
        r = check_invariant(df, rule)
        assert r.n_applicable == 2  # only flag==1 rows
        assert r.n_violations == 1  # row 0 satisfies, row 2 (value_a=3) violates


class TestSortinoVsSharpeCorrection:
    """§2.4: sortino>=sharpe is false whenever excess_return<0, even for a
    perfectly correct calculation. excess=-0.10, vol=0.20, downside_dev=0.10
    -> Sharpe=-0.50, Sortino=-1.00, so Sortino<Sharpe with no defect present.
    """

    def _counterexample_df(self):
        return pd.DataFrame({
            "sharpe": [-0.5],
            "sortino": [-1.0],
            "excess_return": [-0.10],
        })

    def test_naive_unconditional_rule_produces_false_positive(self):
        naive_rule = InvariantRule("NAIVE_SORTINO_GE_SHARPE", "sortino >= sharpe")
        r = check_invariant(self._counterexample_df(), naive_rule)
        assert r.n_violations == 1  # demonstrates the defect being corrected

    def test_corrected_sign_conditioned_rule_does_not_flag_it(self):
        corrected_rule = InvariantRule(
            "SORTINO_VS_SHARPE_UP", "sortino >= sharpe - 0.0001", when="excess_return > 0",
        )
        r = check_invariant(self._counterexample_df(), corrected_rule)
        assert r.n_applicable == 0  # excess_return<0 -> rule does not apply here
        assert r.n_violations == 0

    def test_corrected_rule_still_catches_a_real_defect_when_excess_positive(self):
        df = pd.DataFrame({"sharpe": [1.0], "sortino": [0.2], "excess_return": [0.10]})
        corrected_rule = InvariantRule(
            "SORTINO_VS_SHARPE_UP", "sortino >= sharpe - 0.0001", when="excess_return > 0",
        )
        r = check_invariant(df, corrected_rule)
        assert r.n_applicable == 1
        assert r.n_violations == 1


class TestFrozenNavCorrection:
    """§2.4: vol_ann==0 does not by itself imply a frozen NAV — a flat,
    identical nonzero periodic return legitimately produces vol_ann==0. The
    correct invariant ties zero volatility to actual periodic-return variance.
    """

    def _rule(self):
        return InvariantRule(
            "FROZEN_NAV_ZERO_VOL",
            "not (vol_ann == 0 and periodic_return_variance > 0.0001)",
        )

    def test_flat_nonzero_return_is_not_flagged(self):
        df = pd.DataFrame({"vol_ann": [0.0], "periodic_return_variance": [0.0]})
        r = check_invariant(df, self._rule())
        assert r.n_violations == 0

    def test_zero_vol_with_real_variance_is_flagged(self):
        # vol_ann==0 while the underlying periodic returns actually vary —
        # exactly the calculation-defect signature this invariant targets.
        df = pd.DataFrame({"vol_ann": [0.0], "periodic_return_variance": [0.02]})
        r = check_invariant(df, self._rule())
        assert r.n_violations == 1


class TestCheckBounds:
    def test_breach_detected_outside_bounds(self):
        df = pd.DataFrame({"vol_ann": [0.1, 6.0, -0.5]})
        rule = BoundRule(metric="vol_ann", min_value=0.0, max_value=5.0, bound_type="PLAUSIBILITY")
        r = check_bounds(df, "vol_ann", rule)
        assert r.n_breaches == 2

    def test_crisis_carveout_separates_expected_breaches(self):
        df = pd.DataFrame({
            "sharpe": [-15.0, -15.0],
            "horizon": ["crisis_2008", "since_inception"],
        })
        rule = BoundRule(metric="sharpe", min_value=-10.0, max_value=10.0,
                          bound_type="PLAUSIBILITY", crisis_carveout=True)
        r = check_bounds(df, "sharpe", rule, horizon_column="horizon")
        assert r.n_breaches == 1  # only the non-crisis row escalates
        assert r.n_carved_out == 1
