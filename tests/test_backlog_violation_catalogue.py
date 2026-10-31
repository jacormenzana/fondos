# tests/test_backlog_violation_catalogue.py
# -*- coding: utf-8 -*-
"""Violation catalogue, static half (FND-0335, FND-0334): every code of PROPUESTA_BACKLOG_V2.md section 7 whose source exists in the backlog database
has a row source in one phase's backlog.v_violations_<phase> view (the union is backlog.v_backlog_violations), and no view emits a code the catalogue
does not define. Codes whose source is not built yet are listed as pending, so a code cannot silently fall out of both lists. No database needed."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = (ROOT / "doc" / "especificacion" / "PROPUESTA_BACKLOG_V2.md").read_text(encoding="utf-8")
VIEWS = {"v_violations_d": ROOT / "db" / "pg_backlog" / "60_groups_violations.sql",      # phases A, B and D
         "v_violations_c": ROOT / "db" / "pg_backlog" / "70_sync.sql",                   # Phase C slice 1
         "v_violations_c2": ROOT / "db" / "pg_backlog" / "80_hook.sql",                  # Phase C slice 2
         "v_violations_c3": ROOT / "db" / "pg_backlog" / "95_attest.sql"}                # Phase C slice 4
# A code whose row source is a view of its own, outside the union (FND-0350): the catalogue names it in its source column.
OWN_VIEW = {"V-GATE-OVERRIDE": (ROOT / "db" / "pg_backlog" / "40_relations_v2.sql", "v_gate_override_event")}

# Codes whose source is not in this database yet: FND-0332 (control.cycle_flag in
# `fondos`, computed by the cycle report).
PENDING = {"V-STALE-READ"}


def catalogue() -> dict:
    """{code: phase column} from the table of section 7."""
    section = SPEC.split("## 7. Violation catalogue", 1)[1].split("\n## ", 1)[0]
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"^\| `(V-[A-Z0-9-]+)` \|.*\| ([^|]+) \|\s*$", section, re.M)}


def view_codes(view: str) -> set:
    ddl = VIEWS[view].read_text(encoding="utf-8")
    body = ddl.split(f"CREATE OR REPLACE VIEW backlog.{view} AS", 1)[1]
    body = re.split(r"\nCREATE OR REPLACE |\n-- Ownership", body, maxsplit=1)[0]          # comments may hold ';'
    return set(re.findall(r"SELECT '(V-[A-Z0-9-]+)'", body))


def emitted() -> set:
    return set().union(*(view_codes(v) for v in VIEWS))


def test_the_catalogue_is_parsed():
    cat = catalogue()
    assert len(cat) >= 15 and "V-00" in cat and "V-HOLD-OPEN" in cat


def test_each_view_emits_its_own_codes():
    assert {"V-00", "V-FAST", "V-HOLD-OPEN"} <= view_codes("v_violations_d")
    assert view_codes("v_violations_c") == {"V-RETRO", "V-WORK-ON-CLOSED"}
    assert view_codes("v_violations_c2") == {"V-HOOK-BYPASS"}
    assert view_codes("v_violations_c3") == {"V-GATE-UNPROTECTED", "V-GATE-UNVERIFIED", "V-GATE-EMERGENCY"}
    assert view_codes("v_violations_d").isdisjoint(view_codes("v_violations_c") | view_codes("v_violations_c2") | view_codes("v_violations_c3"))


def test_every_code_has_a_source_or_is_explicitly_pending():
    cat, out = catalogue(), emitted()
    assert out.isdisjoint(PENDING) and out.isdisjoint(OWN_VIEW)
    for code, (path, view) in OWN_VIEW.items():
        assert f"CREATE OR REPLACE VIEW backlog.{view} AS" in path.read_text(encoding="utf-8"), code
    out |= set(OWN_VIEW)
    assert set(cat) == out | PENDING, f"in the catalogue but nowhere: {set(cat) - out - PENDING}; not in the catalogue: {(out | PENDING) - set(cat)}"


def test_phase_c_codes_come_from_the_phase_c_view_and_pending_ones_belong_to_c_or_fnd_0332():
    cat = catalogue()
    assert all(cat[c] == "C" for c in view_codes("v_violations_c") | view_codes("v_violations_c2") | view_codes("v_violations_c3"))
    for code in PENDING:
        assert cat[code] in ("C", "FND-0332"), (code, cat[code])
