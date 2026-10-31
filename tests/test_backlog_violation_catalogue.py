# tests/test_backlog_violation_catalogue.py
# -*- coding: utf-8 -*-
"""Phase D acceptance, static half (FND-0335): every code of the violation catalogue (PROPUESTA_BACKLOG_V2.md section 7) whose source exists in the
backlog database after phases A, B and D has a row source in backlog.v_backlog_violations, and the view emits no code the catalogue does not define.
Codes owned by Phase C or FND-0332 are listed as pending, so a code cannot silently fall out of both lists. No database needed."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = (ROOT / "doc" / "especificacion" / "PROPUESTA_BACKLOG_V2.md").read_text(encoding="utf-8")
DDL = (ROOT / "db" / "pg_backlog" / "60_groups_violations.sql").read_text(encoding="utf-8")

# Codes whose source is not in this database yet: Phase C (ops_event, gate_state, the GitHub API) and FND-0332 (control.cycle_flag in `fondos`).
PENDING = {"V-RETRO", "V-WORK-ON-CLOSED", "V-HOOK-BYPASS", "V-GATE-UNPROTECTED", "V-GATE-UNVERIFIED", "V-GATE-EMERGENCY", "V-STALE-READ"}


def catalogue() -> dict:
    """{code: phase column} from the table of section 7."""
    section = SPEC.split("## 7. Violation catalogue", 1)[1].split("\n## ", 1)[0]
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"^\| `(V-[A-Z0-9-]+)` \|.*\| ([^|]+) \|\s*$", section, re.M)}


def view_codes() -> set:
    view = DDL.split("CREATE OR REPLACE VIEW backlog.v_backlog_violations AS", 1)[1].split("CREATE OR REPLACE VIEW", 1)[0]   # comments may hold ';'
    return set(re.findall(r"SELECT '(V-[A-Z0-9-]+)'", view))


def test_the_catalogue_is_parsed():
    cat = catalogue()
    assert len(cat) >= 15 and "V-00" in cat and "V-HOLD-OPEN" in cat


def test_every_code_has_a_source_or_is_explicitly_pending():
    cat, emitted = catalogue(), view_codes()
    assert emitted.isdisjoint(PENDING)
    assert set(cat) == emitted | PENDING, f"in the catalogue but nowhere: {set(cat) - emitted - PENDING}; not in the catalogue: {(emitted | PENDING) - set(cat)}"


def test_pending_codes_belong_to_phase_c_or_fnd_0332():
    cat = catalogue()
    for code in PENDING:
        assert cat[code] in ("C", "FND-0332"), (code, cat[code])
