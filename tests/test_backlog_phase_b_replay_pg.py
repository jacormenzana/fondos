# tests/test_backlog_phase_b_replay_pg.py
# -*- coding: utf-8 -*-
"""The Phase B replay (scripts/audit/backlog_phase_b_replay.py, FND-0333) against the REAL gate code. Hermetic (pg_conn): synthetic histories check that the replay
refuses exactly the delivery closures that had an open prerequisite on their day, never gates REJECTED / DUPLICATED, honours the links of each moment, and counts flags.
The test at the end replays the REAL history when a snapshot of the live backlog is supplied (it is skipped otherwise) and writes the report:

    python scripts/audit/backlog_phase_b_replay.py --export SNAPSHOT.json
    FONDOS_B_REPLAY_SNAPSHOT=SNAPSHOT.json FONDOS_B_REPLAY_REPORT=REPORT.json python scripts/ops/run_pg_tests.py -- tests/test_backlog_phase_b_replay_pg.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(_ROOT / "scripts" / "audit"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_phase_b_replay as rp  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402
from test_backlog_transitions_pg import _base  # noqa: E402


@pytest.fixture
def scratch(pg_conn):
    """A scratch database with the real DDL of steps A-1 and Phase B, and no tickets."""
    _base(pg_conn)
    mig.migrate(pg_conn, "transitions")
    mig.migrate(pg_conn, "relations")
    return pg_conn


def _ticket(ap, status="OPEN", sol=None, closed=None):
    t = {"action_point_id": ap, "title": f"title of {ap}", "project_code": "FND", "nature": "BUG", "object_type": "N/A", "status": status, "priority": "LOW",
         "session_id": "FND_20261026_DEV_TEST"}
    if status == "CLOSED":
        t.update(solution_type=sol, closure_ts=closed, release_version="R2026.10-OLD" if sol in rp.DELIVERY else None,
                 deployment_ts=closed if sol in rp.DELIVERY else None)
    return t


def _ev(i, a, b, typ="DEPENDS_ON", st="CONFIRMED", ts="2026-10-01T10:00:00+00:00", absorbs=None):
    return {"event_id": i, "from_id": a, "to_id": b, "link_type": typ, "status": st, "evidence": "e", "absorbs": absorbs, "username": "agent:test", "event_ts": ts}


def run(conn, tickets, events, **kw):
    rp.load_snapshot(conn, {"tickets": tickets, "link_events": events})
    return rp.replay(conn, **kw)


def refused(rep):
    return sorted(r["ticket"] for r in rep["gate"]["refused"])


def test_a_delivery_closure_over_an_open_prerequisite_is_refused_and_the_others_are_not(scratch):
    tickets = [_ticket("FND-0001", "CLOSED", "CODE_FIX", "2026-10-10T10:00:00+00:00"),        # depends on 2, which is still open: refused
               _ticket("FND-0002", "IN_PROGRESS"),
               _ticket("FND-0003", "CLOSED", "CODE_FIX", "2026-10-12T10:00:00+00:00"),        # depends on 4, closed BEFORE: fine
               _ticket("FND-0004", "CLOSED", "DATA_FIX", "2026-10-11T10:00:00+00:00"),
               _ticket("FND-0005", "CLOSED", "WORKAROUND", "2026-10-13T10:00:00+00:00"),      # depends on 6, closed AFTER: refused
               _ticket("FND-0006", "CLOSED", "CODE_FIX", "2026-10-14T10:00:00+00:00"),
               _ticket("FND-0007", "CLOSED", "CODE_FIX", "2026-10-15T10:00:00+00:00")]        # no dependency at all
    events = [_ev(1, "FND-0001", "FND-0002"), _ev(2, "FND-0003", "FND-0004"), _ev(3, "FND-0005", "FND-0006")]
    rep = run(scratch, tickets, events)
    assert refused(rep) == ["FND-0001", "FND-0005"] and rep["gate"]["delivery_closures"] == 6 and rep["gate"]["errors"] == []
    assert rep["gate"]["refused"][0]["blockers"] == [{"kind": "PREREQUISITE", "ticket": "FND-0002", "status": "IN_PROGRESS"}]


def test_rejected_and_duplicated_closures_are_never_gated_by_the_replay(scratch):
    tickets = [_ticket("FND-0001", "CLOSED", "REJECTED", "2026-10-10T10:00:00+00:00"), _ticket("FND-0002", "CLOSED", "DUPLICATED", "2026-10-11T10:00:00+00:00"),
               _ticket("FND-0003", "IN_PROGRESS")]
    rep = run(scratch, tickets, [_ev(1, "FND-0001", "FND-0003"), _ev(2, "FND-0002", "FND-0003")])
    assert refused(rep) == [] and rep["gate"]["delivery_closures"] == 0 and rep["gate"]["not_gated_non_delivery"] == 2


def test_only_confirmed_links_that_still_stand_gate_the_replay(scratch):
    tickets = [_ticket(f"FND-000{i}", "CLOSED", "CODE_FIX", f"2026-10-1{i}T10:00:00+00:00") for i in (1, 2, 3)] + [_ticket("FND-0009", "IN_PROGRESS")]
    events = [_ev(1, "FND-0001", "FND-0009", st="PROPOSED"),                                                                 # never confirmed
              _ev(2, "FND-0002", "FND-0009"), _ev(3, "FND-0002", "FND-0009", st="REJECTED", ts="2026-10-05T10:00:00+00:00"),     # confirmed, then rejected
              _ev(4, "FND-0003", "FND-0009", typ="RELATED")]                                                                   # another type
    assert refused(run(scratch, tickets, events)) == []


def test_the_links_as_of_each_closure_ignore_a_link_decided_after_it(scratch):
    tickets = [_ticket("FND-0001", "CLOSED", "CODE_FIX", "2026-10-10T10:00:00+00:00"), _ticket("FND-0002", "IN_PROGRESS")]
    events = [_ev(1, "FND-0001", "FND-0002", ts="2026-10-20T10:00:00+00:00")]                  # the link was confirmed ten days AFTER the closure
    assert refused(run(scratch, tickets, events)) == ["FND-0001"]                              # today's links: the closure looks like it passed an open prerequisite
    assert refused(rp.replay(scratch, links_as_of_closure=True)) == []                         # the links that existed then: it could not have


def test_in_audit_mode_the_replay_refuses_nothing_and_logs_what_would_have_been_refused(scratch):
    tickets = [_ticket("FND-0001", "CLOSED", "CODE_FIX", "2026-10-10T10:00:00+00:00"), _ticket("FND-0002", "IN_PROGRESS"), _ticket("FND-0003", "CLOSED", "CODE_FIX", "2026-10-12T10:00:00+00:00")]
    rp.load_snapshot(scratch, {"tickets": tickets, "link_events": [_ev(1, "FND-0001", "FND-0002")]})
    gate = rp.replay(scratch, mode="AUDIT")["gate"]
    assert gate["mode"] == "AUDIT" and gate["refused"] == [] and [r["ticket"] for r in gate["audited"]] == ["FND-0001"] and gate["delivery_closures"] == 2
    assert [r["ticket"] for r in rp.replay(scratch, mode="ENFORCE")["gate"]["refused"]] == ["FND-0001"]


def test_the_flag_replay_counts_what_each_closure_would_have_raised(scratch):
    tickets = [_ticket("FND-0001", "CLOSED", "CODE_FIX", "2026-10-10T10:00:00+00:00"), _ticket("FND-0002", "IN_PROGRESS"), _ticket("FND-0003", "CLOSED", "CODE_FIX", "2026-10-09T10:00:00+00:00"),
               _ticket("FND-0004", "IN_PROGRESS"), _ticket("FND-0005", "CLOSED", "CODE_FIX", "2026-10-11T10:00:00+00:00")]
    events = [_ev(1, "FND-0001", "FND-0002", typ="IMPACTS"), _ev(2, "FND-0001", "FND-0004", typ="OVERLAPS", absorbs="scope"),     # 0001 raises on 0002 and 0004
              _ev(3, "FND-0001", "FND-0003", typ="IMPACTS"),                                                                     # 0003 closed before 0001: nothing to flag
              _ev(4, "FND-0005", "FND-0004", typ="OVERLAPS")]                                                                    # no absorbs: nothing
    fl = run(scratch, tickets, events)["flags"]
    assert fl == {"closures": 3, "median": 0, "max": 2, "closures_raising_any": 1}


def test_evaluate_accepts_only_the_expected_refusal_and_a_small_flag_median():
    side = lambda refused_, errors=(), audited=("FND-0223",): {"gate": {"delivery_closures": 213, "refused": [{"ticket": t} for t in refused_], "audited": [{"ticket": t} for t in audited],     # noqa: E731
                                                                       "errors": list(errors), "not_gated_non_delivery": 20},
                                                              "flags": {"closures": 233, "median": 0, "max": 2, "closures_raising_any": 7}}
    audit_ok = side([], audited=["FND-0223"])
    rep = lambda today, then, audit=audit_ok: {"links_as_of_today": today, "links_as_of_closure": then, "audit_mode": audit}     # noqa: E731
    assert all(p for _, p, _ in rp.evaluate(rep(side(["FND-0223"]), side(["FND-0223"]))))
    assert not all(p for _, p, _ in rp.evaluate(rep(side(["FND-0223", "FND-0160"]), side(["FND-0223"]))))
    assert not all(p for _, p, _ in rp.evaluate(rep(side([]), side([]))))
    assert not all(p for _, p, _ in rp.evaluate(rep(side(["FND-0223"], errors=[{"ticket": "x"}]), side(["FND-0223"]))))
    assert not all(p for _, p, _ in rp.evaluate(rep(side(["FND-0223"]), side(["FND-0223"]), audit=side(["FND-0223"], audited=["FND-0223"]))))      # audit mode must refuse nothing
    assert not all(p for _, p, _ in rp.evaluate(rep(side(["FND-0223"]), side(["FND-0223"]), audit=side([], audited=["FND-0223", "FND-0160"]))))      # and log exactly FND-0223
    bad = side(["FND-0223"])
    bad["flags"]["median"] = 3
    assert not all(p for _, p, _ in rp.evaluate(rep(bad, side(["FND-0223"]))))


@pytest.mark.skipif(not os.environ.get("FONDOS_B_REPLAY_SNAPSHOT"), reason="set FONDOS_B_REPLAY_SNAPSHOT (a --export file) to replay the real history")
def test_replay_of_the_real_history(scratch):
    snap = json.loads(Path(os.environ["FONDOS_B_REPLAY_SNAPSHOT"]).read_text(encoding="utf-8"))
    rp.load_snapshot(scratch, snap)
    report = {"population": {"tickets": len(snap["tickets"]), "link_events": len(snap["link_events"])},
              "links_as_of_today": rp.replay(scratch), "links_as_of_closure": rp.replay(scratch, links_as_of_closure=True), "audit_mode": rp.replay(scratch, mode="AUDIT")}
    out = os.environ.get("FONDOS_B_REPLAY_REPORT")
    if out:
        Path(out).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    assert all(p for _, p, _ in rp.evaluate(report)), rp.evaluate(report)


# --- the scratch-database guard: a programmatic check, not a convention on names (owner mitigation of 2026-10-28) ---------------------------------------------------------------

LIVE = {("local", "5436", "gestion"), ("local", "5436", "fondos")}


@pytest.mark.parametrize("host,port,db,ok", [
    ("127.0.0.1", 5497, "fondos_test", True),                  # the hermetic container
    ("localhost", "5481", "backlog_staging_20261028", True),   # a staging database on another port
    ("127.0.0.1", 5436, "gestion", False),                     # the live backlog
    ("localhost", 5436, "gestion", False),                     # ... through the other name of the same machine
    ("::1", 5436, "fondos", False),                            # the live fondos store
    ("db.example.com", 5436, "gestion", False),                # the same port and name on a remote host: still refused (port + name is the identity)
    ("127.0.0.1", 5497, "gestion", False),                     # the live NAME on a scratch port: refused (the name alone is enough to refuse)
    ("127.0.0.1", 5497, "production", False),                  # not on the allow-list: refused even though it is not live
    ("127.0.0.1", 5497, "", False),
    ("127.0.0.1", 5497, "fondos_test_copy", False),            # the allow-list is exact, not a prefix
])
def test_scratch_verdict_is_a_positive_identification(host, port, db, ok):
    assert rp.scratch_verdict(host, port, db, LIVE)[0] is ok


def test_live_targets_are_read_from_the_environment_and_the_owner_dsn_counts_as_the_backlog_too():
    env = {"FONDOS_BACKLOG_PG_DSN": "postgresql://w:pw@127.0.0.1:5436/gestion", "FONDOS_PG_DSN": "postgresql://a:pw@127.0.0.1:5436/fondos",
           "FONDOS_PG_DSN_OWNER": "postgresql://o:pw@localhost:5436/fondos", "UNRELATED": "x"}
    live = rp.live_targets(env)
    assert ("local", "5436", "gestion") in live and ("local", "5436", "fondos") in live and len(live) == 2
    assert rp.live_targets({}) == set()


def test_the_replay_refuses_a_connection_to_a_live_target(scratch, monkeypatch):
    """The scratch fixture is allowed; the same code pointed at a database that the environment names as live is refused before it executes anything."""
    info = scratch.info
    rp.assert_scratch(scratch, {})                              # no live target known: the allow-listed name passes
    monkeypatch.setenv("FONDOS_BACKLOG_PG_DSN", f"postgresql://x@{info.host or '127.0.0.1'}:{info.port}/{info.dbname}")      # the environment now says: this port and name are LIVE
    with pytest.raises(RuntimeError, match="LIVE database"):
        rp.assert_scratch(scratch)
    with pytest.raises(RuntimeError, match="refuses to run"):
        rp.load_snapshot(scratch, {"tickets": [], "link_events": []})
    with pytest.raises(RuntimeError, match="refuses to run"):
        rp.replay(scratch)
    assert scratch.execute("SELECT count(*) FROM backlog.BACKLOG").fetchone()[0] == 0                                         # it touched nothing
