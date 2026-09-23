"""
tests/test_pg_seed_guard.py — pg_seed.py's preflight (2026-09-23).

pg_seed is a one-shot loader for an EMPTY database. Pointed at a populated one it died half-way with
a ForeignKeyViolation; pointed at a database that had become the live primary it could TRUNCATE live
data. It now refuses both, up front and clearly. (An auto-purge "fix" was rejected on purpose: it
would make the seeder able to destroy a live database — the worse failure.) No test had ever
imported pg_seed before this file.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("pg_seed_guard_test", _ROOT / "scripts" / "mig" / "pg_seed.py")
pg_seed = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pg_seed   # @dataclass + postponed annotations look the module up here
_spec.loader.exec_module(pg_seed)


def _scratch(conn, schema: str, tables: dict[str, int]) -> None:
    conn.execute(f"CREATE SCHEMA {schema}")
    for name, n_rows in tables.items():
        conn.execute(f"CREATE TABLE {schema}.{name} (x integer)")
        for i in range(n_rows):
            conn.execute(f"INSERT INTO {schema}.{name} VALUES ({i})")


def test_target_units_expand_the_partitioned_table_into_its_five_partitions():
    names = [u for u, _, _ in pg_seed._target_units()]
    assert {"gold.fmts_p_vol_ann", "gold.fmts_p_max_dd", "gold.fmts_p_return_ann",
            "gold.fmts_p_sharpe", "gold.fmts_p_sortino"} <= set(names)
    assert "gold.fund_metric_timeseries" not in names       # progress is tracked per partition
    assert "silver.fund_master" in names and "gold.fund_scores" in names


def test_gate_already_passed_reflects_a_migration_state_row(pg_conn):
    pg_conn.execute("DELETE FROM control.migration_state")
    assert pg_seed.gate_already_passed(pg_conn) is False
    pg_conn.execute(
        "INSERT INTO control.migration_state (pg_version, source_sqlite_path, source_sqlite_size_bytes, "
        "source_sqlite_sha256, gate_results) VALUES ('17', 'x.sqlite', 1, 'abc', '{}'::jsonb)")
    assert pg_seed.gate_already_passed(pg_conn) is True


def test_populated_but_not_recorded_flags_only_rows_the_seeder_did_not_load(pg_conn):
    _scratch(pg_conn, "seedguard_t", {"t_done": 2, "t_rogue": 2, "t_pending": 1, "t_empty": 0})
    pg_conn.execute("DELETE FROM control.seed_progress WHERE target_table LIKE 'seedguard_t.%'")
    pg_conn.execute("INSERT INTO control.seed_progress (target_table, status, completed_at) "
                    "VALUES ('seedguard_t.t_done', 'done', now())")
    pg_conn.execute("INSERT INTO control.seed_progress (target_table, status) "
                    "VALUES ('seedguard_t.t_pending', 'pending')")     # 'pending' is NOT loaded
    units = [(f"seedguard_t.{t}", "seedguard_t", t) for t in ("t_done", "t_rogue", "t_pending", "t_empty")]

    assert pg_seed.populated_but_not_recorded(pg_conn, units) == ["seedguard_t.t_rogue", "seedguard_t.t_pending"]


def test_preflight_refuses_a_certified_database_unless_forced(pg_conn):
    pg_conn.execute("DELETE FROM control.migration_state")
    pg_conn.execute(
        "INSERT INTO control.migration_state (pg_version, source_sqlite_path, source_sqlite_size_bytes, "
        "source_sqlite_sha256, gate_results) VALUES ('17', 'x.sqlite', 1, 'abc', '{}'::jsonb)")
    msg = pg_seed.preflight(pg_conn, force_reseed=False, only=None)
    assert msg and "may be the live primary" in msg and "--force-reseed" in msg
    # --only is a debug partial reseed but is just as destructive to a live table: still refused.
    assert pg_seed.preflight(pg_conn, force_reseed=False, only="gold.fund_scores")
    # The override skips ONLY this refusal (the populated-table check below is separate).
    assert "live primary" not in (pg_seed.preflight(pg_conn, force_reseed=True, only="gold.fund_scores") or "")


def test_preflight_names_the_offending_tables_and_the_way_out(pg_conn, monkeypatch):
    pg_conn.execute("DELETE FROM control.migration_state")
    _scratch(pg_conn, "seedguard_p", {"t1": 1})
    pg_conn.execute("DELETE FROM control.seed_progress WHERE target_table LIKE 'seedguard_p.%'")
    monkeypatch.setattr(pg_seed, "_target_units", lambda: [("seedguard_p.t1", "seedguard_p", "t1")])

    msg = pg_seed.preflight(pg_conn, force_reseed=False, only=None)
    assert msg and "seedguard_p.t1" in msg and "EMPTY database" in msg and "Recreate the database" in msg
    # A debug --only run legitimately targets a populated unit, so the populated-table check is skipped.
    assert pg_seed.preflight(pg_conn, force_reseed=False, only="seedguard_p.t1") is None


def test_preflight_passes_on_an_empty_uncertified_database(pg_conn, monkeypatch):
    pg_conn.execute("DELETE FROM control.migration_state")
    _scratch(pg_conn, "seedguard_e", {"t1": 0})
    pg_conn.execute("DELETE FROM control.seed_progress WHERE target_table LIKE 'seedguard_e.%'")
    monkeypatch.setattr(pg_seed, "_target_units", lambda: [("seedguard_e.t1", "seedguard_e", "t1")])
    assert pg_seed.preflight(pg_conn, force_reseed=False, only=None) is None
