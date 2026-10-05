"""
tests/test_batch_standards.py -- static enforcement of doc/reglas/NORMAS_BATCH.md over EVERY .bat launcher.

Pure text checks (no cmd.exe, any OS). They make the standard a gate instead of a wish: a new launcher cannot
re-introduce a hard-coded repo path, a bare `python`, a literal version/date, a raw `powercfg`/`chcp`, a
literal exit code that collides with a tool's, or an `endlocal` / `exit` split over two lines.

Allowlist protocol (tests/_allowlist.py): `{(rule, file): reason}`, reason cites a ticket or starts with `DESIGN:`;
a stale key fails the suite. A NEW launcher gets no entry -- fix the launcher.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from _allowlist import bad_reasons

ROOT = Path(__file__).resolve().parent.parent
LAUNCH = ROOT / "scripts" / "launch"
LIB = LAUNCH / "lib"

BATS = sorted(p for p in [*LAUNCH.glob("*.bat"), *LIB.glob("*.bat")])
COMMON = LIB / "common.bat"
TOP_LEVEL_LOCKED = ("P1_P2_Complete.bat", "P2_P3_complete.bat", "P1_P2_P3.bat")
IS_LIB = lambda p: p.parent == LIB          # lib files are subroutines / data, not launchers

# {(rule, file name): reason}
_EXEMPT: dict = {
    ("no_fixed_dates", "P2_discoverLoadMetrics.bat"):
        "DESIGN: --desde 2000-01-01 is the start of the NAV history we keep, a parameter of the data and not a stale version",
}


def _lines(p: Path) -> list:
    return p.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n").split("\n")


def _code(p: Path):
    """(lineno, line) for every non-comment, non-blank line."""
    for n, ln in enumerate(_lines(p), 1):
        s = ln.strip()
        if s and not s.startswith("::") and not s.lower().startswith("rem "):
            yield n, ln


def _exempt(rule: str, p: Path) -> bool:
    return (rule, p.name) in _EXEMPT


def _fail_if(rule: str, offenders: list, how: str):
    offenders = [o for o in offenders if o]
    assert not offenders, f"[{rule}] NORMAS_BATCH.md violated:\n  " + "\n  ".join(offenders) + f"\n{how}"


def _per_file(rule: str, check, how: str):
    out = []
    for p in BATS:
        if _exempt(rule, p):
            continue
        out += [f"{p.relative_to(ROOT).as_posix()}:{n}: {why}" for n, why in check(p)]
    _fail_if(rule, out, how)


# ─── the allowlist itself ────────────────────────────────────────────────────────────────────────

def test_exemption_reasons_are_real():
    assert not bad_reasons({f"{r}:{f}": v for (r, f), v in _EXEMPT.items()})


def test_exemptions_are_not_stale():
    names = {p.name for p in BATS}
    assert not [k for k in _EXEMPT if k[1] not in names], "an exempt launcher no longer exists: delete the entry"


def test_there_are_launchers_to_check():
    assert COMMON.exists() and len(BATS) >= 15, BATS


# ─── §2 skeleton, line endings, encoding ─────────────────────────────────────────────────────────

def test_crlf_only():
    _per_file("crlf", lambda p: [(0, "LF-only line endings (cmd.exe needs CRLF)")]
              if re.search(rb"(?<!\r)\n", p.read_bytes()) else [], "Convert to CRLF and check with `file`.")


def test_ascii_only():
    def chk(p):
        return [(n, "non-ASCII character") for n, ln in enumerate(_lines(p), 1) if any(ord(c) > 127 for c in ln)]
    _per_file("ascii", chk, "Write messages without accents (the file is ASCII; the console page is set by :utf8_on).")


def test_skeleton_first_lines():
    def chk(p):
        ls = _lines(p)
        if IS_LIB(p):
            return [] if ls[0].lower() == "@echo off" else [(1, "must start with @echo off")]
        want = ["@echo off",
                "setlocal EnableExtensions EnableDelayedExpansion",
                'call "%~dp0lib\\common.bat" :init || (endlocal & exit /b 101)',
                'call "%COMMON%" :utf8_on']
        return [(i + 1, f"line {i + 1} must be exactly: {w}") for i, w in enumerate(want) if ls[i] != w]
    _per_file("skeleton", chk, "Start from scripts/launch/_template.bat.")


def test_every_launcher_documents_its_usage():
    def chk(p):
        return [] if IS_LIB(p) or any("Uso:" in ln for ln in _lines(p)[:80] if ln.startswith("::")) \
            else [(1, "no `:: Uso:` block in the header")]
    _per_file("usage_doc", chk, "The header documents every option (NORMAS_BATCH.md section 3).")


def test_launchers_that_reject_arguments_also_answer_help():
    def chk(p):
        text = "\n".join(_lines(p))
        if IS_LIB(p) or "%RC_USAGE%" not in text:
            return []
        return [] if re.search(r'--help', text) else [(1, "rejects unknown arguments but has no -h/--help")]
    _per_file("help", chk, "Add `-h`/`--help` (usage, RC 0).")


# ─── §4 nothing fixed ────────────────────────────────────────────────────────────────────────────

def test_no_fixed_repo_or_interpreter_paths():
    pat = re.compile(r"c:\\desarrollo|c:\\data\\envs", re.I)

    def chk(p):
        return [(n, "hard-coded repo/interpreter path: " + ln.strip()[:70]) for n, ln in _code(p)
                if pat.search(ln) and not (p == COMMON and ln.strip().startswith('set "PYTHON='))]
    _per_file("no_fixed_paths", chk, "Use %ROOT% / %PYTHON% / %LAUNCH% from :init (the only literal lives in common.bat).")


def test_absolute_drive_paths_only_in_set_lines():
    drive = re.compile(r"(?<![\w%])[A-Za-z]:\\")
    setline = re.compile(r'^\s*set\s+"?\w+=', re.I)

    def chk(p):
        return [(n, "drive path outside a `set` line: " + ln.strip()[:70]) for n, ln in _code(p)
                if drive.search(ln) and not setline.match(ln)]
    _per_file("drive_paths", chk, "Data paths outside the repo are configuration: one `set \"X=C:\\...\"` at the top.")


def test_no_fixed_versions_or_dates():
    pat = re.compile(r"(?<![\w.])20\d{6}(?![\w.])|(?<![\w.])20\d{2}-\d{2}-\d{2}(?![\w])")

    def chk(p):
        return [(n, "date/version literal: " + ln.strip()[:70]) for n, ln in _code(p) if pat.search(ln)]
    _per_file("no_fixed_dates", chk, "Read it from the system (calc-version, git, :get_time) -- never write it.")


def test_python_is_always_the_quoted_interpreter_variable():
    bare = re.compile(r"(^|[\s(&|])(python|pip)(\.exe)?(\s|$)", re.I)
    unquoted = re.compile(r"^\s*@?%PYTHON%")

    def chk(p):
        out = []
        for n, ln in _code(p):
            s = ln.strip()
            if unquoted.match(ln):
                out.append((n, 'unquoted %PYTHON% as a command: use "%PYTHON%"'))
            elif bare.search(re.sub(r'"[^"]*"', '""', ln)) and not s.lower().startswith("echo"):
                out.append((n, "bare python/pip: use \"%PYTHON%\" (bare python resolves to the WindowsApps shim)"))
        return out
    _per_file("python_var", chk, 'Run Python as "%PYTHON%" -X utf8 ... (set by :init).')


# ─── §6 exit codes ───────────────────────────────────────────────────────────────────────────────

def test_no_literal_exit_codes_that_collide_with_tool_codes():
    pat = re.compile(r"exit\s+/b\s+(\d+)\b", re.I)

    def chk(p):
        out = []
        for n, ln in _code(p):
            for m in pat.finditer(ln):
                v = int(m.group(1))
                if v == 0 or v >= 100 or (v == 1 and p == COMMON):
                    continue
                out.append((n, f"literal `exit /b {v}`: 1-99 belong to the tools; use an RC_* constant (100-199)"))
        return out
    _per_file("exit_codes", chk, "Own codes: RC_USAGE, RC_ENV, RC_REFUSED, RC_STATE, RC_PREFLIGHT, RC_BUSY (from :init).")


def test_endlocal_and_exit_are_on_one_line():
    def chk(p):
        text = "\n".join(_lines(p))
        return [(text[:m.start()].count("\n") + 1, "`endlocal` and `exit /b !VAR!` on separate lines (returns 0 always)")
                for m in re.finditer(r"^endlocal[ \t]*\n[ \t]*exit /b[^\n]*!", text, re.M | re.I)]
    _per_file("endlocal_one_line", chk, "`endlocal & exit /b %RC%` in a single line.")


# ─── §8 global effects live in the library, and are paired ───────────────────────────────────────

@pytest.mark.parametrize("word,rule", [("powercfg", "powercfg"), ("chcp", "chcp"), ("wmic", "wmic"),
                                       ("powershell", "powershell")])
def test_global_effect_commands_only_in_the_library(word, rule):
    pat = re.compile(rf"(^|[\s&(|]){word}(\s|\.exe|$)", re.I)

    def chk(p):
        if p == COMMON:
            return []
        return [(n, f"direct `{word}`: use the lib\\common.bat subroutine") for n, ln in _code(p)
                if pat.search(ln) and not ln.strip().lower().startswith("echo")]
    _per_file(rule, chk, "powercfg -> :standby_disable/:standby_restore; chcp -> :utf8_on/:utf8_off; wmic and powershell -> :get_time / :tail (Python helpers).")


def test_utf8_and_standby_calls_are_paired():
    def chk(p):
        t = "\n".join(_lines(p))
        out = []
        if p != COMMON and ":utf8_on" in t and ":utf8_off" not in t:
            out.append((1, ":utf8_on without :utf8_off"))
        if p != COMMON and ":standby_disable" in t and ":standby_restore" not in t:
            out.append((1, ":standby_disable without :standby_restore"))
        return out
    _per_file("paired", chk, "Whatever a launcher changes it must restore before exiting.")


# ─── §9 single instance for the top-level cyclers ────────────────────────────────────────────────

@pytest.mark.parametrize("name", TOP_LEVEL_LOCKED)
def test_top_level_cyclers_hold_the_single_instance_lock(name):
    text = "\n".join(_lines(LAUNCH / name))
    assert 'call :cycle 9>"%LOCK_FILE%"' in text and 'set "LOCK_FILE=%STATE_DIR%\\fondos_cycle.lock"' in text, \
        f"{name} must run :cycle under the shared lock handle (NORMAS_BATCH.md section 9)"
    assert 'set "LOCK_HELD=1"' in text and "%RC_BUSY%" in text
    assert 'if defined FONDOS_CYCLE_LOCK (call :cycle) else (call :cycle 9>"%LOCK_FILE%")' in text and 'set "FONDOS_CYCLE_LOCK=1"' in text, (
        f"{name}: a launcher nested under another top-level one must not ask for the lock again "
        "(NORMAS_BATCH.md section 9)")


def test_sub_launchers_do_not_take_the_lock():
    """A child would try to open the handle its parent holds and refuse to run."""
    for p in BATS:
        if p.name not in TOP_LEVEL_LOCKED:
            assert "9>" not in "\n".join(ln for _, ln in _code(p)) or p == COMMON, p.name


# ─── the library contract ────────────────────────────────────────────────────────────────────────

def test_library_defines_the_documented_subroutines():
    text = "\n".join(_lines(COMMON))
    for label in (":init", ":get_time", ":tail", ":is_uint", ":utf8_on", ":utf8_off", ":standby_disable",
                  ":standby_restore", ":state_query", ":log_open", ":log_close"):
        assert re.search(rf"^{re.escape(label)}\s*$", text, re.M), label
    assert "setlocal" not in "\n".join(ln for _, ln in _code(COMMON)).lower(), \
        "library subroutines must not call setlocal (their variables are the contract)"


def test_exit_code_constants_match_the_python_helper():
    """One set of numbers: lib\\common.bat RC_* and scripts/launch/p1p2_state.py."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("p1p2_state", LAUNCH / "p1p2_state.py")
    st = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(st)
    rc = dict(re.findall(r'^set "(RC_\w+)=(\d+)"', "\n".join(_lines(COMMON)), re.M))
    assert rc == {"RC_USAGE": "100", "RC_ENV": "101", "RC_REFUSED": "102", "RC_STATE": "103",
                  "RC_PREFLIGHT": "104", "RC_BUSY": "105"}, rc
    assert (st.RC_BAD_ARGS, st.RC_REFUSED, st.RC_NOT_WRITABLE, st.RC_PREFLIGHT_FAILED) == (
        int(rc["RC_USAGE"]), int(rc["RC_REFUSED"]), int(rc["RC_STATE"]), int(rc["RC_PREFLIGHT"]))
    assert all(100 <= int(v) <= 199 for v in rc.values())


def test_the_template_documents_a_working_option_table():
    t = "\n".join(_lines(LAUNCH / "_template.bat"))
    assert 'set "BOOL_OPTS=' in t and 'set "UINT_OPTS=' in t and ":usage_text" in t


# ─── portability traps found while testing the launchers (NORMAS_BATCH.md section 11) ─────────────

def test_no_stray_control_characters():
    """A `\f` typed inside a path silently becomes a form feed when a script writes the file."""
    def chk(p):
        bad = sorted({hex(c) for c in p.read_bytes() if c < 32 and c not in (9, 10, 13)})
        return [(0, f"control characters {bad}")] if bad else []
    _per_file("control_chars", chk, "Re-type the line; check with `grep -P '[\x00-\x08\x0b\x0c\x0e-\x1f]'`.")


def test_find_is_windows_find_by_explicit_path():
    """From Git Bash `find` resolves to GNU find (it walks the whole drive: the launcher hangs at 100% CPU)."""
    pat = re.compile(r"(^|[|&^\s(])find(\.exe)?\s+/", re.I)

    def chk(p):
        return [(n, "bare `find`: use \"%WINFIND%\" (set by :init)") for n, ln in _code(p) if pat.search(ln)]
    _per_file("find", chk, 'Use "%WINFIND%" /c /v "" (PATH order must not decide which find runs).')


def test_the_cycle_options_have_a_single_source_shared_by_both_cyclers():
    opts = "\n".join(_lines(LIB / "p1p2_options.bat"))
    assert 'set "BOOL_OPTS=' in opts and 'set "UINT_OPTS=' in opts
    for name in ("P1_P2_Complete.bat", "P1_P2_P3.bat"):
        text = "\n".join(_lines(LAUNCH / name))
        assert 'call "%LAUNCH%\\lib\\p1p2_options.bat"' in text, name
        assert 'set "BOOL_OPTS=' not in text and 'set "UINT_OPTS=' not in text, f"{name} must not redefine the shared table"


def test_the_standby_is_restored_only_by_the_launcher_that_disabled_it():
    """`restore` takes no owner argument: STANDBY_OWNED (reset by every :standby_disable) decides."""
    for p in BATS:
        if p != COMMON:
            assert ":standby_restore owner" not in "\n".join(_lines(p)), p.name
