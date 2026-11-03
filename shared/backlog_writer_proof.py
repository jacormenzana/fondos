# shared/backlog_writer_proof.py
# -*- coding: utf-8 -*-
"""backlog_writer_proof.py -- the harness that proves an automatic writer before it may write (FND-0370; the register and the rule are in shared/backlog_writers.py).

A writer under proof offers an ENTRY module with:
    plan(bc, now)                      -> list[Write]   what it would write; it may only READ (a read-only connection makes a write fail)
    apply(bc, planned, *, caps, enabled) -> list[Write]   writes through `bc` and returns exactly the writes it made; with enabled=False (its kill switch) it writes nothing; it stops at `caps`
    protections(bc)                    -> list[(name, ok, detail)]   optional: checks of its own protections (for example "it never overwrites a person's decision")
`bc` is the backlog client bound to one connection (BoundClient), so the same entry runs against the live backlog READ-ONLY (the dry run) and against a throwaway database (the proof).

The two parts, in the owner's terms:
  * DRY RUN ON REAL DATA (`dry_run`): the plan is measured, not only completed: DUPLICATIONS, VOLUME of writes, CONCENTRATION of changes per ticket, against the writer's caps.
  * PROOF ON A SCRATCH DATABASE LOADED FROM A REAL SNAPSHOT (`scratch_proof`; shared/backlog_scratch.py refuses any live target before anything runs): the writer runs for real and its protections are checked:
    the kill switch OFF writes nothing; the caps stop it; what it reports equals what the database counted; its writes are within its declared kinds and within the caps; a second run writes nothing
    (idempotent); a plan with fewer than two writes proves nothing and fails. Everything is reproducible from the snapshot file and the code digest recorded in the proof."""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from shared import backlog_writers as bw

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 1

# how the writes of one identity are counted in the database, by kind: (table, the column that holds the identity)
COUNT_TABLES = {
    "LOG": ("backlog.backlog_logs", "username"),
    "LINK": ("backlog.backlog_link_event", "username"),
    "TRANSITION": ("backlog.status_event", "actor"),
    "FACT": ("backlog.ops_event", "actor"),
    "MODULE": ("backlog.ticket_module_event", "actor"),
    "PROBE": ("backlog.probe_event", "actor"),
    "GROUP": ("backlog.group_member", "actor"),
    "TICKET": ("backlog.backlog", "reporter"),
}


class BoundClient:
    """The backlog client module with every call that takes `conn` bound to one connection: the entry of a writer calls `bc.append_log(...)` and the write goes to that connection and nowhere else."""

    def __init__(self, client, conn):
        self._client, self._conn = client, conn

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if callable(attr) and "conn" in inspect.signature(attr).parameters:
            return lambda *a, **k: attr(*a, conn=self._conn, **k)
        return attr


def load_entry(spec: bw.WriterSpec, root: Path = ROOT):
    """The entry module of a writer (its `entry` is a module name importable from the repo or scripts/audit)."""
    for p in (str(root), str(Path(root) / "scripts" / "audit")):
        if p not in sys.path:
            sys.path.insert(0, p)
    mod = importlib.import_module(spec.entry)
    for fn in ("plan", "apply"):
        if not callable(getattr(mod, fn, None)):
            raise ValueError(f"the entry {spec.entry} has no {fn}()")
    return mod


def snapshot_digest(snap: dict) -> str:
    return hashlib.sha256(json.dumps(snap, sort_keys=True, default=str).encode("utf-8")).hexdigest()


# ── the snapshot: what a writer may need besides the tickets and the links ──────────────────────────

EXTRA_TABLES = ("backlog.backlog_logs", "backlog.ticket_module_event")


def export_extra(conn, tables: tuple = EXTRA_TABLES) -> dict:
    """{table: [row dicts]} of the tables a writer may read besides the tickets and link events that scripts/audit/backlog_phase_b_replay.export_snapshot already holds. Run on a READ-ONLY connection."""
    out = {}
    for table in tables:
        if not conn.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()[0]:
            continue
        cur = conn.execute(f"SELECT * FROM {table}")
        cols = [d.name for d in cur.description]
        out[table] = json.loads(json.dumps([dict(zip(cols, row)) for row in cur.fetchall()], default=str))
    return out


def load_extra_tables(conn, extra: dict) -> None:
    """Insert `extra` into a SCRATCH database that already has the DDL (the caller has run assert_scratch), keeping the ids and timestamps, and move each table's sequences past the loaded ids."""
    conn.execute("SELECT set_config('backlog.via_proc', 'on', true)")
    for table, rows in extra.items():
        if not rows or not conn.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()[0]:
            continue
        schema, name = table.split(".")
        have = {r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s", (schema, name)).fetchall()}
        for row in rows:
            cols = [c for c in row if c in have]
            conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", [row[c] for c in cols])
        for (col,) in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s AND column_default LIKE 'nextval%%'", (schema, name)).fetchall():
            conn.execute(f"SELECT setval(pg_get_serial_sequence(%s, %s), GREATEST((SELECT max({col}) FROM {table}), 1))", (table, col))
    conn.execute("SELECT set_config('backlog.via_proc', 'off', true)")


# ── the dry run on real data ────────────────────────────────────────────────────

def dry_run(entry, bc, caps: bw.Caps, now: datetime) -> dict:
    """{'completed', 'planned', 'metrics', 'breaches', 'error'}: the plan of the writer on the data `bc` reads (read-only), measured against `caps`. A plan that raises is reported, never swallowed."""
    try:
        planned = list(entry.plan(bc, now))
    except Exception as exc:                                                   # noqa: BLE001
        return {"completed": False, "planned": 0, "metrics": None, "breaches": [], "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    m = bw.metrics(planned)
    return {"completed": True, "planned": len(planned), "metrics": m, "breaches": bw.breaches(m, caps), "error": None}


# ── the proof on a scratch database ─────────────────────────────────────────────

def count_writes(conn, actor: str) -> dict:
    """{kind: rows written under `actor`} in the database `conn` points at. A table that this database does not have (a phase not applied) counts 0: asking for it must not abort the transaction."""
    out = {}
    for kind, (table, column) in COUNT_TABLES.items():
        if not conn.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()[0]:
            out[kind] = 0
            continue
        out[kind] = int(conn.execute(f"SELECT count(*) FROM {table} WHERE {column} = %s", (actor,)).fetchone()[0])
    return out


def _delta(before: dict, after: dict) -> dict:
    return {k: after[k] - before[k] for k in after if after[k] != before[k]}


def scratch_proof(conn, spec: bw.WriterSpec, entry, bc: BoundClient, now: datetime, assert_scratch: Callable) -> dict:
    """The proof run. `assert_scratch(conn)` is called FIRST and raises on any live-looking target. Returns {'checks': [(name, ok, detail)], 'failures': [...], 'metrics': {...}, 'planned': n}."""
    assert_scratch(conn)
    caps = spec.caps
    checks: list = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))
        return ok

    planned = list(entry.plan(bc, now))
    check("the plan has work to prove", len(planned) >= 2, f"{len(planned)} planned write(s); a plan with fewer than two proves nothing: pick data (a snapshot) where the writer has something to do")
    if len(planned) < 2:
        return _result(checks, bw.metrics(planned), len(planned))
    before = count_writes(conn, spec.actor)

    off = list(entry.apply(bc, planned, caps=caps, enabled=False))
    after_off = count_writes(conn, spec.actor)
    check("kill switch OFF writes nothing", not off and not _delta(before, after_off), f"reported {len(off)} write(s), database delta {_delta(before, after_off) or 'none'}")

    tiny = bw.Caps(max_writes_per_run=1, max_writes_per_ticket=1, max_top_ticket_share=1.0, min_writes_for_share=10 ** 9, max_duplicates=0)
    capped = list(entry.apply(bc, planned, caps=tiny, enabled=True))
    after_cap = count_writes(conn, spec.actor)
    check("the caps stop it", len(capped) <= 1 and sum(_delta(after_off, after_cap).values()) <= 1, f"cap 1: it made {len(capped)} write(s), database delta {_delta(after_off, after_cap) or 'none'}")

    # the real run on top of the capped one: re-plan, so what the capped run already did is not planned again
    replanned = list(entry.plan(bc, now))
    made = list(entry.apply(bc, replanned, caps=caps, enabled=True))
    after_real = count_writes(conn, spec.actor)
    delta_real = _delta(after_cap, after_real)
    m = bw.metrics(made)                                                       # volume and concentration are judged PER RUN: the real run, not the artificial one-write run before it
    across = bw.metrics(capped + made)                                         # duplications are judged across the runs: the same fact written twice is a duplication wherever it happens
    check("what it reports equals what the database counted", sum(delta_real.values()) == len(made), f"reported {len(made)}, database delta {delta_real or 'none'}")
    check("it writes only the kinds it declares", set(_delta(before, after_real)) <= set(spec.writes), f"database delta by kind {_delta(before, after_real)}, declared {sorted(spec.writes)}")
    br = bw.breaches(m, caps)
    if across["duplicates"] > caps.max_duplicates and not any(b.startswith("DUPLICATION") for b in br):
        br.append(f"DUPLICATION: {across['duplicates']} repeated write(s) across the runs, the cap is {caps.max_duplicates} (e.g. {'; '.join(across['duplicate_samples'][:3])})")
    check("its real writes are within its caps (volume, concentration, duplications)", not br, "; ".join(br) or f"{m['total']} writes on {m['tickets']} ticket(s), busiest {m['busiest_ticket']} {m['max_per_ticket']}, duplicates {across['duplicates']}")

    again = list(entry.apply(bc, list(entry.plan(bc, now)), caps=caps, enabled=True))
    after_again = count_writes(conn, spec.actor)
    check("a second run writes nothing (idempotent)", not again and not _delta(after_real, after_again), f"second run reported {len(again)} write(s), database delta {_delta(after_real, after_again) or 'none'}")

    extra = getattr(entry, "protections", None)
    if callable(extra):
        for name, ok, detail in extra(bc):
            check(f"protection: {name}", ok, detail)
    return _result(checks, m, len(planned))


def _result(checks: list, m: dict, planned: int) -> dict:
    return {"checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks], "failures": [f"{n}: {d}" for n, ok, d in checks if not ok], "metrics": m, "planned": planned}


# ── the record ──────────────────────────────────────────────────────────────────

def build_record(spec: bw.WriterSpec, digest: str, dry: dict, scratch: Optional[dict], snapshot: Optional[dict], static: list, now: datetime) -> dict:
    """The proof record: PASS only when the static checks are clean, the dry run completed within the caps and the scratch proof has no failure. Pure."""
    failures = [f"static: {p}" for p in static]
    if not dry.get("completed"):
        failures.append(f"dry run did not complete: {dry.get('error')}")
    failures += [f"dry run: {b}" for b in dry.get("breaches", [])]
    if scratch is None:
        failures.append("the scratch proof was not run")
    else:
        failures += [f"scratch: {f}" for f in scratch.get("failures", [])]
    return {"schema": SCHEMA, "writer": spec.name, "actor": spec.actor, "ts": now.astimezone(timezone.utc).isoformat(timespec="seconds"), "code_digest": digest,
            "snapshot": snapshot, "dry_run": {k: dry.get(k) for k in ("completed", "planned", "metrics", "breaches")}, "scratch": scratch,
            "verdict": "PASS" if not failures else "FAIL", "failures": failures}


def append_record(record: dict, path: Optional[Path] = None) -> Path:
    p = Path(path) if path else bw.proofs_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    return p
