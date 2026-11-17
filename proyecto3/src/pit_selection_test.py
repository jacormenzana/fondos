# proyecto3/src/pit_selection_test.py
# -*- coding: utf-8 -*-
"""FND-0429 / WP5 -- the ranking-layer test (selection level, M2) and its two pre-verdict checks, WP5.1 controls and WP5.2 feasibility.

Pre-registration: doc/decisiones/PREREG_FND-0429_WP5.md (signed off 2026-11-17). PURE (pandas / numpy, no database, no pipeline import: R-7); the command line is
scripts/audit/p3/p3_pit_selection_test.py.

What is measured. For every (as_of, sub-portfolio) the champion's top quintile by `score_final` is compared, fund by fund, with the median of its peer cell on the outcome
`forward 12-month return / trailing 36-month annualised volatility at formation` (the risk is known at formation). One class per `fund_family_id` (the cheapest by ongoing
charge). Peers: Fund_Nature x ex-ante volatility tercile x FX-exposure class, collapsing to Fund_Nature x FX and then to Fund_Nature when a cell has fewer than 15 families.
The statistic is the pooled mean of those excesses over the top-quintile fund-months. Its standard error is the larger of a stationary block bootstrap over dates and a
cluster bootstrap over families.

BLINDNESS (a design rule, pinned by a test). The verdict is not produced here. The WP5.1 / WP5.2 code paths that touch the REAL scores call `bootstrap_se` only, which never
computes or returns the point estimate; the point estimate of a real-score run is `estimate`, which no WP5.1 / WP5.2 path calls. The controls use permuted or planted scores.

Every outcome uses month ordinals from shared.period_key (one period key, R-9) and never fills a missing month: a missing NAV month makes the return NaN.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
import pandas as pd

TOP_FRAC = 0.20
MIN_FAMILIES = 15
FWD_MONTHS = 12
VOL_WINDOW = 36
MIN_VOL_OBS = 30          # harness detail: at least 30 of the 36 monthly returns must exist for the volatility to count
PLANT_SHARE = 0.10
VOL_FLOOR = 0.001         # shared.config.RATIO_MIN_VOL_ANN: no ratio on a near-zero denominator


def z_of(p: float) -> float:
    """Standard normal quantile."""
    return NormalDist().inv_cdf(p)


# --------------------------------------------------------------------------------------------------------------------------------------------------
# outcomes
# --------------------------------------------------------------------------------------------------------------------------------------------------
def monthly_nav(nav: pd.DataFrame) -> pd.DataFrame:
    """Wide NAV (dates x isin, any stamps) -> one row per calendar month ordinal, the last observation of the month, over the FULL ordinal range (a month with no NAV is a NaN row,
    never filled). Index = month ordinal (year * 12 + month - 1, shared.period_key)."""
    from shared.period_key import period_ordinals
    ords = np.asarray(period_ordinals(pd.Series(pd.DatetimeIndex(nav.index))))
    m = nav.groupby(ords).last()
    return m.reindex(np.arange(int(m.index.min()), int(m.index.max()) + 1))


def outcomes_frame(nav_m: pd.DataFrame, as_ords, fwd_months: int = FWD_MONTHS, vol_window: int = VOL_WINDOW, min_obs: int = MIN_VOL_OBS, vol_floor: float = VOL_FLOOR) -> pd.DataFrame:
    """Long frame (as_ord, isin, fwd, vol, outcome) for the formation months `as_ords`: fwd = NAV[t + fwd_months] / NAV[t] - 1; vol = std of the monthly returns of the `vol_window` months
    ending at t (at least `min_obs`), annualised; outcome = fwd / vol, NaN where vol < `vol_floor`. Rows without a finite outcome are dropped."""
    r = nav_m / nav_m.shift(1) - 1.0
    fwd = nav_m.shift(-fwd_months) / nav_m - 1.0
    vol = r.rolling(vol_window, min_periods=min_obs).std(ddof=1) * math.sqrt(12.0)
    out = fwd / vol.where(vol >= vol_floor)
    sel = np.asarray([o for o in np.asarray(as_ords).tolist() if o in out.index])
    f, v, o = fwd.loc[sel].to_numpy(), vol.loc[sel].to_numpy(), out.loc[sel].to_numpy()
    ti, ni = np.nonzero(np.isfinite(o))
    return pd.DataFrame({"as_ord": sel[ti], "isin": nav_m.columns.to_numpy()[ni], "fwd": f[ti, ni], "vol": v[ti, ni], "outcome": o[ti, ni]})


# --------------------------------------------------------------------------------------------------------------------------------------------------
# units, cells, panel
# --------------------------------------------------------------------------------------------------------------------------------------------------
def pick_units(scores: pd.DataFrame, ongoing_charge: pd.Series) -> pd.DataFrame:
    """One class per (as_of, sub-portfolio, fund_family_id) among the eligible rows: the one with the lowest ongoing charge (unknown last), ties by ISIN."""
    s = scores[scores["eligible"]].copy()
    s["_chg"] = pd.to_numeric(s["isin"].map(ongoing_charge), errors="coerce").to_numpy(dtype=float)
    s["_chg"] = np.where(np.isfinite(s["_chg"]), s["_chg"], np.inf)
    s = s.sort_values(["as_of", "subportfolio", "fund_family_id", "_chg", "isin"], kind="mergesort")
    return s.drop_duplicates(["as_of", "subportfolio", "fund_family_id"], keep="first").drop(columns="_chg").reset_index(drop=True)


@dataclass(frozen=True)
class Panel:
    group_id: np.ndarray      # (as_of, sub-portfolio) group of each unit-month
    date_id: np.ndarray       # as_of index 0..T-1 (dates with at least one valid outcome)
    family_id: np.ndarray     # family index 0..F-1
    score: np.ndarray         # champion score_final
    excess: np.ndarray        # outcome minus its peer-cell median; NaN where the outcome does not exist
    valid: np.ndarray         # excess is finite
    n_dates: int
    n_families: int
    cell_levels: dict         # share of valid unit-months by the peer-cell level used (1 = nature x tercile x FX, 2 = nature x FX, 3 = nature)


def build_panel(units: pd.DataFrame, outcomes: pd.DataFrame, fx_exposed: pd.Series, min_families: int = MIN_FAMILIES) -> Panel:
    """Join the units with their outcomes and assign each valid unit-month its peer-cell median (plan 5.3). `units` has as_of, subportfolio, fund_family_id, fund_nature, isin,
    score_final; `outcomes` is `outcomes_frame`; `fx_exposed` is a bool Series indexed by ISIN. Funds with no outcome stay in the frame (they are ranked by the score, which is ex-ante)
    but have NaN excess and enter no mean and no median."""
    from shared.period_key import period_ordinals
    df = units.copy()
    df["as_ord"] = np.asarray(period_ordinals(df["as_of"]))
    df = df.merge(outcomes[["as_ord", "isin", "vol", "outcome"]], on=["as_ord", "isin"], how="left")
    fx = fx_exposed.reindex(df["isin"]).to_numpy()
    df["fx"] = np.where(pd.isna(fx), False, fx).astype(bool)
    df["fund_nature"] = df["fund_nature"].astype(str)
    valid = np.isfinite(df["outcome"].to_numpy(dtype=float))
    grp = ["as_ord", "subportfolio"]
    v = df.loc[valid, grp + ["fund_nature", "fx", "vol", "outcome"]].copy()
    pct = v.groupby(grp + ["fund_nature"])["vol"].rank(pct=True, method="first")
    v["terc"] = (np.ceil(pct * 3.0).astype(int) - 1).clip(0, 2)
    med, cnt = {}, {}
    for lvl, keys in ((1, grp + ["fund_nature", "terc", "fx"]), (2, grp + ["fund_nature", "fx"]), (3, grp + ["fund_nature"])):
        g = v.groupby(keys)["outcome"]
        med[lvl], cnt[lvl] = g.transform("median").to_numpy(), g.transform("size").to_numpy()
    use1, use2 = cnt[1] >= min_families, cnt[2] >= min_families
    level = np.where(use1, 1, np.where(use2, 2, 3))
    chosen = np.where(use1, med[1], np.where(use2, med[2], med[3]))
    excess = np.full(len(df), np.nan)
    excess[np.flatnonzero(valid)] = v["outcome"].to_numpy() - chosen
    group_id = df.groupby(grp, sort=False).ngroup().to_numpy()
    date_id, uniq = pd.factorize(df["as_ord"])        # every unit-month has a date id; rows without an outcome never enter a sum (valid is False)
    fam_id, fam_u = pd.factorize(df["fund_family_id"])
    levels = {int(k): float((level == k).mean()) for k in (1, 2, 3)}
    return Panel(group_id=group_id, date_id=date_id, family_id=fam_id, score=df["score_final"].to_numpy(dtype=float), excess=excess, valid=np.isfinite(excess),
                 n_dates=int(len(uniq)), n_families=int(len(fam_u)), cell_levels=levels)


# --------------------------------------------------------------------------------------------------------------------------------------------------
# selection, statistic, bootstrap
# --------------------------------------------------------------------------------------------------------------------------------------------------
def top_mask(group_id: np.ndarray, key: np.ndarray, frac: float = TOP_FRAC) -> np.ndarray:
    """True for the best `ceil(frac * n_group)` rows of every group by `key` (higher is better; ties keep the row order)."""
    n = len(key)
    order = np.lexsort((-key, group_id))
    g = group_id[order]
    starts = np.r_[0, np.flatnonzero(np.diff(g)) + 1]
    counts = np.diff(np.r_[starts, n])
    pos = np.arange(n) - np.repeat(starts, counts)
    keep = pos < np.repeat(np.ceil(frac * counts).astype(int), counts)
    mask = np.zeros(n, dtype=bool)
    mask[order[keep]] = True
    return mask


@dataclass(frozen=True)
class Sums:
    s_date: np.ndarray
    n_date: np.ndarray
    s_fam: np.ndarray
    n_fam: np.ndarray


def sums_for(panel: Panel, mask: np.ndarray, excess: "np.ndarray | None" = None) -> Sums:
    """Per-date and per-family sums of the excess and counts over the selected valid unit-months (the sufficient statistics of the pooled mean)."""
    ex = panel.excess if excess is None else excess
    w = mask & np.isfinite(ex)
    e = np.where(w, ex, 0.0)
    wf = w.astype(float)
    return Sums(np.bincount(panel.date_id, weights=e, minlength=panel.n_dates), np.bincount(panel.date_id, weights=wf, minlength=panel.n_dates),
                np.bincount(panel.family_id, weights=e, minlength=panel.n_families), np.bincount(panel.family_id, weights=wf, minlength=panel.n_families))


@dataclass(frozen=True)
class Boot:
    date_idx: np.ndarray      # (R, T) stationary-bootstrap positions over dates
    fam_counts: np.ndarray    # (R, F) multinomial family multiplicities


def make_boot(n_dates: int, n_families: int, n_boot: int = 2000, mean_block: float = 6.0, seed: int = 12345) -> Boot:
    from proyecto3.src.backtest_inference import stationary_bootstrap_indices
    idx = stationary_bootstrap_indices(n_dates, n_boot, mean_block, seed)
    rng = np.random.default_rng(seed + 1)
    counts = rng.multinomial(n_families, np.full(n_families, 1.0 / n_families), size=n_boot).astype(float)
    return Boot(idx, counts)


def _ratio(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        return num / den


def bootstrap_se(sums: Sums, boot: Boot) -> dict:
    """Standard error of the pooled mean: {se_date, se_family, se = the larger}. NEVER computes or returns the point estimate (blindness, see the module docstring)."""
    d = _ratio(sums.s_date[boot.date_idx].sum(axis=1), sums.n_date[boot.date_idx].sum(axis=1))
    f = _ratio(boot.fam_counts @ sums.s_fam, boot.fam_counts @ sums.n_fam)
    se_d, se_f = float(np.nanstd(d, ddof=1)), float(np.nanstd(f, ddof=1))
    return {"se_date": se_d, "se_family": se_f, "se": max(se_d, se_f)}


def estimate(sums: Sums) -> float:
    """The pooled mean excess over the selected valid unit-months. Used by the controls (permuted or planted scores) and, in the verdict step only, by the real-score run."""
    n = sums.n_date.sum()
    return float(sums.s_date.sum() / n) if n > 0 else float("nan")


# --------------------------------------------------------------------------------------------------------------------------------------------------
# WP5.1 controls
# --------------------------------------------------------------------------------------------------------------------------------------------------
def wilson_upper(k: int, n: int, z: float = 1.959963984540054) -> float:
    """Upper limit of the Wilson 95 % interval of a proportion k / n."""
    if n == 0:
        return float("nan")
    p = k / n
    d = 1 + z * z / n
    return float((p + z * z / (2 * n) + z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d)


def _pass(sums: Sums, boot: Boot, z: float) -> tuple:
    est, se = estimate(sums), bootstrap_se(sums, boot)["se"]
    return bool(est - z * se > 0), est, se


def permutation_control(panel: Panel, boot: Boot, n_perm: int, alpha: float = 0.05, seed: int = 12345) -> dict:
    """Permuted-label runs: the scores are replaced by random keys inside every (as_of, sub-portfolio) group, so the selection is random and the true effect is zero. The PASS rate
    (lower one-sided bound of the pooled mean above zero at `alpha`) must be at most `alpha`. Also reports the standard deviation of the estimate across permutations."""
    rng = np.random.default_rng(seed)
    z = z_of(1 - alpha)
    passes, ests, ses = 0, [], []
    for _ in range(n_perm):
        ok, est, se = _pass(sums_for(panel, top_mask(panel.group_id, rng.random(len(panel.score)))), boot, z)
        passes += int(ok)
        ests.append(est)
        ses.append(se)
    rate = passes / n_perm
    return {"n_perm": n_perm, "alpha": alpha, "pass_count": passes, "pass_rate": rate, "pass_rate_wilson95_upper": wilson_upper(passes, n_perm),
            "meets_rule_rate_le_alpha": bool(rate <= alpha), "null_estimate_sd": float(np.std(ests, ddof=1)), "mean_bootstrap_se": float(np.mean(ses))}


def planted_control(panel: Panel, boot: Boot, delta: float, n_rep: int, alpha: float = 0.05, target: float = 0.80, share: float = PLANT_SHARE, seed: int = 12345) -> dict:
    """Planted alpha: a random `share` of the families gets `delta` added to its outcome in every month and is ranked on top of the random keys of the others. The detection rate is
    the share of replications that PASS; it must reach `target`. The expected selection spread is delta * (planted share of the top quintile), reported as `expected_spread`."""
    rng = np.random.default_rng(seed)
    z = z_of(1 - alpha)
    F, n = panel.n_families, len(panel.score)
    k = max(1, int(round(share * F)))
    hits, ests = 0, []
    for _ in range(n_rep):
        planted = np.zeros(F, dtype=bool)
        planted[rng.choice(F, size=k, replace=False)] = True
        row = planted[panel.family_id]
        ex = panel.excess + np.where(row, delta, 0.0)
        ok, est, _se = _pass(sums_for(panel, top_mask(panel.group_id, rng.random(n) + 2.0 * row), excess=ex), boot, z)
        hits += int(ok)
        ests.append(est)
    rate = hits / n_rep
    return {"n_rep": n_rep, "alpha": alpha, "delta_on_outcome": float(delta), "plant_share": share, "detect_count": hits, "detection_rate": rate,
            "detection_target": target, "meets_target": bool(rate >= target), "mean_recovered_spread": float(np.mean(ests))}


# --------------------------------------------------------------------------------------------------------------------------------------------------
# WP5.2 feasibility
# --------------------------------------------------------------------------------------------------------------------------------------------------
def feasibility(panel: Panel, boot: Boot, alpha: float = 0.05, power: float = 0.80, last_nav_ord: "int | None" = None, first_ord: "int | None" = None) -> dict:
    """Independent periods and the minimum detectable selection spread of the REAL-score top quintile. Only `bootstrap_se` is called: no point estimate of the real scores is
    computed or returned. mde_spread = (z_alpha + z_power) * SE, in outcome units (a 12-month return per unit of volatility, i.e. Sharpe-like)."""
    se = bootstrap_se(sums_for(panel, top_mask(panel.group_id, panel.score)), boot)
    za, zp = z_of(1 - alpha), z_of(power)
    out = {"alpha": alpha, "power": power, "n_dates_12m": int(panel.n_dates), "independent_12m_windows": panel.n_dates / 12.0, "se_date": se["se_date"], "se_family": se["se_family"],
           "se_used": se["se"], "mde_spread": float((za + zp) * se["se"]), "cell_levels": panel.cell_levels}
    if last_nav_ord is not None and first_ord is not None:
        n36 = max(0, last_nav_ord - 36 - first_ord + 1)
        out["n_dates_36m"], out["independent_36m_windows"] = int(n36), n36 / 36.0
    return out


def sharpe_difference_feasibility(champion_net: pd.Series, pool: pd.Series, cash: pd.Series, alpha: float = 0.05, power: float = 0.80, n_boot: int = 2000, mean_block: float = 6.0,
                                  seed: int = 12345) -> dict:
    """Minimum detectable Sharpe difference (M1) from the paired block bootstrap of `champion_net` against `pool` (the plan's interval, +-0.058 at 90 %). Only the half-width of the
    90 % interval is used; the point difference is not returned. NOTE: `pool` in the PIT table is the frictionless pool, a proxy of the net pool for the SE."""
    from proyecto3.src.backtest_inference import paired_sharpe_difference
    r = paired_sharpe_difference(champion_net, pool, cash=cash, n_boot=n_boot, mean_block=mean_block, ci=0.90, seed=seed)
    half = float((r["ci_high"] - r["ci_low"]) / 2.0)
    se = half / z_of(0.95)
    return {"n_months": int(r["n"]), "ci90_half_width": half, "se": se, "mde_delta_sharpe": float((z_of(1 - alpha) + z_of(power)) * se)}
