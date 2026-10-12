# proyecto1/tests/test_statistical_audit_gating.py
# -*- coding: utf-8 -*-
"""FND-0251: the blocking predicate shared by the audit's --mode check and the P3 promotion gate, the scope split (defect nature x
membership in the scenario) and the coverage consistency check. Pure functions + the loader over a fake connection (R-7: no DB)."""
from __future__ import annotations

import inspect
import os
import sys
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.statistical_audit import gating  # noqa: E402
from shared.statistical_audit.gating import coverage_of, is_blocking, scope_findings  # noqa: E402

RULE = "WINDOW_FISHER_IDENTITY"
FAR = date(2027, 1, 4)


def _f(rule_id=RULE, rule_class="HARD_INVARIANT", block="BLOCK5", group_key=None, isin=None, evidence="40/2809 rows violate"):
    return {"rule_id": rule_id, "rule_class": rule_class, "block": block, "group_key": group_key or rule_id, "isin": isin,
            "evidence": evidence}


# ---------------- the predicate ----------------

@pytest.mark.parametrize("rule_class, block, rule_id, expected", [
    ("HARD_INVARIANT", "BLOCK5", RULE, True),
    ("PLAUSIBILITY", "BLOCK5", "SHARPE_BOUND", False),                 # a bound that is only plausible is a WARN
    ("STATISTICAL_ANOMALY", "BLOCK4", "OUTLIER_MAD_Z", False),         # FND-0175: review item, not a defect (even at ALARM)
    ("STATISTICAL_ANOMALY", "BLOCK2", "REAL_EQUALS_NOMINAL_sharpe", True),   # BLOCK2 = scalar vs timeseries / deflation identities
    ("STATISTICAL_ANOMALY", "BLOCK4", "TIMESERIES_DUPLICATE", True),
    ("STATISTICAL_ANOMALY", "BLOCK1", "SNAPSHOT_HELD_OUT", False),
    (None, None, None, False),
])
def test_is_blocking_truth_table(rule_class, block, rule_id, expected):
    assert is_blocking(rule_class, block, rule_id) is expected


def test_the_audit_runner_uses_the_same_predicate():
    """--mode check and the promotion gate must never disagree about what a defect is (P#11)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_statistical_audit_gating",
                                                  os.path.join(_ROOT, "scripts", "audit", "run_statistical_audit.py"))
    runner = importlib.util.module_from_spec(spec)
    sys.modules["run_statistical_audit_gating"] = runner
    spec.loader.exec_module(runner)
    assert runner.is_blocking is is_blocking
    run = runner.AuditRun("p2_metrics")
    run.findings = [_f("OUTLIER_MAD_Z", "STATISTICAL_ANOMALY", "BLOCK4")]
    assert runner._has_blocking_findings(run) is False
    run.findings.append(_f())
    assert runner._has_blocking_findings(run) is True


def test_the_held_out_finding_carries_its_isins_so_the_coverage_gate_can_name_the_funds():
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_statistical_audit_heldout",
                                                  os.path.join(_ROOT, "scripts", "audit", "run_statistical_audit.py"))
    runner = importlib.util.module_from_spec(spec)
    sys.modules["run_statistical_audit_heldout"] = runner
    spec.loader.exec_module(runner)
    run = runner.AuditRun("p2_metrics")
    run.snapshot_held_out = {"LU0000000009", "LU0000000001"}
    run.snapshot_max_spread_days = 400.0
    runner._emit_snapshot_held_out_finding(run)
    (finding,) = run.findings
    assert finding["rule_id"] == gating.HELD_OUT_RULE_ID
    assert finding["violating_isins"] == ("LU0000000001", "LU0000000009") and finding["distance"] == 2.0   # the count is the cross-check
    assert not is_blocking(finding["rule_class"], finding["block"], finding["rule_id"])


def test_the_loader_sql_filter_names_every_blocking_constant():
    """The persisted-row filter is SQL text (the EXPLAIN sweep needs a literal): keep it in step with the constants."""
    src = inspect.getsource(gating.load_scope)
    for cls in gating.BLOCKING_RULE_CLASSES:
        assert f"rule_class = '{cls}'" in src
    for block in gating.BLOCKING_BLOCKS:
        assert f"block = '{block}'" in src
    for rule_id in gating.BLOCKING_RULE_IDS | {gating.HELD_OUT_RULE_ID}:
        assert f"rule_id = '{rule_id}'" in src


# ---------------- scope ----------------

def test_a_defect_is_split_by_membership_in_the_scenario():
    scope = scope_findings([_f()], {(RULE, RULE): {"IN1", "OUT1", "OUT2"}}, {"IN1", "IN2"}, {})
    assert [(d.rule_id, d.isins) for d in scope.on_scenario] == [(RULE, ("IN1",))]
    assert [(d.rule_id, d.isins) for d in scope.universe_only] == [(RULE, ("OUT1", "OUT2"))]


def test_statistical_anomalies_are_never_defects_whatever_their_scope():
    scope = scope_findings([_f("OUTLIER_MAD_Z", "STATISTICAL_ANOMALY", "BLOCK4", isin="IN1")], {}, {"IN1"}, {})
    assert scope.on_scenario == [] and scope.universe_only == []


def test_a_per_isin_finding_uses_its_own_isin():
    scope = scope_findings([_f("TIMESERIES_DUPLICATE", "STATISTICAL_ANOMALY", "BLOCK4", isin="IN1")], {}, {"IN1"}, {})
    assert [(d.rule_id, d.isins) for d in scope.on_scenario] == [("TIMESERIES_DUPLICATE", ("IN1",))]


def test_a_finding_with_no_isin_detail_is_assumed_to_touch_the_scenario():
    scope = scope_findings([_f()], {}, {"IN1"}, {})
    assert [(d.rule_id, d.isins) for d in scope.on_scenario] == [(RULE, ())] and scope.universe_only == []


def test_an_active_residual_covers_a_universe_violator_and_is_named_in_the_signoff_data():
    accepted = {RULE: {"OUT1": ("FND-0250", FAR), "OUT2": ("FND-0250", FAR)}}
    scope = scope_findings([_f()], {(RULE, RULE): {"OUT1", "OUT2"}}, {"IN1"}, accepted)
    assert scope.universe_only == [] and scope.on_scenario == []
    assert [(r[1], r[2]) for r in scope.covered_by_residual] == [("OUT1", "FND-0250"), ("OUT2", "FND-0250")]


def test_a_partly_covered_finding_keeps_only_the_open_violators():
    accepted = {RULE: {"OUT1": ("FND-0250", FAR)}}
    scope = scope_findings([_f()], {(RULE, RULE): {"OUT1", "OUT2"}}, {"IN1"}, accepted)
    assert [(d.rule_id, d.isins) for d in scope.universe_only] == [(RULE, ("OUT2",))]


def test_a_residual_never_covers_a_scenario_fund_and_is_reported_on_its_own():
    accepted = {RULE: {"IN1": ("FND-0250", FAR)}, "OTHER_RULE": {"OUT9": ("FND-0001", FAR)}}
    scope = scope_findings([_f()], {(RULE, RULE): {"IN1"}}, {"IN1"}, accepted)
    assert scope.on_scenario == [] and scope.universe_only == []         # accepted, so not an open defect ...
    assert scope.residuals_on_scenario == [(RULE, "IN1", "FND-0250", FAR)]   # ... but it cannot cover a selected fund


def test_a_residual_on_a_scenario_fund_is_reported_even_when_its_rule_does_not_fire_today():
    scope = scope_findings([], {}, {"IN1"}, {RULE: {"IN1": ("FND-0250", FAR)}})
    assert scope.residuals_on_scenario == [(RULE, "IN1", "FND-0250", FAR)]


def test_an_expired_residual_is_not_in_the_active_set_so_the_violator_is_open_again():
    scope = scope_findings([_f()], {(RULE, RULE): {"OUT1"}}, {"IN1"}, {})          # load_accepted drops expired rows
    assert [d.isins for d in scope.universe_only] == [("OUT1",)]


def test_a_downgraded_finding_persisted_as_hard_class_info_is_still_scoped_by_what_is_accepted_now():
    """A0: a fully accepted finding is persisted as HARD_INVARIANT/INFO. The gate does not trust the persisted severity: if the acceptance
    lapsed between the audit and the promotion, its violators are open defects again."""
    row = _f(evidence="ACCEPTED RESIDUAL (FND-0250, until 2026-10-01): 1/1 ISINs")
    assert [d.isins for d in scope_findings([row], {(RULE, RULE): {"OUT1"}}, {"IN1"}, {}).universe_only] == [("OUT1",)]
    covered = scope_findings([row], {(RULE, RULE): {"OUT1"}}, {"IN1"}, {RULE: {"OUT1": ("FND-0250", FAR)}})
    assert covered.universe_only == []


# ---------------- coverage ----------------

def test_coverage_is_ok_when_nothing_was_held_out():
    assert coverage_of(None, [], {"IN1"}) == ("OK", (), "")


def test_coverage_lists_the_scenario_funds_the_audit_held_out():
    status, on, detail = coverage_of(3, ["IN1", "OUT1", "OUT2"], {"IN1", "IN2"})
    assert (status, on, detail) == ("OK", ("IN1",), "")


def test_coverage_is_unknown_when_the_persisted_detail_disagrees_with_its_count_and_never_guesses_per_fund():
    status, on, detail = coverage_of(51, [], {"IN1"})                       # a run persisted before the detail existed
    assert status == "UNKNOWN" and on == () and "51" in detail and "--persist" in detail


# ---------------- the loader over a fake connection ----------------

@pytest.fixture(autouse=True)
def _legacy_run_selection_by_default(monkeypatch):
    """The loader tests below use a fake connection that only knows audit_finding: they exercise the run selection WITHOUT the
    completeness contract (shared.config.AUDIT_COMPLETENESS_CONTRACT_ENABLED, ON since 2026-10-12). The contract path has its own
    tests further down, which pass contract=True explicitly or set the switch themselves."""
    import shared.config as cfg
    monkeypatch.setattr(cfg, "AUDIT_COMPLETENESS_CONTRACT_ENABLED", False)


class _Conn:
    def __init__(self, run=("run1", datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)), findings=(), detail=(), anomalies=(),
                 in_universe=("IN1",), detail_table=True):
        self.run, self.findings, self.detail, self.anomalies = run, list(findings), list(detail), list(anomalies)
        self.in_universe, self.detail_table, self.sql = set(in_universe), detail_table, []

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        self.sql.append(s)
        rows = []
        if s.startswith("SELECT run_id, MAX(detected_at) FROM audit_finding"):
            rows = [self.run] if self.run else []
        elif s.startswith("SELECT rule_id, rule_class, block, group_key, isin, evidence, distance FROM audit_finding"):
            rows = self.findings
        elif "to_regclass('audit_finding_isin')" in s:
            rows = [("audit_finding_isin",)] if self.detail_table else [(None,)]
        elif s.startswith("SELECT rule_id, group_key, isin FROM audit_finding_isin"):
            rows = self.detail
        elif s.startswith("SELECT isin, rule_id, group_key FROM audit_finding"):
            rows = [r for r in self.anomalies if r[0] in params[2]]
        elif s.startswith("SELECT isin FROM fund_master"):
            rows = [(i,) for i in params[0] if i in self.in_universe]
        return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: list(rows))


def _row(rule_id, rule_class="HARD_INVARIANT", block="BLOCK5", group_key=None, isin=None, evidence="e", distance=None):
    return (rule_id, rule_class, block, group_key or rule_id, isin, evidence, distance)


def test_loader_without_any_persisted_run_reports_has_run_false():
    scope = gating.load_scope(_Conn(run=None), "p2_metrics", ["IN1"])
    assert not scope.has_run and scope.run_id is None


def test_loader_assembles_defects_anomalies_coverage_and_population():
    conn = _Conn(
        findings=[_row(RULE), _row("SNAPSHOT_HELD_OUT", "STATISTICAL_ANOMALY", "BLOCK1", distance=2.0)],
        detail=[(RULE, RULE, "IN1"), (RULE, RULE, "OUT1"), ("SNAPSHOT_HELD_OUT", "SNAPSHOT_HELD_OUT", "IN2"),
                ("SNAPSHOT_HELD_OUT", "SNAPSHOT_HELD_OUT", "OUT7")],
        anomalies=[("IN1", "OUTLIER_MAD_Z", "sharpe|1y"), ("OUT1", "OUTLIER_MAD_Z", "sharpe|1y")],
        in_universe=("IN1",))
    scope = gating.load_scope(conn, "p2_metrics", ["IN1", "IN2"], {})
    assert scope.run_id == "run1" and scope.isin_detail_available
    assert [d.isins for d in scope.on_scenario] == [("IN1",)] and [d.isins for d in scope.universe_only] == [("OUT1",)]
    assert scope.anomalies_on_scenario == [("IN1", "OUTLIER_MAD_Z", "sharpe|1y")]
    assert scope.coverage == "OK" and scope.held_out_on_scenario == ("IN2",)
    assert scope.outside_population == ("IN2",)                          # IN2 is not In_Current_Universe any more


def test_loader_reports_unknown_coverage_for_a_run_persisted_before_the_held_out_detail_existed():
    conn = _Conn(findings=[_row("SNAPSHOT_HELD_OUT", "STATISTICAL_ANOMALY", "BLOCK1", distance=51.0)], detail=[])
    scope = gating.load_scope(conn, "p2_metrics", ["IN1"], {})
    assert scope.coverage == "UNKNOWN" and scope.held_out_on_scenario == ()


def test_loader_without_the_detail_table_treats_aggregate_defects_as_touching_the_scenario():
    conn = _Conn(findings=[_row(RULE)], detail_table=False)
    scope = gating.load_scope(conn, "p2_metrics", ["IN1"], {})
    assert not scope.isin_detail_available
    assert [(d.rule_id, d.isins) for d in scope.on_scenario] == [(RULE, ())]


class _RunsConn(_Conn):
    """Several persisted runs; applies the loader's sample-exclusion clause only if the SQL carries it (so the test fails without it)."""
    def __init__(self, runs, **kw):
        super().__init__(run=None, **kw)
        self.runs = runs                                          # [(run_id, ts)]

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("SELECT run_id, MAX(detected_at) FROM audit_finding"):
            self.sql.append(s)
            runs = [r for r in self.runs if not (f"right(run_id, 7) <> '{gating.SAMPLE_RUN_SUFFIX}'" in s
                                                   and r[0].endswith(gating.SAMPLE_RUN_SUFFIX))]
            rows = sorted(runs, key=lambda r: r[1], reverse=True)[:1]
            return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: list(rows))
        return super().execute(sql, params)


def _ts(hour):
    return datetime(2026, 10, 11, hour, 0, tzinfo=timezone.utc)


def test_the_sample_filter_literal_matches_the_constant():
    assert len(gating.SAMPLE_RUN_SUFFIX) == 7                     # right(run_id, 7) in the SQL literal
    assert f"right(run_id, 7) <> '{gating.SAMPLE_RUN_SUFFIX}'" in inspect.getsource(gating.select_evidence_run)


def test_loader_skips_a_newer_sample_run_and_uses_the_latest_full_run():
    conn = _RunsConn([("audit_full", _ts(10)), ("audit_20261011T120000Z_sample", _ts(12)), ("post_x_p2", _ts(11))])
    assert gating.load_scope(conn, "p2_metrics", ["IN1"], {}).run_id == "post_x_p2"


def test_loader_with_only_sample_runs_has_no_evidence():
    conn = _RunsConn([("audit_20261011T120000Z_sample", _ts(12))])
    assert not gating.load_scope(conn, "p2_metrics", ["IN1"], {}).has_run      # -> GATE:AUDIT_STALE at promotion


def _runner(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, "scripts", "audit", "run_statistical_audit.py"))
    runner = importlib.util.module_from_spec(spec)
    sys.modules[name] = runner
    spec.loader.exec_module(runner)
    return runner


@pytest.mark.parametrize("run_id, isins, expected", [
    (None, None, "audit_20261011T120000Z"),
    (None, ["LU1"], "audit_20261011T120000Z_sample"),
    ("my_check", ["LU1"], "my_check_sample"),                       # an explicit id under --isin is suffixed too
    ("my_check_sample", ["LU1"], "my_check_sample"),                # never doubled
    ("post_20261011_190000_p2", None, "post_20261011_190000_p2"),   # the cycle launchers' shape: kept as typed
    ("x", [], "x"),                                                 # an empty --isin list is not a scope
])
def test_resolve_run_id_marks_every_isin_scoped_run_as_a_sample(run_id, isins, expected):
    assert _runner("run_statistical_audit_runid")._resolve_run_id(run_id, isins, _ts(12)) == expected


def test_loader_is_read_only():
    conn = _Conn(findings=[_row(RULE)], detail=[(RULE, RULE, "IN1")])
    gating.load_scope(conn, "p2_metrics", ["IN1"], {})
    assert conn.sql and all(s.startswith("SELECT") for s in conn.sql)


# ---------------- BLOCK2 attribution (a finding that names its funds can be scoped) ----------------

def test_block2_findings_expose_the_funds_behind_them():
    import pandas as pd
    from shared.statistical_audit.comparisons import PairRule, compare_pairs
    df = pd.DataFrame({"isin": ["A", "B", "C", "D"], "left": [1.0, 1.0, 2.0, None], "right": [1.0, 1.5, 2.0, 3.0]})
    r = compare_pairs(df, "left", "right", PairRule("X", tolerance=0.01, min_matches=1))
    assert sorted(r.matched_rows["isin"]) == ["A", "C"]
    assert list(r.unmatched_rows["isin"]) == ["B"]                    # eligible (both present) but diverging; D is not eligible


def test_isins_of_is_sorted_distinct_and_tolerant():
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_statistical_audit_isins",
                                                  os.path.join(_ROOT, "scripts", "audit", "run_statistical_audit.py"))
    runner = importlib.util.module_from_spec(spec)
    sys.modules["run_statistical_audit_isins"] = runner
    spec.loader.exec_module(runner)
    import pandas as pd
    assert runner._isins_of(pd.DataFrame({"isin": ["B", "A", "B", None]})) == ("A", "B")
    assert runner._isins_of(pd.DataFrame({"isin": []})) == () and runner._isins_of(None) == ()
    assert runner._isins_of(pd.DataFrame({"other": ["A"]})) == ()


# ---------------- FND-0261: evidence from control.audit_run when the completeness contract is on ----------------

class _HeaderConn(_Conn):
    """control.audit_run headers [(run_id, started_at, scope, status)]; applies the scope / status clauses only if the SQL carries them
    (so a test fails if the loader drops one)."""
    def __init__(self, headers, table=True, **kw):
        super().__init__(run=("legacy_run", _ts(23)), **kw)          # what the contract-OFF query would pick
        self.headers, self.table = headers, table

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if "to_regclass('audit_run')" in s:
            self.sql.append(s)
            rows = [("audit_run",)] if self.table else [(None,)]
            return SimpleNamespace(fetchone=lambda: rows[0], fetchall=lambda: rows)
        if s.startswith("SELECT run_id, COALESCE(started_at, finished_at) AS run_ts FROM audit_run"):
            self.sql.append(s)
            hs = [h for h in self.headers
                  if not ("scope = 'FULL'" in s and h[2] != "FULL") and not ("status <> 'INCOMPLETE'" in s and h[3] == "INCOMPLETE")]
            rows = [(h[0], h[1]) for h in sorted(hs, key=lambda h: h[1], reverse=True)[:1]]
            return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: list(rows))
        return super().execute(sql, params)


def test_contract_on_takes_the_latest_full_complete_run_and_a_fail_run_counts():
    conn = _HeaderConn([("full_pass", _ts(9), "FULL", "PASS"), ("full_fail", _ts(10), "FULL", "FAIL"),
                        ("full_incomplete", _ts(12), "FULL", "INCOMPLETE"), ("x_sample", _ts(13), "SAMPLE", "PASS")])
    scope = gating.load_scope(conn, "p2_metrics", ["IN1"], {}, contract=True)
    assert scope.run_id == "full_fail" and scope.run_ts == _ts(10) and scope.selection_note == ""


def test_contract_on_with_only_incomplete_or_sample_runs_has_no_evidence_and_says_why():
    conn = _HeaderConn([("full_incomplete", _ts(12), "FULL", "INCOMPLETE"), ("x_sample", _ts(13), "SAMPLE", "PASS")])
    scope = gating.load_scope(conn, "p2_metrics", ["IN1"], {}, contract=True)
    assert not scope.has_run and "INCOMPLETE" in scope.selection_note


def test_contract_on_without_the_header_table_fails_closed():
    scope = gating.load_scope(_HeaderConn([], table=False), "p2_metrics", ["IN1"], {}, contract=True)
    assert not scope.has_run and "migrate_audit_run.py" in scope.selection_note


def test_contract_off_ignores_the_headers():
    conn = _HeaderConn([("full_pass", _ts(9), "FULL", "PASS")])
    assert gating.load_scope(conn, "p2_metrics", ["IN1"], {}, contract=False).run_id == "legacy_run"
    assert not any("audit_run" in s for s in conn.sql)


def test_contract_default_is_the_config_switch(monkeypatch):
    import shared.config as cfg
    conn = _HeaderConn([("full_pass", _ts(9), "FULL", "PASS")])
    monkeypatch.setattr(cfg, "AUDIT_COMPLETENESS_CONTRACT_ENABLED", True)
    assert gating.load_scope(conn, "p2_metrics", ["IN1"], {}).run_id == "full_pass"
