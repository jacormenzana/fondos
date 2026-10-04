"""
tests/test_bi_compat.py — the temporary Superset compatibility schema (FND-0069, 2026-09-23).

Superset datasets were built against the legacy BI store, which has SQLite's column names
(fund_master."ISIN", "window", ...). The primary Postgres renamed them. db/pg_bi_compat/bi_compat.sql
re-exposes the three registered datasets under their legacy names; it is generated from
rename_map.yaml, so these tests pin (1) it is never stale and (2) it really covers every legacy
column, including the one that is also a Postgres reserved word.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts" / "mig"))

import gen_bi_compat as g  # noqa: E402


@pytest.fixture(scope="module")
def rename_map():
    return yaml.safe_load(g.RENAME_MAP.read_text(encoding="utf-8"))


def test_committed_sql_is_not_stale(rename_map):
    assert g.OUT_FILE.read_text(encoding="utf-8") == g.render(rename_map), (
        "db/pg_bi_compat/bi_compat.sql is out of date — run scripts/mig/gen_bi_compat.py")


def test_every_legacy_column_is_exposed_under_its_legacy_name(rename_map):
    """Every rename_map column appears in the generated view under its legacy name, EXCEPT
    g.BI_COLUMN_EXCLUDE entries (v27 pivot, FND fund_metric_timeseries: ref_type/ref_value were
    100% NULL pre-pivot and the compat view drops them — rename_map.yaml still lists them because
    it separately pins fidelity to the frozen SQLite source, checked below)."""
    sql = g.OUT_FILE.read_text(encoding="utf-8")
    for table in g.BI_TABLES:
        view = re.search(rf"CREATE OR REPLACE VIEW bi_compat\.{table} AS(.*?);\n", sql, re.S).group(1)
        excluded = g.BI_COLUMN_EXCLUDE.get(table, frozenset())
        for old, new in rename_map["columns"][table].items():
            if old in excluded:
                assert f'"{new}" AS "{old}"' not in view, f"{table}: {old} should be excluded, but is present"
                continue
            assert f'"{new}" AS "{old}"' in view, f"{table}: {old} -> {new} missing"


def test_reserved_word_and_mixed_case_are_quoted(rename_map):
    sql = g.OUT_FILE.read_text(encoding="utf-8")
    assert '"window_label" AS "window"' in sql
    assert '"isin" AS "ISIN"' in sql
    assert not re.search(r"AS window\b", sql)


def test_grants_are_read_only_and_limited_to_superset_ro():
    sql = g.OUT_FILE.read_text(encoding="utf-8")
    grants = re.findall(r"^GRANT .*$", sql, re.M)
    assert grants and all(x.rstrip(";").endswith("TO superset_ro") for x in grants)
    assert all(x.startswith(("GRANT USAGE ON SCHEMA bi_compat", "GRANT SELECT ON bi_compat.")) for x in grants)


def test_legacy_names_match_the_live_sqlite_columns(rename_map):
    """rename_map keys must equal the real SQLite columns, i.e. what the legacy mirror published."""
    db = _ROOT / "db" / "fondos.sqlite"
    # SQLite is retired (FND-0102/0103): the sealed file is archived, and anything that merely opens the old path
    # leaves a 0-byte file behind. Only a real database (non-empty) can be compared with rename_map.
    if not db.exists() or db.stat().st_size == 0:
        pytest.skip("legacy SQLite is retired/archived (missing or empty): nothing to compare rename_map against")
    con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    try:
        for table in g.BI_TABLES:
            live = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
            assert live == list(rename_map["columns"][table]), table
    finally:
        con.close()
