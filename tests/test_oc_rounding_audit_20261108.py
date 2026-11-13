"""
tests/test_oc_rounding_audit_20261108.py -- OC_NOT_CONTAMINATED does not apply where the publisher's one-decimal ACI rounding explains the equality (owner decision 2026-11-08, FND-0034).

The cost audit was FAIL from 2026-11-06 because one new fund, LU2132880597 (management 0,30 + transaction 0,04 = 0,34, printed "0,3"), violated the rule and no acceptance covered it.
The marker is computed by the runner from shared.cost_rounding (the definition P1 uses to accept the OC); the rule only reads the column. R-7: no DB.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.statistical_audit.catalog_invariants import COST_INVARIANTS  # noqa: E402
from shared.statistical_audit.invariants import check_invariant         # noqa: E402


def _runner():
    spec = importlib.util.spec_from_file_location("rsa_oc_rounding", ROOT / "scripts" / "audit" / "run_statistical_audit.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rsa_oc_rounding"] = mod
    spec.loader.exec_module(mod)
    return mod


def _frame(rows):
    cols = ["ISIN", "Ongoing_Charge_Recurrent", "ACI_RHP", "Transaction_Cost_Pct", "Entry_Fee_Pct_Max", "Exit_Fee_Pct_Max", "Performance_Fee_Pct"]
    return pd.DataFrame(rows, columns=cols)


def _violators(rows):
    master = _frame(rows)
    _runner()._mark_oc_rounding(master)
    master["OC_Is_Management_Row"] = False        # 2026-11-13: set by _mark_oc_is_management_row (its own test, tests/test_oc_management_row_20261113.py)
    rule = next(r for r in COST_INVARIANTS if r.rule_id == "OC_NOT_CONTAMINATED")
    return sorted(check_invariant(master, rule).violations["ISIN"].tolist()) if check_invariant(master, rule).n_violations else []


ROWS = [
    ("NEW_ROUNDING", 0.003, 0.3, 0.04, None, 0.0, None),       # the fund that failed the audit: explained, so not a violation
    ("ZERO_TC", 0.003, 0.3, 0.0, None, None, None),            # ACI = OC for real
    ("TC_NULL", 0.003, 0.3, None, None, None, None),           # no transaction cost on record: cannot be explained, stays a violation
    ("TC_BIG", 0.003, 0.3, 0.06, None, None, None),            # the gap is more than half a unit: the OC was copied from the ACI
    ("ENTRY", 0.003, 0.3, 0.04, 3.0, None, None),              # an entry fee is charged: ACI is not OC + TC
    ("PERF", 0.003, 0.3, 0.04, None, None, 20.0),              # a performance fee is charged
    ("DIFFERENT", 0.003, 0.4, 0.04, None, None, None),         # OC differs from the ACI: valid, never a violation
]


def test_only_the_equalities_that_the_rounding_cannot_explain_remain_violations():
    assert _violators(ROWS) == ["ENTRY", "PERF", "TC_BIG", "TC_NULL"]


def test_the_marker_is_false_everywhere_when_the_columns_it_needs_are_missing():
    master = pd.DataFrame({"ISIN": ["X"], "Ongoing_Charge_Recurrent": [0.003]})
    _runner()._mark_oc_rounding(master)
    assert master["OC_ACI_Rounding_Explained"].tolist() == [False]
    empty = _frame([])
    _runner()._mark_oc_rounding(empty)
    assert "OC_ACI_Rounding_Explained" in empty.columns


def test_the_runner_marks_the_frame_before_the_invariants_run_and_the_rule_reads_that_column():
    src = (ROOT / "scripts" / "audit" / "run_statistical_audit.py").read_text(encoding="utf-8")
    assert src.index("_mark_oc_rounding(master)") < src.index('frames_by_table = {"fund_master": master')
    rule = next(r for r in COST_INVARIANTS if r.rule_id == "OC_NOT_CONTAMINATED")
    assert rule.when == "not OC_ACI_Rounding_Explained and not OC_Is_Management_Row" and rule.bound_type == "HARD_INVARIANT"
