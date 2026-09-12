# proyecto1/tests/test_statistical_audit_metric_bounds.py
# -*- coding: utf-8 -*-
"""Tests unitarios de shared/statistical_audit/catalog_metric_bounds.py —
cierra la cobertura de Block 7 (P2) sin duplicar los invariantes ya cubiertos
en catalog_invariants.py (doc/reglas/AUDITORIA_ESTADISTICA.md §5).

Cumple R-7: sin importar pipeline.py ni core.io.
"""

import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from shared.statistical_audit.catalog_metric_bounds import get_metric_bound


def test_vol_ann_explicit_bound():
    b = get_metric_bound("vol_ann")
    assert b.min_value == 0.0 and b.max_value == 5.0
    assert b.bound_type == "PLAUSIBILITY"


def test_bounded_unit_metric_gets_generic_zero_one_bound():
    b = get_metric_bound("momentum_rank")
    assert (b.min_value, b.max_value) == (0.0, 1.0)


def test_macro_r2_excluded_to_avoid_duplicating_hard_invariant():
    assert get_metric_bound("macro_r2") is None


def test_continuous_signed_metric_without_explicit_entry_has_no_bound():
    assert get_metric_bound("beta_rate_eu") is None


def test_bound_carries_the_correct_metric_name_not_the_template_wildcard():
    b = get_metric_bound("alpha_persistence")
    assert b.metric == "alpha_persistence"
