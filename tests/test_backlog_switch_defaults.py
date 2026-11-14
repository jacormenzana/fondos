# tests/test_backlog_switch_defaults.py
# -*- coding: utf-8 -*-
"""FND-0370 (d): the default of every automatic-writer switch in shared/config.py equals the default documented in NORMAS_BACKLOG.md section 4.9, and a new (REGISTERED) writer can only be documented OFF.
The documented table is read as data, so changing one side without the other fails here instead of drifting. No database."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import shared.config as cfg
from shared.backlog_writers import REGISTERED, REGISTRY

_DOC = Path(__file__).resolve().parents[1] / "doc" / "reglas" / "NORMAS_BACKLOG.md"
_ROW = re.compile(r"^\s*\|\s*`(?P<name>[A-Z0-9_]+)`\s*\|\s*`(?P<value>[^`]+)`\s*\|")
OFF_VALUES = (False, "OFF")


def _documented() -> dict:
    """{switch: documented default} from the table of section 4.9 (only rows whose switch name starts with BACKLOG_)."""
    text = _DOC.read_text(encoding="utf-8")
    start = text.index("### §4.9.")
    end = text.index("\n## §5.", start)
    out = {}
    for line in text[start:end].splitlines():
        m = _ROW.match(line)
        if m and m["name"].startswith("BACKLOG_"):
            assert m["name"] not in out, f"{m['name']} is documented twice"
            out[m["name"]] = ast.literal_eval(m["value"])
    return out


def test_the_table_is_found_and_not_empty():
    assert len(_documented()) >= 4, "the default-values table of NORMAS_BACKLOG section 4.9 is missing or unreadable"


def test_every_documented_switch_exists_and_its_default_equals_the_documented_one():
    for name, documented in _documented().items():
        assert hasattr(cfg, name), f"{name} is documented in NORMAS_BACKLOG 4.9 but is not in shared/config.py"
        assert getattr(cfg, name) == documented, (
            f"{name}: shared/config.py says {getattr(cfg, name)!r}, NORMAS_BACKLOG 4.9 says {documented!r}: change both in the same commit (turning a writer on is an owner event)")


def test_every_switch_of_a_registered_writer_is_documented():
    documented = _documented()
    for spec in REGISTRY.values():
        if spec.switch:
            assert spec.switch in documented, f"writer {spec.name!r}: its switch {spec.switch} is not in the table of NORMAS_BACKLOG 4.9"


def test_a_new_writer_is_documented_and_shipped_off():
    documented = _documented()
    for spec in REGISTRY.values():
        if spec.status == REGISTERED:
            assert spec.switch, f"{spec.name}: a REGISTERED writer needs a switch"
            assert documented.get(spec.switch) in OFF_VALUES, f"{spec.name}: a new writer ships OFF, the document says {documented.get(spec.switch)!r}"
            assert getattr(cfg, spec.switch) in OFF_VALUES, f"{spec.name}: its switch {spec.switch} is ON in shared/config.py before its proof"


def test_the_switches_the_autopilot_writes_with_are_documented_too():
    documented = _documented()
    for name in ("BACKLOG_IMPACT_NOTES_ENABLED", "BACKLOG_MODULE_CLUSTERS_MODE", "BACKLOG_AUTO_REVOKE_ENABLED"):
        assert name in documented
