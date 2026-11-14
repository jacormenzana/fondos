#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
family_key_switch_diff.py -- read-only OFF/ON comparison of the family-of-share-classes key (FND-0246, OFFICIAL_NAME_FAMILY_KEY_ENABLED).

    python scripts/audit/family_key_switch_diff.py [--regime Shock_Energetico] [--samples 25] [--out diff.csv]

Computes the families of the SAME persisted fund_master twice with the one definition of the key (fund_family_builder.compute_family_groups):
  OFF  today's key: (management company, normalised catalogue fund_name);
  ON   (management company, official sub-fund name from the KIID head), the catalogue key as the fallback for classes whose head has no name.
and prints what the switch would change BEFORE the owner turns it on:
  * merges      ON families that span several OFF families (the abbreviation splits this ticket exists to fix), with their member names;
  * splits      OFF families whose members the ON key separates (e.g. currency sub-funds that the catalogue key stripped to one stem);
  * nature      ON families whose ACTIVE members carry different Fund_Nature: the builder's family arbitration (rule 4) would act on them and
                `family_refresh_pending` would hold P3 until `run_block.py --family-nature-refresh` runs;
  * P3          the live scorer run twice on the same inputs with fund_family_id swapped in memory: secondary classes dropped by
                `fund_scorer.deduplicate_by_family` OFF vs ON, and (fund, sub-portfolio) representatives that change;
  * PRODUCTION  held funds that would collide in one family (the global `families_used` exclusion of the portfolio builder) or lose their slot.
Nothing is persisted or written (a CSV only with --out). The exit code is 0 whatever the difference: information, not a gate.
"""
from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "proyecto1")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def family_map(groups: dict) -> dict:
    """{isin: family_id}, ids issued exactly as the builder issues them (sorted keys, FAM_000001...)."""
    out = {}
    for n, (_key, isins) in enumerate(sorted(groups.items()), start=1):
        for isin in isins:
            out[isin] = f"FAM_{n:06d}"
    return out


def diff_partitions(off: dict, on: dict) -> dict:
    """Pure. off/on = {isin: family_id}. Returns {"merges": {on_family: sorted(off_families)}, "splits": {off_family: sorted(on_families)}} with only the
    families that span more than one on the other side. The ids are renumbered on every build, so families are compared by their members only."""
    off_of_on, on_of_off = collections.defaultdict(set), collections.defaultdict(set)
    for isin, fam_on in on.items():
        fam_off = off.get(isin)
        if fam_off is not None:
            off_of_on[fam_on].add(fam_off)
            on_of_off[fam_off].add(fam_on)
    return {"merges": {f: sorted(s) for f, s in off_of_on.items() if len(s) > 1},
            "splits": {f: sorted(s) for f, s in on_of_off.items() if len(s) > 1}}


def nature_conflicts(on: dict, nature: dict, active: set) -> dict:
    """Pure. {on_family: sorted natures} for the ON families whose ACTIVE members hold more than one Fund_Nature."""
    by_family = collections.defaultdict(set)
    for isin, fam in on.items():
        if isin in active and nature.get(isin):
            by_family[fam].add(nature[isin])
    return {f: sorted(s) for f, s in by_family.items() if len(s) > 1}


def representatives(res) -> set:
    """Pure. {(isin, subportfolio)} of a scoring result (already de-duplicated by family)."""
    if res is None or len(res) == 0:
        return set()
    isins = res["isin"] if "isin" in res.columns else res.index.to_series()
    return set(zip(isins.tolist(), res["subportfolio"].tolist()))


def held_collisions(held: list, fam: dict) -> dict:
    """Pure. {family: [held isins]} for the families holding more than one fund of the scenario (the portfolio builder allows one per family)."""
    by_family = collections.defaultdict(list)
    for isin in held:
        if isin in fam:
            by_family[fam[isin]].append(isin)
    return {f: sorted(v) for f, v in by_family.items() if len(v) > 1}


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--regime", default=None, help="default: the regime of the latest persisted scoring")
    ap.add_argument("--samples", type=int, default=25, help="merge / split / conflict examples to print")
    ap.add_argument("--out", default=None, help="write the merges and splits to this CSV")
    args = ap.parse_args(argv)

    import pandas as pd
    from shared import config as cfg
    from shared.db import get_connection
    from proyecto1.core.fund_family_builder import compute_family_groups
    from proyecto3.src import fund_scorer as fs

    conn = get_connection()
    try:
        rows = [tuple(r) for r in conn.execute(
            "SELECT ISIN, Fund_Name, Management_Company, official_fund_name, Fund_Nature, In_Current_Universe FROM fund_master "
            "WHERE Fund_Name IS NOT NULL ORDER BY Management_Company, Fund_Name").fetchall()]
        builder_rows = [(r[0], r[1], r[2], r[3]) for r in rows]
        name = {r[0]: r[1] for r in rows}
        nature = {r[0]: r[4] for r in rows}
        active = {r[0] for r in rows if r[5] == 1}
        n_named = sum(1 for r in rows if r[3])
        off_groups = compute_family_groups(builder_rows, use_official=False)
        on_groups = compute_family_groups(builder_rows, use_official=True)
        off, on = family_map(off_groups), family_map(on_groups)

        print(f"fund_master rows={len(rows)} (active={len(active)}); with an official name={n_named} ({n_named / max(len(rows), 1):.1%})")
        print(f"families  OFF={len(off_groups)}  ON={len(on_groups)}  delta={len(on_groups) - len(off_groups):+d}")
        d = diff_partitions(off, on)
        members = collections.defaultdict(list)
        for isin, fam in on.items():
            members[fam].append(isin)
        members_off = collections.defaultdict(list)
        for isin, fam in off.items():
            members_off[fam].append(isin)

        print(f"\nMERGES: {len(d['merges'])} ON families unify several OFF families (examples, largest first):")
        for fam, offs in sorted(d["merges"].items(), key=lambda kv: -len(kv[1]))[:args.samples]:
            print(f"  {fam}: {len(offs)} OFF families, {len(members[fam])} classes: " + "; ".join(sorted({name[i] for i in members[fam]})[:5]))
        print(f"\nSPLITS: {len(d['splits'])} OFF families are separated by the ON key (examples):")
        for fam, ons in sorted(d["splits"].items(), key=lambda kv: -len(kv[1]))[:args.samples]:
            print(f"  {fam} -> {len(ons)} ON families: " + "; ".join(sorted({name[i] for i in members_off[fam]})[:5]))

        conflicts = nature_conflicts(on, nature, active)
        off_conflicts = nature_conflicts(off, nature, active)
        print(f"\nNATURE: ON families whose active members disagree on Fund_Nature = {len(conflicts)} (OFF today = {len(off_conflicts)}); "
              "the builder's arbitration would act on them and family_refresh_pending would hold P3 until the refresh runs:")
        for fam, natures in list(conflicts.items())[:args.samples]:
            print(f"  {fam} {natures}: " + "; ".join(f"{name[i]} [{nature.get(i)}]" for i in members[fam] if i in active)[:230])

        regime = args.regime
        if regime is None:
            row = conn.execute("SELECT regime FROM fund_scores WHERE regime IS NOT NULL ORDER BY calculated_at DESC LIMIT 1").fetchone()
            regime = row[0] if row else None
        if regime is None:
            print("\nP3: no persisted scoring to take the regime from; pass --regime (skipped)")
        else:
            df = fs.load_fund_metrics_for_scoring(conn, regime=regime)
            res = {}
            for label, fam in (("OFF", off), ("ON", on), ("NONE", {})):
                frame = df.copy()
                frame["fund_family_id"] = [fam.get(i) for i in frame.index]
                res[label] = fs.score_funds_from_df(frame, regime, verbose=False)
            dropped_off = len(res["NONE"]) - len(res["OFF"])
            dropped_on = len(res["NONE"]) - len(res["ON"])
            r_off, r_on = representatives(res["OFF"]), representatives(res["ON"])
            print(f"\nP3 (regime={regime}, funds scored={df.shape[0]}): (fund, sub-portfolio) rows without de-duplication={len(res['NONE'])}")
            print(f"  secondary classes dropped by deduplicate_by_family  OFF={dropped_off}  ON={dropped_on}  delta={dropped_on - dropped_off:+d}")
            print(f"  representatives that change: leave={len(r_off - r_on)}  enter={len(r_on - r_off)}")
            for isin, block in sorted(r_off - r_on)[:args.samples]:
                print(f"    leaves {block:12s} {isin}  {str(name.get(isin))[:40]}")
            for isin, block in sorted(r_on - r_off)[:args.samples]:
                print(f"    enters {block:12s} {isin}  {str(name.get(isin))[:40]}")
            prod = conn.execute("SELECT scenario_id FROM portfolio_scenarios WHERE status = 'PRODUCTION'").fetchone()
            if prod is not None:
                held = [r[0] for r in conn.execute("SELECT isin FROM portfolio_weights WHERE scenario_id = %s", (prod[0],)).fetchall()]
                col_on, col_off = held_collisions(held, on), held_collisions(held, off)
                lose = sorted(isin for isin, block in (r_off - r_on) if isin in set(held))
                print(f"\nPRODUCTION {prod[0]} ({len(held)} funds): families holding > 1 fund  OFF={len(col_off)}  ON={len(col_on)}; "
                      f"held funds that lose their representative slot ON={len(lose)} {lose}")
                for fam, isins in list(col_on.items())[:args.samples]:
                    print(f"  {fam}: " + "; ".join(f"{i} {name.get(i)}" for i in isins))
        conn.rollback()
        if args.out:
            out = [{"kind": "merge", "on_family": f, "off_families": ",".join(v), "classes": "; ".join(sorted({name[i] for i in members[f]}))}
                   for f, v in d["merges"].items()]
            out += [{"kind": "split", "on_family": "", "off_families": f, "classes": "; ".join(sorted({name[i] for i in members_off[f]}))}
                    for f in d["splits"]]
            pd.DataFrame(out).to_csv(args.out, index=False)
            print(f"written: {args.out}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
