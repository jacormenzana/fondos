"""
shared/env_guard.py -- fail-fast guard for a blocked PostgreSQL driver (RC_ENV_BLOCKED = 106).

Windows Application Control can block the `pq` DLL that psycopg loads: every DB tool then dies with an
unrelated traceback somewhere in the middle of a run. This module turns that into ONE explicit, early,
numbered failure shared by the batch launchers (lib/common.bat :init -> lib/batch_helpers.py driver-check)
and by the Python entry points (`require_db_driver()`), so the same exit code means the same thing in both
languages (doc/reglas/NORMAS_BATCH.md, P#11).

No retry, no alternative interpreter, no copied DLL: a blocked driver is an owner/IT decision.

Two layers, deliberately different:
    * batch layer  (`batch_check`)       -- skips the import while a marker `STATE_DIR/env_driver_ok` is fresh
                                            (interpreter + its mtime, 10 minutes). Cost optimisation only.
    * python layer (`require_db_driver`) -- always does the real in-process import; never reads the marker. A
                                            policy applied inside the marker window is therefore still reported
                                            as 106 by the first Python tool that runs.
A blocked environment is never cached, and the local record `STATE_DIR/env_blocked.log` is capped at 1 MB
(the DB-backed logs are unavailable by definition when the driver cannot load).

Stdlib only: this module must import in an interpreter whose DB driver is broken.
"""
from __future__ import annotations

import importlib
import os
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

RC_ENV_BLOCKED = 106                    # must equal RC_ENV_BLOCKED in lib/common.bat (tests/test_batch_standards.py)
MARKER_NAME = "env_driver_ok"
BLOCKED_LOG_NAME = "env_blocked.log"
MARKER_TTL_S = 600
LOG_MAX_BYTES = 1_000_000
DEFAULT_STATE_DIR = Path(__file__).resolve().parents[1] / "proyecto1" / "log"


def driver_module() -> str:
    """The module whose import proves the driver loads. FONDOS_DB_DRIVER_MODULE exists only so the tests can
    simulate a blocked driver without touching the real one."""
    return os.environ.get("FONDOS_DB_DRIVER_MODULE") or "psycopg"


def check_driver(module: str | None = None) -> tuple:
    """(True, "") when the driver imports, else (False, "ExcType: message"). Never raises."""
    try:
        importlib.import_module(module or driver_module())
        return True, ""
    except BaseException as exc:        # noqa: BLE001 - a blocked DLL surfaces as ImportError/OSError, never trust the type
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return False, f"{type(exc).__name__}: {str(exc)[:300]}"


# ── local record of a block ──────────────────────────────────────────────────────────────────────

def record_block(state_dir: Path | str, launcher: str, detail: str, now: datetime | None = None,
                 max_bytes: int = LOG_MAX_BYTES) -> bool:
    """Appends one line to STATE_DIR/env_blocked.log. If the file already exceeds `max_bytes` it is truncated
    first, so an orchestrator retrying in a loop cannot fill the disk. Returns False (never raises) when the
    line cannot be written: the caller's message on stderr is the primary report."""
    path = Path(state_dir) / BLOCKED_LOG_NAME
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        truncated = path.exists() and path.stat().st_size > max_bytes
        with open(path, "w" if truncated else "a", encoding="utf-8") as fh:
            if truncated:
                fh.write(f"{stamp} [truncated: the previous content exceeded {max_bytes} bytes]\n")
            fh.write(f"{stamp} RC={RC_ENV_BLOCKED} launcher={launcher or '-'} host={socket.gethostname()} "
                     f"interpreter={sys.executable} {detail}\n")
        return True
    except OSError:
        return False


def blocked_message(detail: str) -> str:
    return (f"[ERROR] RC {RC_ENV_BLOCKED}: the PostgreSQL driver cannot be loaded by {sys.executable} "
            f"({detail}). Windows Application Control is the usual cause. Nothing was run. Do not work around it "
            f"(no other interpreter, no copied DLL): ask the owner to allow the driver, then retry.")


# ── marker (batch layer only) ────────────────────────────────────────────────────────────────────

def _marker_body(interpreter: str, mtime: float, stamp: float) -> str:
    return f"{interpreter}|{int(mtime)}|{int(stamp)}"


def marker_is_fresh(state_dir: Path | str, interpreter: str | None = None, now: float | None = None,
                    ttl_s: int = MARKER_TTL_S) -> bool:
    interpreter = interpreter or sys.executable
    try:
        body = (Path(state_dir) / MARKER_NAME).read_text(encoding="utf-8").strip()
        path, mtime, stamp = body.rsplit("|", 2)
        return (path == interpreter and int(mtime) == int(os.stat(interpreter).st_mtime)
                and 0 <= (now if now is not None else time.time()) - int(stamp) <= ttl_s)
    except (OSError, ValueError):
        return False


def write_marker(state_dir: Path | str, interpreter: str | None = None, now: float | None = None) -> bool:
    interpreter = interpreter or sys.executable
    try:
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        (Path(state_dir) / MARKER_NAME).write_text(
            _marker_body(interpreter, os.stat(interpreter).st_mtime, now if now is not None else time.time()),
            encoding="utf-8")
        return True
    except OSError:
        return False


def clear_marker(state_dir: Path | str) -> None:
    try:
        (Path(state_dir) / MARKER_NAME).unlink()
    except OSError:
        pass


# ── entry points ─────────────────────────────────────────────────────────────────────────────────

def batch_check(state_dir: Path | str, launcher: str = "", now: float | None = None) -> int:
    """Used by lib/batch_helpers.py (`driver-check`). 0 = driver fine, RC_ENV_BLOCKED = blocked (message printed,
    local record written, marker removed). A fresh marker skips the import."""
    if marker_is_fresh(state_dir, now=now):
        return 0
    ok, detail = check_driver()
    if ok:
        write_marker(state_dir, now=now)
        return 0
    clear_marker(state_dir)
    print(blocked_message(detail), file=sys.stderr)
    record_block(state_dir, launcher, detail)
    return RC_ENV_BLOCKED


def require_db_driver(launcher: str | None = None, state_dir: Path | str | None = None) -> None:
    """First statement of a Python tool that needs the database (P#11: the one Python-side guard). Always does the
    real import (free: the tool needs the driver anyway) and exits RC_ENV_BLOCKED with the batch layer's message."""
    ok, detail = check_driver()
    if ok:
        return
    sdir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    clear_marker(sdir)
    print(blocked_message(detail), file=sys.stderr)
    record_block(sdir, launcher or Path(sys.argv[0]).name, detail)
    raise SystemExit(RC_ENV_BLOCKED)
