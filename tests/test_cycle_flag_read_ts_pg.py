"""FND-0332 (decision-time read rule): control.cycle_flag.ap_status_read_ts on a real Postgres.

The block `cycle_flag_read_ts` of db/pg/35_control.sql is loaded by the hermetic container with the rest of the file; the tests also drop the column
to prove the writer still works before the owner applies it. pg_conn is a rolled-back SAVEPOINT connection: nothing persists.
Run: python scripts/ops/run_pg_tests.py -- tests/test_cycle_flag_read_ts_pg.py
"""
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import cycle_telemetry as ct  # noqa: E402

_spec = importlib.util.spec_from_file_location("migrate_cycle_flag_read_ts", _ROOT / "scripts" / "ops" / "migrate_cycle_flag_read_ts.py")
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)

CID = "20261030_100000"


@pytest.fixture(autouse=True)
def _no_repo_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv(ct.ENV_FALLBACK, str(tmp_path / "fallback.jsonl"))
    monkeypatch.delenv(ct.ENV_CYCLE_ID, raising=False)


@pytest.fixture
def cyc(pg_conn):
    assert ct.begin_cycle("P1P2_runCycle", {}, cycle_id=CID, conn=pg_conn) is True
    return pg_conn


def flag(conn, code):
    return conn.execute("SELECT ap_id, ap_status, ap_status_read_ts FROM control.cycle_flag WHERE cycle_id = %s AND flag_code = %s",
                        (CID, code)).fetchone()


def test_block_and_migration_are_idempotent(pg_conn):
    assert mig.existing(pg_conn) == {"table": True, "read_ts_column": True}
    mig.migrate(pg_conn)
    mig.migrate(pg_conn)
    assert mig.existing(pg_conn)["read_ts_column"] is True


def test_a_flag_stores_when_its_status_was_read(cyc):
    before = datetime.now(timezone.utc)
    assert ct.raise_flag("F_READ", "WARN", ap_id="FND-0001", cycle_id=CID, conn=cyc, lookup=lambda ap: "OPEN") is True
    ap, st, ts = flag(cyc, "F_READ")
    assert (ap, st) == ("FND-0001", "OPEN") and before - timedelta(seconds=1) <= ts <= datetime.now(timezone.utc) + timedelta(seconds=1)
    assert ct.raise_flag("F_GONE", "WARN", ap_id="FND-9999", cycle_id=CID, conn=cyc, lookup=lambda ap: "NOT_FOUND") is True
    assert flag(cyc, "F_GONE")[2] is not None                         # the backlog answered: that read has a time too


def test_a_failed_lookup_or_no_ticket_has_no_read_time(cyc):
    ct.raise_flag("F_FAIL", "WARN", ap_id="FND-0001", cycle_id=CID, conn=cyc, lookup=lambda ap: "LOOKUP_FAILED")
    ct.raise_flag("F_NONE", "INFO", cycle_id=CID, conn=cyc)
    assert flag(cyc, "F_FAIL") == ("FND-0001", "LOOKUP_FAILED", None)
    assert flag(cyc, "F_NONE") == (None, None, None)


def test_refresh_stamps_the_read_time_of_what_it_resolves(cyc):
    ct.raise_flag("F_LATE", "WARN", ap_id="FND-0002", cycle_id=CID, conn=cyc, lookup=lambda ap: "LOOKUP_FAILED")
    assert ct.refresh_unverified_flags(cyc, lookup=lambda ap: "CLOSED") == 1
    ap, st, ts = flag(cyc, "F_LATE")
    assert st == "CLOSED" and ts is not None


def test_the_read_time_survives_the_fallback_replay(cyc):
    read = "2026-10-30T09:00:00+00:00"
    ev = ct._event("flag", CID, flag_code="F_REPLAY", severity="WARN", ap_id="FND-0003", ap_status="TODO", ap_status_read_ts=read)
    ct.emit(ev, cyc)                                                  # what replay_fallback applies, hours later
    assert flag(cyc, "F_REPLAY")[2] == datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc)


def test_before_the_owner_applies_the_column_flags_are_written_as_before(cyc):
    cyc.execute("ALTER TABLE control.cycle_flag DROP COLUMN ap_status_read_ts")
    assert ct.raise_flag("F_OLD", "WARN", ap_id="FND-0001", cycle_id=CID, conn=cyc, lookup=lambda ap: "OPEN") is True
    assert cyc.execute("SELECT ap_status FROM control.cycle_flag WHERE flag_code = 'F_OLD'").fetchone()[0] == "OPEN"
    ct.raise_flag("F_OLD2", "WARN", ap_id="FND-0001", cycle_id=CID, conn=cyc, lookup=lambda ap: "LOOKUP_FAILED")
    assert ct.refresh_unverified_flags(cyc, lookup=lambda ap: "OPEN") == 1


def test_the_cycle_report_collector_classifies_the_latest_cycle(cyc, monkeypatch):
    sys.path.insert(0, str(_ROOT / "scripts" / "launch"))
    import p1p2_cycle_report as cr
    from shared import backlog_client
    ct.raise_flag("F_A", "WARN", ap_id="FND-0001", cycle_id=CID, conn=cyc, lookup=lambda ap: "OPEN")
    ct.raise_flag("F_B", "WARN", ap_id="FND-0002", cycle_id=CID, conn=cyc, lookup=lambda ap: "OPEN")
    later = datetime.now(timezone.utc) + timedelta(minutes=5)
    monkeypatch.setattr(backlog_client, "latest_status_events", lambda ids, **kw: {
        "FND-0001": {"status": "CLOSED", "event_ts": later, "origin": "LIVE"},             # moved after the read: stale
        "FND-0002": {"status": "OPEN", "event_ts": later - timedelta(days=2), "origin": "LIVE"}})
    out = cr.collect_stale_reads(cyc)
    assert out["applied"] is True and out["checked"] == 2 and [s["ap_id"] for s in out["stale"]] == ["FND-0001"]
    assert isinstance(out["stale"][0]["read_ts"], str)                                      # JSON-ready
    cyc.execute("ALTER TABLE control.cycle_flag DROP COLUMN ap_status_read_ts")
    assert cr.collect_stale_reads(cyc) == {"applied": False}
