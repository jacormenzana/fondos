# proyecto1/tests/test_statistical_audit_aci_grid.py
# -*- coding: utf-8 -*-
"""FND-0276 / FND-0277: ACI_1Y and ACI_RHP are published with ONE decimal, so ACI_AMORTISATION_ORDER tolerates one published unit and
ACI_MUST_AMORTISE applies only where the entry fee moves the ACI by at least one unit. The rows are the real funds that exposed it
(IE00B52VLZ70: 1.4 vs 1.5; LU0895849734: 2.4 == 2.4 with an entry fee of 0.05 over 5 years)."""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))

from shared.statistical_audit.catalog_invariants import COST_INVARIANTS  # noqa: E402
from shared.statistical_audit.invariants import check_invariant  # noqa: E402
from shared.statistical_audit.tolerances import ACI_PUBLISHED_UNIT_PP  # noqa: E402

RULES = {r.rule_id: r for r in COST_INVARIANTS}


def _violations(rule_id: str, **row) -> int:
    return len(check_invariant(pd.DataFrame([row]), RULES[rule_id]).violations)


def test_order_tolerates_one_published_unit_but_not_two():
    assert ACI_PUBLISHED_UNIT_PP == 0.10
    assert _violations("ACI_AMORTISATION_ORDER", ACI_1Y=1.4, ACI_RHP=1.5) == 0          # IE00B52VLZ70: one grid unit, float 1.4 + 0.1 == 1.5
    assert _violations("ACI_AMORTISATION_ORDER", ACI_1Y=6.5, ACI_RHP=6.6) == 0          # a unit the float grid cannot represent exactly
    assert _violations("ACI_AMORTISATION_ORDER", ACI_1Y=1.4, ACI_RHP=1.6) == 1          # two units: a real inversion
    assert _violations("ACI_AMORTISATION_ORDER", ACI_1Y=2.0, ACI_RHP=1.0) == 0


def test_amortisation_applies_only_when_the_entry_fee_moves_the_aci_by_a_unit():
    base = dict(ACI_1Y=2.4, ACI_RHP=2.4, Cost_RHP_Years=5.0)
    assert _violations("ACI_MUST_AMORTISE", Entry_Fee_Pct=0.05, **base) == 0            # LU0895849734: 0.05 * 0.8 = 0.04 pp, below the grid
    assert _violations("ACI_MUST_AMORTISE", Entry_Fee_Pct=0.125, **base) == 1           # 0.125 * 0.8 = 0.10 pp: visible, so equality is a defect
    assert _violations("ACI_MUST_AMORTISE", Entry_Fee_Pct=1.0, **base) == 1
    assert _violations("ACI_MUST_AMORTISE", Entry_Fee_Pct=1.0, ACI_1Y=2.4, ACI_RHP=2.0, Cost_RHP_Years=5.0) == 0
    assert _violations("ACI_MUST_AMORTISE", Entry_Fee_Pct=1.0, ACI_1Y=2.4, ACI_RHP=2.4, Cost_RHP_Years=1.0) == 0   # RHP of one year: nothing to amortise over
