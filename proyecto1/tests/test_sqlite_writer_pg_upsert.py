# proyecto1/tests/test_sqlite_writer_pg_upsert.py
# -*- coding: utf-8 -*-
"""
Postgres migration Phase 5c (2026-09-20) — dedicated regression test for the COALESCE-preservation
invariant in sqlite_writer.upsert_fund_master(), written per the migration plan's own discipline
("Every one of these three patterns gets a dedicated regression test... asserting the specific
invariant... written before the site is touched, so the rewrite is red->green per site").

Named invariant under test (plan §5c): "P#1 COALESCE upserts map to
ON CONFLICT ... DO UPDATE SET col = COALESCE(excluded.col, tbl.col)" — a partial "CACHED-cycle"
record (many fields None, matching real record.get() shape) must NOT overwrite existing non-null
values in fund_master with NULL.

Runs against the REAL, live-seeded silver.fund_master table (not a throwaway in-memory schema like
the SQLite-only tests in this directory use) — deliberate choice: the pg_conn fixture's SAVEPOINT
guarantees zero permanent mutation regardless of outcome, and testing against the actual production
schema (63 columns, real NOT NULL constraints, real FK to fund_families) catches real constraint
issues that a hand-built minimal test schema would not — exactly what surfaced during manual
verification of this same invariant before this test was written (a first attempt with an
unrealistic all-NULL partial record hit two real NOT NULL constraints that a real classifier call
would never actually violate).

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
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from sqlite_writer import upsert_fund_master  # noqa: E402


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
