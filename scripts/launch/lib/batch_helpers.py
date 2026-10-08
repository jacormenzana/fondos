#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
batch_helpers.py -- small helpers called by scripts/launch/lib/common.bat.

Why Python and not PowerShell: logic belongs in Python, where it is tested (doc/reglas/NORMAS_BATCH.md section 1),
and the interpreter is already a prerequisite of every launcher. PowerShell cost ~0.3 s per call, was only used to
print the time, and when it hung (it did, 2026-10-04: even `powershell -Command 1+1` never returned) every launcher
hung with it. These helpers start in ~30 ms and cannot hang on a console.

Sub-commands (always exit 0 unless the arguments are wrong):
    time FORMAT            current time with a .NET-style format (yyyy MM dd HH mm ss and separators), e.g. yyyyMMdd_HHmmss
    standby-minutes        AC suspension timeout of the active power plan, in minutes; prints nothing if unknown
    tail FILE [N]          the last N lines (default 15) of FILE; nothing if it does not exist
    usage FILE             the usage of a launcher: its own header comment block (what -h/--help prints, before anything runs)
    driver-check STATE_DIR LAUNCHER
                           exit 0 when the PostgreSQL driver loads in THIS interpreter, 106 (RC_ENV_BLOCKED) when it
                           does not (shared/env_guard.py: message on stderr, local record, 10-minute ok marker)
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# .NET custom format tokens the launchers use -> strftime. Anything else is rejected: a typo must never
# silently print a wrong stamp that then names a log file or a baseline.
_TOKENS = {"yyyy": "%Y", "MM": "%m", "dd": "%d", "HH": "%H", "mm": "%M", "ss": "%S"}
_FORMAT = re.compile(r"yyyy|MM|dd|HH|mm|ss|[_\-:. ]")


def strftime_format(net_format: str) -> str:
    out, pos = [], 0
    while pos < len(net_format):
        m = _FORMAT.match(net_format, pos)
        if not m:
            raise ValueError(f"unsupported format token at {net_format[pos:]!r} in {net_format!r}")
        out.append(_TOKENS.get(m.group(0), m.group(0)))
        pos = m.end()
    return "".join(out)


def now(net_format: str, when: datetime | None = None) -> str:
    return (when or datetime.now()).strftime(strftime_format(net_format))


_HEX = re.compile(r"0x[0-9a-fA-F]+")


def parse_standby_minutes(query_output: str) -> int | None:
    """`powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE` localizes its labels, so no text is searched: the
    output always ends with two hexadecimal values, AC and DC, in seconds. The penultimate one is the AC value."""
    values = _HEX.findall(query_output)
    if len(values) < 2:
        return None
    return int(values[-2], 16) // 60


def read_standby_minutes(exe: str | None = None) -> int | None:
    exe = exe or os.environ.get("FONDOS_POWERCFG") or "powercfg"
    try:
        r = subprocess.run([exe, "/query", "SCHEME_CURRENT", "SUB_SLEEP", "STANDBYIDLE"],
                           capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return parse_standby_minutes(r.stdout.decode("utf-8", errors="replace"))


def tail(path: str, n: int = 15) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return "\n".join(fh.read().splitlines()[-n:])
    except OSError:
        return ""


ROOT = Path(__file__).resolve().parents[3]


def usage_text(path: str) -> str:
    """The usage of a launcher = its own header comment: the run of `::` lines that follows the five skeleton lines, minus the `:: ====` rules
    and the leading `:: `. ONE source (the file's header, which NORMAS_BATCH.md section 3 already requires to document every option), so
    `-h/--help` of the launchers that have no hand-written usage cannot drift from it. Empty when the file has no header."""
    try:
        lines = Path(path).read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n").split("\n")
    except OSError:
        return ""
    out, started = [], False
    for ln in lines[5:]:
        if ln.startswith("::"):
            started = True
            body = ln[2:]
            if body.strip().strip("=") == "":
                if out:
                    out.append("")          # a rule inside the header separates blocks; collapsed below
                continue
            out.append(body[1:] if body.startswith(" ") else body)
        elif started or ln.strip():
            break
    text = "\n".join(out).strip("\n")
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text


def driver_check(state_dir: str, launcher: str) -> int:
    """The batch-layer environment guard. The logic lives in shared/env_guard.py (one implementation for the
    launchers and the Python tools); this only locates it relative to the library, like every other path."""
    sys.path.insert(0, str(ROOT))
    try:
        from shared import env_guard
    except ImportError as exc:
        print(f"[batch_helpers] shared/env_guard.py not found under {ROOT}: {exc}", file=sys.stderr)
        return 1
    return env_guard.batch_check(state_dir, launcher)


def main(argv: list | None = None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if a[:1] == ["time"] and len(a) == 2:
        try:
            print(now(a[1]))
        except ValueError as exc:
            print(f"[batch_helpers] {exc}", file=sys.stderr)
            return 2
        return 0
    if a == ["standby-minutes"]:
        minutes = read_standby_minutes()
        if minutes is not None:
            print(minutes)
        return 0
    if a[:1] == ["tail"] and len(a) in (2, 3) and (len(a) == 2 or a[2].isdigit()):
        out = tail(a[1], int(a[2]) if len(a) == 3 else 15)
        if out:
            print(out)
        return 0
    if a[:1] == ["usage"] and len(a) == 2:
        text = usage_text(a[1])
        print(text or f"(no usage header in {Path(a[1]).name})")
        return 0
    if a[:1] == ["driver-check"] and len(a) in (2, 3):
        return driver_check(a[1], a[2] if len(a) == 3 else "")
    print("usage: batch_helpers.py time FORMAT | standby-minutes | tail FILE [N] | usage FILE | driver-check STATE_DIR [LAUNCHER]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
