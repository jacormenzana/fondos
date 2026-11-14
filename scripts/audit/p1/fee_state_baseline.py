#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fee_state_baseline.py -- read-only baseline of the entry / exit fee state (Package H, Slice 0; plan sections R6.5, R7.2).

    python scripts/audit/p1/fee_state_baseline.py                 # prints the summary, writes the JSON
    python scripts/audit/p1/fee_state_baseline.py --no-write      # prints only
    python scripts/audit/p1/fee_state_baseline.py --out PATH.json

What it records, for BOTH populations (the current universe and all funds), from the database alone:
  1. the stored matrix: Fee_Known_Flag x state of the point x state of the ceiling (the defect the owner reported, measured);
  2. the derived state each fund WOULD read once the flags are generated from their numbers (shared.fee_state.derive_fee_state), and the
     transition stored flag -> derived state (what BI users will see change);
  3. the pair conflicts (the applied fee above its own declared maximum) with the entry-row window of the stored KID text;
  4. negative stored fee values (the guard of plan R7.1 #2 is a guard, not a repair: none expected);
  5. the consumer map: which production files read the fee columns (grep of the repository, tests and documents excluded);
  6. the accuracy-sample strata and their sizes, with the Wilson STOP / REVIEW rule (plan R7.1 #3);
  7. the shape of kiid_pdf_hash (the changed-document condition of the pair rules rests on it).

Exit codes: 0 done | 3 cannot read the database. Read-only: SELECTs only, no write of any kind to the database. The only file it writes is the JSON.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from shared.fee_state import (ENTRY_STATES, EXIT_STATES, PAIR_TOLERANCE_PP, RATIO_TO_PCT, accuracy_decision, derive_fee_state,  # noqa: E402
                              pair_conflict)

RC_OK, RC_NO_DB = 0, 3
DEFAULT_OUT = ROOT / "doc" / "backlog" / "PKG_H_fee_state_baseline.json"
SAMPLE_PER_STRATUM = 30
FEE_COLUMN_RE = re.compile(r"entry_fee_pct|exit_fee_pct|fee_known_flag", re.IGNORECASE)   # the *_max columns share the prefix
# `upload/` holds tracked snapshot copies of code made for external uploads (prepare_upload.py, *_upload.py): not consumers.
SKIP_DIRS = {"tests", "__pycache__", "out", "doc", ".git", ".claude", "cache", "log", "deployments", "node_modules", "upload"}
ENTRY_ROW_RE = re.compile(r"(costes? de entrada|entry costs?|cost of entry)", re.IGNORECASE)
EXIT_ROW_RE = re.compile(r"(costes? de salida|exit costs?|cost of exit)", re.IGNORECASE)
WINDOW = 360


# ───────────────────────────── pure core (R-7) ─────────────────────────────

def number_state(value) -> str:
    """'null' | 'zero' | 'pos' | 'neg' for one stored number."""
    if value is None:
        return "null"
    if value < 0:
        return "neg"
    return "zero" if value == 0 else "pos"


def stored_matrix(rows: list) -> list:
    """Entry side: how many funds hold each (stored flag, point state, ceiling state). Sorted by size."""
    counts = Counter((r["fee_known_flag"], number_state(r["entry_fee_pct"]), number_state(r["entry_fee_pct_max"])) for r in rows)
    return [{"flag": f, "point": p, "ceiling": c, "n": n} for (f, p, c), n in sorted(counts.items(), key=lambda kv: -kv[1])]


def exit_matrix(rows: list) -> list:
    """Exit side has no stored flag: only the (point state, ceiling state) pairs."""
    counts = Counter((number_state(r["exit_fee_pct"]), number_state(r["exit_fee_pct_max"])) for r in rows)
    return [{"point": p, "ceiling": c, "n": n} for (p, c), n in sorted(counts.items(), key=lambda kv: -kv[1])]


def derived_distribution(rows: list, side: str) -> dict:
    point, ceiling = ("entry_fee_pct", "entry_fee_pct_max") if side == "entry" else ("exit_fee_pct", "exit_fee_pct_max")
    states = ENTRY_STATES if side == "entry" else EXIT_STATES
    counts = Counter(derive_fee_state(r[point], r[ceiling], side) for r in rows)
    return {s: counts.get(s, 0) for s in states}


def transitions(rows: list) -> list:
    """Stored entry flag -> derived entry state: what changes for whoever reads the flag today."""
    counts = Counter((r["fee_known_flag"], derive_fee_state(r["entry_fee_pct"], r["entry_fee_pct_max"], "entry")) for r in rows)
    return [{"stored": s, "derived": d, "n": n} for (s, d), n in sorted(counts.items(), key=lambda kv: (kv[0][0] or "", -kv[1]))]


def conflicts(rows: list, side: str) -> list:
    point, ceiling = ("entry_fee_pct", "entry_fee_pct_max") if side == "entry" else ("exit_fee_pct", "exit_fee_pct_max")
    return sorted(({"isin": r["isin"], "in_universe": r["in_current_universe"] == 1, "stored_flag": r["fee_known_flag"],
                    "point": r[point], "ceiling_pct": r[ceiling]}
                   for r in rows if pair_conflict(r[point], r[ceiling])), key=lambda d: d["isin"])


def negatives(rows: list) -> dict:
    cols = ("entry_fee_pct", "entry_fee_pct_max", "exit_fee_pct", "exit_fee_pct_max")
    return {c: sum(1 for r in rows if r[c] is not None and r[c] < 0) for c in cols}


def sample_strata(rows: list, side: str) -> dict:
    """Sizes of the accuracy-sample strata (plan R7.1 #3): the four derived states; on the exit side the zero state is split in point 0
    without a ceiling and point 0 with a ceiling of 0 (the 1,434 + 1,033 funds the entry-only sample would leave unaudited)."""
    point, ceiling = ("entry_fee_pct", "entry_fee_pct_max") if side == "entry" else ("exit_fee_pct", "exit_fee_pct_max")
    counts: Counter = Counter()
    for r in rows:
        state = derive_fee_state(r[point], r[ceiling], side)
        if side == "exit" and state == "ZERO_CONFIRMED":
            state = "ZERO_CONFIRMED:point0_no_ceiling" if r[ceiling] is None else "ZERO_CONFIRMED:point0_ceiling0_or_ceiling_only"
        counts[state] += 1
    return dict(sorted(counts.items()))


def consumer_map(root: Path) -> dict:
    """{path: {column-ish token: count}} of production files that mention a fee column. Static scan, tests / documents / outputs skipped."""
    found: dict = {}
    for ext in ("*.py", "*.sql"):
        for path in root.rglob(ext):
            rel = path.relative_to(root)
            if any(part in SKIP_DIRS for part in rel.parts) or path.name == Path(__file__).name:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            tokens = Counter(m.group(0).lower() for m in FEE_COLUMN_RE.finditer(text))
            if tokens:
                found[rel.as_posix()] = dict(tokens)
    return dict(sorted(found.items()))


def entry_window(text: str, side: str = "entry", width: int = WINDOW) -> str:
    """The stored KID text around the first entry (or exit) charge row, whitespace collapsed; '' when the row is not found."""
    if not text:
        return ""
    m = (ENTRY_ROW_RE if side == "entry" else EXIT_ROW_RE).search(text)
    if not m:
        return ""
    return re.sub(r"\s+", " ", text[max(0, m.start() - 20): m.start() + width]).strip()


def gate_table(n: int = SAMPLE_PER_STRATUM) -> list:
    """The Wilson decision for every possible error count at the sample size (so the rule is read, not recomputed)."""
    return [{"errors": e, "decision": accuracy_decision(e, n)} for e in range(0, min(n, 8) + 1)]


def analyze(rows: list) -> dict:
    universe = [r for r in rows if r["in_current_universe"] == 1]
    result = {"populations": {"universe": len(universe), "all": len(rows)}, "pair_tolerance_pp": PAIR_TOLERANCE_PP, "ratio_to_pct": RATIO_TO_PCT}
    for name, pop in (("universe", universe), ("all", rows)):
        result[name] = {
            "entry_stored_matrix": stored_matrix(pop),
            "exit_matrix": exit_matrix(pop),
            "entry_derived": derived_distribution(pop, "entry"),
            "exit_derived": derived_distribution(pop, "exit"),
            "entry_transitions": transitions(pop),
            "entry_conflicts": len(conflicts(pop, "entry")),
            "exit_conflicts": len(conflicts(pop, "exit")),
            "negatives": negatives(pop),
            "strata_entry": sample_strata(pop, "entry"),
            "strata_exit": sample_strata(pop, "exit"),
        }
    result["conflict_entry"] = conflicts(rows, "entry")
    result["conflict_exit"] = conflicts(rows, "exit")
    return result


# ───────────────────────────── thin SQL loader ─────────────────────────────

def load(conn) -> list:
    """fund_master fee columns joined with the KID hash. SELECTs only."""
    cur = conn.execute(
        "SELECT f.isin, f.in_current_universe, f.fee_known_flag, f.entry_fee_pct, f.entry_fee_pct_max, f.exit_fee_pct, f.exit_fee_pct_max, "
        "f.kid_format, k.kiid_pdf_hash FROM fund_master f LEFT JOIN fund_kiid_metadata k ON k.isin = f.isin")
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def add_windows(conn, result: dict) -> None:
    """Attach the entry / exit row window of the stored text to each conflict (bounded text, one query per ISIN, a few dozen at most)."""
    for key, side in (("conflict_entry", "entry"), ("conflict_exit", "exit")):
        for item in result[key]:
            row = conn.execute("SELECT raw_kiid_text FROM fund_kiid_metadata WHERE isin = %s", (item["isin"],)).fetchone()
            item["window"] = entry_window(row[0] if row else "", side)


def hash_shape(rows: list) -> dict:
    hashes = [r["kiid_pdf_hash"] for r in rows if r["kiid_pdf_hash"]]
    return {"funds": len(rows), "with_hash": len(hashes), "distinct": len(set(hashes)), "hex_lengths": sorted({len(h) for h in hashes}),
            "meaning": "SHA-256 of the downloaded PDF bytes (proyecto1/core/io.py pdf_sha256); changes only on a new download; a CACHED pass "
                       "returns the stored value, so 'unchanged document' is 'same hash'"}


def render(result: dict) -> str:
    out = ["FEE STATE BASELINE (read-only)", f"  populations: {result['populations']}"]
    for name in ("universe", "all"):
        r = result[name]
        out.append(f"  [{name}] derived entry: {r['entry_derived']}")
        out.append(f"  [{name}] derived exit : {r['exit_derived']}")
        out.append(f"  [{name}] pair conflicts: entry {r['entry_conflicts']}, exit {r['exit_conflicts']}; negatives: {r['negatives']}")
        bad = [m for m in r["entry_stored_matrix"]
               if (m["flag"] in ("NOT_FOUND", "ENTRY_CONDITIONAL") and m["point"] != "null") or (m["flag"] == "EXIT_INFERRED_ZERO")]
        out.append(f"  [{name}] stored entry flag contradicted by its own point: {sum(m['n'] for m in bad)} funds")
    out.append(f"  conflicts (entry, all funds): {len(result['conflict_entry'])}; (exit): {len(result['conflict_exit'])}")
    out.append(f"  accuracy gate at n={SAMPLE_PER_STRATUM}: " + ", ".join(f"{g['errors']} err -> {g['decision']}" for g in result["gate"]))
    out.append(f"  consumer files mentioning a fee column: {len(result['consumers'])}")
    out.append(f"  kiid_pdf_hash: {result['hash']['with_hash']} of {result['hash']['funds']}, {result['hash']['distinct']} distinct")
    return "\n".join(out)


def main(argv=None, conn=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only baseline of the entry / exit fee state (Package H, Slice 0).")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where the JSON baseline is written (default doc/backlog/PKG_H_fee_state_baseline.json)")
    parser.add_argument("--no-write", action="store_true", help="print the summary only; write no file")
    args = parser.parse_args(argv)
    if conn is None:
        from shared.env_guard import require_db_driver
        require_db_driver("fee_state_baseline.py")
        try:
            from shared.db import get_connection
            conn = get_connection()
        except Exception as exc:
            print(f"cannot read the database: {type(exc).__name__}: {str(exc)[:160]}", file=sys.stderr)
            return RC_NO_DB
    rows = load(conn)
    result = analyze(rows)
    add_windows(conn, result)
    result["generated"] = date.today().isoformat()
    result["gate"] = gate_table()
    result["consumers"] = consumer_map(ROOT)
    result["hash"] = hash_shape(rows)
    print(render(result))
    if not args.no_write:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        print(f"written: {args.out}")
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
