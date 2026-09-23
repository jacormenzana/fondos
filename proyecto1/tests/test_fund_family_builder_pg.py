# proyecto1/tests/test_fund_family_builder_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration Phase 5c (2026-09-20) — dedicated regression tests for
core.fund_family_builder's three write functions: correct_family_inconsistencies(),
build_fund_families(), and _populate_fund_families().

All three call conn.commit() internally, so — per the mark_stale_for_refresh incident
(see test_io_pg_mark_stale.py and project memory) — every test here uses
pg_conn_module_schema (isolated, disposable schema), NEVER the bare pg_conn SAVEPOINT
fixture. A bare SAVEPOINT does not survive an internal commit.
"""
from __future__ import annotations

import importlib.util
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
_P1_DIR = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
if _P1_DIR not in sys.path:
    sys.path.insert(0, _P1_DIR)

_spec = importlib.util.spec_from_file_location(
    "fund_family_builder_pg_test", os.path.join(_CORE_DIR, "fund_family_builder.py")
)
_ffb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ffb)
correct_family_inconsistencies = _ffb.correct_family_inconsistencies
build_fund_families = _ffb.build_fund_families
_populate_fund_families = _ffb._populate_fund_families


def _make_fund_master(conn):
    conn.execute("""
        CREATE TABLE fund_master (
            isin TEXT PRIMARY KEY,
            fund_name TEXT,
            management_company TEXT,
            fund_nature TEXT,
            fund_family_id TEXT,
            data_quality_flag TEXT,
            srri_quality_flag TEXT,
            family TEXT
        )
    """)


def _make_ingestion_log(conn):
    conn.execute("""
        CREATE TABLE ingestion_log (
            id SERIAL PRIMARY KEY,
            isin TEXT,
            step TEXT,
            status TEXT,
            message TEXT,
            created_at TEXT
        )
    """)


def _make_fund_families(conn):
    conn.execute("""
        CREATE TABLE fund_families (
            family_id TEXT PRIMARY KEY,
            family_name TEXT,
            fund_nature TEXT,
            n_funds INTEGER,
            updated_at TEXT
        )
    """)


def test_correct_family_inconsistencies_applies_restantes_override_and_reapplies_bl64e(
    pg_session_conn, pg_conn_module_schema,
):
    """Family FAM_T1 has 2 members: one 'Restantes' (fallback classifier), one a concrete
    Nature ('Renta Fija Corto Plazo') — rule 2-bis-A of _resolve_family_nature says the
    concrete Nature always wins over Restantes. Confirms: (1) the Restantes member's
    Fund_Nature is corrected; (2) since the corrected Nature is 'Renta Fija Corto Plazo',
    the BL64E_FAMCORR_REAPPLIED path re-checks Family against RFC_INCOMPATIBLE_FAMILIES and
    fixes it when incompatible; (3) both corrections are logged to ingestion_log; (4) commit
    survives (conn.commit() runs without raising — the fail-soft ingestion_log INSERTs must
    not poison the transaction)."""
    conn = pg_session_conn
    schema = pg_conn_module_schema
    conn.execute(f"SET search_path = {schema}")
    _make_fund_master(conn)
    _make_ingestion_log(conn)

    conn.execute("""
        INSERT INTO fund_master
            (isin, fund_name, fund_nature, fund_family_id, data_quality_flag,
             srri_quality_flag, family)
        VALUES
            ('R1', 'Some Bond Fund A', 'Restantes', 'FAM_T1', 'OK', 'HIGH',
             'Emerging Market Debt'),
            ('R2', 'Some Bond Fund B', 'Renta Fija Corto Plazo', 'FAM_T1', 'OK', 'HIGH',
             'Short-Term Fixed Income')
    """)

    n = correct_family_inconsistencies(conn, dry_run=False)

    assert n == 1, f"expected exactly 1 correction (R1), got {n}"

    rows = {
        r[0]: (r[1], r[2])
        for r in conn.execute("SELECT isin, fund_nature, family FROM fund_master").fetchall()
    }
    assert rows["R1"] == ("Renta Fija Corto Plazo", "Short-Term Fixed Income"), (
        "R1 must be corrected to the concrete Nature (rule 2-bis-A) AND have its "
        "incompatible Family re-fixed (BL64E_FAMCORR_REAPPLIED)"
    )
    assert rows["R2"] == ("Renta Fija Corto Plazo", "Short-Term Fixed Income"), (
        "R2 was already correct and must be untouched"
    )

    log_steps = [
        r[0] for r in conn.execute("SELECT step FROM ingestion_log ORDER BY id").fetchall()
    ]
    assert "BL64E_FAMCORR_REAPPLIED" in log_steps
    assert "FAMILY_NATURE_CORRECTION" in log_steps


def test_correct_family_inconsistencies_dry_run_makes_no_changes(
    pg_session_conn, pg_conn_module_schema,
):
    """dry_run=True must report the correction count without writing anything — confirms the
    early-return branch (never reaches the executemany/commit code) is dialect-safe too."""
    conn = pg_session_conn
    schema = pg_conn_module_schema
    conn.execute(f"SET search_path = {schema}")
    _make_fund_master(conn)
    _make_ingestion_log(conn)

    conn.execute("""
        INSERT INTO fund_master
            (isin, fund_name, fund_nature, fund_family_id, data_quality_flag,
             srri_quality_flag, family)
        VALUES
            ('R1', 'Some Bond Fund A', 'Restantes', 'FAM_T1', 'OK', 'HIGH', NULL),
            ('R2', 'Some Bond Fund B', 'Renta Fija Corto Plazo', 'FAM_T1', 'OK', 'HIGH', NULL)
    """)

    n = correct_family_inconsistencies(conn, dry_run=True)

    assert n == 1
    natures = dict(conn.execute("SELECT isin, fund_nature FROM fund_master").fetchall())
    assert natures["R1"] == "Restantes", "dry_run must not mutate fund_master"
    assert conn.execute("SELECT COUNT(*) FROM ingestion_log").fetchone()[0] == 0


def test_populate_fund_families_replaces_rows(pg_session_conn, pg_conn_module_schema):
    """Confirms the DELETE+executemany INSERT rebuild: dominant Fund_Nature per family_id is
    computed from fund_master and the fund_families table is fully replaced (old rows gone,
    new rows present) — matches SQLite's DELETE-then-INSERT semantics exactly."""
    conn = pg_session_conn
    schema = pg_conn_module_schema
    conn.execute(f"SET search_path = {schema}")
    _make_fund_master(conn)
    _make_fund_families(conn)

    conn.execute("""
        INSERT INTO fund_master (isin, fund_name, fund_nature, fund_family_id) VALUES
        ('A1', 'Fund A Acc', 'Renta Variable', 'FAM_000001'),
        ('A2', 'Fund A Dist', 'Renta Variable', 'FAM_000001'),
        ('B1', 'Fund B', 'Monetario', 'FAM_000002')
    """)
    conn.execute("""
        INSERT INTO fund_families (family_id, family_name, fund_nature, n_funds, updated_at)
        VALUES ('FAM_STALE', 'Stale Leftover Family', 'Alternativo', 1, '2020-01-01')
    """)

    family_data = [
        ("FAM_000001", "Fund A", 2),
        ("FAM_000002", "Fund B", 1),
    ]
    n = _populate_fund_families(conn, family_data)

    assert n == 2
    rows = {
        r[0]: (r[1], r[2], r[3])
        for r in conn.execute(
            "SELECT family_id, family_name, fund_nature, n_funds FROM fund_families"
        ).fetchall()
    }
    assert "FAM_STALE" not in rows, "old rows must be gone (DELETE-then-INSERT rebuild)"
    assert rows["FAM_000001"] == ("Fund A", "Renta Variable", 2)
    assert rows["FAM_000002"] == ("Fund B", "Monetario", 1)


def test_build_fund_families_end_to_end_assigns_ids_and_populates(
    pg_session_conn, pg_conn_module_schema,
):
    """End-to-end: two share classes of the same underlying fund (same Management_Company,
    names differing only by a class-suffix token) must land in the same fund_family_id; a
    third, unrelated fund gets its own singleton family. Confirms the full pipeline
    (executemany UPDATE -> correct_family_inconsistencies -> _populate_fund_families) runs
    clean under Postgres, including its 2 internal conn.commit() calls."""
    conn = pg_session_conn
    schema = pg_conn_module_schema
    conn.execute(f"SET search_path = {schema}")
    _make_fund_master(conn)
    _make_ingestion_log(conn)
    _make_fund_families(conn)

    conn.execute("""
        INSERT INTO fund_master
            (isin, fund_name, management_company, fund_nature, data_quality_flag,
             srri_quality_flag)
        VALUES
            ('C1', 'Global Growth Fund A EUR Acc', 'Acme AM', 'Renta Variable', 'OK', 'HIGH'),
            ('C2', 'Global Growth Fund A USD Acc', 'Acme AM', 'Renta Variable', 'OK', 'HIGH'),
            ('C3', 'Standalone Money Market Fund', 'Acme AM', 'Monetario', 'OK', 'HIGH')
    """)

    n = build_fund_families(conn, dry_run=False)

    assert n == 3, f"expected all 3 ISINs updated with a fund_family_id, got {n}"

    fam_ids = dict(conn.execute("SELECT isin, fund_family_id FROM fund_master").fetchall())
    assert fam_ids["C1"] == fam_ids["C2"], "same underlying fund (class-suffix-only diff) must share a family"
    assert fam_ids["C3"] != fam_ids["C1"], "unrelated fund must get its own family"

    families = {
        r[0]: r[1]
        for r in conn.execute("SELECT family_id, n_funds FROM fund_families").fetchall()
    }
    assert families[fam_ids["C1"]] == 2
    assert families[fam_ids["C3"]] == 1


def _make_fund_master_with_family_fk(conn):
    """The real Postgres schema: fund_master.fund_family_id REFERENCES fund_families (db/pg/20_silver.sql).
    The helpers above build both tables WITHOUT that FK, which is why the launcher-level failure
    (DELETE FROM fund_families -> ForeignKeyViolation) went unseen until a real P1 launcher step ran."""
    _make_fund_families(conn)
    conn.execute("""
        CREATE TABLE fund_master (
            isin TEXT PRIMARY KEY, fund_name TEXT, management_company TEXT, fund_nature TEXT,
            fund_family_id TEXT REFERENCES fund_families (family_id),
            data_quality_flag TEXT, srri_quality_flag TEXT, family TEXT
        )
    """)


def test_rebuild_respects_the_real_family_foreign_key(pg_session_conn, pg_conn_module_schema):
    """A re-run on an already-populated database: ids are re-issued sequentially, one family is new
    (its parent row must exist before fund_master is repointed), one old family is now unreferenced
    (must be dropped), and nothing may violate fund_master_family_fk."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_master_with_family_fk(conn)
    _make_ingestion_log(conn)
    conn.execute("""
        INSERT INTO fund_families (family_id, family_name, fund_nature, n_funds, updated_at) VALUES
        ('FAM_000001', 'Old Name One', 'Monetario', 1, '2020-01-01'),
        ('FAM_000002', 'Old Name Two', 'Monetario', 1, '2020-01-01'),
        ('FAM_STALE',  'Gone',         'Alternativo', 1, '2020-01-01')
    """)
    conn.execute("""
        INSERT INTO fund_master
            (isin, fund_name, management_company, fund_nature, fund_family_id, data_quality_flag, srri_quality_flag)
        VALUES
            ('C1', 'Global Growth Fund A EUR Acc', 'Acme AM', 'Renta Variable', 'FAM_000001', 'OK', 'HIGH'),
            ('C2', 'Global Growth Fund A USD Acc', 'Acme AM', 'Renta Variable', 'FAM_000001', 'OK', 'HIGH'),
            ('C3', 'Standalone Money Market Fund', 'Acme AM', 'Monetario',      'FAM_STALE',  'OK', 'HIGH'),
            ('D1', 'Zeta Fund',                    'Zed AM',  'Renta Variable', 'FAM_000002', 'OK', 'HIGH')
    """)

    n = build_fund_families(conn, dry_run=False)

    assert n == 4
    fam = dict(conn.execute("SELECT isin, fund_family_id FROM fund_master").fetchall())
    assert fam["C1"] == fam["C2"] and len({fam["C1"], fam["C3"], fam["D1"]}) == 3
    families = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT family_id, fund_nature, n_funds FROM fund_families").fetchall()}
    assert set(families) == set(fam.values()), "exactly the referenced families remain (FAM_STALE dropped)"
    assert "FAM_STALE" not in families
    assert families[fam["C1"]] == ("Renta Variable", 2)
    assert families[fam["D1"]][1] == 1
