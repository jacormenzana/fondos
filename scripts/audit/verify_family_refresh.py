#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
verify_family_refresh.py -- read-only closure check of the family-nature refresh (FND-0244 follow-up). Anyone can run it, any time.

    python scripts/audit/verify_family_refresh.py

It answers, from the database alone, whether the derived attributes of the funds the family builder corrected now agree with their
sibling share classes, so closing the ticket never waits on a conversation:

  1. HARD: `family_refresh_pending` = 0 active funds (shared.family_refresh), and no ACTIVE family with more than one Fund_Nature.
  2. SOFT: for every ISIN that ever got a FAMILY_NATURE_CORRECTION, its nature-derived attributes (DERIVED_ATTRS) are compared with
     the comparable sibling classes of its family (same nature; preferring the same hedging policy and fund currency). Share classes
     of one fund legitimately differ in cost structure or in a secondary strategy, so a divergence is INFORMATION, never a block: every
     one is dumped in full (corrected ISIN, sibling ISIN, family, names, natures, both values, the class used to pick the sibling) plus
     a per-attribute count, so the decision to close needs no further query.

Exit codes: 0 clean | 1 HARD findings (pending > 0 or an active multi-nature family) | 2 only SOFT divergences | 3 cannot read the database.
P3 depends only on the pending count of its own gate, never on this script. Read-only: SELECTs only, no write of any kind.
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RC_CLEAN, RC_HARD, RC_SOFT, RC_NO_DB = 0, 1, 2, 3

# Attributes derived from the nature / portfolio and therefore equal across the classes of ONE fund (cost fields, currency and hedging
# are class attributes and are deliberately absent).
DERIVED_ATTRS = ("heuristic_block", "heuristic_core", "profile", "style_profile", "credit_quality", "duration_profile",
                 "investment_focus", "strategy", "mmf_structure", "alt_strategy")
LOAD_COLS = ("isin", "fund_name", "fund_nature", "fund_family_id", "in_current_universe", "hedging_policy", "fund_currency") + DERIVED_ATTRS


# ───────────────────────────── pure core (R-7) ─────────────────────────────

def pick_siblings(fund: dict, family_members: list) -> tuple:
    """(siblings, class_used). Siblings = other members with the SAME Fund_Nature; those sharing hedging policy and fund currency are
    preferred, then those sharing the hedging policy, then any of the same nature."""
    same_nature = [m for m in family_members if m["isin"] != fund["isin"] and m["fund_nature"] == fund["fund_nature"]]
    for label, keys in (("hedging+currency", ("hedging_policy", "fund_currency")), ("hedging", ("hedging_policy",))):
        pool = [m for m in same_nature if all(m.get(k) == fund.get(k) and fund.get(k) is not None for k in keys)]
        if pool:
            return pool, f"{label}:{'/'.join(str(fund.get(k)) for k in keys)}"
    return same_nature, "any (no sibling shares the hedging class)"


def compare_attributes(fund: dict, sibling: dict, attrs=DERIVED_ATTRS) -> list:
    """[(attr, fund value, sibling value)] where they differ. None equals None; None vs a value is a divergence (a recomputed
    attribute that stayed empty next to a filled sibling is exactly the stale case to look at)."""
    return [(a, fund.get(a), sibling.get(a)) for a in attrs if fund.get(a) != sibling.get(a)]


def active_multi_nature_families(rows: list) -> list:
    """[(family id, sorted natures)] for families whose ACTIVE members hold more than one Fund_Nature."""
    natures = defaultdict(set)
    for r in rows:
        if r.get("in_current_universe") and r.get("fund_family_id") and r.get("fund_nature"):
            natures[r["fund_family_id"]].add(r["fund_nature"])
    return sorted((f, sorted(n)) for f, n in natures.items() if len(n) > 1)


def analyze(rows: list, corrected_isins: list, pending: list) -> dict:
    """rows = fund_master rows as dicts (LOAD_COLS); returns the findings. No I/O."""
    by_family = defaultdict(list)
    by_isin = {}
    for r in rows:
        by_isin[r["isin"]] = r
        if r.get("fund_family_id"):
            by_family[r["fund_family_id"]].append(r)
    divergences, checked, no_sibling = [], 0, []
    for isin in sorted(set(corrected_isins)):
        fund = by_isin.get(isin)
        if fund is None or not fund.get("fund_family_id"):
            continue
        siblings, used = pick_siblings(fund, by_family[fund["fund_family_id"]])
        if not siblings:
            no_sibling.append(isin)
            continue
        checked += 1
        for sib in siblings:
            for attr, a, b in compare_attributes(fund, sib):
                divergences.append({"isin": isin, "sibling": sib["isin"], "family": fund["fund_family_id"], "attr": attr,
                                    "value": a, "sibling_value": b, "class_used": used, "name": fund["fund_name"],
                                    "sibling_name": sib["fund_name"], "nature": fund["fund_nature"],
                                    "sibling_nature": sib["fund_nature"]})
    return {"pending": list(pending), "multi_nature": active_multi_nature_families(rows), "divergences": divergences,
            "checked": checked, "no_sibling": no_sibling, "corrected": len(set(corrected_isins))}


def exit_code(result: dict) -> int:
    if result["pending"] or result["multi_nature"]:
        return RC_HARD
    return RC_SOFT if result["divergences"] else RC_CLEAN


def render(result: dict) -> str:
    out = ["FAMILY REFRESH CLOSURE CHECK (read-only)"]
    p = result["pending"]
    out.append(f"  [{'FAIL' if p else 'OK  '}] family_refresh_pending: " + (f"{len(p)} active funds: {', '.join(p[:50])}" if p else "0 active funds"))
    mn = result["multi_nature"]
    out.append(f"  [{'FAIL' if mn else 'OK  '}] active multi-nature families: " + (str(len(mn)) if mn else "0"))
    for fam, nat in mn:
        out.append(f"           {fam}: {', '.join(nat)}")
    d = result["divergences"]
    out.append(f"  [{'INFO' if d else 'OK  '}] derived attributes vs sibling classes: {result['checked']} corrected funds compared "
               f"(of {result['corrected']}; {len(result['no_sibling'])} with no same-nature sibling), {len(d)} divergences")
    if d:
        counts = Counter(x["attr"] for x in d)
        out.append("  per attribute: " + ", ".join(f"{a}={n}" for a, n in sorted(counts.items())))
        out.append("  divergences (corrected | sibling | family | attribute | corrected value | sibling value | sibling picked by):")
        for x in d:
            out.append(f"    {x['isin']} [{x['name']}] | {x['sibling']} [{x['sibling_name']}] | {x['family']} | {x['attr']} | "
                       f"{x['value']!r} | {x['sibling_value']!r} | {x['class_used']} (natures {x['nature']} / {x['sibling_nature']})")
    rc = exit_code(result)
    out.append("")
    if rc == RC_CLEAN:
        out.append("RESULT: clean. Nothing blocks closing FND-0244; closing it (and releasing P3) is the owner's decision. Authorize closing? ")
    elif rc == RC_SOFT:
        out.append("RESULT: only informational divergences (classes of one fund may legitimately differ). Review the lines above; if they are "
                   "acceptable, authorize closing FND-0244.")
    else:
        out.append("RESULT: HARD findings. Do not close FND-0244: run `run_block.py --family-nature-refresh --master-db` (or "
                   "P1_discoverAllFunds.bat) and the family builder, then re-run this check.")
    return "\n".join(out)


# ───────────────────────────── thin SQL loader ─────────────────────────────

def load(conn) -> tuple:
    """(fund_master rows as dicts, ISINs that ever had a FAMILY_NATURE_CORRECTION, pending active ISINs). SELECTs only."""
    from shared.family_refresh import STEP_CORRECTION, pending_family_refresh
    cur = conn.execute(          # a literal (not assembled from LOAD_COLS) so the EXPLAIN sweep can verify it
        "SELECT isin, fund_name, fund_nature, fund_family_id, in_current_universe, hedging_policy, fund_currency, "
        "heuristic_block, heuristic_core, profile, style_profile, credit_quality, duration_profile, investment_focus, "
        "strategy, mmf_structure, alt_strategy FROM fund_master")
    cols = [d.name for d in cur.description]
    missing = set(LOAD_COLS) - set(cols)
    if missing:                                    # a DERIVED_ATTRS entry the query does not select would compare None == None forever
        raise RuntimeError(f"verify_family_refresh: the SELECT lacks {sorted(missing)}; keep it in step with LOAD_COLS")
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    corrected = [r[0] for r in conn.execute(
        "SELECT DISTINCT isin FROM ingestion_log WHERE step = %s AND isin IS NOT NULL", (STEP_CORRECTION,)).fetchall()]
    return rows, corrected, pending_family_refresh(conn)


def main(argv=None, conn=None) -> int:
    if conn is None:
        sys.path.insert(0, str(ROOT))
        from shared.env_guard import require_db_driver
        require_db_driver("verify_family_refresh.py")
        try:
            from shared.db import get_connection
            conn = get_connection()
        except Exception as exc:
            print(f"cannot read the database: {type(exc).__name__}: {str(exc)[:160]}", file=sys.stderr)
            return RC_NO_DB
    result = analyze(*load(conn))
    print(render(result))
    return exit_code(result)


if __name__ == "__main__":
    sys.exit(main())
