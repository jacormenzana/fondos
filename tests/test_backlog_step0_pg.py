# tests/test_backlog_step0_pg.py
# -*- coding: utf-8 -*-
"""Step 0 of Backlog v2 (scripts/ops/backlog_step0.py and its manifest, FND-0333): the fixed list of historical corrections. Hermetic (pg_conn): a scratch backlog
shaped like the real one (blocked tickets without a blocker, DEFERRED ones without a revisit date, two DUPLICATED closures without a link, the FND-0223 closure).
Checks that the dry run lists every row of spec 6.6 and changes nothing, that applying is append-only and idempotent, and that nothing is reversed.

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_step0_pg.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_step0 as s0  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402
from shared import backlog_client as bc  # noqa: E402
from test_backlog_transitions_pg import _base  # noqa: E402

ME = "agent:claude-opus-5-5"
DEFERRED = ("FND-0033", "FND-0035", "FND-0078", "FND-0167", "FND-0185", "FND-0209")
DEPLOYED = date(2026, 11, 2)


def mk(conn, ap, status, sol=None):
    conn.execute("SELECT set_config('backlog.via_proc', 'on', true)")
    closed = status == "CLOSED"
    conn.execute("INSERT INTO backlog.BACKLOG (action_point_id, title, project_code, nature, object_type, status, priority, session_id, solution_type, closure_ts, release_version, deployment_ts) "
                 "VALUES (%s, %s, 'FND', 'BUG', 'N/A', %s, 'LOW', 'FND_20261026_DEV_TEST', %s, %s, %s, %s)",
                 (ap, f"title of {ap}", status, sol, "2026-10-20T10:00:00+00:00" if closed else None,
                  "R2026.10-OLD" if sol in ("CODE_FIX", "DATA_FIX") else None, "2026-10-20T10:00:00+00:00" if sol in ("CODE_FIX", "DATA_FIX") else None))
    conn.execute("SELECT set_config('backlog.via_proc', 'off', true)")


@pytest.fixture
def world(pg_conn):
    _base(pg_conn)
    mig.migrate(pg_conn, "transitions")
    mig.migrate(pg_conn, "relations")
    for ap, st, sol in [("FND-0203", "BLOCKED", None), ("FND-0236", "IN_PROGRESS", None), ("FND-0029", "BLOCKED", None), ("FND-0041", "BLOCKED", None),
                        ("FND-0158", "BLOCKED", None), ("FND-0193", "IN_PROGRESS", None), ("FND-0223", "CLOSED", "CODE_FIX"),
                        ("FND-0022", "CLOSED", "DUPLICATED"), ("FND-0015", "CLOSED", "CODE_FIX"), ("FND-0038", "CLOSED", "DUPLICATED"), ("FND-0046", "CLOSED", "CODE_FIX")] + [(d, "DEFERRED", None) for d in DEFERRED]:
        mk(pg_conn, ap, st, sol)
    bc.record_link_event(from_id="FND-0203", to_id="FND-0236", link_type="RELEASE_WITH", status="CONFIRMED", evidence="pre-existing", username=ME, conn=pg_conn)
    return pg_conn


def run_all(conn, data):
    ctx = s0.build_context(data["rows"], bc, conn=conn)
    return {r["id"]: s0.state_of(r, ctx) for r in data["rows"]}, ctx


def apply_all(conn, data):
    ctx = s0.build_context(data["rows"], bc, conn=conn)
    out = []
    for r in data["rows"]:
        if s0.state_of(r, ctx) == "TODO":
            out.append((r["id"], s0.apply_row(r, bc, actor=ME, deployed_on=DEPLOYED, revisit_days=data["revisit_days"], conn=conn)))
    return out


def test_the_manifest_lists_every_row_of_the_spec():
    data = s0.load_manifest()
    rows = data["rows"]
    assert data["version"] == 1 and data["revisit_days"] == 90 and [r["id"] for r in rows] == [f"S0-{i:02d}" for i in range(1, 14)]
    by = {(r["ticket"], r["action"]) for r in rows}
    assert {("FND-0203", "link"), ("FND-0203", "annotate_blocker"), ("FND-0029", "annotate_blocker"), ("FND-0041", "transition"), ("FND-0223", "record_exception"),
            ("FND-0022", "link"), ("FND-0038", "link")} <= by
    assert {r["ticket"] for r in rows if r["action"] == "annotate_revisit"} == set(DEFERRED)
    assert all(r["basis"] for r in rows)
    assert not [r for r in rows if r["action"] == "link" and r["status"] != "CONFIRMED"]            # no row rejects or reverses a link
    assert not [r for r in rows if r["action"] == "transition" and r["to_status"] == "OPEN"]        # and none reopens anything


def test_the_loader_refuses_a_malformed_manifest(tmp_path):
    for bad in ("version: 1\nrevisit_days: 90\nrows:\n  - {id: A, ticket: FND-1, action: nonsense, basis: b}\n",
                "version: 1\nrevisit_days: 90\nrows:\n  - {id: A, ticket: FND-1, action: link, basis: b}\n",
                "version: 1\nrevisit_days: 90\nrows:\n  - {id: A, ticket: FND-1, action: annotate_revisit, basis: b}\n  - {id: A, ticket: FND-2, action: annotate_revisit, basis: b}\n",
                "version: 1\nrevisit_days: 90\nrows:\n  - {id: A, ticket: FND-1, action: annotate_blocker, basis: b}\n"):
        p = tmp_path / "m.yaml"
        p.write_text(bad, encoding="utf-8")
        with pytest.raises(ValueError):
            s0.load_manifest(p)


def test_the_dry_run_lists_every_row_as_to_do_and_changes_nothing(world):
    data = s0.load_manifest()
    before = (bc.history_fingerprint(conn=world), world.execute("SELECT count(*) FROM backlog.wait_event").fetchone()[0], world.execute("SELECT count(*) FROM backlog.status_event").fetchone()[0])
    states, _ = run_all(world, data)
    assert set(states.values()) == {"TODO"} and len(states) == 13
    assert (bc.history_fingerprint(conn=world), world.execute("SELECT count(*) FROM backlog.wait_event").fetchone()[0], world.execute("SELECT count(*) FROM backlog.status_event").fetchone()[0]) == before


def test_applying_is_append_only_complete_and_idempotent(world):
    data = s0.load_manifest()
    closed_before = world.execute("SELECT action_point_id, status, solution_type, closure_ts, release_version FROM backlog.BACKLOG WHERE status = 'CLOSED' ORDER BY 1").fetchall()
    fp = bc.history_fingerprint(conn=world)
    done = apply_all(world, data)
    assert len(done) == 13
    assert set(run_all(world, data)[0].values()) == {"DONE"}
    assert apply_all(world, data) == []                                                           # the second run changes nothing
    # history: nothing rewritten (the fingerprint up to the old ids is identical); only appended
    assert bc.history_fingerprint(max_log_id=fp["max_log_id"], max_event_id=fp["max_event_id"], conn=world) == fp
    assert world.execute("SELECT action_point_id, status, solution_type, closure_ts, release_version FROM backlog.BACKLOG WHERE status = 'CLOSED' ORDER BY 1").fetchall()[:len(closed_before)] == closed_before
    # links: the pre-existing RELEASE_WITH is untouched, the new ones carry origin STEP0
    rows = world.execute("SELECT from_id, to_id, link_type, status, origin FROM backlog.backlog_link_event WHERE origin = 'STEP0' ORDER BY event_id").fetchall()
    assert rows == [("FND-0203", "FND-0236", "DEPENDS_ON", "CONFIRMED", "STEP0"), ("FND-0022", "FND-0015", "DUPLICATES", "CONFIRMED", "STEP0"), ("FND-0038", "FND-0046", "DUPLICATES", "CONFIRMED", "STEP0")]
    assert world.execute("SELECT link_type, status FROM backlog.backlog_link_event WHERE link_type = 'RELEASE_WITH'").fetchall() == [("RELEASE_WITH", "CONFIRMED")]


def test_the_waits_get_their_structure_and_the_deferred_placeholders_count_from_the_deployment_day(world):
    apply_all(world, s0.load_manifest())
    w = {(r[0], r[1]): r[2:] for r in world.execute("SELECT action_point_id, kind, blocker_ref, condition_text, revisit_by, revisit_basis FROM backlog.wait_event").fetchall()}
    assert w[("FND-0203", "BLOCKED")] == ("FND-0236", None, None, "DECLARED")
    assert w[("FND-0029", "BLOCKED")][1] == "external source (Deutsche Bank catalogue); re-check after each harvest"
    for d in DEFERRED:
        assert w[(d, "DEFERRED")][2:] == (DEPLOYED + timedelta(days=90), "DEFAULT_90D")
    status = dict(world.execute("SELECT action_point_id, status FROM backlog.BACKLOG").fetchall())
    assert status["FND-0203"] == "BLOCKED" and status["FND-0029"] == "BLOCKED" and all(status[d] == "DEFERRED" for d in DEFERRED)      # nothing transitions automatically


def test_fnd_0041_moves_to_todo_as_a_legal_move_and_fnd_0223_is_only_recorded(world):
    apply_all(world, s0.load_manifest())
    ev = world.execute("SELECT from_status, to_status, forced, actor FROM backlog.status_event WHERE action_point_id = 'FND-0041' ORDER BY event_id DESC LIMIT 1").fetchone()
    assert ev == ("BLOCKED", "TODO", False, ME)                                                   # a legal move of the matrix: not forced, not counted as an override
    assert world.execute("SELECT status, solution_type FROM backlog.BACKLOG WHERE action_point_id = 'FND-0223'").fetchone() == ("CLOSED", "CODE_FIX")      # not reopened, not changed
    logs = world.execute("SELECT comment FROM backlog.BACKLOG_LOGS WHERE action_point_id = 'FND-0223'").fetchall()
    assert len(logs) == 1 and logs[0][0].startswith("STEP0-PAST-GATE-EXCEPTION: ")
    assert world.execute("SELECT count(*) FROM backlog.status_event WHERE action_point_id = 'FND-0223' AND origin = 'LIVE'").fetchone()[0] == 0


def test_a_row_that_no_longer_applies_is_skipped_not_forced(world):
    data = s0.load_manifest()
    bc.transition("FND-0041", "IN_PROGRESS", session_id="FND_20261028_DEV_TEST", comment="the owner already moved it by hand", actor=ME, conn=world)
    states, _ = run_all(world, data)
    assert states["S0-04"].startswith("SKIP:") and "IN_PROGRESS" in states["S0-04"]
    assert [r for r, _ in apply_all(world, data)].count("S0-04") == 0


def test_a_missing_ticket_is_reported_not_crashed(world):
    data = s0.load_manifest()
    world.execute("SET session_replication_role = replica")
    world.execute("DELETE FROM backlog.BACKLOG WHERE action_point_id = 'FND-0185'")
    world.execute("SET session_replication_role = DEFAULT")
    states, _ = run_all(world, data)
    assert states["S0-12"] == "SKIP: FND-0185 does not exist"


def test_the_link_rows_respect_the_cycle_guard(world):
    """A DEPENDS_ON the other way would be a cycle: step 0 must fail loud on that row and leave the rest to apply."""
    bc.record_link_event(from_id="FND-0236", to_id="FND-0203", link_type="DEPENDS_ON", status="CONFIRMED", evidence="a prior decision", username=ME, conn=world)
    data = s0.load_manifest()
    ctx = s0.build_context(data["rows"], bc, conn=world)
    row = next(r for r in data["rows"] if r["id"] == "S0-01")
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation, match="cycle"):
        with world.transaction():
            s0.apply_row(row, bc, actor=ME, deployed_on=DEPLOYED, revisit_days=90, conn=world)
    assert s0.state_of(row, ctx) == "TODO"
