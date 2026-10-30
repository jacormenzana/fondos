# tests/test_backlog_status_read_pg.py
# -*- coding: utf-8 -*-
"""FND-0332 (decision-time read rule): shared.backlog_client.latest_status_events() on the Phase A store. Hermetic (rolled-back savepoint).

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_status_read_pg.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import migrate_backlog_v2 as mig  # noqa: E402
from shared import backlog_client as bc  # noqa: E402
from test_backlog_transitions_pg import _base, _mk, tr  # noqa: E402


def test_latest_status_event_per_ticket(pg_conn):
    _base(pg_conn)
    old = _mk(pg_conn, "TODO")
    mig.migrate(pg_conn, "transitions")                     # `old` gets its BACKFILL event
    moved = _mk(pg_conn)
    first = pg_conn.execute("SELECT clock_timestamp()").fetchone()[0]
    tr(pg_conn, moved, "IN_PROGRESS")
    tr(pg_conn, moved, "BLOCKED", blocker=old)
    got = bc.latest_status_events([old, moved, "FND-0000"], conn=pg_conn)
    assert set(got) == {old, moved}                         # an unknown id is simply absent
    assert got[old]["status"] == "TODO" and got[old]["origin"] == "BACKFILL"
    assert got[moved]["status"] == "BLOCKED" and got[moved]["origin"] == "LIVE" and got[moved]["event_ts"] > first
    last = pg_conn.execute("SELECT max(event_ts) FROM backlog.status_event WHERE action_point_id = %s", (moved,)).fetchone()[0]
    assert got[moved]["event_ts"] == last
    assert bc.latest_status_events([], conn=pg_conn) == {}
