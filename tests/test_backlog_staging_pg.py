# tests/test_backlog_staging_pg.py
# -*- coding: utf-8 -*-
"""The staging scenarios of Backlog v2 (FND-0351; NORMAS_BACKLOG.md 9): the state machine and the gate verified on a SCRATCH Postgres that holds a snapshot of the backlog, never on the live one.

    python scripts/ops/backlog_staging.py                              # one command: a fresh read-only snapshot, the throwaway container, the real DDL, every scenario, teardown
    python scripts/ops/backlog_staging.py --scenario a2_revoke         # one scenario
    python scripts/ops/run_pg_tests.py -- tests/test_backlog_staging_pg.py   # the scenarios on the built-in synthetic snapshot (what CI runs)

Environment (set by the command above; all optional): FONDOS_STAGING_SNAPSHOT (a snapshot file: the real history), FONDOS_STAGING_PHASES (comma list of the phases of scripts/ops/migrate_backlog_v2.py applied
in this order; default transitions,relations). Every scenario calls assert_scratch BEFORE its first statement (shared/backlog_scratch.py): it refuses on any live target, whatever its name.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg
import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(_ROOT / "scripts" / "audit"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_phase_b_replay as rp  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402
from shared.backlog_scratch import assert_scratch  # noqa: E402
from test_backlog_relations_pg import REL, blockers, close, enforce, link, rfd  # noqa: E402
from test_backlog_transitions_pg import FORCE_REASON, OWNER, _base, _mk, refused, status, tr  # noqa: E402

SNAPSHOT_ENV, PHASES_ENV = "FONDOS_STAGING_SNAPSHOT", "FONDOS_STAGING_PHASES"
DEFAULT_PHASES = "transitions,relations"
WHO = "backlog_staging"


def synthetic_snapshot() -> dict:
    """A small history with the shapes the scenarios need: open, in-progress and closed tickets, one confirmed dependency between closed ones. Used when no real snapshot is given."""
    def t(n, st="OPEN"):
        row = {"action_point_id": f"FND-{n:04d}", "title": f"synthetic ticket {n}", "project_code": "FND", "nature": "BUG", "object_type": "N/A", "status": st, "priority": "LOW",
               "session_id": "FND_20261031_DEV_STAGING"}
        if st == "CLOSED":
            row.update(solution_type="REJECTED", closure_ts="2026-10-01T10:00:00+00:00")
        return row
    tickets = [t(n, "OPEN") for n in range(100, 175)] + [t(n, "IN_PROGRESS") for n in range(200, 210)] + [t(n, "CLOSED") for n in range(300, 305)]
    return {"tickets": tickets, "link_events": []}


def load_snapshot_for_staging() -> tuple:
    """(snapshot, is_real)."""
    path = os.environ.get(SNAPSHOT_ENV)
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8")), True
    return synthetic_snapshot(), False


@pytest.fixture
def staged(pg_conn):
    """A scratch database with the real DDL of the chosen phases and the snapshot loaded. The guard runs FIRST."""
    assert_scratch(pg_conn, who=WHO)
    _base(pg_conn)
    for phase in os.environ.get(PHASES_ENV, DEFAULT_PHASES).split(","):
        mig.migrate(pg_conn, phase.strip())
    snap, real = load_snapshot_for_staging()
    rp.load_snapshot(pg_conn, snap)
    pg_conn.staging_real = real                                                           # noqa: B010  (a note for the scenarios)
    return pg_conn


def clean_in_progress(conn, n: int) -> list:
    """n tickets IN_PROGRESS that no confirmed link touches (real ones when the snapshot has them; synthetic ones fill the rest)."""
    rows = conn.execute("SELECT b.action_point_id FROM backlog.BACKLOG b WHERE b.status = 'IN_PROGRESS' AND NOT EXISTS (SELECT 1 FROM backlog.v_backlog_link l WHERE l.status = 'CONFIRMED' "
                        "AND (l.from_id = b.action_point_id OR l.to_id = b.action_point_id)) ORDER BY 1 LIMIT %s", (n,)).fetchall()
    ids = [r[0] for r in rows]
    while len(ids) < n:
        ids.append(_mk(conn, "IN_PROGRESS"))
    return ids


# ---------------------------------------------------------------- the guard is the first thing every scenario meets

def test_a_live_looking_database_is_refused_before_anything_runs():
    class Info:
        host, port, dbname = "localhost", 5436, "gestion"

    class Conn:
        info = Info()
    with pytest.raises(RuntimeError, match="refuses to run"):
        assert_scratch(Conn(), {"FONDOS_BACKLOG_PG_DSN": "postgresql://u:p@127.0.0.1:5436/gestion"}, who=WHO)


def test_the_staging_fixture_is_a_scratch_database(staged):
    assert_scratch(staged, who=WHO)
    assert staged.execute("SELECT count(*) FROM backlog.BACKLOG").fetchone()[0] > 50


# ---------------------------------------------------------------- the gate: clean, blocked, forced, audit window

def test_scenario_gate_clean(staged):
    enforce(staged)
    x, = clean_in_progress(staged, 1)
    assert blockers(staged, x) == []
    rfd(staged, x)
    close(staged, x)
    assert status(staged, x) == "CLOSED"


def test_scenario_gate_blocked(staged):
    enforce(staged)
    x, y = clean_in_progress(staged, 2)
    link(staged, x, y, "DEPENDS_ON")
    with pytest.raises(psycopg.errors.Error, match="delivery gate") as e:
        with staged.transaction():
            rfd(staged, x)
    assert y in str(e.value) and "PREREQUISITE" in str(e.value)
    assert status(staged, x) == "IN_PROGRESS" and blockers(staged, x) == [{"kind": "PREREQUISITE", "ticket": y, "status": "IN_PROGRESS"}]
    link(staged, x, y, "DEPENDS_ON", "REJECTED")                                          # the dependency is lifted by an event, never by an edit
    assert blockers(staged, x) == []
    rfd(staged, x)


def test_scenario_gate_forced(staged):
    enforce(staged)
    x, y = clean_in_progress(staged, 2)
    link(staged, x, y, "DEPENDS_ON")
    refused(staged, "delivery gate", rfd, x)
    rfd(staged, x, force=True, comment=FORCE_REASON)                                      # the exception path: a reason of 40+ characters, recorded
    assert status(staged, x) == "READY_FOR_DEPLOY"
    assert staged.execute("SELECT count(*) FROM backlog.status_event WHERE action_point_id = %s AND forced", (x,)).fetchone()[0] == 1


def test_scenario_gate_audit_window(staged):
    enforce(staged)
    staged.execute("SELECT backlog.sp_set_gate('DELIVERY', 'AUDIT', %s, 'the owner puts the gate back in audit mode for the window')", (OWNER,))
    x, y = clean_in_progress(staged, 2)
    link(staged, x, y, "DEPENDS_ON")
    rfd(staged, x)                                                                        # AUDIT: nothing is refused ...
    assert status(staged, x) == "READY_FOR_DEPLOY"
    logged = staged.execute("SELECT blockers FROM backlog.gate_audit_event WHERE action_point_id = %s ORDER BY event_id DESC LIMIT 1", (x,)).fetchone()
    assert logged and logged[0] and logged[0][0]["ticket"] == y                           # ... but what WOULD have blocked is logged


# ---------------------------------------------------------------- the A-2 revoke, rehearsed on the real population

def test_scenario_a2_revoke_rehearsal(staged):
    """The pending live step (`migrate_backlog_v2.py --phase revoke`) rehearsed: the gate stays closed under 50 real transitions, opens at 50, and after the revoke the writer can no longer update
    the governed columns while the routines keep working."""
    openers = [r[0] for r in staged.execute("SELECT action_point_id FROM backlog.BACKLOG WHERE status = 'OPEN' ORDER BY 1").fetchall()]
    openers += [_mk(staged, "OPEN") for _ in range(max(0, mig.SHADOW_REQUIRED - len(openers)))]
    base = mig.shadow_stage(staged)["live_non_system_since_last_v00"]
    assert mig.revoke_gate(mig.shadow_stage(staged))[0] is (base >= mig.SHADOW_REQUIRED)
    for ap in openers[: mig.SHADOW_REQUIRED - 1 - base]:
        tr(staged, ap, "TODO")
    assert mig.revoke_gate(mig.shadow_stage(staged))[0] is False                          # 49: closed
    tr(staged, openers[mig.SHADOW_REQUIRED - 1 - base], "TODO")
    allowed, msg = mig.revoke_gate(mig.shadow_stage(staged))
    assert allowed is True and f"{mig.SHADOW_REQUIRED} of {mig.SHADOW_REQUIRED}" in msg   # 50: open
    mig.migrate(staged, "revoke")
    assert staged.execute("SELECT has_column_privilege('backlog_writer', 'backlog.backlog', 'status', 'UPDATE')").fetchone()[0] is False
    assert staged.execute("SELECT has_table_privilege('backlog_writer', 'backlog.backlog', 'INSERT')").fetchone()[0] is False
    y = clean_in_progress(staged, 1)[0]
    assert tr(staged, y, "READY_FOR_DEPLOY", release_version=REL) > 0                     # the routines still move tickets


# ---------------------------------------------------------------- the replay of the real history (needs the real snapshot)

def test_scenario_replay_of_the_real_history(staged):
    if not staged.staging_real:
        pytest.skip("needs a real snapshot (python scripts/ops/backlog_staging.py exports a fresh one)")
    assert "relations" in os.environ.get(PHASES_ENV, DEFAULT_PHASES)
    report = {"links_as_of_today": rp.replay(staged), "links_as_of_closure": rp.replay(staged, links_as_of_closure=True), "audit_mode": rp.replay(staged, mode="AUDIT")}
    failed = [(n, d) for n, ok, d in rp.evaluate(report) if not ok]
    assert failed == [], failed
