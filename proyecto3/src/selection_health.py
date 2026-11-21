# proyecto3/src/selection_health.py
# -*- coding: utf-8 -*-
"""P3-1.6 -- `selection_health`: standing monitors of the scoring and selection process (roadmap of 2026-11-18, doc/backlog/PKG_L_p3_alignment.md, FND-0430).

Why. Until now the reliability of the selection was checked by one-off studies. These monitors turn the questions "did the ranking move a lot", "is the top concentrated", "did the
eligible set change" into numbers per (date, sub-portfolio), so a change in the process shows up in the month it happens. E-7 (a partial scoring run) is watched here through the
eligible-set change: a run that scored only part of the universe makes it jump.

Calibration mode. A threshold taken from a guess raises false alarms and erodes the zero-touch principle (Directive D-0). Thresholds are therefore EMPIRICAL percentiles of the
same monitors over the point-in-time history (`calibrate`), and the live block stays report-only until the owner has read them. An alert never blocks anything.

Monitors (each per date and sub-portfolio; top set = the `n` best eligible funds by score_final with score > 0, ties by ISIN):
  eligible            number of eligible funds
  eligible_change     |E_t symmetric-difference E_t-1| / |E_t-1|   (the share of the eligible set that entered or left since the previous date)
  top_turnover        1 - |top_t intersect top_t-1| / n_t          (how much of the top changed)
  top_nature_max      the largest number of top funds of one Fund_Nature
  top_manager_max     the largest number of top funds of one manager (needs the manager of each fund)
Live only, because the PIT scores do not carry them: the share of score weight sitting on a missing metric (`nan_weight_share` works on the metrics frame the live scorer loads) and the
counts of funds under a data hold or a data-suspicion flag. THE LIVE BLOCK IS WIRED (FND-0430, `run_live`, called by scripts/launch/p3_build_portfolio.py after the build, kill switch
`P3_SELECTION_HEALTH_ENABLED`, limits `P3_SELECTION_HEALTH_LIMITS` with their provenance in shared/config.py): report-only and fail-soft; the change monitors compare the current run with
the latest persisted run strictly before today; concentration is measured on the CONSTRUCTED portfolio. Weak spot: a partial run (E-7) must drop about a third of the eligible set to stand out.
Pure except the three loaders, which reach the database only through `conn.execute` of a literal statement.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MONITORS = ("eligible", "eligible_change", "top_turnover", "top_nature_max", "top_manager_max")
QUANTILES = (0.50, 0.90, 0.95, 0.99)


def _top_set(block: pd.DataFrame, n: int) -> list:
    ok = block[block["eligible"] & (block["score_final"] > 0)]
    return ok.sort_values(["score_final", "isin"], ascending=[False, True])["isin"].head(n).tolist()


def health_series(scores: pd.DataFrame, manager: "pd.Series | None" = None, n: int = 10) -> pd.DataFrame:
    """The monitors per (as_of, subportfolio), indexed by those two keys, in time order. `scores`: as_of, subportfolio, isin, fund_nature, eligible, score_final.
    `manager`: Series indexed by ISIN (without it `top_manager_max` is NaN). The first date of each sub-portfolio has no previous date: its change monitors are NaN."""
    rows = []
    for sub, g in scores.groupby("subportfolio", sort=True):
        prev_e, prev_top = None, None
        for as_of, block in sorted(g.groupby("as_of"), key=lambda kv: kv[0]):
            elig = set(block.loc[block["eligible"], "isin"])
            top = _top_set(block, n)
            nat = block.set_index("isin")["fund_nature"].reindex(top).value_counts()
            mgr = manager.reindex(top).value_counts() if manager is not None else None
            rows.append({"as_of": as_of, "subportfolio": sub, "eligible": len(elig),
                         "eligible_change": (len(elig ^ prev_e) / max(len(prev_e), 1)) if prev_e is not None else np.nan,
                         "top_turnover": (1.0 - len(set(top) & set(prev_top)) / max(len(top), 1)) if prev_top is not None and top else np.nan,
                         "top_nature_max": float(nat.max()) if len(nat) else np.nan,
                         "top_manager_max": float(mgr.max()) if mgr is not None and len(mgr) else np.nan})
            prev_e, prev_top = elig, top
    return pd.DataFrame(rows).set_index(["as_of", "subportfolio"]).sort_index()


def calibrate(health: pd.DataFrame, quantiles=QUANTILES) -> pd.DataFrame:
    """Empirical quantiles of every monitor per sub-portfolio over the history (NaN months dropped). Index (subportfolio, monitor), columns q50 / q90 / ..., max and n."""
    out = {}
    for sub, g in health.groupby(level="subportfolio"):
        for m in MONITORS:
            v = g[m].dropna()
            out[(sub, m)] = {**{f"q{int(q * 100)}": float(v.quantile(q)) if len(v) else np.nan for q in quantiles}, "max": float(v.max()) if len(v) else np.nan, "n": int(len(v))}
    return pd.DataFrame(out).T.rename_axis(["subportfolio", "monitor"])


def degenerate(calibration: pd.DataFrame, q: float = 0.95) -> list:
    """The (subportfolio, monitor) pairs whose `q` quantile already equals the largest value ever seen: a threshold there can never be exceeded, so the monitor cannot fire.
    Measured on the 2026-11-17 PIT run: `top_nature_max` of the RAW top 10 (the nature bonus and the within-nature percentiles make that list one nature in the median month of
    Dinamica; the real diversification happens in the constrained selection, which the PIT scores do not carry)."""
    col = f"q{int(q * 100)}"
    return sorted((s, m) for (s, m), r in calibration.iterrows() if m != "eligible" and np.isfinite(r[col]) and r[col] >= r["max"])


def thresholds(calibration: pd.DataFrame, q: float = 0.95) -> dict:
    """{(subportfolio, monitor): threshold} = the `q` quantile of the calibration, without the monitors that cannot fire (`degenerate`) and without `eligible`, a level and not an
    alarm of its own (its change has a threshold)."""
    col = f"q{int(q * 100)}"
    dead = set(degenerate(calibration, q))
    return {(s, m): float(v) for (s, m), v in calibration[col].items() if m != "eligible" and np.isfinite(v) and (s, m) not in dead}


def evaluate(row: "pd.Series | dict", sub: str, limits: dict) -> list:
    """The monitors of one (date, sub-portfolio) row that exceed their threshold: [(monitor, value, threshold)]. Report-only: nothing blocks."""
    return [(m, float(row[m]), t) for (s, m), t in limits.items() if s == sub and m in row and np.isfinite(row[m]) and row[m] > t]


def historical_alert_rate(health: pd.DataFrame, limits: dict) -> pd.Series:
    """Share of months in which each monitor exceeded its threshold, per (subportfolio, monitor): about 1 - q by construction, a sanity check that the thresholds are not degenerate."""
    out = {}
    for (s, m), t in limits.items():
        v = health.xs(s, level="subportfolio")[m].dropna()
        out[(s, m)] = float((v > t).mean()) if len(v) else np.nan
    return pd.Series(out)


def nan_weight_share(metrics: pd.DataFrame, weights: dict) -> pd.Series:
    """Per fund: the share of the base-score weight sitting on a metric the fund does not have (E-5: such a metric contributes 0, below the last-ranked fund). `metrics`: one row per
    fund with a column per weighted metric; `weights`: {metric: weight} of one sub-portfolio. A metric with no column counts as missing for every fund."""
    w = pd.Series(weights, dtype=float)
    present = pd.DataFrame({m: metrics[m].notna() if m in metrics.columns else pd.Series(False, index=metrics.index) for m in w.index})
    return 1.0 - present.mul(w, axis=1).sum(axis=1) / w.sum()


# ============================================================
# The LIVE block (P3-1.6 step 2): report-only, fail-soft, wired in scripts/launch/p3_build_portfolio.py after the build.
# Pure at its top: the database is reached only through `conn.execute` of the three loaders, and `run_live` takes the loaders as arguments so stubs drive it (R-7).
# ============================================================
_FRAME_COLUMNS = ["subportfolio", "isin", "fund_nature", "eligible", "score_final"]
_EXCLUSION_PREFIXES = ("data_hold", "data_suspect")


def _normalized(frame: "pd.DataFrame | None") -> pd.DataFrame:
    """The monitor columns of a scores frame, with `eligible` as bool and a blank nature when the frame has none (the persisted `fund_scores` rows carry no nature)."""
    out = frame.copy() if frame is not None else pd.DataFrame()
    if out.empty:
        return pd.DataFrame(columns=_FRAME_COLUMNS)
    if "fund_nature" not in out.columns:
        out["fund_nature"] = ""
    out["eligible"] = out["eligible"].astype(bool)
    return out[_FRAME_COLUMNS]


def live_monitors(current: pd.DataFrame, previous: "pd.DataFrame | None" = None, manager: "pd.Series | None" = None, n: int = 10) -> pd.DataFrame:
    """The monitors of the CURRENT run per sub-portfolio (index `subportfolio`), the change monitors computed against `previous` (the latest persisted run before today) when there is
    one, NaN otherwise. Same code path as the calibration (`health_series`), so a live value and a calibrated threshold mean the same thing."""
    cur, prev = _normalized(current), _normalized(previous)
    stacked = pd.concat([prev.assign(as_of=0), cur.assign(as_of=1)], ignore_index=True) if not prev.empty else cur.assign(as_of=1)
    h = health_series(stacked, manager, n)
    return h.xs(1, level="as_of")


def exclusion_counts(current: pd.DataFrame) -> dict:
    """Funds under a data hold / a data-suspicion flag, counted once per ISIN by the prefix of `exclusion_reason` (the live scorer writes `data_hold: ...` / `data_suspect: ...`)."""
    out = {p: 0 for p in _EXCLUSION_PREFIXES}
    if current is None or current.empty or "exclusion_reason" not in current.columns:
        return out
    reason = current["exclusion_reason"].astype(object).where(current["exclusion_reason"].notna(), "").astype(str)
    for p in _EXCLUSION_PREFIXES:
        out[p] = int(current.loc[reason.str.startswith(p), "isin"].nunique())
    return out


def constructed_concentration(funds: list) -> dict:
    """{subportfolio: {funds, nature_max, manager_max}} of the CONSTRUCTED portfolio (`portfolio.all_funds`: dicts with subportfolio, fund_nature, gestora). The raw top list is one nature
    by design (see `degenerate`); the diversification that matters happens in the constrained selection, so it is measured here."""
    out = {}
    for sub in sorted({f["subportfolio"] for f in funds}):
        fs = [f for f in funds if f["subportfolio"] == sub]
        nat = pd.Series([f.get("fund_nature") for f in fs]).value_counts()
        mgr = pd.Series([f.get("gestora") for f in fs]).value_counts()
        out[sub] = {"funds": len(fs), "nature_max": int(nat.max()) if len(nat) else 0, "manager_max": int(mgr.max()) if len(mgr) else 0}
    return out


def nan_weight_by_subportfolio(metrics: pd.DataFrame, current: pd.DataFrame, weights: dict) -> dict:
    """{subportfolio: {mean, funds_with_gap}}: for the ELIGIBLE funds of each sub-portfolio, the mean share of base-score weight sitting on a missing metric (E-5) and the number of funds
    with any. `metrics`: one row per fund, indexed by ISIN; `weights`: {subportfolio: {metric: weight}}."""
    out = {}
    for sub, w in weights.items():
        isins = current.loc[(current["subportfolio"] == sub) & current["eligible"].astype(bool), "isin"]
        if isins.empty or not w:
            continue
        share = nan_weight_share(metrics.reindex(isins.unique()), w)
        out[sub] = {"mean": float(share.mean()), "funds_with_gap": int((share > 1e-12).sum())}
    return out


def live_report(current: pd.DataFrame, previous: "pd.DataFrame | None", limits: dict, manager: "pd.Series | None" = None, funds: "list | None" = None,
                nan_weight: "dict | None" = None, holds: "dict | None" = None, n: int = 10) -> dict:
    """Everything the block prints, as data: monitors, alerts [(subportfolio, monitor, value, limit)], constructed concentration, E-5 share, hold counts, whether a previous run existed."""
    mon = live_monitors(current, previous, manager, n)
    alerts = [(sub, m, v, t) for sub in mon.index for (m, v, t) in evaluate(mon.loc[sub], sub, limits)]
    return {"monitors": mon, "alerts": alerts, "has_previous": previous is not None and not previous.empty, "constructed": constructed_concentration(funds or []),
            "nan_weight": nan_weight, "holds": holds if holds is not None else exclusion_counts(current)}


def format_block(rep: dict, previous_as_of=None) -> str:
    """The compact text block (about ten lines). Report-only: an alert is a line, never a refusal."""
    if not rep["has_previous"]:
        prev_txt = "no previous run: change monitors n/a"
    else:
        prev_txt = f"previous run: {previous_as_of}" if previous_as_of is not None else "previous run available"
    lines = [f"selection_health (report-only, never blocks) | {prev_txt}"]
    for sub, r in rep["monitors"].iterrows():
        chg = f"{r['eligible_change']:.1%}" if np.isfinite(r["eligible_change"]) else "n/a"
        tt = f"{r['top_turnover']:.0%}" if np.isfinite(r["top_turnover"]) else "n/a"
        mg = f"{int(r['top_manager_max'])}" if np.isfinite(r["top_manager_max"]) else "n/a"
        built = rep["constructed"].get(sub)
        built_txt = f"built {built['funds']} funds, same-nature max {built['nature_max']}, same-manager max {built['manager_max']}" if built else "built n/a"
        nw = (rep["nan_weight"] or {}).get(sub)
        nw_txt = f"nan_weight {nw['mean']:.1%} ({nw['funds_with_gap']} funds)" if nw else "nan_weight n/a"
        lines.append(f"  {sub:<12} eligible {int(r['eligible'])} (change {chg}) | top-10 turnover {tt}, same-manager max {mg} | {built_txt} | {nw_txt}")
    lines.append(f"  under data_hold: {rep['holds'].get('data_hold', 0)} | under data_suspect: {rep['holds'].get('data_suspect', 0)}")
    lines += [f"  ALERT {s} {m} {v:.3f} > {t:.3f}" for (s, m, v, t) in rep["alerts"]] or ["  no monitor above its limit"]
    return "\n".join(lines)


def load_previous_run(conn, score_version: str, before) -> pd.DataFrame:
    """The latest PERSISTED scoring run of each block strictly BEFORE `before` (a date), WITHOUT the regime filter (a regime change is part of what the monitors watch). A real build has
    just persisted today's rows, so 'the latest persisted run' would be the run itself. The latest `as_of_date` is chosen in a `runs` CTE joined back: a correlated MAX took > 40 s."""
    rows = conn.execute("""
        WITH runs AS (
            SELECT DISTINCT ON (block) block, as_of_date, regime
            FROM fund_scores
            WHERE score_version = %s AND as_of_date < %s
            ORDER BY block, as_of_date DESC, calculated_at DESC
        )
        SELECT fs.block, fs.isin, fs.score_total, fs.eligible, fs.as_of_date
        FROM fund_scores fs
        JOIN runs r ON r.block = fs.block AND r.as_of_date = fs.as_of_date AND r.regime = fs.regime
        WHERE fs.score_version = %s
    """, (score_version, before, score_version)).fetchall()
    out = pd.DataFrame([(r[0], r[1], "", bool(r[3]), float(r[2] or 0.0)) for r in rows], columns=_FRAME_COLUMNS)
    out.attrs["as_of"] = max((r[4] for r in rows), default=None)
    return out


def load_managers(conn, isins) -> pd.Series:
    """{ISIN: Management_Company} of the given ISINs (the raw top lists), for `top_manager_max`."""
    isins = list(isins)
    if not isins:
        return pd.Series(dtype=object)
    rows = conn.execute("SELECT ISIN, Management_Company FROM fund_master WHERE ISIN = ANY(%s)", (isins,)).fetchall()
    return pd.Series({r[0]: r[1] for r in rows if r[1]}, dtype=object)


def run_live(conn, current: pd.DataFrame, portfolio, *, enabled: bool, limits: dict, today, weights: dict, load_previous, load_managers, load_metrics,
             score_version: str = "v1", n: int = 10) -> "str | None":
    """The wired entry point: None when the kill switch is off (nothing is read), the block text otherwise, ONE `AVISO: selection_health not run (...)` line when anything essential fails.
    Never raises, never mutates `current`, returns text only: it cannot change the DRAFT, the return code or a gate. The manager read and the E-5 read degrade their own monitor only.
    Known weak spot (stated in the limits' provenance): a partial run (E-7) must drop about a third of the eligible set to stand out."""
    if not enabled:
        return None
    try:
        cur = _normalized(current)
        prev = load_previous(conn, score_version, today)
        top = [i for _, g in cur.groupby("subportfolio") for i in _top_set(g, n)]
        try:
            manager = load_managers(conn, top)
        except Exception:                                    # noqa: BLE001 -- only top_manager_max is lost
            manager = None
        try:
            metrics = load_metrics(conn)
            nan_weight = nan_weight_by_subportfolio(metrics, cur, weights) if metrics is not None and weights else None
        except Exception:                                    # noqa: BLE001 -- only the E-5 line is lost
            nan_weight = None
        rep = live_report(current, prev, limits, manager=manager, funds=portfolio.all_funds, nan_weight=nan_weight, holds=exclusion_counts(current), n=n)
        return format_block(rep, prev.attrs.get("as_of") if prev is not None else None)
    except Exception as exc:                                 # noqa: BLE001 -- a monitor never refuses a build
        return f"AVISO: selection_health not run ({type(exc).__name__}: {str(exc)[:160]})".replace("\n", " ")
