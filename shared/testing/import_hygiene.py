# shared/testing/import_hygiene.py
# -*- coding: utf-8 -*-
"""import_hygiene.py -- keep the bare names of the repository's scripts pointing at the SCRIPTS (pytest plugin, registered in the root conftest).

Four names exist twice: shared/<name>.py (a library) and scripts/{audit,ops}/<name>.py (the tool that wraps it): backlog_autopilot, backlog_impact, backlog_modules, owner_vault. The scripts and their tests
import the tool by its bare name (`import backlog_modules as run`) after putting the script directory on sys.path. Two P1 modules (proyecto1/core/fund_writer.py, cost_cross_validator.py) put the `shared/`
directory itself at the FRONT of sys.path when they are imported, which happens at collection time of any test that imports run_block. From then on, in that process, the bare name resolved to the LIBRARY: 23 to 32
tests failed with "module 'backlog_modules' has no attribute 'cluster_step'" or "'owner_vault' has no attribute 'main'", but only in the shard compositions that put the two kinds of file in one pytest process
(receipts of 96cd9bb and 2bfbead, 2026-11-12; found when the receipt balanced the shards with new durations). Before every module collection and every test this plugin puts the script directories back at the
front of sys.path and drops a bare name that was registered from shared/. It changes nothing else: `import config` and the other bare imports of P1 keep working, and production code is untouched. Pure stdlib."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SHARED = _ROOT / "shared"
SCRIPT_DIRS = tuple(str(_ROOT / "scripts" / d) for d in ("audit", "ops"))


def colliding_names(root: Path = _ROOT) -> tuple:
    """The module names that exist in shared/ and in scripts/audit or scripts/ops. Computed, never listed by hand. Pure apart from reading the directories."""
    lib = {p.stem for p in (root / "shared").glob("*.py")} - {"__init__"}
    tools = {p.stem for d in ("audit", "ops") for p in (root / "scripts" / d).glob("*.py")}
    return tuple(sorted(lib & tools))


COLLIDING = colliding_names()


def _under_shared(path) -> bool:
    try:
        return Path(path).resolve().parent == _SHARED
    except (OSError, ValueError, TypeError):
        return False


def restore() -> list:
    """Script directories back at the front of sys.path (order kept) and every colliding bare name that was registered from shared/ dropped from sys.modules. Returns the dropped names. Idempotent."""
    for d in reversed(SCRIPT_DIRS):
        while d in sys.path:
            sys.path.remove(d)
        sys.path.insert(0, d)
    dropped = []
    for name in COLLIDING:
        mod = sys.modules.get(name)
        if mod is not None and _under_shared(getattr(mod, "__file__", None)):
            del sys.modules[name]
            dropped.append(name)
    return dropped


def pytest_collectstart(collector):                       # noqa: D401 -- pytest hook: before a test module is imported
    restore()


def pytest_runtest_setup(item):                           # noqa: D401 -- pytest hook: before each test
    restore()
