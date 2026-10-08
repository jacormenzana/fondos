"""
tests/test_env_guard.py -- shared/env_guard.py: the fail-fast guard for a blocked PostgreSQL driver (RC 106).

Pure (stdlib only, tmp_path): the real driver is never touched; a blocked one is simulated with a module name
that does not exist (FONDOS_DB_DRIVER_MODULE), which is the same ImportError path as a blocked DLL.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared import env_guard as g  # noqa: E402


@pytest.fixture()
def blocked(monkeypatch):
    monkeypatch.setenv("FONDOS_DB_DRIVER_MODULE", "no_such_driver_module_xyz")


def test_the_code_is_106():
    assert g.RC_ENV_BLOCKED == 106 and 100 <= g.RC_ENV_BLOCKED <= 199


def test_check_driver_ok_and_blocked(monkeypatch, blocked):
    ok, detail = g.check_driver()
    assert not ok and "ModuleNotFoundError" in detail
    monkeypatch.setenv("FONDOS_DB_DRIVER_MODULE", "json")             # any importable module
    assert g.check_driver() == (True, "")


def test_a_dll_style_oserror_is_also_a_block(monkeypatch):
    def boom(_name):
        raise OSError("DLL load failed: blocked by policy")
    monkeypatch.setattr(g.importlib, "import_module", boom)
    ok, detail = g.check_driver()
    assert not ok and detail.startswith("OSError")


# ── marker ──────────────────────────────────────────────────────────────────────────────────────

def test_marker_roundtrip_and_ttl(tmp_path):
    assert not g.marker_is_fresh(tmp_path)
    assert g.write_marker(tmp_path, now=1000.0)
    assert g.marker_is_fresh(tmp_path, now=1000.0 + g.MARKER_TTL_S)
    assert not g.marker_is_fresh(tmp_path, now=1000.0 + g.MARKER_TTL_S + 1)       # expired
    assert not g.marker_is_fresh(tmp_path, now=999.0)                              # from the future: not trusted


def test_marker_is_tied_to_the_interpreter(tmp_path):
    g.write_marker(tmp_path, now=1000.0)
    other = tmp_path / "other_python.exe"
    other.write_text("x")
    assert not g.marker_is_fresh(tmp_path, interpreter=str(other), now=1000.0)


def test_a_garbled_marker_is_not_fresh(tmp_path):
    (tmp_path / g.MARKER_NAME).write_text("garbage")
    assert not g.marker_is_fresh(tmp_path)


# ── batch layer ─────────────────────────────────────────────────────────────────────────────────

def test_batch_check_ok_writes_the_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("FONDOS_DB_DRIVER_MODULE", "json")
    assert g.batch_check(tmp_path, "X.bat") == 0
    assert g.marker_is_fresh(tmp_path)
    assert not (tmp_path / g.BLOCKED_LOG_NAME).exists()


def test_batch_check_blocked_returns_106_records_and_never_caches(tmp_path, blocked, capsys):
    g.write_marker(tmp_path, now=1.0)                                  # a stale marker from an earlier good run
    assert g.batch_check(tmp_path, "P1_P2_Complete.bat", now=1.0 + g.MARKER_TTL_S + 5) == g.RC_ENV_BLOCKED
    assert not (tmp_path / g.MARKER_NAME).exists()                     # a blocked environment is never cached
    err = capsys.readouterr().err
    assert "RC 106" in err and "Do not work around it" in err
    line = (tmp_path / g.BLOCKED_LOG_NAME).read_text()
    assert "launcher=P1_P2_Complete.bat" in line and "ModuleNotFoundError" in line


def test_batch_check_fresh_marker_skips_the_import(tmp_path, blocked):
    g.write_marker(tmp_path)
    assert g.batch_check(tmp_path, "X.bat") == 0                       # the marker is a cost optimisation, documented


# ── python layer ────────────────────────────────────────────────────────────────────────────────

def test_require_db_driver_exits_106_even_with_a_fresh_marker(tmp_path, blocked, capsys):
    g.write_marker(tmp_path)                                           # the python layer must NOT trust the marker
    with pytest.raises(SystemExit) as e:
        g.require_db_driver("tool.py", tmp_path)
    assert e.value.code == g.RC_ENV_BLOCKED
    assert "RC 106" in capsys.readouterr().err
    assert not (tmp_path / g.MARKER_NAME).exists()


def test_require_db_driver_returns_quietly_when_the_driver_loads(tmp_path, monkeypatch):
    monkeypatch.setenv("FONDOS_DB_DRIVER_MODULE", "json")
    assert g.require_db_driver("tool.py", tmp_path) is None
    assert not (tmp_path / g.BLOCKED_LOG_NAME).exists()


# ── the local record is bounded (1 MB) ──────────────────────────────────────────────────────────

def test_record_block_appends_below_the_cap(tmp_path):
    for i in range(3):
        assert g.record_block(tmp_path, f"L{i}.bat", "detail")
    assert len((tmp_path / g.BLOCKED_LOG_NAME).read_text().splitlines()) == 3


def test_record_block_truncates_above_the_cap(tmp_path):
    log = tmp_path / g.BLOCKED_LOG_NAME
    log.write_text("x" * (g.LOG_MAX_BYTES + 10))
    assert g.record_block(tmp_path, "L.bat", "detail", now=datetime(2026, 10, 8, 12, 0, 0))
    lines = log.read_text().splitlines()
    assert len(lines) == 2 and "truncated" in lines[0] and "launcher=L.bat" in lines[1]
    assert log.stat().st_size < 2000


def test_a_retry_loop_cannot_grow_the_log_without_bound(tmp_path):
    for _ in range(40):
        g.record_block(tmp_path, "loop.bat", "d" * 200, max_bytes=2000)
    assert (tmp_path / g.BLOCKED_LOG_NAME).stat().st_size <= 2000 + 600


def test_record_block_never_raises(tmp_path):
    blocker = tmp_path / "file_not_dir"
    blocker.write_text("x")
    assert g.record_block(blocker / "sub", "L.bat", "d") is False
