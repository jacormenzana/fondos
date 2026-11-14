# shared/backlog_writers.py
# -*- coding: utf-8 -*-
"""backlog_writers.py -- the register of AUTOMATIC WRITERS of the backlog and the rule that none may write until it has been proven (FND-0370; owner requirements of 2026-11-03).

An automatic writer is a process that writes to the backlog under a `system:<name>` identity (the autopilot, the commit-msg gate, the deploy watcher, ...). Owner requirements, in his words:
  1. the dry run of a writer must check more than that it completes: DUPLICATIONS, the VOLUME of write operations and the CONCENTRATION of changes per ticket;
  2. no NEW automatic writer may modify the backlog until it has been demonstrated, with reproducible tests on REAL data, that it works correctly and that its protection mechanisms are operational.

What this module holds (pure apart from reading files): the registry (REGISTRY), the measures of a set of planned or performed writes (`metrics`, `breaches`), the digest of a writer's code, the proof records
(out/audit/writer_proofs.jsonl, written by scripts/gov/backlog/writer_proof.py) and the gate `assert_may_write`, which shared/backlog_client.py calls before every write that goes to a real connection:
  * a person or an agent (human:* / agent:*) is out of scope here;
  * a `system:*` identity that is not in the registry is refused (a new writer cannot land without an entry: tests/test_backlog_writers.py scans the code for every `system:<name>` literal);
  * a GRANDFATHERED writer (it was live when this rule was made, 2026-11-03) may write; the proof is only asked of what is new;
  * any other writer may write only while it has a PASS proof recorded for the CURRENT digest of its code files: a change to its code invalidates the proof, and the proof needs its kill switch to ship OFF.
It is a guard of the process that calls the client, not a security boundary: whoever holds a connection string can write without it (the same limit as the vault and the live-write guard)."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from shared.out_dir import out_root

ROOT = Path(__file__).resolve().parents[1]
PROOFS_NAME = "writer_proofs.jsonl"
KINDS = ("LOG", "LINK", "TRANSITION", "FACT", "MODULE", "PROBE", "GROUP", "TICKET")
GRANDFATHERED, REGISTERED = "GRANDFATHERED", "REGISTERED"


class WriterRefused(RuntimeError):
    """A system:* identity may not write: it is not registered, or it has no valid proof. Nothing was written."""


@dataclass(frozen=True)
class Caps:
    """What a proven writer may do in one run (the owner's measures): at most `max_writes_per_run` writes, at most `max_writes_per_ticket` on one ticket, the busiest ticket at most `max_top_ticket_share` of
    the run's writes (when the run has `min_writes_for_share` or more), and `max_duplicates` repeated writes (0: none)."""
    max_writes_per_run: int = 200
    max_writes_per_ticket: int = 3
    max_top_ticket_share: float = 0.25
    min_writes_for_share: int = 8
    max_duplicates: int = 0


@dataclass(frozen=True)
class WriterSpec:
    name: str                                    # the identity is `system:<name>`
    writes: tuple                                # which kinds of record it writes (KINDS)
    status: str = REGISTERED                     # GRANDFATHERED (live before 2026-11-03) or REGISTERED (new: must be proven)
    switch: Optional[str] = None                 # the kill switch in shared/config.py (a new writer ships with it OFF / "OFF" / False)
    files: tuple = ()                            # the repo files that make up its code: their digest is what a proof vouches for
    caps: Optional[Caps] = None                  # required of a REGISTERED writer
    entry: Optional[str] = None                  # "module:function" of its plan/apply entry for the proof harness (a REGISTERED writer)
    note: str = ""

    @property
    def actor(self) -> str:
        return f"system:{self.name}"


def _g(name: str, writes: tuple, switch: Optional[str], files: tuple, note: str) -> WriterSpec:
    return WriterSpec(name=name, writes=writes, status=GRANDFATHERED, switch=switch, files=files, note=note)


# The writers that existed when the rule was made (census of the live backlog and of the code, 2026-11-03). They are not asked for a proof; they are listed so that the register is complete and so that anything
# else is, by construction, new.
REGISTRY: dict = {s.name: s for s in (
    _g("autopilot", ("LOG", "TRANSITION", "FACT", "MODULE", "PROBE", "GROUP"), "BACKLOG_AUTOPILOT_MODE",
       ("scripts/gov/backlog/backlog_autopilot.py", "shared/backlog_autopilot.py", "scripts/gov/backlog/backlog_modules.py", "scripts/gov/backlog/backlog_impact.py", "shared/backlog_clusters.py",
        "shared/backlog_modules.py", "shared/backlog_impact.py"), "moves tickets on recorded evidence, writes MERGE / TEXT / COMMIT facts, impact notes, probe runs and module groups"),
    _g("gate", ("TRANSITION", "LINK", "LOG"), "BACKLOG_AUTO_FORWARD_ENABLED", ("scripts/gov/backlog/commit_msg_gate.py", "shared/backlog_gate.py", "scripts/gov/backlog/pre_push_gate.py"),
       "the commit-msg hook: OPEN/TODO -> IN_PROGRESS on Refs:, collision proposals"),
    _g("sync", ("FACT",), None, ("scripts/gov/backlog/backlog_sync.py",), "records COMMIT facts from git history"),
    _g("deploy", ("FACT",), "BACKLOG_AUTO_DEPLOY_MODE", ("scripts/gov/backlog/owner_deploy.py",), "records the DEPLOY fact after a verified apply (the scoped login backlog_facts)"),
    _g("autodetect", ("LOG", "TICKET"), "BACKLOG_INCIDENT_AUTO_APPEND_ENABLED", ("shared/backlog_client.py", "shared/backlog_overlap.py"), "report_incident: opens or appends an incident"),
    _g("calibrator", ("LOG",), None, ("scripts/gov/backlog/gate_calibrator.py",), "may only retire the collision gate or tighten T0"),
    _g("replay", ("TRANSITION",), None, ("scripts/mig/backlog/backlog_phase_b_replay.py",), "replays closures on a SCRATCH database only (shared/backlog_scratch.py refuses a live target)"),
    _g("backfill", ("TRANSITION",), None, (), "historic: the 302 status events written when the status history was created"),
    _g("migration", ("TRANSITION", "LOG"), None, ("scripts/mig/backlog/migrate_backlog_v2.py",), "the default closing conditions given to in-flight tickets by the probes migration"),
    _g("owner-waiver", ("FACT",), None, (), "historic: the single gate_switch row of 2026-10-28 (limitation L8)"),
)}


# ── measures: duplicates, volume, concentration (owner requirement 1) ───────────────────────────────

@dataclass(frozen=True)
class Write:
    """One planned or performed write: the ticket it is about, its kind, a key that identifies the fact written (two writes with the same ticket, kind and key are the same fact), and its text."""
    ticket: str
    kind: str
    key: str = ""
    text: str = ""


def metrics(writes: list) -> dict:
    """{'total', 'tickets', 'max_per_ticket', 'busiest_ticket', 'top_share', 'duplicates', 'duplicate_samples'} of a list of Write. A duplicate is a write whose (ticket, kind, key) was already written, or whose
    (ticket, kind, text) was, when it has no key: the second and later ones are counted. Pure."""
    per_ticket: dict = {}
    seen: set = set()
    dups = []
    for w in writes:
        per_ticket[w.ticket] = per_ticket.get(w.ticket, 0) + 1
        ident = (w.ticket, w.kind, w.key) if w.key else (w.ticket, w.kind, "text:" + w.text)
        if ident in seen:
            dups.append(ident)
        seen.add(ident)
    total = len(writes)
    busiest = max(per_ticket.items(), key=lambda kv: (kv[1], kv[0])) if per_ticket else (None, 0)
    return {"total": total, "tickets": len(per_ticket), "max_per_ticket": busiest[1], "busiest_ticket": busiest[0],
            "top_share": (busiest[1] / total) if total else 0.0, "duplicates": len(dups), "duplicate_samples": [f"{t} {k} {key[:60]}" for t, k, key in dups[:5]]}


def breaches(m: dict, caps: Caps) -> list:
    """The caps a set of writes breaks, as readable sentences (empty = within the caps). Pure."""
    out = []
    if m["total"] > caps.max_writes_per_run:
        out.append(f"VOLUME: {m['total']} writes in one run, the cap is {caps.max_writes_per_run}")
    if m["max_per_ticket"] > caps.max_writes_per_ticket:
        out.append(f"CONCENTRATION: {m['busiest_ticket']} receives {m['max_per_ticket']} writes, the cap per ticket is {caps.max_writes_per_ticket}")
    if m["total"] >= caps.min_writes_for_share and m["top_share"] > caps.max_top_ticket_share:
        out.append(f"CONCENTRATION: the busiest ticket ({m['busiest_ticket']}) takes {m['top_share']:.0%} of the writes, the cap is {caps.max_top_ticket_share:.0%}")
    if m["duplicates"] > caps.max_duplicates:
        out.append(f"DUPLICATION: {m['duplicates']} repeated write(s), the cap is {caps.max_duplicates} (e.g. {'; '.join(m['duplicate_samples'][:3])})")
    return out


# ── the digest of a writer's code and its proof records ─────────────────────────────────────────────

def code_digest(spec: WriterSpec, root: Path = ROOT) -> str:
    """sha-256 over the (path, content digest) of every file of the writer; a missing file counts as missing, not as an error. A change to any of them is a different writer as far as a proof goes."""
    h = hashlib.sha256()
    for rel in sorted(spec.files):
        h.update(rel.encode("utf-8"))
        try:
            h.update(hashlib.sha256((Path(root) / rel).read_bytes()).digest())
        except OSError:
            h.update(b"<missing>")
    return h.hexdigest()


def proofs_path(environ: Optional[dict] = None, root: Path = ROOT) -> Path:
    """out/audit/writer_proofs.jsonl of the MAIN tree (FONDOS_OUT_DIR when the code runs from the autopilot's clean clone)."""
    if environ is not None and environ.get("FONDOS_OUT_DIR"):
        return Path(environ["FONDOS_OUT_DIR"]) / "audit" / PROOFS_NAME
    return out_root(root) / "audit" / PROOFS_NAME


def read_proofs(path: Optional[Path] = None) -> list:
    """The proof records, oldest first; a line that is not JSON is skipped."""
    p = Path(path) if path else proofs_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def proof_status(spec: WriterSpec, proofs: list, digest: str) -> tuple:
    """(status, why) of a writer: GRANDFATHERED; PROVEN (the newest proof of this writer passed and vouches for exactly this code); or PENDING with the reason. Pure."""
    if spec.status == GRANDFATHERED:
        return GRANDFATHERED, "it was live before the rule of 2026-11-03"
    mine = [p for p in proofs if p.get("writer") == spec.name]
    if not mine:
        return "PENDING", "no proof on record: python scripts/gov/backlog/writer_proof.py --writer " + spec.name
    last = mine[-1]
    if last.get("verdict") != "PASS":
        return "PENDING", f"its newest proof did not pass ({'; '.join(map(str, (last.get('failures') or [])[:3]))})"
    if last.get("code_digest") != digest:
        return "PENDING", "its code changed since the proof (digest differs): prove it again"
    return "PROVEN", f"proof of {str(last.get('ts'))[:19]} for the current code"


def assert_may_write(actor: Optional[str], *, proofs: Optional[list] = None, root: Path = ROOT, registry: Optional[dict] = None) -> None:
    """Raise WriterRefused unless `actor` may write. Only `system:*` identities are judged. See the module docstring for the rule. `proofs` and `registry` are injectable for the tests."""
    who = str(actor or "")
    if not who.startswith("system:"):
        return
    reg = REGISTRY if registry is None else registry
    spec = reg.get(who.split(":", 1)[1])
    if spec is None:
        raise WriterRefused(f"{who} is not a registered automatic writer: add it to shared/backlog_writers.py (new writers ship OFF) and prove it with `python scripts/gov/backlog/writer_proof.py --writer <name>` "
                            f"before it writes to the backlog (FND-0370)")
    if spec.status == GRANDFATHERED:
        return
    status, why = proof_status(spec, read_proofs() if proofs is None else proofs, code_digest(spec, root))
    if status != "PROVEN":
        raise WriterRefused(f"{who} may not write yet: {why} (FND-0370: a new automatic writer writes only after a proof on real data)")


def static_problems(spec: WriterSpec, config=None, root: Path = ROOT) -> list:
    """What is wrong with a REGISTERED writer's entry before any run: no caps, no entry, no files, a missing kill switch, a kill switch that does not ship OFF. Empty for a GRANDFATHERED one. Pure given `config`."""
    if spec.status == GRANDFATHERED:
        return []
    problems = []
    if spec.caps is None:
        problems.append("no caps declared")
    if not spec.entry:
        problems.append("no plan/apply entry for the proof harness")
    if not spec.files:
        problems.append("no code files listed (a proof would vouch for nothing)")
    for rel in spec.files:
        if not (Path(root) / rel).exists():
            problems.append(f"listed file {rel} does not exist")
    if not spec.switch:
        problems.append("no kill switch")
    elif config is not None:
        if not hasattr(config, spec.switch):
            problems.append(f"kill switch {spec.switch} is not in shared/config.py")
        elif getattr(config, spec.switch) not in (False, "OFF"):
            problems.append(f"kill switch {spec.switch} does not ship OFF (it is {getattr(config, spec.switch)!r})")
    return problems
