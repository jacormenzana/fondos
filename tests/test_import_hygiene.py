"""The import-hygiene plugin (shared/testing/import_hygiene.py): after a module put shared/ at the front of sys.path, the bare names of the scripts still mean the scripts. No database, no network."""
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from shared.testing import import_hygiene as ih  # noqa: E402


def test_the_colliding_names_are_computed_from_the_tree_and_include_the_four_known_ones():
    assert {"backlog_autopilot", "backlog_impact", "backlog_modules", "owner_vault"} <= set(ih.COLLIDING)
    assert all((ROOT / "shared" / f"{n}.py").exists() for n in ih.COLLIDING)


def test_after_shared_is_put_at_the_front_the_bare_name_is_the_script_again(monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    saved = {n: sys.modules.get(n) for n in ih.COLLIDING}
    try:
        for n in ih.COLLIDING:
            sys.modules.pop(n, None)
        sys.path.insert(0, str(ROOT / "shared"))                      # what proyecto1/core/fund_writer.py does when it is imported
        polluted = importlib.import_module("backlog_modules")
        assert Path(polluted.__file__).parent == ROOT / "shared"        # the defect: the bare name is the library
        dropped = ih.restore()
        assert "backlog_modules" in dropped
        assert sys.path[:len(ih.SCRIPT_DIRS)] == list(ih.SCRIPT_DIRS)                     # the script directories are back at the front, order kept
        again = importlib.import_module("backlog_modules")
        assert Path(again.__file__).parent == ROOT / "scripts" / "gov" / "backlog" and hasattr(again, "cluster_step")
        assert ih.restore() == []                                       # idempotent (the scripts put the repository root in front of the path again when they are imported: not our concern)
        owner = importlib.import_module("owner_vault")
        assert Path(owner.__file__).parent == ROOT / "scripts" / "gov" / "backlog" and hasattr(owner, "main")
    finally:
        for n, m in saved.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m
