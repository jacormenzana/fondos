# scripts/audit/fixture_schema_lint.py
# -*- coding: utf-8 -*-
"""Schema lint of the test fixtures (FND-0249): columns a fixture's CREATE TABLE declares that the production DDL no longer has.

Hermetic tests build toy tables that mirror a subset of db/pg/*.sql. A toy table MISSING a column the code now reads fails loudly; the opposite
direction is silent: a fixture column that a migration renamed or dropped keeps passing and gives false confidence that the test exercises the
real schema. This check is static (pure Python, no database): it parses the CREATE TABLE statements declared in the Python test modules,
resolves each table by its unqualified name against the CREATE TABLE statements of db/pg/*.sql (plus the ALTER TABLE ... ADD COLUMN of those
files) and reports the fixture columns absent from every table of that name.

A table that is not in the DDL at all is a deliberate toy (reported separately, never a finding). A fixture table whose body is built with an
f-string placeholder cannot be parsed statically and is skipped (reported). Deliberate extras are an explicit allowlist in the test that owns the
gate, each with a reason (tests/test_fixture_schema_lint.py).

Usage:  C:\\Users\\jacor\\.conda\\envs\\des\\python.exe scripts\\audit\\fixture_schema_lint.py [--root DIR]    (exit 1 when there are findings)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DDL_GLOB = "db/pg/*.sql"
FIXTURE_DIRS = ("tests", "proyecto1/tests", "proyecto2/tests", "proyecto3/tests")
# The lint's own tests hold synthetic CREATE TABLE text on purpose.
SELF_TESTS = frozenset({"tests/test_fixture_schema_lint.py"})

_CREATE = re.compile(r"CREATE\s+(?:(?:GLOBAL\s+|LOCAL\s+)?(?:TEMP|TEMPORARY)\s+|UNLOGGED\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w\".]+)\s*\(",
                     re.IGNORECASE)
_ALTER_ADD = re.compile(r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?([\w\".]+)\s+((?:ADD\s+COLUMN\s+[^;]*?)(?=;))", re.IGNORECASE | re.DOTALL)
_ADD_COL = re.compile(r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?(\w+)\"?", re.IGNORECASE)
_CONSTRAINT_WORDS = {"primary", "foreign", "unique", "check", "constraint", "exclude", "like"}


def _strip_sql_comments(text: str) -> str:
    text = re.sub(r"--[^\n]*", "", text)
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def _balanced_body(text: str, open_idx: int) -> str | None:
    """Text between the parenthesis at open_idx and its match (quotes respected), or None when unbalanced."""
    depth, i, quote = 0, open_idx, None
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i]
        i += 1
    return None


def _split_top_level(body: str) -> list:
    parts, depth, cur, quote = [], 0, [], None
    for ch in body:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


def _table_name(raw: str) -> str:
    return raw.replace('"', "").split(".")[-1].lower()


def columns_of_body(body: str) -> list:
    """Column names declared by the body of a CREATE TABLE (table constraints skipped)."""
    out = []
    for part in _split_top_level(body):
        m = re.match(r"\s*\"?(\w+)\"?", part)
        if m and m.group(1).lower() not in _CONSTRAINT_WORDS:
            out.append(m.group(1).lower())
    return out


def parse_create_tables(text: str, strip_comments: bool = True) -> tuple:
    """([(table, [columns])], [tables skipped because the body holds an f-string placeholder])."""
    if strip_comments:
        text = _strip_sql_comments(text)
    found, skipped = [], []
    for m in _CREATE.finditer(text):
        body = _balanced_body(text, m.end() - 1)
        if body is None:
            continue
        name = _table_name(m.group(1))
        if "{" in body or "}" in body:
            skipped.append(name)
            continue
        found.append((name, columns_of_body(body)))
    return found, skipped


def ddl_columns(root: Path) -> dict:
    """{unqualified table name: set(columns)} over db/pg/*.sql: CREATE TABLE bodies plus ALTER TABLE ... ADD COLUMN (same-named tables of
    different schemas are merged: a fixture column is an orphan only when NO table of that name has it)."""
    out: dict = {}
    for path in sorted(root.glob(DDL_GLOB)):
        text = _strip_sql_comments(path.read_text(encoding="utf-8"))
        for name, cols in parse_create_tables(text, strip_comments=False)[0]:
            out.setdefault(name, set()).update(cols)
        for m in _ALTER_ADD.finditer(text):
            for col in _ADD_COL.findall(m.group(2)):
                out.setdefault(_table_name(m.group(1)), set()).add(col.lower())
    return out


def fixture_tables(root: Path) -> tuple:
    """([(file, table, [columns])], [(file, table) skipped]) over every .py under the fixture directories."""
    found, skipped = [], []
    for d in FIXTURE_DIRS:
        for path in sorted((root / d).rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if not _CREATE.search(text):
                continue
            rel = path.relative_to(root).as_posix()
            if rel in SELF_TESTS:
                continue
            tables, skip = parse_create_tables(text)
            found += [(rel, t, c) for t, c in tables]
            skipped += [(rel, t) for t in skip]
    return found, skipped


def lint(root: Path = ROOT) -> dict:
    """{'findings': [(file, table, column)], 'unknown_tables': sorted set, 'skipped': [(file, table)]}."""
    ddl = ddl_columns(root)
    tables, skipped = fixture_tables(root)
    findings, unknown = set(), set()
    for rel, table, cols in tables:
        if table not in ddl:
            unknown.add(table)
            continue
        findings.update((rel, table, c) for c in cols if c not in ddl[table])
    return {"findings": sorted(findings), "unknown_tables": sorted(unknown), "skipped": sorted(set(skipped))}


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=ROOT)
    args = ap.parse_args(argv)
    res = lint(args.root)
    for rel, table, col in res["findings"]:
        print(f"ORPHAN  {rel}: {table}.{col} is not in db/pg DDL")
    print(f"toy tables not in the DDL (not checked): {len(res['unknown_tables'])}; unparsable (f-string body): {len(res['skipped'])}; "
          f"orphan columns: {len(res['findings'])}")
    return 1 if res["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
