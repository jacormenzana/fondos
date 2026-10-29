# tests/test_backlog_phase_a_acceptance.py
# -*- coding: utf-8 -*-
"""Acceptance logic of Backlog v2 Phase A (FND-0331; scripts/audit/backlog_phase_a_acceptance.py). Pure: no database (R-7). The hermetic counterpart, which
runs the checker against the real DDL, is tests/test_backlog_phase_a_acceptance_pg.py."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_phase_a_acceptance as acc  # noqa: E402

FP = {"logs": 1300, "logs_md5": "a" * 32, "events": 300, "events_md5": "b" * 32}
BASE = dict(FP, tickets=301)
CFG = ["search_path=pg_catalog, backlog", "backlog.via_proc=on"]


def _routine(name, **kw):
    r = {"name": name, "secdef": True, "owner": "backlog_owner",
         "config": CFG if name in acc.WRITING_ROUTINES else ["search_path=pg_catalog, backlog"]}
    r.update(kw)
    return r


def _catalog_a1(**over):
    c = {"owner_role": {"superuser": False, "can_login": False},
         "routines": [_routine(n) for n in acc.HARDENED],
         "audit_table_owners": {"raw_update_event": "backlog_owner", "status_event": "backlog_owner", "wait_event": "backlog_owner"},
         "writer": {"can_update_status": True, "can_update_release_version": True, "can_update_assignee": True, "can_update_title": True,
                    "can_insert_ticket": True, "can_insert_log": True, "can_insert_link_event": True, "can_write_audit_tables": False,
                    "can_execute_transition": True},
         "triggers": ["governed_writes", "set_update_ts"], "tickets": 301, "max_ticket_id": "FND-0336", "backfill_events": 301,
         "tickets_without_status_event": 0}
    c.update(over)
    return c


def _catalog_a2(**w):
    c = _catalog_a1()
    c["writer"].update(dict(can_update_status=False, can_update_release_version=False, can_update_assignee=False, can_insert_ticket=False), **w)
    c["triggers"] = ["governed_writes", "set_update_ts", "logs_actor", "link_event_actor"]
    return c


def failed(results):
    return [n for n, ok, _ in results if not ok]


def test_a1_complete_state_is_accepted():
    assert failed(acc.evaluate_a1(0, BASE, dict(FP), _catalog_a1())) == []


def test_a1_each_criterion_can_fail_on_its_own():
    ev = lambda **kw: failed(acc.evaluate_a1(kw.pop("rc", 0), BASE, kw.pop("after", dict(FP)), kw.pop("catalog", _catalog_a1())))
    assert ev(rc=1) == ["migration exit code is 0"]
    assert ev(catalog=_catalog_a1(owner_role={"superuser": True, "can_login": False})) == ["backlog_owner exists, is not a superuser and cannot log in"]
    assert ev(catalog=_catalog_a1(owner_role={"superuser": False, "can_login": True})) == ["backlog_owner exists, is not a superuser and cannot log in"]
    assert ev(catalog=_catalog_a1(owner_role=None)) == ["backlog_owner exists, is not a superuser and cannot log in"]
    name = "every ticket has a status event (BACKFILL for those that pre-date A-1, LIVE for later ones)"
    assert ev(catalog=_catalog_a1(tickets_without_status_event=2)) == [name]
    assert ev(catalog=_catalog_a1(tickets_without_status_event=None)) == [name]                  # the audit table is absent
    assert ev(catalog=_catalog_a1(backfill_events=0)) == [name]                                  # nothing was ever backfilled
    assert ev(catalog=_catalog_a1(backfill_events=300, tickets=305)) == []                       # tickets opened later carry LIVE events: more tickets than backfill rows is fine
    assert ev(after=dict(FP, logs_md5="c" * 32)) == ["history up to the baseline ids is identical"]
    assert ev(after=dict(FP, events=299)) == ["history up to the baseline ids is identical"]
    assert ev(catalog=_catalog_a1(triggers=["set_update_ts"])) == ["the shadow-stage trigger exists"]


def test_a1_hardening_failures_are_all_detected():
    for bad in ({"secdef": False}, {"owner": "fondos_owner"}, {"config": []}, {"config": ["search_path=public"]}):
        cat = _catalog_a1()
        cat["routines"] = [_routine("sp_transition", **bad)] + [r for r in cat["routines"] if r["name"] != "sp_transition"]
        assert failed(acc.evaluate_a1(0, BASE, dict(FP), cat)) == ["routines are SECURITY DEFINER, owned by backlog_owner, search_path pinned"], bad
    cat = _catalog_a1()                                                                          # the two writing routines also need the via_proc marker
    cat["routines"] = [_routine("sp_open_ticket", config=["search_path=pg_catalog, backlog"])] + [r for r in cat["routines"] if r["name"] != "sp_open_ticket"]
    assert failed(acc.evaluate_a1(0, BASE, dict(FP), cat))
    cat = _catalog_a1()
    cat["routines"] = [r for r in cat["routines"] if r["name"] != "trg_governed_writes"]          # a missing routine is a failure
    assert failed(acc.evaluate_a1(0, BASE, dict(FP), cat))


def test_a1_audit_table_ownership_and_additivity():
    assert failed(acc.evaluate_a1(0, BASE, dict(FP), _catalog_a1(audit_table_owners={"status_event": "fondos_owner"})))
    for key, val in (("can_update_status", False), ("can_insert_ticket", False), ("can_insert_log", False), ("can_execute_transition", False),
                     ("can_write_audit_tables", True)):
        cat = _catalog_a1()
        cat["writer"][key] = val
        assert failed(acc.evaluate_a1(0, BASE, dict(FP), cat)) == ["A-1 is additive: the writer lost nothing and cannot write the audit tables"], key
    assert failed(acc.evaluate_a1(0, BASE, dict(FP), _catalog_a1(writer=None)))                  # no writer role at all is not an accepted A-1


def test_a2_complete_state_is_accepted():
    assert failed(acc.evaluate_a2(0, BASE, dict(FP), _catalog_a2())) == []


def test_a2_requires_the_revoke_the_exceptions_and_the_triggers():
    ev = lambda catalog, rc=0, after=None: failed(acc.evaluate_a2(rc, BASE, after or dict(FP), catalog))
    assert ev(_catalog_a2(), rc=2) == ["migration exit code is 0"]
    for key in ("can_update_status", "can_update_release_version", "can_update_assignee", "can_insert_ticket"):
        assert len(ev(_catalog_a2(**{key: True}))) == 1, key
    assert len(ev(_catalog_a2(can_write_audit_tables=True))) == 1
    for key in ("can_insert_log", "can_insert_link_event", "can_update_title", "can_execute_transition"):            # an exception that was lost
        assert len(ev(_catalog_a2(**{key: False}))) == 1, key
    cat = _catalog_a2()
    cat["triggers"] = ["governed_writes", "set_update_ts"]
    assert ev(cat) == ["the author-check triggers exist on logs and link events"]
    assert ev(_catalog_a2(), after=dict(FP, logs=1299)) == ["history up to the baseline ids is identical"]


def test_the_evaluators_do_not_mutate_their_inputs():
    cat, base = _catalog_a1(), dict(BASE)
    snap = copy.deepcopy((cat, base))
    acc.evaluate_a1(0, base, dict(FP), cat)
    assert (cat, base) == snap


def test_verify_without_its_arguments_is_a_usage_error():
    assert acc.main(["--verify", "a1"]) == acc.RC_USAGE
    assert acc.main(["--verify", "a2", "--baseline-file", "x.json"]) == acc.RC_USAGE
    assert acc.main(["--verify", "human"]) == acc.RC_USAGE and acc.main(["--verify", "history", "--baseline-file", "x.json"]) == acc.RC_USAGE


# --- FND-0337 (owner-only role) and FND-0338 (descriptive history) ---------------------------------------------------------------------------------

def _ident(**over):
    i = {"human_role": {"superuser": False, "can_login": True, "member_of_writer": True}, "authorization_function": True,
         "calls_authorization": {"sp_open_ticket": True, "sp_transition": True, "trg_actor_known": False},
         "history": {"owner": "backlog_owner", "trigger_def": "CREATE TRIGGER field_changes AFTER UPDATE OF " + ", ".join(acc.DESCRIPTIVE_FIELDS) + " ON backlog.backlog",
                     "append_only": True, "writer_can_insert": False, "view": True}}
    i.update(over)
    return i


def test_the_descriptive_columns_of_the_acceptance_are_the_client_whitelist():
    from shared import backlog_client as bc
    assert acc.DESCRIPTIVE_FIELDS == bc.DESCRIPTIVE_FIELDS


def test_human_step_complete_state_is_accepted_and_each_criterion_can_fail():
    ok = lambda **kw: failed(acc.evaluate_human(kw.pop("rc", 0), BASE, kw.pop("after", dict(FP)), kw.pop("catalog", _catalog_a1()), kw.pop("ident", _ident())))
    assert ok() == []
    assert ok(rc=1) == ["migration exit code is 0"]
    for bad in (None, {"superuser": True, "can_login": True, "member_of_writer": True}, {"superuser": False, "can_login": False, "member_of_writer": True},
                {"superuser": False, "can_login": True, "member_of_writer": False}, {"superuser": False, "can_login": True, "member_of_writer": None}):
        assert len(ok(ident=_ident(human_role=bad))) == 1, bad
    assert len(ok(ident=_ident(authorization_function=False))) == 1
    assert len(ok(ident=_ident(calls_authorization={"sp_open_ticket": True, "sp_transition": False}))) == 1
    locked = _catalog_a1()
    locked["writer"]["can_execute_transition"] = False
    assert len(ok(catalog=locked)) == 1
    assert ok(after=dict(FP, logs=1)) == ["history up to the baseline ids is identical"]


def test_history_step_complete_state_is_accepted_and_coverage_is_checked_column_by_column():
    ok = lambda **kw: failed(acc.evaluate_history(kw.pop("rc", 0), BASE, kw.pop("after", dict(FP)), _catalog_a1(), kw.pop("ident", _ident())))
    assert ok() == []
    assert ok(rc=3) == ["migration exit code is 0"]
    for col in acc.DESCRIPTIVE_FIELDS:                                                          # a trigger that misses ANY one column fails the coverage check
        h = dict(_ident()["history"])
        h["trigger_def"] = h["trigger_def"].replace(col, "other_column", 1) if col != "title" else h["trigger_def"].replace("OF title,", "OF")
        assert ok(ident=_ident(history=h)) == ["the trigger watches all eight descriptive columns (coverage)"], col
    for key, val in (("owner", "fondos_owner"), ("append_only", False)):
        assert len(ok(ident=_ident(history=dict(_ident()["history"], **{key: val})))) == 1, key
    assert len(ok(ident=_ident(history=dict(_ident()["history"], view=False)))) == 1
    assert len(ok(ident=_ident(history=dict(_ident()["history"], writer_can_insert=True)))) == 1
    assert len(ok(ident=_ident(history=None))) >= 4                                             # not applied: rejected on every count


# --- IAM (FND-0354) -------------------------------------------------------------------------------------------------------------------------

def _iam(**over):
    role = lambda ex: {"can_login": True, "superuser": False, "select_all": True, "write_tables": [], "executes": ex}      # noqa: E731
    c = {"roles": {"backlog_reader": role([]), "backlog_logger": role(["sp_append_log"]), "backlog_incident": role(["sp_report_incident"])},
         "routines": {"fn_actor_bound": True, "sp_bind_login": True, "sp_set_binding_window": True, "fn_off_hours": True, "sp_append_log": True, "sp_report_incident": True},
         "bindings": [], "active_logins": 0, "active_persons": [], "writers": [], "unbound_writers": [],
         "binding_store": {"declared_by_column": True, "declared_by_constraint": True, "audit_view": True, "window_view": True}}
    c.update(over)
    return c


def test_iam_step_is_accepted_when_each_role_has_exactly_its_scope_and_each_criterion_can_fail():
    results = acc.evaluate_iam(0, FP, FP, _iam())
    assert all(ok for _, ok, _ in results) and len(results) == 7
    broken = {
        "migration rc": (1, _iam()),
        "a routine is missing": (0, _iam(routines={"fn_actor_bound": True, "sp_bind_login": True, "sp_set_binding_window": True, "fn_off_hours": True, "sp_append_log": False, "sp_report_incident": True})),
        "no operational window": (0, _iam(binding_store={"declared_by_column": True, "declared_by_constraint": True, "audit_view": True, "window_view": False})),
        "the window routines are missing": (0, _iam(routines={"fn_actor_bound": True, "sp_bind_login": True, "sp_append_log": True, "sp_report_incident": True})),
        "no declared_by column": (0, _iam(binding_store={"declared_by_column": False, "declared_by_constraint": True, "audit_view": True, "window_view": True})),
        "no declared_by constraint": (0, _iam(binding_store={"declared_by_column": True, "declared_by_constraint": False, "audit_view": True, "window_view": True})),
        "no audit view": (0, _iam(binding_store={"declared_by_column": True, "declared_by_constraint": True, "audit_view": False, "window_view": True})),
        "store not reported": (0, _iam(binding_store=None)),
    }
    for name, (rc, cat) in broken.items():
        assert not all(ok for _, ok, _ in acc.evaluate_iam(rc, FP, FP, cat)), name
    for role, field, value in (("backlog_reader", "executes", ["sp_append_log"]), ("backlog_logger", "executes", ["sp_append_log", "sp_report_incident"]),
                               ("backlog_incident", "write_tables", ["backlog_logs"]), ("backlog_logger", "superuser", True), ("backlog_reader", "select_all", False)):
        cat = copy.deepcopy(_iam())
        cat["roles"][role][field] = value
        assert not all(ok for _, ok, _ in acc.evaluate_iam(0, FP, FP, cat)), (role, field)
    gone = _iam()
    del gone["roles"]["backlog_logger"]
    assert not all(ok for _, ok, _ in acc.evaluate_iam(0, FP, FP, gone))
    assert not all(ok for _, ok, _ in acc.evaluate_iam(0, FP, dict(FP, logs=1301), _iam()))                             # history must not move


def test_retire_needs_no_unbound_writer_and_three_distinct_persons_who_have_made_transitions():
    human = lambda n, person: {"login_name": n, "actor": f"human:{n}", "person": person, "kind": "HUMAN", "active": True, "declared_by": "human:j.cormenzana_owner"}      # noqa: E731
    three = [human("a", "a.gomez"), human("b", "j.cormenzana"), human("c", "m.ruiz")]
    moved = {"a.gomez": 4, "j.cormenzana": 9, "m.ruiz": 2}
    ok = _iam(bindings=three, writers=[{"login": "a", "bound": True}], transitions_by_person=moved, transition_persons=sorted(moved))
    assert all(r[1] for r in acc.evaluate_retire(0, FP, FP, ok)) and len(acc.evaluate_retire(0, FP, FP, ok)) == 6
    assert acc.REQUIRED_OPERATOR_PERSONS == 3
    two_persons = [human("a", "a.gomez"), human("b", "j.cormenzana"), human("c", "j.cormenzana")]                           # three logins, two people: NOT enough
    assert not all(r[1] for r in acc.evaluate_retire(0, FP, FP, dict(ok, bindings=two_persons)))
    assert not all(r[1] for r in acc.evaluate_retire(0, FP, FP, dict(ok, transitions_by_person={"a.gomez": 4}, transition_persons=["a.gomez"])))   # bound, but only one has moved a ticket
    assert not all(r[1] for r in acc.evaluate_retire(0, FP, FP, _iam(bindings=three)))                                       # nobody has moved anything
    assert not all(r[1] for r in acc.evaluate_retire(0, FP, FP, dict(ok, unbound_writers=["fondos_writer"])))
    undeclared = [dict(three[0], declared_by=None)] + three[1:]
    assert not all(r[1] for r in acc.evaluate_retire(0, FP, FP, dict(ok, bindings=undeclared)))                               # every active binding must name its declarer
    assert all(r[1] for r in acc.evaluate_retire(0, FP, FP, dict(ok, bindings=three + [dict(three[0], login_name="old", active=False, declared_by=None)])))     # a revoked legacy one does not count
    agent = {"login_name": "x", "actor": "agent:%", "person": "claude", "kind": "AGENT", "active": True}
    assert not all(r[1] for r in acc.evaluate_retire(0, FP, FP, dict(ok, bindings=three[:2] + [agent])))                    # agents never count as operators
    revoked = dict(three[2], active=False)
    assert not all(r[1] for r in acc.evaluate_retire(0, FP, FP, dict(ok, bindings=three[:2] + [revoked])))
    assert not all(r[1] for r in acc.evaluate_retire(1, FP, FP, ok))
