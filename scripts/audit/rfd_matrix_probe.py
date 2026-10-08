#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
rfd_matrix_probe.py -- evidence matrix for the READY_FOR_DEPLOY tickets of gestion.backlog (read-only). Nothing is closed by this script.

A READY_FOR_DEPLOY ticket means "developed, not deployed". It may be closed only when what the ticket says must be true in PRODUCTION is true. Closing them
in bulk would hide the ones still waiting for an owner action (a flag still off, a recompute not run, a Windows task not checked). This script states,
per ticket, the predicates that decide it and evaluates the ones a machine can evaluate:

    pushed(sha)            the commit is on origin/master            flag(NAME, True)     shared.config switch has the value the ticket needs
    absent_re_in(file, rx) no code reference matching rx (a quoted metric name, not a docstring mention)
    calc_share(min)        share of fund_metrics rows already on the current CALC_VERSION (i.e. the recompute ran)
    metric_absent(metric)  no row of a retired metric             absent_in(file, text) the source no longer contains the text
    ols_ran()              a P2 RUN_SUMMARY with ols_funds > 0 exists after the CALC_VERSION bump
    ticket_closed(id)      another ticket this one closes with        task_ok(name)  last result 0 of a Windows scheduled task
    manual(text)           an owner action no script can see

Verdict per ticket: READY (every probe passes: closing is a decision with evidence), BLOCKED (a probe fails: a named precondition is missing),
MANUAL (machine probes pass but an owner action remains). The workflow is in doc/operativos/BACKLOG_RFD_VERIFICATION_MATRIX.md.

    python scripts/audit/rfd_matrix_probe.py            # prints the matrix; exit 0
    python scripts/audit/rfd_matrix_probe.py --md FILE  # also writes it as markdown
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ticket -> list of probes: (kind, *args). The text of each ticket's closing condition is in its backlog log; this table is its executable form.
SPEC = {
    "FND-0073": [("task_ok", "Fondos PG basebackup+prune"), ("task_ok", "Fondos PG offbox copy"),
                 ("manual", "DBeaver -> :5436 as fondos_ro and Metabase -> superset_ro repointed; delete the DBeaver connection to the retired sqlite path")],
    "FND-0181": [("pushed", "af3a3db"), ("metric_absent", "beta_m2_global")],
    "FND-0196": [("pushed", "8507fad"), ("flag", "MACRO_VIF_ITERATIVE_ENABLED", True), ("calc_share", 0.95), ("ols_ran",)],
    "FND-0200": [("pushed", "8507fad"), ("flag", "PERSISTENCE_FIRST_LAST_NAV_ENABLED", True), ("calc_share", 0.95)],
    "FND-0201": [("pushed", "7793f0d"), ("absent_in", "proyecto2/src/readers/db_readers.py", "data_status != 'INACTIVE'")],
    "FND-0202": [("pushed", "8507fad"), ("flag", "CAPTURE_MONTH_END_ENABLED", True), ("calc_share", 0.95)],
    "FND-0203": [("ticket_closed", "FND-0236")],
    "FND-0225": [("pushed", "b38125d"), ("absent_re_in", "proyecto3/src/fund_scorer.py", r"""["']beta_(oil|rate_eu|spread_hy|vix)["']""")],
    "FND-0226": [("pushed", "8507fad"), ("flag", "MACRO_FACTOR_CLEAN_ENABLED", True), ("calc_share", 0.95)],
    "FND-0229": [("pushed", "d7202ec"), ("ols_ran",), ("calc_share", 0.95)],
    "FND-0235": [("pushed", "c0312a7"), ("flag", "FX_CONTRIBUTION_EUR_VIEW_ENABLED", True),
                 ("manual", "ships in the SAME release as FND-0243 (owner directive 2026-10-07)")],
    "FND-0240": [("pushed", "ed6df6c"), ("flag", "ANNUALIZATION_INTERVAL_ENABLED", True), ("manual", "risk + rolling recompute done and the audit clean afterwards")],
    "FND-0241": [("pushed", "929ceed"), ("flag", "DEFLATION_MONTH_ALIGN_ENABLED", True), ("manual", "risk + rolling recompute done and the audit clean afterwards")],
    "FND-0242": [("pushed", "d304ba6"), ("flag", "SORTINO_MIN_DOWNSIDE_COUNT_ENABLED", True), ("manual", "risk + rolling recompute done and the audit clean afterwards")],
}
PROBE_KINDS = {"pushed", "flag", "calc_share", "metric_absent", "absent_in", "absent_re_in", "ols_ran", "ticket_closed", "task_ok", "manual"}


# ── pure ──────────────────────────────────────────────────────────────────────────────────────────

def verdict(results: list) -> str:
    """results = [(ok, detail)] with ok True / False / None (None = manual). BLOCKED beats MANUAL beats READY."""
    if any(ok is False for ok, _ in results):
        return "BLOCKED"
    if any(ok is None for ok, _ in results):
        return "MANUAL"
    return "READY"


def render_markdown(rows: list) -> str:
    out = ["| Ticket | Verdict | Evidence |", "|---|---|---|"]
    for tid, v, results in rows:
        ev = "<br>".join(f"{'OK' if ok else ('MANUAL' if ok is None else 'FAIL')}: {d}" for ok, d in results)
        out.append(f"| {tid} | **{v}** | {ev} |")
    return "\n".join(out)


def spec_problems(spec: dict = SPEC) -> list:
    return [f"{t}: unknown probe {p[0]!r}" for t, probes in spec.items() for p in probes if p[0] not in PROBE_KINDS]


# ── probes (read-only) ────────────────────────────────────────────────────────────────────────────

def _run(cmd: list) -> tuple:
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=60)
    return r.returncode, r.stdout.strip()


def probe_pushed(sha: str) -> tuple:
    rc, out = _run(["git", "-C", str(ROOT), "branch", "-r", "--contains", sha])
    return ("origin/master" in out), f"commit {sha} {'is' if 'origin/master' in out else 'is NOT'} on origin/master"


def probe_flag(name: str, want: bool) -> tuple:
    sys.path.insert(0, str(ROOT))
    import shared.config as cfg
    have = getattr(cfg, name, None)
    return (have is want), f"{name} = {have} (needs {want})"


def probe_absent_in(rel: str, text: str) -> tuple:
    src = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    return (text not in src), f"{rel} {'no longer contains' if text not in src else 'STILL contains'} {text!r}"


def probe_absent_re_in(rel: str, pattern: str) -> tuple:
    """Like absent_in, for code references: a quoted metric name as the scorer would READ it, not a mention in a docstring."""
    hit = re.search(pattern, (ROOT / rel).read_text(encoding="utf-8", errors="replace"))
    return (hit is None), f"{rel} {'has no code reference matching' if hit is None else 'STILL references'} {pattern!r}" + ("" if hit is None else f" ({hit.group(0)})")


def probe_task_ok(name: str) -> tuple:
    rc, out = _run(["schtasks", "/query", "/tn", name, "/v", "/fo", "list"])
    if rc != 0:
        return False, f"scheduled task {name!r} not found"
    m = re.search(r"(?:Last Result|Resultado de la .ltima ejecuci.n|.ltimo resultado)\s*:\s*(\-?\d+)", out)
    if not m:
        return None, f"task {name!r}: could not read its last result (locale); check LastTaskResult = 0 by hand"
    return (m.group(1) == "0"), f"task {name!r} last result = {m.group(1)}"


def probe_db(conn, kind: str, *args) -> tuple:
    if kind == "calc_share":
        src = (ROOT / "proyecto2/src/pipeline/run_pipeline.py").read_text(encoding="utf-8")
        calc = re.search(r'CALC_VERSION\s*(?::\s*str\s*)?=\s*["\'](\d+)["\']', src).group(1)
        cur, total = conn.execute("SELECT COUNT(*) FILTER (WHERE algorithm_version = %s), COUNT(*) FROM fund_metrics", (calc,)).fetchone()
        share = cur / total if total else 0.0
        return share >= args[0], f"{share:.1%} of {total:,} fund_metrics rows are on CALC_VERSION {calc} (needs >= {args[0]:.0%})"
    if kind == "metric_absent":
        n = conn.execute("SELECT COUNT(*) FROM fund_metrics WHERE metric = %s", (args[0],)).fetchone()[0]
        return n == 0, f"{n} rows of metric {args[0]}"
    if kind == "ols_ran":
        n = conn.execute("SELECT COUNT(*) FROM p2_pipeline_log WHERE step = 'RUN_SUMMARY' AND message !~ 'ols_funds=0( |$)' AND message ~ 'ols_funds=[1-9]'").fetchone()[0]
        return n > 0, f"{n} P2 RUN_SUMMARY rows with ols_funds > 0"
    raise ValueError(kind)


def probe_ticket_closed(backlog_conn, tid: str) -> tuple:
    row = backlog_conn.execute("SELECT status FROM backlog.backlog WHERE action_point_id = %s", (tid,)).fetchone()
    st = row[0] if row else "NOT_FOUND"
    return st == "CLOSED", f"{tid} is {st}"


def evaluate(spec: dict = SPEC) -> list:
    sys.path.insert(0, str(ROOT))
    from shared.env_guard import require_db_driver
    require_db_driver("rfd_matrix_probe.py")
    import os
    import shared.config  # noqa: F401  (loads .env)
    import psycopg
    from shared.db import get_connection
    conn = get_connection()
    bconn = psycopg.connect(os.environ["FONDOS_BACKLOG_PG_DSN"]) if os.environ.get("FONDOS_BACKLOG_PG_DSN") else None
    rows = []
    for tid, probes in spec.items():
        results = []
        for p in probes:
            kind, args = p[0], p[1:]
            try:
                if kind == "pushed":
                    results.append(probe_pushed(*args))
                elif kind == "flag":
                    results.append(probe_flag(*args))
                elif kind == "absent_in":
                    results.append(probe_absent_in(*args))
                elif kind == "absent_re_in":
                    results.append(probe_absent_re_in(*args))
                elif kind == "task_ok":
                    results.append(probe_task_ok(*args))
                elif kind == "ticket_closed":
                    results.append(probe_ticket_closed(bconn, *args) if bconn else (None, "no FONDOS_BACKLOG_PG_DSN: check by hand"))
                elif kind == "manual":
                    results.append((None, args[0]))
                else:
                    results.append(probe_db(conn, kind, *args))
            except Exception as exc:                       # a probe that cannot run is a failed probe, never a pass
                results.append((False, f"{kind}{args}: probe failed ({type(exc).__name__}: {str(exc)[:100]})"))
        rows.append((tid, verdict(results), results))
    conn.rollback()
    conn.close()
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--md", help="also write the matrix as markdown to this file")
    args = ap.parse_args(argv)
    rows = evaluate()
    md = render_markdown(rows)
    print(md)
    counts = {v: sum(1 for _, x, _ in rows if x == v) for v in ("READY", "MANUAL", "BLOCKED")}
    print(f"\n{counts}  (nothing was closed: closing a ticket is a separate, per-ticket decision)")
    if args.md:
        Path(args.md).write_text(md + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
