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

BLINDNESS (a design rule, pinned by a test). The WP5.1 / WP5.2 code paths that touch the REAL scores call `bootstrap_se` only, which never computes or returns the point estimate;
the point estimate of a real-score run is `estimate`, which no WP5.1 / WP5.2 path calls. The controls use permuted or planted scores. The VERDICT path (`m2_estimate`, `verdict`,
Amendment 1 of the pre-registration, owner order 2026-11-17) is the only one that reads the point estimate, and it is run once, after the amendment was committed.

Every outcome uses month ordinals from shared.period_key (one period key, R-9) and never fills a missing month: a missing NAV month makes the return NaN.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
import pandas as pd

from shared.config import RATIO_MIN_VOL_ANN

TOP_FRAC = 0.20
MIN_FAMILIES = 15
FWD_MONTHS = 12
VOL_WINDOW = 36
MIN_VOL_OBS = 30          # harness detail: at least 30 of the 36 monthly returns must exist for the volatility to count
PLANT_SHARE = 0.10
VOL_FLOOR = RATIO_MIN_VOL_ANN   # one definition (P#11, FND-0431 H-11): was a literal copy of 0.001; a run records the value it used in its manifest


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


def forward_max_drawdown(nav_m: pd.DataFrame, as_ords, months: int = FWD_MONTHS) -> pd.DataFrame:
    """Maximum drawdown (a negative number) of every fund's NAV path over the `months` months after each formation month, the path starting at the formation month itself. NaN when any
    month of the path is missing (no fill). Returns the (len(as_ords) x funds) frame indexed by the ordinal."""
    out = {}
    for t in as_ords:
        w = nav_m.loc[t:t + months]
        if len(w) < months + 1:
            continue
        a = w.to_numpy(dtype=float)
        peak = np.maximum.accumulate(a, axis=0)
        out[t] = np.min(a / peak - 1.0, axis=0)
    return pd.DataFrame.from_dict(out, orient="index", columns=nav_m.columns)


def outcomes_frame(nav_m: pd.DataFrame, as_ords, fwd_months: int = FWD_MONTHS, vol_window: int = VOL_WINDOW, min_obs: int = MIN_VOL_OBS, vol_floor: float = VOL_FLOOR,
                   with_dd: bool = False) -> pd.DataFrame:
    """Long frame (as_ord, isin, fwd, vol, outcome) for the formation months `as_ords`: fwd = NAV[t + fwd_months] / NAV[t] - 1; vol = std of the monthly returns of the `vol_window` months
    ending at t (at least `min_obs`), annualised; outcome = fwd / vol, NaN where vol < `vol_floor`. Rows without a finite outcome are dropped."""
    r = nav_m / nav_m.shift(1) - 1.0
    fwd = nav_m.shift(-fwd_months) / nav_m - 1.0
    vol = r.rolling(vol_window, min_periods=min_obs).std(ddof=1) * math.sqrt(12.0)
    out = fwd / vol.where(vol >= vol_floor)
    sel = np.asarray([o for o in np.asarray(as_ords).tolist() if o in out.index])
    f, v, o = fwd.loc[sel].to_numpy(), vol.loc[sel].to_numpy(), out.loc[sel].to_numpy()
    ti, ni = np.nonzero(np.isfinite(o))
    frame = pd.DataFrame({"as_ord": sel[ti], "isin": nav_m.columns.to_numpy()[ni], "fwd": f[ti, ni], "vol": v[ti, ni], "outcome": o[ti, ni]})
    if with_dd:                                                                       # the forward 12-month path risk of the drawdown condition (plan 5.4)
        dd = forward_max_drawdown(nav_m, sel.tolist(), FWD_MONTHS).reindex(sel)
        frame["mdd"] = dd.to_numpy()[ti, ni]
    return frame


# --------------------------------------------------------------------------------------------------------------------------------------------------
# units, cells, panel
# --------------------------------------------------------------------------------------------------------------------------------------------------
def pick_units(scores: pd.DataFrame, ongoing_charge: pd.Series) -> pd.DataFrame:
    """One class per (as_of, sub-portfolio, fund_family_id) among the eligible rows: the one with the lowest ongoing charge (unknown last), ties by ISIN."""
    s = scores[scores["eligible"]].copy()
    # FND-0431 E-16: a fund with no family id is its own family (the scorer's rule, fund_scorer.deduplicate_by_family). A null here used to collapse
    # every such fund of a date into ONE unit (drop_duplicates treats nulls as equal) and then break the panel's family factorisation.
    fam = s["fund_family_id"]
    s["fund_family_id"] = fam.where(fam.notna() & (fam.astype(str).str.strip() != ""), "isin:" + s["isin"].astype(str))
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
    excess_dd: "np.ndarray | None" = None   # forward max drawdown minus its peer-cell median (NaN where unknown); None when the outcomes carry no `mdd`
    q10_gap: "np.ndarray | None" = None     # the cell's 10th-percentile outcome minus its median (the worst-decile loss given death of the survivorship bound)


def build_panel(units: pd.DataFrame, outcomes: pd.DataFrame, fx_exposed: pd.Series, min_families: int = MIN_FAMILIES, peers: str = "nature") -> Panel:
    """Join the units with their outcomes and assign each valid unit-month its peer-cell median (plan 5.3). `units` has as_of, subportfolio, fund_family_id, fund_nature, isin,
    score_final; `outcomes` is `outcomes_frame`; `fx_exposed` is a bool Series indexed by ISIN. Funds with no outcome stay in the frame (they are ranked by the score, which is ex-ante)
    but have NaN excess and enter no mean and no median. `peers` = "nature" (the signed cells) or "no_nature" (the pre-registered sensitivity: ex-ante volatility tercile x FX, collapsing to FX and
    then to the whole group, because the nature classifier uses realized volatility over the full history)."""
    from shared.period_key import period_ordinals
    df = units.copy()
    df["as_ord"] = np.asarray(period_ordinals(df["as_of"]))
    cols = ["as_ord", "isin", "vol", "outcome"] + (["mdd"] if "mdd" in outcomes.columns else [])
    df = df.merge(outcomes[cols], on=["as_ord", "isin"], how="left")
    fx = fx_exposed.reindex(df["isin"]).to_numpy()
    df["fx"] = np.where(pd.isna(fx), False, fx).astype(bool)
    df["fund_nature"] = df["fund_nature"].astype(str)
    valid = np.isfinite(df["outcome"].to_numpy(dtype=float))
    grp = ["as_ord", "subportfolio"]
    has_dd = "mdd" in df.columns
    v = df.loc[valid, grp + ["fund_nature", "fx", "vol", "outcome"] + (["mdd"] if has_dd else [])].copy()
    if peers == "nature":
        terc_keys, levels = grp + ["fund_nature"], ((1, grp + ["fund_nature", "terc", "fx"]), (2, grp + ["fund_nature", "fx"]), (3, grp + ["fund_nature"]))
    elif peers == "no_nature":
        terc_keys, levels = grp, ((1, grp + ["terc", "fx"]), (2, grp + ["fx"]), (3, grp))
    else:
        raise ValueError(f"peers must be 'nature' or 'no_nature', not {peers!r}")
    pct = v.groupby(terc_keys)["vol"].rank(pct=True, method="first")
    v["terc"] = (np.ceil(pct * 3.0).astype(int) - 1).clip(0, 2)
    med, cnt, q10, med_dd = {}, {}, {}, {}
    for lvl, keys in levels:
        g = v.groupby(keys)["outcome"]
        med[lvl], cnt[lvl], q10[lvl] = g.transform("median").to_numpy(), g.transform("size").to_numpy(), g.transform("quantile", 0.10).to_numpy()
        if has_dd:
            med_dd[lvl] = v.groupby(keys)["mdd"].transform("median").to_numpy()
    use1, use2 = cnt[1] >= min_families, cnt[2] >= min_families
    level = np.where(use1, 1, np.where(use2, 2, 3))

    def pick(d):
        return np.where(use1, d[1], np.where(use2, d[2], d[3]))
    chosen = pick(med)
    excess = np.full(len(df), np.nan)
    excess[np.flatnonzero(valid)] = v["outcome"].to_numpy() - chosen
    excess_dd = q10_gap = None
    if has_dd:
        excess_dd = np.full(len(df), np.nan)
        excess_dd[np.flatnonzero(valid)] = v["mdd"].to_numpy() - pick(med_dd)
    q10_gap = np.full(len(df), np.nan)
    q10_gap[np.flatnonzero(valid)] = pick(q10) - chosen
    group_id = df.groupby(grp, sort=False).ngroup().to_numpy()
    # FND-0431 H-12: the date index counts only dates with at least one valid outcome (as the Panel docstring says), in time order. Before, the final
    # months, which have no 12-month outcome yet, were counted too: n_dates (and so the independent-window count) was overstated and the date
    # bootstrap drew empty dates. A row of an outcome-less date takes id 0: its excess is NaN, so it never enters a sum.
    codes, uniq_all = pd.factorize(df["as_ord"])
    has_outcome = np.zeros(len(uniq_all), dtype=bool)
    has_outcome[codes[np.isfinite(excess)]] = True
    date_id = np.where(has_outcome[codes], (np.cumsum(has_outcome) - 1)[codes], 0)
    fam_id, fam_u = pd.factorize(df["fund_family_id"])
    levels_share = {int(k): float((level == k).mean()) for k in (1, 2, 3)}
    return Panel(group_id=group_id, date_id=date_id, family_id=fam_id, score=df["score_final"].to_numpy(dtype=float), excess=excess, valid=np.isfinite(excess),
                 n_dates=int(has_outcome.sum()), n_families=int(len(fam_u)), cell_levels=levels_share, excess_dd=excess_dd, q10_gap=q10_gap)


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


# --------------------------------------------------------------------------------------------------------------------------------------------------
# the M2 verdict (Amendment 1, owner order 2026-11-17): the only path that reads the point estimate of the real scores
# --------------------------------------------------------------------------------------------------------------------------------------------------
DD_MARGIN = 0.02                          # parameter 5 of the pre-registration: the top quintile's max drawdown may be at most 2 pp worse than the peer median's
ATTRITION_RATES = (0.025, 0.05, 0.10)     # the locked base 5 % a year and its bounds


def m2_estimate(panel: Panel, boot: Boot, alpha: float = 0.05) -> dict:
    """M2 for one outcome horizon: the pooled mean excess of the champion's top quintile over its peer-cell median, its date-block and family-cluster standard errors, the one-sided lower
    bound at `alpha`, and (when the panel carries drawdowns) the mean forward max-drawdown excess of the top quintile and the mean worst-decile gap of its cells."""
    mask = top_mask(panel.group_id, panel.score)
    sums = sums_for(panel, mask)
    est, se = estimate(sums), bootstrap_se(sums, boot)
    out = {"estimate": est, **se, "lower_bound": float(est - z_of(1 - alpha) * se["se"]), "n_selected_valid": int(sums.n_date.sum()), "n_dates": int(panel.n_dates),
           "n_families": int(panel.n_families), "cell_levels": panel.cell_levels}
    if panel.excess_dd is not None:
        w = mask & np.isfinite(panel.excess_dd)
        out["dd_excess_mean"], out["n_dd"] = float(panel.excess_dd[w].mean()), int(w.sum())
    if panel.q10_gap is not None:
        out["q10_gap_mean"] = float(np.nanmean(panel.q10_gap[mask & panel.valid]))
    return out


def _required(m2: dict, key: str, what: str) -> float:
    """FND-0431 E-14: a quantity the outcome rule depends on must be present. A missing one used to default to 0.0, which PASSES the drawdown
    condition and leaves the survivorship gap at zero without a word; now it is an error that names the cause."""
    if key not in m2:
        raise ValueError(f"M2 result has no {key!r}: {what}")
    return m2[key]


MAX_ATTRITION_SHARE = 0.5                 # a * k above one half would make the dead funds the majority of the selection: the adjustment is then meaningless, so it is refused


def attrition_adjusted(m2: dict, rates=ATTRITION_RATES, alpha: float = 0.05, k: float = 1.0) -> list:
    """Survivorship bound (plan 5.5, pre-registered rule of the draft: the loss given death is the worst-decile outcome of the fund's own peer cell). A fraction `a` of the funds that
    would have been in the sample died unobserved and is selected at the base rate (the scores cannot know it), so the top-quintile mean becomes (1 - a) * M2 + a * (worst-decile gap).
    The expectation replaces the 1,000 simulated paths: the pooled mean is deterministic in `a`. Returns one dict per rate with the adjusted estimate, its lower bound and whether the
    adjustment flips the sign of the unadjusted estimate.
    FND-0436 (Package L): `k` is the selection propensity of the dead funds (k = 1: selected at the base rate, today's rule; k > 1: over-represented among the selected, e.g. the funds that
    ran hottest before a crisis). The share that enters the formula is `a * k` (reported as "share"), refused above MAX_ATTRITION_SHARE. k = 1 gives exactly the unadjusted rows."""
    if not k > 0:
        raise ValueError(f"k must be positive, not {k!r}")
    est, se = m2["estimate"], m2["se"]
    gap = _required(m2, "q10_gap_mean", "the panel has no worst-decile gap; build the outcomes with the peer cells (build_panel computes it)")
    z = z_of(1 - alpha)
    rows = []
    for a in rates:
        share = a * k
        if not 0 <= share <= MAX_ATTRITION_SHARE + 1e-12:
            raise ValueError(f"attrition share a * k = {share:g} is outside [0, {MAX_ATTRITION_SHARE}] (a = {a}, k = {k})")
        adj = (1 - share) * est + share * gap
        rows.append({"rate": a, "k": float(k), "share": float(share), "estimate_adjusted": float(adj), "lower_bound_adjusted": float(adj - z * (1 - share) * se),
                     "flips_sign": bool((adj > 0) != (est > 0))})
    return rows


def survivorship_breakdown(m2: dict, alpha: float = 0.05) -> dict:
    """FND-0436: the BREAKDOWN POINT of the survivorship adjustment, the share a * k at which the adjusted estimate (`estimate_flip`) and the adjusted one-sided lower bound
    (`lower_bound_flip`) reach zero. A verdict is only as strong as the distance between this share and the plausible attrition: a rate nobody can measure (the PIT panel has no dead
    funds) is reported as the point where the conclusion changes, not assumed. Closed form of `attrition_adjusted`: (1 - x) * v + x * gap = 0 with v the unadjusted estimate or lower
    bound, so x = v / (v - gap). 0.0 when v is already not positive; inf when the dead funds would have done at least as well (gap >= v): nothing flips."""
    est, se = m2["estimate"], m2["se"]
    gap = _required(m2, "q10_gap_mean", "the panel has no worst-decile gap; build the outcomes with the peer cells (build_panel computes it)")
    lower = est - z_of(1 - alpha) * se

    def flip(v: float) -> float:
        if v <= 0:
            return 0.0
        return float("inf") if gap >= v else float(v / (v - gap))
    return {"estimate_flip": flip(est), "lower_bound_flip": flip(lower)}


def realised_quantile_path(paths: pd.DataFrame, q: float = 0.10) -> pd.Series:
    """FND-0436: the stressed path of the survivorship tail stress is the REALISED monthly return path of one fund, the one whose cumulative return over the window is the q-quantile
    (lower: the fund at sorted position floor(q * (n - 1))), never a pointwise percentile of the paths (that series belongs to no fund). `paths`: months x funds of one peer cell over one
    window. A fund with a missing month is dropped (a hole is never filled); no fund left is an error."""
    full = paths.dropna(axis=1, how="any")
    if full.shape[1] == 0:
        raise ValueError("no fund of the cell has a complete path over the window")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q must be in [0, 1], not {q!r}")
    cum = (1.0 + full).prod() - 1.0
    order = cum.sort_values(kind="mergesort").index
    return full[order[int(math.floor(q * (len(order) - 1)))]]


def tail_stress_max_dd(port_ret: pd.Series, stress_ret: pd.Series, share: float) -> float:
    """FND-0436: maximum drawdown (a negative number, the starting capital counts as the first peak) of the WHOLE portfolio trajectory when the fraction `share` = a * k of its positions is
    replaced by the stressed fund (`realised_quantile_path`): monthly return (1 - share) * port + share * stress, one window at a time (the stressed fraction is re-drawn at each
    window, so the stress does not compound across windows). Same standard for the baseline and every variant. Refuses a share outside [0, MAX_ATTRITION_SHARE], two series on
    different indexes and non-finite values (never filled)."""
    if not 0.0 <= share <= MAX_ATTRITION_SHARE + 1e-12:
        raise ValueError(f"share {share!r} is outside [0, {MAX_ATTRITION_SHARE}]")
    if not port_ret.index.equals(stress_ret.index):
        raise ValueError("portfolio and stress series must share the same index")
    if not (np.isfinite(port_ret.to_numpy(dtype=float)).all() and np.isfinite(stress_ret.to_numpy(dtype=float)).all()):
        raise ValueError("portfolio and stress returns must be finite (a missing month is never filled)")
    mixed = (1.0 - share) * port_ret.to_numpy(dtype=float) + share * stress_ret.to_numpy(dtype=float)
    cum = np.concatenate(([1.0], np.cumprod(1.0 + mixed)))
    return float((cum / np.maximum.accumulate(cum) - 1.0).min())


def _klass(m2: dict, dd_margin: float) -> str:
    """The M2-level reading of one run: PIVOT-side when the estimate is not positive, CONTINUE-side when the lower bound is above zero and the drawdown condition holds, else INCONCLUSIVE."""
    if m2["estimate"] <= 0:
        return "PIVOT"
    dd_ok = _required(m2, "dd_excess_mean", "the drawdown condition needs the forward drawdown; build the outcomes with with_dd=True") >= -dd_margin
    return "CONTINUE" if (m2["lower_bound"] > 0 and dd_ok) else "INCONCLUSIVE"


def verdict(main: dict, h36: dict, nonat: dict, portfolio_dd_worse_by: float, m1_diff: "float | None" = None, dd_margin: float = DD_MARGIN) -> dict:
    """The WP5 outcome of the signed pre-registration and Amendment 1. `main` = M2 at 12 months, `h36` = M2 at 36 months, `nonat` = the 12-month run with peers that avoid Fund_Nature,
    `portfolio_dd_worse_by` = champion max drawdown worse than B-POOL's, in fractions (positive = worse).
      CONTINUE: lower bound of the 12-month M2 above zero and the drawdown condition met. PIVOT: the 12-month estimate is not positive (the survivorship adjustment only lowers it, so it
      cannot flip it) or the champion's portfolio drawdown is worse than B-POOL's by more than the margin. INCONCLUSIVE: anything else, and also when the 36-month estimate has the other
      sign or the nature-free sensitivity changes the reading. `m1_diff` (net Sharpe difference, descriptive only) adds the CONSTRUCTION-LOSS note to a CONTINUE when it is not positive."""
    base = _klass(main, dd_margin)
    reasons = []
    outcome = base
    if portfolio_dd_worse_by > dd_margin:
        outcome = "PIVOT"
        reasons.append(f"champion max drawdown is worse than B-POOL's by {portfolio_dd_worse_by:.3%}, above the {dd_margin:.0%} margin")
    consistent36 = bool((h36["estimate"] > 0) == (main["estimate"] > 0))
    nonat_klass = _klass(nonat, dd_margin)
    if outcome != "INCONCLUSIVE" and not consistent36:
        outcome = "INCONCLUSIVE"
        reasons.append("the 36-month estimate has the other sign than the 12-month one")
    if outcome != "INCONCLUSIVE" and nonat_klass != base and not (outcome == "PIVOT" and portfolio_dd_worse_by > dd_margin):
        outcome = "INCONCLUSIVE"
        reasons.append(f"the nature-free sensitivity reads {nonat_klass}, the main run {base}")
    note = None
    if outcome == "CONTINUE" and m1_diff is not None and m1_diff <= 0:
        note = "CONSTRUCTION-LOSS (descriptive only, Amendment 1): scoring has selection value while the portfolio net Sharpe does not beat the pool"
    # FND-0431 E-15: the outcome rule turns on the SIGN of the point estimate, so the verdict also carries the numbers that say how firm that sign is.
    return {"outcome": outcome, "main_klass": base, "nonature_klass": nonat_klass, "consistent_36m_sign": consistent36,
            "dd_condition_met": bool(_required(main, "dd_excess_mean", "the drawdown condition needs the forward drawdown") >= -dd_margin),
            "main_estimate": main["estimate"], "main_se": main["se"], "main_lower_bound": main["lower_bound"],
            "reasons": reasons, "note": note, "attrition": attrition_adjusted(main)}
