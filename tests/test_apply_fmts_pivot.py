# tests/test_apply_fmts_pivot.py
# -*- coding: utf-8 -*-
"""
Pure-Python coverage for scripts/ops/apply_fmts_pivot.py's DDL extraction + name substitution
(v27 pivot, 2026-09-27, harmonic-marinating-balloon.md). No database needed -- these functions only
read db/pg/30_gold.sql / db/pg/40_matviews.sql and do string substitution to build the `_new`
staging DDL the live migration executes. Guards against the two failure modes that would otherwise
only surface mid-migration against the live database:
  (a) an unrelated edit to the sentinel-marked DDL blocks silently breaking extraction;
  (b) a substitution collision leaving some object still named after the live one, which would
      make the live migration fail outright (name already exists) rather than silently misbehave.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "ops"))

import apply_fmts_pivot as p  # noqa: E402


@pytest.fixture(scope="module")
def staging_ddl():
    return p.build_staging_ddl()


def test_build_staging_ddl_extracts_non_empty_blocks(staging_ddl):
    table_ddl, views_ddl = staging_ddl
    assert "CREATE TABLE IF NOT EXISTS gold.fund_metric_timeseries_new (" in table_ddl
    assert "CREATE OR REPLACE VIEW gold.v_fund_metric_timeseries_long_new AS" in views_ddl


def test_sentinel_extraction_raises_on_missing_marker():
    with pytest.raises(ValueError):
        p._extract_sentinel_block("-- no sentinels here\nSELECT 1;", "base table DDL")


def test_table_ddl_never_names_the_live_table_or_partitions(staging_ddl):
    """Every occurrence of the canonical table name must carry the `_new` suffix -- if even one
    slipped through unprefixed, --apply would try to CREATE TABLE a name that already exists live
    and fail loudly (safe), but it would mean the substitution rules drifted from the DDL file."""
    table_ddl, _ = staging_ddl
    # No unqualified reference to the canonical table survives (every one must be followed by _new).
    for m in re.finditer(r"gold\.fund_metric_timeseries(_new)?\b", table_ddl):
        assert m.group(1) == "_new", f"un-suffixed reference at {m.start()}: ...{table_ddl[m.start()-20:m.end()+20]}..."

    for partition in p._PARTITIONS:
        new_partition = partition.replace("fmts_p_", "fmts_new_p_")
        assert f"gold.{new_partition}" in table_ddl
        # The canonical partition name must not appear except as a substring of its own _new form.
        for m in re.finditer(re.escape(partition) + r"\b", table_ddl):
            assert table_ddl[m.start():m.start() + len(new_partition)] == new_partition, (
                f"un-suffixed partition reference at {m.start()}")


def test_table_ddl_renames_pkey_indexes_and_statistics(staging_ddl):
    table_ddl, _ = staging_ddl
    assert f"CONSTRAINT {p._PKEY_NAME}_new PRIMARY KEY" in table_ddl
    assert f"CONSTRAINT {p._PKEY_NAME} PRIMARY KEY" not in table_ddl
    for idx in p._INDEX_RENAMES:
        assert f"{idx}_new" in table_ddl
        assert re.search(rf"\b{re.escape(idx)}\b(?!_new)", table_ddl) is None
    assert f"{p._STATS_RENAME}_new" in table_ddl


def test_table_ddl_reloption_loop_targets_new_partitions_only(staging_ddl):
    """The DO $$ ... FOREACH block builds partition names as plain string array literals (not
    identifiers the earlier regex checks would catch) -- verified separately since a miss here
    would silently reset autovacuum/fillfactor settings on the LIVE partitions instead of the
    staging ones."""
    table_ddl, _ = staging_ddl
    array_literal = re.search(r"ARRAY\[(.*?)\]", table_ddl, re.S).group(1)
    for partition in p._PARTITIONS:
        assert partition not in array_literal
        assert partition.replace("fmts_p_", "fmts_new_p_") in array_literal


def test_views_ddl_renames_view_and_every_matview(staging_ddl):
    _, views_ddl = staging_ddl
    assert "FROM   gold.fund_metric_timeseries_new t" in views_ddl
    for mv in p._MATVIEWS:
        name = mv.split(".", 1)[1]
        assert f"CREATE MATERIALIZED VIEW IF NOT EXISTS gold.{name}_new AS" in views_ddl
        assert f"CREATE MATERIALIZED VIEW IF NOT EXISTS {mv} AS" not in views_ddl
    for idx in p._MATVIEW_INDEXES:
        assert f"{idx}_new" in views_ddl
        assert re.search(rf"\b{re.escape(idx)}\b(?!_new)", views_ddl) is None


def test_views_ddl_has_no_grants(staging_ddl):
    """Grants apply to the live names only, added automatically (ALTER DEFAULT PRIVILEGES FOR ROLE
    fondos_owner, db/pg/00_roles_schemas.sql + db/pg_ops/readonly_roles.sql) once this DDL runs as
    the owner -- see the module docstring. A GRANT inside the sentinel block would target the
    literal `_new` names, which is never useful, so its absence is asserted rather than assumed."""
    _, views_ddl = staging_ddl
    assert not re.search(r"^\s*GRANT\b", views_ddl, re.M)
