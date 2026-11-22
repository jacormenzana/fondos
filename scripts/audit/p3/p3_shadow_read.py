# scripts/audit/p3/p3_shadow_read.py
# -*- coding: utf-8 -*-
r"""
P3-7: reader of the forward shadow of the AC arm, exactly as Amendment 1 of doc/decisiones/PREREG_P3_7.md (section 13) defines it. READ-ONLY on the database (SELECT only); the only thing it writes is the
frozen fee snapshot of a review, in a new folder.

    py -X utf8 scripts\audit\p3\p3_shadow_read.py                      integrity fields of the rows of P3_FEE_PENALTY_SHADOW (the default, and the only mode before the first review)
    py -X utf8 scripts\audit\p3\p3_shadow_read.py --review [--snapshot DIR] [--out FILE.json]
                                                                       the R6 cost review: REFUSED (exit 3, nothing about the comparison is printed) until the window exists

Series, all rebuilt from stored rows: L = the live arm (`off` of each AC row = consecutive DRAFTs), AC = `chain` of the AC rows, B = `chain` of the band-only hysteresis rows (P3_HYSTERESIS_SHADOW).
One row per calendar month (the LAST row of the month); a month COUNTS only when the previous calendar month has a row (the first row after a gap, and the first row ever, have no predecessor in the
series and are excluded from the chain statistics). Turnover = one-way turnover RECOMPUTED from the stored weights of two consecutive counted rows (never the stored `traded_notional`). Cost = entry fee x
what is bought + exit fee x what is sold, fund by fund, with the stated `fund_master` fees of a hash-verified frozen snapshot; a fee that is unknown takes the median of the known ones of the funds held and
is counted. Cost drag a year = 12 x the mean monthly cost (a fraction of capital), in pp.

Readiness (R6): the rows span at least 6 calendar months and the last 6 calendar months have at most 1 gap (more postpones the review by a month). The statistics use the counted months of that window,
common to the three series when B has rows. Reading choices this script makes where the amendment is silent are printed with the report ("reading").
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src import fee_penalty_shadow as fp  # noqa: E402
from proyecto3.src import hysteresis_shadow as hs  # noqa: E402
from proyecto3.src import pit_p37_snapshot as SNAP  # noqa: E402

AC_STEP, AC_STEP_ERROR, B_STEP = fp.STEP, fp.STEP_ERROR, hs.STEP
FEE_COLUMNS = SNAP.FEE_COLUMNS
FEES_FILE = "fees.parquet"
REVIEW_MONTHS = 6                       # R6: the window of the first review (PREREG_P3_7 section 13)
MAX_GAPS = 1                            # more gaps than this in the window postpones the review
TURNOVER_REDUCTION_MIN = 0.30           # A5, rule 1
COST_DRAG_REDUCTION_MIN_PP = 0.50       # A5, rule 2
AC_VS_BAND_SAVING_MIN_PP = 0.50         # N, rule 3
_EPS = 1e-9
ROWS_LIMIT = 600


class NotReady(RuntimeError):
    """The review window does not exist yet: nothing about the comparison may be computed or shown."""


# --------------------------------------------------------------------------------------------------------------------------------------------------
# months, gaps, counted months
# --------------------------------------------------------------------------------------------------------------------------------------------------
def month_of(row: dict) -> str:
    return str(row["built_at"])[:7]


def _ordinal(month: str) -> int:
    y, m = month.split("-")
    return int(y) * 12 + int(m) - 1


def _month(ordinal: int) -> str:
    return f"{ordinal // 12:04d}-{ordinal % 12 + 1:02d}"


def last_per_month(rows: list) -> dict:
    """{month: row}: the LAST row of each calendar month (by `built_at`, the input order breaks a tie)."""
    by: dict = {}
    for r in sorted(rows, key=lambda r: str(r["built_at"])):
        by[month_of(r)] = r
    return by


def gaps(by_month: dict) -> list:
    """The calendar months with no row between the first and the last row."""
    if not by_month:
        return []
    ords = sorted(_ordinal(m) for m in by_month)
    present = set(ords)
    return [_month(o) for o in range(ords[0], ords[-1] + 1) if o not in present]


def counted_months(by_month: dict) -> list:
    """The months whose previous calendar month also has a row (so a turnover between two consecutive rows exists), oldest first."""
    present = {_ordinal(m) for m in by_month}
    return [_month(o) for o in sorted(present) if (o - 1) in present]


def readiness(by_month: dict, n: int = REVIEW_MONTHS, max_gaps: int = MAX_GAPS) -> dict:
    """May the review run? The rows span at least `n` calendar months and the last `n` calendar months have at most `max_gaps` months without a row."""
    if not by_month:
        return {"ready": False, "span": 0, "gaps_in_window": 0, "window": [], "reason": "NOT READY: no SHADOW row yet"}
    ords = sorted(_ordinal(m) for m in by_month)
    span = ords[-1] - ords[0] + 1
    window = [_month(o) for o in range(ords[-1] - n + 1, ords[-1] + 1)]
    missing = [m for m in window if m not in by_month]
    if span < n:
        return {"ready": False, "span": span, "gaps_in_window": len(missing), "window": window, "reason": f"NOT READY: the rows span {span} calendar month(s), the review needs {n}"}
    if len(missing) > max_gaps:
        return {"ready": False, "span": span, "gaps_in_window": len(missing), "window": window,
                "reason": f"NOT READY: {len(missing)} gap(s) in the last {n} months ({', '.join(missing)}); the review is postponed by a month"}
    return {"ready": True, "span": span, "gaps_in_window": len(missing), "window": window, "reason": "ready"}


# --------------------------------------------------------------------------------------------------------------------------------------------------
# integrity view (the only thing shown before the first review): a positive whitelist
# --------------------------------------------------------------------------------------------------------------------------------------------------
def _funds_per_sub(rows: list) -> dict:
    out: dict = {}
    for r in rows or []:
        k = str(r.get("subportfolio"))
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


def integrity_view(row: dict) -> dict:
    """Status, scenario, band, H, funds per sub-portfolio and weight sum of each series, calc versions and the two counts. Nothing else of the row is read."""
    arms = ("off", "one_step", "chain")
    return {"status": "SHADOW", "month": month_of(row), "scenario_id": row.get("scenario_id"), "built_at": row.get("built_at"), "band": row.get("band"), "l4_h_years": row.get("l4_h_years"),
            "funds_per_subportfolio": {a: _funds_per_sub(row[a]["weights"]) for a in arms},
            "weight_sums": {a: round(sum(float(w["weight"]) for w in row[a]["weights"]), 6) for a in arms},
            "calc_versions": row.get("calc_versions") or {}, "n_candidates": row.get("n_candidates"), "n_fee_unknown": row.get("n_fee_unknown")}


def render_integrity(rows: list, n_errors: int) -> str:
    by = last_per_month(rows)
    lines = [f"P3_FEE_PENALTY_SHADOW: {len(rows)} SHADOW row(s), {len(by)} calendar month(s), {n_errors} ERROR row(s)"]
    g = gaps(by)
    lines.append(f"gap(s): {', '.join(g) if g else 'none'}")
    for m in sorted(by):
        v = integrity_view(by[m])
        lines.append(f"- {m}  {v['status']}  scenario {v['scenario_id']}  band {v['band']}  H {v['l4_h_years']}  built {v['built_at']}")
        lines.append("    funds per sub-portfolio: " + "; ".join(f"{a} {v['funds_per_subportfolio'][a]}" for a in v["funds_per_subportfolio"]))
        lines.append("    weight sums: " + "; ".join(f"{a} {s:.4f}" for a, s in v["weight_sums"].items()))
        lines.append(f"    calc_versions {json.dumps(v['calc_versions'], sort_keys=True)}  n_candidates {v['n_candidates']}  n_fee_unknown {v['n_fee_unknown']}")
    lines.append("comparison fields are withheld until the first review (PREREG_P3_7 section 13); run with --review when the window exists")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------------------------------------------------------------
# turnover and cost (review only)
# --------------------------------------------------------------------------------------------------------------------------------------------------
def one_way_turnover(new_rows: list, prev_rows: list) -> float:
    return hs.one_way_turnover(hs.weights_of(new_rows), hs.weights_of(prev_rows))


def default_fees(fees: pd.DataFrame, held: set) -> "tuple[float, float]":
    """The price of an unknown fee: the median of the known entry / exit fees of the funds held in the rows used (the convention of the cost check)."""
    f = fees.reindex(sorted(held))
    return float(f["Entry_Fee_Pct"].dropna().median()), float(f["Exit_Fee_Pct"].dropna().median())


def pair_cost(new_rows: list, prev_rows: list, entry: pd.Series, exit_: pd.Series, default_entry: float, default_exit: float) -> "tuple[float, float]":
    """(monthly cost as a fraction of capital, share of the traded notional whose fee was unknown) between two consecutive rows: entry fee on what is bought, exit fee on what is sold."""
    new, prev = hs.weights_of(new_rows), hs.weights_of(prev_rows)
    cost = traded = unknown = 0.0
    for isin in set(new) | set(prev):
        d = new.get(isin, 0.0) - prev.get(isin, 0.0)
        if d == 0:
            continue
        fee = entry.get(isin, np.nan) if d > 0 else exit_.get(isin, np.nan)
        miss = not np.isfinite(fee)
        if miss:
            fee = default_entry if d > 0 else default_exit
        cost += abs(d) * float(fee)
        traded += abs(d)
        unknown += abs(d) if miss else 0.0
    return cost, (unknown / traded if traded else 0.0)


def unknown_fee_weight_share(rows: list, fees: pd.DataFrame) -> float:
    """The share of the weight held in funds whose entry or exit fee is unknown (a fund absent from the snapshot counts as unknown). Reported, never gating."""
    w = hs.weights_of(rows)
    total = sum(w.values())
    if not total:
        return 0.0
    f = fees.reindex(list(w))
    bad = f[FEE_COLUMNS].isna().any(axis=1)
    return float(sum(w[i] for i in f.index[bad.to_numpy()]) / total)


# --------------------------------------------------------------------------------------------------------------------------------------------------
# frozen fee snapshot
# --------------------------------------------------------------------------------------------------------------------------------------------------
def freeze_fees(folder, fees: pd.DataFrame) -> dict:
    """Freeze the stated fees once (parquet + SHA-256 manifest). Refuses a folder that exists."""
    return SNAP.freeze_frames(folder, {FEES_FILE: fees[FEE_COLUMNS]})


def load_fees(folder) -> "tuple[pd.DataFrame, str]":
    """(fees, snapshot id) after verifying the file against its manifest (SnapshotMismatch on any difference)."""
    frames = SNAP.load_frames(folder)
    manifest = json.loads((Path(folder) / SNAP.MANIFEST).read_text(encoding="utf-8"))
    return frames[FEES_FILE], manifest["snapshot_id"]


# --------------------------------------------------------------------------------------------------------------------------------------------------
# R6
# --------------------------------------------------------------------------------------------------------------------------------------------------
def evaluate_r6(stats: dict) -> dict:
    """The three deterministic cost rules, each PASS / FAIL / NOT INFORMATIVE, inclusive at the threshold, never a blend. `stats` = {L, AC, B|None}: {turnover, cost_pp}."""
    L, AC, B = stats["L"], stats["AC"], stats.get("B")
    r1 = "NOT INFORMATIVE" if not L["turnover"] else ("PASS" if (L["turnover"] - AC["turnover"]) / L["turnover"] >= TURNOVER_REDUCTION_MIN - _EPS else "FAIL")
    r2 = "PASS" if L["cost_pp"] - AC["cost_pp"] >= COST_DRAG_REDUCTION_MIN_PP - _EPS else "FAIL"
    r3 = "NOT INFORMATIVE" if B is None else ("PASS" if B["cost_pp"] - AC["cost_pp"] >= AC_VS_BAND_SAVING_MIN_PP - _EPS else "FAIL")
    if (r1, r2, r3) == ("PASS", "PASS", "PASS"):
        verdict = "AC cost-confirmed"
    elif r1 == "PASS" and r2 == "PASS" and r3 == "FAIL":
        verdict = "AC not confirmed over the band: the band alone (B) is the candidate, the penalty is dropped"
    else:
        verdict = "AC not cost-confirmed (" + ", ".join(f"rule{i + 1} {v}" for i, v in enumerate((r1, r2, r3)) if v != "PASS") + ")"
    return {"rule1": r1, "rule2": r2, "rule3": r3, "verdict": verdict}


def _series_rows(by_month: dict, arm: str) -> dict:
    key = "off" if arm == "L" else "chain"
    return {m: r[key]["weights"] for m, r in by_month.items()}


def _arm_stats(weights_by_month: dict, months: list, entry, exit_, de, dx) -> dict:
    tos, costs, unks = [], [], []
    for m in months:
        prev = _month(_ordinal(m) - 1)
        tos.append(one_way_turnover(weights_by_month[m], weights_by_month[prev]))
        c, u = pair_cost(weights_by_month[m], weights_by_month[prev], entry, exit_, de, dx)
        costs.append(c)
        unks.append(u)
    return {"turnover": float(np.mean(tos)), "cost_pp": float(12 * 100 * np.mean(costs)), "unknown_fee_traded_share": float(np.mean(unks))}


def review_r6(ac_rows: list, b_rows: list, fees: pd.DataFrame) -> dict:
    """The R6 numbers. Raises NotReady before the window exists, so no comparison value can be obtained early."""
    ac_by, b_by = last_per_month(ac_rows), last_per_month(b_rows)
    ready = readiness(ac_by)
    if not ready["ready"]:
        raise NotReady(ready["reason"])
    window = set(ready["window"])
    months = [m for m in counted_months(ac_by) if m in window]
    if b_by:
        months = [m for m in months if m in set(counted_months(b_by))]
    if not months:
        raise NotReady("NOT READY: no counted month common to the series in the window")
    series = {"L": _series_rows(ac_by, "L"), "AC": _series_rows(ac_by, "AC")}
    if b_by:
        series["B"] = _series_rows(b_by, "B")
    held = {r["isin"] for s in series.values() for m in months for key in (m, _month(_ordinal(m) - 1)) for r in s[key]}
    de, dx = default_fees(fees, held)
    entry, exit_ = fees["Entry_Fee_Pct"], fees["Exit_Fee_Pct"]
    stats = {k: _arm_stats(v, months, entry, exit_, de, dx) for k, v in series.items()}
    stats.setdefault("B", None)
    last = months[-1]
    return {"readiness": ready, "months": months, "stats": stats, "rules": evaluate_r6(stats), "default_fees": {"entry": de, "exit": dx},
            "ac_unknown_fee_weight_share": unknown_fee_weight_share(series["AC"][last], fees), "n_months": len(months)}


def render_review(res: dict, snapshot_id: str) -> str:
    s, r = res["stats"], res["rules"]
    lines = [f"R6 cost review: {res['n_months']} counted month(s) {res['months'][0]} .. {res['months'][-1]} (window {res['readiness']['window'][0]} .. {res['readiness']['window'][-1]}, "
             f"{res['readiness']['gaps_in_window']} gap(s)); fee snapshot {snapshot_id[:12]}",
             f"reading: a month counts when the previous calendar month has a row; unknown fee priced at the median of the known ones (entry {res['default_fees']['entry']:.4f}, exit {res['default_fees']['exit']:.4f}); "
             "cost drag a year = 12 x mean monthly cost"]
    for k in ("L", "AC", "B"):
        if s.get(k):
            lines.append(f"{k:>2}: one-way turnover {s[k]['turnover']:.1%} a month; cost drag {s[k]['cost_pp']:.2f} pp a year; traded notional with an unknown fee {s[k]['unknown_fee_traded_share']:.1%}")
        else:
            lines.append(f"{k:>2}: no row of the band-only stream")
    lines.append(f"AC weight in funds with an unknown fee (latest counted row): {res['ac_unknown_fee_weight_share']:.1%} (reported, not gating)")
    lines.append(f"rule1 AC turnover at least {TURNOVER_REDUCTION_MIN:.0%} below L: {r['rule1']}")
    lines.append(f"rule2 AC cost drag at least {COST_DRAG_REDUCTION_MIN_PP:.2f} pp below L: {r['rule2']}")
    lines.append(f"rule3 AC cost drag at least {AC_VS_BAND_SAVING_MIN_PP:.2f} pp below B: {r['rule3']}")
    lines.append(f"verdict: {r['verdict']} (R6 never adopts anything: R7)")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------------------------------------------------------------------------------
def run(argv: list, *, rows_ac: list, rows_b: list, n_errors: int, fees: "pd.DataFrame | None" = None) -> "tuple[int, str]":
    """The whole program on already-read rows: (exit code, text). 0 = shown, 3 = review refused (window not ready), 2 = a review needs the fees."""
    ap = argparse.ArgumentParser(prog="p3_shadow_read")
    ap.add_argument("--review", action="store_true", help="the R6 cost review (refused until the window exists)")
    ap.add_argument("--snapshot", default=None, help="folder of the frozen fee snapshot (frozen from the live fund_master when it does not exist)")
    ap.add_argument("--out", default=None, help="write the review as JSON")
    args = ap.parse_args(argv)
    if not args.review:
        return 0, render_integrity(rows_ac, n_errors)
    ready = readiness(last_per_month(rows_ac))
    if not ready["ready"]:
        return 3, ready["reason"]
    folder = Path(args.snapshot) if args.snapshot else _ROOT / "out" / "audit" / "p3_shadow" / f"{datetime.now(timezone.utc):%Y%m%d}_R6" / "snapshot"
    if folder.exists():
        frozen, snapshot_id = load_fees(folder)
    else:
        if fees is None:
            return 2, "a review needs the stated fees: none given and no snapshot folder to load"
        snapshot_id = freeze_fees(folder, fees)["snapshot_id"]
        frozen, _ = load_fees(folder)
    try:
        res = review_r6(rows_ac, rows_b, frozen)
    except NotReady as exc:
        return 3, str(exc)
    if args.out:
        Path(args.out).write_text(json.dumps({"snapshot_id": snapshot_id, **res}, indent=2, default=str), encoding="utf-8")
    return 0, render_review(res, snapshot_id)


def _read_live(argv: list):
    """(rows of the AC stream, rows of the band-only stream, ERROR row count, live fees or None). SELECT only."""
    from shared.db import get_connection
    conn = get_connection()
    try:
        rows_ac = hs.read_rows(conn, ROWS_LIMIT, step=AC_STEP)
        rows_b = hs.read_rows(conn, ROWS_LIMIT, step=B_STEP) if "--review" in argv else []
        n_errors = int(conn.execute("SELECT COUNT(*) FROM ingestion_log WHERE step = %s", (AC_STEP_ERROR,)).fetchone()[0])
        fees = None
        if "--review" in argv:
            snap = _arg_value(argv, "--snapshot")
            if not (snap and Path(snap).exists()):
                rows = conn.execute("SELECT ISIN, Entry_Fee_Pct, Exit_Fee_Pct FROM fund_master").fetchall()
                fees = pd.DataFrame([tuple(r) for r in rows], columns=["isin", *FEE_COLUMNS]).set_index("isin").apply(pd.to_numeric, errors="coerce")
        return rows_ac, rows_b, n_errors, fees
    finally:
        conn.close()


def _arg_value(argv: list, name: str):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None


def main(argv: "list | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    rows_ac, rows_b, n_errors, fees = _read_live(argv)
    rc, text = run(argv, rows_ac=rows_ac, rows_b=rows_b, n_errors=n_errors, fees=fees)
    print(text)
    return rc


if __name__ == "__main__":
    sys.exit(main())
