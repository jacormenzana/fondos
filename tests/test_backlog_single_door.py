# tests/test_backlog_single_door.py
# -*- coding: utf-8 -*-
"""The single door of the backlog (FND-0331, owner decision D3): tickets are opened and moved ONLY through backlog.sp_open_ticket / sp_transition
(shared/backlog_client.py is the one place that calls them). No Python file may carry a raw `INSERT INTO backlog.backlog` or `UPDATE backlog.backlog`
statement, except the files listed below, each with its reason (tests/_allowlist.py protocol). Logs (backlog_logs) and link events
(backlog_link_event) are append-only tables with their own exceptions (E1, E2 of db/pg_backlog/31_revoke_raw_update.sql) and are not covered here.

Pure: scans the repository's Python files with the AST (string literals only), no database. A stale allowlist key fails the suite.
"""
from __future__ import annotations

import ast
import re
import sys
import warnings
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _allowlist import bad_reasons  # noqa: E402

# a write to the TICKET table (not backlog_logs / backlog_link_event / backlog_...): INSERT INTO backlog.backlog / UPDATE backlog.backlog, any case
_RAW = re.compile(r"\b(?:INSERT\s+INTO|UPDATE)\s+backlog\.backlog\b(?!_)", re.IGNORECASE)

ALLOWED: dict = {
    "shared/backlog_client.py":
        "FND-0338 update_attributes() is the one UPDATE of the descriptive columns of exception E3 (a whitelist of eight columns), and stays; the legacy INSERT of "
        "report_incident was REMOVED on the owner's order of 2026-10-28",
    "scripts/audit/backlog_phase_b_replay.py":
        "FND-0333 the replay loads a snapshot of the backlog into a THROWAWAY scratch database (the hermetic test container) and moves tickets there to the state they had "
        "at each historical closure; it never connects to the live backlog for anything but a read-only export",
}
# tests build their fixtures with raw statements on throwaway databases, as the owner role: that is what a hermetic test is
TEST_DIR_REASON = "DESIGN: hermetic tests create fixture tickets with raw statements on a throwaway database inside a rolled-back savepoint"
_SKIP_DIRS = {".git", "out", "__pycache__", ".venv", "node_modules"}


def _python_files():
    for p in _ROOT.rglob("*.py"):
        rel = p.relative_to(_ROOT)
        if not (set(rel.parts) & _SKIP_DIRS):
            yield p, rel.as_posix()


def _raw_statements(path: Path) -> list:
    """[(line, snippet)] of string literals in `path` that contain a raw write to the ticket table."""
    try:
        with warnings.catch_warnings():              # other files of the repo may carry harmless invalid-escape warnings
            warnings.simplefilter("ignore")
            tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            m = _RAW.search(node.value)
            if m:
                hits.append((node.lineno, node.value[m.start():m.start() + 60].replace("\n", " ")))
    return hits


def test_the_allowlist_reasons_follow_the_protocol():
    assert bad_reasons(ALLOWED) == []


def test_no_raw_write_to_the_ticket_table_outside_the_allowlist():
    offenders = {}
    for path, rel in _python_files():
        if rel.startswith("tests/") or rel in ALLOWED:
            continue
        hits = _raw_statements(path)
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        "raw INSERT/UPDATE on backlog.backlog found; open and move tickets with shared.backlog_client.open_ticket()/transition() "
        f"(or add the file to ALLOWED with a reason): {offenders}")


def test_the_allowlist_has_no_stale_key():
    stale = [rel for rel in ALLOWED if not (_ROOT / rel).exists() or not _raw_statements(_ROOT / rel)]
    assert not stale, f"allowlisted files that no longer carry a raw write (remove them from ALLOWED): {stale}"


def test_the_scan_itself_works():
    sample = _ROOT / "tests" / "test_backlog_transitions_pg.py"
    assert _raw_statements(sample), "the scan found no raw statement in a test that has some: the scan is broken"
    assert not _RAW.search("INSERT INTO backlog.backlog_logs (x) VALUES (1)")
    assert not _RAW.search("UPDATE backlog.backlog_link_event SET x = 1")
    assert _RAW.search("update backlog.BACKLOG set status = 'x'") and _RAW.search("INSERT  INTO  backlog.backlog (a)")
    assert TEST_DIR_REASON.startswith("DESIGN: ")
