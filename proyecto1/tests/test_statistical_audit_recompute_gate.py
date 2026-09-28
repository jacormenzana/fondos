# proyecto1/tests/test_statistical_audit_recompute_gate.py
# -*- coding: utf-8 -*-
"""Tests unitarios de shared/statistical_audit/recompute_gate.py
(capture_state / assert_recompute_happened -- funcion #14, doc/reglas/AUDITORIA_ESTADISTICA.md
§4). P2 Method Control #3 ("verify fund_metric_state.input_hash actually changed before trusting
an A/B recompute result") como comprobacion mecanica, no solo disciplina manual (B6/FND-0128,
2026-09-28).

capture_state() necesita una conexion viva a Postgres; solo se cubre aqui la parte pura
(assert_recompute_happened), que es la logica de decision real. La integracion end-to-end de
capture_state se verifica en vivo (scripts/audit/run_statistical_audit.py --state-snapshot /
--verify-recompute), no aqui.

Cumple R-7: sin importar pipeline.py ni core.io.
"""

import os
import sys

import pandas as pd

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from shared.statistical_audit.recompute_gate import assert_recompute_happened


def _snapshot(rows):
    return pd.DataFrame(rows, columns=["isin", "metric_version", "input_hash", "calculated_at"])


def test_recompute_verified_when_hash_changed():
    before = _snapshot([["LU1", "v1", "hash-old", "2026-09-01"]])
    after = _snapshot([["LU1", "v1", "hash-new", "2026-09-28"]])
    result = assert_recompute_happened(before, after)
    assert result.unchanged.empty
    assert result.missing_after.empty
    assert result.n_checked == 1


def test_recompute_void_when_hash_unchanged():
    # The idempotency cache bypassed processing -- the "after" numbers are stale.
    before = _snapshot([["LU1", "v1", "hash-same", "2026-09-01"]])
    after = _snapshot([["LU1", "v1", "hash-same", "2026-09-01"]])
    result = assert_recompute_happened(before, after)
    assert len(result.unchanged) == 1
    assert result.unchanged.iloc[0]["isin"] == "LU1"
    assert result.missing_after.empty


def test_recompute_void_when_hash_same_but_calculated_at_changed_is_still_recomputed():
    # calculated_at alone changing (hash identical) still counts as recomputed -- the "OR" in the
    # module docstring ("hash OR calculated_at changed").
    before = _snapshot([["LU1", "v1", "hash-same", "2026-09-01"]])
    after = _snapshot([["LU1", "v1", "hash-same", "2026-09-28"]])
    result = assert_recompute_happened(before, after)
    assert result.unchanged.empty


def test_recompute_flags_missing_after_row():
    # A row present before the fix but absent afterward (e.g. ISIN dropped from the universe, or
    # metric_version retired) -- void the A/B result for it just as surely as an unchanged hash.
    before = _snapshot([["LU1", "v1", "hash-old", "2026-09-01"]])
    after = _snapshot([])
    result = assert_recompute_happened(before, after)
    assert result.unchanged.empty
    assert len(result.missing_after) == 1
    assert result.missing_after.iloc[0]["isin"] == "LU1"


def test_recompute_mixed_batch_reports_each_independently():
    before = _snapshot([
        ["LU1", "v1", "hash-a", "2026-09-01"],
        ["LU2", "v1", "hash-b", "2026-09-01"],
        ["LU3", "v1", "hash-c", "2026-09-01"],
    ])
    after = _snapshot([
        ["LU1", "v1", "hash-a-new", "2026-09-28"],  # recomputed
        ["LU2", "v1", "hash-b", "2026-09-01"],       # void: unchanged
        # LU3 missing entirely: void
    ])
    result = assert_recompute_happened(before, after)
    assert set(result.unchanged["isin"]) == {"LU2"}
    assert set(result.missing_after["isin"]) == {"LU3"}
    assert result.n_checked == 3


def test_recompute_empty_before_snapshot():
    result = assert_recompute_happened(_snapshot([]), _snapshot([]))
    assert result.unchanged.empty
    assert result.missing_after.empty
    assert result.n_checked == 0
