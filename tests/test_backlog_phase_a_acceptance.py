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
         "triggers": ["governed_writes", "set_update_ts"], "tickets": 301, "max_ticket_id": "FND-0336", "backfill_events": 301}
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
    assert ev(catalog=_catalog_a1(backfill_events=300)) == ["every baseline ticket has a BACKFILL status event"]
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
