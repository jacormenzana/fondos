"""FND-0239: the telemetry DDL (db/pg/35_control.sql, loaded into the hermetic container) and shared/cycle_telemetry.py on a real Postgres.

Uses pg_conn (a SAVEPOINT connection that is rolled back), so nothing persists. Run: python scripts/ops/run_pg_tests.py
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

_spec = importlib.util.spec_from_file_location("migrate_cycle_telemetry", _ROOT / "scripts" / "ops" / "migrate_cycle_telemetry.py")
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)

T0 = datetime(2026, 10, 7, 8, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_repo_fallback(monkeypatch, tmp_path):
    """A failing emit must never write into the repo's log/ directory from a test."""
    monkeypatch.setenv(ct.ENV_FALLBACK, str(tmp_path / "fallback.jsonl"))
    monkeypatch.delenv(ct.ENV_CYCLE_ID, raising=False)


def _iso(minutes=0):
    return (T0 + timedelta(minutes=minutes)).isoformat()


def _cycle(conn, cid, launcher="P1_P2_P3"):
    assert ct.begin_cycle(launcher, {"force": True}, cycle_id=cid, conn=conn, ts=_iso()) is True


def _step(conn, cid, code, start_min, dur_min, rc=0, attempt=1, **kw):
    assert ct.step_begin(code, cycle_id=cid, conn=conn, ts=_iso(start_min), attempt=attempt) is True
    assert ct.step_end(code, rc, started_at=_iso(start_min), cycle_id=cid, conn=conn, ts=_iso(start_min + dur_min), attempt=attempt, **kw) is True


# ---------------------------------------------------------------- the DDL
def test_the_ddl_block_is_what_the_migration_script_applies_and_it_is_idempotent(pg_conn):
    block = mig.ddl_block()
    assert block.startswith("-- BEGIN cycle_telemetry") and block.rstrip().endswith("-- END cycle_telemetry")
    have = mig.existing(pg_conn)
    assert have["tables"] == sorted(mig.TABLES) and have["view"] is True
    mig.migrate(pg_conn)                              # second run: nothing breaks, seeds untouched
    n = pg_conn.execute("SELECT count(*) FROM control.cycle_step_def").fetchone()[0]
    mig.migrate(pg_conn)
    assert pg_conn.execute("SELECT count(*) FROM control.cycle_step_def").fetchone()[0] == n == 14


def test_seeds_carry_the_data_derived_ceilings_and_survive_tuning(pg_conn):
    pg_conn.execute("UPDATE control.cycle_step_def SET hard_max_s = 111 WHERE step_code = 'P2_CALC'")
    mig.migrate(pg_conn)                              # ON CONFLICT DO NOTHING: a tuned threshold is never overwritten
    assert pg_conn.execute("SELECT hard_max_s FROM control.cycle_step_def WHERE step_code = 'P2_CALC'").fetchone()[0] == 111
    assert pg_conn.execute("SELECT hard_max_s FROM control.cycle_step_def WHERE step_code = 'P1_CLASSIFY'").fetchone()[0] == 11520


# ---------------------------------------------------------------- events
def test_every_event_is_idempotent_so_the_fallback_can_be_replayed_twice(pg_conn):
    cid = "20261007_080000"
    ev = [{"op": "cycle_begin", "cycle_id": cid, "ts": _iso(), "launcher": "L", "options": {"a": 1}},
          {"op": "step_begin", "cycle_id": cid, "ts": _iso(1), "step_code": "P2_CALC"},
          {"op": "step_end", "cycle_id": cid, "ts": _iso(31), "step_code": "P2_CALC", "started_at": _iso(1), "rc": 0},
          {"op": "metric", "cycle_id": cid, "ts": _iso(31), "step_code": "P2_CALC", "metric_code": "ols_funds", "scope": "", "value_num": 12.0},
          {"op": "flag", "cycle_id": cid, "ts": _iso(32), "flag_code": "F", "severity": "WARN", "scope": ""}]
    for _ in range(2):
        for e in ev:
            ct._apply(pg_conn, e)
    q = lambda t: pg_conn.execute(f"SELECT count(*) FROM control.{t} WHERE cycle_id = %s", (cid,)).fetchone()[0]
    assert (q("cycle_run"), q("cycle_step"), q("cycle_metric"), q("cycle_flag")) == (1, 1, 1, 1)
    assert pg_conn.execute("SELECT dur_s, status FROM control.cycle_step WHERE cycle_id = %s", (cid,)).fetchone() == (1800.0, "OK")


def test_a_lost_begin_event_leaves_a_stub_cycle_that_a_late_begin_completes(pg_conn):
    cid = "20261007_090000"
    ct._apply(pg_conn, {"op": "metric", "cycle_id": cid, "ts": _iso(), "step_code": "P2_CALC", "metric_code": "p2_errors", "value_num": 3.0})
    assert pg_conn.execute("SELECT launcher FROM control.cycle_run WHERE cycle_id = %s", (cid,)).fetchone()[0] == "UNKNOWN"
    ct._apply(pg_conn, {"op": "cycle_begin", "cycle_id": cid, "ts": _iso(), "launcher": "P1_P2_Complete", "options": {}})
    assert pg_conn.execute("SELECT launcher FROM control.cycle_run WHERE cycle_id = %s", (cid,)).fetchone()[0] == "P1_P2_Complete"


def test_a_late_step_begin_replay_does_not_regress_a_finished_step(pg_conn):
    cid = "20261007_100000"
    _cycle(pg_conn, cid)
    _step(pg_conn, cid, "P1_BENCH", 1, 5)
    ct._apply(pg_conn, {"op": "step_begin", "cycle_id": cid, "ts": _iso(99), "step_code": "P1_BENCH"})      # replayed after its end
    assert pg_conn.execute("SELECT status, dur_s FROM control.cycle_step WHERE cycle_id = %s", (cid,)).fetchone() == ("OK", 300.0)


# ---------------------------------------------------------------- baselines at ingest
def test_step_baseline_waits_for_three_ok_attempts_and_a_slow_run_is_warned(pg_conn):
    for i, dur in enumerate((30, 31, 29)):                                  # three normal P2_CALC runs on three days
        cid = f"2026100{i + 1}_080000"
        ct.begin_cycle("L", cycle_id=cid, conn=pg_conn, ts=(T0 + timedelta(days=i - 10)).isoformat())
        ct.step_begin("P2_CALC", cycle_id=cid, conn=pg_conn, ts=(T0 + timedelta(days=i - 10)).isoformat())
        ct.step_end("P2_CALC", 0, started_at=(T0 + timedelta(days=i - 10)).isoformat(), cycle_id=cid, conn=pg_conn,
                    ts=(T0 + timedelta(days=i - 10, minutes=dur)).isoformat())
    rows = pg_conn.execute("SELECT cycle_id, baseline_s, ratio, status FROM control.cycle_step WHERE step_code = 'P2_CALC' "
                           "AND cycle_id LIKE '2026100%%' ORDER BY cycle_id").fetchall()
    assert [r[1] for r in rows] == [None, None, None]                                                   # warm-up: < 3 prior OK attempts each
    cid = "20261007_110000"
    _cycle(pg_conn, cid)
    _step(pg_conn, cid, "P2_CALC", 0, 95)                                                               # 95 min against a ~30 min median
    base, ratio, status = pg_conn.execute("SELECT baseline_s, ratio, status FROM control.cycle_step WHERE cycle_id = %s", (cid,)).fetchone()
    assert base == pytest.approx(30 * 60, rel=0.05) and ratio > 3 and status == "WARN"


def test_the_hard_ceiling_fires_in_the_very_first_cycle(pg_conn):
    cid = "20261007_120000"
    _cycle(pg_conn, cid)
    pg_conn.execute("UPDATE control.cycle_step_def SET hard_max_s = 600 WHERE step_code = 'P1_BENCH'")
    _step(pg_conn, cid, "P1_BENCH", 0, 20)                                                              # 1,200 s > 600 s, no baseline exists
    assert pg_conn.execute("SELECT status, baseline_s FROM control.cycle_step WHERE cycle_id = %s", (cid,)).fetchone() == ("WARN", None)


def test_a_failed_step_is_failed_and_does_not_enter_a_later_baseline(pg_conn):
    cid = "20261007_130000"
    _cycle(pg_conn, cid)
    _step(pg_conn, cid, "P1_CLASSIFY", 0, 10, rc=1)
    assert pg_conn.execute("SELECT status FROM control.cycle_step WHERE cycle_id = %s", (cid,)).fetchone()[0] == "FAILED"


def test_metric_baseline_is_the_median_of_the_previous_cycles(pg_conn):
    for i, v in enumerate((100.0, 110.0, 90.0, 105.0)):
        cid = f"2026090{i + 1}_000000"
        ct.record_metric("P2_CALC", "p2_written_rows", v, cycle_id=cid, conn=pg_conn)
    base, ratio = pg_conn.execute("SELECT baseline_value, ratio FROM control.cycle_metric WHERE cycle_id = '20260904_000000'").fetchone()
    assert base == 100.0 and ratio == pytest.approx(1.05)                                              # median(100, 110, 90)
    assert pg_conn.execute("SELECT baseline_value FROM control.cycle_metric WHERE cycle_id = '20260903_000000'").fetchone()[0] is None   # 2 prior: warm-up


# ---------------------------------------------------------------- flags and the executive view
def test_the_view_has_no_steps_times_flags_fan_out(pg_conn):
    cid = "20261007_140000"
    _cycle(pg_conn, cid)
    for i, code in enumerate(("P1_BENCH", "P1_CLASSIFY", "P2_CALC")):
        _step(pg_conn, cid, code, i * 10, 5)
    for code in ("A", "B", "C"):
        assert ct.raise_flag(code, "HIGH", cycle_id=cid, conn=pg_conn, ts=_iso(60)) is True
    ct.record_metric("P2_CALC", "ols_funds", 7.0, cycle_id=cid, conn=pg_conn)
    ct.record_metric("P2_CALC", "cache_hit_pct", 91.5, cycle_id=cid, conn=pg_conn)
    row = pg_conn.execute("SELECT flags_high, flags_orphan, high_flags, p2_calc_min, ols_funds, cache_hit_pct FROM control.v_cycle_exec "
                          "WHERE cycle_id = %s", (cid,)).fetchall()
    assert len(row) == 1 and row[0][0] == 3 and row[0][1] == 3 and row[0][2] == "A,B,C"                  # exactly 3, not 9
    assert row[0][3] == pytest.approx(5.0) and row[0][4] == 7.0 and row[0][5] == 91.5


def test_orphan_semantics_a_lookup_failure_is_not_an_orphan(pg_conn):
    cid = "20261007_150000"
    _cycle(pg_conn, cid)
    status_of = {"FND-OPEN": "IN_PROGRESS", "FND-CLOSED": "CLOSED", "FND-GONE": "NOT_FOUND", "FND-DOWN": "LOOKUP_FAILED"}
    ct.raise_flag("TRACKED", "WARN", ap_id="FND-OPEN", cycle_id=cid, conn=pg_conn, lookup=status_of.get)
    ct.raise_flag("RECURRED", "WARN", ap_id="FND-CLOSED", cycle_id=cid, conn=pg_conn, lookup=status_of.get)
    ct.raise_flag("MISSING", "WARN", ap_id="FND-GONE", cycle_id=cid, conn=pg_conn, lookup=status_of.get)
    ct.raise_flag("UNKNOWN", "WARN", ap_id="FND-DOWN", cycle_id=cid, conn=pg_conn, lookup=status_of.get)
    ct.raise_flag("NOAP", "HIGH", cycle_id=cid, conn=pg_conn)
    ct.raise_flag("INFO_ONLY", "INFO", cycle_id=cid, conn=pg_conn)
    orphan, unverified = pg_conn.execute("SELECT flags_orphan, flags_unverified FROM control.v_cycle_exec WHERE cycle_id = %s", (cid,)).fetchone()
    assert (orphan, unverified) == (3, 1)                  # RECURRED (CLOSED), MISSING (NOT_FOUND), NOAP; INFO ignored; DOWN is unverified, not orphan


def test_a_standing_ack_supplies_the_ap_and_refresh_resolves_its_status(pg_conn):
    cid = "20261007_160000"
    _cycle(pg_conn, cid)
    pg_conn.execute("INSERT INTO control.cycle_flag_ack (flag_code, scope, ap_id, note, expires_at) VALUES ('ALARM_STAGNANT', '', 'FND-0034', 'x', current_date + 30)")
    pg_conn.execute("INSERT INTO control.cycle_flag_ack (flag_code, scope, ap_id, note, expires_at) VALUES ('OLD', '', 'FND-0001', 'x', current_date - 1)")
    ct.raise_flag("ALARM_STAGNANT", "WARN", cycle_id=cid, conn=pg_conn)
    ct.raise_flag("OLD", "WARN", cycle_id=cid, conn=pg_conn)                                          # an expired ack does not apply
    got = dict(((r[0]), (r[1], r[2])) for r in pg_conn.execute("SELECT flag_code, ap_id, ap_status FROM control.cycle_flag WHERE cycle_id = %s", (cid,)).fetchall())
    assert got["ALARM_STAGNANT"] == ("FND-0034", "LOOKUP_FAILED") and got["OLD"] == (None, None)
    assert ct.refresh_unverified_flags(pg_conn, lookup=lambda ap: "IN_PROGRESS") == 1
    assert pg_conn.execute("SELECT ap_status FROM control.cycle_flag WHERE cycle_id = %s AND flag_code = 'ALARM_STAGNANT'", (cid,)).fetchone()[0] == "IN_PROGRESS"


def test_cycle_end_records_the_outcome_and_the_view_reports_it(pg_conn):
    cid = "20261007_170000"
    _cycle(pg_conn, cid)
    _step(pg_conn, cid, "P2_CALC", 0, 3)
    _step(pg_conn, cid, "P3_BUILD", 5, 2, rc=2)
    assert ct.end_cycle("FAILED", 2, cycle_id=cid, conn=pg_conn, ts=_iso(60), failed_step="P3_BUILD", regime="Expansion") is True
    row = pg_conn.execute("SELECT status, failed_step, steps_failed, regime, total_h FROM control.v_cycle_exec WHERE cycle_id = %s", (cid,)).fetchone()
    assert row[:4] == ("FAILED", "P3_BUILD", 1, "Expansion") and float(row[4]) == pytest.approx(1.0)
