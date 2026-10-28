# tests/test_backlog_incident_overlap_pg.py
# -*- coding: utf-8 -*-
"""report_incident with the two-tier overlap policy (owner safeguards S-2 / D-1) against the real backlog DDL.

Hermetic: the throwaway container inside a rolled-back savepoint (pg_conn). report_incident opens its own connection, so the
tests hand it the fixture connection through a wrapper that never commits or closes it.

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_incident_overlap_pg.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import migrate_backlog_links as links  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402
from shared import backlog_client as bc  # noqa: E402

SCHEMA_SQL = (_ROOT / "db" / "pg_backlog" / "00_schema.sql").read_text(encoding="utf-8")
SAME = ("psycopg.errors.UndefinedColumn: column real_flag does not exist in fund_metric_timeseries while the P2 writer "
        "persisted the rolling rows of the nature panel")


class _Borrowed:
    """Stands in for `psycopg.connect(...)`: hands out the fixture connection, never commits or closes it."""

    def __init__(self, conn):
        self._c = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._c.cursor()

    def commit(self):
        pass


@pytest.fixture
def db(pg_conn, monkeypatch, tmp_path):
    """Production as it is: step A-1 applied (the legacy raw INSERTs of report_incident were removed on the owner's order of 2026-10-28). The fallback log is a temporary file, never the repository's."""
    pg_conn.execute(SCHEMA_SQL)
    links.migrate(pg_conn)
    mig.migrate(pg_conn, "alias")
    mig.migrate(pg_conn, "transitions")
    monkeypatch.setenv("FONDOS_BACKLOG_FALLBACK_LOG", str(tmp_path / "fallback.jsonl"))
    pg_conn.execute(
        "INSERT INTO backlog.BACKLOG (action_point_id, title, project_code, nature, object_type, object_name, status, priority, "
        "creation_ts, scenario_description) VALUES ('FND-9001', 'writer fails', 'FND', 'BUG', 'JOB', 'run_pipeline.py', 'OPEN', "
        "'HIGH', now() - interval '10 days', %s)", (SAME,))
    monkeypatch.setenv("FONDOS_BACKLOG_PG_DSN", "postgresql://unused")
    monkeypatch.setattr(bc, "psycopg", SimpleNamespace(connect=lambda *a, **k: _Borrowed(pg_conn)))
    return pg_conn


def _tickets(conn):
    return [r[0] for r in conn.execute("SELECT action_point_id FROM backlog.BACKLOG ORDER BY 1").fetchall()]


def _logs(conn, apid):
    return [r[0] for r in conn.execute("SELECT comment FROM backlog.BACKLOG_LOGS WHERE action_point_id = %s ORDER BY log_id",
                                       (apid,)).fetchall()]


def test_policy_off_keeps_todays_behaviour(db, monkeypatch):
    monkeypatch.setattr(bc, "_overlap_settings", lambda: None)
    apid = bc.report_incident(object_name="run_pipeline.py", scenario_description=SAME + " (again)")
    assert apid != "FND-9001" and len(_tickets(db)) == 2           # the 24 h window does not see the 10-day-old ticket


def test_high_confidence_appends_to_the_open_ticket_with_the_reason(db, monkeypatch):
    monkeypatch.setattr(bc, "_overlap_settings", lambda: (0.70, 0.45))
    apid = bc.report_incident(object_name="run_pipeline.py", scenario_description=SAME + " (again)")
    assert apid == "FND-9001" and _tickets(db) == ["FND-9001"]
    log = _logs(db, "FND-9001")[-1]
    assert "overlap" in log and "same object_name" in log and "(again)" in log


def test_medium_confidence_opens_a_new_ticket_and_only_suggests_the_related_one(db, monkeypatch):
    monkeypatch.setattr(bc, "_overlap_settings", lambda: (0.70, 0.45))
    apid = bc.report_incident(object_name="nav_discovery.py", scenario_description=SAME)
    assert apid != "FND-9001" and len(_tickets(db)) == 2
    first = _logs(db, apid)[0]
    assert "Possibly related (NOT merged): FND-9001" in first


def test_an_unrelated_incident_is_a_plain_new_ticket(db, monkeypatch):
    monkeypatch.setattr(bc, "_overlap_settings", lambda: (0.70, 0.45))
    apid = bc.report_incident(object_name="macro_discovery.py",
                              scenario_description="ConnectionError: HTTPSConnectionPool host api.stlouisfed.org timed out")
    assert apid != "FND-9001"
    assert "Possibly related" not in _logs(db, apid)[0]


def test_a_failing_overlap_check_never_blocks_the_incident(db, monkeypatch):
    import shared.backlog_overlap as O
    monkeypatch.setattr(bc, "_overlap_settings", lambda: (0.70, 0.45))
    monkeypatch.setattr(O, "classify_incident", lambda **kw: (_ for _ in ()).throw(RuntimeError("scorer exploded")))
    apid = bc.report_incident(object_name="run_pipeline.py", scenario_description=SAME + " (again)")
    assert apid is not None and apid != "FND-9001" and len(_tickets(db)) == 2
