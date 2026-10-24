"""FND-0249: the test fixtures' CREATE TABLE columns must exist in the production DDL (db/pg/*.sql).

Pure Python, no database. The unit tests pin the parser on a synthetic tree; the gate test runs the lint over the real repository.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "audit"))

import fixture_schema_lint as lint  # noqa: E402

# (file, table, column) deliberately present in a fixture but not in the DDL, each with the reason. Empty: none today.
ALLOWED_EXTRAS: dict = {}


def _tree(tmp_path: Path, ddl: str, fixture: str) -> Path:
    (tmp_path / "db" / "pg").mkdir(parents=True)
    (tmp_path / "db" / "pg" / "20_silver.sql").write_text(ddl, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(fixture, encoding="utf-8")
    return tmp_path


DDL = """
-- a comment with CREATE TABLE silver.ghost (nope int);
CREATE TABLE IF NOT EXISTS silver.fund_master (
    isin varchar(12) PRIMARY KEY,
    "window_label" text,
    price numeric(10, 2) DEFAULT 0,
    CONSTRAINT fm_chk CHECK (price >= 0),
    UNIQUE (isin, price)
);
ALTER TABLE silver.fund_master ADD COLUMN IF NOT EXISTS added_later text;
"""


def test_orphan_column_is_reported(tmp_path):
    root = _tree(tmp_path, DDL, 'SQL = """CREATE TABLE fund_master (isin TEXT PRIMARY KEY, dropped_col TEXT)"""\n')
    assert lint.lint(root)["findings"] == [("tests/test_x.py", "fund_master", "dropped_col")]


def test_columns_in_ddl_are_not_findings(tmp_path):
    fixture = 'SQL = "CREATE TEMP TABLE fund_master (isin TEXT, window_label TEXT, price NUMERIC, added_later TEXT)"\n'
    assert lint.lint(_tree(tmp_path, DDL, fixture))["findings"] == []


def test_constraints_are_not_columns():
    body = "isin text, price numeric(10, 2), PRIMARY KEY (isin), CONSTRAINT c CHECK (price > 0), UNIQUE (isin)"
    assert lint.columns_of_body(body) == ["isin", "price"]


def test_ddl_comment_is_not_a_table(tmp_path):
    _tree(tmp_path, DDL, "")
    assert "ghost" not in lint.ddl_columns(tmp_path)


def test_toy_table_not_in_ddl_is_informational(tmp_path):
    root = _tree(tmp_path, DDL, 'SQL = "CREATE TABLE only_a_toy (a INT, b INT)"\n')
    res = lint.lint(root)
    assert res["findings"] == [] and res["unknown_tables"] == ["only_a_toy"]


def test_fstring_body_is_skipped_not_guessed(tmp_path):
    root = _tree(tmp_path, DDL, 'SQL = f"CREATE TABLE fund_master (isin TEXT, {extra} TEXT)"\n')
    res = lint.lint(root)
    assert res["findings"] == [] and res["skipped"] == [("tests/test_x.py", "fund_master")]


def test_same_table_name_in_two_schemas_is_merged(tmp_path):
    ddl = "CREATE TABLE silver.t (a int);\nCREATE TABLE gold.t (b int);\n"
    root = _tree(tmp_path, ddl, 'SQL = "CREATE TABLE t (a INT, b INT, c INT)"\n')
    assert [f[2] for f in lint.lint(root)["findings"]] == ["c"]


def test_repository_fixtures_match_the_production_ddl():
    """The gate: a fixture column that a migration removed or renamed must be fixed (or allowlisted above with its reason)."""
    res = lint.lint(ROOT)
    new = [f for f in res["findings"] if f not in ALLOWED_EXTRAS]
    assert not new, "fixture columns absent from db/pg DDL (a stale toy table gives false confidence):\n  " + "\n  ".join(
        f"{rel}: {table}.{col}" for rel, table, col in new)
    stale = [f for f in ALLOWED_EXTRAS if f not in res["findings"]]
    assert not stale, f"allowlist entries that no longer apply, delete them: {stale}"
