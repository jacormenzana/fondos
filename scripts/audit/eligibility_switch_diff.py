#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
eligibility_switch_diff.py -- read-only ON/OFF comparison of the build-time data-eligibility rule (FND-0251, Commit B).

    python scripts/audit/eligibility_switch_diff.py [--regime Shock_Energetico] [--out diff.csv]

Scores the SAME persisted inputs twice with the pure scoring core (nothing is persisted, nothing is written):
  OFF  today's behaviour;
  ON   FUND_DATA_ELIGIBILITY_ENABLED = True: a fund with a monthly NAV older than P3_FUND_NAV_MAX_AGE_DAYS, or with an ACTIVE accepted
       audit residual (p2_metrics), is ineligible (exclusion_reason nav_stale / accepted_residual).
and prints, per sub-portfolio block, which (fund, block) pairs become ineligible and why, and which only change the family representative
(fund_scorer.deduplicate_by_family keeps one class per family and block, so excluding a class hands the slot to a sibling). The switch
ships OFF: the owner turns it on only after reading this output. The exit code is 0 whatever the difference (information, not a gate).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _isin_series(res: pd.DataFrame) -> pd.Series:
    return res["isin"] if "isin" in res.columns else res.index.to_series()


def diff_eligibility(res_off: pd.DataFrame, res_on: pd.DataFrame) -> pd.DataFrame:
    """(isin, subportfolio) pairs whose result differs between two scoring results. Columns: isin, subportfolio, change, eligible_off,
    eligible_on, exclusion_reason_on, fund_name. `change` is
      excluded     the fund is in both results and the rule made it ineligible;
      family_out   the fund was the family's representative OFF and is not ON (a sibling class took over);
      family_in    the fund is the family's representative only ON (it took over from an excluded sibling).
    The last two are not exclusions: deduplicate_by_family keeps one class per (family, sub-portfolio), so excluding a class moves the
    representative. Empty frame when nothing changes. Pure."""
    cols = ["isin", "subportfolio", "change", "eligible_off", "eligible_on", "exclusion_reason_on", "fund_name"]
    if res_off.empty and res_on.empty:
        return pd.DataFrame(columns=cols)

    def keyed(res, suffix):
        r = pd.DataFrame({"isin": _isin_series(res).values, "subportfolio": res["subportfolio"].values,
                          f"eligible_{suffix}": res["eligible"].astype(bool).values})
        r["name_" + suffix] = res["fund_name"].values if "fund_name" in res.columns else ""
        if suffix == "on":
            r["exclusion_reason_on"] = res["exclusion_reason"].values
        return r.drop_duplicates(["isin", "subportfolio"])

    m = keyed(res_off, "off").merge(keyed(res_on, "on"), on=["isin", "subportfolio"], how="outer", indicator=True)
    in_off, in_on = m["_merge"] != "right_only", m["_merge"] != "left_only"
    m["eligible_off"] = m["eligible_off"].astype("boolean").fillna(False).astype(bool)
    m["eligible_on"] = m["eligible_on"].astype("boolean").fillna(False).astype(bool)
    m["change"] = ""
    m.loc[in_off & in_on & (m["eligible_off"] != m["eligible_on"]), "change"] = "excluded"
    m.loc[~in_on & m["eligible_off"], "change"] = "family_out"
    m.loc[~in_off & m["eligible_on"], "change"] = "family_in"
    m["fund_name"] = m["name_on"].where(in_on, m["name_off"])
    out = m[m["change"] != ""][cols]
    return out.sort_values(["subportfolio", "change", "isin"]).reset_index(drop=True)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--regime", default=None, help="default: the regime of the latest persisted scoring")
    ap.add_argument("--out", default=None, help="write the differing pairs to this CSV")
    args = ap.parse_args(argv)

    from shared import config as cfg
    from shared.db import get_connection
    from proyecto3.src import fund_scorer as fs

    conn = get_connection()
    try:
        regime = args.regime
        if regime is None:
            row = conn.execute("SELECT regime FROM fund_scores WHERE regime IS NOT NULL ORDER BY calculated_at DESC LIMIT 1").fetchone()
            if row is None:
                print("error: no persisted scoring to take the regime from; pass --regime", file=sys.stderr)
                return 2
            regime = row[0]
        saved = cfg.FUND_DATA_ELIGIBILITY_ENABLED
        try:
            cfg.FUND_DATA_ELIGIBILITY_ENABLED = False
            df = fs.load_fund_metrics_for_scoring(conn, regime=regime)
            inputs = fs.load_eligibility_inputs(conn)
            res_off = fs.score_funds_from_df(df, regime, verbose=False)
            cfg.FUND_DATA_ELIGIBILITY_ENABLED = True
            res_on = fs.score_funds_from_df(df.join(inputs[fs.ELIGIBILITY_COLUMNS], how="left"), regime, verbose=False)
        finally:
            cfg.FUND_DATA_ELIGIBILITY_ENABLED = saved
        conn.rollback()

        diff = diff_eligibility(res_off, res_on)
        print(f"regime={regime}; limit P3_FUND_NAV_MAX_AGE_DAYS={cfg.P3_FUND_NAV_MAX_AGE_DAYS}; funds scored={df.shape[0]}")
        for block, g in res_off.groupby("subportfolio"):
            n_off = int(g["eligible"].astype(bool).sum())
            n_on = int(res_on[res_on["subportfolio"] == block]["eligible"].astype(bool).sum())
            print(f"  {block:12s} eligible OFF={n_off:5d}  ON={n_on:5d}  delta={n_on - n_off:+d}")
        ex = diff[diff["change"] == "excluded"]
        fam = diff[diff["change"] != "excluded"]
        if diff.empty:
            print("\nEMPTY DIFF: the switch changes no (fund, block) eligibility on today's inputs.")
        else:
            print(f"\n{len(ex)} (fund, block) pairs become INELIGIBLE ({ex['isin'].nunique()} funds):")
            for _, r in ex.iterrows():
                print(f"  {r['subportfolio']:12s} {r['isin']}  {str(r['fund_name'])[:44]:44s} -> {r['exclusion_reason_on']}")
            if len(fam):
                print(f"{len(fam)} more pairs only change the family representative (a sibling class takes over; not an exclusion):")
                for _, r in fam.iterrows():
                    print(f"  {r['subportfolio']:12s} {r['isin']}  {str(r['fund_name'])[:44]:44s} {r['change']}")
        prod = conn.execute("SELECT scenario_id FROM portfolio_scenarios WHERE status = 'PRODUCTION'").fetchone()
        if prod is not None:
            held = {r[0] for r in conn.execute("SELECT isin FROM portfolio_weights WHERE scenario_id = %s", (prod[0],)).fetchall()}
            hit = sorted(set(diff["isin"]) & held)
            print(f"\nfunds of the PRODUCTION scenario {prod[0]} that the switch would exclude or replace: {len(hit)}"
                  + (f" {hit}" if hit else ""))
        conn.rollback()
        if args.out:
            diff.to_csv(args.out, index=False)
            print(f"written: {args.out}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
