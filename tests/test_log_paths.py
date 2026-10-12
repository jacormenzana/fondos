"""
tests/test_log_paths.py — static guards for the log-routing rule (NORMAS_IMPLEMENTACION.md §4.9).

Every log goes to the log/ directory of its project or context; the registry is `LOG_DIRS` in
shared/config.py. What each test is for:

  1. The registry itself: the expected contexts, each a `log` directory directly under its owner.
  2. `get_log_dir()`: creates on demand, refuses an unregistered context.
  3. The fallbacks that deliberately do not import shared.config (import-light: they run inside
     exception handlers) cannot drift from the registry.
  4. No production module builds a repo-root `logs/` path (the pre-registry destination of
     nav_discovery: 120+ files accumulated there).
  5. Every launcher's `LOG_DIR` / `STATE_DIR` is a registry entry. Static parse: launchers are never
     executed against the real repo from a test.

The CSV/JSONL data a diagnostic writes is not a log and is out of scope here (§4.9 rule 2).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import shared.config as cfg

_ROOT = Path(__file__).resolve().parent.parent
_SCAN_DIRS = ("proyecto1", "proyecto2", "proyecto3", "shared", "scripts")
_SKIP_PARTS = {"tests", "__pycache__", "log", "upload", "prev"}
_EXPECTED_CONTEXTS = {"p1", "p2", "p3", "shared", "tests"}
_OWNER = {"p1": "proyecto1", "p2": "proyecto2", "p3": "proyecto3", "shared": "shared", "tests": "tests"}


# ── 1. registry ──────────────────────────────────────────────────────────────

def test_registry_contexts_and_shape():
    assert set(cfg.LOG_DIRS) == _EXPECTED_CONTEXTS
    for ctx, d in cfg.LOG_DIRS.items():
        assert d == _ROOT / _OWNER[ctx] / "log", (ctx, d)


def test_versioned_log_dirs_exist():
    """shared/log and tests/log are tracked through a .gitkeep (log/ is git-ignored: `git add -f`)."""
    for ctx in ("shared", "tests"):
        assert (cfg.LOG_DIRS[ctx] / ".gitkeep").exists(), ctx


def test_log_dir_compat_alias_points_at_shared():
    assert cfg.LOG_DIR == cfg.LOG_DIRS["shared"]


# ── 2. get_log_dir ───────────────────────────────────────────────────────────

def test_get_log_dir_creates_on_demand(tmp_path, monkeypatch):
    target = tmp_path / "proyecto9" / "log"
    monkeypatch.setattr(cfg, "LOG_DIRS", {"p9": target})
    assert not target.exists()
    assert cfg.get_log_dir("p9") == target
    assert target.is_dir()


def test_get_log_dir_default_is_shared(tmp_path, monkeypatch):
    target = tmp_path / "shared" / "log"
    monkeypatch.setattr(cfg, "LOG_DIRS", {"shared": target})
    assert cfg.get_log_dir() == target


def test_get_log_dir_refuses_unknown_context():
    with pytest.raises(KeyError, match="unknown log context"):
        cfg.get_log_dir("nope")


# ── 3. fallbacks that do not import shared.config ────────────────────────────

def test_import_light_fallbacks_match_registry():
    from shared import backlog_client, cycle_telemetry
    assert backlog_client._DEFAULT_FALLBACK_LOG.parent == cfg.LOG_DIRS["shared"]
    assert cycle_telemetry.DEFAULT_FALLBACK.parent == cfg.LOG_DIRS["shared"]


@pytest.mark.parametrize("rel", ["proyecto1/core/dla2_xband_prototype.py",
                                 "proyecto1/core/dla2_dual_strategy_compare.py"])
def test_dla2_prototypes_default_log_dir_is_p1(rel):
    """The prototypes need pdfplumber to import, so the check is static: the constant is
    `<file>/../log` and the file lives where `<file>/../log` is LOG_DIRS['p1']."""
    p = _ROOT / rel
    assert p.parent.parent / "log" == cfg.LOG_DIRS["p1"]
    assert re.search(r'^_P1_LOG_DIR = Path\(__file__\)\.resolve\(\)\.parent\.parent / "log"', p.read_text(encoding="utf-8"), re.M)


# ── 4. no repo-root logs/ ────────────────────────────────────────────────────

_ROOT_LOGS = re.compile(r"""(/\s*["']logs["'])|(["']logs[/\\])|(\\logs\\)""")


def _production_py():
    for d in _SCAN_DIRS:
        for p in (_ROOT / d).rglob("*.py"):
            rel = p.relative_to(_ROOT)
            if _SKIP_PARTS & set(rel.parts[:-1]) or p.name.startswith("test_"):
                continue
            yield p, rel


def test_no_production_module_writes_to_root_logs_dir():
    # proyecto2/logs/*.py are misnamed source files (FND-0267), not writers; scanned like the rest.
    offenders = []
    for p, rel in _production_py():
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if _ROOT_LOGS.search(line):
                offenders.append(f"{rel.as_posix()}:{n}: {line.strip()[:100]}")
    assert not offenders, "root-level logs/ path (use get_log_dir):\n" + "\n".join(offenders)


# ── 5. launchers ─────────────────────────────────────────────────────────────

_LAUNCH = _ROOT / "scripts" / "launch"
_SET_DIR = re.compile(r'\bset\s+"?(LOG_DIR|STATE_DIR)=([^"\r\n]+)', re.I)


def _launcher_dirs():
    for p in sorted(list(_LAUNCH.glob("*.bat")) + list((_LAUNCH / "lib").glob("*.bat"))):
        for m in _SET_DIR.finditer(p.read_text(encoding="utf-8", errors="replace")):
            yield p.name, m.group(1).upper(), m.group(2).strip()


def _resolve(value: str) -> Path | None:
    """`%ROOT%\\x\\log` -> repo path; `%STATE_DIR%` is checked at its own definition."""
    if value.upper() == "%STATE_DIR%":
        return None
    assert value.upper().startswith("%ROOT%\\"), f"launcher dir not derived from %ROOT%: {value}"
    return _ROOT.joinpath(*value.split("\\")[1:])


def test_launcher_log_dirs_are_registry_entries():
    seen = list(_launcher_dirs())
    assert seen, "no launcher defines LOG_DIR/STATE_DIR: parser out of date"
    registry = set(cfg.LOG_DIRS.values())
    bad = []
    for name, var, value in seen:
        d = _resolve(value)
        if d is not None and d not in registry:
            bad.append(f"{name}: {var}={value}")
    assert not bad, "launcher log/state dir outside LOG_DIRS:\n" + "\n".join(bad)


def test_audit_launchers_route_to_owning_project():
    by = {}
    for name, var, value in _launcher_dirs():
        by.setdefault(name, []).append(_resolve(value))
    assert by["AUDIT_P1.bat"] == [cfg.LOG_DIRS["p1"]]
    assert by["AUDIT_P2.bat"] == [cfg.LOG_DIRS["p2"]]
    assert by["AUDIT_statistical.bat"] == [cfg.LOG_DIRS["p1"], cfg.LOG_DIRS["p2"]]   # costs default, p2 override


def test_launcher_state_dir_is_p1_log():
    states = [(n, v) for n, var, v in _launcher_dirs() if var == "STATE_DIR"]
    assert states == [("common.bat", r"%ROOT%\proyecto1\log")]
