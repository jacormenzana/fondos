# proyecto3/tests/test_scenario_status_ddl_pg.py
# -*- coding: utf-8 -*-
"""
The scenario-lifecycle DDL on Postgres (owner decision 2026-10-11): the block of db/pg/30_gold.sql that
scripts/ops/migrate_portfolio_scenario_status.py applies. Hermetic; no Python lifecycle code is imported here.

Two layers:
  * the REAL `gold` tables of the throwaway database (DDL loaded from db/pg/*.sql) inside a rolled-back savepoint: the columns exist and the
    CHECK / default / partial unique index behave;
  * toy tables in a per-test schema running the SAME block text (the migration's own extraction): backfill, archiving, idempotency.

    python scripts/ops/run_pg_tests.py -- proyecto3/tests/test_scenario_status_ddl_pg.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scenario_pg_support import apply_block, db, statuses  # noqa: E402,F401  (db is a fixture)


def test_the_real_gold_table_has_the_lifecycle_columns_and_rules(pg_app_conn):
    conn = pg_app_conn
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='gold' AND table_name='portfolio_scenarios'").fetchall()}
    assert {"status", "promoted_at", "promoted_by", "supersedes", "built_at"} <= cols
    ins = "INSERT INTO gold.portfolio_scenarios (scenario_id, profile, created_at, status) VALUES (%s, 'Equilibrada', '2026-10-11', %s)"
    conn.execute("INSERT INTO gold.portfolio_scenarios (scenario_id, profile, created_at) VALUES ('t_default', 'Equilibrada', '2026-10-11')")
    assert conn.execute("SELECT status FROM gold.portfolio_scenarios WHERE scenario_id='t_default'").fetchone()[0] == "DRAFT"
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute(ins, ("t_bad", "PUBLISHED"))
    conn.execute(ins, ("t_p1", "PRODUCTION"))
    with pytest.raises(psycopg.errors.UniqueViolation):
        with conn.transaction():
            conn.execute(ins, ("t_p2", "PRODUCTION"))


def test_built_at_is_a_not_null_timestamptz_that_defaults_to_the_build_instant(pg_app_conn):
    conn = pg_app_conn
    typ, nullable = conn.execute(
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema='gold' AND table_name='portfolio_scenarios' AND column_name='built_at'").fetchone()
    assert typ == "timestamp with time zone" and nullable == "NO"
    conn.execute("INSERT INTO gold.portfolio_scenarios (scenario_id, profile, created_at) VALUES ('t_built', 'Equilibrada', '2026-10-11')")
    assert conn.execute("SELECT built_at IS NOT NULL FROM gold.portfolio_scenarios WHERE scenario_id='t_built'").fetchone()[0]


def test_migration_archives_old_scenarios_keeps_the_newest_as_draft_and_is_idempotent(db):
    assert statuses(db) == {"legacy_a": "ARCHIVED", "legacy_b": "ARCHIVED", "legacy_c": "DRAFT"}
    db.execute("UPDATE portfolio_scenarios SET status = 'PRODUCTION' WHERE scenario_id = 'legacy_b'")
    db.commit()
    apply_block(db)                                # second run: must not touch anything
    db.commit()
    assert statuses(db) == {"legacy_a": "ARCHIVED", "legacy_b": "PRODUCTION", "legacy_c": "DRAFT"}


def test_migration_backfills_built_at_from_the_legacy_date(db):
    assert db.execute("SELECT COUNT(*) FROM portfolio_scenarios WHERE built_at IS NULL").fetchone()[0] == 0
    assert db.execute("SELECT built_at::date = created_at FROM portfolio_scenarios WHERE scenario_id='legacy_a'").fetchone()[0]
