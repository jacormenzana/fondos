#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
preflight_check.py -- READ-ONLY pre-flight before acting on a stored plan, a summary or a long-idle session.

Several Claude sessions work on this repository at once (2026-10-11: one built the FX constraint while another shipped a better promotion
gate, promoted, and started a 5 h recompute). The five checks below take seconds and say whether the plan in your head is still true:

  0. driver    psycopg imports or not, Smart App Control state, CodeIntegrity 3077 blocks, the active interpreter
  1. git       dirty files (another session's work in flight), commits ahead/behind origin, worktrees, stashes
  2. processes pipeline / test / audit processes running, the launcher cycle lock
  3. database  PRODUCTION scenario, newest persisted audit, metrics algorithm_version vs CALC_VERSION, last P2 run (read-only, fondos_app)
  4. memory    topic files touched recently (a file by another session = shared state moved)
  5. backlog   tickets opened since --since-hours

    python scripts/audit/preflight_check.py                 # report; exit 0
    python scripts/audit/preflight_check.py --strict        # exit 1 when the verdict is CAUTION (use in a launcher / a hook)
    python scripts/audit/preflight_check.py --json          # machine-readable

Never writes anything, never fetches (origin figures are as of the last fetch), never prints a DSN. A section that cannot be read
(no DSN, DB down, no PowerShell) says so and the rest still runs: absence of evidence is reported, not hidden.
"""
from __future__ import annotations

import argparse
import json
import ntpath
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# untracked directories that are known to belong to no current change set: reported, never a reason for caution
KNOWN_UNOWNED_PREFIXES = ("db/oper/",)
# command-line fragments of the processes whose presence means "someone is running a long job"
PROCESS_PATTERNS = (
    "run_pipeline", "P2_calculate", "P2_discover", "P1_P2", "P2_P3", "runCycle", "P3_build", "P3_generate", "p3_build_portfolio",
    "p3_pit_backtest", "run_statistical_audit", "nav_discovery", "macro_discovery", "run_block", "run_pg_tests", "pytest",
    "migrate_",
)
LOCK_FILE = ROOT / "proyecto1" / "log" / "fondos_cycle.lock"
# the interpreter every repo command is meant to run under. Two conda envs named `des` exist on the owner's machine
# (C:\data\envs\des and C:\Users\jacor\.conda\envs\des, so `conda ... -n des` targets the WRONG one): a mismatch is
# reported, never fatal -- Linux CI, the hermetic PG suite and any other host must keep working. FONDOS_PYTHON overrides.
DEFAULT_INTERPRETER = r"C:\data\envs\des\python.exe"
CI_LOOKBACK_HOURS = 6.0                 # how far back the CodeIntegrity 3077 query looks (bounded: the check stays fast)


# ---------------------------------------------------------------- pure helpers (unit-tested)

def parse_porcelain(text: str) -> list:
    """[(xy, path)] from `git status --porcelain` output; a rename keeps the NEW path."""
    out = []
    for line in text.splitlines():
        if len(line) < 4:
            continue
        xy, path = line[:2], line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip().strip('"')
        out.append((xy, path.replace("\\", "/")))
    return out


def split_dirty(entries: list, unowned_prefixes=KNOWN_UNOWNED_PREFIXES, mine=()) -> dict:
    """{'tracked': [modified/staged], 'untracked': [new files], 'unowned': [under a known-unowned prefix], 'mine': [declared by the caller]}.

    `mine` = paths (or prefixes) the calling session itself changed: the checker cannot tell them from another session's work, so the
    caller declares them and they stop counting as somebody else's in-flight change."""
    tracked, untracked, unowned, own = [], [], [], []
    mine = tuple(m.replace("\\", "/") for m in mine)
    for xy, path in entries:
        if any(path.startswith(p) for p in unowned_prefixes):
            unowned.append(path)
        elif any(path == m or path.startswith(m) for m in mine):
            own.append(path)
        elif xy == "??":
            untracked.append(path)
        else:
            tracked.append(path)
    return {"tracked": tracked, "untracked": untracked, "unowned": unowned, "mine": own}


def parse_left_right(text: str) -> Optional[tuple]:
    """(behind, ahead) from `git rev-list --left-right --count origin/master...HEAD`, None if unreadable."""
    m = re.match(r"^\s*(\d+)\s+(\d+)\s*$", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def match_processes(rows: list, patterns=PROCESS_PATTERNS, own_pid: Optional[int] = None) -> list:
    """[(pid, command)] whose command line contains a pattern (case-insensitive); never the checker itself."""
    out = []
    for pid, cmd in rows:
        if own_pid is not None and pid == own_pid:
            continue
        low = (cmd or "").lower()
        if "preflight_check" in low:
            continue
        if any(p.lower() in low for p in patterns):
            out.append((pid, cmd))
    return out


def code_calc_version(source: str) -> Optional[str]:
    m = re.search(r'^CALC_VERSION\s*(?::\s*str\s*)?=\s*"([^"]+)"', source, re.MULTILINE)
    return m.group(1) if m else None


def parse_driver_probe(text: str) -> tuple:
    """(sac_state, [(when, file)]) from the one-shot Windows probe, which prints `SAC<TAB>state` and one
    `EV<TAB>when<TAB>path` per CodeIntegrity 3077 event. Only the file's base name is kept (the full device path adds
    nothing); anything unparsable is ignored, because a probe that half-worked must not crash the checker."""
    sac, events = None, []
    for line in (text or "").splitlines():
        parts = line.rstrip("\r\n").split("\t")
        if parts[0] == "SAC" and len(parts) >= 2:
            sac = parts[1].strip() or None
        elif parts[0] == "EV" and len(parts) >= 3:
            when, path = parts[1].strip(), parts[2].strip()
            if when and path:
                events.append((when, path.replace("\\", "/").rsplit("/", 1)[-1]))
    return sac, events


def expected_interpreter() -> str:
    return os.environ.get("FONDOS_PYTHON") or DEFAULT_INTERPRETER


def same_interpreter(a: str, b: str) -> bool:
    """Windows paths differ in case and in slash direction without being different files.

    ntpath, not os.path: os.path only folds case and backslashes on Windows, so the same comparison was False on Linux (FND-0313).
    """
    return ntpath.normcase(ntpath.normpath(a or "")) == ntpath.normcase(ntpath.normpath(b or ""))


def summarize_driver(import_ok: bool, detail: str, sac: Optional[str], events: list, interpreter: str,
                     expected: Optional[str], lookback_hours: float = CI_LOOKBACK_HOURS) -> dict:
    """The driver section, derived from already-collected facts so it is unit-testable without Windows."""
    files = []
    for _when, name in events:
        if name not in files:
            files.append(name)
    return {
        "import_ok": bool(import_ok),
        "detail": detail or "",
        "sac_state": sac,
        "blocks": len(events),
        "newest_block": max((w for w, _n in events), default=None),
        "blocked_files": files,
        "lookback_hours": lookback_hours,
        "interpreter": interpreter,
        "expected_interpreter": expected,
        "interpreter_ok": same_interpreter(interpreter, expected) if expected else True,
    }


def verdict(report: dict, audit_max_age_days: float = 3.0) -> dict:
    """{'level': 'CLEAR'|'CAUTION', 'cautions': [...], 'notes': [...]}.

    CAUTION = something that makes acting on a stored plan unsafe right now (shared files being edited, a long job running, no production
    scenario). NOTES = things to know (commits ahead, mixed metric versions, recent memory, new tickets)."""
    cautions, notes = [], []
    dr = report.get("driver") or {}
    if dr:
        look = dr.get("lookback_hours", CI_LOOKBACK_HOURS)
        if not dr.get("import_ok"):
            if dr.get("blocks"):
                cautions.append(f"the PostgreSQL driver does not load while Application Control blocked {dr['blocks']} file(s) in the last "
                                f"{look:g} h (newest {dr.get('newest_block')}): nothing DB-backed can run, but this is most likely a "
                                f"transient reputation window -- re-check in a few minutes before escalating to the owner")
            else:
                cautions.append(f"the PostgreSQL driver does not load ({dr.get('detail', '')[:80]}) and no recent Application Control block "
                                f"explains it: nothing DB-backed can run (RC 106)")
        elif dr.get("blocks"):
            notes.append(f"the driver loads now, but Application Control blocked {dr['blocks']} file(s) in the last {look:g} h "
                         f"({', '.join(dr.get('blocked_files', [])[:3])}): a run started inside such a window fails with RC 106")
        if not dr.get("interpreter_ok"):
            notes.append(f"running under {dr.get('interpreter')}, not the expected {dr.get('expected_interpreter')}: two conda envs are "
                         f"named `des` on the owner's machine, so address the env by explicit prefix (`-p <env dir>`), never `-n des`")
    g = report.get("git", {})
    d = g.get("dirty", {})
    if d.get("tracked") or d.get("untracked"):
        n = len(d.get("tracked", [])) + len(d.get("untracked", []))
        cautions.append(f"{n} modified/new file(s) in the working tree that are not a known-unowned path: another session's work in flight "
                        f"-- do not edit, stage, test over or revert them")
    if g.get("ahead"):
        notes.append(f"{g['ahead']} local commit(s) not on origin (as of the last fetch): who pushes them?")
    if g.get("behind"):
        notes.append(f"{g['behind']} commit(s) on origin not in HEAD: fetch/rebase decision before committing")
    if g.get("stashes"):
        notes.append(f"{g['stashes']} git stash entr(y/ies)")
    if len(g.get("worktrees", [])) > 1:
        notes.append(f"{len(g['worktrees'])} git worktrees registered")
    p = report.get("processes", {})
    if p.get("running"):
        cautions.append(f"{len(p['running'])} pipeline/test/audit process(es) running: a long job is in flight -- no P2/P3 run, full suite "
                        f"or launcher until it ends")
    if p.get("lock_held"):
        cautions.append("launcher cycle lock present (proyecto1/log/fondos_cycle.lock)")
    db = report.get("db", {})
    if not db.get("error"):
        if db.get("lifecycle_installed") and not db.get("production"):
            cautions.append("lifecycle installed but no PRODUCTION scenario")
        if db.get("p2_rows_last_10min"):
            cautions.append(f"p2_pipeline_log wrote {db['p2_rows_last_10min']} row(s) in the last 10 minutes: a P2 run is in progress "
                            f"(possibly from another machine or WSL, invisible to the process table)")
        age = db.get("audit_age_days")
        if age is not None and age > audit_max_age_days:
            notes.append(f"newest persisted audit is {age:.1f} days old (> {audit_max_age_days:g}): the promotion gate will refuse as stale")
        dom, code = db.get("dominant_version"), report.get("calc_version")
        if dom and code and dom != code:
            notes.append(f"stored metrics are mostly on {dom} but the code is CALC_VERSION {code}: a recompute is pending or running")
    elif db.get("error") != "skipped":
        notes.append(f"database section not read: {db['error']}")
    m = report.get("memory", {})
    if m.get("recent"):
        notes.append(f"{len(m['recent'])} memory file(s) touched in the last {m.get('recent_hours')} h: read them before acting")
    b = report.get("backlog", {})
    if b.get("new"):
        notes.append(f"{len(b['new'])} ticket(s) opened in the last {b.get('since_hours')} h: scan them before creating similar ones")
    return {"level": "CAUTION" if cautions else "CLEAR", "cautions": cautions, "notes": notes}


# ---------------------------------------------------------------- readers (side-effect free)

def _run(cmd: list, timeout: int = 20) -> Optional[str]:
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def read_driver(lookback_hours: float = CI_LOOKBACK_HOURS) -> dict:
    """Does the DB driver load in THIS interpreter, is Application Control enforcing, and did it block anything recently.

    The repo's own guard (`shared/env_guard.check_driver`) answers the first question, so the checker and the launchers
    agree by construction. The Windows-only part is one PowerShell call: it is the right place for event-log reading
    (`env_guard` is stdlib-only and must import in an interpreter whose driver is broken). Anything unavailable -- another
    OS, no PowerShell, an unreadable log -- degrades to "unknown" and the rest of the section still reports."""
    try:
        from shared import env_guard
        ok, detail = env_guard.check_driver()
    except Exception as exc:                                   # noqa: BLE001 -- report, do not crash the checker
        ok, detail = False, f"{type(exc).__name__}: {str(exc)[:120]}"
    sac, events = None, []
    if sys.platform == "win32":
        ps = ("$ErrorActionPreference='SilentlyContinue';"
              "$p=(Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\CI\\Policy')."
              "VerifiedAndReputablePolicyState; \"SAC`t$p\";"
              f"Get-WinEvent -FilterHashtable @{{LogName='Microsoft-Windows-CodeIntegrity/Operational';Id=3077;"
              f"StartTime=(Get-Date).AddHours(-{lookback_hours})}} -MaxEvents 50 | ForEach-Object {{"
              "$f='?'; if ($_.Message -match 'attempted to load (\\S+)') { $f=$matches[1] };"
              "\"EV`t$($_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss'))`t$f\" }")
        sac, events = parse_driver_probe(_run(["powershell", "-NoProfile", "-Command", ps], timeout=25) or "")
    return summarize_driver(ok, detail, sac, events, sys.executable, expected_interpreter(), lookback_hours)


def read_git(mine=()) -> dict:
    status = _run(["git", "status", "--porcelain", "--untracked-files=all"])
    if status is None:
        return {"error": "git not available / not a repository"}
    lr = parse_left_right(_run(["git", "rev-list", "--left-right", "--count", "origin/master...HEAD"]) or "")
    return {
        "branch": (_run(["git", "branch", "--show-current"]) or "").strip(),
        "head": (_run(["git", "log", "--oneline", "-3"]) or "").strip().splitlines(),
        "behind": lr[0] if lr else None, "ahead": lr[1] if lr else None,
        "dirty": split_dirty(parse_porcelain(status), mine=mine),
        "stashes": len((_run(["git", "stash", "list"]) or "").strip().splitlines()),
        "worktrees": [l for l in (_run(["git", "worktree", "list"]) or "").strip().splitlines() if l],
    }


def list_processes() -> Optional[list]:
    """[(pid, command line)] of python/cmd processes (Windows PowerShell CIM); None when it cannot be read."""
    script = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='cmd.exe' or Name='py.exe'\" | "
              "ForEach-Object { '{0}|{1}' -f $_.ProcessId, $_.CommandLine }")
    out = _run(["powershell.exe", "-NoProfile", "-Command", script], timeout=30)
    if out is None:
        return None
    rows = []
    for line in out.splitlines():
        pid, _, cmd = line.partition("|")
        if pid.strip().isdigit():
            rows.append((int(pid), cmd.strip()))
    return rows


def cycle_lock_held() -> bool:
    """True when ANOTHER process holds the launcher's cycle lock: the repo's own predicate (the file merely existing means nothing; the
    launcher holds it open with write sharing denied, so opening it for append fails while held)."""
    try:
        sys.path.insert(0, str(ROOT / "scripts" / "ops"))
        from migrate_fund_metric_state_ols_calc_version import lock_file_held
        return bool(lock_file_held(LOCK_FILE))
    except Exception:                                          # noqa: BLE001 -- predicate unavailable: same test, locally
        if not LOCK_FILE.exists():
            return False
        try:
            with open(LOCK_FILE, "a"):
                return False
        except OSError:
            return True


def read_processes() -> dict:
    rows = list_processes()
    return {
        "running": None if rows is None else [{"pid": p, "cmd": c[:160]} for p, c in match_processes(rows, own_pid=os.getpid())],
        "unreadable": rows is None,
        "lock_held": cycle_lock_held(),
    }


def read_db() -> dict:
    if not os.environ.get("FONDOS_PG_DSN") and not (ROOT / ".env").exists():
        return {"error": "skipped"}
    try:
        import shared.config  # noqa: F401  (autoloads the repo .env)
        from shared.db import get_connection
        conn = get_connection()
    except Exception as exc:                                   # noqa: BLE001 -- report, do not crash the checker
        return {"error": f"cannot connect: {type(exc).__name__}"}
    out: dict = {}
    try:
        cols = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'portfolio_scenarios'").fetchall()}
        out["lifecycle_installed"] = "status" in cols
        if out["lifecycle_installed"]:
            out["production"] = [r[0] for r in conn.execute(
                "SELECT scenario_id FROM portfolio_scenarios WHERE status = 'PRODUCTION'").fetchall()]
            out["drafts"] = int(conn.execute("SELECT COUNT(*) FROM portfolio_scenarios WHERE status = 'DRAFT'").fetchone()[0])
        row = conn.execute("SELECT run_id, MAX(detected_at) FROM audit_finding WHERE domain = 'p2_metrics' GROUP BY run_id "
                           "ORDER BY MAX(detected_at) DESC LIMIT 1").fetchone()
        if row:
            out["audit_run"] = row[0]
            ts = row[1]
            out["audit_at"] = str(ts)
            if ts is not None:
                now = datetime.now(ts.tzinfo) if getattr(ts, "tzinfo", None) else datetime.now()
                out["audit_age_days"] = (now - ts).total_seconds() / 86400.0
        vers = conn.execute("SELECT algorithm_version, COUNT(DISTINCT isin) FROM fund_metrics GROUP BY 1 ORDER BY 2 DESC LIMIT 4").fetchall()
        out["versions"] = [(r[0], int(r[1])) for r in vers]
        out["dominant_version"] = vers[0][0] if vers else None
        last = conn.execute("SELECT step, status, created_at FROM p2_pipeline_log ORDER BY id DESC LIMIT 1").fetchone()
        out["last_p2_log"] = [str(x) for x in last] if last else None
        # a P2 run in progress logs continuously (same signal the owner-side migration scripts use): independent of the process table
        out["p2_rows_last_10min"] = int(conn.execute(
            "SELECT count(*) FROM p2_pipeline_log WHERE created_at > now() - make_interval(mins => %s)", (10,)).fetchone()[0])
    except Exception as exc:                                   # noqa: BLE001
        out["error"] = f"query failed: {type(exc).__name__}: {str(exc)[:80]}"
    finally:
        try:
            conn.rollback()
            conn.close()
        except Exception:                                      # noqa: BLE001
            pass
    return out


def memory_dir(explicit: Optional[str] = None) -> Optional[Path]:
    if explicit:
        return Path(explicit)
    base = Path.home() / ".claude" / "projects"
    if not base.is_dir():
        return None
    want = re.sub(r"[^A-Za-z0-9]", "-", str(ROOT)).lower()
    for d in base.iterdir():
        if d.name.lower() == want and (d / "memory").is_dir():
            return d / "memory"
    return None


def read_memory(explicit: Optional[str], recent_hours: float) -> dict:
    d = memory_dir(explicit)
    if d is None:
        return {"error": "memory directory not found", "recent": [], "recent_hours": recent_hours}
    files = sorted((f for f in d.glob("*.md") if f.name != "MEMORY.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    cutoff = datetime.now().timestamp() - recent_hours * 3600
    rows = []
    for f in files[:12]:
        head = f.read_text(encoding="utf-8", errors="replace")[:800]
        m = re.search(r"originSessionId:\s*(\S+)", head)
        rows.append({"file": f.name, "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                     "session": (m.group(1)[:8] if m else "?"), "recent": f.stat().st_mtime >= cutoff})
    return {"dir": str(d), "newest": rows[:6], "recent": [r["file"] for r in rows if r["recent"]], "recent_hours": recent_hours}


def gate_line(g: dict) -> str:
    """One line on the Phase B delivery gate (FND-0333) so that an AUDIT window that is clear, or one that has stalled, is seen at every pre-flight and not forgotten. Pure."""
    if not g:
        return ""
    hours = "-" if g.get("hours_in_audit") is None else f"{float(g['hours_in_audit']):.1f}"
    line = (f"delivery gate: mode {g.get('mode')}, {hours} h in audit, {g.get('tickets_48h')} of {g.get('required_tickets')} different tickets moved in the last 48 h, "
            f"{g.get('violations_48h')} violation(s) in them")
    if g.get("mode") == "AUDIT":
        line += " -> ENFORCE CAN BE AUTHORIZED (window clear)" if g.get("clear") else " -> window not clear yet"
    if g.get("last_override_ts") is not None:
        line += f" [executive override recorded {str(g['last_override_ts'])[:16]}]"
    return line


def _like(pattern: str, actor: str) -> bool:
    return re.fullmatch(re.escape(pattern).replace("%", ".*"), actor) is not None


def iam_line(cat: dict) -> str:
    """One line on the IAM step (FND-0354): not applied, or how many logins are bound to how many PERSONS (several logins of one person count once), and which logins can still open or
    move a ticket without being bound. Pure."""
    if not cat or not (cat.get("routines") or {}).get("sp_append_log"):
        return "IAM: step iam not applied (no per-identity logins; agents write through the shared writer or not at all)" if cat is not None else ""
    persons = cat.get("active_persons") or []
    line = f"IAM: {cat.get('active_logins', 0)} bound login(s), {len(persons)} distinct human person(s)"
    if "transition_persons" in cat:
        line += f", transitions made by {len(cat['transition_persons'])} of them"
    if cat.get("unbound_writers"):
        line += "; UNBOUND logins that can still open or move a ticket: " + ", ".join(cat["unbound_writers"])
    elif cat.get("writers"):
        line += "; every writing login is bound"
    return line


def a2_line(sp: dict, actors: list, required: int = 50, bindings: "list | None" = None) -> str:
    """One line on the A-2 gate (FND-0331): non-system transitions counted since the last direct write (V-00), who made them, and a warning when ONE actor did them all (the owner asks
    for several actors before the revoke). Pure."""
    if not sp:
        return ""
    n = int(sp.get("live_non_system_since_last_v00") or 0)
    line = f"A-2 gate: {n} of {required} real transitions since the last V-00 (V-00 total {sp.get('v00_total', 0)})"
    if actors:
        if bindings:                                           # IAM applied: say which of these identities no bound login backs (a claim, not a proof)
            patterns = [b["actor"] for b in bindings if b.get("active")]
            actors = [(a + ("" if any(_like(p, a) for p in patterns) else " [UNBOUND]"), c) for a, c in actors]
        line += "; by " + ", ".join(f"{a} x{c}" for a, c in actors[:4]) + f" ({len(actors)} distinct actor(s))"
        if len(actors) < 3:
            line += " -> NEEDS MORE ACTORS before the revoke"
    return line


def read_backlog(since_hours: float) -> dict:
    if not os.environ.get("FONDOS_BACKLOG_PG_DSN") and not (ROOT / ".env").exists():
        return {"error": "skipped", "new": [], "since_hours": since_hours}
    try:
        import shared.config  # noqa: F401
        from shared import backlog_client as bc
        tickets = bc.list_tickets()
    except Exception as exc:                                   # noqa: BLE001
        return {"error": f"backlog not read: {type(exc).__name__}", "new": [], "since_hours": since_hours}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    new = []
    for t in tickets:
        ts = t.get("creation_ts")
        if ts is not None:
            ts = ts if getattr(ts, "tzinfo", None) else ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                new.append({"id": t["action_point_id"], "status": t["status"], "title": str(t.get("title"))[:80]})
    gate = None
    try:                                                       # Phase B may not be applied: then there is simply no gate line
        gate = bc.gate_audit_window() if bc.relations_store_present() else None
    except Exception:                                          # noqa: BLE001
        gate = None
    a2, actors = None, []
    try:                                                       # step A-1 may not be applied: then there is no A-2 line
        a2, actors = bc.shadow_progress(), bc.shadow_actors()
    except Exception:                                          # noqa: BLE001
        a2, actors = None, []
    iam = None
    try:                                                       # step iam may not be applied: then the line says so
        iam = bc.iam_catalog()
    except Exception:                                          # noqa: BLE001
        iam = None
    return {"open": sum(1 for t in tickets if t.get("status") != "CLOSED"), "new": new, "since_hours": since_hours, "gate": gate,
            "a2": a2, "a2_actors": actors, "a2_required": getattr(bc, "SHADOW_REQUIRED", 50), "iam": iam}


# ---------------------------------------------------------------- report

def build_report(args) -> dict:
    mine = tuple(args.mine or ())
    src = (ROOT / "proyecto2" / "src" / "pipeline" / "run_pipeline.py")
    report = {
        "driver": read_driver(),
        "git": read_git(mine),
        "processes": read_processes(),
        "db": {"error": "skipped"} if args.no_db else read_db(),
        "memory": read_memory(args.memory_dir, args.recent_hours),
        "backlog": {"error": "skipped", "new": [], "since_hours": args.since_hours} if args.no_db else read_backlog(args.since_hours),
        "calc_version": code_calc_version(src.read_text(encoding="utf-8", errors="replace")) if src.exists() else None,
    }
    report["verdict"] = verdict(report, args.audit_max_age_days)
    return report


def render(r: dict) -> str:
    L = ["=== PRE-FLIGHT (read-only) ==="]
    dr = r.get("driver") or {}
    if dr:
        L.append(f"[0] driver: import {'OK' if dr.get('import_ok') else 'BLOCKED (' + dr.get('detail', '')[:60] + ')'}"
                 f" | SAC={dr.get('sac_state', '?')} | {dr.get('blocks', 0)} block(s)/{dr.get('lookback_hours', 0):g}h"
                 f" (newest {dr.get('newest_block')}) | interpreter {'OK' if dr.get('interpreter_ok') else 'MISMATCH'}")
        for f in dr.get("blocked_files", [])[:4]:
            L.append(f"      blocked: {f}")
        if not dr.get("interpreter_ok"):
            L.append(f"      running under {dr.get('interpreter')}, expected {dr.get('expected_interpreter')}")
    g = r["git"]
    dirty = g.get("dirty", {})
    L.append("[1] git: " + (g.get("error") or
                            f"{g.get('branch', '?')} | ahead {g.get('ahead')} / behind {g.get('behind')} of origin/master (last fetch) | "
                            f"{len(dirty.get('tracked', []))} modified, {len(dirty.get('untracked', []))} new, "
                            f"{len(dirty.get('unowned', []))} known-unowned, {len(dirty.get('mine', []))} mine | {g.get('stashes', 0)} stash"))
    for p in (dirty.get("tracked", []) + dirty.get("untracked", []))[:12]:
        L.append(f"      dirty: {p}")
    for h in g.get("head", [])[:3]:
        L.append(f"      {h[:110]}")
    p = r["processes"]
    running = p.get("running") or []
    L.append("[2] processes: " + ("unreadable" if p.get("unreadable") else f"{len(running)} pipeline/test/audit running")
             + f" | cycle lock {'HELD' if p.get('lock_held') else 'free'}")
    for x in running[:6]:
        L.append(f"      pid {x['pid']}: {x['cmd'][:110]}")
    d = r["db"]
    if d.get("error"):
        L.append(f"[3] database: not read ({d['error']})")
    else:
        L.append(f"[3] database: PRODUCTION={d.get('production')} drafts={d.get('drafts')} | audit {d.get('audit_run')} "
                 f"({(d.get('audit_age_days') if d.get('audit_age_days') is not None else float('nan')):.1f} d) | metrics {d.get('versions')} vs code CALC_VERSION {r.get('calc_version')}")
        L.append(f"      last p2_pipeline_log: {d.get('last_p2_log')}")
    m = r["memory"]
    L.append("[4] memory: " + (m.get("error") or f"{len(m.get('recent', []))} file(s) touched in the last {m.get('recent_hours', 0):g} h"))
    for row in m.get("newest", [])[:4]:
        L.append(f"      {row['mtime']} [{row['session']}] {row['file']}")
    b = r["backlog"]
    L.append("[5] backlog: " + (b.get("error") or f"{b.get('open', '?')} open; {len(b.get('new', []))} opened in the last {b.get('since_hours', 0):g} h"))
    for t in b.get("new", [])[:6]:
        L.append(f"      {t['id']} [{t['status']}] {t['title']}")
    if gate_line(b.get("gate") or {}):
        L.append("      " + gate_line(b["gate"]))
    bindings = (b.get("iam") or {}).get("bindings") or None
    if a2_line(b.get("a2") or {}, b.get("a2_actors") or [], b.get("a2_required", 50), bindings):
        L.append("      " + a2_line(b["a2"], b.get("a2_actors") or [], b.get("a2_required", 50), bindings))
    if iam_line(b.get("iam")):
        L.append("      " + iam_line(b["iam"]))
    v = r["verdict"]
    L.append(f"VERDICT: {v['level']}")
    L += [f"  CAUTION: {c}" for c in v["cautions"]] + [f"  note: {n}" for n in v["notes"]]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read-only pre-flight before acting on a stored plan")
    ap.add_argument("--strict", action="store_true", help="exit 1 when the verdict is CAUTION")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-db", action="store_true", help="skip the database and backlog sections")
    ap.add_argument("--mine", nargs="*", default=[], metavar="PATH",
                    help="paths (or prefixes) THIS session changed; they are listed apart and do not raise the caution")
    ap.add_argument("--memory-dir", default=None)
    ap.add_argument("--recent-hours", type=float, default=6.0)
    ap.add_argument("--since-hours", type=float, default=24.0)
    ap.add_argument("--audit-max-age-days", type=float, default=3.0)
    args = ap.parse_args(argv)
    report = build_report(args)
    print(json.dumps(report, indent=2, default=str) if args.json else render(report))
    return 1 if (args.strict and report["verdict"]["level"] == "CAUTION") else 0


if __name__ == "__main__":
    sys.exit(main())
