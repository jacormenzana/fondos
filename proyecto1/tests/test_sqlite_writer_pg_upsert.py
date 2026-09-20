# proyecto1/tests/test_sqlite_writer_pg_upsert.py
# -*- coding: utf-8 -*-
"""
Postgres migration Phase 5c (2026-09-20) — dedicated regression tests for sqlite_writer.py's
COALESCE/preservation upsert invariants, written per the migration plan's own discipline ("Every
one of these three patterns gets a dedicated regression test... asserting the specific invariant...
written before the site is touched, so the rewrite is red->green per site"). Covers the functions
ported so far; grows as more of sqlite_writer.py's write path is ported.

Named invariants under test (plan §5c):
  - upsert_fund_master(): "P#1 COALESCE upserts map to ON CONFLICT ... DO UPDATE SET
    col = COALESCE(excluded.col, tbl.col)" — a partial "CACHED-cycle" record (many fields None,
    matching real record.get() shape) must NOT overwrite existing non-null values with NULL.
  - upsert_kiid_metadata(): "fund_kiid_metadata must NOT become a blind DO UPDATE — it would wipe
    raw_kiid_text" — a CACHED-cycle record (no re-download, Raw_KIID_Text=None) must preserve the
    existing KIID text, not null it out. Also covers the KIID_Status CACHED-preservation CASE rule
    (a cached cycle must not downgrade a real 'OK' status to 'CACHED') and SRRI COALESCE.

Runs against the REAL, live-seeded tables (not a throwaway in-memory schema like the SQLite-only
tests in this directory use) — deliberate choice: the pg_conn fixture's SAVEPOINT guarantees zero
permanent mutation regardless of outcome, and testing against the actual production schema (real
NOT NULL constraints, real FK) catches real constraint issues that a hand-built minimal test schema
would not — exactly what surfaced during manual verification of upsert_fund_master's invariant
before its test was written (a first attempt with an unrealistic all-NULL partial record hit two
real NOT NULL constraints that a real classifier call would never actually violate).

R-7 note: this test necessarily imports the real production module (sqlite_writer.py) — the R-7
"no imports of pipeline.py or core.io" restriction is about avoiding the classification pipeline's
heavier dependencies, not about avoiding sqlite_writer.py itself (already the norm in this
directory — see test_cost_oc_mismatch.py, test_universe_reconcile_20260718.py, etc.).
"""
from __future__ import annotations

import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
_P1_DIR = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)
if _P1_DIR not in sys.path:
    # _upsert_kiid_benchmark does `from core.benchmark_normalizer import normalize_benchmark`
    # internally — that needs proyecto1/ itself (not just proyecto1/core/) on sys.path so 'core'
    # resolves as a package. Without this, _get_normalizer() fails silently and the function
    # early-returns with no error — a test would falsely "pass" without exercising any SQL at all
    # (found live 2026-09-20 while first verifying this function manually, before this test existed).
    sys.path.insert(0, _P1_DIR)

from sqlite_writer import (  # noqa: E402
    upsert_fund_master,
    upsert_kiid_metadata,
    insert_nav_series,
    _upsert_kiid_benchmark,
    upsert_cost_schedule,
    publish_fund,
    correct_oc_aci_mismatch,
    reconcile_universe_membership,
    global_post_pipeline_normalize_db,
    log_ingestion,
)


def _minimal_partial_record(isin: str, fund_nature: str, fund_name: str) -> dict:
    """A realistic partial 'CACHED cycle' record: only fields the classifier actually determined
    this cycle are populated; everything else is None, matching record.get()'s real shape. All
    NOT NULL columns are populated (a real classifier call never leaves these None) so the eager
    NOT NULL check Postgres performs on the proposed row (independent of ON CONFLICT resolution,
    confirmed live 2026-09-20) doesn't fire on an unrealistic test record."""
    return {
        "ISIN": isin,
        "Fund_Name": fund_name,
        "Management_Company": "TEST OVERWRITE MANAGEMENT CO",  # 'ow' policy — should overwrite
        "Fund_Nature": fund_nature,
        "Profile": None, "Vehicle_Structure": None, "Strategy": None,
        "Family": None, "Style_Profile": None, "Geography": None, "Theme": None,
        "Exposure_Bias": "Neutral", "Benchmark_Type": None,
        "Market_Cap_Focus": None, "Sector_Focus": None,
        "Investment_Universe": None, "Investment_Focus": None, "Credit_Quality": None,
        "Accumulation_Policy": None, "Heuristic_Block": "test", "Heuristic_Core": 1,
        "SRRI": None, "Fund_Currency": None, "Asset_Currency": None, "Hedging_Policy": None,
        "Replication_Method": None, "Derivatives_Usage": None, "Benchmark_Declared": None,
        "Ongoing_Charge_Recurrent": None, "Entry_Fee_Pct": None, "Exit_Fee_Pct": None,
        "Fee_Known_Flag": 0, "Sfdr_Article": None, "Recommended_Holding_Period": None,
        "Leverage_Used": 0, "Liquidity_Profile": None, "Distribution_Frequency": "Acumulacion",
        "fund_family_id": None, "Inference_Trace": "test-coalesce-invariant",
        "SRRI_Quality_Flag": None, "Data_Quality_Flag": "OK",
        "KID_Format": None, "KID_Currency": None, "Cost_Extraction_Quality": None,
        "Cost_RHP_Years": None, "Entry_Fee_Pct_Max": None, "Exit_Fee_Pct_Max": None,
        "Management_Fee_Pct": None, "Transaction_Cost_Pct": None, "Performance_Fee_Pct": None,
        "ACI_1Y": None, "ACI_RHP": None,
        "Development_Status": "Established", "Duration_Profile": "NA",
        "MMF_Structure": "NA", "Alt_Strategy": "NA", "Payoff_Profile": "Linear",
    }


def test_coalesce_preserves_existing_nonnull_values_on_partial_upsert(pg_conn):
    """A partial upsert (Style_Profile/Sector_Focus = None) against a fund that already has
    non-null values for those columns must PRESERVE the existing values, not null them out —
    the whole point of the COALESCE ON CONFLICT policy (P#1)."""
    pg_conn.execute("SET search_path = gold, silver, bronze, control, public")

    # Pick a real fund with non-null Style_Profile/Sector_Focus, so the preservation is
    # actually observable (not a vacuous NULL == NULL check).
    row = pg_conn.execute(
        "SELECT isin, fund_name, fund_nature FROM fund_master "
        "WHERE style_profile IS NOT NULL AND sector_focus IS NOT NULL LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("no fund_master row with non-null style_profile+sector_focus to test against")
    isin, fund_name, fund_nature = row[0], row[1], row[2]

    before = pg_conn.execute(
        "SELECT management_company, style_profile, sector_focus FROM fund_master WHERE isin = %s",
        (isin,),
    ).fetchone()
    before_mgmt, before_style, before_sector = before

    record = _minimal_partial_record(isin, fund_nature, fund_name)
    upsert_fund_master(pg_conn, record)

    after = pg_conn.execute(
        "SELECT management_company, style_profile, sector_focus FROM fund_master WHERE isin = %s",
        (isin,),
    ).fetchone()
    after_mgmt, after_style, after_sector = after

    # COALESCE-policy columns: must be UNCHANGED (preserved), not overwritten with the record's None.
    assert after_style == before_style, (
        f"Style_Profile was overwritten to {after_style!r} — COALESCE should have preserved "
        f"{before_style!r}"
    )
    assert after_sector == before_sector, (
        f"Sector_Focus was overwritten to {after_sector!r} — COALESCE should have preserved "
        f"{before_sector!r}"
    )
    # 'ow' (overwrite) policy column: must actually have changed, proving this isn't a no-op upsert.
    assert after_mgmt == "TEST OVERWRITE MANAGEMENT CO"
    assert after_mgmt != before_mgmt


def test_upsert_fund_master_insert_path_creates_new_row(pg_conn):
    """The INSERT branch (a brand-new ISIN, no prior row) must create the row with the values
    given — exercises the 'ins'-policy columns (ISIN, fund_family_id) and confirms the whole
    ON CONFLICT machinery doesn't only work on the UPDATE path."""
    pg_conn.execute("SET search_path = gold, silver, bronze, control, public")

    fake_isin = "TEST00000001"  # 12 chars, matching real ISIN length (isin is varchar(12))
    record = _minimal_partial_record(fake_isin, "Monetario", "TEST NEW FUND PHASE5C")
    upsert_fund_master(pg_conn, record)

    row = pg_conn.execute(
        "SELECT isin, fund_name, fund_nature, management_company FROM fund_master WHERE isin = %s",
        (fake_isin,),
    ).fetchone()
    assert row is not None, "new ISIN was not inserted"
    assert row[0] == fake_isin
    assert row[1] == "TEST NEW FUND PHASE5C"
    assert row[2] == "Monetario"
    assert row[3] == "TEST OVERWRITE MANAGEMENT CO"


def _minimal_kiid_record(isin: str, kiid_class: int = 1, **overrides) -> dict:
    """A realistic CACHED-cycle KIID record: no re-download happened this cycle, so every
    extraction-derived field is None, matching what the parser genuinely produces when it skips
    re-parsing a cached PDF. Only KIID_Status/Processing_* are always populated (the pipeline logs
    every cycle even when it skips extraction)."""
    base = {
        "ISIN": isin, "KIID_Class": kiid_class, "KIID_URL": None, "KIID_PDF_Hash": None,
        "KIID_Status": "CACHED", "Language": None, "Raw_KIID_Text": None,
        "KIID_Published_Date": None, "KIID_Downloaded_At": None,
        "SRRI": None, "SRRI_Visual": None, "SRRI_Textual": None, "SRRI_Validation_Status": None,
        "Processing_Time_Ms": 5, "Processing_Breakdown": "test:5ms",
        "DLA2_Table_Text": None,
        "Cost_Mgmt_BandsX": None, "Cost_Mgmt_Ruled": None, "Cost_Mgmt_Arbitration": None,
        "Cost_Oper_BandsX": None, "Cost_Oper_Ruled": None, "Cost_Oper_Arbitration": None,
        "Cost_ACI_RHP_BandsX": None, "Cost_ACI_RHP_Ruled": None, "Cost_ACI_RHP_Arbitration": None,
        "Cost_ACI_1Y_BandsX": None, "Cost_ACI_1Y_Ruled": None, "Cost_ACI_1Y_Arbitration": None,
    }
    base.update(overrides)
    return base


def test_kiid_upsert_preserves_raw_text_on_cached_cycle(pg_conn):
    """The core invariant this function exists for: a CACHED cycle (Raw_KIID_Text=None, no
    re-download) must NOT null out the existing KIID text. INSERT OR REPLACE would (DELETE+INSERT);
    this is exactly why upsert_kiid_metadata uses ON CONFLICT DO UPDATE with COALESCE instead."""
    pg_conn.execute("SET search_path = gold, silver, bronze, control, public")

    row = pg_conn.execute(
        "SELECT isin, kiid_class FROM fund_kiid_metadata "
        "WHERE raw_kiid_text IS NOT NULL AND length(raw_kiid_text) > 100 LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("no fund_kiid_metadata row with real raw_kiid_text to test against")
    isin, kiid_class = row[0], row[1]

    before = pg_conn.execute(
        "SELECT raw_kiid_text, kiid_status, srri FROM fund_kiid_metadata "
        "WHERE isin = %s AND kiid_class = %s",
        (isin, kiid_class),
    ).fetchone()
    before_text, before_status, before_srri = before
    assert before_status == "OK", "fixture assumption: pick a fund whose status isn't already CACHED"

    record = _minimal_kiid_record(isin, kiid_class)
    upsert_kiid_metadata(pg_conn, record)

    after = pg_conn.execute(
        "SELECT raw_kiid_text, kiid_status, srri FROM fund_kiid_metadata "
        "WHERE isin = %s AND kiid_class = %s",
        (isin, kiid_class),
    ).fetchone()
    after_text, after_status, after_srri = after

    assert after_text == before_text, (
        "Raw_KIID_Text was overwritten/nulled on a CACHED cycle — this is the exact data-loss "
        "bug this function's COALESCE design exists to prevent"
    )
    assert after_status == "OK", (
        f"KIID_Status was downgraded from 'OK' to {after_status!r} by a CACHED cycle — the CASE "
        "rule should preserve a real status instead of letting cache-refresh degrade it"
    )
    assert after_srri == before_srri, "SRRI was overwritten to NULL on a CACHED cycle"


def test_kiid_upsert_insert_path_creates_new_row(pg_conn):
    """A brand-new (ISIN, KIID_Class) pair must insert cleanly — confirms the ON CONFLICT(ISIN,
    KIID_Class) two-column conflict target (not just ISIN alone, unlike fund_master) works."""
    pg_conn.execute("SET search_path = gold, silver, bronze, control, public")

    fake_isin = "TEST00000002"
    record = _minimal_kiid_record(
        fake_isin, kiid_class=1, KIID_Status="OK", Raw_KIID_Text="test kiid text content",
    )
    upsert_kiid_metadata(pg_conn, record)

    row = pg_conn.execute(
        "SELECT isin, kiid_class, kiid_status, raw_kiid_text FROM fund_kiid_metadata "
        "WHERE isin = %s AND kiid_class = 1",
        (fake_isin,),
    ).fetchone()
    assert row is not None, "new (ISIN, KIID_Class) row was not inserted"
    assert row[0] == fake_isin
    assert row[1] == 1
    assert row[2] == "OK"
    assert row[3] == "test kiid text content"


def test_insert_nav_series_ignores_existing_dates(pg_conn):
    """INSERT OR IGNORE's Postgres translation is ON CONFLICT DO NOTHING (not DO UPDATE) —
    a duplicate (isin, date) must be silently skipped, leaving the original NAV value untouched,
    while a genuinely new date is inserted normally."""
    pg_conn.execute("SET search_path = gold, silver, bronze, control, public")

    row = pg_conn.execute("SELECT isin, date, nav FROM fund_nav_monthly LIMIT 1").fetchone()
    if row is None:
        pytest.skip("no fund_nav_monthly row to test against")
    isin, existing_date, existing_nav = row[0], row[1], row[2]

    n_before = pg_conn.execute(
        "SELECT COUNT(*) FROM fund_nav_monthly WHERE isin = %s", (isin,)
    ).fetchone()[0]

    insert_nav_series(pg_conn, isin, [
        # Duplicate of an existing (isin, date) with a deliberately wrong NAV — must be ignored.
        {"date": str(existing_date), "nav": 999999.0, "currency": "USD",
         "nav_type": "official", "is_estimated": 0, "source": "TEST"},
        # Genuinely new date for this ISIN — must be inserted.
        {"date": "1990-01-01", "nav": 42.0, "currency": "USD",
         "nav_type": "official", "is_estimated": 0, "source": "TEST"},
    ])

    n_after = pg_conn.execute(
        "SELECT COUNT(*) FROM fund_nav_monthly WHERE isin = %s", (isin,)
    ).fetchone()[0]
    unchanged_nav = pg_conn.execute(
        "SELECT nav FROM fund_nav_monthly WHERE isin = %s AND date = %s", (isin, existing_date)
    ).fetchone()[0]
    new_row = pg_conn.execute(
        "SELECT nav FROM fund_nav_monthly WHERE isin = %s AND date = '1990-01-01'", (isin,)
    ).fetchone()

    assert n_after == n_before + 1, f"expected exactly +1 row (new date only), got {n_after - n_before}"
    assert float(unchanged_nav) == float(existing_nav), (
        "existing NAV was overwritten — ON CONFLICT DO NOTHING should have ignored the duplicate"
    )
    assert new_row is not None and float(new_row[0]) == 42.0


def test_upsert_kiid_benchmark_insert_then_update(pg_conn):
    """_upsert_kiid_benchmark is a full-row replace (every column freshly computed each call, no
    COALESCE needed) — confirms both the INSERT path and the UPDATE (re-run with a different
    declared benchmark) path work, and that re-running never creates a second row for the same
    (isin, source)."""
    pg_conn.execute("SET search_path = gold, silver, bronze, control, public")

    isin = "LU1873132101"
    pg_conn.execute("DELETE FROM fund_benchmarks WHERE isin = %s AND source = 'KIID'", (isin,))

    _upsert_kiid_benchmark(pg_conn, "ICE BofA US Treasury Bill Index", isin)
    first = pg_conn.execute(
        "SELECT benchmark_name FROM fund_benchmarks WHERE isin = %s AND source = 'KIID'", (isin,)
    ).fetchone()
    assert first is not None, "_upsert_kiid_benchmark did not insert a row — check core.benchmark_normalizer is importable"

    _upsert_kiid_benchmark(pg_conn, "MSCI World Index", isin)
    second = pg_conn.execute(
        "SELECT benchmark_name FROM fund_benchmarks WHERE isin = %s AND source = 'KIID'", (isin,)
    ).fetchone()
    n_rows = pg_conn.execute(
        "SELECT COUNT(*) FROM fund_benchmarks WHERE isin = %s AND source = 'KIID'", (isin,)
    ).fetchone()[0]

    assert second[0] != first[0], "second call should have overwritten benchmark_name"
    assert n_rows == 1, "re-running must UPDATE the existing (isin, source) row, not insert a duplicate"


def test_upsert_cost_schedule_replaces_rows_for_isin(pg_conn):
    """DELETE-then-INSERT full replace, no upsert/conflict logic — confirms both the datetime('now')
    -> now() translation and the placeholder translation work, and that a second call fully
    replaces (not appends to) the first call's rows for the same ISIN."""
    pg_conn.execute("SET search_path = gold, silver, bronze, control, public")
    isin = "LU1873132101"
    pg_conn.execute("DELETE FROM fund_cost_schedule WHERE isin = %s", (isin,))

    n1 = upsert_cost_schedule(pg_conn, isin, [
        {"Horizon_Years": 1.0, "Is_RHP": 0, "Source": "MANUAL", "Total_Costs_Pct": 0.01},
        {"Horizon_Years": 3.0, "Is_RHP": 1, "Source": "MANUAL", "Total_Costs_Pct": 0.03},
    ])
    rows1 = pg_conn.execute(
        "SELECT horizon_years FROM fund_cost_schedule WHERE isin = %s ORDER BY horizon_years",
        (isin,),
    ).fetchall()
    assert n1 == 2
    assert [r[0] for r in rows1] == [1.0, 3.0]

    n2 = upsert_cost_schedule(pg_conn, isin, [
        {"Horizon_Years": 5.0, "Is_RHP": 0, "Source": "MANUAL", "Total_Costs_Pct": 0.05},
    ])
    rows2 = pg_conn.execute(
        "SELECT horizon_years FROM fund_cost_schedule WHERE isin = %s ORDER BY horizon_years",
        (isin,),
    ).fetchall()
    assert n2 == 1
    assert [r[0] for r in rows2] == [5.0], "second call should fully REPLACE, not append to, the first"


def test_publish_fund_multiple_sequential_calls_keep_connection_open(pg_conn):
    """The critical finding this test guards against (found live 2026-09-20): bare `with conn:` on
    a psycopg3 connection COMMITS THEN CLOSES the connection, unlike sqlite3 where it only manages
    the transaction. publish_fund is called once per fund, thousands of times, on ONE long-lived
    connection — a naive port would close the connection after the FIRST call and break every
    subsequent one in the same pipeline run. This test's entire point is calling publish_fund TWICE
    on the same connection and confirming both succeed and the connection never closes."""
    pg_conn.execute("SET search_path = gold, silver, bronze, control, public")

    isins = ["LU1873132101", "LU0252218937"]
    for isin in isins:
        fund_nature = pg_conn.execute(
            "SELECT fund_nature FROM fund_master WHERE isin = %s", (isin,)
        ).fetchone()[0]
        record = _minimal_partial_record(isin, fund_nature, "PUBLISH TEST")
        cost_rows = [{"Horizon_Years": 3.0, "Is_RHP": 1, "Source": "MANUAL", "Total_Costs_Pct": 0.05}]

        publish_fund(pg_conn, record, cost_schedule_rows=cost_rows)

        assert not pg_conn.closed, f"connection was closed after publish_fund({isin}) — the with-conn: bug regressed"

        mgmt = pg_conn.execute(
            "SELECT management_company FROM fund_master WHERE isin = %s", (isin,)
        ).fetchone()[0]
        assert mgmt == "TEST OVERWRITE MANAGEMENT CO"

        cost = pg_conn.execute(
            "SELECT source FROM fund_cost_schedule WHERE isin = %s", (isin,)
        ).fetchone()
        assert cost is not None and cost[0] == "MANUAL"

        log = pg_conn.execute(
            "SELECT status FROM ingestion_log WHERE isin = %s AND step = 'PUBLISH_FUND' "
            "ORDER BY id DESC LIMIT 1",
            (isin,),
        ).fetchone()
        assert log is not None and log[0] == "OK"


def test_publish_fund_error_path_rolls_back_and_logs(pg_conn):
    """An exception during publish_fund's transaction block must: (1) roll back that fund's
    changes, (2) log an ERROR row via log_ingestion, (3) re-raise to the caller, and (4) leave the
    connection open (same underlying conn.transaction() concern as the success-path test above,
    but exercising the rollback branch specifically)."""
    pg_conn.execute("SET search_path = gold, silver, bronze, control, public")

    isin = "LU1873132101"
    before_mgmt = pg_conn.execute(
        "SELECT management_company FROM fund_master WHERE isin = %s", (isin,)
    ).fetchone()[0]

    # Fund_Nature=None trips upsert_fund_master's NOT NULL column before any SQL executes
    # (int(None) inside the Heuristic_Core coercion) — any exception serves this test's purpose.
    bad_record = _minimal_partial_record(isin, None, "SHOULD NOT PERSIST")

    with pytest.raises(Exception):
        publish_fund(pg_conn, bad_record)

    assert not pg_conn.closed, "connection was closed after a failed publish_fund call"

    after_mgmt = pg_conn.execute(
        "SELECT management_company FROM fund_master WHERE isin = %s", (isin,)
    ).fetchone()[0]
    assert after_mgmt == before_mgmt, "fund_master was mutated despite the transaction failing"

    err_log = pg_conn.execute(
        "SELECT status FROM ingestion_log WHERE isin = %s AND step = 'PUBLISH_FUND' "
        "ORDER BY id DESC LIMIT 1",
        (isin,),
    ).fetchone()
    assert err_log is not None and err_log[0] == "ERROR"


def test_correct_oc_aci_mismatch_converts_pct_to_ratio_no_coalesce(pg_conn):
    """Deliberately non-COALESCE overwrite (unlike every other function in this file) — the whole
    point of this function is to force-correct a bad value regardless of what's currently stored.
    Also confirms the integer-percent -> decimal-ratio conversion happens via cost_scale.pct_to_ratio
    (not hand-rolled here), and that a nonexistent ISIN returns False without erroring."""
    pg_conn.execute("SET search_path = gold, silver, bronze, control, public")

    isin = "LU1873132101"
    updated = correct_oc_aci_mismatch(pg_conn, isin, 0.70, "test-source")
    assert updated is True

    new_value = pg_conn.execute(
        "SELECT ongoing_charge_recurrent FROM fund_master WHERE isin = %s", (isin,)
    ).fetchone()[0]
    assert abs(float(new_value) - 0.007) < 1e-9, f"expected ~0.007 (0.70% as ratio), got {new_value}"

    missing = correct_oc_aci_mismatch(pg_conn, "NOSUCHISIN01", 0.70, "test-source")
    assert missing is False


def test_reconcile_universe_membership_flags_correctly(pg_conn):
    """A subset of ISINs must be flagged In_Current_Universe=1, everything else 0 — and the two
    counts must always sum to the full table size (nothing left un-flagged either way)."""
    pg_conn.execute("SET search_path = gold, silver, bronze, control, public")

    all_isins = [r[0] for r in pg_conn.execute("SELECT isin FROM fund_master").fetchall()]
    total = len(all_isins)
    subset = all_isins[: max(1, total // 3)]

    in_u, orphans = reconcile_universe_membership(pg_conn, subset)

    assert in_u == len(subset)
    assert orphans == total - len(subset)
    assert in_u + orphans == total

    flagged = {
        r[0] for r in pg_conn.execute(
            "SELECT isin FROM fund_master WHERE in_current_universe = 1"
        ).fetchall()
    }
    assert flagged == set(subset)


def test_global_post_pipeline_normalize_db_es_to_en(pg_session_conn, pg_conn_module_schema):
    """global_post_pipeline_normalize_db() calls conn.commit() internally (twice) — running it
    against the live-seeded fund_master via the standard SAVEPOINT-based pg_conn fixture would
    commit PAST the savepoint into the real database, permanently mutating all ~3,726 rows. This is
    exactly the scenario shared/testing/pg_fixtures.py's own docstring warns about ('if the code
    under test itself calls conn.commit()... breaking isolation'). Uses pg_conn_module_schema (an
    isolated, disposable schema, dropped at module teardown) instead, with a minimal purpose-built
    fund_master table — matching how the SQLite-only tests in this directory build minimal
    in-memory schemas rather than touching a real database, for the same class of reason.

    pg_conn_module_schema yields only the schema NAME; the connection to use is pg_session_conn
    (requested alongside it — the fixture toggles that shared connection's autocommit for the
    isolated-schema's lifetime, per its own docstring)."""
    conn = pg_session_conn
    schema = pg_conn_module_schema
    conn.execute(f"SET search_path = {schema}")
    conn.execute("""
        CREATE TABLE fund_master (
            isin TEXT PRIMARY KEY,
            sector_focus TEXT,
            family TEXT
        )
    """)
    conn.execute("""
        INSERT INTO fund_master (isin, sector_focus, family) VALUES
        ('T1', 'Tecnología e Innovación', 'RV Core'),
        ('T2', 'Servicios Financieros', 'Monetario'),
        ('T3', 'Technology & Innovation', 'Equity Core')
    """)

    metrics = global_post_pipeline_normalize_db(conn)

    rows = dict(
        (r[0], (r[1], r[2]))
        for r in conn.execute("SELECT isin, sector_focus, family FROM fund_master").fetchall()
    )
    assert rows["T1"] == ("Technology & Innovation", "Equity Core")
    assert rows["T2"] == ("Financial Services", "Money Market")
    assert rows["T3"] == ("Technology & Innovation", "Equity Core"), "already-EN row must pass through unchanged"
    assert metrics["sector_focus_es_stale"] == 2
    assert metrics["family_es_stale"] == 2
    assert metrics["sf_financials_resolved"] == 1


def test_log_ingestion_failure_does_not_poison_enclosing_transaction(pg_conn):
    """Regression guard for a real bug found live 2026-09-20: Postgres aborts the WHOLE enclosing
    transaction on any failed statement — a plain caught-and-swallowed exception in log_ingestion
    (matching its 'El log nunca debe interrumpir el pipeline' design) used to leave the transaction
    unusable for whatever ran next, and would even make conn.transaction()'s own implicit COMMIT
    raise on a clean exit — silently rolling back the fund's ENTIRE substantive write over an
    incidental logging failure. log_ingestion now wraps its own statement in a nested SAVEPOINT on
    Postgres specifically to contain this. isin='...TOO_LONG' (>12 chars) forces a genuine
    StringDataRightTruncation through the real code path, not a synthetic error."""
    pg_conn.execute("SET search_path = gold, silver, bronze, control, public")

    with pg_conn.transaction():
        pg_conn.execute("UPDATE fund_master SET fund_name = fund_name WHERE isin = 'LU1873132101'")
        log_ingestion(pg_conn, "THIS_ISIN_IS_WAY_TOO_LONG_FOR_THE_COLUMN", "TEST_STEP", "OK", None)
        # This statement must succeed — if log_ingestion's failure poisoned the transaction, this
        # would raise InFailedSqlTransaction instead.
        row = pg_conn.execute(
            "UPDATE fund_master SET fund_name = fund_name WHERE isin = 'LU0252218937' RETURNING isin"
        ).fetchone()
        assert row is not None
    # conn.transaction()'s own implicit commit on exit must not raise either.
    assert not pg_conn.closed


def test_upsert_kiid_benchmark_failure_does_not_poison_enclosing_transaction(pg_conn):
    """Same regression class as the log_ingestion test above, for _upsert_kiid_benchmark's own
    fail-soft try/except. isin='...TOO_LONG' forces a genuine StringDataRightTruncation."""
    pg_conn.execute("SET search_path = gold, silver, bronze, control, public")

    with pg_conn.transaction():
        pg_conn.execute("UPDATE fund_master SET fund_name = fund_name WHERE isin = 'LU1873132101'")
        _upsert_kiid_benchmark(pg_conn, "ICE BofA US Treasury Bill Index", "THIS_ISIN_IS_TOO_LONG")
        row = pg_conn.execute(
            "UPDATE fund_master SET fund_name = fund_name WHERE isin = 'LU0252218937' RETURNING isin"
        ).fetchone()
        assert row is not None
    assert not pg_conn.closed
