# proyecto1/tests/test_statistical_audit_exception_events.py
# -*- coding: utf-8 -*-
"""FND-0263 (GOV-04): governed exceptions -- the fold that defines control.audit_accepted_finding from its append-only history, grant
states, drift detection, the CLI's mode rules, the EXCEPTIONS gate and the promotion note. No database (R-7); the writers, the trigger
and the migration are covered by test_statistical_audit_exception_events_pg.py."""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.statistical_audit import accepted_residuals as ar  # noqa: E402

D = "p2_metrics"
UNTIL = date(2027, 1, 9)


def _ts(n):
    return datetime(2026, 10, 12, 8, n, tzinfo=timezone.utc)


def grant(eid, isins, ap="FND-0250", until=UNTIL, kind="GRANT", rule="R", actor="ana"):
    return {"event_id": eid, "event_ts": _ts(eid), "event_type": kind, "domain": D, "rule_id": rule, "isins": list(isins), "ap_id": ap,
            "reason": "why", "expires_at": until, "cause": None, "ref_event_id": None, "actor": actor}


def approve(eid, ref, actor="ana"):
    return {"event_id": eid, "event_ts": _ts(eid), "event_type": "APPROVE", "domain": D, "rule_id": "R", "isins": None, "ap_id": None,
            "reason": None, "expires_at": None, "cause": None, "ref_event_id": ref, "actor": actor}


def revoke(eid, ref, isins=None, cause="OWNER"):
    return {"event_id": eid, "event_ts": _ts(eid), "event_type": "REVOKE", "domain": D, "rule_id": "R", "isins": isins, "ap_id": None,
            "reason": "no longer", "expires_at": None, "cause": cause, "ref_event_id": ref, "actor": "ana"}


# --- the fold --------------------------------------------------------------------------------------------------------------------
def test_a_pending_grant_accepts_nothing():
    assert ar.fold_events([grant(1, ["A", "B"])]) == {}


def test_an_approved_grant_accepts_its_isins_and_records_both_actors():
    out = ar.fold_events([grant(1, ["A", "B"], actor="ana"), approve(2, 1, actor="bob")])
    assert set(out) == {("R", "A"), ("R", "B")}
    a = out[("R", "A")]
    assert (a["ap_id"], a["expires_at"], a["granted_by"], a["approved_by"], a["grant_event_id"]) == ("FND-0250", UNTIL, "ana", "bob", 1)


def test_the_same_person_may_grant_and_approve_as_two_events():                           # decision D6
    out = ar.fold_events([grant(1, ["A"], actor="jacor"), approve(2, 1, actor="jacor")])
    assert out[("R", "A")]["granted_by"] == out[("R", "A")]["approved_by"] == "jacor"


def test_a_later_approved_renewal_supersedes_and_revoking_the_old_grant_does_not_touch_it():
    later = date(2027, 3, 1)
    ev = [grant(1, ["A", "B"]), approve(2, 1), grant(3, ["A"], ap="FND-0300", until=later, kind="RENEW"), approve(4, 3), revoke(5, 1)]
    out = ar.fold_events(ev)
    assert set(out) == {("R", "A")}                                                       # B went with grant 1; A lives on grant 3
    assert (out[("R", "A")]["ap_id"], out[("R", "A")]["expires_at"]) == ("FND-0300", later)


def test_a_partial_revoke_withdraws_only_those_isins():
    out = ar.fold_events([grant(1, ["A", "B", "C"]), approve(2, 1), revoke(3, 1, isins=["B"])])
    assert set(out) == {("R", "A"), ("R", "C")}


def test_an_approval_never_revives_revoked_isins():
    ev = [grant(1, ["A", "B"]), revoke(2, 1, isins=["A"]), approve(3, 1)]                # partial revoke while pending
    assert set(ar.fold_events(ev)) == {("R", "B")}
    assert ar.fold_events([grant(1, ["A"]), revoke(2, 1), approve(3, 1)]) == {}          # fully revoked: nothing to approve


def test_the_fold_ignores_event_order_in_the_input():
    ev = [grant(1, ["A"]), approve(2, 1), revoke(3, 1)]
    assert ar.fold_events(list(reversed(ev))) == ar.fold_events(ev) == {}


def test_an_expired_grant_stays_in_the_fold_like_the_table_kept_it():                     # readers filter by date (load_accepted)
    assert set(ar.fold_events([grant(1, ["A"], until=date(2020, 1, 1)), approve(2, 1)])) == {("R", "A")}


@pytest.mark.parametrize("events,state", [
    ([], "MISSING"),
    ([grant(1, ["A"]), approve(2, 1)], "APPROVED"),
    ([grant(1, ["A"])], "PENDING"),
    ([grant(1, ["A"]), approve(2, 1), revoke(3, 1)], "REVOKED"),
    ([grant(1, ["A", "B"]), approve(2, 1), revoke(3, 1, isins=["A"])], "APPROVED"),     # partially revoked: still approved
])
def test_grant_state(events, state):
    assert ar.grant_state(events, 1) == state


def test_an_approve_event_is_not_a_grant():
    assert ar.grant_state([grant(1, ["A"]), approve(2, 1)], 2) == "NOT_A_GRANT"


def test_the_loader_selects_exactly_the_event_columns_in_order():
    selected = ar._EVENTS_SQL.split("SELECT ", 1)[1].split(" FROM ", 1)[0]
    assert tuple(c.strip() for c in selected.split(",")) == ar.EVENT_COLUMNS


# --- drift -------------------------------------------------------------------------------------------------------------------------
def test_drift_is_empty_when_the_table_says_what_the_history_says():
    folded = ar.fold_events([grant(1, ["A", "B"]), approve(2, 1)])
    assert ar.drift([("R", "A", "FND-0250", UNTIL), ("R", "B", "FND-0250", UNTIL)], folded) == []


def test_drift_names_rows_missing_extra_or_different():
    folded = ar.fold_events([grant(1, ["A", "B"]), approve(2, 1)])
    out = ar.drift([("R", "A", "FND-0250", date(2028, 1, 1)), ("R", "Z", "FND-0999", UNTIL)], folded)
    assert out == [("R", "A", ("FND-0250", date(2028, 1, 1)), ("FND-0250", UNTIL)),     # expiry edited in the table
                   ("R", "B", None, ("FND-0250", UNTIL)),                                # approved, missing from the table
                   ("R", "Z", ("FND-0999", UNTIL), None)]                                # written behind the history's back


# --- the CLI's mode rules ------------------------------------------------------------------------------------------------------------
def _cli():
    spec = importlib.util.spec_from_file_location("accept_audit_residual_ev", os.path.join(_ROOT, "scripts", "audit", "accept_audit_residual.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["accept_audit_residual_ev"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("argv,mode,ok", [
    (["--domain", "p2", "--rule", "R", "--ap", "FND-0250", "--reason", "x" * 25], "grant", True),
    (["--domain", "p2", "--rule", "R", "--reason", "x" * 25], None, False),               # a grant needs --ap
    (["--approve", "7"], "approve", True),
    (["--approve", "7", "--revoke", "8"], None, False),                                   # one decision per command (D6: two steps)
    (["--revoke", "7", "--cause", "RESOLVED", "--reason", "x" * 25], "revoke", True),
    (["--revoke", "7", "--reason", "x" * 25], "revoke", False),                           # no cause
    (["--revoke", "7", "--cause", "OWNER", "--reason", "short"], "revoke", False),
    (["--history"], None, False),                                                         # needs --domain
    (["--history", "--domain", "costs"], "history", True),
])
def test_cli_modes(argv, mode, ok):
    cli = _cli()
    got, problems = cli.mode_of(cli._parser().parse_args(argv))
    assert got == mode and (problems == []) == ok


def test_cli_revoke_causes_are_the_modules():
    cli = _cli()
    assert tuple(cli.REVOKE_CAUSES) == tuple(ar.REVOKE_CAUSES)
    assert cli.AUDIT_DOMAIN == {"costs": "cost_attributes", "p2": "p2_metrics"}


# --- the gate and the promotion note -------------------------------------------------------------------------------------------------
def test_an_untrustworthy_history_is_a_hard_gate_that_no_override_covers():
    from proyecto3.src import scenario_lifecycle as lc
    from shared.statistical_audit.gating import AuditScope
    audit = AuditScope(run_id="r", run_ts=datetime(2026, 10, 12, tzinfo=timezone.utc))
    v = lc.evaluate_promotion(status="DRAFT", n_funds=10, weight_sum=1.0, stale_checks=[], exposure_non_eur=0.1, budget=0.3,
                              audit=audit, accept_alarms="a written reason", accept_ap="FND-0263", ap_exists=True,
                              exceptions_problem="history unreadable")
    assert [b for b in v.blockers if b.startswith("[GATE:EXCEPTIONS]")] and not v.overridden
    clean = lc.evaluate_promotion(status="DRAFT", n_funds=10, weight_sum=1.0, stale_checks=[], exposure_non_eur=0.1, budget=0.3,
                                  audit=audit)
    assert clean.blockers == []


def _promote():
    sys.path.insert(0, os.path.join(_ROOT, "scripts", "launch"))
    import p3_promote_scenario as cli
    return cli


@pytest.mark.parametrize("enabled,behaviour,problem_fragment", [
    (True, "raise", "no es legible"),
    (False, "raise", None),                                                               # switch OFF: silent, as before
    (True, "drift", "difieren"),
    (False, "drift", None),
    (True, "clean", None),
])
def test_governed_exceptions_fail_closed_only_with_the_switch(monkeypatch, enabled, behaviour, problem_fragment):
    cli = _promote()

    def fake(conn, domain):
        if behaviour == "raise":
            raise RuntimeError("relation does not exist")
        return {"events": 2, "drift": [("R", "A", ("FND-1", UNTIL), None)] if behaviour == "drift" else [],
                "sources": {("R", "A"): 11}}

    monkeypatch.setattr(ar, "verify_governed", fake)
    problem, sources = cli._governed_exceptions(SimpleNamespace(rollback=lambda: None), enabled)
    assert (problem is None) if problem_fragment is None else (problem_fragment in problem)
    assert sources == ({} if behaviour == "raise" else {("R", "A"): 11})


def test_the_note_names_the_grants_the_promotion_relied_on():
    cli = _promote()
    scope = SimpleNamespace(covered_by_residual=[("R", "A", "FND-1", UNTIL), ("R", "B", "FND-1", UNTIL)],
                            residuals_on_scenario=[("Q", "C", "FND-2", UNTIL)])
    assert cli._exception_ids(scope, {("R", "A"): 11, ("R", "B"): 11, ("Q", "C"): 4}) == "exceptions 4,11"
    assert cli._exception_ids(scope, {}) == ""
