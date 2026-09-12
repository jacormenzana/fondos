# proyecto1/tests/test_statistical_audit_comparisons.py
# -*- coding: utf-8 -*-
"""Tests unitarios de shared/statistical_audit/comparisons.py (compare_pairs —
funcion #8, doc/reglas/AUDITORIA_ESTADISTICA.md §4). Verifica que nunca se
evalua abs(a-b)<eps sola: exige elegibilidad AND tolerancia AND min_matches.

Cumple R-7: sin importar pipeline.py ni core.io.
"""

import os
import sys

import pandas as pd

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from shared.statistical_audit.comparisons import PairRule, compare_pairs


def test_triggers_when_matches_meet_min_matches():
    df = pd.DataFrame({"a": [1.0] * 10, "b": [1.0] * 10})
    rule = PairRule(rule_id="R1", tolerance=0.0001, min_matches=8)
    r = compare_pairs(df, "a", "b", rule)
    assert r.n_matches == 10
    assert r.triggered


def test_not_triggered_below_min_matches():
    df = pd.DataFrame({"a": [1.0] * 5 + [2.0] * 5, "b": [1.0] * 5 + [9.0] * 5})
    rule = PairRule(rule_id="R1", tolerance=0.0001, min_matches=8)
    r = compare_pairs(df, "a", "b", rule)
    assert r.n_matches == 5
    assert not r.triggered


def test_both_present_required():
    df = pd.DataFrame({"a": [None, 1.0], "b": [1.0, None]})
    rule = PairRule(rule_id="R1", tolerance=0.0001, min_matches=1)
    r = compare_pairs(df, "a", "b", rule)
    assert r.n_matches == 0
    assert r.n_eligible == 0


def test_eligibility_predicate_excludes_otherwise_matching_rows():
    # Reproduces the doc's real/nominal example: an eligibility predicate
    # (IPC > 0) must gate the comparison, or a zero-inflation case where
    # nominal legitimately equals real would fire the deflation-defect rule.
    df = pd.DataFrame({
        "return_nominal": [0.05, 0.05],
        "return_real": [0.05, 0.05],
        "ipc_yoy": [0.0, 0.03],
    })
    rule = PairRule(
        rule_id="REAL_EQUALS_NOMINAL", tolerance=0.0001, min_matches=1,
        eligibility=lambda d: d["ipc_yoy"] > 0.001,
    )
    r = compare_pairs(df, "return_nominal", "return_real", rule)
    assert r.n_eligible == 1
    assert r.n_matches == 1  # only the IPC>0 row counts as a real defect signal
