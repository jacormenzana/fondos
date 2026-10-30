#!/usr/bin/env python
"""scripts/audit/official_name_coverage.py -- FND-0246 Gate A measurement (read-only, one SELECT, nothing persisted).

Runs core/kiid_official_name.extract_official_fund_name over the stored KIID text of the active funds and reports:
  coverage       funds with a name, by rule (LABEL / ISIN_TRAIL / SUBFUND_CLAUSE / ISIN_HEAD / NONE)
  purity         of the current families (silver.fund_master.fund_family_id) with >= 2 named members, the share whose members all share ONE official-name key
  merges         (management company, official key) groups that span more than one current family: the abbreviation splits the new key would unify
  currency       current families whose members differ only by a currency word in the official name (the new key keeps them apart) and the by-construction
                 check that no official key mixes two currency spellings
  broad keys     the largest (manager, key) groups, to spot a name too generic to be a fund (a regression guard)

Gate A (pre-declared 2026-10-24, owner-approved): coverage >= 85% of the active texts, purity >= 95%, zero currency collisions. Exit 0 = report written
(GATE line says PASS / FAIL), 4 = DB unreadable. Scope: all active class-1 KIID texts, or --isin A,B (a sample).

    C:\\Users\\jacor\\.conda\\envs\\des\\python.exe -X utf8 scripts\\audit\\official_name_coverage.py [--isin A,B] [--out-dir DIR] [--samples N] [--stored]

--stored is Gate B: it reads the persisted fund_master.official_fund_name (needs the FND-0246 migration and a P1 pass) instead of recomputing.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "proyecto1")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

RC_OK, RC_DB = 0, 4
GATE_COVERAGE, GATE_PURITY = 0.85, 0.95
_CCY = {"EUR", "USD", "GBP", "CHF", "JPY", "SEK", "NOK", "DKK", "CAD", "AUD", "SGD", "HKD", "CNH", "PLN"}

_SQL = """SELECT k.isin, m.fund_name, m.management_company, m.fund_family_id, k.raw_kiid_text
          FROM bronze.fund_kiid_metadata k JOIN silver.fund_master m ON m.isin = k.isin
          WHERE k.kiid_class = 1 AND k.raw_kiid_text IS NOT NULL AND m.in_current_universe = 1"""


# Gate B (--stored): the same population, but the name is the PERSISTED fund_master.official_fund_name (what a P1 pass wrote), not a recomputation.
_SQL_STORED = """SELECT k.isin, m.fund_name, m.management_company, m.fund_family_id, m.official_fund_name
          FROM bronze.fund_kiid_metadata k JOIN silver.fund_master m ON m.isin = k.isin
          WHERE k.kiid_class = 1 AND k.raw_kiid_text IS NOT NULL AND m.in_current_universe = 1"""


def _stored_extract(stored_name, _isin):
    return (stored_name, "STORED") if stored_name else (None, "NONE")


def _ccy_tokens(key: str) -> frozenset:
    return frozenset(t.upper() for t in key.split() if t.upper() in _CCY)


def _without_ccy(key: str) -> str:
    return " ".join(t for t in key.split() if t.upper() not in _CCY)


def measure(rows, extract, key_of, samples: int = 12) -> dict:
    """Pure: rows = (isin, fund_name, manager, family_id, text). Returns the report dict."""
    named, rules = {}, collections.Counter()
    meta = {}
    for isin, name, mgr, fam, text in rows:
        meta[isin] = (name, (mgr or "").strip().lower(), fam)
        official, rule = extract(text, isin)
        rules[rule] += 1
        if official:
            named[isin] = (official, key_of(official))
    total = len(rows)
    coverage = len(named) / total if total else 0.0

    fams = collections.defaultdict(list)
    for isin in named:
        fam = meta[isin][2]
        if fam is not None:
            fams[fam].append(isin)
    multi = {f: v for f, v in fams.items() if len(v) >= 2}
    impure = {f: v for f, v in multi.items() if len({named[i][1] for i in v}) > 1}
    purity = (len(multi) - len(impure)) / len(multi) if multi else 1.0

    groups = collections.defaultdict(set)
    members = collections.defaultdict(list)
    for isin, (official, key) in named.items():
        g = (meta[isin][1], key)
        groups[g].add(meta[isin][2])
        members[g].append(isin)
    merges = {g: f for g, f in groups.items() if len(f) > 1}

    # a current family whose members' official keys differ only by currency words: the old key may have merged different sub-funds
    ccy_split = {}
    for f, v in impure.items():
        keys = {named[i][1] for i in v}
        if len({_without_ccy(k) for k in keys}) == 1:
            ccy_split[f] = sorted(keys)
    # by construction no official key mixes two currency spellings (the key keeps the words); verified, not assumed
    mixed_ccy = sum(1 for g, isins in members.items() if len({_ccy_tokens(named[i][1]) for i in isins}) > 1)

    def line(g):
        return {"manager": g[0], "key": g[1], "n_funds": len(members[g]), "n_current_families": len(groups[g]),
                "catalogue_names": sorted({meta[i][0] for i in members[g]})[:5]}

    broad = sorted(members, key=lambda g: -len(members[g]))[:samples]
    return {
        "n_texts": total, "n_named": len(named), "coverage": round(coverage, 4), "by_rule": dict(rules),
        "families_with_2plus_named": len(multi), "families_pure": len(multi) - len(impure), "purity": round(purity, 4),
        "official_groups": len(groups), "current_families_touched": len({meta[i][2] for i in named}),
        "merge_groups": len(merges),
        "merge_samples": [line(g) for g in sorted(merges, key=lambda g: -len(merges[g]))[:samples]],
        "impure_family_samples": [{"family": f, "names": sorted({named[i][0] for i in v})[:5]} for f, v in list(impure.items())[:samples]],
        "currency_only_split_families": len(ccy_split), "currency_only_samples": dict(list(ccy_split.items())[:samples]),
        "currency_collisions": mixed_ccy,
        "largest_groups": [line(g) for g in broad],
        "unnamed_samples": [{"isin": i, "fund_name": meta[i][0]} for i in [x[0] for x in rows if x[0] not in named][:samples]],
        "gate": {"coverage_ok": coverage >= GATE_COVERAGE, "purity_ok": purity >= GATE_PURITY, "currency_ok": mixed_ccy == 0},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--isin", default="", help="comma-separated ISINs (sample); default = every active class-1 text")
    ap.add_argument("--out-dir", default="", help="default: shared.config.AUDIT_DIRS['p1']")
    ap.add_argument("--samples", type=int, default=12)
    ap.add_argument("--stored", action="store_true", help="Gate B: measure the persisted fund_master.official_fund_name instead of recomputing from the text")
    a = ap.parse_args(argv)
    try:
        import shared.config as cfg
        from shared.db import get_connection
        from core.kiid_official_name import extract_official_fund_name, official_name_key
        conn = get_connection()
        try:
            isins = [s.strip() for s in a.isin.split(",") if s.strip()]
            rows = conn.execute((_SQL_STORED if a.stored else _SQL) + (" AND k.isin = ANY(%s)" if isins else ""), (isins,) if isins else ()).fetchall()
            rows = [tuple(r) for r in rows]
        finally:
            conn.rollback()
            conn.close()
    except Exception as exc:                                   # a measurement that cannot read is not a pass
        print(f"[ERROR] official-name coverage unreadable: {type(exc).__name__}: {exc}")
        return RC_DB
    rep = measure(rows, _stored_extract if a.stored else extract_official_fund_name, official_name_key, a.samples)
    rep["scope"] = ("SAMPLE" if a.isin else "FULL") + ("/STORED" if a.stored else "")
    out_dir = Path(a.out_dir) if a.out_dir else cfg.AUDIT_DIRS["p1"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"official_name_coverage_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    g = rep["gate"]
    print(f"texts={rep['n_texts']} named={rep['n_named']} coverage={rep['coverage']:.1%} (gate >= {GATE_COVERAGE:.0%}) by_rule={rep['by_rule']}")
    print(f"families with >=2 named: {rep['families_with_2plus_named']} pure={rep['families_pure']} purity={rep['purity']:.1%} (gate >= {GATE_PURITY:.0%})")
    print(f"official groups={rep['official_groups']} over {rep['current_families_touched']} current families; merge groups (span >1 current family)={rep['merge_groups']}")
    print(f"currency: families split only by a currency word={rep['currency_only_split_families']}; official keys mixing currency spellings={rep['currency_collisions']} (gate 0)")
    print(f"report: {out}")
    print("GATE A: " + ("PASS" if all(g.values()) else "FAIL " + ", ".join(k for k, v in g.items() if not v)) + f"  [{rep['scope']}]")
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
