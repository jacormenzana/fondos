#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
writer_proof.py -- proves an AUTOMATIC WRITER of the backlog before it may write (FND-0370; owner requirements of 2026-11-03). The register and the rule: shared/backlog_writers.py; the harness: shared/backlog_writer_proof.py.

    python scripts/gov/backlog/writer_proof.py --list                          # every registered writer, its status (GRANDFATHERED / PROVEN / PENDING) and why
    python scripts/gov/backlog/writer_proof.py --writer NAME --dry-run-only    # REAL data, read-only: what it would write, measured against its caps (duplications, volume, concentration per ticket)
    python scripts/gov/backlog/writer_proof.py --writer NAME                   # the whole proof: the dry run, then a throwaway Postgres loaded from a read-only snapshot of the real backlog where the writer
                                                                         # runs FOR REAL and its protections are checked; the result is recorded in out/audit/writer_proofs.jsonl
    python scripts/gov/backlog/writer_proof.py --verify NAME                   # is it PROVEN for the code on disk now?

A new writer is added to shared/backlog_writers.py (status REGISTERED, kill switch OFF, caps, files, an entry module with plan() and apply()); until a proof PASSES for the current digest of its files, the client
refuses every write it makes. A change to its code invalidates the proof. Nothing here writes to the live backlog: the dry run uses a read-only transaction, the snapshot is a read-only export, and the writer runs
for real only in the scratch container (shared/backlog_scratch.py refuses any live target before anything runs).
Exit codes: 0 PASS / status shown, 1 the proof FAILED, 2 usage or environment error."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "mig" / "backlog"))
from shared import backlog_writer_proof as wp  # noqa: E402
from shared import backlog_writers as bw  # noqa: E402
from shared.out_dir import out_root  # noqa: E402

RC_OK, RC_FAIL, RC_USAGE = 0, 1, 2


def list_lines(proofs: list, root: Path = ROOT) -> list:
    """One line per registered writer: name, status, what it writes, why. Pure given the proofs."""
    lines = []
    for name, spec in sorted(bw.REGISTRY.items()):
        status, why = bw.proof_status(spec, proofs, bw.code_digest(spec, root))
        lines.append(f"{spec.actor:<20} {status:<13} writes {','.join(spec.writes):<32} {why}")
    return lines


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true")
    g.add_argument("--writer", metavar="NAME")
    g.add_argument("--verify", metavar="NAME")
    ap.add_argument("--dry-run-only", action="store_true", help="real data, read-only; records nothing")
    ap.add_argument("--snapshot", help="use this snapshot file instead of exporting a fresh one (reproducible re-runs)")
    ap.add_argument("--image", default="postgres:17")
    args = ap.parse_args(argv)
    proofs = bw.read_proofs()
    if args.list:
        print("\n".join(list_lines(proofs)))
        return RC_OK
    name = args.verify or args.writer
    spec = bw.REGISTRY.get(name)
    if spec is None:
        print(f"error: {name!r} is not in the register (shared/backlog_writers.py)", file=sys.stderr)
        return RC_USAGE
    status, why = bw.proof_status(spec, proofs, bw.code_digest(spec))
    if args.verify:
        print(f"{spec.actor}: {status} -- {why}")
        return RC_OK if status in (bw.GRANDFATHERED, "PROVEN") else RC_FAIL
    if spec.status == bw.GRANDFATHERED:
        print(f"{spec.actor} is GRANDFATHERED (live before the rule of 2026-11-03): it needs no proof. Register new writers with status REGISTERED.")
        return RC_OK
    import shared.config as cfg
    from shared import backlog_client as bc
    static = bw.static_problems(spec, cfg)
    for p in static:
        print(f"static: {p}")
    try:
        entry = wp.load_entry(spec)
    except Exception as exc:                                                       # noqa: BLE001
        print(f"error: the entry of {spec.name} could not be loaded: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return RC_USAGE
    now = datetime.now(timezone.utc)
    try:
        with bc._session(None) as c:                                               # the live backlog, READ-ONLY: a write attempt fails in the database itself
            c.execute("SET TRANSACTION READ ONLY")
            dry = wp.dry_run(entry, wp.BoundClient(bc, c), spec.caps or bw.Caps(), now)
            snap = None
            if not args.dry_run_only and not args.snapshot:
                import backlog_phase_b_replay as rp
                snap = rp.export_snapshot(c)
                snap["extra"] = wp.export_extra(c)
    except Exception as exc:                                                       # noqa: BLE001 -- never echo a DSN
        print(f"error: the live backlog could not be read ({type(exc).__name__})", file=sys.stderr)
        return RC_USAGE
    m = dry.get("metrics") or {}
    print(f"dry run on real data: completed={dry['completed']} planned={dry['planned']} tickets={m.get('tickets')} max/ticket={m.get('max_per_ticket')} "
          f"busiest={m.get('busiest_ticket')} share={(m.get('top_share') or 0):.0%} duplicates={m.get('duplicates')}")
    for b in dry.get("breaches", []):
        print(f"  BREACH {b}")
    if args.dry_run_only:
        return RC_OK if dry["completed"] and not dry["breaches"] else RC_FAIL
    staging = out_root(ROOT) / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    if args.snapshot:
        snap_path = Path(args.snapshot)
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
    else:
        snap_path = staging / f"writer_proof_{name}_{now:%Y%m%dT%H%M%SZ}.json"
        snap_path.write_text(json.dumps(snap), encoding="utf-8")
    result_path = staging / f"writer_proof_{name}_{now:%Y%m%dT%H%M%SZ}.result.json"
    result_path.unlink(missing_ok=True)
    env = {**os.environ, "FONDOS_PROOF_WRITER": name, "FONDOS_PROOF_SNAPSHOT": str(snap_path), "FONDOS_PROOF_RESULT": str(result_path)}
    cmd = [sys.executable, "-X", "utf8", str(ROOT / "scripts" / "gov" / "delivery" / "run_pg_tests.py"), "--image", args.image, "--", "tests/test_backlog_writer_proof_pg.py", "-k", "prove_the_real_writer", "-q", "--tb=short", "-p", "no:cacheprovider"]
    print(f"scratch proof: snapshot {snap_path.name} ({len(snap['tickets'])} tickets, {len(snap['link_events'])} link events); a throwaway container, the real DDL ...")
    subprocess.run(cmd, cwd=ROOT, env=env, stdin=subprocess.DEVNULL)
    scratch = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else None
    record = wp.build_record(spec, bw.code_digest(spec), dry, scratch, {"file": snap_path.name, "digest": wp.snapshot_digest(snap), "tickets": len(snap["tickets"]), "link_events": len(snap["link_events"])}, static, now)
    path = wp.append_record(record)
    for c in (scratch or {}).get("checks", []):
        print(f"  [{'ok' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}")
    print(f"PROOF {record['verdict']} for {spec.actor} (code digest {record['code_digest'][:12]}), recorded in {path}")
    for f in record["failures"]:
        print(f"  - {f}")
    return RC_OK if record["verdict"] == "PASS" else RC_FAIL


if __name__ == "__main__":
    sys.exit(main())
