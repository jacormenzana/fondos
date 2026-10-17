# proyecto1/tests/test_statistical_audit_incidents.py
# -*- coding: utf-8 -*-
"""FND-0264 (GOV-05): the closed-loop remediation core (shared/statistical_audit/incidents.py), the CLI helpers and the closure gate of
scripts/ops/close_release.py. Pure (R-7); the writers are in test_statistical_audit_incidents_pg.py."""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import shared.config as cfg  # noqa: E402
from shared.statistical_audit import incidents as inc  # noqa: E402
from shared.statistical_audit.incidents import Reaudit, certify, closable, finding_key, incidents_to_open  # noqa: E402

P2, COSTS = "p2_metrics", "cost_attributes"
T0 = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
KEY = finding_key(COSTS, "MGMT_LE_TOTAL", "MGMT_LE_TOTAL")


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, *rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def finding(rule="MGMT_LE_TOTAL", rule_class="HARD_INVARIANT", block="BLOCK5", group=None, evidence="73/2836 rows violate", isin=None):
    return {"rule_id": rule, "rule_class": rule_class, "block": block, "group_key": group or rule, "evidence": evidence, "isin": isin}


def incident(**over):
    base = {"incident_id": 7, "finding_key": KEY, "domain": COSTS, "rule_id": "MGMT_LE_TOTAL", "group_key": "MGMT_LE_TOTAL", "state": "OPEN",
            "ap_id": "FND-0900", "fix_kind": "CODE", "isins": ["LU1", "LU2"], "opened_run_id": "run_a", "opened_at": T0,
            "certified_run_id": None}
    base.update(over)
    return base


def reaudit(**over):
    base = dict(run_id="run_b", domain=COSTS, scope="FULL", status="PASS_WITH_WARNINGS", finished_at=T0 + timedelta(days=1), violators={})
    base.update(over)
    return Reaudit(**base)


# --- what opens an incident (D7) ---------------------------------------------------------------------------------------------------------
def test_only_critical_findings_open_incidents_and_an_accepted_one_does_not():
    findings = [finding(), finding("PATHOLOGICAL_SHAPE", "STATISTICAL_ANOMALY", "BLOCK3"),
                finding("OC_NOT_CONTAMINATED", evidence="ACCEPTED RESIDUAL (FND-0034, until 2027-01-04): 88/88 ISINs")]
    out = incidents_to_open(COSTS, "run_a", findings, {KEY: {"LU2", "LU1"}}, live_keys=[])
    assert [c.rule_id for c in out] == ["MGMT_LE_TOTAL"] and out[0].isins == ("LU1", "LU2") and out[0].key == KEY


def test_a_cost_coincidence_pair_never_opens_an_incident_but_a_p2_identity_does():
    pair = finding("Entry_Fee_Pct_Max__EQUALS__ACI_RHP", "STATISTICAL_ANOMALY", "BLOCK2")
    assert incidents_to_open(COSTS, "r", [pair], {}, []) == []
    assert [c.rule_id for c in incidents_to_open(P2, "r", [pair], {}, [])] == ["Entry_Fee_Pct_Max__EQUALS__ACI_RHP"]


def test_a_live_incident_is_never_opened_twice_and_a_key_is_listed_once():
    assert incidents_to_open(COSTS, "r", [finding()], {}, live_keys=[KEY]) == []
    assert len(incidents_to_open(COSTS, "r", [finding(), finding()], {}, [])) == 1


def test_policy_recurrence_opens_only_a_key_seen_in_two_runs_and_first_opens_at_once():
    assert len(incidents_to_open(COSTS, "r", [finding()], {}, [], previous_keys=[], policy=inc.POLICY_FIRST)) == 1
    assert incidents_to_open(COSTS, "r", [finding()], {}, [], previous_keys=[], policy=inc.POLICY_RECURRENCE) == []
    assert len(incidents_to_open(COSTS, "r", [finding()], {}, [], previous_keys=[KEY], policy=inc.POLICY_RECURRENCE)) == 1
    with pytest.raises(ValueError):
        incidents_to_open(COSTS, "r", [finding()], {}, [], policy="whenever")


def test_a_finding_with_its_own_isin_and_no_detail_row_still_names_it():
    (c,) = incidents_to_open(P2, "r", [finding("BOUND_x", "HARD_INVARIANT", "BLOCK7", isin="LU9")], {}, [])
    assert c.isins == ("LU9",)


# --- certification: re-audit evidence, never a claim (the plan's four cases) ------------------------------------------------------------------
def test_fixed_closes_a_complete_later_reaudit_with_no_violator_certifies():
    assert certify(incident(), reaudit()).ok
    assert certify(incident(), reaudit(violators={KEY: frozenset({"LU99"})})).ok          # others may still violate: not the affected set


def test_still_violating_stays_open():
    out = certify(incident(), reaudit(violators={KEY: frozenset({"LU2", "LU5"})}))
    assert not out.ok and "still violate" in " ".join(out.reasons)


def test_an_incomplete_reaudit_cannot_certify():
    out = certify(incident(), reaudit(status="INCOMPLETE"))
    assert not out.ok and "INCOMPLETE" in " ".join(out.reasons)


def test_a_recompute_fix_needs_proof_that_the_recompute_ran():
    inc_recompute = incident(domain=P2, finding_key=finding_key(P2, "WINDOW_FISHER_IDENTITY", "WINDOW_FISHER_IDENTITY"), fix_kind="RECOMPUTE")
    ra = reaudit(domain=P2)
    assert not certify(inc_recompute, ra).ok and "recompute" in " ".join(certify(inc_recompute, ra).reasons)           # not provided
    assert not certify(inc_recompute, ra, recompute_ok=False).ok and "unchanged" in " ".join(certify(inc_recompute, ra, False).reasons)
    assert certify(inc_recompute, ra, recompute_ok=True).ok


def test_a_recompute_alone_never_certifies_even_with_a_clean_hash_check_when_the_reaudit_is_missing_or_bad():
    # exit criterion: no CRITICAL defect is closed by a recompute alone
    inc_recompute = incident(fix_kind="RECOMPUTE")
    assert not certify(inc_recompute, reaudit(status="INCOMPLETE"), recompute_ok=True).ok
    assert not certify(inc_recompute, reaudit(violators={KEY: frozenset({"LU1"})}), recompute_ok=True).ok
    assert not certify(inc_recompute, reaudit(run_id="run_a"), recompute_ok=True).ok                  # the run that opened it is not a fix


@pytest.mark.parametrize("over,fragment", [
    (dict(domain=P2), "re-audit is of"),
    (dict(finished_at=T0 - timedelta(hours=1)), "before the incident was opened"),
])
def test_a_reaudit_must_be_of_the_same_domain_and_later(over, fragment):
    out = certify(incident(), reaudit(**over))
    assert not out.ok and fragment in " ".join(out.reasons)


def test_a_sample_reaudit_certifies_only_if_it_covers_every_affected_isin():
    ok = certify(incident(), reaudit(scope="SAMPLE", scope_isins=frozenset({"LU1", "LU2", "LU3"})))
    short = certify(incident(), reaudit(scope="SAMPLE", scope_isins=frozenset({"LU1"})))
    unknown = certify(incident(), reaudit(scope="SAMPLE"))
    assert ok.ok and not short.ok and not unknown.ok and "1 not covered" in " ".join(short.reasons)


def test_only_an_open_incident_certifies_and_an_accept_incident_is_not_certified():
    assert not certify(incident(state="CERTIFIED"), reaudit()).ok
    assert not certify(incident(fix_kind="ACCEPT"), reaudit()).ok


def test_acceptance_needs_an_active_residual_for_every_affected_isin():
    assert inc.acceptance(incident(), ["LU1", "LU2", "LU9"]).ok
    out = inc.acceptance(incident(), ["LU1"])
    assert not out.ok and "1 affected ISIN" in " ".join(out.reasons)
    assert not inc.acceptance(incident(isins=[]), []).ok
    assert not inc.acceptance(incident(state="CERTIFIED"), ["LU1", "LU2"]).ok


# --- the closure gate ------------------------------------------------------------------------------------------------------------------------
def test_a_ticket_with_no_linked_incident_is_closable_and_an_open_one_blocks():
    assert closable("FND-0900", []).ok and closable("FND-0900", [incident(ap_id="FND-1111")]).ok
    out = closable("FND-0900", [incident()])
    assert not out.ok and "--certify" in out.reasons[0] and "#7" in out.reasons[0]


def test_certified_accepted_and_closed_incidents_let_the_ticket_close():
    assert closable("FND-0900", [incident(state="CERTIFIED", certified_run_id="run_b")]).ok
    assert closable("FND-0900", [incident(state="ACCEPTED")]).ok and closable("FND-0900", [incident(state="CLOSED")]).ok


def test_a_certified_incident_without_its_certifying_run_does_not_count():
    assert not closable("FND-0900", [incident(state="CERTIFIED", certified_run_id=None)]).ok


# --- scripts/ops/close_release.py ------------------------------------------------------------------------------------------------------------
def _close():
    return _load("close_release_t", ("scripts", "ops", "close_release.py"))


def test_close_release_validates_its_arguments():
    cr = _close()
    assert cr.validate_args("R2026.10-GOV-05", ["FND-0264"], "x" * 50) == []
    assert len(cr.validate_args("v1", [], "short")) == 3
    assert any("distinct" in p for p in cr.validate_args("R2026.10-GOV-05", ["FND-0264", "FND-0264"], "x" * 50))


def test_close_release_refuses_missing_closed_and_already_released_tickets():
    cr = _close()
    rows = {"FND-1": ("IN_PROGRESS", None), "FND-2": ("CLOSED", None), "FND-3": ("READY_FOR_DEPLOY", "R2026.10-OLD")}
    why = " | ".join(cr.check_tickets(rows, ["FND-1", "FND-2", "FND-3", "FND-4"]))
    assert "FND-2 is CLOSED" in why and "FND-3 already carries release R2026.10-OLD" in why and "FND-4 does not exist" in why
    assert cr.check_tickets(rows, ["FND-1"]) == []


def test_close_release_incident_gate_refuses_an_uncertified_incident_and_fails_closed_without_the_store():
    cr = _close()
    assert cr.incident_gate(["FND-0900"], [incident()], True, False)[0].startswith("FND-0900:")
    assert cr.incident_gate(["FND-0900"], [incident(state="CERTIFIED", certified_run_id="run_b")], True, True) == []
    assert cr.incident_gate(["FND-0900"], [], False, False) == []                          # no store, loop off: nothing links the ticket
    assert "fail closed" in cr.incident_gate(["FND-0900"], [], False, True)[0]              # no store, loop on: refuse


# --- scripts/audit/audit_incident.py helpers ----------------------------------------------------------------------------------------------------
def _cli():
    return _load("audit_incident_t", ("scripts", "audit", "audit_incident.py"))


def test_build_reaudit_keeps_only_critical_violators_and_reads_the_sample_scope_from_the_manifest():
    cli = _cli()
    findings = [finding(), finding("PATHOLOGICAL_SHAPE", "STATISTICAL_ANOMALY", "BLOCK3")]
    header = ("run_b", T0, "SAMPLE", "FAIL", {"scope_isins": ["LU1", "LU2"]})
    ra = cli.build_reaudit(COSTS, header, findings, {KEY: {"LU2"}})
    assert ra.violators == {KEY: frozenset({"LU2"})} and ra.scope_isins == frozenset({"LU1", "LU2"}) and ra.scope == "SAMPLE"
    full = cli.build_reaudit(COSTS, ("run_c", T0, "FULL", "PASS", None), [], {})
    assert full.violators == {} and full.scope_isins is None


def test_the_plan_runs_without_the_incident_table_and_counts_every_key_as_new():
    cli = _cli()
    findings = [finding(), finding("ACI_MUST_AMORTISE")]

    class Conn:
        def execute(self, sql, params=()):
            s = " ".join(sql.split())
            rows = []
            if s.startswith("SELECT run_id, finished_at, scope, status, manifest FROM audit_run"):
                rows = [("run_a", T0, "FULL", "FAIL", {})]
            elif s.startswith("SELECT rule_id, rule_class, block, group_key, isin, evidence FROM audit_finding"):
                rows = [(f["rule_id"], f["rule_class"], f["block"], f["group_key"], f["isin"], f["evidence"]) for f in findings]
            elif "to_regclass('audit_finding_isin')" in s:
                rows = [("audit_finding_isin",)]
            elif s.startswith("SELECT rule_id, group_key, isin FROM audit_finding_isin"):
                rows = [("MGMT_LE_TOTAL", "MGMT_LE_TOTAL", "LU1")]
            from types import SimpleNamespace
            return SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: rows[0] if rows else None)

    out = cli.plan(Conn(), [COSTS], "first", live_keys=set())
    assert [c.rule_id for c in out] == ["ACI_MUST_AMORTISE", "MGMT_LE_TOTAL"] and out[1].isins == ("LU1",)


# --- switch, catalogue and the runner's sample scope --------------------------------------------------------------------------------------------
def test_the_incident_loop_switch_is_on_since_the_owner_confirmed_d7():
    assert cfg.AUDIT_INCIDENT_LOOP_ENABLED is True


def test_the_incident_ddl_block_carries_the_live_index_and_the_certification_checks():
    sys.path.insert(0, os.path.join(_ROOT, "scripts", "ops"))
    from migrate_cycle_telemetry import ddl_block
    block = ddl_block(name="audit_incident")
    for needle in ("uq_audit_incident_live", "WHERE state <> 'CLOSED'", "audit_incident_certified_ck", "audit_incident_closed_ck",
                   "CHECK (state IN ('OPEN', 'CERTIFIED', 'ACCEPTED', 'CLOSED'))", "CHECK (domain IN ('p2_metrics', 'cost_attributes'))"):
        assert needle in block, needle
    assert set(inc.STATES) == {"OPEN", "CERTIFIED", "ACCEPTED", "CLOSED"} and set(inc.FIX_KINDS) == {"RECOMPUTE", "CODE", "DATA", "ACCEPT"}


def test_the_incident_columns_match_the_select_and_the_ddl():
    selected = inc._INCIDENTS_SQL.split("SELECT ", 1)[1].split(" FROM ", 1)[0]
    assert tuple(c.strip() for c in selected.split(",")) == inc.INCIDENT_COLUMNS
    sys.path.insert(0, os.path.join(_ROOT, "scripts", "ops"))
    from migrate_cycle_telemetry import ddl_block
    for col in inc.INCIDENT_COLUMNS:
        assert f"    {col} " in ddl_block(name="audit_incident"), col


def test_a_sample_run_records_the_funds_it_covered_in_its_manifest():
    runner = _load("rsa_incident_t", ("scripts", "audit", "run_statistical_audit.py"))
    run = runner.AuditRun(domain="cost_attributes")
    run.scope_isins = ("LU2", "LU1")

    class Conn:
        def rollback(self):
            pass

        def execute(self, *_a):
            raise RuntimeError("no db")

    assert runner._run_manifest(Conn(), run)["scope_isins"] == ["LU1", "LU2"]
    assert "scope_isins" not in runner._run_manifest(Conn(), runner.AuditRun(domain="cost_attributes"))
