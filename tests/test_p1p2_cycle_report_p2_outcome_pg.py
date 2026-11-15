"""
tests/test_p1p2_cycle_report_p2_outcome_pg.py -- FND-0327 on a real Postgres: the P2 outcome of a cycle that CROSSES MIDNIGHT, read in a UTC session.

The cycle started at 23:57 local on 11-14 and P2 wrote its RUN_SUMMARY at 00:17 local on 11-15. A date comparison, or a naive datetime read in a session whose TimeZone is UTC,
would shift the window by an hour or mix this cycle with the earlier one of the same date. pg_app_conn = a SAVEPOINT connection with the application search_path, rolled back,
so nothing persists. Run: python scripts/gov/delivery/run_pg_tests.py
"""
import importlib.util
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("p1p2_cycle_report_p2_pg", _ROOT / "scripts" / "launch" / "p1p2_cycle_report.py")
cr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cr)

MADRID = ZoneInfo("Europe/Madrid")
SUMMARY = ("run_id={rid} processed={p} skipped={s} errors=0 warnings=4 defl_skipped=0 written=2115 quarantined=3 ols_funds=0 "
           "cond_guard_funds=0 betas_nulled=0 elapsed=360s")


def _put(conn, step, message, at, status="OK"):
    conn.execute("INSERT INTO p2_pipeline_log (isin, step, status, metric_version, message, created_at) VALUES (NULL, %s, %s, 'v', %s, %s)",
                 (step, status, message, at))


def test_a_cycle_crossing_midnight_reads_its_own_summary_in_a_utc_session(pg_app_conn):
    conn = pg_app_conn
    conn.execute("SET TIME ZONE 'UTC'")                                                 # the session zone must not matter
    _put(conn, "RUN_SUMMARY", SUMMARY.format(rid="EARLIER", p=500, s=3200), datetime(2026, 11, 14, 1, 30, tzinfo=MADRID))   # the earlier cycle of the same date
    _put(conn, "RUN_SUMMARY", SUMMARY.format(rid="THIS", p=122, s=3621), datetime(2026, 11, 15, 0, 17, tzinfo=MADRID))

    start, from_stamp = cr.cycle_start_from_stamp("20261114_235743", date(2026, 11, 14))
    out = cr.collect_p2_outcome(conn, start, from_stamp)

    assert out["state"] == "recomputed" and out["processed"] == 122, "this cycle's summary, not the earlier one of 11-14 01:30"
    assert out["window_from_stamp"] is True and out["cycle_start"] == "2026-11-14T23:57:43+01:00"


def test_a_summary_from_before_the_stamp_is_ignored_and_a_cycle_without_p2_rows_is_not_run(pg_app_conn):
    conn = pg_app_conn
    conn.execute("SET TIME ZONE 'UTC'")
    _put(conn, "RUN_SUMMARY", SUMMARY.format(rid="EARLIER", p=500, s=3200), datetime(2026, 11, 14, 23, 56, tzinfo=MADRID))   # one minute before the cycle started
    start, from_stamp = cr.cycle_start_from_stamp("20261114_235743", date(2026, 11, 14))
    out = cr.collect_p2_outcome(conn, start, from_stamp)
    assert out["state"] == "not_run" and out["started"] is False, "P1-only cycle: no P2 trace since the start"
    assert cr.p2_outcome_attention(out) == []


def test_p2_rows_without_a_summary_are_an_aborted_run(pg_app_conn):
    conn = pg_app_conn
    conn.execute("SET TIME ZONE 'UTC'")
    start, from_stamp = cr.cycle_start_from_stamp("20261114_235743", date(2026, 11, 14))
    _put(conn, "NAV_LOAD", "Total NAV escritos : 64", start + timedelta(minutes=18))
    out = cr.collect_p2_outcome(conn, start, from_stamp)
    assert out["state"] == "not_run" and out["started"] is True
    assert any("se interrumpio" in i for i in cr.p2_outcome_attention(out))


def test_the_latest_summary_wins_when_p2_ran_twice_in_the_cycle(pg_app_conn):
    conn = pg_app_conn
    start, from_stamp = cr.cycle_start_from_stamp("20261114_235743", date(2026, 11, 14))
    _put(conn, "RUN_SUMMARY", SUMMARY.format(rid="FIRST", p=0, s=3743), start + timedelta(minutes=20))
    _put(conn, "RUN_SUMMARY", SUMMARY.format(rid="SECOND", p=9, s=3734), start + timedelta(minutes=40))
    assert cr.collect_p2_outcome(conn, start, from_stamp)["processed"] == 9
