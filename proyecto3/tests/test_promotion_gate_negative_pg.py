# proyecto3/tests/test_promotion_gate_negative_pg.py
# -*- coding: utf-8 -*-
"""
FND-0251 -- negative-test matrix of the promotion gate. Each case plants ONE defect in a throwaway Postgres (toy tables in a per-test schema,
the migration's own lifecycle DDL, the audit tables cloned from the real control DDL) and drives the REAL CLI (`p3_promote_scenario.main`):
it must refuse (RC 1), write exactly one P3_SCENARIO_PROMOTION_BLOCKED row, change no status and name the gate. A positive control
(the same world without the defect) must promote, so a refusal is never an artefact of the fixture.

What this certifies is the GATE LOGIC and its wiring to real SQL. It does not certify detector coverage: a defect no audit rule detects
is not in `audit_finding` and cannot be blocked here (the coverage gate only shrinks that blind spot).

    python scripts/ops/run_pg_tests.py -- proyecto3/tests/test_promotion_gate_negative_pg.py
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "launch"))
from scenario_pg_support import db, statuses  # noqa: E402,F401  (db is a fixture)

import p3_promote_scenario as cli  # noqa: E402

SCENARIO = "legacy_c"                                    # the DRAFT of the shared fixture
FUNDS = ["LU0000000001", "LU0000000002", "LU0000000003", "LU0000000004"]
OUTSIDE = "LU0000000099"                                  # a universe fund that is not in the scenario
RUN = "post_test_p2"
BUILT = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)
AUDITED = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
TODAY = date.today()
RULE = "WINDOW_FISHER_IDENTITY"


class _KeepOpen:
    """The CLI closes its connection in `finally`; the fixture's connection must survive the call."""
    def __init__(self, conn):
        self._c = conn

    def __getattr__(self, name):
        return getattr(self._c, name)

    def close(self):
        pass


@pytest.fixture
def world(db, monkeypatch):
    conn = db
    conn.execute("ALTER TABLE fund_master ADD COLUMN fund_currency text, ADD COLUMN hedging_policy text, "
                 "ADD COLUMN asset_currency text, ADD COLUMN in_current_universe smallint NOT NULL DEFAULT 1")
    for t in ("audit_finding", "audit_finding_isin", "audit_accepted_finding", "audit_run"):
        conn.execute(f"CREATE TABLE {t} (LIKE control.{t} INCLUDING ALL)")          # real CHECK constraints and defaults, no FK
    # FND-0261 (AUDIT_COMPLETENESS_CONTRACT_ENABLED ON): the evidence is the run's header, written with its rows by --persist
    conn.execute("INSERT INTO audit_run (run_id, domain, scope, status, started_at) VALUES (%s, 'p2_metrics', 'FULL', 'PASS_WITH_WARNINGS', %s)",
                 (RUN, AUDITED))
    for isin in FUNDS + [OUTSIDE]:
        conn.execute("INSERT INTO fund_master (isin, fund_nature, fund_currency, hedging_policy, asset_currency) "
                     "VALUES (%s, 'Mixtos', 'EUR', 'Unhedged', 'EUR')", (isin,))
    for isin in FUNDS:
        conn.execute("INSERT INTO portfolio_weights (scenario_id, isin, block, weight) VALUES (%s, %s, 'Equilibrada', 0.25)", (SCENARIO, isin))
    conn.execute("UPDATE portfolio_scenarios SET built_at = %s WHERE scenario_id = %s", (BUILT, SCENARIO))
    conn.execute("INSERT INTO audit_finding (run_id, domain, block, rule_id, rule_class, severity, group_key, isin, evidence, detected_at) "
                 "VALUES (%s, 'p2_metrics', 'BLOCK4', 'OUTLIER_MAD_Z', 'STATISTICAL_ANOMALY', 'ALARM', 'sharpe|since_inception', %s, "
                 "'robust z 6', %s)", (RUN, FUNDS[0], AUDITED))          # a statistical anomaly ON the scenario: must never block
    conn.commit()

    import shared.db
    import shared.backlog_client as bc
    import proyecto3.src.data_freshness as df
    import proyecto3.src.regime_classifier as rcl
    state = {"stale": []}
    monkeypatch.setattr(shared.db, "get_connection", lambda: _KeepOpen(conn))
    monkeypatch.setattr(df, "check_universe_freshness", lambda c, clf: [type("C", (), {"name": n, "ok": False})() for n in state["stale"]])
    monkeypatch.setattr(df, "stale_checks", lambda checks: list(checks))
    monkeypatch.setattr(rcl, "RegimeClassifier", lambda c: object())
    monkeypatch.setattr(bc, "get_action_point_status", lambda ap_id: "OPEN")      # never the live backlog from a test
    monkeypatch.setattr(cli, "_git_state", lambda: "git test clean")
    return conn, state


def _hard_finding(conn, isins, rule=RULE, severity="ALARM", run=RUN, at=AUDITED):
    conn.execute("INSERT INTO audit_finding (run_id, domain, block, rule_id, rule_class, severity, group_key, evidence, detected_at) "
                 "VALUES (%s, 'p2_metrics', 'BLOCK5', %s, 'HARD_INVARIANT', %s, %s, '40/2809 rows violate', %s)",
                 (run, rule, severity, rule, at))
    for isin in isins:
        conn.execute("INSERT INTO audit_finding_isin (run_id, domain, rule_id, group_key, isin) VALUES (%s, 'p2_metrics', %s, %s, %s)",
                     (run, rule, rule, isin))
    conn.commit()


def _accept(conn, isin, expires, rule=RULE, ap="FND-0250"):
    conn.execute("INSERT INTO audit_accepted_finding (domain, rule_id, isin, ap_id, reason, accepted_at, expires_at) "
                 "VALUES ('p2_metrics', %s, %s, %s, 'known residual', %s, %s)", (rule, isin, ap, TODAY - timedelta(days=60), expires))
    conn.commit()


def _held_out(conn, count, isins):
    conn.execute("INSERT INTO audit_finding (run_id, domain, block, rule_id, rule_class, severity, group_key, evidence, distance, detected_at) "
                 "VALUES (%s, 'p2_metrics', 'BLOCK1', 'SNAPSHOT_HELD_OUT', 'STATISTICAL_ANOMALY', 'INFO', 'SNAPSHOT_HELD_OUT', "
                 "'held out', %s, %s)", (RUN, float(count), AUDITED))
    for isin in isins:
        conn.execute("INSERT INTO audit_finding_isin (run_id, domain, rule_id, group_key, isin) "
                     "VALUES (%s, 'p2_metrics', 'SNAPSHOT_HELD_OUT', 'SNAPSHOT_HELD_OUT', %s)", (RUN, isin))
    conn.commit()


def _refused_rows(conn):
    return conn.execute("SELECT message FROM ingestion_log WHERE step = 'P3_SCENARIO_PROMOTION_BLOCKED'").fetchall()


def _assert_refused(conn, rc, capsys, gate):
    out = capsys.readouterr().out
    assert rc == cli.RC_BLOCKED, out
    rows = _refused_rows(conn)
    assert len(rows) == 1 and f"[GATE:{gate}]" in rows[0][0], (rows, out)
    assert statuses(conn)[SCENARIO] == "DRAFT"
    assert conn.execute("SELECT COUNT(*) FROM ingestion_log WHERE step = 'P3_SCENARIO_PROMOTED'").fetchone()[0] == 0


# ---------------- positive controls ----------------

def test_control_a_clean_world_promotes_and_the_anomaly_on_a_selected_fund_does_not_block(world, capsys):
    conn, _ = world
    rc = cli.main([SCENARIO])
    out = capsys.readouterr().out
    assert rc == cli.RC_OK, out
    assert statuses(conn)[SCENARIO] == "PRODUCTION" and "anomalias estadisticas ALARM" in out and "no bloquean" in out
    note = conn.execute("SELECT message FROM ingestion_log WHERE step = 'P3_SCENARIO_PROMOTED'").fetchone()[0]
    assert "audit post_test_p2" in note and "anomalies on scenario 1 (informational)" in note and "git test clean" in note


def test_control_a_universe_defect_covered_by_an_active_residual_promotes_and_is_in_the_signoff(world, capsys):
    conn, _ = world
    _hard_finding(conn, [OUTSIDE])
    _accept(conn, OUTSIDE, TODAY + timedelta(days=45))
    assert cli.main([SCENARIO]) == cli.RC_OK
    note = conn.execute("SELECT message FROM ingestion_log WHERE step = 'P3_SCENARIO_PROMOTED'").fetchone()[0]
    assert "covered by residual 1 ISINs (FND-0250" in note


def test_control_the_reasoned_override_with_a_ticket_promotes_a_defect_on_a_selected_fund_and_leaves_the_trace(world, capsys):
    conn, _ = world
    _hard_finding(conn, [FUNDS[1]])
    assert cli.main([SCENARIO, "--accept-alarms", "falso positivo del detector FND-0234", "--ap", "FND-0234"]) == cli.RC_OK
    note = conn.execute("SELECT message FROM ingestion_log WHERE step = 'P3_SCENARIO_PROMOTED'").fetchone()[0]
    assert "[OVERRIDE:DATA_SCENARIO] FND-0234: falso positivo del detector FND-0234" in note and "defects on scenario 1" in note


# ---------------- negative matrix: every case must refuse ----------------

def test_a_hard_invariant_on_a_selected_fund_refuses(world, capsys):
    conn, _ = world
    _hard_finding(conn, [FUNDS[1], OUTSIDE])
    _assert_refused(conn, cli.main([SCENARIO]), capsys, "DATA_SCENARIO")


def test_an_active_accepted_residual_on_a_selected_fund_refuses(world, capsys):
    conn, _ = world
    _hard_finding(conn, [FUNDS[2]], severity="INFO")           # persisted as a downgraded hard finding (A0)
    _accept(conn, FUNDS[2], TODAY + timedelta(days=60))
    _assert_refused(conn, cli.main([SCENARIO]), capsys, "RESIDUAL_SCENARIO")


def test_a_residual_accepted_for_a_universe_fund_does_not_cover_it_once_the_fund_is_selected(world, capsys):
    """The scope change: the residual was fine while OUTSIDE was universe-only; the next DRAFT selects it."""
    conn, _ = world
    _hard_finding(conn, [OUTSIDE])
    _accept(conn, OUTSIDE, TODAY + timedelta(days=60))
    conn.execute("INSERT INTO portfolio_weights (scenario_id, isin, block, weight) VALUES (%s, %s, 'Equilibrada', 0.0)", (SCENARIO, OUTSIDE))
    conn.commit()
    _assert_refused(conn, cli.main([SCENARIO]), capsys, "RESIDUAL_SCENARIO")


def test_an_expired_residual_on_a_universe_only_finding_refuses(world, capsys):
    conn, _ = world
    _hard_finding(conn, [OUTSIDE], severity="INFO")            # the audit ran while it was accepted; it lapsed since
    _accept(conn, OUTSIDE, TODAY - timedelta(days=1))
    _assert_refused(conn, cli.main([SCENARIO]), capsys, "DATA_UNIVERSE")


def test_a_universe_only_hard_finding_without_a_residual_refuses_even_with_an_override(world, capsys):
    conn, _ = world
    _hard_finding(conn, [OUTSIDE])
    rc = cli.main([SCENARIO, "--accept-alarms", "un motivo", "--ap", "FND-0250"])
    _assert_refused(conn, rc, capsys, "DATA_UNIVERSE")


def test_a_stale_input_in_the_freshness_gate_refuses(world, capsys):
    """Wiring only: the gate's own logic (e.g. the uniform-CALC_VERSION share) is covered by test_data_freshness*."""
    conn, state = world
    state["stale"] = ["metrics_calc_version_uniform"]
    _assert_refused(conn, cli.main([SCENARIO]), capsys, "FRESHNESS")


def test_exposure_above_the_budget_refuses_and_no_override_helps(world, capsys):
    conn, _ = world
    conn.execute("UPDATE fund_master SET fund_currency = 'USD', asset_currency = 'USD' WHERE isin = ANY(%s)", (FUNDS[:3],))
    conn.commit()
    rc = cli.main([SCENARIO, "--accept-alarms", "un motivo", "--ap", "FND-0250"])
    _assert_refused(conn, rc, capsys, "FX")


def test_declared_cash_above_the_cap_refuses_and_no_override_helps(world, capsys):
    conn, _ = world
    conn.execute("UPDATE portfolio_weights SET weight = 0.20 WHERE scenario_id = %s", (SCENARIO,))             # 0.80 + 0.20 declared
    conn.execute("UPDATE portfolio_scenarios SET notes = '{\"cash_weight\": 0.20}' WHERE scenario_id = %s", (SCENARIO,))
    conn.commit()
    rc = cli.main([SCENARIO, "--accept-alarms", "un motivo", "--ap", "FND-0250"])
    _assert_refused(conn, rc, capsys, "CASH")


def test_an_audit_older_than_the_draft_build_refuses_and_no_override_helps(world, capsys):
    conn, _ = world
    conn.execute("UPDATE portfolio_scenarios SET built_at = %s WHERE scenario_id = %s", (AUDITED + timedelta(hours=1), SCENARIO))
    conn.commit()
    rc = cli.main([SCENARIO, "--accept-alarms", "un motivo", "--ap", "FND-0251"])
    _assert_refused(conn, rc, capsys, "AUDIT_STALE")


def test_no_persisted_audit_at_all_refuses(world, capsys):
    conn, _ = world
    conn.execute("DELETE FROM audit_finding")
    conn.execute("DELETE FROM audit_run")
    conn.commit()
    _assert_refused(conn, cli.main([SCENARIO]), capsys, "AUDIT_STALE")


def test_a_run_without_a_header_is_not_evidence(world, capsys):
    # FND-0261: findings persisted before control.audit_run existed (or by a crash) carry no completeness verdict
    conn, _ = world
    conn.execute("DELETE FROM audit_run")
    conn.commit()
    _assert_refused(conn, cli.main([SCENARIO]), capsys, "AUDIT_STALE")


def test_an_incomplete_full_run_is_not_evidence_and_the_refusal_says_why(world, capsys):
    conn, _ = world
    conn.execute("UPDATE audit_run SET status = 'INCOMPLETE' WHERE run_id = %s", (RUN,))
    conn.commit()
    rc = cli.main([SCENARIO])
    out = capsys.readouterr().out
    assert rc != 0 and "[GATE:AUDIT_STALE]" in out and "INCOMPLETE" in out


def test_a_selected_fund_held_out_of_the_audit_refuses(world, capsys):
    conn, _ = world
    _held_out(conn, 2, [FUNDS[3], OUTSIDE])
    _assert_refused(conn, cli.main([SCENARIO]), capsys, "DATA_COVERAGE")


def test_a_held_out_count_that_the_persisted_detail_does_not_match_refuses_as_unknown_not_per_fund(world, capsys):
    conn, _ = world
    _held_out(conn, 51, [])                                      # an audit persisted before the detail existed
    rc = cli.main([SCENARIO])
    out = capsys.readouterr().out
    assert rc == cli.RC_BLOCKED and "DESCONOCIDA" in out and "51" in out and FUNDS[0] not in out.split("NO PROMOVIBLE")[1]
    assert len(_refused_rows(conn)) == 1 and statuses(conn)[SCENARIO] == "DRAFT"


def test_a_selected_fund_that_left_the_audited_universe_refuses(world, capsys):
    conn, _ = world
    conn.execute("UPDATE fund_master SET in_current_universe = 0 WHERE isin = %s", (FUNDS[0],))
    conn.commit()
    _assert_refused(conn, cli.main([SCENARIO]), capsys, "DATA_COVERAGE")


def test_an_override_without_a_ticket_refuses(world, capsys):
    conn, _ = world
    _hard_finding(conn, [FUNDS[1]])
    rc = cli.main([SCENARIO, "--accept-alarms", "un motivo sin ticket"])
    out = capsys.readouterr().out
    assert rc == cli.RC_BLOCKED and "[GATE:OVERRIDE_AP]" in out
    assert len(_refused_rows(conn)) == 1 and statuses(conn)[SCENARIO] == "DRAFT"


def test_a_dry_run_of_a_refusal_writes_nothing(world, capsys):
    conn, _ = world
    _hard_finding(conn, [FUNDS[1]])
    assert cli.main([SCENARIO, "--dry-run"]) == cli.RC_BLOCKED
    assert _refused_rows(conn) == [] and statuses(conn)[SCENARIO] == "DRAFT"
