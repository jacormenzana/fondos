# -*- coding: utf-8 -*-
"""Fault-injection harness for the NAV validators (FND-0392, WS0.5 of FND-0391; plan amendments A3 G-05, A4 #2 and A5).

Question it answers, with an independent ground truth: when a real, healthy monthly NAV series is corrupted in a KNOWN way (shared/fault_injection.py), do the validators notice?

    python scripts/audit/fault_injection_run.py                          # report on the committed fixture, exit 0
    python scripts/audit/fault_injection_run.py --json out.json --min-recall 0.95   # exit 1 if a class that SHOULD be caught is not

Hermetic by construction: it reads one CSV (tests/fixtures/fault_injection/nav_sample.csv.gz: isin, nature, role, date, nav) and imports no database code, so it runs on any machine, in CI-less local
delivery, with no live access. The fixture is built read-only from the live store by scripts/audit/fault_injection_fixture.py (a 40-ISIN sample plus the peers it needs). Today it measures validator
-> P2 metrics; the suspicion predicate (WS2) and the escalation engine (WS3) plug into DETECTORS later and are measured by the same run.

Method, per sample fund and per corruption class:
  1. choose the corrupted month deterministically (first month after half the series where >= NAV_UNSHARED_JUMP_MIN_PEERS peers report, with the class's tail) and inject;
  2. run every detector on the CLEAN series first. A detector that already fails on the clean series cannot be credited with the corrupted one: that fund is "not attributable" for it (and is listed as a
     baseline flag, which is also the false-positive evidence);
  3. recall(detector, class) = detected / (injected - not attributable); a class is MET when every detector in its `expected` tuple reaches --min-recall;
  4. the P2 metrics the corruption feeds (annualised volatility, trailing-12-month annualised return) are recomputed, to show what a miss costs downstream.
A class with an empty `expected` tuple is a known gap for a later layer: it is reported, never hidden, and never fails the run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT, _ROOT / "proyecto2"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from shared import config as _cfg                                                    # noqa: E402
from shared.fault_injection import CORRUPTIONS, choose_month, inject                 # noqa: E402
from src.calculations.returns import annualized_return, annualized_volatility   # noqa: E402
from src.utils.peer_returns import build_peer_returns                                # noqa: E402
from src.utils.validators import validate_nav, validate_nav_stale_flat, validate_nav_unshared_jump   # noqa: E402

DEFAULT_FIXTURE = _ROOT / "tests" / "fixtures" / "fault_injection" / "nav_sample.csv.gz"
MIN_ROWS = 48                      # a series shorter than this is not corrupted (no tail, no peers worth comparing)

# name -> callable(nav_df, isin, peers) -> (ok, message). The thresholds come from shared.config; stale-flat is forced ON so a rollback of its switch cannot hide a regression here.
DETECTORS = {
    "validate_nav": lambda nav, isin, peers: validate_nav(nav),
    "validate_nav_stale_flat": lambda nav, isin, peers: validate_nav_stale_flat(nav, enabled=True),
    "validate_nav_unshared_jump": lambda nav, isin, peers: validate_nav_unshared_jump(nav, peers, isin),
}


def load_fixture(path=DEFAULT_FIXTURE) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"isin": str, "nature": str, "role": str})
    df["date"] = pd.to_datetime(df["date"])
    df["nav"] = df["nav"].astype(float)
    return df.sort_values(["isin", "date"]).reset_index(drop=True)


def p2_effects(nav_df: pd.DataFrame) -> dict:
    """The two P2 outputs a bad NAV feeds first, over the whole series: annualised volatility and annualised return (a corruption anywhere in the history moves both; the trailing windows only see
    it when it is recent). NaN when not computable."""
    try:
        s = nav_df["nav"].reset_index(drop=True)
        return {"vol_ann": float(annualized_volatility(s)), "ret_ann": float(annualized_return(s))}
    except Exception:                                                                  # noqa: BLE001 -- an effect that cannot be computed is reported as NaN
        return {"vol_ann": float("nan"), "ret_ann": float("nan")}


def _safe(detector, nav, isin, peers):
    try:
        return detector(nav, isin, peers)
    except Exception as exc:                                                           # noqa: BLE001 -- a crashing detector counts as "did not notice", with its message
        return True, f"detector error: {type(exc).__name__}: {exc}"


def run_harness(long_df: pd.DataFrame, corruptions=None, detectors=None, min_peers=None, min_recall=0.95) -> dict:
    """The whole measurement on a long frame (isin, nature, role, date, nav). Pure and deterministic. Returns
    {"funds", "baseline_flags", "real_positive", "classes": {name: {...}}, "detectors", "min_recall"}."""
    corruptions = {n: CORRUPTIONS[n] for n in (corruptions or CORRUPTIONS)}
    detectors = detectors or DETECTORS
    min_peers = _cfg.NAV_UNSHARED_JUMP_MIN_PEERS if min_peers is None else min_peers
    peers = build_peer_returns(long_df[["isin", "nature", "date", "nav"]])
    sample = long_df[long_df["role"].isin(["sample", "real_positive"])]
    series = {isin: g[["date", "nav"]].reset_index(drop=True) for isin, g in sample.groupby("isin", sort=True)}
    roles = sample.groupby("isin")["role"].first().to_dict()

    clean_ok, baseline_flags = {}, []
    for isin, nav in series.items():
        for dname, det in detectors.items():
            ok, msg = _safe(det, nav, isin, peers)
            clean_ok[(isin, dname)] = ok
            if not ok:
                baseline_flags.append({"isin": isin, "role": roles[isin], "detector": dname, "message": str(msg)[:140]})

    eligible = [i for i, nav in series.items() if len(nav) >= MIN_ROWS and roles[i] == "sample"]
    classes = {}
    for cname, c in corruptions.items():
        rec = {"description": c.description, "expected": list(c.expected), "injected": 0, "skipped": 0,
               "detectors": {d: {"detected": 0, "not_attributable": 0} for d in detectors}, "any_detected": 0, "effects": []}
        for isin in eligible:
            nav = series[isin]
            k = choose_month(nav, cname, lambda kk, nav=nav, isin=isin: len(peers.peers_at(isin, nav["date"].iloc[kk])) >= min_peers)
            if k is None:
                rec["skipped"] += 1
                continue
            bad = inject(nav, cname, k)
            rec["injected"] += 1
            hit_any = False
            for dname, det in detectors.items():
                if not clean_ok[(isin, dname)]:
                    rec["detectors"][dname]["not_attributable"] += 1
                    continue
                ok, _msg = _safe(det, bad, isin, peers)
                if not ok:
                    rec["detectors"][dname]["detected"] += 1
                    hit_any = True
            rec["any_detected"] += int(hit_any)
            before, after = p2_effects(nav), p2_effects(bad)
            rec["effects"].append({"vol_ratio": after["vol_ann"] / before["vol_ann"] if before["vol_ann"] else float("nan"),
                                   "ret_after": after["ret_ann"], "ret_before": before["ret_ann"]})
        for dname, d in rec["detectors"].items():
            denom = rec["injected"] - d["not_attributable"]
            d["recall"] = (d["detected"] / denom) if denom > 0 else None
        rec["met"] = (None if not c.expected else
                      all(rec["detectors"].get(d, {}).get("recall") is not None and rec["detectors"][d]["recall"] >= min_recall for d in c.expected))     # an expected detector that is not registered is not met
        eff = pd.DataFrame(rec.pop("effects"))
        rec["median_vol_ratio"] = None if eff.empty else float(np.nanmedian(eff["vol_ratio"]))
        rec["median_ret_after"] = None if eff.empty else float(np.nanmedian(eff["ret_after"]))
        rec["median_ret_before"] = None if eff.empty else float(np.nanmedian(eff["ret_before"]))
        classes[cname] = rec

    real = {i: [d for d in detectors if not clean_ok[(i, d)]] for i, r in roles.items() if r == "real_positive"}
    flagged_funds = {}
    for f in baseline_flags:
        if f["role"] == "sample":
            flagged_funds.setdefault(f["detector"], set()).add(f["isin"])
    return {"funds": {"sample": len([i for i in roles if roles[i] == "sample"]), "eligible": len(eligible), "peers": int(long_df[long_df["role"] == "peer"]["isin"].nunique())},
            "baseline_flags": baseline_flags,
            "baseline_false_positive_funds": {d: sorted(v) for d, v in flagged_funds.items()},
            "real_positive": real, "classes": classes, "detectors": list(detectors), "min_recall": min_recall, "min_peers": min_peers}


def render(report: dict) -> str:
    L = []
    f = report["funds"]
    L.append(f"Fault-injection report: {f['eligible']} eligible of {f['sample']} sample funds, {f['peers']} peers | min_peers={report['min_peers']} min_recall={report['min_recall']}")
    L.append("")
    L.append(f"{'class':22} {'inj':>4} {'skip':>4}  " + "  ".join(f"{d.replace('validate_nav', 'nav').replace('_', ' ')[:18]:>18}" for d in report["detectors"]) + f"  {'any':>4} {'exp':>3}  vol x   ret_ann (median)")
    for name, r in report["classes"].items():
        cells = []
        for d in report["detectors"]:
            x = r["detectors"][d]
            cells.append(f"{x['detected']:>3}/{r['injected'] - x['not_attributable']:<3} ({'-' if x['recall'] is None else format(x['recall'], '.2f')})".rjust(18))
        met = "-" if r["met"] is None else ("OK" if r["met"] else "MISS")
        vol = "-" if r["median_vol_ratio"] is None else f"{r['median_vol_ratio']:.2f}"
        ret = "-" if r["median_ret_after"] is None else f"{r['median_ret_before']:+.3f}->{r['median_ret_after']:+.3f}"
        L.append(f"{name:22} {r['injected']:>4} {r['skipped']:>4}  " + "  ".join(cells) + f"  {r['any_detected']:>4} {met:>3}  {vol:>5}  {ret}")
    L.append("")
    gaps = [n for n, r in report["classes"].items() if not r["expected"]]
    L.append("classes no validator is expected to catch yet (gaps for WS2/WS3, never a failure): " + (", ".join(gaps) or "none"))
    L.append("baseline flags on CLEAN sample series (false-positive evidence): " + (", ".join(f"{d}={len(v)}" for d, v in report["baseline_false_positive_funds"].items()) or "none"))
    for fl in report["baseline_flags"][:6]:
        L.append(f"  - {fl['isin']} [{fl['role']}] {fl['detector']}: {fl['message']}")
    if report["real_positive"]:
        L.append("real positive control (uncorrupted real series): " + "; ".join(f"{i} flagged by {', '.join(d) or 'NOTHING'}" for i, d in report["real_positive"].items()))
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    ap.add_argument("--json", default=None, help="write the full report here")
    ap.add_argument("--min-recall", type=float, default=None, help="exit 1 when a class with expected detectors is below this recall (default: report only)")
    args = ap.parse_args(argv)
    long_df = load_fixture(args.fixture)
    report = run_harness(long_df, min_recall=0.95 if args.min_recall is None else args.min_recall)
    print(render(report))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    if args.min_recall is not None:
        missed = [n for n, r in report["classes"].items() if r["met"] is False]
        if missed:
            print(f"\nBELOW min-recall {args.min_recall}: {', '.join(missed)}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
