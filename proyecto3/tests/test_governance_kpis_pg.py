# proyecto3/tests/test_governance_kpis_pg.py
# -*- coding: utf-8 -*-
"""FND-0251 item E on Postgres: the KPI queries run through the REAL audit DDL (tables cloned from control.*) over SYNTHETIC rows, so query
correctness (and the EXPLAIN sweep over the script) does not depend on how many live cycles exist.

    python scripts/ops/run_pg_tests.py -- proyecto3/tests/test_governance_kpis_pg.py
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scenario_pg_support import db  # noqa: E402,F401  (db is a fixture)

_spec = importlib.util.spec_from_file_location("governance_kpis_pg", _ROOT / "scripts" / "audit" / "governance_kpis.py")
k = importlib.util.module_from_spec(_spec)
sys.modules["governance_kpis_pg"] = k
_spec.loader.exec_module(k)

TODAY = date(2026, 10, 11)
T0 = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def world(db):
    conn = db
    for t in ("audit_finding", "audit_finding_isin", "audit_accepted_finding"):
        conn.execute(f"CREATE TABLE {t} (LIKE control.{t} INCLUDING ALL)")
    for isin in ("F1", "F2", "OUT"):
        conn.execute("INSERT INTO fund_master (isin, fund_nature) VALUES (%s, 'Mixtos')", (isin,))
    # a PRODUCTION scenario promoted at T0 with F1 + F2
    conn.execute("UPDATE portfolio_scenarios SET status = 'PRODUCTION', promoted_at = %s WHERE scenario_id = 'legacy_c'", (T0,))
    for isin in ("F1", "F2"):
        conn.execute("INSERT INTO portfolio_weights (scenario_id, isin, block, weight) VALUES ('legacy_c', %s, 'Equilibrada', 0.5)", (isin,))
    ins = ("INSERT INTO ingestion_log (isin, step, status, message, created_at) VALUES (NULL, %s, %s, %s, %s)")
    conn.execute(ins, ("P3_SCENARIO_PROMOTED", "OK", "scenario S1 promoted to PRODUCTION by jacor; supersedes none; audit r1; defects on scenario 0", T0))
    conn.execute(ins, ("P3_SCENARIO_PROMOTED", "OK", "scenario S2 promoted to PRODUCTION by jacor; [OVERRIDE:DATA_SCENARIO] FND-0234: falso positivo", T0))
    conn.execute(ins, ("P3_SCENARIO_PROMOTION_BLOCKED", "BLOCKED", "promotion of scenario S3 refused for jacor: [GATE:FX] x | [GATE:AUDIT_STALE] y", T0))
    conn.execute(ins, ("P3_SCENARIO_PROMOTION_BLOCKED", "BLOCKED", "promotion of scenario S3 refused for jacor: [GATE:AUDIT_STALE] y", T0))
    conn.execute(ins, ("OTHER_STEP", "OK", "[GATE:FX] must not be counted", T0))
    # residuals: 2 active (one expiring), 1 lapsed row left in the table, one other domain
    r = ("INSERT INTO audit_accepted_finding (domain, rule_id, isin, ap_id, reason, accepted_at, expires_at) "
         "VALUES (%s, %s, %s, %s, 'known', %s, %s)")
    past = TODAY - timedelta(days=120)
    conn.execute(r, ("p2_metrics", "WINDOW_FISHER_IDENTITY", "OUT", "FND-0250", past, TODAY + timedelta(days=80)))
    conn.execute(r, ("p2_metrics", "REAL_EQUALS_NOMINAL_sharpe", "OUT", "FND-0252", past, TODAY + timedelta(days=10)))
    conn.execute(r, ("p2_metrics", "OLD_RULE", "OUT", "FND-0001", past, TODAY - timedelta(days=1)))
    conn.execute(r, ("cost_attributes", "OC_NOT_CONTAMINATED", "OUT", "FND-0034", past, TODAY + timedelta(days=60)))
    # audit runs: r1 (older), r2 (latest, AFTER the promotion); a hard finding on F1 only in r2
    f = ("INSERT INTO audit_finding (run_id, domain, block, rule_id, rule_class, severity, group_key, evidence, detected_at) "
         "VALUES (%s, 'p2_metrics', %s, %s, %s, %s, %s, 'e', %s)")
    conn.execute(f, ("r1", "BLOCK5", "WINDOW_FISHER_IDENTITY", "HARD_INVARIANT", "ALARM", "WINDOW_FISHER_IDENTITY", T0 - timedelta(days=30)))
    conn.execute(f, ("r2", "BLOCK5", "WINDOW_FISHER_IDENTITY", "HARD_INVARIANT", "INFO", "WINDOW_FISHER_IDENTITY", T0 + timedelta(days=1)))   # accepted: INFO
    conn.execute(f, ("r2", "BLOCK5", "TIMESERIES_DUPLICATE", "STATISTICAL_ANOMALY", "ALARM", "TIMESERIES_DUPLICATE", T0 + timedelta(days=1)))
    conn.execute(f, ("r2", "BLOCK4", "OUTLIER_MAD_Z", "STATISTICAL_ANOMALY", "ALARM", "sharpe|since_inception", T0 + timedelta(days=1)))   # not hard
    di = "INSERT INTO audit_finding_isin (run_id, domain, rule_id, group_key, isin) VALUES (%s, 'p2_metrics', %s, %s, %s)"
    conn.execute(di, ("r2", "WINDOW_FISHER_IDENTITY", "WINDOW_FISHER_IDENTITY", "OUT"))
    conn.execute(di, ("r2", "TIMESERIES_DUPLICATE", "TIMESERIES_DUPLICATE", "F1"))
    conn.execute(di, ("r2", "TIMESERIES_DUPLICATE", "TIMESERIES_DUPLICATE", "OUT"))
    conn.commit()
    return conn


def test_the_kpi_queries_run_on_the_real_ddl_and_count_the_synthetic_rows_exactly(world):
    out = k.collect(world, n_runs=6, today=TODAY)
    assert out["promotions"] == {"total": 2, "with_override": 1, "without_override": 1, "overrides": {"DATA_SCENARIO": 1}}
    assert out["refusals"] == {"total": 2, "by_gate": {"AUDIT_STALE": 2, "FX": 1}}          # OTHER_STEP is not a refusal
    res = out["residuals"]
    assert (res["active_isins"], res["expiring_isins"], res["lapsed_isins"]) == (2, 1, 1)   # the cost_attributes row is another domain
    rec = {d["rule"]: d for d in out["recurrence"]}
    assert set(rec) == {"WINDOW_FISHER_IDENTITY", "TIMESERIES_DUPLICATE"}                    # MAD_Z is not a hard finding
    assert rec["WINDOW_FISHER_IDENTITY"]["runs_with_finding"] == 2 and rec["TIMESERIES_DUPLICATE"]["runs_with_finding"] == 1
    assert out["runs_considered"] == ["r2", "r1"]


def test_post_promotion_counts_only_non_info_hard_findings_on_production_funds_after_promoted_at(world):
    pp = k.collect(world, n_runs=6, today=TODAY)["post_promotion"]
    assert pp["scenario"] == "legacy_c" and pp["hard_findings"] == 1
    assert pp["funds"] == ["F1"] and pp["rules"] == ["TIMESERIES_DUPLICATE"]
    # the accepted Fisher finding (INFO) and the one on OUT (not in the scenario) and the pre-promotion r1 finding do not count


def test_an_empty_database_gives_zeros_not_errors(db):
    conn = db
    for t in ("audit_finding", "audit_finding_isin", "audit_accepted_finding"):
        conn.execute(f"CREATE TABLE {t} (LIKE control.{t} INCLUDING ALL)")
    conn.commit()
    out = k.collect(conn, n_runs=6, today=TODAY)
    assert out["promotions"]["total"] == 0 and out["refusals"]["total"] == 0 and out["recurrence"] == []
    assert out["post_promotion"] is None and out["residuals"]["active_isins"] == 0
    assert "none" in k.render(out)
