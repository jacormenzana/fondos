# -*- coding: utf-8 -*-
"""scripts/audit/sync_agents_md.py: a sentinel that cannot be checked is a failure (FND-0312), and both skill layouts give one table (R-7: no DB)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "audit"))

import sync_agents_md as sam  # noqa: E402


def _block(name: str, body: str) -> str:
    return f"<!-- AUTO:BEGIN {name} -->\n{body}\n<!-- AUTO:END {name} -->"


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """Two governed files: AGENTS.md owns sentinel `a`, doc.md owns `b`; both start in sync."""
    agents, doc = tmp_path / "AGENTS.md", tmp_path / "doc.md"
    agents.write_text("intro\n" + _block("a", "A1") + "\nouttro\n", encoding="utf-8", newline="\n")
    doc.write_text("head\n" + _block("b", "B1") + "\ntail\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(sam, "ROOT", tmp_path)
    monkeypatch.setattr(sam, "AGENTS", agents)
    monkeypatch.setattr(sam, "GENERATORS", {"a": lambda: "A1", "b": (lambda: "B1", doc)})
    return agents, doc


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_in_sync_tree_passes_and_write_is_a_noop(tree):
    agents, doc = tree
    before = (_text(agents), _text(doc))
    assert sam.sync("check") == 0
    assert sam.sync("write") == 0
    assert (_text(agents), _text(doc)) == before


def test_drift_fails_check_and_write_repairs_it(tree, monkeypatch):
    agents, _ = tree
    monkeypatch.setitem(sam.GENERATORS, "a", lambda: "A2")
    assert sam.sync("check") == 1
    assert sam.sync("write") == 0
    assert "A2" in _text(agents) and sam.sync("check") == 0


def test_registered_sentinel_missing_from_its_file_fails(tree):
    agents, _ = tree
    agents.write_text("no markers here\n", encoding="utf-8", newline="\n")
    assert sam.sync("check") == 1


def test_duplicated_sentinel_fails(tree):
    agents, _ = tree
    agents.write_text(_text(agents) + _block("a", "A1") + "\n", encoding="utf-8", newline="\n")
    assert sam.sync("check") == 1


@pytest.mark.parametrize("broken", [
    "<!-- AUTO:BEGIN a -->\nA1\n",                                  # BEGIN without END
    "A1\n<!-- AUTO:END a -->\n",                                    # END without BEGIN
    "<!-- AUTO:BEGIN a -->\nA1\n<!-- AUTO:END b -->\n",             # mismatched names
])
def test_unbalanced_or_malformed_markers_fail(tree, broken):
    agents, _ = tree
    agents.write_text(broken, encoding="utf-8", newline="\n")
    assert sam.sync("check") == 1


def test_sentinel_without_a_generator_for_that_file_fails(tree):
    agents, doc = tree
    agents.write_text(_text(agents) + _block("zz", "?") + "\n", encoding="utf-8", newline="\n")
    assert sam.sync("check") == 1
    agents.write_text("x\n" + _block("a", "A1") + "\n", encoding="utf-8", newline="\n")
    doc.write_text(_text(doc) + _block("a", "A1") + "\n", encoding="utf-8", newline="\n")  # registered for AGENTS.md, found in doc.md
    assert sam.sync("check") == 1


def test_raising_generator_fails_instead_of_keeping_the_old_block(tree, monkeypatch):
    def boom():
        raise RuntimeError("source unreadable")
    monkeypatch.setitem(sam.GENERATORS, "a", boom)
    assert sam.sync("check") == 1


def test_write_refuses_and_writes_nothing_when_the_structure_is_broken(tree, monkeypatch):
    agents, doc = tree
    monkeypatch.setitem(sam.GENERATORS, "b", (lambda: "B2", doc))     # doc.md drifts...
    agents.write_text("no markers\n", encoding="utf-8", newline="\n")  # ...while AGENTS.md is broken
    before = (_text(agents), _text(doc))
    assert sam.sync("write") == 1
    assert (_text(agents), _text(doc)) == before


def test_report_mode_prints_errors_but_stays_informational(tree):
    agents, _ = tree
    agents.write_text("no markers\n", encoding="utf-8", newline="\n")
    assert sam.sync("report") == 0


# ---------------- skills: flat and registered layouts give the same table ----------------

def _skill(text_name: str) -> str:
    return f"---\nname: {text_name}\ndescription: Does {text_name} things\n---\n\n# body\n"


def test_skills_table_is_identical_for_flat_and_skill_md_layouts(tmp_path, monkeypatch):
    monkeypatch.setattr(sam, "ROOT", tmp_path)
    flat = tmp_path / ".claude" / "skills"
    flat.mkdir(parents=True)
    for n in ("alphaSkill", "betaSkill"):
        (flat / f"{n}.md").write_text(_skill(n), encoding="utf-8")
    table_flat = sam.gen_skills_table()

    for f in list(flat.glob("*.md")):
        (flat / f.stem).mkdir()
        f.rename(flat / f.stem / "SKILL.md")
    assert sam.gen_skills_table() == table_flat
    assert "| `alphaSkill` | Does alphaSkill things |" in table_flat


def test_skill_defined_in_both_layouts_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(sam, "ROOT", tmp_path)
    skills = tmp_path / ".claude" / "skills"
    (skills / "dup").mkdir(parents=True)
    (skills / "dup.md").write_text(_skill("dup"), encoding="utf-8")
    (skills / "dup" / "SKILL.md").write_text(_skill("dup"), encoding="utf-8")
    with pytest.raises(ValueError, match="both layouts"):
        sam.gen_skills_table()
