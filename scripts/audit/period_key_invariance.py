"""R-9 standing audit: does an indicator change when NAV dates are snapped to the calendar period key? (FND-0300.)

Promoted from the one-off reliability study of 2026-10-18 (`out/audit/p2_reliability_20261018/invariance_harness.py`, whose REPORT.md is the
evidence behind R-9) so the measurement runs every cycle instead of once. The four controls that made the study's result trustworthy are kept
verbatim -- they are the reason the numbers can be believed, not scaffolding.

READ-ONLY. Runs the production per-fund computation (proyecto2/src/pipeline/run_pipeline.py::run with dry_run=True, force=True) in two modes and
captures every row the writers would persist (the writers are replaced by capturing stubs; the database connection is forced read-only):

  raw     production behaviour: raw fund_nav_monthly dates
  snapped the fund's own NAV (load_nav) AND every peer series (peer_panel._nature_frame) moved to the calendar month-end,
          a same-month duplicate keeps the last

Controls (any failure stops before results are read): determinism (raw twice on a subset), fidelity (raw vs the stored fund_metrics),
no-writes (row counts of the persisted tables unchanged).  Predictions are written to predictions.json BEFORE the first run.

NON-BLOCKING by contract (`shared/governance/controls.py`: T-PERIOD-KEY-INVARIANCE, consequence WARN). It never decides to stop a cycle; a
family that is predicted invariant and is not becomes an `[ATENCION]` line in the cycle report. Exit codes: 0 nothing to report, 1 a finding
(a prediction did not hold), 2 a control failed so the run proves nothing.

    C:\\Users\\jacor\\.conda\\envs\\des\\python.exe -X utf8 scripts/audit/period_key_invariance.py --contract-only     # the data contract, seconds
    C:\\Users\\jacor\\.conda\\envs\\des\\python.exe -X utf8 scripts/audit/period_key_invariance.py --n 120             # cycle diagnostic
    C:\\Users\\jacor\\.conda\\envs\\des\\python.exe -X utf8 scripts/audit/period_key_invariance.py --n 400             # the study's own sample size
"""
import argparse
import json
import os
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
# The repository root is derived from this file's own location (scripts/audit/<me>.py), never hard-coded: NORMAS_BATCH.md's rule for
# launchers is the same rule for the tools they call, and a worktree must run its own code.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "proyecto2"))
from shared.config import load_env_file

for _k, _v in load_env_file(str(ROOT / ".env")).items():
    os.environ.setdefault(_k, _v)

from shared.config import REPORT_DIRS                                      # noqa: E402  (after the sys.path bootstrap)

#: Artifacts go under the governed report tree (FND-0270), one stamped folder per run -- never next to the script.
OUT = REPORT_DIRS["p2"] / "period_key_invariance" / time.strftime("%Y%m%d_%H%M%S")

import numpy as np
import pandas as pd

from shared import period_key_contract as pkc                              # noqa: E402
from shared.period_key import is_period_complete, to_period_key            # noqa: E402

# ------------------------------------------------------------------ predictions (fixed before any run)
# family -> (expectation, rule).  "invariant": changed share <= 1% of compared values and no validity flips beyond 0.5% of funds;
# "changes": a material effect is EXPECTED (changed share >= 5% for the calendar-stamped funds); "small": between the two.
PREDICTIONS = {
    "capture": ("invariant", "both sides are snapped to month-end inside the production code"),
    "fx": ("invariant", "NAV and FX both snapped to month-end"),
    "regime": ("invariant", "NAV snapped to month-end, regime index snapped"),
    "risk_since_inception": ("invariant", "positional returns; annualisation by month ordinals"),
    "momentum": ("invariant", "positional window; peers are stored scalars (FND-0297: non-reproducible in the raw/raw control)"),
    "short": ("invariant", "daily NAV, untouched by the monthly convention"),
    # rolling and macro were pre-registered "changes" (raw-date DateOffset cutoff: 12 vs 13 rows; raw NAV inner-joined to a month-end
    # index) and were fixed by FND-0300 (NAV labelled at the reader, NAV_MONTH_END_ALIGN_ENABLED ON since 2026-10-19). They are now
    # guarded invariants: with the switch OFF they would alarm, which is the point.
    "rolling": ("invariant", "FND-0300: month-end labelled NAV, window = 12 returns whatever the stamp (was DateOffset on raw dates)"),
    # The 2026-10-18 study PRE-REGISTERED "changes" here and the data REFUTED it (2.4%, 6 funds, all calendar-stamped): the
    # suspected 12-vs-13-row gate does not bite. The original prediction is kept in the study's own predictions.json, which is
    # the pre-registration record; a standing audit must encode what is now KNOWN, or this family would score "prediction
    # failed" every cycle for being correct. Changed deliberately, once, with the evidence named -- not tuned to the result.
    "srri_rolling_1y": ("invariant", "pre-registered as 'changes' (srri.py:100's >= 13 row gate); REFUTED by the 2026-10-18 "
                                     "study at 2.4% of values and 0 validity flips, so it is now a guarded invariant"),
    "persistence": ("small", "raw-date windows and drifting DateOffset(6) steps (persistence.py:185,212)"),
    "macro": ("invariant", "FND-0300: month-end labelled NAV joins the month-end factor index (was a raw-date inner join, ~30% of months lost)"),
}
FAMILY_OF = {  # metric-name prefix -> family; first match wins
    "beta_": "macro", "macro_": "macro", "energy_sensitivity": "macro", "hy_spread_sensitivity": "macro",
    "alpha_persistence": "persistence", "upside_capture": "capture", "downside_capture": "capture", "capture_ratio": "capture",
    "fx_": "fx", "momentum": "momentum", "short_": "short", "n_obs_": "regime", "regime_coverage": "regime", "crisis_stress": "regime",
}
REGIME_SUFFIXES = ("_contraccion", "_crisis_financiera", "_estanflacion", "_expansion", "_recalentamiento", "_recalentamiento_tardio", "_shock_energetico")
PREDICTIONS["crisis"] = ("small", "slice_window on raw dates (time_windows.py): only the window edges can differ")


PREDICTIONS["freshness"] = ("excluded", "nav_data_freshness_delta is defined by dates (last NAV vs run date): not an invariance candidate")
MATERIAL_REL = 0.01      # a value "changed materially" when it moves by more than 1% relative (and more than MATERIAL_ABS in absolute terms)
MATERIAL_ABS = 1e-6
PILOT_NOTE = ("2026-10-18, pilot on 6 funds BEFORE the real sample: (1) 'changed' was an exact comparison, so FX-date effects of ~0.4% in capture counted as "
              "failures; scoring now uses MATERIAL change (> 1% relative and > 1e-6 absolute); the exact share is still reported; (2) a trailing partial "
              "month is not moved to a future month-end; (3) nav_data_freshness_delta is date-defined and excluded. No prediction was changed.")


def family_of(metric: str, horizon: str) -> str:
    if metric == "nav_data_freshness_delta":
        return "freshness"
    if horizon in ("rolling_1m", "rolling_3m", "rolling_6m"):
        return "short"
    if metric == "srri_nav" and horizon == "rolling_1y":
        return "srri_rolling_1y"
    for p, f in FAMILY_OF.items():
        if metric.startswith(p):
            return f
    if metric.endswith(REGIME_SUFFIXES):
        return "regime"
    if horizon.startswith("rolling_"):
        return "rolling"
    if horizon.startswith("crisis_"):
        return "crisis"
    return "risk_since_inception" if horizon == "since_inception" else "risk_other"


def month_end(d) -> pd.Timestamp:
    return to_period_key([d])[0]


def snap_frame(df: pd.DataFrame, key_cols=("isin",)) -> pd.DataFrame:
    """Dates REPLACED by their canonical period key; a same-month duplicate (per key) keeps the last row. A trailing PARTIAL month (the newest
    NAV is more than PARTIAL_TAIL_DAYS before its month-end) keeps its raw dates: moving it to a future month-end would invent a date
    (pilot finding, 2026-10-18; R-9 rule 7).

    This REPLACES the date column rather than adding one beside it (which is what shared.period_key.snap_frame does), because the frame is fed
    straight back into production code that reads `date`. The key itself comes from `to_period_key`, so the audit measures the convention R-9
    actually mandates and cannot drift from it."""
    if df is None or len(df) == 0:
        return df
    out = df.copy()
    raw = pd.to_datetime(out["date"])
    snapped = pd.Series(to_period_key(raw).to_numpy(), index=raw.index)
    last = raw.max()
    if not is_period_complete(last):
        partial = snapped == month_end(last)
        snapped = snapped.where(~partial, raw)
    out["date"] = snapped
    keys = [c for c in key_cols if c in out.columns] + ["date"]
    return out.sort_values(keys).drop_duplicates(keys, keep="last").reset_index(drop=True)


# ------------------------------------------------------------------ the data contract (R-9 deliverable D)
def contract_check(conn) -> dict:
    """Which stamp convention each stored series actually uses, against `shared/period_key_contract.EXPECTED_STAMPS`.

    Read-only and cheap (three aggregates and one GROUP BY), so the cycle report can afford it every run. The verdict logic is in
    shared/period_key_contract.py and is shared with scripts/launch/p1p2_cycle_report.py -- one definition of the contract, two callers."""
    row = conn.execute("""
        WITH d AS (SELECT isin, date::date AS dt, (date_trunc('month', date::date) + interval '1 month - 1 day')::date AS me
                   FROM fund_nav_monthly)
        SELECT COUNT(*),
               SUM(CASE WHEN dt <> me THEN 1 ELSE 0 END),
               (SELECT COUNT(*) FROM (SELECT 1 FROM d GROUP BY date_trunc('month', dt) HAVING COUNT(DISTINCT dt) > 1) x),
               (SELECT COUNT(*) FROM (SELECT 1 FROM d GROUP BY isin, date_trunc('month', dt) HAVING COUNT(*) > 1) y)
        FROM d""").fetchone()
    total, off, dup_months, dup_isin_month = int(row[0] or 0), int(row[1] or 0), int(row[2] or 0), int(row[3] or 0)
    nav = pkc.assess_nav(off / total if total else 0.0, dup_months, dup_isin_month)
    nav["rows"] = total

    macro_rows = conn.execute("""
        SELECT indicator, geography, COUNT(*),
               SUM(CASE WHEN EXTRACT(day FROM date) = 1 THEN 1 ELSE 0 END),
               SUM(CASE WHEN date::date = (date_trunc('month', date::date) + interval '1 month - 1 day')::date THEN 1 ELSE 0 END)
        FROM series_macro GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()
    macro = pkc.assess_macro_stamps(
        [{"indicator": r[0], "geography": r[1], "n_rows": r[2], "n_month_start": r[3], "n_month_end": r[4]} for r in macro_rows])

    infl = conn.execute("""
        SELECT COUNT(*), SUM(CASE WHEN EXTRACT(day FROM date) = 1 THEN 1 ELSE 0 END) FROM series_inflation""").fetchone()
    inflation = {"rows": int(infl[0] or 0), "month_start": int(infl[1] or 0)}

    return {"nav": nav, "macro": macro, "inflation": inflation,
            "attention": pkc.attention_lines(nav, macro)}


# ------------------------------------------------------------------ sample
def build_sample(conn, n: int, seed: int) -> pd.DataFrame:
    cur = conn.execute("""
        SELECT fm.isin, fm.fund_nature AS nature, COALESCE(fm.in_current_universe, 0) AS active,
               COUNT(*) AS rows, MIN(n.date) AS first, MAX(n.date) AS last,
               AVG(CASE WHEN n.date = (date_trunc('month', n.date) + interval '1 month - 1 day')::date THEN 0.0 ELSE 1.0 END) AS off_share
        FROM silver.fund_master fm JOIN fund_nav_monthly n ON n.isin = fm.isin
        GROUP BY 1, 2, 3 HAVING COUNT(*) >= 12""")
    df = pd.DataFrame([tuple(r) for r in cur.fetchall()], columns=[d.name for d in cur.description])
    df["hist"] = pd.cut(df["rows"], [0, 60, 120, 10_000], labels=["<5y", "5-10y", ">10y"])
    df["stamp"] = np.where(df["off_share"] > 0.15, "weekday", "calendar")
    cells = df.groupby(["nature", "hist", "stamp"], observed=True)
    quota = max(2, int(np.ceil(n / max(1, cells.ngroups))))
    rng = np.random.default_rng(seed)
    parts = [g.sample(min(len(g), quota), random_state=int(rng.integers(1 << 31))) for _, g in cells]
    s = pd.concat(parts)
    if len(s) > n:
        s = s.sample(n, random_state=seed)
    return s.sort_values("isin").reset_index(drop=True)


# ------------------------------------------------------------------ one mode
def run_mode(mode: str, isins: list) -> tuple:
    """-> (metrics {(isin, metric, horizon, real_flag): value}, timeseries {(isin, metric, window, month_end, real_flag): value})"""
    import dataclasses

    import src.calculations.peer_panel as pp
    import src.pipeline.run_pipeline as rp
    import shared.db as shdb

    cap, ts = {}, {}

    def put(isin, metrics, horizon):
        for m in metrics:
            v = m["value"]
            cap[(isin, m["metric"], horizon, m["real_flag"])] = None if (v is None or (isinstance(v, float) and v != v)) else float(v)

    def w_metrics(conn, isin, metrics, horizon, dry_run, **kw):
        put(isin, metrics, horizon)
        return len(metrics)

    def w_batch(conn, items, dry_run, **kw):
        for isin, horizon, metrics in items:
            put(isin, metrics, horizon)
        return sum(len(m) for _, _, m in items)

    def w_ts(conn, rows, dry_run, **kw):
        for r in rows:
            v = r["value"]
            ts[(r["isin"], r["metric"], r["window"], month_end(r["date"]), r["real_flag"])] = None if (v is None or (isinstance(v, float) and v != v)) else float(v)
        return len(rows)

    orig_get, orig_load_nav, orig_nature = shdb.get_connection, rp.load_nav, pp._nature_frame
    orig_specs = rp._PER_FUND_METRIC_FAMILIES
    orig_writers = (rp._write_metrics, rp._replace_beta_set, rp._write_metrics_batch, rp._write_timeseries)

    def ro_conn(*a, **k):
        c = orig_get(*a, **k)
        c.execute("SET default_transaction_read_only = on")          # any write attempt now raises
        c.commit()                                                    # a session SET issued inside a transaction is undone by a later ROLLBACK
        return c

    try:
        rp.get_connection = ro_conn
        shdb.get_connection = ro_conn
        rp._write_metrics = rp._replace_beta_set = w_metrics
        rp._write_metrics_batch, rp._write_timeseries = w_batch, w_ts
        rp._PER_FUND_METRIC_FAMILIES = [dataclasses.replace(s, writer=w_metrics) for s in orig_specs]
        if mode == "snapped":
            rp.load_nav = lambda conn, isin: snap_frame(orig_load_nav(conn, isin), key_cols=())
            pp._nature_frame = lambda conn, nature: snap_frame(orig_nature(conn, nature), key_cols=("isin",))
        rc = rp.run(isins=isins, dry_run=True, force=True)
    finally:
        rp.get_connection, shdb.get_connection = orig_get, orig_get
        rp.load_nav, pp._nature_frame = orig_load_nav, orig_nature
        rp._PER_FUND_METRIC_FAMILIES = orig_specs
        rp._write_metrics, rp._replace_beta_set, rp._write_metrics_batch, rp._write_timeseries = orig_writers
        pp.reset()
    print(f"  mode={mode}: run() rc={rc}; captured {len(cap)} metric rows and {len(ts)} timeseries rows for {len(isins)} funds")
    return cap, ts


# ------------------------------------------------------------------ comparison
def compare(a: dict, b: dict, rtol=1e-9, atol=1e-12) -> pd.DataFrame:
    """Per (family, metric, horizon, real_flag): values compared key by key between mode a and mode b."""
    keys = set(a) | set(b)
    recs = {}
    for k in keys:
        isin, metric, horizon, rf = k[0], k[1], k[2], k[-1]
        g = (family_of(metric, horizon), metric, horizon, rf)
        r = recs.setdefault(g, {"n_both": 0, "valid_to_null": 0, "null_to_valid": 0, "only_a": 0, "only_b": 0, "changed": 0, "material": 0, "xs": [], "ys": []})
        va, vb = a.get(k, "missing"), b.get(k, "missing")
        if va == "missing":
            r["only_b"] += 1
            continue
        if vb == "missing":
            r["only_a"] += 1
            continue
        r["n_both"] += 1
        if va is not None and vb is None:
            r["valid_to_null"] += 1
        elif va is None and vb is not None:
            r["null_to_valid"] += 1
        elif va is not None and vb is not None:
            r["xs"].append(va)
            r["ys"].append(vb)
            if abs(va - vb) > atol + rtol * max(abs(va), abs(vb)):
                r["changed"] += 1
            if abs(va - vb) > MATERIAL_ABS and abs(va - vb) > MATERIAL_REL * max(abs(va), abs(vb), 1e-12):
                r["material"] += 1
    rows = []
    for (fam, metric, horizon, rf), r in recs.items():
        x, y = np.array(r["xs"]), np.array(r["ys"])
        n = len(x)
        rel = np.abs(y - x) / np.maximum(np.abs(x), 1e-12) if n else np.array([])
        sp = pd.Series(x).corr(pd.Series(y), method="spearman") if n > 5 and np.ptp(x) > 0 and np.ptp(y) > 0 else np.nan
        rows.append({"family": fam, "metric": metric, "horizon": horizon, "real_flag": rf, "n_both": r["n_both"], "n_values": n,
                     "valid_to_null": r["valid_to_null"], "null_to_valid": r["null_to_valid"], "only_a": r["only_a"], "only_b": r["only_b"],
                     "changed_share": r["changed"] / n if n else np.nan, "material_share": r["material"] / n if n else np.nan, "spearman": sp,
                     "sign_agree": float((np.sign(x) == np.sign(y)).mean()) if n else np.nan,
                     "median_rel_change": float(np.median(rel)) if n else np.nan, "p95_rel_change": float(np.quantile(rel, .95)) if n else np.nan})
    return pd.DataFrame(rows).sort_values(["family", "metric", "horizon", "real_flag"]).reset_index(drop=True)


def family_scoreboard(cmp: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for fam, g in cmp.groupby("family"):
        n_vals = g["n_values"].sum()
        exact = float((g["changed_share"].fillna(0) * g["n_values"]).sum() / n_vals) if n_vals else np.nan
        changed = float((g["material_share"].fillna(0) * g["n_values"]).sum() / n_vals) if n_vals else np.nan      # scoring uses MATERIAL change
        flips = int(g["valid_to_null"].sum() + g["null_to_valid"].sum() + g["only_a"].sum() + g["only_b"].sum())
        compared = int(g["n_both"].sum() + g["only_a"].sum() + g["only_b"].sum())
        flip_share = flips / compared if compared else np.nan
        exp = PREDICTIONS.get(fam, ("?", ""))[0]
        if exp == "invariant":
            ok = bool(changed <= 0.01 and flip_share <= 0.005)
        elif exp == "changes":
            ok = bool(changed >= 0.05 or flip_share >= 0.05)
        elif exp == "small":
            ok = bool(changed <= 0.25 and flip_share <= 0.05)
        else:
            ok = None
        rows.append({"family": fam, "predicted": exp, "material_share": changed, "exact_changed_share": exact, "validity_flip_share": flip_share,
                     "values_compared": int(n_vals),
                     "prediction_holds": ok, "why_predicted": PREDICTIONS.get(fam, ("", ""))[1]})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ main
def _persisted_row_counts(conn) -> dict:
    """Row counts of the tables a run must not write. One literal statement per table, so the EXPLAIN sweep can resolve each."""
    return {
        "fund_metrics": conn.execute("SELECT COUNT(*) FROM fund_metrics").fetchone()[0],
        "fund_metric_timeseries": conn.execute("SELECT COUNT(*) FROM fund_metric_timeseries").fetchone()[0],
        "ingestion_log": conn.execute("SELECT COUNT(*) FROM ingestion_log").fetchone()[0],
        "p2_pipeline_log": conn.execute("SELECT COUNT(*) FROM p2_pipeline_log").fetchone()[0],
        "fund_metric_state": conn.execute("SELECT COUNT(*) FROM fund_metric_state").fetchone()[0],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20261018)
    ap.add_argument("--control-n", type=int, default=60, help="funds in the raw/raw determinism control")
    ap.add_argument("--skip-controls", action="store_true")
    ap.add_argument("--contract-only", action="store_true",
                    help="only the R-9 data contract (which stamp each stored series uses): seconds, no pipeline run")
    ap.add_argument("--out-dir", default=None, help="override the stamped output folder")
    args = ap.parse_args()
    t0 = time.time()

    global OUT
    if args.out_dir:
        OUT = Path(args.out_dir)
    OUT.mkdir(parents=True, exist_ok=True)

    if args.contract_only:
        from shared.db import get_connection
        with get_connection() as _c:
            contract = contract_check(_c)
        (OUT / "data_contract.json").write_text(json.dumps(contract, indent=2, default=str), encoding="utf-8")
        nav = contract["nav"]
        print(f"fund_nav_monthly: {nav['rows']} rows, {100 * nav['off_month_end_share']:.1f}% off the calendar month-end "
              f"({100 * nav['off_month_end_drift']:+.1f} pp vs baseline), {nav['duplicate_months']} months with two raw dates, "
              f"{nav['isin_month_duplicates']} (isin, month) duplicates")
        print(f"series_inflation: {contract['inflation']['month_start']} of {contract['inflation']['rows']} rows on the month start")
        for m in contract["macro"]:
            if m["state"] != "ok":
                print(f"  [{m['state']}] series_macro {m['indicator']}/{m['geography']}: observed={m['observed']} expected={m['expected']}")
        for line in contract["attention"]:
            print(f"[ATENCION] {line}")
        print(f"\ndone in {time.time() - t0:.0f} s; files in {OUT}")
        return 1 if contract["attention"] else 0
    (OUT / "predictions.json").write_text(json.dumps({"pilot_note": PILOT_NOTE, "material_change": {"relative": MATERIAL_REL, "absolute": MATERIAL_ABS},
                                                      "rules": {"invariant": "material share <= 1% and validity flips <= 0.5%", "changes": "material share >= 5% or flips >= 5%",
                                                                "small": "material share <= 25% and flips <= 5%"},
                                                      "families": {k: {"expected": v[0], "because": v[1]} for k, v in PREDICTIONS.items()}}, indent=2), encoding="utf-8")
    from shared.db import get_connection
    conn = get_connection()
    sample = build_sample(conn, args.n, args.seed)
    sample.to_csv(OUT / "sample.csv", index=False)
    isins = sample["isin"].tolist()
    print(f"sample: {len(isins)} funds | by nature {sample['nature'].value_counts().to_dict()} | stamp {sample['stamp'].value_counts().to_dict()} | "
          f"hist {sample['hist'].value_counts().to_dict()}")

    counts0 = _persisted_row_counts(conn)

    print("raw run ...")
    raw, raw_ts = run_mode("raw", isins)
    if not args.skip_controls:
        print(f"control 1: determinism (raw twice on {args.control_n} funds) ...")
        sub = isins[:args.control_n]
        again, _ = run_mode("raw", sub)
        first = {k: v for k, v in raw.items() if k[0] in set(sub)}
        det = compare(first, again)
        det.to_csv(OUT / "control_determinism.csv", index=False)
        bad = det[(det["changed_share"].fillna(0) > 0) | (det["valid_to_null"] + det["null_to_valid"] + det["only_a"] + det["only_b"] > 0)]
        mom = bad[bad["family"] == "momentum"]
        other = bad[bad["family"] != "momentum"]
        print(f"  non-deterministic groups: {len(bad)} (momentum {len(mom)}, other {len(other)})")
        if len(other):
            print(other.head(10).to_string())
            print("STOP: raw/raw differs outside momentum (FND-0297). Results are not trustworthy."); return 2
        print("control 2: fidelity (raw vs stored fund_metrics) ...")
        cur = conn.execute("SELECT isin, metric, horizon, real_flag, value FROM fund_metrics WHERE isin = ANY(%s) AND metric_version = 'v1'", (isins,))
        stored = {(r[0], r[1], r[2], r[3]): (None if r[4] is None else float(r[4])) for r in cur.fetchall()}
        common = [k for k in raw if k in stored]
        eq = sum(1 for k in common if (raw[k] is None and stored[k] is None) or (raw[k] is not None and stored[k] is not None and abs(raw[k] - stored[k]) <= 1e-9 + 1e-9 * abs(stored[k])))
        fid = eq / len(common) if common else 0.0
        print(f"  fidelity {fid:.4f} over {len(common)} comparable rows (captured {len(raw)}, stored {len(stored)})")
        (OUT / "control_fidelity.json").write_text(json.dumps({"fidelity": fid, "comparable": len(common), "captured": len(raw), "stored": len(stored)}), encoding="utf-8")
        if fid < 0.95:
            print("STOP: fidelity below 95%."); return 2
    print("snapped run ...")
    snap, snap_ts = run_mode("snapped", isins)
    counts1 = _persisted_row_counts(conn)
    (OUT / "control_no_writes.json").write_text(json.dumps({"before": counts0, "after": counts1}), encoding="utf-8")
    if counts0 != counts1:
        print("STOP: persisted row counts changed", counts0, counts1); return 2
    import pickle
    with open(OUT / "captured.pkl", "wb") as fh:
        pickle.dump({"raw": raw, "snap": snap, "raw_ts": raw_ts, "snap_ts": snap_ts}, fh)       # for the per-stratum analysis (analyze_strata.py)
    cmp = compare(raw, snap)
    cmp.to_csv(OUT / "invariance_by_metric.csv", index=False, float_format="%.6g")
    board = family_scoreboard(cmp)
    board.to_csv(OUT / "invariance_scoreboard.csv", index=False, float_format="%.6g")
    ts_cmp = compare(raw_ts, snap_ts)
    ts_cmp.to_csv(OUT / "invariance_timeseries.csv", index=False, float_format="%.6g")
    pd.set_option("display.width", 220)
    print("\n== SCOREBOARD (prediction vs observation)\n", board.round(4).to_string(index=False))

    # The data contract rides along: the same run that measures invariance also records which stamp each stored series carries,
    # so a convention change and its effect are read side by side rather than in two reports a month apart.
    contract = contract_check(conn)
    (OUT / "data_contract.json").write_text(json.dumps(contract, indent=2, default=str), encoding="utf-8")

    # A finding is a family that was PREDICTED invariant and is not. A family predicted to change and changing is the known
    # defect, already ticketed, and must not raise an alarm every cycle (that is how alarms stop being read).
    broke = board[(board["predicted"] == "invariant") & (board["prediction_holds"] == False)]       # noqa: E712 (pandas mask)
    attention = list(contract["attention"])
    for r in broke.itertuples(index=False):
        attention.append(f"la familia {r.family}, que R-9 da por invariante a la convencion de fecha, cambia en "
                         f"{100 * r.material_share:.1f}% de {r.values_compared} valores "
                         f"(flips de validez {100 * r.validity_flip_share:.1f}%): abrir ticket (R-9)")
    (OUT / "attention.json").write_text(json.dumps(attention, indent=2), encoding="utf-8")
    for line in attention:
        print(f"[ATENCION] {line}")
    print(f"\ndone in {time.time() - t0:.0f} s; files in {OUT}")
    return 1 if attention else 0


if __name__ == "__main__":
    sys.exit(main())
