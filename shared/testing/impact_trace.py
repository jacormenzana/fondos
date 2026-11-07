# shared/testing/impact_trace.py
# -*- coding: utf-8 -*-
"""impact_trace.py -- dynamic ground truth for the impact selector (FND-0385, evidence E2). A pytest plugin that records, for every TEST FILE, which repository files actually executed code
while its tests ran (and while it was collected/imported). The selector's static graph must contain every one of them: a file that ran but is not in the test's static closure is a hole.

    IMPACT_TRACE_DIR=<dir> IMPACT_TRACE_ROOT=<repo root> PYTEST_ADDOPTS="-p shared.testing.impact_trace" python -m pytest ...
    -> <dir>/main_<pid>.json and <dir>/child_<pid>.json: {"<test file>": ["<repo file>", ...]}

How: sys.monitoring PY_START (Python 3.12+) with the callback returning DISABLE, so every code location fires once; `restart_events()` at each switch of test file re-arms them, which gives per-test-file
attribution at the cost of one callback per location per test file. Python subprocesses a test starts are followed through a generated sitecustomize on PYTHONPATH (they inherit the environment; one started
with its own `env=` is blind, and the selector already runs such tests in every impact receipt). The plugin is FAIL-OPEN: any error here is swallowed and can never change a test verdict. It is a
measuring instrument, loaded only by the evidence harness (scripts/audit/impact_selection_evidence.py), never by a delivery receipt. Stdlib only apart from pytest."""
from __future__ import annotations

import atexit
import json
import os
import sys
from pathlib import Path

_OUT = os.environ.get("IMPACT_TRACE_DIR")
_ROOT = Path(os.environ.get("IMPACT_TRACE_ROOT") or ".").resolve()
_TOOL = 1                                                   # sys.monitoring.COVERAGE_ID: free unless coverage.py runs, which the harness does not
_seen: dict = {}
_current: list = ["(collection)"]
_cache: dict = {}
_started = False


def _rel(filename: str):
    hit = _cache.get(filename, 0)
    if hit != 0:
        return hit
    try:
        rel = Path(filename).resolve().relative_to(_ROOT).as_posix()
        rel = rel if rel.endswith(".py") and not rel.startswith((".git/", "out/")) else None
    except (ValueError, OSError):
        rel = None
    _cache[filename] = rel
    return rel


_DISABLE = getattr(getattr(sys, "monitoring", None), "DISABLE", None)      # bound now: at interpreter shutdown the module globals (sys) are already None


def _on_start(code, offset):
    try:
        rel = _rel(code.co_filename)
        if rel:
            _seen.setdefault(_current[0], set()).add(rel)
    except BaseException:                                   # noqa: BLE001 -- fail open, silent at shutdown
        pass
    return _DISABLE


def _switch(test_file: str) -> None:
    try:
        _current[0] = test_file
        os.environ["IMPACT_TRACE_TEST"] = test_file         # a child process attributes its execution to the test that started it
        sys.monitoring.restart_events()
    except Exception:                                       # noqa: BLE001
        pass


def _start() -> None:
    global _started
    if _started or not _OUT:
        return
    try:
        mon = sys.monitoring
        mon.use_tool_id(_TOOL, "impact_trace")
        mon.register_callback(_TOOL, mon.events.PY_START, _on_start)
        mon.set_events(_TOOL, mon.events.PY_START)
        _started = True
    except Exception:                                       # noqa: BLE001 -- another tool owns the id: trace nothing rather than disturb the run
        pass


def _dump(prefix: str) -> None:
    try:
        if not _OUT:
            return
        Path(_OUT).mkdir(parents=True, exist_ok=True)
        data = {k: sorted(v) for k, v in _seen.items()}
        tmp = Path(_OUT) / f".{prefix}_{os.getpid()}.tmp"
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        os.replace(tmp, Path(_OUT) / f"{prefix}_{os.getpid()}.json")
    except Exception:                                       # noqa: BLE001
        pass


def start_child() -> None:
    """Called from the generated sitecustomize in a subprocess: trace it and attribute everything to the test that started it."""
    _current[0] = os.environ.get("IMPACT_TRACE_TEST") or "(unattributed)"
    _start()
    atexit.register(_dump, "child")


def _write_sitecustomize() -> None:
    site = Path(_OUT) / "site"
    site.mkdir(parents=True, exist_ok=True)
    (site / "sitecustomize.py").write_text(
        "import importlib.util, os\n"
        "try:\n"
        f"    _s = importlib.util.spec_from_file_location('_impact_trace_child', {str(Path(__file__).resolve())!r})\n"
        "    _m = importlib.util.module_from_spec(_s); _s.loader.exec_module(_m); _m.start_child()\n"
        "except Exception:\n"
        "    pass\n", encoding="utf-8")
    os.environ["PYTHONPATH"] = str(site) + os.pathsep + os.environ.get("PYTHONPATH", "")


# ── pytest hooks (only meaningful in the main process) ──────────────────────────

if "pytest" in sys.modules:                                 # only inside the pytest process. A traced CHILD must never import pytest: shared/config.py turns the .env autoload off when "pytest" is in
    import pytest                                           # sys.modules, so an instrument that imported it would change the behaviour it measures (A10, found by the first traced full run)

    def pytest_configure(config):
        if _OUT:
            _start()
            try:
                _write_sitecustomize()
            except Exception:                               # noqa: BLE001
                pass

    @pytest.hookimpl(wrapper=True)
    def pytest_make_collect_report(collector):
        path = getattr(collector, "path", None)
        if _OUT and isinstance(collector, pytest.Module) and path is not None:
            _switch(_rel_test(path))                        # importing a test module runs the code it imports: attribute that to the module
        return (yield)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_protocol(item, nextitem):
        if _OUT:
            _switch(_rel_test(item.path))
        return (yield)

    def pytest_sessionfinish(session, exitstatus):
        _dump("main")

    def _rel_test(path) -> str:
        try:
            return Path(path).resolve().relative_to(_ROOT).as_posix()
        except (ValueError, OSError):
            return str(path)
