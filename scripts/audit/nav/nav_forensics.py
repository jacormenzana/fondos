# -*- coding: utf-8 -*-
"""Per-ISIN NAV forensic report for the funds the statistical audit flags as extreme (FND-0393, WS1 of FND-0391; plan amendments A3 G-13 and A5).

    python scripts/audit/nav/nav_forensics.py --from-run post_20261108_173304_p2 --min-dev 50          # every fund whose |MAD-Z| reached 50 in that audit
    python scripts/audit/nav/nav_forensics.py --isins DE000DWS3LN1 LU0232465467 --run <audit run id>   # named funds

READ-ONLY (a read-only session: the server refuses any write). Generic: no fund is named in code (P#5), so it serves every fund the engine flags in future, which turns "one person reads 22 NAV series" into
"review a report". Per fund it answers four questions, each with a number:
  1. What do the validators say today?       validate_nav (8x step), validate_nav_stale_flat (12 flat months then 40%), validate_nav_unshared_jump (one-month move its Fund_Nature peers did not make).
  2. What does the NAV itself show?          the three largest one-month moves with the peer median and how many peers moved the same way, the longest run of identical NAVs, the largest step ratio, volatility.
  3. How extreme is the fund WITHIN its nature? the robust z of its most extreme metric against the funds of its own Fund_Nature, next to the universe-wide z the audit used. A score that collapses inside its
     nature is baseline pollution (a money-market or bond fund judged against equity), not a data defect (plan G-03).
  4. Which class is it?                      VERDICTS below, from those numbers only; the rule of each is stated, never a hidden judgement.
Output: a JSON and a Markdown table under out/audit/P2/ (shared.config.AUDIT_DIRS['p2']) unless --out-dir says otherwise.

Verdicts (first match wins; 2026-11-08 finding on the 22 funds of audit post_20261108_173304_p2: 1 NAV defect, the rest sampling or ratio artifacts, see FND-0393):
  NAV_DEFECT_GUARDED     a current validator already rejects the series (it is quarantined or will be on the next pass)
  LEVEL_SHIFT_UNSHARED   a one-month move of at least the unshared-jump cut that its peers did not make, and no validator rejects it (the gap WS0.5 closes in SHADOW)
  SMALL_SAMPLE_METRIC    no NAV defect, but its most extreme metric rests on fewer observations (`fund_metrics.source_rows`) than the 10th percentile AND than half the median of the same metric in its nature: the score is a sampling
                         artifact (e.g. a 3-year rolling Sharpe slope on 3 points), a gap in P2's minimum-observation floor, not a corrupt NAV
  LOW_VOLATILITY_RATIO   no NAV defect, adequate sample, a Sharpe/Sortino-type metric, and the fund's volatility is below the 10th percentile of its nature: the ratio explodes with a tiny denominator
  BASELINE_ARTIFACT      none of the above and the within-nature |z| is below WITHIN_NATURE_MAX (default 20) while the universe |z| is at least 50: the extreme score is the baseline's, not the fund's
  REVIEW                 none of the above: extreme against its own peers with an adequate sample (genuine, smoothed NAV, mis-classified nature or an unmodelled defect)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
for _p in (_ROOT, _ROOT / "proyecto2"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from shared import config as _cfg                                                     # noqa: E402
from shared.data_suspect import robust_z                                              # noqa: E402,F401  (P#11: one definition, shared with the predicate)
from shared.db import get_connection                                                  # noqa: E402
from shared.family_scope import load_family_map                                       # noqa: E402
from src.readers.db_readers import load_nav, load_peer_returns                        # noqa: E402
from src.utils.peer_returns import one_month_returns                                  # noqa: E402
from src.utils.validators import find_unshared_jumps, validate_nav, validate_nav_stale_flat   # noqa: E402

WITHIN_NATURE_MAX = 20.0


def longest_flat_run(nav_df: pd.DataFrame) -> dict:
    """The longest run of identical consecutive NAV values: {length, value, ends}. Length 1 = no repeated value."""
    vals = nav_df["nav"].to_numpy(dtype=float)
    best = (1, 0)
    run = 1
    for i in range(1, len(vals)):
        run = run + 1 if vals[i] == vals[i - 1] else 1
        if run > best[0]:
            best = (run, i)
    n, i = best
    return {"length": int(n), "value": float(vals[i]) if len(vals) else None, "ends": str(nav_df["date"].iloc[i])[:7] if len(vals) else None}


def max_step_ratio(nav_df: pd.DataFrame) -> float:
    vals = nav_df["nav"].to_numpy(dtype=float)
    if len(vals) < 2:
        return 1.0
    r = vals[1:] / vals[:-1]
    return float(max(r.max(), 1.0 / r.min()))


def top_moves(nav_df, peers, isin, k=3) -> list:
    """The k largest one-month moves (consecutive months only) with the peer median, peer count and the share of peers that moved the same way by at least half as much."""
    rets = one_month_returns(nav_df).dropna()
    out = []
    for month, r in rets.reindex(rets.abs().sort_values(ascending=False).index)[:k].items():
        arr = peers.peers_at(isin, month)
        share = float(((np.sign(arr) == np.sign(r)) & (np.abs(arr) >= 0.5 * abs(r))).mean()) if len(arr) else None
        out.append({"month": str(month)[:7], "ret": round(float(r), 4), "peer_median": None if not len(arr) else round(float(np.median(arr)), 4),
                    "n_peers": int(len(arr)), "peer_share": None if share is None else round(share, 3)})
    return out


def parse_group_key(group_key: str):
    """'metric|horizon|real_flag|version' -> (metric, horizon, real_flag, version); None when it is not a fund_metrics key (e.g. the three-part rolling snapshot keys)."""
    parts = str(group_key).split("|")
    return (parts[0], parts[1], int(parts[2]), parts[3]) if len(parts) == 4 and parts[2] in ("0", "1") else None


def verdict(validators: dict, jumps: list, z_nature, z_universe, within_max=WITHIN_NATURE_MAX, source_rows=None, rows_p10=None, rows_p50=None,
            ratio_metric=False, vol=None, vol_p10=None) -> str:
    if any(not ok for ok, _ in validators.values()):
        return "NAV_DEFECT_GUARDED"
    if jumps:
        return "LEVEL_SHIFT_UNSHARED"
    if source_rows is not None and rows_p10 is not None and source_rows < rows_p10 and (rows_p50 is None or source_rows < 0.5 * rows_p50):
        return "SMALL_SAMPLE_METRIC"
    if ratio_metric and vol is not None and vol_p10 is not None and vol < vol_p10:
        return "LOW_VOLATILITY_RATIO"
    if z_nature is not None and abs(z_nature) < within_max and z_universe is not None and abs(z_universe) >= 50:
        return "BASELINE_ARTIFACT"
    return "REVIEW"


def forensic(conn, peers, isin: str, run_id: str, family_of: dict) -> dict:
    m = conn.execute("SELECT fund_nature, in_current_universe, left(fund_name, 48) FROM silver.fund_master WHERE isin = %s", (isin,)).fetchone()
    nature, in_universe, name = (m[0], m[1], m[2]) if m else (None, None, None)
    st = conn.execute("SELECT data_status FROM control.nav_sources WHERE isin = %s", (isin,)).fetchone()
    nav = load_nav(conn, isin)
    rec = {"isin": isin, "name": name, "nature": nature, "in_universe": in_universe, "nav_status": st[0] if st else None, "family": family_of.get(isin), "n_rows": int(len(nav))}
    if len(nav) >= 2:
        rec["span"] = f"{str(nav['date'].iloc[0])[:7]}..{str(nav['date'].iloc[-1])[:7]}"
        rets = one_month_returns(nav).dropna()
        rec["vol_ann"] = round(float(rets.std(ddof=1) * np.sqrt(12)), 4) if len(rets) > 2 else None
        rec["flat_run"] = longest_flat_run(nav)
        rec["max_step_ratio"] = round(max_step_ratio(nav), 3)
        rec["top_moves"] = top_moves(nav, peers, isin)
    validators = {"validate_nav": validate_nav(nav) if len(nav) else (True, None),
                  "validate_nav_stale_flat": validate_nav_stale_flat(nav, enabled=True) if len(nav) else (True, None)}
    jumps = find_unshared_jumps(nav, peers, isin) if len(nav) >= 2 else []
    validators["validate_nav_unshared_jump"] = (not jumps, None if not jumps else f"{jumps[0]['ret']:+.0%} in {str(jumps[0]['month'])[:7]}")
    rec["validators"] = {k: ("PASS" if ok else f"REJECT: {msg}") for k, (ok, msg) in validators.items()}
    # the audit's own view: its most extreme MAD-Z finding and the alarms of the fund in this run
    f = conn.execute("SELECT group_key, distance FROM control.audit_finding WHERE run_id = %s AND domain = 'p2_metrics' AND isin = %s AND rule_id = 'OUTLIER_MAD_Z' "
                     "ORDER BY abs(distance) DESC NULLS LAST LIMIT 1", (run_id, isin)).fetchone()
    n_alarm = conn.execute("SELECT count(*) FROM control.audit_finding WHERE run_id = %s AND domain = 'p2_metrics' AND isin = %s AND severity = 'ALARM'", (run_id, isin)).fetchone()[0]
    rec["alarms"] = int(n_alarm)
    z_nat = z_uni = None
    if f and parse_group_key(f[0]):
        metric, horizon, real, ver = parse_group_key(f[0])
        rows = conn.execute("SELECT m.isin, f.fund_nature, m.value FROM fund_metrics m JOIN silver.fund_master f ON f.isin = m.isin WHERE f.in_current_universe = 1 "
                            "AND m.metric = %s AND m.horizon = %s AND m.real_flag = %s AND m.metric_version = %s AND m.value IS NOT NULL", (metric, horizon, real, ver)).fetchall()
        mine = next((r[2] for r in rows if r[0] == isin), None)
        if mine is not None:
            z_uni = robust_z(mine, [r[2] for r in rows])
            z_nat = robust_z(mine, [r[2] for r in rows if r[1] == nature])
        sr = conn.execute("SELECT source_rows FROM fund_metrics WHERE isin = %s AND metric = %s AND horizon = %s AND real_flag = %s AND metric_version = %s", (isin, metric, horizon, real, ver)).fetchone()
        qs = conn.execute("SELECT percentile_disc(0.1) WITHIN GROUP (ORDER BY m.source_rows), percentile_disc(0.5) WITHIN GROUP (ORDER BY m.source_rows) FROM fund_metrics m "
                          "JOIN silver.fund_master f ON f.isin = m.isin WHERE f.in_current_universe = 1 AND f.fund_nature = %s AND m.metric = %s AND m.horizon = %s AND m.real_flag = %s "
                          "AND m.metric_version = %s AND m.source_rows IS NOT NULL", (nature, metric, horizon, real, ver)).fetchone()
        src_rows, p10_rows = (sr[0] if sr else None), qs[0]
        ratio_metric = metric.startswith(("sharpe", "sortino"))
        vol_p10 = None
        if ratio_metric:
            vrows = [r[1] for r in conn.execute("SELECT m.isin, m.value FROM fund_metrics m JOIN silver.fund_master f ON f.isin = m.isin WHERE f.in_current_universe = 1 AND f.fund_nature = %s "
                                                "AND m.metric = 'vol_ann' AND m.horizon = 'since_inception' AND m.real_flag = 0 AND m.value IS NOT NULL", (nature,)).fetchall()]
            vol_p10 = float(np.percentile(vrows, 10)) if len(vrows) >= 10 else None
        rec["top_metric"] = {"key": f[0], "audit_dev": None if f[1] is None else round(float(f[1]), 1), "value": mine,
                             "z_universe": None if z_uni is None else round(z_uni, 1), "z_within_nature": None if z_nat is None else round(z_nat, 1),
                             "n_in_nature": int(sum(1 for r in rows if r[1] == nature)), "source_rows": src_rows, "source_rows_p10_in_nature": p10_rows, "source_rows_p50_in_nature": qs[1]}
        rec["verdict"] = verdict(validators, jumps, z_nat, z_uni, source_rows=src_rows, rows_p10=p10_rows, rows_p50=qs[1], ratio_metric=ratio_metric, vol=rec.get("vol_ann"), vol_p10=vol_p10)
    else:
        rec["verdict"] = verdict(validators, jumps, z_nat, z_uni)
    return rec


def render(recs: list, run_id: str) -> str:
    L = [f"# NAV forensic report (audit run {run_id})", "", f"{len(recs)} funds. Verdict rules: see the module docstring of scripts/audit/nav/nav_forensics.py. Read-only.", "",
         "| ISIN | nature | uni | verdict | audit dev | z universe | z in nature | top metric | rows (p10 in nature) | validators | worst 1-month move (peer median, peers moved same way) |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in recs:
        tm = r.get("top_metric", {})
        mv = (r.get("top_moves") or [{}])[0]
        val = ",".join(k.replace("validate_nav_", "").replace("validate_nav", "step8x") for k, v in r["validators"].items() if v != "PASS") or "pass"
        L.append(f"| {r['isin']} | {r['nature']} | {r['in_universe']} | **{r['verdict']}** | {tm.get('audit_dev')} | {tm.get('z_universe')} | {tm.get('z_within_nature')} | {str(tm.get('key', ''))[:34]} | {tm.get('source_rows')} ({tm.get('source_rows_p10_in_nature')}) | {val} | "
                 f"{mv.get('ret')} in {mv.get('month')} ({mv.get('peer_median')}, {mv.get('peer_share')}) |")
    by = {}
    for r in recs:
        by.setdefault(r["verdict"], []).append(r["isin"])
    L += ["", "## Summary"] + [f"- **{v}**: {len(i)} ({', '.join(i[:6])}{', ...' if len(i) > 6 else ''})" for v, i in sorted(by.items())]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--from-run", help="audit run id: take every fund whose |OUTLIER_MAD_Z| reached --min-dev in it")
    ap.add_argument("--min-dev", type=float, default=50.0)
    ap.add_argument("--isins", nargs="*", default=[])
    ap.add_argument("--run", help="audit run id to read the fund's findings from (default: --from-run)")
    ap.add_argument("--out-dir", default=str(_cfg.AUDIT_DIRS["p2"]))
    args = ap.parse_args(argv)
    run_id = args.run or args.from_run
    if not run_id:
        ap.error("--run or --from-run is required (the audit run whose findings are read)")
    conn = get_connection()
    conn.read_only = True
    isins = list(args.isins)
    if args.from_run:
        isins += [r[0] for r in conn.execute("SELECT isin FROM control.audit_finding WHERE run_id = %s AND domain = 'p2_metrics' AND rule_id = 'OUTLIER_MAD_Z' AND isin IS NOT NULL "
                                             "GROUP BY isin HAVING max(abs(distance)) >= %s ORDER BY max(abs(distance)) DESC", (args.from_run, args.min_dev)).fetchall()]
    isins = list(dict.fromkeys(isins))
    peers = load_peer_returns(conn)
    family_of = load_family_map(conn, isins)
    recs = [forensic(conn, peers, i, run_id, family_of) for i in isins]
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"nav_forensics_{run_id}.json").write_text(json.dumps(recs, indent=1, default=str), encoding="utf-8")
    text = render(recs, run_id)
    (out / f"nav_forensics_{run_id}.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"\nwritten: {out / ('nav_forensics_' + run_id + '.md')} (+ .json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
