# proyecto1/tests/test_audit_benchmark_consistency_dialect.py
# -*- coding: utf-8 -*-
"""
FND-0069: proyecto1/tools/audit_benchmark_consistency.py must behave the same on Postgres, which
folds result-column names to lowercase (`isin`), as on SQLite (`ISIN`). The tool indexes rows by the
SQLite spelling, so `_fetch_dicts` maps the names back through db/pg/rename_map.yaml.

R-7: no database and no pipeline.py — a fake connection stands in for the cursor.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto1.tools.audit_benchmark_consistency import _fetch_dicts  # noqa: E402


class _Cur:
    def __init__(self, cols, rows):
        self.description = [(c,) for c in cols]
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Con:
    def __init__(self, cols, rows):
        self._cur = _Cur(cols, rows)
        self.sql = None

    def execute(self, sql):
        self.sql = sql
        return self._cur


def test_postgres_lowercase_names_are_restored_to_the_sqlite_spelling():
    con = _Con(["isin", "fund_name", "fund_nature", "srri"], [("LU1", "Fund A", "Mixtos", 4)])
    (row,) = _fetch_dicts(con.execute("SELECT ..."), "fund_master")
    assert row["ISIN"] == "LU1" and row["Fund_Name"] == "Fund A" and row["Fund_Nature"] == "Mixtos"
    assert "isin" not in row


def test_sqlite_spelling_passes_through_unchanged():
    con = _Con(["ISIN", "Fund_Nature"], [("LU1", "Mixtos")])
    (row,) = _fetch_dicts(con.execute("SELECT ..."), "fund_master")
    assert row == {"ISIN": "LU1", "Fund_Nature": "Mixtos"}


def test_columns_that_are_already_the_same_in_both_dialects_are_kept():
    # fund_benchmarks: `source`, `benchmark_name`, ... are lowercase in SQLite too; only `ISIN` differs
    con = _Con(["isin", "source", "benchmark_name"], [("LU1", "KIID", "MSCI World")])
    (row,) = _fetch_dicts(con.execute("SELECT * FROM fund_benchmarks"), "fund_benchmarks")
    assert row == {"ISIN": "LU1", "source": "KIID", "benchmark_name": "MSCI World"}


def test_kiid_metadata_columns_are_restored():
    con = _Con(["isin", "kiid_status", "raw_kiid_text"], [("LU1", "OK", "text")])
    (row,) = _fetch_dicts(con.execute("SELECT ..."), "fund_kiid_metadata")
    assert row["ISIN"] == "LU1" and row["KIID_Status"] == "OK" and row["Raw_KIID_Text"] == "text"
