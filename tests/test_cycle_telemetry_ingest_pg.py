"""
tests/test_cycle_telemetry_ingest_pg.py -- the P2 ingest, the audit counts and the flag catalogue on a real Postgres (FND-0239 stage 3).

pg_app_conn = a SAVEPOINT connection with the application search_path that is rolled back, so nothing persists. Run: python scripts/ops/run_pg_tests.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import cycle_telemetry as ct  # noqa: E402
from shared import cycle_telemetry_flags as fl  # noqa: E402
from shared import cycle_telemetry_ingest as ing  # noqa: E402

T0 = datetime(2026, 10, 8, 8, 0, tzinfo=timezone.utc)
GOOD = ("run_id=P2-1 processed=120 skipped=2900 errors=0 warnings=3 defl_skipped=1 written=48000 quarantined=0 "
        "ols_funds=120 cond_guard_funds=2 betas_nulled=15 elapsed=1834s")


@pytest.fixture(autouse=True)
def _no_repo_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv(ct.ENV_FALLBACK, str(tmp_path / "fallback.jsonl"))
    monkeypatch.delenv(ct.ENV_CYCLE_ID, raising=False)


def _iso(minutes=0):
    return (T0 + timedelta(minutes=minutes)).isoformat()


def _cycle(conn, cid="20261008_080000"):
    assert ct.begin_cycle("P1_P2_Complete", {}, cycle_id=cid, conn=conn, ts=_iso()) is True
    return cid


def _p2_step(conn, cid, rc=0, ended=True):
    assert ct.step_begin("P2_CALC", cycle_id=cid, conn=conn, ts=_iso(1)) is True
    if ended:
        assert ct.step_end("P2_CALC", rc, started_at=_iso(1), cycle_id=cid, conn=conn, ts=_iso(40)) is True


def _log(conn, step, message, batch="B1", status="OK"):
    conn.execute("INSERT INTO p2_pipeline_log (isin, step, status, metric_version, message, batch_id, created_at) "
                 "VALUES (NULL, %s, %s, 'v', %s, %s, now())", (step, status, message, batch))


def _flags(conn, cid):
    return {(r[0], r[1], r[2]): r[3] for r in conn.execute(
        "SELECT flag_code, severity, scope, detail FROM control.cycle_flag WHERE cycle_id = %s", (cid,)).fetchall()}


def _metrics(conn, cid):
    return {r[0]: r[1] for r in conn.execute("SELECT metric_code, value_num FROM control.cycle_metric WHERE cycle_id = %s", (cid,)).fetchall()}


# ---------------------------------------------------------------- DDL
def test_the_beta_gate_step_is_seeded_after_the_diagnostics_and_before_p3(pg_app_conn):
    rows = dict(pg_app_conn.execute("SELECT step_code, seq FROM control.cycle_step_def").fetchall())
    assert rows["BETA_GATE"] > rows["CYCLE_REPORT"] and rows["BETA_GATE"] < rows["P3_BUILD"] < rows["P3_REPORT"]


# ---------------------------------------------------------------- the P2 ingest
def test_a_clean_run_becomes_metrics_with_the_seeded_codes_and_no_flags(pg_app_conn):
    cid = _cycle(pg_app_conn)
    _p2_step(pg_app_conn, cid)
    _log(pg_app_conn, "RUN_SUMMARY", GOOD)
    res = ing.ingest_p2(pg_app_conn, cid)
    assert "error" not in res and res["metrics"] == 11 and res["flags"] == [] and res["summaries"] == 1
    m = _metrics(pg_app_conn, cid)
    assert m["p2_processed"] == 120 and m["p2_written_rows"] == 48000 and m["ols_funds"] == 120 and m["p2_elapsed_s"] == 1834.0
    assert m["cache_hit_pct"] == pytest.approx(100.0 * 2900 / 3020)
    assert _flags(pg_app_conn, cid) == {}
    assert ing.ingest_p2(pg_app_conn, cid)["metrics"] == 11 and len(_metrics(pg_app_conn, cid)) == 11              # idempotent: same PK upserts


def test_format_drift_writes_a_warn_flag_and_still_returns_normally(pg_app_conn, caplog):
    import logging
    cid = _cycle(pg_app_conn)
    _p2_step(pg_app_conn, cid)
    _log(pg_app_conn, "RUN_SUMMARY", GOOD.replace("quarantined=0 ", ""))
    with caplog.at_level(logging.WARNING, logger="cycle_telemetry_ingest"):
        res = ing.ingest_p2(pg_app_conn, cid)
    assert "error" not in res and res["flags"] == ["INGEST_FORMAT_DRIFT"] and res["metrics"] == 0
    (key, detail), = _flags(pg_app_conn, cid).items()
    assert key == ("INGEST_FORMAT_DRIFT", "WARN", "B1") and "missing key quarantined" in detail
    assert any("INGEST_FORMAT_DRIFT" in r.message and "missing key quarantined" in r.message for r in caplog.records)


def test_a_p2_step_that_ended_without_any_summary_is_a_high_crash_flag(pg_app_conn):
    cid = _cycle(pg_app_conn)
    _p2_step(pg_app_conn, cid, rc=137)
    res = ing.ingest_p2(pg_app_conn, cid)
    assert res["flags"] == ["INGEST_CRASH_DETECTED"]
    assert list(_flags(pg_app_conn, cid))[0] == ("INGEST_CRASH_DETECTED", "HIGH", "window")


def test_a_backfill_start_without_an_end_is_a_high_flag_per_batch(pg_app_conn):
    cid = _cycle(pg_app_conn)
    _p2_step(pg_app_conn, cid)
    _log(pg_app_conn, "BACKFILL_START", "CALC_VERSION drift: stored=A current=B", batch="B7")
    _log(pg_app_conn, "RUN_SUMMARY", GOOD, batch="B7")
    ing.ingest_p2(pg_app_conn, cid)
    assert ("INGEST_CRASH_DETECTED", "HIGH", "B7") in _flags(pg_app_conn, cid)


def test_rows_older_than_the_p2_step_are_not_read(pg_app_conn):
    cid = _cycle(pg_app_conn)
    _p2_step(pg_app_conn, cid)
    pg_app_conn.execute("INSERT INTO p2_pipeline_log (step, status, metric_version, message, batch_id, created_at) "
                    "VALUES ('RUN_SUMMARY', 'OK', 'v', 'garbage from last month', 'OLD', %s)", (T0 - timedelta(days=30),))
    assert ing.ingest_p2(pg_app_conn, cid)["summaries"] == 0                            # the old row is outside the window


def test_ingest_without_the_tables_or_the_cycle_never_raises(pg_app_conn):
    assert "error" in ing.ingest_p2(pg_app_conn, "no_such_cycle")


# ---------------------------------------------------------------- audit counts
def _finding(conn, run_id, rule, severity, group="g", isin=None):
    conn.execute("INSERT INTO audit_finding (run_id, domain, block, rule_id, rule_class, severity, group_key, isin) "
                 "VALUES (%s, 'p2_metrics', 'BLOCK1', %s, 'HARD_INVARIANT', %s, %s, %s)", (run_id, rule, severity, group, isin))


def test_audit_counts_and_the_diff_against_the_baseline_are_read_from_audit_finding(pg_app_conn):
    cid = _cycle(pg_app_conn)
    for rule, sev in (("R1", "ALARM"), ("R2", "WARN"), ("R3", "WARN")):
        _finding(pg_app_conn, "pre_X_p2", rule, sev)
    for rule, sev in (("R2", "WARN"), ("R3", "WARN"), ("R4", "INFO"), ("R5", "ALARM")):
        _finding(pg_app_conn, "post_X_p2", rule, sev)
    assert ing.record_audit(pg_app_conn, cid, "p2_metrics", "pre", "pre_X_p2") == {"n_alarm": 1, "n_warn": 2, "n_info": 0}
    out = ing.record_audit(pg_app_conn, cid, "p2_metrics", "post", "post_X_p2", baseline_run_id="pre_X_p2")
    assert out == {"n_alarm": 1, "n_warn": 2, "n_info": 1, "n_new": 2, "n_resolved": 1, "n_unchanged": 2}
    row = pg_app_conn.execute("SELECT run_id, n_alarm, n_warn, n_info, n_new, n_resolved, n_unchanged FROM control.cycle_audit_run "
                          "WHERE cycle_id = %s AND domain = 'p2_metrics' AND phase = 'post'", (cid,)).fetchone()
    assert tuple(row) == ("post_X_p2", 1, 2, 1, 2, 1, 2)


def test_audit_counts_for_an_unknown_run_are_zeros_not_an_error(pg_app_conn):
    cid = _cycle(pg_app_conn)
    assert ing.record_audit(pg_app_conn, cid, "cost_attributes", "pre", "never_ran") == {"n_alarm": 0, "n_warn": 0, "n_info": 0}


# ---------------------------------------------------------------- the end-of-cycle catalogue
def test_evaluate_cycle_raises_the_slow_step_failed_diagnostic_and_pending_refresh_flags(pg_app_conn):
    cid = _cycle(pg_app_conn)
    for i in range(4):                                           # four OK previous runs of P1_BENCH (baseline 600 s)
        pg_app_conn.execute("INSERT INTO control.cycle_run (cycle_id, launcher, started_at, status) VALUES (%s, 'x', %s, 'OK')", (f"2026100{i}_000000", _iso(-10000 - i)))
        _p2 = (f"2026100{i}_000000", _iso(-10000 - i))
        pg_app_conn.execute("INSERT INTO control.cycle_step (cycle_id, step_code, attempt, step_kind, started_at, ended_at, rc, dur_s, status) "
                        "VALUES (%s, 'P1_BENCH', 1, 'PIPELINE', %s, %s, 0, 600, 'OK')", (_p2[0], _p2[1], _p2[1]))
    assert ct.step_begin("P1_BENCH", cycle_id=cid, conn=pg_app_conn, ts=_iso(1)) is True
    assert ct.step_end("P1_BENCH", 0, started_at=_iso(1), cycle_id=cid, conn=pg_app_conn, ts=_iso(1 + 40)) is True        # 2400 s = 4x the baseline
    assert ct.step_begin("BETA_COMPARE", cycle_id=cid, conn=pg_app_conn, ts=_iso(60)) is True
    assert ct.step_end("BETA_COMPARE", 2, started_at=_iso(60), cycle_id=cid, conn=pg_app_conn, ts=_iso(61)) is True
    pg_app_conn.execute("INSERT INTO fund_master (isin, fund_name, fund_nature, heuristic_block, heuristic_core, in_current_universe) "
                    "VALUES ('FR0000000A1', 'F', 'Mixtos', 'mixtos', 1, 1)")
    pg_app_conn.execute("INSERT INTO ingestion_log (isin, step, status, message, created_at) VALUES ('FR0000000A1', 'FAMILY_NATURE_CORRECTION', 'OK', 'x', now())")
    res = fl.evaluate_cycle(pg_app_conn, cid, "OK")
    assert "error" not in res and set(res["flags"]) == {"STEP_SLOW", "DIAG_FAILED_CYCLE_OK", "FAMILY_REFRESH_PENDING"}
    got = _flags(pg_app_conn, cid)
    assert got[("STEP_SLOW", "HIGH", "P1_BENCH")].startswith("P1_BENCH took 2400 s")                                   # 4x >= the 3.0 alarm ratio
    assert ("DIAG_FAILED_CYCLE_OK", "WARN", "BETA_COMPARE") in got and ("FAMILY_REFRESH_PENDING", "HIGH", "") in got


def test_evaluate_cycle_on_a_quiet_cycle_raises_nothing(pg_app_conn):
    cid = _cycle(pg_app_conn)
    assert fl.evaluate_cycle(pg_app_conn, cid, "OK") == {"flags": []}
    assert _flags(pg_app_conn, cid) == {}


def test_every_telemetry_statement_of_the_new_modules_is_a_literal_with_percent_s_only():
    for q in (ing.Q_P2_STEP, ing.Q_CYCLE_START, ing.Q_P2_ROWS, ing.Q_AUDIT_COUNTS, ing.Q_AUDIT_DIFF, fl.Q_STEPS, fl.Q_METRICS):
        assert "{" not in q and "?" not in q
