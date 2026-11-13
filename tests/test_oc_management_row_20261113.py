"""
tests/test_oc_management_row_20261113.py -- an ongoing charge that IS the printed management row is not "the ACI copied into the OC" (package B, FND-0034, owner order 2026-11-13).

The PRIIPs ongoing charge is the management row (proyecto1/core/cost_scale.py). 33 of the 35 funds that OC_NOT_CONTAMINATED and P1's own warning counted store an OC equal to the management row
the KID prints with its normative wording; the ACI equals it because the publisher leaves the transaction cost out. Only an OC that is NOT the management row (FR001400I0X7, IE00BF1T6L34: the
management figure was never extracted) is the contamination of FND-0034. One predicate (cost_scale.contaminated_by_aci) serves the resolver and the pipeline warning; the audit reads the
same definition plus independent text evidence. R-7: no database.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "proyecto1" / "core"):
    sys.path.insert(0, str(p))

from cost_scale import contaminated_by_aci, resolve_ongoing_charge                      # noqa: E402
from shared.statistical_audit.catalog_invariants import COST_INVARIANTS                # noqa: E402
from shared.statistical_audit.invariants import check_invariant                        # noqa: E402
from shared.statistical_audit.text_evidence import management_row_printed              # noqa: E402

RULE = next(r for r in COST_INVARIANTS if r.rule_id == "OC_NOT_CONTAMINATED")


def _runner():
    spec = importlib.util.spec_from_file_location("rsa_oc_management_row", ROOT / "scripts" / "audit" / "run_statistical_audit.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rsa_oc_management_row"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, texts):
        self._texts = texts

    def execute(self, sql, params):
        return _Rows([(i, self._texts.get(i)) for i in params[0]])


def test_the_resolver_stays_strict_with_a_management_figure_equal_to_the_aci():
    """Owner decision 2026-11-08 (FND-0034): without the transaction cost an OC equal to the ACI is not accepted by the P1 writer, because the extracted management figure could itself be a copy.
    The management row is trusted only with independent TEXT evidence, which the audit has (OC_Is_Management_Row) and the writer does not."""
    assert resolve_ongoing_charge("PRIIPS_KID", 1.62, None, None, 1.62, 1.62) == (None, "none")
    assert resolve_ongoing_charge("PRIIPS_KID", 2.0, None, 0.025, 2.5, 4.3, 0.53) == (0.02, "management_fee")      # a clean management figure is written
    assert contaminated_by_aci(0.025, 2.5, 4.3, 0.53) is True                                                       # FR001400I0X7: the OC is the ACI


def test_the_management_row_evidence_is_the_value_followed_by_the_composition_wording():
    assert management_row_printed("Comisiones de gestion y otros\n1,62% del valor de su inversión por año.\n", 1.62) is True
    assert management_row_printed("Management fees\n1.62% of the value of your investment per year", 1.62) is True
    assert management_row_printed("Incidencia anual de los costes\n1,62%\n", 1.62) is False            # the ACI row has no composition wording
    assert management_row_printed("11,62% del valor de su inversión", 1.62) is False                   # not a longer number
    assert management_row_printed(None, 1.62) is False and management_row_printed("x", None) is False


def test_the_audit_exempts_only_a_management_row_the_document_prints():
    cols = ["ISIN", "Ongoing_Charge_Recurrent", "ACI_RHP", "Management_Fee_Pct", "Transaction_Cost_Pct", "Entry_Fee_Pct_Max", "Exit_Fee_Pct_Max", "Performance_Fee_Pct"]
    master = pd.DataFrame([("PRINTED", 0.0162, 1.62, 1.62, 0.07, None, 0.0, None),       # the OC is the printed management row
                           ("COPY", 0.025, 2.5, None, 0.53, None, 0.0, None),            # management missing: the ACI was copied into the OC
                           ("NOT_PRINTED", 0.0162, 1.62, 1.62, 0.07, None, 0.0, None),   # equal to a management figure the text does not print: no evidence
                           ("DIFFERENT", 0.016, 2.5, 1.6, 0.07, None, 0.0, None)], columns=cols)
    texts = {"PRINTED": "Comisiones de gestion\n1,62% del valor de su inversión por año.", "NOT_PRINTED": "Incidencia anual de los costes 1,62%"}
    runner = _runner()
    runner._mark_oc_rounding(master)
    runner._mark_oc_is_management_row(_Conn(texts), master)
    assert master.set_index("ISIN")["OC_Is_Management_Row"].to_dict() == {"PRINTED": True, "COPY": False, "NOT_PRINTED": False, "DIFFERENT": False}
    result = check_invariant(master, RULE)
    assert sorted(result.violations["ISIN"].tolist()) == ["COPY", "NOT_PRINTED"]
