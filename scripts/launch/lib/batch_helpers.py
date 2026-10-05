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
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime

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
    print("usage: batch_helpers.py time FORMAT | standby-minutes | tail FILE [N]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
