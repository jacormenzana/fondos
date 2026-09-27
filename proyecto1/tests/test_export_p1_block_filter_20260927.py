# proyecto1/tests/test_export_p1_block_filter_20260927.py
# -*- coding: utf-8 -*-
"""
FND-0065 (2026-09-27): export_p1's --block filter had two real defects, both reproduced live
against the Postgres schema before fixing:

  1. fund_benchmarks and fund_families do NOT have a heuristic_block column (only fund_master
     does) -- get_tables() applied `heuristic_block = '<value>'` to them directly, which crashed
     ("column heuristic_block does not exist") the moment --block was used at all.
  2. export_p1()'s block validation used block.upper() but kept interpolating the ORIGINAL
     (as-typed) value into the WHERE clauses -- the module's own documented usage
     ("--block renta_variable", lowercase) never matched the actual uppercase stored values
     (RENTA_VARIABLE, ...), so a correctly-validated invocation silently exported 0 rows on every
     sheet.

get_tables() is pure (string-building only, no DB) -- these tests check the generated WHERE
clauses directly rather than requiring a live/hermetic DB.

R-7 compliant: no pipeline.py / core.io imports.
"""
import pytest

from proyecto1.src.analysis.export_p1 import VALID_BLOCKS, get_tables


def _by_table(tables, name):
    return next(t for t in tables if t.table == name)


def test_no_filter_leaves_every_where_clause_none():
    tables = get_tables(block_filter=None)
    assert all(t.where is None for t in tables)


def test_fund_master_and_kiid_and_benchmarks_filter_by_isin_via_fund_master():
    """fund_benchmarks is keyed by ISIN, same shape as fund_kiid_metadata's (already-correct)
    filter -- the regression: it used to get fund_master's OWN heuristic_block column literal
    instead of this ISIN-subquery shape."""
    tables = get_tables(block_filter="RENTA_VARIABLE")
    fm = _by_table(tables, "fund_master")
    kiid = _by_table(tables, "fund_kiid_metadata")
    fb = _by_table(tables, "fund_benchmarks")
    cost = _by_table(tables, "fund_cost_schedule")

    assert fm.where == "heuristic_block = 'RENTA_VARIABLE'"
    expected_isin_subquery = (
        "ISIN IN (SELECT ISIN FROM fund_master WHERE heuristic_block = 'RENTA_VARIABLE')")
    assert kiid.where == expected_isin_subquery
    assert fb.where == expected_isin_subquery       # the exact regression
    assert cost.where == expected_isin_subquery


def test_fund_families_filters_by_family_id_not_by_a_nonexistent_isin_or_heuristic_block_column():
    """fund_families has neither ISIN nor heuristic_block -- it's an aggregate keyed by
    family_id. The regression applied fund_master's heuristic_block literal here too, which
    would have crashed identically to fund_benchmarks."""
    tables = get_tables(block_filter="MONETARIOS")
    ff = _by_table(tables, "fund_families")
    assert ff.where == (
        "family_id IN (SELECT fund_family_id FROM fund_master "
        "WHERE heuristic_block = 'MONETARIOS' AND fund_family_id IS NOT NULL)"
    )
    assert "heuristic_block = 'MONETARIOS'" not in ff.where.split("WHERE", 1)[0]


@pytest.mark.parametrize("block", sorted(VALID_BLOCKS))
def test_every_valid_block_produces_a_where_clause_referencing_only_real_columns(block):
    """Regression guard for the class of bug, not just the one instance: no table's WHERE clause
    should ever be a bare heuristic_block literal except fund_master's own."""
    tables = get_tables(block_filter=block)
    for t in tables:
        if t.table == "fund_master":
            assert t.where == f"heuristic_block = '{block}'"
        else:
            assert t.where is None or "SELECT" in t.where, (
                f"{t.table}: {t.where!r} looks like a bare column literal, not a subquery")
