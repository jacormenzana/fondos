# tests/test_backlog_actor_alias.py
# -*- coding: utf-8 -*-
"""Canonical author identity of the backlog (FND-0330; db/pg_backlog/25_actor_alias.sql, shared/backlog_client.py, scripts/ops/migrate_backlog_v2.py).

Pure: no database, no pipeline imports (R-7). The hermetic Postgres counterpart is tests/test_backlog_actor_alias_pg.py.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import migrate_backlog_v2 as mig  # noqa: E402
from shared import backlog_client as bc  # noqa: E402

# The 13 author strings found in backlog_logs on 2026-10-26 (Q13 of doc/especificacion/PROPUESTA_BACKLOG_V2.md, Appendix B).
LIVE_SPELLINGS_20261026 = {
    "claude", "jacor", "claude-code", "CLAUDE_SESSION", "claude-opus-5-5", "claude_p3_assessment", "LEGACY_HTML_MIGRATION",
    "SYSTEM_AUTODETECT", "SYSTEM_RELEASE", "CLAUDE_DEV_SESSION", "claude-sonnet-5", "CLAUDE_CODE", "backlog_writer",
}
_SEED = re.compile(r"\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*(NULL|'[^']*')\s*\)")


def _seed() -> list:
    return [(m.group(1), m.group(2)) for m in _SEED.finditer(mig.ddl_block("alias"))]


def test_seed_covers_every_live_spelling_and_nothing_else():
    raws = [r for r, _ in _seed()]
    assert len(raws) == len(set(raws)), "a raw spelling is listed twice"
    assert set(raws) == LIVE_SPELLINGS_20261026


def test_every_seed_canonical_is_in_canonical_form():
    for raw, canonical in _seed():
        assert bc.ACTOR_PATTERN.match(canonical), f"{raw!r} -> {canonical!r}"


def test_python_pattern_equals_every_sql_pattern():
    """The CHECK of actor_alias and the two views use the same regex as ACTOR_PATTERN: one definition of 'canonical'."""
    block = mig.ddl_block("alias")
    found = set(re.findall(r"~ '(\^\(human\|agent\|system\):[^']+)'", block))
    assert found == {bc.ACTOR_PATTERN.pattern}, found
    assert len(re.findall(r"~ '\^\(human\|agent\|system\)", block)) == 4        # CHECK + 2 in v_backlog_log_actor + 1 in v_actor_unmapped (NOT)


def test_current_actor_reads_only_the_environment_and_never_guesses():
    assert bc.current_actor({"FONDOS_ACTOR": "agent:claude-opus-5-5"}) == "agent:claude-opus-5-5"
    assert bc.current_actor({"FONDOS_ACTOR": "  human:jacor "}) == "human:jacor"
    for bad in ({}, {"FONDOS_ACTOR": ""}, {"FONDOS_ACTOR": "claude"}, {"FONDOS_ACTOR": "agent:"}, {"FONDOS_ACTOR": "robot:x"},
                {"FONDOS_ACTOR": "agent:has space"}, {"USERNAME": "jacor", "USER": "jacor"}):
        with pytest.raises(bc.ActorUnknown):
            bc.current_actor(bad)


def test_canonical_actor_alias_then_itself_then_unmapped():
    aliases = {"claude": "agent:claude", "jacor": "human:jacor"}
    assert bc.canonical_actor("claude", aliases) == "agent:claude"
    assert bc.canonical_actor("agent:claude-opus-5-5", aliases) == "agent:claude-opus-5-5"      # already canonical
    assert bc.canonical_actor("somebody", aliases) == "unmapped:somebody"
    assert bc.canonical_actor("", aliases) == "unmapped:"


def test_phase_objects_match_up_and_down_blocks():
    spec = mig.PHASES["alias"]
    up, down = mig.ddl_block("alias"), mig.ddl_block("alias", down=True)
    assert up.startswith("-- BEGIN backlog_actor_alias") and up.rstrip().endswith("-- END backlog_actor_alias")
    assert down.startswith("-- BEGIN backlog_actor_alias_down") and down.rstrip().endswith("-- END backlog_actor_alias_down")
    for t in spec["tables"]:
        assert re.search(rf"CREATE TABLE IF NOT EXISTS backlog\.{t}\b", up)
        assert re.search(rf"DROP TABLE IF EXISTS backlog\.{t}\b", down)
    for v in spec["views"]:
        assert re.search(rf"CREATE OR REPLACE VIEW backlog\.{v}\b", up)
        assert re.search(rf"DROP VIEW IF EXISTS backlog\.{v}\b", down)


def test_down_block_never_touches_logs_or_link_events():
    down = mig.ddl_block("alias", down=True)
    assert not re.search(r"backlog\.(backlog|backlog_logs|backlog_link_event)\b", down, re.I)       # history tables are not referenced
    assert not re.search(r"\b(DELETE|UPDATE|TRUNCATE)\b", down, re.I)


def test_ddl_is_idempotent_by_construction():
    up = mig.ddl_block("alias")
    assert "INSERT INTO backlog.actor_alias" in up and "ON CONFLICT (raw_name) DO NOTHING" in up
    assert "CREATE TABLE IF NOT EXISTS" in up and "DROP TRIGGER IF EXISTS" in up
    assert not re.search(r"^\s*(DROP TABLE|DELETE|TRUNCATE)\b", up, re.I | re.M)
