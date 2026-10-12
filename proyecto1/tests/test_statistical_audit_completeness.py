# proyecto1/tests/test_statistical_audit_completeness.py
# -*- coding: utf-8 -*-
"""FND-0261 (GOV-02): the audit completeness contract -- skip classification, run status, exit code, the runner's skip sites, the
control.audit_run DDL and its join with telemetry, the run identity. No database (R-7); the transactional write is covered by
test_statistical_audit_audit_run_pg.py."""
from __future__ import annotations

import ast
import os
import re
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import shared.config as cfg  # noqa: E402
from shared.governance import controls as gc  # noqa: E402
from shared.governance import run_identity as ri  # noqa: E402
from shared.statistical_audit import completeness as cpl  # noqa: E402
from shared.statistical_audit.completeness import Skip  # noqa: E402
from shared.statistical_audit.persistence import AUDIT_RUN_COLUMNS  # noqa: E402

_RUNNER = os.path.join(_ROOT, "scripts", "audit", "run_statistical_audit.py")
_CONTROL_SQL = os.path.join(_ROOT, "db", "pg", "35_control.sql")

DEFECT = {"block": "BLOCK5", "rule_id": "X", "rule_class": "HARD_INVARIANT", "severity": "ALARM"}
REVIEW = {"block": "BLOCK4", "rule_id": "OUTLIER_MAD_Z", "rule_class": "STATISTICAL_ANOMALY", "severity": "ALARM"}
INFO = {"block": "BLOCK3", "rule_id": "PATHOLOGICAL_SHAPE_EXPECTED", "rule_class": "STATISTICAL_ANOMALY", "severity": "INFO"}


# --- classification ------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("check,kind,scope,expected", [
    ("COST_COLUMN", cpl.KIND_MISSING, cpl.SCOPE_FULL, cpl.RESOLVED_MISSING),
    ("COST_COLUMN", cpl.KIND_MISSING, cpl.SCOPE_SAMPLE, cpl.RESOLVED_MISSING),           # a missing input is missing in any scope
    ("BETA_ORPHAN_BATCH", cpl.KIND_EMPTY, cpl.SCOPE_FULL, cpl.RESOLVED_MISSING),          # required + empty universe = missing input
    ("BETA_ORPHAN_BATCH", cpl.KIND_EMPTY, cpl.SCOPE_SAMPLE, cpl.RESOLVED_LEGIT),          # 40 ISINs may hold no betas
    ("ALERT_RECONCILIATION", cpl.KIND_EMPTY, cpl.SCOPE_FULL, cpl.RESOLVED_LEGIT),         # optional check
    ("PEER_SEGMENT", cpl.KIND_NOT_APPLICABLE, cpl.SCOPE_FULL, None),                      # by design: never counts
])
def test_a_skip_resolves_by_kind_scope_and_requirement(check, kind, scope, expected):
    assert cpl.resolve(Skip(check, kind, "d"), scope) == expected


def test_an_unregistered_check_or_kind_is_refused_at_the_skip_site():
    with pytest.raises(ValueError, match="register it"):
        Skip("NEW_CHECK", cpl.KIND_EMPTY, "d")
    with pytest.raises(ValueError):
        Skip("COST_COLUMN", "SKIPPED", "d")


def test_scope_of_a_run_follows_isin_scoping():
    assert cpl.scope_of(None) == cpl.SCOPE_FULL and cpl.scope_of([]) == cpl.SCOPE_FULL
    assert cpl.scope_of(["LU0000000001"]) == cpl.SCOPE_SAMPLE


# --- status and exit code ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("skips,findings,expected", [
    ([], [], gc.STATE_PASS),
    ([], [INFO], gc.STATE_PASS),
    ([], [REVIEW, INFO], gc.STATE_PASS_WITH_WARNINGS),
    ([], [REVIEW, DEFECT], gc.STATE_FAIL),
    ([Skip("INVARIANT", cpl.KIND_MISSING, "d")], [DEFECT], gc.STATE_INCOMPLETE),         # could not look outranks looked and failed
    ([Skip("PEER_SEGMENT", cpl.KIND_NOT_APPLICABLE, "d")], [], gc.STATE_PASS),
])
def test_run_status_precedence(skips, findings, expected):
    assert cpl.run_status(skips, findings, cpl.SCOPE_FULL) == expected


def test_status_uses_the_same_defect_predicate_as_the_gate():
    assert cpl.run_status([], [{"block": "BLOCK2", "rule_id": "P", "rule_class": "STATISTICAL_ANOMALY"}], cpl.SCOPE_FULL) == gc.STATE_FAIL
    assert cpl.run_status([], [{"block": "BLOCK6", "rule_id": "TIMESERIES_DUPLICATE", "rule_class": "PLAUSIBILITY"}],
                          cpl.SCOPE_FULL) == gc.STATE_FAIL


@pytest.mark.parametrize("status,enabled,mode,blocking,rc", [
    (gc.STATE_INCOMPLETE, True, "report", False, 2),
    (gc.STATE_INCOMPLETE, True, "check", True, 2),
    (gc.STATE_INCOMPLETE, False, "report", False, 0),      # switch OFF: shadow only, RC unchanged
    (gc.STATE_INCOMPLETE, False, "check", True, 1),
    (gc.STATE_FAIL, True, "check", True, 1),
    (gc.STATE_FAIL, True, "report", True, 0),
    (gc.STATE_PASS, True, "check", False, 0),
])
def test_exit_code(status, enabled, mode, blocking, rc):
    assert cpl.exit_code(status, contract_enabled=enabled, mode=mode, blocking=blocking) == rc


def test_the_switch_ships_off():
    # Flipping it is an owner decision after the migration and one shadow cycle: change this test in that same reviewed commit.
    assert cfg.AUDIT_COMPLETENESS_CONTRACT_ENABLED is False


def test_statuses_are_the_governance_states():
    assert {gc.STATE_PASS, gc.STATE_PASS_WITH_WARNINGS, gc.STATE_FAIL, gc.STATE_INCOMPLETE} == set(gc.STATES)
    assert cpl.RC_INCOMPLETE == 2


# --- the runner's skip sites (contract: every skip is classified and registered) ------------------------------------------------
def _runner_tree():
    with open(_RUNNER, encoding="utf-8") as f:
        return ast.parse(f.read())


def _skip_calls():
    calls = []
    for node in ast.walk(_runner_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "skip":
            calls.append(node)
    return calls


def test_every_skip_site_names_a_registered_check_and_a_kind_constant():
    calls = _skip_calls()
    assert len(calls) >= 20, "the runner's skip sites were not found"
    used = set()
    for call in calls:
        check, kind = call.args[0], call.args[1]
        assert isinstance(check, ast.Constant) and check.value in cpl.CHECKS, ast.dump(check)
        assert isinstance(kind, ast.Name) and kind.id in {"KIND_MISSING", "KIND_EMPTY", "KIND_NOT_APPLICABLE"}, ast.dump(kind)
        used.add(check.value)
    assert used == set(cpl.CHECKS), f"registered but never skipped: {sorted(set(cpl.CHECKS) - used)}"


def test_nothing_appends_to_skipped_except_the_classified_skip_method():
    offenders = []
    for node in ast.walk(_runner_tree()):
        if isinstance(node, ast.FunctionDef) and node.name != "skip":
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "append"
                        and isinstance(sub.func.value, ast.Attribute) and sub.func.value.attr == "skipped"):
                    offenders.append((node.name, sub.lineno))
    assert offenders == []


# --- control.audit_run DDL and its join with telemetry --------------------------------------------------------------------------
def _create_table(name):
    sys.path.insert(0, os.path.join(_ROOT, "scripts", "ops"))
    from migrate_fund_metric_family_state import ddl_sql
    return ddl_sql(table=name)


def _columns(ddl):
    body = ddl[ddl.index("(") + 1:ddl.rindex(")")]
    return [m.group(1) for m in re.finditer(r"^\s{4}([a-z_]+)\s+\w", body, re.M)]       # one column per line; CONSTRAINT is upper


def _check_values(ddl, column):
    m = re.search(rf"{column}\s+text\s+NOT NULL CHECK \({column} IN \(([^)]*)\)\)", ddl)
    assert m, f"CHECK on {column} not found"
    return set(re.findall(r"'([A-Z_a-z0-9]+)'", m.group(1)))


def test_audit_run_columns_are_the_key_the_database_default_and_what_the_writer_sets():
    cols = _columns(_create_table("control.audit_run"))
    assert set(cols) == {"run_id", "domain", "finished_at"} | set(AUDIT_RUN_COLUMNS)


def test_audit_run_check_domains_match_the_code():
    ddl = _create_table("control.audit_run")
    assert _check_values(ddl, "status") == set(gc.STATES)
    assert _check_values(ddl, "scope") == set(cpl.SCOPES)
    assert _check_values(ddl, "domain") == _check_values(_create_table("control.audit_finding"), "domain")


def test_audit_run_carries_no_telemetry_counts_and_joins_cycle_audit_run_on_run_id():
    run_cols = set(_columns(_create_table("control.audit_run")))
    tele_ddl = _create_table("control.cycle_audit_run")
    tele_cols = set(_columns(tele_ddl))
    counts = set(re.findall(r"\b(n_[a-z]+)\s+integer", tele_ddl))                     # several share a line there
    assert {"n_alarm", "n_warn", "n_info", "n_new", "n_resolved"} <= counts, "cycle_audit_run counts not found"
    assert not run_cols & counts and not any(c.startswith("n_") for c in run_cols)
    assert {"run_id", "domain"} <= run_cols & tele_cols


def test_the_migration_script_targets_the_table():
    with open(os.path.join(_ROOT, "scripts", "ops", "migrate_audit_run.py"), encoding="utf-8") as f:
        assert 'TABLE = "control.audit_run"' in f.read()


# --- run identity -----------------------------------------------------------------------------------------------------------------
def test_config_hash_covers_switches_and_mandate_limits_only():
    base = SimpleNamespace(A_ENABLED=False, MAX_X=0.3, OTHER=1, lower_ENABLED=True)
    h = ri.config_hash(base)
    assert h.startswith(ri.CONFIG_HASH_VERSION + ":")
    assert ri.config_hash(SimpleNamespace(A_ENABLED=True, MAX_X=0.3, OTHER=1)) != h          # a flipped switch changes it
    assert ri.config_hash(SimpleNamespace(A_ENABLED=False, MAX_X=0.25, OTHER=1)) != h        # a changed limit changes it
    assert ri.config_hash(SimpleNamespace(A_ENABLED=False, MAX_X=0.3, OTHER=2)) == h         # anything else does not
    assert [n for n, _v in ri.config_items(cfg)] and all(n.endswith("_ENABLED") or n.startswith("MAX_") for n, _v in ri.config_items(cfg))
    assert ("AUDIT_COMPLETENESS_CONTRACT_ENABLED", False) in ri.config_items(cfg)


def test_git_state_outside_a_repository_is_unknown_not_an_error(tmp_path):
    assert ri.git_state(tmp_path) == (None, None)
    assert ri.describe_git_state((None, None)) == "git n/a"
    assert ri.describe_git_state(("abc1234", True)) == "git abc1234 clean"
    assert ri.describe_git_state(("abc1234", False)) == "git abc1234 dirty-tracked"


def test_the_runner_header_has_exactly_the_writer_columns(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_statistical_audit_cpl", _RUNNER)
    runner = importlib.util.module_from_spec(spec)
    sys.modules["run_statistical_audit_cpl"] = runner
    spec.loader.exec_module(runner)
    monkeypatch.setattr(runner, "git_state", lambda: ("abc1234", True))
    run = runner.AuditRun("p2_metrics")
    run.universe_size = 3
    run.skip("BETA_ORPHAN_BATCH", cpl.KIND_EMPTY, "no beta rows")
    header = runner._run_header(run, cpl.SCOPE_FULL, gc.STATE_INCOMPLETE, datetime(2026, 10, 12, tzinfo=timezone.utc), "cv1")
    assert tuple(header) == AUDIT_RUN_COLUMNS
    assert header["skips"] == [{"check": "BETA_ORPHAN_BATCH", "kind": "EMPTY", "resolved": "MISSING", "detail": "no beta rows"}]
    assert (header["git_commit"], header["git_clean"], header["universe_size"]) == ("abc1234", True, 3)
    assert run.skipped == ["no beta rows"]                                               # the printed list is kept
