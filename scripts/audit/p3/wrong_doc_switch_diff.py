#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
wrong_doc_switch_diff.py -- read-only OFF/ON validation of FND-0323 D1 (P3_WRONG_DOC_EXCLUSION_ENABLED) on a deterministic 40-ISIN sample.

    python scripts/audit/wrong_doc_switch_diff.py [--regime Shock_Energetico] [--pit-start 2022-01-31] [--out report.txt]

Owner HARD RULE: never the full population. The sample is every active WRONG_DOC fund that has a monthly NAV (up to SAMPLE_WD) plus
non-WRONG_DOC controls in md5 order, 40 in all. Nothing is written to the database; the switch is flipped in THIS process only and put back.

  LIVE  fund_scorer.load_fund_metrics_for_scoring (the real loader) + score_funds_from_df, OFF vs ON.
  PIT   Backtester.run_pit(isins=sample, use_cache=False), OFF vs ON, from --pit-start: the same rule through the scorer's check_hard_filters.

Checks (each prints PASS/FAIL; exit 1 when any fails):
  L1/P1  the (fund, block) pairs that become ineligible belong ONLY to sample WRONG_DOC funds, and there is at least one when such a fund is scored;
  L2/P2  every exclusion carries shared.doc_eligibility.REASON_TEXT;
  L3/P3  every other fund keeps the same eligibility and the same score_final (normalisation does not move);
  L4     the loader with the switch OFF returns no doc_status_reason column (the frame is what it was before).
Funds that only change the family representative (a sibling class takes the slot) are listed as information, not as a failure.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SAMPLE_SIZE = 40
SAMPLE_WD = 10

# Literal SQL (the EXPLAIN sweep needs to see it). WRONG_DOC funds with a monthly NAV first, then controls with metrics and NAV, both by md5.
Q_SAMPLE = """
    (SELECT fm.isin, 'WRONG_DOC' AS kind, md5(fm.isin) AS h
       FROM fund_master fm
      WHERE fm.in_current_universe = 1
        AND EXISTS (SELECT 1 FROM fund_kiid_metadata k WHERE k.isin = fm.isin AND k.kiid_status = 'WRONG_DOC')
        AND EXISTS (SELECT 1 FROM fund_nav_monthly n WHERE n.isin = fm.isin)
      ORDER BY h LIMIT 10)
    UNION ALL
    (SELECT fm.isin, 'CONTROL' AS kind, md5(fm.isin) AS h
       FROM fund_master fm
      WHERE fm.in_current_universe = 1
        AND NOT EXISTS (SELECT 1 FROM fund_kiid_metadata k WHERE k.isin = fm.isin AND k.kiid_status = 'WRONG_DOC')
        AND EXISTS (SELECT 1 FROM fund_nav_monthly n WHERE n.isin = fm.isin)
        AND EXISTS (SELECT 1 FROM fund_metrics m WHERE m.isin = fm.isin AND m.metric = 'sharpe')
      ORDER BY h LIMIT 40)
"""


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def pick_sample(rows: list) -> tuple[list, list]:
    """(wrong_doc_isins, all_sample_isins) from Q_SAMPLE rows (isin, kind, h): every WRONG_DOC row (the query caps it), then controls to SAMPLE_SIZE."""
    wd = [r[0] for r in rows if r[1] == "WRONG_DOC"][:SAMPLE_WD]
    controls = [r[0] for r in rows if r[1] == "CONTROL"][: SAMPLE_SIZE - len(wd)]
    return wd, wd + controls


def compare_scores(off: pd.DataFrame, on: pd.DataFrame, wd: set, reason: str, keys: list) -> dict:
    """Pure. `off`/`on` are two scoring results (long, one row per key). Returns the facts the checks need:
       newly_ineligible  keys that were eligible OFF and are not ON;      foreign   of those, the ones whose isin is not a WRONG_DOC fund;
       bad_reason        newly ineligible rows whose reason differs;       moved     rows of non-WRONG_DOC funds present in both whose eligible/score_final differ;
       family_only       keys present in one result only (a sibling class took the family slot)."""
    m = off.merge(on, on=keys, how="outer", suffixes=("_off", "_on"), indicator=True)
    both = m[m["_merge"] == "both"].copy()
    off_ok = both["eligible_off"].astype("boolean").fillna(False).astype(bool)
    on_ok = both["eligible_on"].astype("boolean").fillna(False).astype(bool)
    newly = both[off_ok & ~on_ok]
    foreign = newly[~newly["isin"].isin(wd)]
    bad_reason = newly[newly["exclusion_reason_on"] != reason]
    rest = both[~both["isin"].isin(wd)]
    moved = rest[(rest["eligible_off"].astype(bool) != rest["eligible_on"].astype(bool)) | ((rest["score_final_off"] - rest["score_final_on"]).abs() > 1e-12)]
    return {"newly_ineligible": newly, "foreign": foreign, "bad_reason": bad_reason, "moved": moved,
            "family_only": m[m["_merge"] != "both"]}


def _verdicts(tag: str, facts: dict, wd_scored: bool) -> list:
    out = [(f"{tag}1 only WRONG_DOC funds become ineligible" + (" (and at least one does)" if wd_scored else ""),
            facts["foreign"].empty and (not wd_scored or len(facts["newly_ineligible"]) > 0)),
           (f"{tag}2 every exclusion carries the shared reason", facts["bad_reason"].empty),
           (f"{tag}3 no other fund changes eligibility or score_final", facts["moved"].empty)]
    return out


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--regime", default=None, help="default: the regime of the latest persisted scoring")
    ap.add_argument("--pit-start", default="2022-01-31")
    ap.add_argument("--pit-end", default=None)
    ap.add_argument("--no-pit", action="store_true", help="skip the PIT half (live only)")
    ap.add_argument("--out", default=None, help="also write this report to a text file")
    args = ap.parse_args(argv)

    from shared import config as cfg
    from shared import doc_eligibility as de
    from shared.db import get_connection
    from proyecto3.src import fund_scorer as fs

    sw = _load_module(ROOT / "scripts" / "audit" / "eligibility_switch_diff.py", "eligibility_switch_diff")   # reuse nothing but its pure helpers
    lines: list = []

    def say(s: str = "") -> None:
        print(s)
        lines.append(s)

    verdicts: list = []
    conn = get_connection()
    saved = cfg.P3_WRONG_DOC_EXCLUSION_ENABLED
    try:
        wd_list, sample = pick_sample([tuple(r) for r in conn.execute(Q_SAMPLE).fetchall()])
        wd = set(wd_list)
        say(f"sample: {len(sample)} ISINs = {len(wd_list)} WRONG_DOC + {len(sample) - len(wd_list)} controls (md5 order); WRONG_DOC: {', '.join(sorted(wd))}")

        regime = args.regime
        if regime is None:
            row = conn.execute("SELECT regime FROM fund_scores WHERE regime IS NOT NULL ORDER BY calculated_at DESC LIMIT 1").fetchone()
            if row is None:
                say("error: no persisted scoring to take the regime from; pass --regime")
                return 2
            regime = row[0]

        # ---------------- LIVE ----------------
        say(f"\n[LIVE] regime={regime}")
        cfg.P3_WRONG_DOC_EXCLUSION_ENABLED = False
        df_off = fs.load_fund_metrics_for_scoring(conn, regime=regime)
        cfg.P3_WRONG_DOC_EXCLUSION_ENABLED = True
        df_on = fs.load_fund_metrics_for_scoring(conn, regime=regime)
        verdicts.append(("L4 OFF loader has no doc_status_reason column", "doc_status_reason" not in df_off.columns))
        s_off, s_on = df_off.loc[df_off.index.intersection(sample)], df_on.loc[df_on.index.intersection(sample)]
        wd_scored = bool(wd & set(s_on.index))
        say(f"  scored frame: {len(s_on)} of {len(sample)} sample funds have metrics; WRONG_DOC with metrics: {sorted(wd & set(s_on.index))}")
        cfg.P3_WRONG_DOC_EXCLUSION_ENABLED = False
        res_off = fs.score_funds_from_df(s_off, regime, verbose=False)
        cfg.P3_WRONG_DOC_EXCLUSION_ENABLED = True
        res_on = fs.score_funds_from_df(s_on, regime, verbose=False)
        facts = compare_scores(res_off, res_on, wd, de.REASON_TEXT, ["isin", "subportfolio"])
        for _, r in facts["newly_ineligible"].iterrows():
            say(f"  excluded  {r['subportfolio']:12s} {r['isin']}  -> {r['exclusion_reason_on']}")
        say(f"  family-representative changes (information): {len(facts['family_only'])}")
        verdicts += _verdicts("L", facts, wd_scored)

        # ---------------- PIT ----------------
        if not args.no_pit:
            from proyecto3.src.backtesting import Backtester
            say(f"\n[PIT] run_pit(isins=sample, use_cache=False) from {args.pit_start}")
            runs = {}
            for flag in (False, True):
                cfg.P3_WRONG_DOC_EXCLUSION_ENABLED = flag
                bt = Backtester(conn)
                bt.run_pit(start_date=args.pit_start, end_date=args.pit_end, isins=sample, use_cache=False)
                runs[flag] = bt.last_pit_run.scores.copy()
            p_off, p_on = runs[False], runs[True]
            say(f"  PIT scores rows: OFF={len(p_off)} ON={len(p_on)}; dates={p_on['as_of'].nunique() if len(p_on) else 0}")
            pf = compare_scores(p_off, p_on, wd, de.REASON_TEXT, ["as_of", "isin", "subportfolio"])
            n_fd = int(len(pf["newly_ineligible"]))
            say(f"  (date, fund, block) rows that become ineligible: {n_fd} across {pf['newly_ineligible']['isin'].nunique()} funds"
                f"; family-representative changes (information): {len(pf['family_only'])}")
            verdicts += _verdicts("P", pf, bool(wd & set(p_on["isin"]) if len(p_on) else False))
        conn.rollback()
    finally:
        cfg.P3_WRONG_DOC_EXCLUSION_ENABLED = saved
        conn.close()

    say("\nRESULTS")
    for label, ok in verdicts:
        say(f"  {'PASS' if ok else 'FAIL'}  {label}")
    allok = all(ok for _, ok in verdicts)
    say(f"\nOVERALL: {'PASS' if allok else 'FAIL'} ({sum(ok for _, ok in verdicts)}/{len(verdicts)})")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"written: {args.out}")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
