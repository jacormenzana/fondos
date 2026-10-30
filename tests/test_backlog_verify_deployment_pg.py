# tests/test_backlog_verify_deployment_pg.py
# -*- coding: utf-8 -*-
"""The zero-input verifier against the real DDL (hermetic throwaway PostgreSQL, rolled-back savepoint): production-shaped state before and after the steps, and the
mistakes it exists to catch (wrong database, gate in the wrong mode, a step not applied).

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_verify_deployment_pg.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(_ROOT / "scripts" / "audit"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_verify_deployment as vd  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402
from shared import backlog_client as bc  # noqa: E402
from test_backlog_transitions_pg import _base  # noqa: E402

PROD = vd.PRODUCTION_TARGET


def enforce(conn):
    conn.execute("INSERT INTO backlog.gate_switch (gate, mode, actor, reason) VALUES ('DELIVERY', 'ENFORCE', 'system:owner-waiver', 'enforced by a test of the verifier')")


def run(conn, baseline, **kw):
    kw.setdefault("actual_target", PROD)
    return vd.verify(conn, baseline, **kw)


def failed(results):
    return sorted(f"{s}: {n}" for s, n, ok, _ in results if not ok)


@pytest.fixture
def before(pg_conn):
    """Production as it is before this deployment: step A-1 and Phase B of the previous release are NOT modelled; the schema is plain, with the shared writer role."""
    _base(pg_conn)
    return pg_conn


@pytest.fixture
def deployed(before):
    """Both steps applied and the gate in ENFORCE (two rows, as in production)."""
    mig.migrate(before, "transitions")
    mig.migrate(before, "relations")
    before.execute("INSERT INTO backlog.gate_switch (gate, mode, actor, reason) VALUES ('DELIVERY', 'AUDIT', 'system:test', 'a second row so that two exist in the test')")
    enforce(before)
    mig.migrate(before, "iam")
    return before


def test_a_complete_deployment_is_verified_without_any_exit_code(deployed):
    baseline = bc.history_fingerprint(conn=deployed)
    results = run(deployed, baseline, min_gate_rows=2)
    assert failed(results) == []
    assert any("not needed" in n for _, n, _, _ in results)                                           # the exit-code criteria are explicit "not needed", twice (relations and iam)
    assert [n for _, n, ok, _ in results if n.startswith("migration exit code")] == ["migration exit code: not needed"] * 2
    info = vd.information(deployed)
    assert info[0].startswith("bindings in force: 0") and any("NOT bound" in line for line in info)
    assert vd.render(results, info, ["H"]).splitlines()[-1] == "RESULT: DEPLOYED AND VERIFIED (all criteria met)"


def test_a_step_that_was_not_applied_is_found_from_the_schema_alone(before):
    mig.migrate(before, "transitions")
    mig.migrate(before, "relations")
    enforce(before)
    baseline = bc.history_fingerprint(conn=before)
    problems = failed(run(before, baseline, min_gate_rows=1))
    assert any(p.startswith("iam:") for p in problems) and not any(p.startswith("relations:") for p in problems)           # iam missing, relations fine
    assert any("roles" in p or "backlog_reader" in p for p in problems)


def test_the_upgrade_of_phase_b_not_applied_is_found_by_the_override_column(before):
    """What the owner named: gate_switch.override. A schema with Phase B but without the FND-0350 column fails the relations criteria."""
    mig.migrate(before, "transitions")
    mig.migrate(before, "relations")
    enforce(before)
    before.execute("ALTER TABLE backlog.gate_switch DROP COLUMN override CASCADE")
    baseline = bc.history_fingerprint(conn=before)
    assert any("override" in p for p in failed(run(before, baseline, min_gate_rows=1)))


def test_the_wrong_database_is_caught_even_when_the_schema_is_perfect(deployed):
    baseline = bc.history_fingerprint(conn=deployed)
    for wrong in ("127.0.0.1:5433/des", "127.0.0.1:5436/fondos", "127.0.0.1:5432/gestion"):
        assert failed(run(deployed, baseline, actual_target=wrong)) == ["target: the connection is the production database"], wrong


def test_the_gate_mode_and_its_rows_are_checked(deployed):
    baseline = bc.history_fingerprint(conn=deployed)
    assert failed(run(deployed, baseline, expect_gate="AUDIT")) == ["gate: the delivery gate is in AUDIT mode"]
    assert failed(run(deployed, baseline, min_gate_rows=99)) == ["gate: gate_switch kept its rows (at least 99)"]


def test_a_change_of_history_since_the_baseline_is_found(deployed):
    baseline = bc.history_fingerprint(conn=deployed)
    baseline = dict(baseline, logs_md5="0" * 32)                                                     # a baseline that no longer matches the history
    assert any("history up to the baseline ids is identical" in p for p in failed(run(deployed, baseline, min_gate_rows=2)))


def test_a_database_without_phase_b_does_not_crash_the_verifier(before):
    mig.migrate(before, "transitions")
    baseline = bc.history_fingerprint(conn=before)
    results = run(before, baseline)
    assert "gate: the delivery gate is in ENFORCE mode" in failed(results) and any(p.startswith("relations:") for p in failed(results))


def test_the_record_is_written_where_the_owner_wants_it(deployed, tmp_path, monkeypatch):
    """main() writes the record to --record-dir (default deployments/); here a temporary directory stands for it."""
    baseline = bc.history_fingerprint(conn=deployed)
    (tmp_path / "base").mkdir()
    (tmp_path / "base" / "fnd0331_baseline_20261029T224146Z.json").write_text(__import__("json").dumps(baseline), encoding="utf-8")
    monkeypatch.setattr(bc, "_dsn", lambda: "postgresql://x@127.0.0.1:5436/gestion")

    class Session:
        def __enter__(self):
            return deployed

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(bc, "_session", lambda *a, **k: Session())
    rc = vd.main(["--baseline-dir", str(tmp_path / "base"), "--record-dir", str(tmp_path / "rec"), "--min-gate-rows", "2"])
    files = list((tmp_path / "rec").glob("*_verification_*.txt"))
    assert rc == vd.RC_OK and len(files) == 1 and files[0].read_text(encoding="utf-8").strip().endswith("DEPLOYED AND VERIFIED (all criteria met)")
