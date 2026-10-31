#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backlog_drift.py -- does the LIVE backlog database have everything the DDL of master defines? (Backlog v2, FND-0351 family; NORMAS_BACKLOG.md 9.1). Read-only.

    python scripts/ops/backlog_drift.py                 # builds master's DDL in the throwaway container, reads the live catalog (read-only) and prints the drift
    python scripts/ops/backlog_drift.py --json          # the same as JSON

Why. A phase can be merged to master and never applied (it happened with FND-0350: the code was on master, the live `sp_set_gate` still had its old signature), and nothing in the repository says so. This
applies the phases of scripts/ops/migrate_backlog_v2.py to the hermetic container IN THE ORDER THE LIVE DATABASE RECEIVED THEM, fingerprints what they leave (an md5 of every routine definition and view,
the columns of every relation of the backlog schema) and compares it with the live catalog. For each difference it names the phase whose DDL mentions the object, so the owner knows which command to run
(dry run first). The IAM phase is not applied by design and is not part of the comparison; the phase `revoke` (A-2) changes privileges only, not definitions, so it is not either.
The live database is only READ (pg_catalog queries on a read-only connection); the container is destroyed.

Exit codes: 0 no drift, 1 drift found, 2 usage error or the live backlog could not be read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "ops"))
RC_OK, RC_DRIFT, RC_USAGE = 0, 1, 2
PHASES_IN_ORDER = ("transitions", "human", "history", "relations", "groups", "sync", "hook", "collision", "attest")
LIVE_ONLY_OK = ("vw_backlog_kpis",)               # legacy analytics view of 10_kpis_and_release.sql: live, not part of the phases compared


def fingerprint(conn) -> dict:
    """{'funcs': {name(args): md5 of the definition}, 'cols': {relation: sorted columns}, 'views': {view: md5 of the definition}} of the backlog schema. Works on any connection."""
    funcs = {r[0]: hashlib.md5(r[1].encode()).hexdigest() for r in conn.execute(
        "SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')', pg_get_functiondef(p.oid) FROM pg_proc p "
        "WHERE p.pronamespace = 'backlog'::regnamespace AND p.prokind = 'f' ORDER BY 1").fetchall()}
    cols = {r[0]: r[1] for r in conn.execute(
        "SELECT table_name, string_agg(column_name, ',' ORDER BY column_name) FROM information_schema.columns WHERE table_schema = 'backlog' GROUP BY 1 ORDER BY 1").fetchall()}
    views = {r[0]: hashlib.md5(r[1].encode()).hexdigest() for r in conn.execute(
        "SELECT c.relname, pg_get_viewdef(c.oid) FROM pg_class c WHERE c.relnamespace = 'backlog'::regnamespace AND c.relkind = 'v' ORDER BY 1").fetchall()}
    return {"funcs": funcs, "cols": cols, "views": views}


def diff(live: dict, master: dict, live_only_ok=LIVE_ONLY_OK) -> dict:
    """What master defines that the live database does not (or defines differently). {'missing': [(kind, name)], 'changed': [(kind, name)], 'columns': [(relation, missing live, extra live)], 'live_only': [...]}. Pure."""
    out = {"missing": [], "changed": [], "columns": [], "live_only": []}
    for kind, key in (("routine", "funcs"), ("view", "views")):
        for name in sorted(set(master[key]) - set(live[key])):
            out["missing"].append((kind, name))
        for name in sorted(k for k in set(live[key]) & set(master[key]) if live[key][k] != master[key][k]):
            out["changed"].append((kind, name))
        out["live_only"] += [(kind, n) for n in sorted(set(live[key]) - set(master[key])) if n not in live_only_ok]
    for rel in sorted(set(live["cols"]) & set(master["cols"])):
        a, b = set(live["cols"][rel].split(",")), set(master["cols"][rel].split(","))
        if a != b:
            out["columns"].append((rel, sorted(b - a), sorted(a - b)))
    for rel in sorted(set(master["cols"]) - set(live["cols"])):
        if rel not in master["views"]:                       # a view shows up in the columns too: reported once, as a view
            out["missing"].append(("table", rel))
    return out


def owning_phase(name: str, phases=PHASES_IN_ORDER, blocks=None) -> str:
    """The last phase whose DDL block DEFINES the object (CREATE [OR REPLACE] FUNCTION/VIEW/TABLE, or ALTER TABLE ... ADD COLUMN on it); when none does, the last that merely mentions it. That is the command to re-run.
    '' when nothing matches. `blocks`: {phase: ddl text} (default: read from the files)."""
    base = re.escape(re.split(r"[(\s]", name, maxsplit=1)[0].lower())
    defines = re.compile(rf"(create\s+(or\s+replace\s+)?(function|view|table)\s+(if\s+not\s+exists\s+)?(backlog\.)?{base}\b|alter\s+table\s+(if\s+exists\s+)?(backlog\.)?{base}\b)", re.I)
    mentions = re.compile(rf"\b{base}\b", re.I)
    defined = mentioned = ""
    for ph in phases:
        text = blocks[ph] if blocks is not None else __import__("migrate_backlog_v2").ddl_block(ph)
        if defines.search(text):
            defined = ph
        if mentions.search(text):
            mentioned = ph
    return defined or mentioned


def has_drift(d: dict) -> bool:
    return bool(d["missing"] or d["changed"] or d["columns"])


def report_lines(d: dict) -> list:
    """The human report. Pure apart from the phase lookup."""
    if not has_drift(d):
        return ["no drift: the live backlog has every routine, view and column that master's DDL defines (IAM excluded by design)"]
    lines = ["DRIFT: master's DDL defines things the live backlog does not have (or has differently):"]
    phases = set()
    for kind, name in d["missing"]:
        ph = owning_phase(name)
        phases.add(ph)
        lines.append(f"  missing in live: {kind} {name}  (phase {ph or '?'})")
    for kind, name in d["changed"]:
        ph = owning_phase(name)
        phases.add(ph)
        lines.append(f"  differs in live: {kind} {name}  (phase {ph or '?'})")
    for rel, miss, extra in d["columns"]:
        ph = owning_phase(rel)
        phases.add(ph)
        lines.append(f"  columns of {rel}: missing live {miss or '-'}, extra live {extra or '-'}  (phase {ph or '?'})")
    for ph in sorted(p for p in phases if p):
        lines.append(f"  -> the owner re-applies it: python scripts/ops/migrate_backlog_v2.py --phase {ph}   (dry run first, then --apply; phases are idempotent)")
    if d["live_only"]:
        lines.append(f"  live only (not in master's DDL; informational): {d['live_only']}")
    return lines


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--image", default="postgres:17")
    args = ap.parse_args(argv)
    try:
        import shared.config  # noqa: F401  (loads .env: FONDOS_BACKLOG_PG_DSN)
        from shared import backlog_client as bc
        with bc._session() as c:
            c.execute("SET TRANSACTION READ ONLY")
            live = fingerprint(c)
    except Exception as exc:                                  # never echo a DSN
        print(f"error: the live backlog could not be read ({type(exc).__name__})", file=sys.stderr)
        return RC_USAGE
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "master_fingerprint.json"
        env = {**os.environ, "FONDOS_DRIFT_OUT": str(out)}
        rc = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "ops" / "run_pg_tests.py"), "--image", args.image, "--", "tests/test_backlog_ddl_fingerprint_pg.py", "-q", "--tb=short",
                             "-p", "no:cacheprovider"], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8").returncode
        if rc != 0 or not out.exists():
            print("error: master's DDL could not be built in the container (is WSL awake?)", file=sys.stderr)
            return RC_USAGE
        master = json.loads(out.read_text(encoding="utf-8"))
    d = diff(live, master)
    if args.json:
        print(json.dumps(d, indent=2))
    else:
        print("\n".join(report_lines(d)))
    return RC_DRIFT if has_drift(d) else RC_OK


if __name__ == "__main__":
    sys.exit(main())
