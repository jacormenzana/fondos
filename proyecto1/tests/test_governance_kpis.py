# proyecto1/tests/test_governance_kpis.py
# -*- coding: utf-8 -*-
"""FND-0251 item E: the governance KPI decisions on SYNTHETIC rows (R-7: no DB). Live data is near-empty at first, so correctness is proven here
(and against the real DDL in proyecto3/tests/test_governance_kpis_pg.py), not by waiting for cycles."""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_spec = importlib.util.spec_from_file_location("governance_kpis", os.path.join(_ROOT, "scripts", "audit", "governance_kpis.py"))
k = importlib.util.module_from_spec(_spec)
sys.modules["governance_kpis"] = k
_spec.loader.exec_module(k)

TODAY = date(2026, 10, 11)

PROMOTED_CLEAN = "scenario S1 promoted to PRODUCTION by jacor; supersedes none; audit run1; defects on scenario 0; git abc clean"
PROMOTED_OVERRIDE = ("scenario S2 promoted to PRODUCTION by jacor; supersedes S1; [OVERRIDE:DATA_SCENARIO] FND-0234: falso positivo; "
                     "[OVERRIDE:DATA_COVERAGE] FND-0234: falso positivo")
BLOCKED_A = "promotion of scenario S3 refused for jacor: [GATE:FX] exposicion | [GATE:DATA_UNIVERSE] defecto"
BLOCKED_B = "promotion of scenario S3 refused for jacor: [GATE:AUDIT_STALE] auditoria anterior"


def test_promotions_count_overrides_and_name_the_gates():
    rows = [("P3_SCENARIO_PROMOTED", PROMOTED_CLEAN), ("P3_SCENARIO_PROMOTED", PROMOTED_OVERRIDE), ("P3_SCENARIO_PROMOTION_BLOCKED", BLOCKED_A)]
    out = k.promotions_kpi(rows)
    assert (out["total"], out["with_override"], out["without_override"]) == (2, 1, 1)
    assert out["overrides"] == {"DATA_COVERAGE": 1, "DATA_SCENARIO": 1}


def test_refusals_group_by_gate_and_one_refusal_can_carry_several_codes():
    rows = [("P3_SCENARIO_PROMOTION_BLOCKED", BLOCKED_A), ("P3_SCENARIO_PROMOTION_BLOCKED", BLOCKED_B),
            ("P3_SCENARIO_PROMOTION_BLOCKED", BLOCKED_B), ("P3_SCENARIO_PROMOTED", PROMOTED_CLEAN)]
    out = k.refusals_kpi(rows)
    assert out["total"] == 3 and out["by_gate"] == {"AUDIT_STALE": 2, "DATA_UNIVERSE": 1, "FX": 1}


def test_a_refusal_row_without_codes_still_counts_as_a_refusal():
    assert k.refusals_kpi([("P3_SCENARIO_PROMOTION_BLOCKED", "old free-text refusal")]) == {"total": 1, "by_gate": {}}


def test_residual_health_splits_active_expiring_and_lapsed():
    rows = [("RULE_A", "FND-0250", "I1", TODAY + timedelta(days=80)), ("RULE_A", "FND-0250", "I2", TODAY + timedelta(days=80)),
            ("RULE_B", "FND-0252", "I3", TODAY + timedelta(days=30)),        # 30 days: inside the warning window (inclusive)
            ("RULE_B", "FND-0252", "I4", TODAY + timedelta(days=31)),
            ("RULE_C", "FND-0001", "I5", TODAY - timedelta(days=1))]         # lapsed, still in the table
    out = k.residuals_kpi(rows, TODAY, 30)
    assert (out["active_isins"], out["expiring_isins"], out["lapsed_isins"]) == (4, 1, 1)
    assert out["active_by_rule_ap"] == [{"rule": "RULE_A", "ap": "FND-0250", "isins": 2}, {"rule": "RULE_B", "ap": "FND-0252", "isins": 2}]


def test_recurrence_ranks_rules_by_how_many_runs_they_fired_in():
    by_run = {"r3": {"A": 6, "B": 1}, "r2": {"A": 6}, "r1": {"A": 40, "C": 2}}
    out = k.recurrence_kpi(by_run, 3)
    assert [d["rule"] for d in out] == ["A", "B", "C"]
    assert out[0] == {"rule": "A", "runs_with_finding": 3, "runs_considered": 3, "latest_count": 6}
    assert out[2]["latest_count"] == 0                                        # C fired in an older run only


def test_post_promotion_target_is_zero_and_lists_the_funds_when_it_is_not():
    clean = k.post_promotion_kpi("S", datetime(2026, 10, 11, tzinfo=timezone.utc), [])
    assert clean["hard_findings"] == 0 and clean["funds"] == []
    dirty = k.post_promotion_kpi("S", None, [("r9", "WINDOW_FISHER_IDENTITY", "LU1"), ("r9", "WINDOW_FISHER_IDENTITY", "LU2"), ("r9", "TIMESERIES_DUPLICATE", "LU1")])
    assert dirty["hard_findings"] == 3 and dirty["funds"] == ["LU1", "LU2"] and dirty["rules"] == ["TIMESERIES_DUPLICATE", "WINDOW_FISHER_IDENTITY"]


# ---- collect() over a fake connection ----

class _Conn:
    def __init__(self, **kw):
        self.d = dict(steps=[], residuals=None, runs=[], by_run=[], prod=None, funds=[], post=[], detail=True)
        self.d.update(kw)
        self.sql = []

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        self.sql.append(s)
        d, rows = self.d, []
        if s.startswith("SELECT step, message FROM ingestion_log"):
            rows = d["steps"]
        elif "to_regclass('audit_accepted_finding')" in s:
            rows = [("audit_accepted_finding",)] if d["residuals"] is not None else [(None,)]
        elif s.startswith("SELECT rule_id, ap_id, isin, expires_at FROM audit_accepted_finding"):
            rows = d["residuals"]
        elif s.startswith("SELECT run_id FROM audit_finding"):
            rows = [(r,) for r in d["runs"]]
        elif s.startswith("SELECT run_id, rule_id, COUNT(*) FROM audit_finding"):
            rows = d["by_run"]
        elif s.startswith("SELECT scenario_id, promoted_at FROM portfolio_scenarios"):
            rows = [d["prod"]] if d["prod"] else []
        elif s.startswith("SELECT isin FROM portfolio_weights"):
            rows = [(f,) for f in d["funds"]]
        elif "to_regclass('audit_finding_isin')" in s:
            rows = [("audit_finding_isin",)] if d["detail"] else [(None,)]
        elif s.startswith("SELECT f.run_id, f.rule_id, i.isin FROM audit_finding f"):
            rows = d["post"]
        return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: list(rows))


def test_collect_assembles_every_kpi_and_is_read_only():
    conn = _Conn(steps=[("P3_SCENARIO_PROMOTED", PROMOTED_CLEAN), ("P3_SCENARIO_PROMOTION_BLOCKED", BLOCKED_A)],
                 residuals=[("R", "FND-0250", "I1", TODAY + timedelta(days=60))], runs=["r2", "r1"],
                 by_run=[("r2", "R", 6), ("r1", "R", 6)], prod=("S1", datetime(2026, 10, 11, tzinfo=timezone.utc)), funds=["F1"],
                 post=[("r2", "R", "F1")])
    out = k.collect(conn, n_runs=6, today=TODAY)
    assert out["promotions"]["total"] == 1 and out["refusals"]["by_gate"] == {"DATA_UNIVERSE": 1, "FX": 1}
    assert out["residuals"]["active_isins"] == 1 and out["recurrence"][0]["runs_with_finding"] == 2
    assert out["post_promotion"]["hard_findings"] == 1 and out["post_promotion"]["funds"] == ["F1"]
    assert conn.sql and all(s.startswith("SELECT") for s in conn.sql)


def test_collect_tolerates_missing_tables_and_no_production_scenario():
    out = k.collect(_Conn(), n_runs=6, today=TODAY)
    assert out["residuals"] == {"table_present": False} and out["post_promotion"] is None and out["recurrence"] == []
    text = k.render(out)
    assert "not present" in text and "no PRODUCTION scenario" in text and "none" in text


def test_render_names_every_kpi_and_json_is_serialisable():
    import json
    out = k.collect(_Conn(steps=[("P3_SCENARIO_PROMOTED", PROMOTED_OVERRIDE)], residuals=[], runs=["r1"], by_run=[]), n_runs=6, today=TODAY)
    text = k.render(out)
    for n in "12345":
        assert f"\n{n}. " in "\n" + text or text.startswith(f"{n}. ")
    assert "with (DATA_COVERAGE=1, DATA_SCENARIO=1)" in text
    json.dumps(out, default=str)
