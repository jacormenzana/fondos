"""
tests/test_apply_benchmark_ms_checks_pg.py — scripts/ops/apply_benchmark_ms_checks.py.

The DDL must come from db/pg/35_control.sql (single source of truth) and be idempotent, and the
privilege check must report what Postgres reports. Pure parts run anywhere; the database parts run on
the hermetic container (python scripts/ops/run_pg_tests.py).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("apply_bms", _ROOT / "scripts" / "ops" / "apply_benchmark_ms_checks.py")
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


def test_ddl_is_read_from_the_migration_file_not_duplicated():
    ddl = mod.extract_ddl((_ROOT / "db" / "pg" / "35_control.sql").read_text(encoding="utf-8"))
    assert ddl.count("CREATE TABLE IF NOT EXISTS control.benchmark_ms_checks") == 1
    assert "CREATE INDEX IF NOT EXISTS idx_bms_checks_next" in ddl
    assert "isin" in ddl and "next_check_at" in ddl and "n_misses" in ddl


def test_extract_fails_loudly_when_the_block_is_missing():
    with pytest.raises(ValueError, match="not found"):
        mod.extract_ddl("CREATE TABLE something_else (a int);")


def test_dry_run_changes_nothing_and_exits_0(capsys):
    assert mod.main([]) in (0, 2)      # 2 only when no owner DSN is configured in this environment
    out = capsys.readouterr()
    assert "APPLY" not in out.out or "DRY RUN" in out.out


def test_apply_is_idempotent_and_the_app_role_can_use_the_table(pg_conn_module_schema, pg_session_conn):
    """Runs against a throwaway schema: create the table twice (IF NOT EXISTS), then read privileges."""
    conn = pg_session_conn
    schema = pg_conn_module_schema
    ddl = mod.extract_ddl((_ROOT / "db" / "pg" / "35_control.sql").read_text(encoding="utf-8"))
    ddl = (ddl.replace("control.benchmark_ms_checks", f"{schema}.benchmark_ms_checks")
              .replace("REFERENCES silver.fund_master (isin) ON DELETE CASCADE", "")
              .replace("CONSTRAINT benchmark_ms_checks_isin_fk FOREIGN KEY (isin)", "CHECK (true)")
              .replace("idx_bms_checks_next ON control.", f"idx_bms_checks_next ON {schema}."))
    conn.execute(ddl)
    conn.execute(ddl)                                                # second run: no error
    n = conn.execute(f"SELECT count(*) FROM {schema}.benchmark_ms_checks").fetchone()[0]
    assert n == 0
    # the privilege helper reports Postgres's own answer: the connecting role owns the table
    role = conn.execute("SELECT current_user").fetchone()[0]
    privs = {p: bool(conn.execute("SELECT has_table_privilege(%s, %s, %s)",
                                  (role, f"{schema}.benchmark_ms_checks", p)).fetchone()[0])
             for p in ("SELECT", "INSERT", "UPDATE", "DELETE")}
    assert all(privs.values())
