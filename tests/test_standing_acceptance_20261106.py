"""
tests/test_standing_acceptance_20261106.py -- owner decisions of 2026-11-06 on the cost audit.

1. PUBLISHER_DEFECT / EXTRACTION_LIMIT residuals are STANDING acceptances: no 90-day clock, bound to the fingerprint of the stored KID text and re-validated on every read.
2. ACI_AMORTISATION_ORDER (FND-0276) tolerates a rising pair only when the stored KID text itself prints it (independent text evidence).
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.statistical_audit import accepted_residuals as ar          # noqa: E402
from shared.statistical_audit.catalog_invariants import COST_INVARIANTS  # noqa: E402
from shared.statistical_audit.invariants import check_invariant         # noqa: E402
from shared.statistical_audit.text_evidence import aci_pair_printed     # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---- independent text evidence -------------------------------------------------------------------------------------------------------------

def test_a_printed_rising_pair_is_vouched_in_the_formats_kids_use():
    assert aci_pair_printed("Annual Cost Impact (*)\n0.8 % 0.9 % each year\n(*) This illustrates", 0.8, 0.9)
    assert aci_pair_printed("Incidencia anual de los costes (*)\n1,4% 1,5% cada año", 1.4, 1.5)
    assert aci_pair_printed("Impact on return per year\n4.10% 4.27%", 4.1, 4.27)                    # trailing zero / two decimals
    assert aci_pair_printed("Annual cost impact 2% 3,0% per year", 2.0, 3.0)                      # integer-valued percentages


def test_the_pair_must_be_printed_in_that_order_and_with_the_annual_impact_wording():
    assert not aci_pair_printed("Annual Cost Impact\n0.8 % 0.9 % each year", 0.9, 0.8)             # swapped
    assert not aci_pair_printed("Annual Cost Impact\n0.8 % 0.9 % each year", 0.8, 1.0)             # the second value is not printed
    assert not aci_pair_printed("Annual Cost Impact\n0.8 % 0.9 % each year", 8.0, 9.0)             # 8.0 is not 0.8: no partial matches of other numbers
    assert not aci_pair_printed("random numbers 0.8 % 0.9 % in a sentence about nothing" + " " * 500, 0.8, 0.9)   # no wording within the windows
    assert not aci_pair_printed(None, 0.8, 0.9) and not aci_pair_printed("", 0.8, 0.9) and not aci_pair_printed("0.8 % 0.9 % each year", None, 0.9)


# ---- the rule ------------------------------------------------------------------------------------------------------------------------------

def test_the_amortisation_rule_tolerates_a_printed_rise_and_keeps_alarming_an_unprinted_one():
    rule = next(r for r in COST_INVARIANTS if r.rule_id == "ACI_AMORTISATION_ORDER")
    df = pd.DataFrame({"ISIN": ["A", "B", "C"], "ACI_1Y": [0.8, 0.8, 3.0], "ACI_RHP": [1.5, 1.5, 2.5], "ACI_Rise_Printed": [True, False, False]})
    res = check_invariant(df, rule)
    assert sorted(res.violations["ISIN"]) == ["B"]                     # A: rise printed by the document -> tolerated; B: rise NOT printed -> ALARM; C: falls -> fine
    assert res.n_applicable == 2                                       # A is not applicable at all


def test_the_runner_marks_only_the_rising_funds_the_text_vouches_for():
    runner = _load("run_statistical_audit_t", "scripts/audit/stat/run_statistical_audit.py")

    class Conn:
        def execute(self, sql, params=None):
            assert "raw_kiid_text" in sql and sorted(params[0]) == ["A", "B"]          # ONLY the rising funds are looked up
            texts = [("A", "Annual Cost Impact (*)\n0.8 % 1.5 % each year"), ("B", "Annual Cost Impact (*)\n1.1 % 1.2 % each year")]
            class Cur:
                def fetchall(self_inner):
                    return texts
            return Cur()
    master = pd.DataFrame({"ISIN": ["A", "B", "C", "D"], "ACI_1Y": [0.8, 0.8, 3.0, None], "ACI_RHP": [1.5, 1.5, 2.5, 1.0]})
    runner._mark_printed_rises(Conn(), master)
    assert master.set_index("ISIN")["ACI_Rise_Printed"].to_dict() == {"A": True, "B": False, "C": False, "D": False}


# ---- standing acceptance ---------------------------------------------------------------------------------------------------------------------

def test_the_standing_marker_roundtrips_and_the_classes_are_the_tools():
    marker = ar.standing_marker("PUBLISHER_DEFECT", {"B": "bbbb", "A": "aaaa"})
    assert marker == "[STANDING:PUBLISHER_DEFECT|A=aaaa;B=bbbb]"
    assert ar.parse_standing("PUBLISHER_DEFECT: the KID contradicts itself " + marker) == ("PUBLISHER_DEFECT", {"A": "aaaa", "B": "bbbb"})
    assert ar.parse_standing("an ordinary 90-day reason") is None and ar.parse_standing(None) is None
    with pytest.raises(ValueError):
        ar.standing_marker("WHATEVER", {})
    tool = _load("accept_audit_residual_t", "scripts/audit/stat/accept_audit_residual.py")
    assert tool.STANDING_CLASSES == ar.STANDING_CLASSES


def test_a_standing_row_is_valid_only_while_its_document_is_the_one_decided_on():
    rows = [("MGMT_LE_TOTAL", "A", "FND-0280", "PUBLISHER_DEFECT: x " + ar.standing_marker("PUBLISHER_DEFECT", {"A": "aaaa", "B": "bbbb"})),
            ("MGMT_LE_TOTAL", "B", "FND-0280", "PUBLISHER_DEFECT: x " + ar.standing_marker("PUBLISHER_DEFECT", {"A": "aaaa", "B": "bbbb"})),
            ("TXN_LE_TOTAL", "C", "FND-0285", "an ordinary reason")]
    valid, lapsed = ar.standing_status(rows, {"A": "aaaa", "B": "CHANGED"})
    assert valid == {("MGMT_LE_TOTAL", "A")} and lapsed == [("MGMT_LE_TOTAL", "B", "FND-0280")]
    assert ar.standing_status(rows, {})[0] == set()                    # the stored KID is gone: not valid either


def test_the_report_says_standing_instead_of_a_far_away_date():
    f = [{"rule_id": "MGMT_LE_TOTAL", "violating_isins": ("A",), "rule_class": "HARD_INVARIANT", "evidence": "1 violates"}]
    out, notes = ar.apply_accepted(f, {"MGMT_LE_TOTAL": {"A": ("FND-0280", ar.STANDING_UNTIL)}})
    assert "standing, evidence-validated" in out[0]["evidence"] and "standing, evidence-validated" in notes[0] and "2099" not in notes[0]
    out2, notes2 = ar.apply_accepted(f, {"MGMT_LE_TOTAL": {"A": ("FND-0280", date(2027, 1, 15))}})
    assert "until 2027-01-15" in notes2[0]


def test_a_standing_class_needs_the_costs_domain():
    tool = _load("accept_audit_residual_t2", "scripts/audit/stat/accept_audit_residual.py")
    args = tool._parser().parse_args(["--domain", "p2", "--rule", "R", "--ap", "FND-0280", "--reason", "x" * 30, "--class", "PUBLISHER_DEFECT"])
    mode, problems = tool.mode_of(args)
    assert mode == "grant" and any("--class needs --domain costs" in p for p in problems)
    args = tool._parser().parse_args(["--domain", "costs", "--rule", "R", "--ap", "FND-0280", "--reason", "x" * 30, "--class", "EXTRACTION_LIMIT"])
    assert tool.mode_of(args) == ("grant", [])


@pytest.fixture()
def standing_db(pg_conn):
    """Toy audit_accepted_finding + fund_kiid_metadata as TEMP tables (rolled back with the test savepoint)."""
    pg_conn.execute("CREATE TEMP TABLE audit_accepted_finding (domain text, rule_id text, isin varchar(12), ap_id text, reason text, accepted_at date DEFAULT current_date, "
                    "accepted_by text, expires_at date)")
    pg_conn.execute("CREATE TEMP TABLE fund_kiid_metadata (isin varchar(12), raw_kiid_text text)")
    pg_conn.execute("INSERT INTO fund_kiid_metadata VALUES ('AA0000000001', 'the KID text v1'), ('AA0000000002', 'another KID')")
    return pg_conn


def _grant(conn, isin, reason, expires):
    conn.execute("INSERT INTO audit_accepted_finding (domain, rule_id, isin, ap_id, reason, expires_at) VALUES ('cost_attributes','MGMT_LE_TOTAL',%s,'FND-0280',%s,%s)",
                 (isin, reason, expires))


def test_load_accepted_keeps_a_standing_acceptance_until_its_kid_changes(standing_db):
    conn = standing_db
    fps = ar.current_fingerprints(conn, ["AA0000000001", "AA0000000002"])
    assert set(fps) == {"AA0000000001", "AA0000000002"} and all(len(v) == 12 for v in fps.values())
    reason = "PUBLISHER_DEFECT: x " + ar.standing_marker("PUBLISHER_DEFECT", {"AA0000000001": fps["AA0000000001"]})
    _grant(conn, "AA0000000001", reason, ar.STANDING_UNTIL)
    _grant(conn, "AA0000000002", "ordinary 90-day reason", date(2027, 2, 1))
    got = ar.load_accepted(conn, "cost_attributes", date(2026, 11, 6))
    assert set(got["MGMT_LE_TOTAL"]) == {"AA0000000001", "AA0000000002"}
    # years later, same KID: still accepted, no renewal
    assert "AA0000000001" in ar.load_accepted(conn, "cost_attributes", date(2031, 1, 1))["MGMT_LE_TOTAL"]
    # the publisher issues a new KID: the standing acceptance lapses by itself, the ordinary one is untouched
    conn.execute("UPDATE fund_kiid_metadata SET raw_kiid_text = 'the KID text v2' WHERE isin = 'AA0000000001'")
    got = ar.load_accepted(conn, "cost_attributes", date(2026, 11, 7))
    assert set(got["MGMT_LE_TOTAL"]) == {"AA0000000002"}
    lines = ar.attention_lines(conn, "cost_attributes", 30, date(2026, 11, 7))
    assert any("AA0000000001" in ln and "CADUCADA" in ln for ln in lines)


def test_a_standing_acceptance_without_a_stored_kid_is_not_valid(standing_db):
    conn = standing_db
    _grant(conn, "AA0000000009", "PUBLISHER_DEFECT: x " + ar.standing_marker("PUBLISHER_DEFECT", {"AA0000000009": "0123456789ab"}), ar.STANDING_UNTIL)
    assert ar.load_accepted(conn, "cost_attributes", date(2026, 11, 6)) == {}
