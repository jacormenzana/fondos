"""
tests/test_aci_components_rule_20261113.py -- ACI_RHP_VS_COMPONENTS (package B, FND-0034, owner decision 2026-11-13).

No rule saw a stored ACI_RHP that the KID contradicts: 20 funds stored the 1-year figure or a scenario percentage (6.66 for a printed 1.66, 11.88 for a printed 2.4) and one stored the
management line (1.04 for a printed 1.3). The rule checks ACI_RHP against management + transaction cost (+ entry / exit charge spread over the holding period), half a published unit either
side, where the components are known, no performance fee is charged, the holding period is a year or more and the KID text does not print the stored pair. R-7: no database.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.cost_rounding import ENTRY_CHARGE_SPREAD_MAX_FACTOR, aci_components_band       # noqa: E402
from shared.statistical_audit.catalog_invariants import COST_INVARIANTS                  # noqa: E402
from shared.statistical_audit.invariants import check_invariant                          # noqa: E402
from shared.statistical_audit.tolerances import ACI_COMPONENTS_TOLERANCE_PP, ACI_PUBLISHED_UNIT_PP  # noqa: E402

RULE = next(r for r in COST_INVARIANTS if r.rule_id == "ACI_RHP_VS_COMPONENTS")


def _runner():
    spec = importlib.util.spec_from_file_location("rsa_aci_components", ROOT / "scripts" / "audit" / "run_statistical_audit.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rsa_aci_components"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    """Stands in for the psycopg connection: the only query is the KID text lookup."""
    def __init__(self, texts):
        self._texts = texts

    def execute(self, sql, params):
        return _Rows([(i, self._texts.get(i)) for i in params[0]])


COLS = ["ISIN", "ACI_1Y", "ACI_RHP", "Management_Fee_Pct", "Transaction_Cost_Pct", "Entry_Fee_Pct", "Exit_Fee_Pct", "Entry_Fee_Pct_Max", "Exit_Fee_Pct_Max", "Performance_Fee_Pct", "Cost_RHP_Years"]


def _violators(rows, texts=None):
    master = pd.DataFrame(rows, columns=COLS)
    _runner()._mark_aci_components(_Conn(texts or {}), master)
    result = check_invariant(master, RULE)
    return sorted(result.violations["ISIN"].tolist()) if result.n_violations else []


def test_the_tolerance_is_half_a_published_unit_with_the_tie_inside():
    assert ACI_PUBLISHED_UNIT_PP == 0.10
    assert 0.05 < ACI_COMPONENTS_TOLERANCE_PP < 0.0501
    assert ENTRY_CHARGE_SPREAD_MAX_FACTOR >= 1.5                  # measured: p95 of the compounding over the straight-line spread is 1.56


def test_the_band_is_management_plus_transaction_cost_up_to_the_charges_spread_over_the_holding_period():
    low, high = aci_components_band(1.65, 0.01, None, 5.0, 0.0, None, None, 5.0)        # IE00BD8DY654: entry charge up to 5 %, RHP 5 years
    assert round(low, 2) == 1.66 and round(high, 2) == round(1.66 + ENTRY_CHARGE_SPREAD_MAX_FACTOR * 1.0, 2)
    assert aci_components_band(1.04, 0.27, None, None, 0.0, None, None, 2.0) == (1.31, 1.31)     # LU2842969151: no charge stored
    assert aci_components_band(1.0, 0.1, 0.05, None, None, None, None, 5.0)[1] > 1.1 + 0.9        # a RATIO entry fee: 0.05 is 5 %, not 0.05 %
    assert aci_components_band(None, 0.1, 0, 0, 0, 0, None, 5.0) is None                          # management unknown
    assert aci_components_band(1.0, None, 0, 0, 0, 0, None, 5.0) is None                           # transaction cost unknown
    assert aci_components_band(1.0, 0.1, 0, 0, 0, 0, 0.2, 5.0) is None                            # a performance fee is charged and its amount is not stored
    assert aci_components_band(1.0, 0.1, 0, 0, 0, 0, 0.0, 5.0) == (1.1, 1.1)                      # a stored 0 is "no performance fee"
    assert aci_components_band(1.0, 0.1, 0, 0, 0, 0, None, 0.5) is None                           # under one year the KID prints the cost of the PERIOD


def test_the_rule_flags_a_stored_value_outside_the_band_and_only_that():
    rows = [
        ("IE_COPY", 6.66, 6.66, 1.65, 0.01, None, 0.0, 5.0, None, None, 5.0),      # the 1-year figure copied into ACI_RHP: outside the band
        ("LU_MGMT", 1.3, 1.04, 1.04, 0.27, None, 0.0, None, None, None, 2.0),      # LU2842969151: management only
        ("OK_EXACT", 1.4, 1.4, 1.1, 0.3, None, 0.0, None, None, None, 3.0),
        ("OK_TIE", 1.3, 1.3, 1.0, 0.25, None, 0.0, None, None, None, 3.0),         # exactly half a unit: 1.25 prints 1.3
        ("OK_ENTRY", 2.4, 2.4, 1.0, 0.3, 0.05, 0.0, 5.0, None, None, 5.0),          # an entry charge of 5 % may sit in the ACI (up to 1.75 pp over 5 years)
        ("SUB_YEAR", 0.2, 0.2, 1.8, 0.1, None, 0.0, None, None, None, 0.25),       # the period cost: out of scope
        ("PERF_FEE", 5.0, 5.0, 1.0, 0.1, None, 0.0, None, None, 0.2, 5.0),         # a performance fee of unknown amount: out of scope
        ("NO_MGMT", 5.0, 5.0, None, 0.1, None, 0.0, None, None, None, 5.0),        # components unknown: out of scope
    ]
    assert _violators(rows) == ["IE_COPY", "LU_MGMT"]


def test_a_stored_pair_the_kid_prints_is_not_flagged_even_if_the_components_disagree():
    rows = [("PRINTED", 2.2, 2.4, 0.9, 0.1, None, 0.0, None, None, None, 5.0),     # a publisher whose own figures disagree: the document is the authority
            ("UNPRINTED", 2.2, 2.4, 0.9, 0.1, None, 0.0, None, None, None, 5.0)]
    texts = {"PRINTED": "Annual costs impact (*)\n2.2% 2.4% each year\nComposition of costs", "UNPRINTED": "Annual costs impact (*)\n1.2% 1.3% each year"}
    assert _violators(rows, texts) == ["UNPRINTED"]
