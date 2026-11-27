# scripts/audit/p3/p3_shadow_read.py
# -*- coding: utf-8 -*-
r"""
P3-7: reader of the forward shadow of the AC arm, exactly as Amendments 1 and 2 of doc/decisiones/PREREG_P3_7.md (sections 13 and 14) define it. READ-ONLY on the database (SELECT only); the only thing it writes is the
frozen fee snapshot of a review, in a new folder.

    py -X utf8 scripts\audit\p3\p3_shadow_read.py                      integrity fields of the rows of P3_FEE_PENALTY_SHADOW (the default, and the only mode before the first review)
    py -X utf8 scripts\audit\p3\p3_shadow_read.py --review [--snapshot DIR] [--out FILE.json]
                                                                       the R6 cost review: REFUSED (exit 3, nothing about the comparison is printed) until the window exists
    py -X utf8 scripts\audit\p3\p3_shadow_read.py --after-adoption [--snapshot DIR] [--out FILE.json]
                                                                       the checks after an adoption (FND-0445): R12, R24, R36 and the revert trigger; refused until 12 post-adoption months

Series, all rebuilt from stored rows: L = the live arm (`off` of each AC row = consecutive DRAFTs), AC = `chain` of the AC rows, B = `chain` of the band-only hysteresis rows (P3_HYSTERESIS_SHADOW).
One row per calendar month (the LAST row of the month); a month COUNTS only when the previous calendar month has a row (the first row after a gap, and the first row ever, have no predecessor in the
series and are excluded from the chain statistics). Turnover = one-way turnover RECOMPUTED from the stored weights of two consecutive counted rows (never the stored `traded_notional`). Cost = entry fee x
what is bought + exit fee x what is sold, fund by fund, with the `fund_master` fees of a hash-verified frozen snapshot. Fill of an unknown fee (section 14, item 2), per fund and side, the same for L, AC and B:
the exact fee, else the declared ceiling, else the p90 of the exact fees of that side among the funds of the same Fund_Nature in the snapshot (all natures when the nature has none). An optimistic variant (a fund
with no figure costs 0) is reported next to the verdict and never gates. Cost drag a year = 12 x the mean monthly cost (a fraction of capital), in pp.

Coverage (section 14, item 3): at least FEE_COVERAGE_MIN of AC's bought notional in the window (positive weight changes between consecutive counted rows) must be in funds with an exact fee or a ceiling on BOTH
sides; below it R6 is NOT INFORMATIVE and carried to the next review. The same share is reported for L and B, and the exit coverage of sold notional for all three.

Readiness (R6): the rows span at least 6 calendar months and the last 6 calendar months have at most 1 gap (more postpones the review by a month). The statistics use the counted months of that window,
common to the three series when B has rows. Reading choices this script makes where the amendment is silent are printed with the report ("reading").

After an adoption (FND-0445, doc/backlog/PKG_M_ac_adoption.md section 2): the adoption month and arm come from the live flags recorded by the baseline stream (P3_BASELINE_SHADOW). Before it the series are
the ones above; from it on the adopted arm = `off` of the baseline rows (the DRAFT) and L = their `chain`. A row of month m earns the return of month m+1 (month-end NAV to month-end NAV), net of the cost of
its own trades. R12 is an alarm; R24 and R36 (non-inferiority and drawdown guard on the first 24 / 36 months of the series) and the revert trigger can say REVERT. Only the owner's order reverts.
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

from proyecto3.src import baseline_shadow as bl  # noqa: E402
from proyecto3.src import fee_penalty_shadow as fp  # noqa: E402
from proyecto3.src import hysteresis_shadow as hs  # noqa: E402
from proyecto3.src import pit_p37_snapshot as SNAP  # noqa: E402
from proyecto3.src import ranking_levers as RL  # noqa: E402
from proyecto3.src.pit_p35a_eval import noninferiority  # noqa: E402

AC_STEP, AC_STEP_ERROR, B_STEP = fp.STEP, fp.STEP_ERROR, hs.STEP
BASE_STEP, BASE_STEP_ERROR = bl.STEP, bl.STEP_ERROR
FEE_COLUMNS = SNAP.FEE_COLUMNS                                     # the two exact fees, ratios (0.05 = 5 %)
CEILING_COLUMNS = ["Entry_Fee_Pct_Max", "Exit_Fee_Pct_Max"]         # the declared ceilings, whole percent (5.0 = 5 %)
SNAPSHOT_COLUMNS = [*FEE_COLUMNS, *CEILING_COLUMNS, "Fund_Nature"]
FEE_COVERAGE_MIN = 0.95                                             # fee_coverage_min (PREREG_P3_7 section 14, owner 2026-11-26)
FILL_QUANTILE = 0.90
FEES_FILE = "fees.parquet"
REVIEW_MONTHS = 6                       # R6: the window of the first review (PREREG_P3_7 section 13)
MAX_GAPS = 1                            # more gaps than this in the window postpones the review
TURNOVER_REDUCTION_MIN = 0.30           # A5, rule 1
COST_DRAG_REDUCTION_MIN_PP = 0.50       # A5, rule 2
AC_VS_BAND_SAVING_MIN_PP = 0.50         # N, rule 3
_EPS = 1e-9
ROWS_LIMIT = 600
# -- after adoption (PREREG_P3_7 sections 13-14; the values of Amendment 5 of PREREG_FND-0193 reused) --
ALARM_MONTH = 12                        # R12
CHECK_MONTHS = (24, 36)                 # R24, R36
REVERT_WINDOW = 12                      # consecutive post-adoption builds
REVERT_RETURN_GAP_PP = 0.50             # a year, net return below L
REVERT_DD_GAP = 0.02                    # maximum drawdown deeper than L's
REVERT_TURNOVER_RATIO = 0.5             # realised turnover reduction below this share of the PIT one
NI_MARGIN_PP = -0.10                    # net_return_tolerance
NI_LEVEL = 0.05                         # one-sided
DD_TOLERANCE = 0.01                     # max_dd_tolerance
DD_CAP = 0.15                           # mandate_dd_cap
DD_INFORMATIVE_MIN = 0.03               # the drawdown guard counts only if L's own drawdown reaches it
PIT_TURNOVER = {"L": 32.2, "AC": 13.4, "B": 14.8}      # one-way, % a month (PREREG_P3_7 section 13)
AC_H_YEARS = 0.58                                     # the preregistered H of the fee penalty (= shared.config.P3_FEE_PENALTY_SHADOW_H_YEARS)


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


def mixed_builds(one: dict, other: dict, months) -> list:
    """The months of `months` whose two rows ({month: row} of two streams) name different builds: one shadow failed on a later rebuild, so the streams are compared across builds. Reported, never
    gating; a row without a scenario_id says nothing."""
    out = []
    for m in sorted(set(months)):
        a, b = (one.get(m) or {}).get("scenario_id"), (other.get(m) or {}).get("scenario_id")
        if a and b and a != b:
            out.append(m)
    return out


def _mixed_line(months: list) -> str:
    return f"rows of one month come from different builds in {', '.join(months)} (reported, not gating)"


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


def render_integrity(rows: list, n_errors: int, pool: "tuple | None" = None) -> str:
    by = last_per_month(rows)
    lines = [f"P3_FEE_PENALTY_SHADOW: {len(rows)} SHADOW row(s), {len(by)} calendar month(s), {n_errors} ERROR row(s)"]
    g = gaps(by)
    lines.append(f"gap(s): {', '.join(g) if g else 'none'}")
    for m in sorted(by):
        v = integrity_view(by[m])
        lines.append(f"- {m}  {v['status']}  scenario {v['scenario_id']}  band {v['band']}  H {v['l4_h_years']}  built {v['built_at']}")
        lines.append("    funds per sub-portfolio: " + "; ".join(f"{a} {v['funds_per_subportfolio'][a]}" for a in v["funds_per_subportfolio"]))
        lines.append("    weight sums: " + "; ".join(f"{a} {s:.4f}" for a, s in v["weight_sums"].items()))
        lines.append(f"    calc_versions {json.dumps(v['calc_versions'], sort_keys=True)}  n_candidates {v['n_candidates']}  n_fee_unknown {v['n_fee_unknown']} "
                     "(funds of the whole scored frame with an unknown one-off fee, not of the candidates)")
    if pool is None or not pool[0]:
        lines.append("candidate pool: not read")
    else:
        n, covered = pool
        lines.append(f"candidate pool: {n} eligible funds today, {100 * covered / n:.1f} % with a point value or a ceiling on both fees (not AC's selection)")
    lines.append("comparison fields are withheld until the first review (PREREG_P3_7 section 13); run with --review when the window exists")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------------------------------------------------------------
# turnover and cost (review only)
# --------------------------------------------------------------------------------------------------------------------------------------------------
def one_way_turnover(new_rows: list, prev_rows: list) -> float:
    return hs.one_way_turnover(hs.weights_of(new_rows), hs.weights_of(prev_rows))


def _side(fees: pd.DataFrame, exact_col: str, ceiling_col: str, optimistic: bool) -> "tuple[pd.Series, float]":
    """(fee of every fund of the snapshot, fee of a fund absent from it) for one side, as ratios."""
    exact = pd.to_numeric(fees[exact_col], errors="coerce")
    out = exact.fillna(pd.to_numeric(fees[ceiling_col], errors="coerce") / 100.0)
    if optimistic:
        return out.fillna(0.0), 0.0
    known = exact.dropna()
    overall = float(known.quantile(FILL_QUANTILE)) if len(known) else float("nan")
    by_nature = exact.groupby(fees["Fund_Nature"]).quantile(FILL_QUANTILE).dropna()
    return out.fillna(fees["Fund_Nature"].map(by_nature)).fillna(overall), overall


def filled_fees(fees: pd.DataFrame, optimistic: bool = False) -> "tuple[pd.Series, pd.Series, float, float]":
    """(entry, exit, entry of an absent fund, exit of an absent fund): exact -> declared ceiling -> p90 of the exact fees of the nature (else of all);
    `optimistic`: exact -> ceiling -> 0. The p90 is taken over the whole frozen snapshot, never over the funds held (PREREG_P3_7 section 14, item 2)."""
    entry, de = _side(fees, FEE_COLUMNS[0], CEILING_COLUMNS[0], optimistic)
    exit_, dx = _side(fees, FEE_COLUMNS[1], CEILING_COLUMNS[1], optimistic)
    return entry, exit_, de, dx


def known_both_sides(fees: pd.DataFrame) -> set:
    """The funds with an exact fee or a declared ceiling on BOTH sides."""
    ok = (fees[FEE_COLUMNS[0]].notna() | fees[CEILING_COLUMNS[0]].notna()) & (fees[FEE_COLUMNS[1]].notna() | fees[CEILING_COLUMNS[1]].notna())
    return set(fees.index[ok.to_numpy()])


def _coverage(weights_by_month: dict, months: list, known: set, known_exit: set) -> dict:
    """Share of the bought notional in funds known on both sides, and share of the sold notional with a known exit, over the counted months. No notional = full coverage."""
    bought = bought_ok = sold = sold_ok = 0.0
    for m in months:
        new, prev = hs.weights_of(weights_by_month[m]), hs.weights_of(weights_by_month[_month(_ordinal(m) - 1)])
        for isin in set(new) | set(prev):
            d = new.get(isin, 0.0) - prev.get(isin, 0.0)
            if d > _EPS:
                bought += d
                bought_ok += d if isin in known else 0.0
            elif d < -_EPS:
                sold -= d
                sold_ok -= d if isin in known_exit else 0.0
    return {"bought": bought_ok / bought if bought else 1.0, "sold_exit": sold_ok / sold if sold else 1.0, "bought_notional": bought, "sold_notional": sold}


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
    return SNAP.freeze_frames(folder, {FEES_FILE: fees[SNAPSHOT_COLUMNS]})


def load_fees(folder) -> "tuple[pd.DataFrame, str]":
    """(fees, snapshot id) after verifying the file against its manifest (SnapshotMismatch on any difference)."""
    frames = SNAP.load_frames(folder)
    manifest = json.loads((Path(folder) / SNAP.MANIFEST).read_text(encoding="utf-8"))
    missing = [c for c in SNAPSHOT_COLUMNS if c not in frames[FEES_FILE].columns]
    if missing:
        raise SNAP.SnapshotMismatch(f"{folder}: the fee snapshot lacks {missing} (frozen before Amendment 2): freeze a new one")
    return frames[FEES_FILE], manifest["snapshot_id"]


# --------------------------------------------------------------------------------------------------------------------------------------------------
# R6
# --------------------------------------------------------------------------------------------------------------------------------------------------
def evaluate_r6(stats: dict, ac_bought_coverage: "float | None" = None) -> dict:
    """The three deterministic cost rules, each PASS / FAIL / NOT INFORMATIVE, inclusive at the threshold, never a blend. `stats` = {L, AC, B|None}: {turnover, cost_pp}.
    `ac_bought_coverage` below FEE_COVERAGE_MIN makes the whole review NOT INFORMATIVE, never a PASS (None = not assessed, for the pure rules)."""
    if ac_bought_coverage is not None and ac_bought_coverage < FEE_COVERAGE_MIN - _EPS:
        return {"rule1": "NOT INFORMATIVE", "rule2": "NOT INFORMATIVE", "rule3": "NOT INFORMATIVE",
                "verdict": f"NOT INFORMATIVE: only {ac_bought_coverage:.1%} of what AC buys has a fee or a ceiling on both sides (needs {FEE_COVERAGE_MIN:.0%}); R6 is carried to the next review"}
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
    tos, costs = [], []
    for m in months:
        prev = _month(_ordinal(m) - 1)
        tos.append(one_way_turnover(weights_by_month[m], weights_by_month[prev]))
        c, _unknown = pair_cost(weights_by_month[m], weights_by_month[prev], entry, exit_, de, dx)
        costs.append(c)
    return {"turnover": float(np.mean(tos)), "cost_pp": float(12 * 100 * np.mean(costs))}


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
    primary, optimistic = filled_fees(fees), filled_fees(fees, optimistic=True)
    stats = {k: _arm_stats(v, months, *primary) for k, v in series.items()}
    stats_opt = {k: _arm_stats(v, months, *optimistic) for k, v in series.items()}
    stats.setdefault("B", None)
    stats_opt.setdefault("B", None)
    known = known_both_sides(fees)
    known_exit = set(fees.index[(fees[FEE_COLUMNS[1]].notna() | fees[CEILING_COLUMNS[1]].notna()).to_numpy()])
    coverage = {k: _coverage(v, months, known, known_exit) for k, v in series.items()}
    cov_ac = coverage["AC"]["bought"]
    last = months[-1]
    used = {*months, *(_month(_ordinal(m) - 1) for m in months)}
    return {"mixed_builds": mixed_builds(ac_by, b_by, used), "readiness": ready, "months": months, "stats": stats, "rules": evaluate_r6(stats, cov_ac), "stats_optimistic": stats_opt,
            "rules_optimistic": evaluate_r6(stats_opt, cov_ac), "coverage": coverage, "default_fees": {"entry": primary[2], "exit": primary[3]},
            "ac_unknown_fee_weight_share": unknown_fee_weight_share(series["AC"][last], fees), "n_months": len(months)}


def render_review(res: dict, snapshot_id: str) -> str:
    s, r = res["stats"], res["rules"]
    lines = [f"R6 cost review: {res['n_months']} counted month(s) {res['months'][0]} .. {res['months'][-1]} (window {res['readiness']['window'][0]} .. {res['readiness']['window'][-1]}, "
             f"{res['readiness']['gaps_in_window']} gap(s)); fee snapshot {snapshot_id[:12]}",
             "reading: a month counts when the previous calendar month has a row; an unknown fee is filled with the declared ceiling, else the p90 of the nature "
             f"(a fund absent from the snapshot: entry {res['default_fees']['entry']:.4f}, exit {res['default_fees']['exit']:.4f}); cost drag a year = 12 x mean monthly cost; no purchases = full coverage"]
    for k in ("L", "AC", "B"):
        if s.get(k):
            c, o = res["coverage"][k], res["stats_optimistic"][k]
            lines.append(f"{k:>2}: one-way turnover {s[k]['turnover']:.1%} a month; cost drag {s[k]['cost_pp']:.2f} pp a year (optimistic {o['cost_pp']:.2f}); "
                         f"bought with a fee or ceiling on both sides {c['bought']:.1%}; sold with a known exit {c['sold_exit']:.1%}")
        else:
            lines.append(f"{k:>2}: no row of the band-only stream")
    if res["mixed_builds"]:
        lines.append("AC and band-only " + _mixed_line(res["mixed_builds"]))
    lines.append(f"coverage condition: AC bought {res['coverage']['AC']['bought']:.1%} against {FEE_COVERAGE_MIN:.0%}")
    lines.append(f"AC weight in funds without an exact fee (latest counted row): {res['ac_unknown_fee_weight_share']:.1%} (reported, not gating)")
    ro = res["rules_optimistic"]
    lines.append(f"optimistic variant (a fund with no figure costs 0; never gating): rule1 {ro['rule1']}, rule2 {ro['rule2']}, rule3 {ro['rule3']}")
    lines.append(f"rule1 AC turnover at least {TURNOVER_REDUCTION_MIN:.0%} below L: {r['rule1']}")
    lines.append(f"rule2 AC cost drag at least {COST_DRAG_REDUCTION_MIN_PP:.2f} pp below L: {r['rule2']}")
    lines.append(f"rule3 AC cost drag at least {AC_VS_BAND_SAVING_MIN_PP:.2f} pp below B: {r['rule3']}")
    lines.append(f"verdict: {r['verdict']} (R6 never adopts anything: R7)")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------------------------------------------------------------
# after adoption (FND-0445)
# --------------------------------------------------------------------------------------------------------------------------------------------------
def _arm_of(flags: dict) -> "str | None":
    """'L' (nothing adopted), 'AC' (band + penalty at the preregistered H), 'B' (band alone) or None for a combination the preregistration does not name."""
    band, penalty = bool(flags.get("PORTFOLIO_HYSTERESIS_ENABLED")), bool(flags.get("P3_L4_FEE_PENALTY_ENABLED"))
    if not band and not penalty:
        return "L"
    if not band:
        return None
    if not penalty:
        return "B"
    h = flags.get("P3_L4_H_YEARS")
    return "AC" if h is not None and abs(float(h) - AC_H_YEARS) <= _EPS else None


def adoption(base_by: dict) -> "tuple[str, str] | None":
    """(first month whose build did not run arm L, adopted arm) from the live flags of the baseline rows, or None while every row ran L."""
    for m in sorted(base_by):
        arm = _arm_of(base_by[m].get("live_flags") or {})
        if arm != "L":
            if arm is None:
                raise NotReady(f"NOT READY: the build of {m} ran a combination that is not a candidate of the preregistration (the fee penalty without the band, or at an H other than {AC_H_YEARS})")
            return m, arm
    return None


def _until_change(base_by: dict, adopted_month: str, arm: str) -> "tuple[dict, str | None]":
    """The baseline rows up to the first month AFTER the adoption that ran another arm (a revert or a further change), and that month."""
    for m in sorted(base_by):
        if m > adopted_month and _arm_of(base_by[m].get("live_flags") or {}) != arm:
            return {k: v for k, v in base_by.items() if k < m}, m
    return base_by, None


def spliced_series(ac_by: dict, base_by: dict, adopted_month: str, b_by: "dict | None" = None) -> "tuple[dict, dict]":
    """({month: weights} of the adopted arm, the same of L). Before the adoption: the `chain` of the arm's own shadow (`b_by` for B, else the AC rows) and the `off` of the AC rows. From it on: the
    `off` (the DRAFT) and the `chain` of the baseline rows."""
    own = b_by if b_by is not None else ac_by
    arm = {m: r["chain"]["weights"] for m, r in own.items() if m < adopted_month}
    low = {m: r["off"]["weights"] for m, r in ac_by.items() if m < adopted_month}
    for m, r in base_by.items():
        if m >= adopted_month:
            arm[m], low[m] = r["off"]["weights"], r["chain"]["weights"]
    return arm, low


def monthly_returns(nav: pd.DataFrame, today: str) -> pd.DataFrame:
    """Month-end NAV panel -> {return month 'YYYY-MM': ISIN returns}, closed months only (strictly before the month of `today`). A missing NAV is never filled.
    A fund's LATEST observed month is provisional and is not read: its row is replaced by every load until a later month exists, and a NAV a few days before month end already carries the month-end label."""
    ends = RL.month_end_rows(nav)
    if ends.empty:
        return pd.DataFrame()
    ends = ends.astype(float)
    month = ends.index.to_period("M")
    for isin in ends.columns:
        seen = nav[isin].last_valid_index()
        if seen is not None:
            ends.loc[month >= seen.to_period("M"), isin] = np.nan
    grid = pd.date_range(ends.index.min(), ends.index.max(), freq="ME")
    out = ends.reindex(grid).pct_change(fill_method=None).iloc[1:]
    out.index = out.index.strftime("%Y-%m")
    return out[out.index < str(today)[:7]].dropna(how="all")


def portfolio_return(rows: list, returns: pd.Series) -> "tuple[float, float]":
    """(gross month return, share of the invested weight without a return). Funds without a return are renormalised away; the cash part earns nothing."""
    w = hs.weights_of(rows)
    invested = sum(w.values())
    ok = {i: v for i, v in w.items() if np.isfinite(returns.get(i, np.nan))}
    covered = sum(ok.values())
    if not covered:
        return float("nan"), 1.0
    return float(invested * sum(v * float(returns[i]) for i, v in ok.items()) / covered), float(1.0 - covered / invested)


def observation_months(arm: dict, low: dict, return_months) -> list:
    """The months with a row in both series, the same in the previous calendar month, and a closed next month."""
    have = set(arm) & set(low)
    closed = set(return_months)
    return [m for m in sorted(have) if _month(_ordinal(m) - 1) in have and _month(_ordinal(m) + 1) in closed]


def observations(arm: dict, low: dict, returns: pd.DataFrame, fees: pd.DataFrame, optimistic: bool = False, adopted_month: "str | None" = None) -> list:
    """One record per observation month: net return (month m+1 on the weights of m, less the cost of the trades of m) and one-way turnover of both series. At the adoption month the adopted arm trades
    from the portfolio really held the month before (L's DRAFT), not from its own shadow chain: flagged `transition`."""
    entry, exit_, de, dx = filled_fees(fees, optimistic=optimistic)
    out = []
    for m in observation_months(arm, low, returns.index):
        prev, nxt = _month(_ordinal(m) - 1), _month(_ordinal(m) + 1)
        transition = m == adopted_month
        rec = {"month": m, "transition": transition}
        if any(np.isnan(portfolio_return(series[m], returns.loc[nxt])[0]) for series in (arm, low)):
            continue                                              # nothing held has a return that month: no observation, never a NaN in the means
        for key, series, before in (("arm", arm, low[prev] if transition else arm[prev]), ("l", low, low[prev])):
            gross, missing = portfolio_return(series[m], returns.loc[nxt])
            cost, _unknown = pair_cost(series[m], before, entry, exit_, de, dx)
            rec.update({f"{key}_net": gross - cost, f"{key}_cost": cost, f"{key}_turnover": one_way_turnover(series[m], before), f"{key}_no_return": missing})
        out.append(rec)
    return out


def max_drawdown(net: list) -> float:
    """The deepest fall of the cumulated net series below its running maximum (the start value included), as a positive fraction."""
    value = peak = 1.0
    worst = 0.0
    for r in net:
        value *= 1.0 + float(r)
        peak = max(peak, value)
        worst = max(worst, 1.0 - value / peak)
    return worst


def _gap_and_drawdowns(obs: list) -> "tuple[float, float, float]":
    """(adopted minus L, mean monthly net return annualised in pp; maximum drawdown of the adopted arm; of L)."""
    gap = 12 * 100 * float(np.mean([o["arm_net"] - o["l_net"] for o in obs]))
    return gap, max_drawdown([o["arm_net"] for o in obs]), max_drawdown([o["l_net"] for o in obs])


def alarm_r12(obs: list) -> dict:
    """R12, the first 12 months of the series: an ALARM on a return gap or a deeper drawdown; no alarm is not a pass."""
    if len(obs) < ALARM_MONTH:
        return {"state": "NOT DUE", "causes": [], "months": len(obs)}
    gap, dd_arm, dd_l = _gap_and_drawdowns(obs[:ALARM_MONTH])
    causes = (["return"] if gap < -REVERT_RETURN_GAP_PP - _EPS else []) + (["drawdown"] if dd_l >= DD_INFORMATIVE_MIN - _EPS and dd_arm - dd_l > REVERT_DD_GAP + _EPS else [])
    return {"state": "ALARM" if causes else "no alarm (not a pass)", "causes": causes, "months": ALARM_MONTH, "return_gap_pp": gap, "dd_arm": dd_arm, "dd_l": dd_l}


def drawdown_guard(dd_arm: float, dd_l: float) -> str:
    """NOT INFORMATIVE while L fell less than 3 %; else PASS only if the arm is no deeper than L + 1 pp and within the 15 % cap (when L itself is deeper than the cap: not worse than L)."""
    if dd_l < DD_INFORMATIVE_MIN - _EPS:
        return "NOT INFORMATIVE"
    if dd_l > DD_CAP + _EPS:
        return "PASS" if dd_arm <= dd_l + _EPS else "FAIL"
    return "PASS" if dd_arm <= DD_CAP + _EPS and dd_arm - dd_l <= DD_TOLERANCE + _EPS else "FAIL"


def check_return(obs: list, months: int) -> dict:
    """R24 / R36 on the first `months` months of the series: non-inferiority of the net return (one-sided lower bound above -0.10 pp a year) and the drawdown guard."""
    if len(obs) < months:
        return {"months": months, "return": "NOT DUE", "drawdown": "NOT DUE", "verdict": "NOT DUE", "have": len(obs)}
    head = obs[:months]
    ni = noninferiority([o["arm_net"] - o["l_net"] for o in head], NI_LEVEL, NI_MARGIN_PP)
    _gap, dd_arm, dd_l = _gap_and_drawdowns(head)
    ret, dd = "PASS" if ni["pass"] else "FAIL", drawdown_guard(dd_arm, dd_l)
    verdict = "FAIL" if "FAIL" in (ret, dd) else ("PASS" if (ret, dd) == ("PASS", "PASS") else "NOT INFORMATIVE")
    return {"months": months, "return": ret, "drawdown": dd, "verdict": verdict, "mean_pp": ni["mean_pp"], "lb_pp": ni["lb_pp"], "dd_arm": dd_arm, "dd_l": dd_l}


def turnover_floor(arm: str) -> float:
    """The smallest realised turnover reduction that does not revert: half of the PIT one."""
    return REVERT_TURNOVER_RATIO * (PIT_TURNOVER["L"] - PIT_TURNOVER[arm]) / PIT_TURNOVER["L"]


def revert_trigger(post: list, arm: str) -> dict:
    """Amendment 5's trigger over every post-adoption month (at least 12). The one-off trade of the adoption month stays in the returns and out of the turnover ratio."""
    if len(post) < REVERT_WINDOW:
        return {"state": "NOT DUE", "causes": [], "months": len(post)}
    gap, dd_arm, dd_l = _gap_and_drawdowns(post)
    steady = [o for o in post if not o.get("transition")]
    to_l = float(np.mean([o["l_turnover"] for o in steady]))
    reduction = (to_l - float(np.mean([o["arm_turnover"] for o in steady]))) / to_l if to_l else None
    floor = turnover_floor(arm)
    causes = ((["return"] if gap < -REVERT_RETURN_GAP_PP - _EPS else []) + (["drawdown"] if dd_arm - dd_l > REVERT_DD_GAP + _EPS else [])
              + (["turnover"] if reduction is not None and reduction < floor - _EPS else []))
    return {"state": "REVERT" if causes else "not triggered", "causes": causes, "months": len(post), "return_gap_pp": gap, "dd_arm": dd_arm, "dd_l": dd_l,
            "turnover_reduction": reduction, "turnover_floor": floor}


def _checks(obs: list, adopted_month: str, arm: str) -> dict:
    res = {"R12": alarm_r12(obs), "revert": revert_trigger([o for o in obs if o["month"] >= adopted_month], arm)}
    res.update({f"R{n}": check_return(obs, n) for n in CHECK_MONTHS})
    reasons = [f"R{n}" for n in CHECK_MONTHS if res[f"R{n}"]["verdict"] == "FAIL"] + (["revert trigger"] if res["revert"]["state"] == "REVERT" else [])
    res["verdict"] = f"REVERT ({', '.join(reasons)})" if reasons else "no revert"
    return res


def review_after_adoption(ac_rows: list, b_rows: list, base_rows: list, returns: "pd.DataFrame | None", fees: "pd.DataFrame | None") -> dict:
    """The post-adoption numbers. Raises NotReady without an adoption or before 12 post-adoption months with a closed next month, so nothing is computed early. With `fees` None only the readiness is
    judged (the caller freezes the fees after it)."""
    ac_by, base_by = last_per_month(ac_rows), last_per_month(base_rows)
    found = adoption(base_by)
    if found is None:
        raise NotReady("NOT READY: no adoption recorded in the baseline stream (every row's build ran arm L)")
    adopted_month, arm = found
    base_by, changed = _until_change(base_by, adopted_month, arm)
    arm_series, low = spliced_series(ac_by, base_by, adopted_month, b_by=last_per_month(b_rows) if arm == "B" else None)
    months = observation_months(arm_series, low, [] if returns is None else returns.index)
    n_post = len([m for m in months if m >= adopted_month])
    if returns is not None and n_post < REVERT_WINDOW:
        raise NotReady(f"NOT READY: {n_post} post-adoption month(s) with a closed next month, the checks need {REVERT_WINDOW}")
    if returns is None or fees is None:
        return {"arm": arm, "adopted_month": adopted_month}
    primary = observations(arm_series, low, returns, fees, adopted_month=adopted_month)
    ac_used = {m: r for m, r in ac_by.items() if m < adopted_month}
    other = last_per_month(b_rows) if arm == "B" else base_by
    mixed = mixed_builds(ac_used, other, ac_used)
    optimistic = observations(arm_series, low, returns, fees, optimistic=True, adopted_month=adopted_month)
    return {"mixed_builds": mixed, "arm": arm, "adopted_month": adopted_month, "changed_month": changed, "months": months, "n_post": n_post, "gaps": gaps({m: 1 for m in arm_series}),
            "max_no_return": max(max(o["arm_no_return"], o["l_no_return"]) for o in primary), "checks": _checks(primary, adopted_month, arm),
            "checks_optimistic": _checks(optimistic, adopted_month, arm)}


def _line_check(name: str, c: dict) -> str:
    if c["verdict"] == "NOT DUE":
        return f"{name}: NOT DUE ({c['have']} of {c['months']} months)"
    return (f"{name}: return {c['return']} (mean {c['mean_pp']:+.2f} pp a year, lower bound {c['lb_pp']:+.2f} against {NI_MARGIN_PP:+.2f}); drawdown {c['drawdown']} "
            f"({c['dd_arm']:.1%} against L {c['dd_l']:.1%}); verdict {c['verdict']}" + (" (carried to the next review, not a pass)" if c["verdict"] == "NOT INFORMATIVE" else ""))


def render_after_adoption(res: dict, snapshot_id: str) -> str:
    c, o = res["checks"], res["checks_optimistic"]
    lines = [f"after adoption: adopted arm {res['arm']} since {res['adopted_month']}; {len(res['months'])} month(s) {res['months'][0]} .. {res['months'][-1]}, {res['n_post']} post-adoption; "
             f"gap(s) {', '.join(res['gaps']) if res['gaps'] else 'none'}; fee snapshot {snapshot_id[:12]}",
             "reading: a row of month m earns month m+1 net of its own trades; R12 / R24 / R36 use the first 12 / 24 / 36 months of the series (one-sided level 0.05); the adoption month trades from L's "
             f"DRAFT and stays out of the turnover ratio; a fund without a return is renormalised away (at most {res['max_no_return']:.1%} of the invested weight in a month)"]
    if res["changed_month"]:
        lines.append(f"the build of {res['changed_month']} ran another arm: the months from it on are not read")
    if res["mixed_builds"]:
        lines.append("before the adoption, " + _mixed_line(res["mixed_builds"]))
    r12 = c["R12"]
    lines.append(f"R12 (alarm only): {r12['state']}" + (f" [{', '.join(r12['causes'])}]" if r12["causes"] else "")
                 + (f"; return gap {r12['return_gap_pp']:+.2f} pp a year" if "return_gap_pp" in r12 else ""))
    lines += [_line_check(f"R{n}", c[f"R{n}"]) for n in CHECK_MONTHS]
    rt = c["revert"]
    lines.append(f"revert trigger: {rt['state']}" + (f" [{', '.join(rt['causes'])}]" if rt["causes"] else ""))
    if "return_gap_pp" in rt:
        reduction = "n/a" if rt["turnover_reduction"] is None else f"{rt['turnover_reduction']:.0%}"
        lines.append(f"    return gap {rt['return_gap_pp']:+.2f} pp a year; drawdown {rt['dd_arm']:.1%} against L {rt['dd_l']:.1%}; turnover reduction {reduction} (floor {rt['turnover_floor']:.0%})")
    lines.append(f"optimistic variant (a fund with no figure costs 0; never gating): {o['verdict']}")
    lines.append(f"verdict: {c['verdict']}" + (". Only the owner's order switches the flags OFF; the baseline shadow keeps running." if c["verdict"] != "no revert" else ""))
    return "\n".join(lines)


def render_baseline_integrity(rows: list, n_errors: int) -> str:
    """The whitelist view of the baseline stream: identity verdict, scenario, funds per sub-portfolio and weight sums. No comparison value."""
    by = last_per_month(rows)
    lines = [f"{BASE_STEP}: {len(rows)} SHADOW row(s), {len(by)} calendar month(s), {n_errors} ERROR row(s)"]
    try:
        found = adoption(by)
        lines.append(f"adoption: {'none' if found is None else f'{found[1]} from {found[0]}'}; gap(s): {', '.join(gaps(by)) if gaps(by) else 'none'}")
    except NotReady as exc:
        lines.append(f"adoption: {exc}")
    for m in sorted(by):
        r = by[m]
        arms = ("off", "one_step", "chain")
        lines.append(f"- {m}  identity {r.get('identity_check')}  scenario {r.get('scenario_id')}  built {r.get('built_at')}")
        lines.append("    funds per sub-portfolio: " + "; ".join(f"{a} {_funds_per_sub(r[a]['weights'])}" for a in arms))
        lines.append("    weight sums: " + "; ".join(f"{a} {sum(float(w['weight']) for w in r[a]['weights']):.4f}" for a in arms))
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------------------------------------------------------------------------------
def run(argv: list, *, rows_ac: list, rows_b: list, n_errors: int, fees: "pd.DataFrame | None" = None, pool: "tuple | None" = None, rows_base: "list | None" = None,
        n_base_errors: int = 0, returns: "pd.DataFrame | None" = None) -> "tuple[int, str]":
    """The whole program on already-read rows: (exit code, text). 0 = shown, 3 = review refused (window not ready), 2 = a review needs the fees (or the returns)."""
    ap = argparse.ArgumentParser(prog="p3_shadow_read")
    ap.add_argument("--review", action="store_true", help="the R6 cost review (refused until the window exists)")
    ap.add_argument("--snapshot", default=None, help="folder of the frozen fee snapshot (frozen from the live fund_master when it does not exist)")
    ap.add_argument("--out", default=None, help="write the review as JSON")
    ap.add_argument("--after-adoption", action="store_true", help="the checks after an adoption (refused until 12 post-adoption months)")
    args = ap.parse_args(argv)
    if args.after_adoption:
        return _run_after_adoption(args, rows_ac, rows_b, rows_base or [], returns, fees)
    if not args.review:
        text = render_integrity(rows_ac, n_errors, pool)
        if rows_base or n_base_errors:
            text += "\n\n" + render_baseline_integrity(rows_base or [], n_base_errors)
        return 0, text
    ready = readiness(last_per_month(rows_ac))
    if not ready["ready"]:
        return 3, ready["reason"]
    got = _snapshot(args.snapshot, "R6", fees)
    if got is None:
        return 2, "a review needs the stated fees: none given and no snapshot folder to load"
    frozen, snapshot_id = got
    try:
        res = review_r6(rows_ac, rows_b, frozen)
    except NotReady as exc:
        return 3, str(exc)
    if args.out:
        Path(args.out).write_text(json.dumps({"snapshot_id": snapshot_id, **res}, indent=2, default=str), encoding="utf-8")
    return 0, render_review(res, snapshot_id)


def _snapshot(path: "str | None", label: str, fees: "pd.DataFrame | None") -> "tuple[pd.DataFrame, str] | None":
    """(fees, snapshot id) of the frozen snapshot at `path` (or today's folder of `label`), freezing `fees` there when it does not exist; None when there is neither."""
    folder = Path(path) if path else _ROOT / "out" / "audit" / "p3_shadow" / f"{datetime.now(timezone.utc):%Y%m%d}_{label}" / "snapshot"
    if folder.exists():
        return load_fees(folder)
    if fees is None:
        return None
    snapshot_id = freeze_fees(folder, fees)["snapshot_id"]
    return load_fees(folder)[0], snapshot_id


def _run_after_adoption(args, rows_ac: list, rows_b: list, rows_base: list, returns, fees) -> "tuple[int, str]":
    """Readiness first (no fee frozen, no value computed before it), then the frozen fees, then the checks."""
    returns = None if returns is None or returns.empty else returns
    try:
        review_after_adoption(rows_ac, rows_b, rows_base, returns, None)                  # raises NotReady: adoption and months
        if returns is None:
            return 2, "the checks need monthly NAV returns: none were read"
        got = _snapshot(args.snapshot, "POST", fees)
        if got is None:
            return 2, "the checks need the stated fees: none given and no snapshot folder to load"
        frozen, snapshot_id = got
        res = review_after_adoption(rows_ac, rows_b, rows_base, returns, frozen)
    except NotReady as exc:
        return 3, str(exc)
    if args.out:
        Path(args.out).write_text(json.dumps({"snapshot_id": snapshot_id, **res}, indent=2, default=str), encoding="utf-8")
    return 0, render_after_adoption(res, snapshot_id)


def _read_live(argv: list):
    """The keyword arguments of `run` from the database. SELECT only: the streams, their ERROR counts, and the live fees and month-end returns of the held funds when a review asks for them."""
    from shared.db import get_connection
    conn = get_connection()
    try:
        review, after = "--review" in argv, "--after-adoption" in argv
        rows_ac = hs.read_rows(conn, ROWS_LIMIT, step=AC_STEP)
        rows_b = hs.read_rows(conn, ROWS_LIMIT, step=B_STEP) if review or after else []
        rows_base = hs.read_rows(conn, ROWS_LIMIT, step=BASE_STEP)
        n_errors = int(conn.execute("SELECT COUNT(*) FROM ingestion_log WHERE step = %s", (AC_STEP_ERROR,)).fetchone()[0])
        n_base_errors = int(conn.execute("SELECT COUNT(*) FROM ingestion_log WHERE step = %s", (BASE_STEP_ERROR,)).fetchone()[0])
        fees = pool = returns = None
        if after:
            from proyecto3.src.pit_inputs import load_nav_panel
            held = sorted({w["isin"] for r in [*rows_ac, *rows_b, *rows_base] for k in ("off", "chain") for w in (r.get(k) or {}).get("weights", [])})
            if held:
                returns = monthly_returns(load_nav_panel(conn, held), today=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        if review or after:
            snap = _arg_value(argv, "--snapshot")
            if not (snap and Path(snap).exists()):
                rows = conn.execute("SELECT ISIN, Entry_Fee_Pct, Exit_Fee_Pct, Entry_Fee_Pct_Max, Exit_Fee_Pct_Max, Fund_Nature FROM fund_master").fetchall()
                fees = pd.DataFrame([tuple(r) for r in rows], columns=["isin", *SNAPSHOT_COLUMNS]).set_index("isin")
                fees[[*FEE_COLUMNS, *CEILING_COLUMNS]] = fees[[*FEE_COLUMNS, *CEILING_COLUMNS]].apply(pd.to_numeric, errors="coerce")
        else:
            pool = tuple(conn.execute(
                "SELECT COUNT(*), COUNT(*) FILTER (WHERE (Entry_Fee_Pct IS NOT NULL OR Entry_Fee_Pct_Max IS NOT NULL) AND (Exit_Fee_Pct IS NOT NULL OR Exit_Fee_Pct_Max IS NOT NULL)) "
                "FROM fund_master WHERE ISIN IN (SELECT DISTINCT isin FROM fund_scores WHERE eligible = 1 AND calculated_at = (SELECT MAX(calculated_at) FROM fund_scores))").fetchone())
        return {"rows_ac": rows_ac, "rows_b": rows_b, "n_errors": n_errors, "fees": fees, "pool": pool, "rows_base": rows_base, "n_base_errors": n_base_errors, "returns": returns}
    finally:
        conn.close()


def _arg_value(argv: list, name: str):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None


def main(argv: "list | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    rc, text = run(argv, **_read_live(argv))
    print(text)
    return rc


if __name__ == "__main__":
    sys.exit(main())
