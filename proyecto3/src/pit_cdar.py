# proyecto3/src/pit_cdar.py
# -*- coding: utf-8 -*-
"""Pure pieces of the feasibility study F of the drawdown-management RFC (Package L, PREREG_P3_10_FEAS, FND-0430): can funds that the individual drawdown filters exclude, but that
hold up in pool-stress months, lift the 4.14 % ceiling that the three sub-portfolio pools reach under the 15 % drawdown cap?

READ-ONLY analysis, pandas / numpy only (R-7), driven by scripts/audit/p3/p3_cdar_feasibility.py on the SEALED run. Nothing here is a production rule.
Every signal at date t uses data <= t. The numbers below are the preregistration's PROPOSED values: they are fixed by the owner's approval of the preregistration, never tuned afterwards (R8).

Conventions: a `returns` frame is dates (month-ends) x ISIN, the value at date s being the return OVER the month ending at s. A decision taken at t earns the return of the NEXT month.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ---- preregistered values (proposals until the owner approves PREREG_P3_10_FEAS.md) ----------------------------------------------------------------------------------------------
STRESS_THRESHOLD = 0.03        # the pool level more than 3 % below its trailing high (same arithmetic as ranking_levers.pool_state)
STRESS_LOOKBACK = 12           # months
WINDOW = 60                    # months of history the stress statistics need
MIN_STRESS = 6                 # stress months inside the window for a stress beta to exist
BETA_MAX = 0.3
MIN_CANDIDATES = 3             # a sub-portfolio with fewer convex candidates holds the fourth column in CASH
MIN_EPISODES = 4               # distinct stress episodes needed in the history
MAX_DROPPED = 0.30             # F1b is not evaluable above this share of unpaired candidate cells
SIGN_EPISODES_BELOW = 8        # below this many episodes F1b also needs the sign count
SIGN_SHARE = 0.75
BOOT_DRAWS = 2000
BOOT_SEED = 20261123


# ------------------------------------------------------------------------------------------------------------------ drawdown tail
def cdar(r: pd.Series, alpha: float = 0.90) -> float:
    """Conditional drawdown at risk: the mean of the worst (1 - alpha) share of the drawdown observations d_t = V_t / max_{s<=t} V_s - 1 of a monthly return path (V starts at the first
    return, as `pit_crisis_diagnostic.ann_and_drawdown` does). The worst share holds at least one observation. <= 0."""
    x = pd.Series(r).dropna().to_numpy(float)
    if len(x) == 0:
        return float("nan")
    cum = np.cumprod(1.0 + x)
    d = np.sort(cum / np.maximum.accumulate(cum) - 1.0)
    k = max(int(round((1.0 - alpha) * len(d))), 1)
    return float(d[:k].mean())


# ------------------------------------------------------------------------------------------------------------------ stress months
def stress_months(r_pool: pd.Series, thr: float = STRESS_THRESHOLD, lookback: int = STRESS_LOOKBACK) -> pd.Series:
    """True at month t when the pool level, built from the `lookback` returns ending at t, is more than `thr` below the high of those levels (exactly `ranking_levers.pool_state` `stress`
    or `recovery`). Fewer than `lookback` finite returns: False (never a guess). A function of the returns up to t only."""
    r = pd.Series(r_pool, dtype=float)
    v = r.to_numpy()
    out = np.zeros(len(v), dtype=bool)
    for i in range(lookback - 1, len(v)):
        w = v[i - lookback + 1:i + 1]
        level = np.concatenate(([1.0], np.cumprod(1.0 + w)))            # a NaN return makes the level NaN and the comparison False: no stress is ever guessed
        out[i] = level[-1] < (1.0 - thr) * level.max()
    return pd.Series(out, index=r.index)


def stress_episodes(stress: pd.Series, gap: int = 0) -> pd.Series:
    """Episode id of every month: runs of consecutive stress months are numbered 1, 2, ...; a month out of stress is 0. With `gap` > 0 two runs separated by at most `gap` months out of stress
    are ONE episode (a one-month rebound inside a drawdown is not a new independent event); the months in between keep id 0. Default 0 = consecutive months only."""
    s = pd.Series(stress).astype(bool)
    start = s & ~s.shift(1, fill_value=False)
    run = start.cumsum().where(s, 0).astype(int)
    if gap <= 0 or run.max() < 2:
        return run
    pos = np.flatnonzero(s.to_numpy())
    cur, ids = 0, {}
    prev_pos, prev_run = None, None
    for p in pos:
        r = int(run.iloc[p])
        if prev_run is None or (r != prev_run and p - prev_pos - 1 > gap):
            cur += 1
        ids[r] = cur
        prev_pos, prev_run = p, r
    return run.map(lambda r: ids.get(int(r), 0) if r else 0).astype(int)


def design_power(stress: pd.Series, window: int = WINDOW, min_stress: int = MIN_STRESS, gap: int = 0) -> dict:
    """The DESIGN-POWER count (read before the preregistration is committed; it is not an outcome): stress months, distinct episodes with their lengths, and the share of the dates (with a full
    window) whose trailing `window` holds at least `min_stress` stress months, i.e. the dates at which a stress beta can exist at all. `enough_episodes` is the stop rule of the study."""
    s = pd.Series(stress).astype(bool)
    ep = stress_episodes(s, gap)
    lengths = ep[ep > 0].value_counts().sort_index().tolist()
    roll = s.astype(int).rolling(window).sum()
    dated = roll.dropna()
    return {"n_months": int(len(s)), "n_stress_months": int(s.sum()), "n_episodes": int(len(lengths)), "episode_lengths": lengths,
            "enough_episodes": bool(len(lengths) >= MIN_EPISODES),
            "share_dates_with_beta": float((dated >= min_stress).mean()) if len(dated) else float("nan")}


def thin_window(cands_by_t: dict, min_candidates: int = MIN_CANDIDATES) -> dict:
    """The thin-window rule: a date with fewer than `min_candidates` convex candidates (the stress months of its window are checked by `convex_candidates` itself) holds the fourth column in CASH
    (an empty membership). The rest is unchanged."""
    return {t: (list(v) if len(v) >= min_candidates else []) for t, v in cands_by_t.items()}


def convex_candidates(returns: pd.DataFrame, r_pool: pd.Series, stress: pd.Series, t, *, window: int = WINDOW, beta_max: float = BETA_MAX,
                      min_stress: int = MIN_STRESS, pool=None) -> list:
    """Funds of `pool` (default: every column) that are CONVEX at month-end `t`, on the `window` months ending at t and ONLY the data up to t:
    a full window of returns; at least `min_stress` stress months in it; stress beta <= beta_max; mean return in the stress months above the pool's; window total return > 0.
    The last two conditions keep a fund in steady decline (beta near 0, losing every month) out of the basket."""
    t = pd.Timestamp(t)
    pos = returns.index.get_indexer([t])[0]
    if pos < 0 or pos < window - 1:
        return []
    cols = list(returns.columns) if pool is None else [i for i in pool if i in returns.columns]
    if not cols:
        return []
    sl = slice(pos - window + 1, pos + 1)
    win = returns[cols].iloc[sl]
    p = pd.Series(r_pool).reindex(returns.index).iloc[sl].to_numpy(float)
    m = pd.Series(stress).reindex(returns.index, fill_value=False).astype(bool).iloc[sl].to_numpy()      # a month the stress series does not cover is not stress
    ok_p = m & np.isfinite(p)
    if ok_p.sum() < min_stress:
        return []
    X = win.to_numpy(float)                      # a hole anywhere in a fund's window makes its total return NaN, so the full-window rule needs no separate test
    Xs, ps = X[ok_p], p[ok_p]
    with np.errstate(invalid="ignore", divide="ignore"):
        pc = ps - ps.mean()
        var = float((pc * pc).sum())
        if var <= 0:
            return []
        beta = ((Xs - Xs.mean(axis=0)) * pc[:, None]).sum(axis=0) / var
        mean_i = Xs.mean(axis=0)
        total = np.prod(1.0 + X, axis=0) - 1.0
    keep = (beta <= beta_max) & (mean_i > ps.mean()) & (total > 0)         # NaN anywhere (a hole in the window) compares False
    return sorted(c for c, k in zip(cols, keep) if bool(k))


# ------------------------------------------------------------------------------------------------------------------ exclusion classes
def classify_exclusion(first_reason: pd.Series, f_dd: pd.Series, f_other: pd.Series) -> pd.Series:
    """One class per fund at one date: ELIGIBLE; DD_ONLY (the sealed first reason is the since-inception drawdown filter and no other metric filter fails independently: `f_dd` True, `f_other`
    = return / SRRI / High Yield all False); SHORT (first reason is a short gate: the earlier filters, drawdown included, passed); OTHER (anything else: data, return, SRRI, High Yield, ...).
    Count (a) of the study is DD_ONLY, count (b) is DD_ONLY + SHORT."""
    fr = pd.Series(first_reason).astype(str)
    out = pd.Series("OTHER", index=fr.index, dtype=object)
    out[fr == "eligible"] = "ELIGIBLE"
    dd = f_dd.reindex(fr.index, fill_value=False).astype(bool)             # unknown drawdown verdict: not DD-only
    other = f_other.reindex(fr.index, fill_value=True).astype(bool)         # unknown other filters: assume one fails (conservative)
    out[(fr == "max_drawdown") & dd & ~other] = "DD_ONLY"
    out[fr.str.startswith("short_")] = "SHORT"
    return out


# ------------------------------------------------------------------------------------------------------------------ F1b gate
def forward_stress_excess(returns: pd.DataFrame, r_pool: pd.Series, stress: pd.Series, t, horizon: int = 12) -> "tuple[pd.Series, int]":
    """Realised OUTCOME for the predictive gate: per fund, the mean of (r_i - r_pool) over the stress months in (t, t + horizon]; NaN when the fund has none finite. Also returns the id (position of
    the first forward stress month) used as the cluster label of the observation, or 0 when the window holds no stress month."""
    pos = returns.index.get_indexer([pd.Timestamp(t)])[0]
    fwd = returns.iloc[pos + 1:pos + 1 + horizon]
    m = pd.Series(stress).reindex(returns.index, fill_value=False).astype(bool).iloc[pos + 1:pos + 1 + horizon].to_numpy()
    if not m.any():
        return pd.Series(np.nan, index=returns.columns, dtype=float), 0
    p = pd.Series(r_pool).reindex(returns.index).iloc[pos + 1:pos + 1 + horizon].to_numpy(float)
    ex = fwd.to_numpy(float)[m] - p[m][:, None]
    first = int(np.flatnonzero(m)[0]) + pos + 1
    with np.errstate(invalid="ignore"):
        mean = np.nanmean(ex, axis=0)
    return pd.Series(mean, index=returns.columns, dtype=float), first


def paired_differences(candidates, excluded, cell_of: pd.Series, sub_of: pd.Series, x: pd.Series) -> list:
    """For every cell (sub-portfolio x nature) that holds a candidate: d = mean(x of the candidates) - mean(x of the controls). Controls = excluded non-candidates of the same cell with a finite `x`;
    with none, the same sub-portfolio (every nature); with none either the cell is DROPPED. One dict per kept cell: cell, level ('cell' | 'sub'), d."""
    cand = [c for c in candidates if c in x.index and np.isfinite(x[c])]
    ctrl_all = [e for e in excluded if e not in set(candidates) and e in x.index and np.isfinite(x[e])]
    rows = []
    for cell in sorted({cell_of[c] for c in cand if c in cell_of.index}):
        cs = [c for c in cand if cell_of.get(c) == cell]
        ctrl = [e for e in ctrl_all if cell_of.get(e) == cell]
        level = "cell"
        if not ctrl:
            sub = sub_of.get(cs[0])
            ctrl = [e for e in ctrl_all if sub_of.get(e) == sub]
            level = "sub"
        if not ctrl:
            continue
        rows.append({"cell": cell, "level": level, "d": float(x[cs].mean() - x[ctrl].mean())})
    return rows


def cluster_bootstrap(obs: pd.DataFrame, draws: int = BOOT_DRAWS, seed: int = BOOT_SEED, q: float = 0.05) -> dict:
    """Mean of `obs['d']` with a one-sided lower bound: the q-quantile of the means over `draws` resamples of the CLUSTERS (episodes) with replacement. Deterministic for a seed.
    Also the number of clusters and the sign count (clusters whose own mean is positive)."""
    g = obs.groupby("cluster")["d"].agg(["sum", "count"])
    n = len(g)
    if n == 0:
        return {"mean": float("nan"), "lb": float("nan"), "n_obs": 0, "n_clusters": 0, "sign_count": 0}
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, n, size=(draws, n))
    sums, cnts = g["sum"].to_numpy(float), g["count"].to_numpy(float)
    means = sums[pick].sum(axis=1) / cnts[pick].sum(axis=1)
    return {"mean": float(obs["d"].mean()), "lb": float(np.quantile(means, q)), "n_obs": int(len(obs)), "n_clusters": int(n),
            "sign_count": int(((g["sum"] / g["count"]) > 0).sum())}


def persistence_verdict(obs: pd.DataFrame, n_candidate_cells: int) -> dict:
    """The F1b gate. `n_candidate_cells` = every (date, cell) that held a candidate; `obs` = the kept ones. Not evaluable when the dropped share exceeds MAX_DROPPED. Passed when the nominal
    one-sided 95 % bound is > 0 and, with fewer than SIGN_EPISODES_BELOW episodes, the pooled mean is > 0 and at least SIGN_SHARE of the episodes are positive. The Holm reference is
    reported by the caller, never applied here."""
    dropped = (n_candidate_cells - len(obs)) / n_candidate_cells if n_candidate_cells else 1.0
    b = cluster_bootstrap(obs) if len(obs) else {"mean": float("nan"), "lb": float("nan"), "n_obs": 0, "n_clusters": 0, "sign_count": 0}
    evaluable = bool(dropped <= MAX_DROPPED and len(obs) > 0)
    passed = bool(evaluable and b["lb"] > 0)
    if passed and b["n_clusters"] < SIGN_EPISODES_BELOW:
        passed = bool(b["mean"] > 0 and b["sign_count"] >= SIGN_SHARE * b["n_clusters"])
    return {**b, "dropped_share": float(dropped), "evaluable": evaluable, "passed": passed}


# ------------------------------------------------------------------------------------------------------------------ the fourth column
def basket_weights(cands_by_t: dict, dates, cadence: str = "monthly") -> pd.DataFrame:
    """Equal weights decided at each date of `dates` (rows; columns = every ISIN named). An empty membership is a row of zeros (CASH). `quarterly`: membership is re-read only every third
    date (by position) and carried in between."""
    cols = sorted({i for v in cands_by_t.values() for i in v})
    rows, last = [], pd.Series(0.0, index=cols)
    for k, t in enumerate(pd.DatetimeIndex(dates)):
        if cadence == "monthly" or k % 3 == 0:
            members = list(cands_by_t.get(t, []))
            last = pd.Series(0.0, index=cols)
            if members:
                last[members] = 1.0 / len(members)
        rows.append(last.copy())
    return pd.DataFrame(rows, index=pd.DatetimeIndex(dates), columns=cols)


def basket_series(W: pd.DataFrame, returns: pd.DataFrame, cash_ret: pd.Series, entry: pd.Series, exit_: pd.Series) -> "tuple[pd.Series, pd.Series]":
    """Net return per unit of basket capital, indexed by the month it is EARNED (decision date + 1), and its state ('ACTIVE' when the basket held funds, else 'CASH').
    Funds without a return that month leave the mean (weights renormalised); the cash share earns `cash_ret`; trading at the decision date pays `entry` on every increase and `exit_` on every
    decrease of a fund's weight (the cash side is free), borne by the month that follows."""
    idx = returns.index
    for name, fee in (("entry", entry), ("exit", exit_)):
        missing = fee.reindex(W.columns).isna()
        if missing.any():
            raise ValueError(f"basket_series: {int(missing.sum())} fund(s) without an {name} fee ({', '.join(map(str, missing[missing].index[:5]))}); "
                             "the caller fills an unknown fee explicitly (the median of the known ones), it is never filled here")
    fee_in, fee_out = entry.reindex(W.columns), exit_.reindex(W.columns)
    out, st = {}, {}
    prev = pd.Series(0.0, index=W.columns)
    for t, w in W.iterrows():
        pos = idx.get_indexer([t])[0]
        if pos < 0 or pos + 1 >= len(idx):
            continue
        nxt = idx[pos + 1]
        delta = w - prev
        cost = float((delta.clip(lower=0) * fee_in).sum() + ((-delta).clip(lower=0) * fee_out).sum())
        held = w[w > 0]
        if held.empty:
            gross = float(cash_ret.get(nxt, np.nan))
            st[nxt] = "CASH"
        else:
            rr = returns.loc[nxt, held.index].astype(float)
            fin = rr.notna()
            if fin.any():
                wf = held[fin[fin].index]
                gross = float((rr[wf.index] * (wf / wf.sum())).sum())
            else:
                gross = float(cash_ret.get(nxt, np.nan))
            st[nxt] = "ACTIVE"
        out[nxt] = gross - cost
        prev = w
    return pd.Series(out, dtype=float), pd.Series(st, dtype=object)


def random_candidates(observed: dict, excluded_by_t: dict, rng: np.random.Generator) -> dict:
    """One null draw: at every date the same NUMBER of funds as the observed basket, taken without replacement from the excluded set of that date (empty stays empty, so the CASH months are the observed ones)."""
    out = {}
    for t, members in observed.items():
        k = len(members)
        pool = list(excluded_by_t.get(t, []))
        out[t] = [] if k == 0 else sorted(rng.choice(pool, size=k, replace=False).tolist())
    return out


def reach(cands_by_t: dict, manager_of: pd.Series, max_per_manager: int = 2, min_funds: int = 3) -> float:
    """Share of the dates with at least `min_funds` basket funds left after at most `max_per_manager` per management company (the builder's own count constraint; the first by ISIN order are kept)."""
    ok = 0
    for members in cands_by_t.values():
        seen, kept = {}, 0
        for i in sorted(members):
            m = manager_of.get(i, i)
            if seen.get(m, 0) < max_per_manager:
                seen[m] = seen.get(m, 0) + 1
                kept += 1
        ok += kept >= min_funds
    return ok / len(cands_by_t) if cands_by_t else float("nan")


# ------------------------------------------------------------------------------------------------------------------ mixes and attribution
def _compositions(n: int, parts: int) -> np.ndarray:
    """All integer vectors of `parts` entries summing to n, lexicographic order (the grid order of `pit_crisis_diagnostic.best_static_mix` for three parts)."""
    if parts == 1:
        return np.array([[n]])
    rows = []
    for a in range(n + 1):
        for rest in _compositions(n - a, parts - 1):
            rows.append([a, *rest])
    return np.array(rows)


def optimise_mix(S: pd.DataFrame, dd_limit: float = -0.15, step: float = 0.1) -> dict:
    """The constant-weight mix of the columns of `S` (grid of `step`, sums to 1) with the highest annual return whose maximum drawdown is not worse than `dd_limit`. HINDSIGHT bound,
    the n-column form of `pit_crisis_diagnostic.best_static_mix` (same arithmetic, same tie order; months with a NaN in any column are dropped). Returns weights, ann, max_dd."""
    X = S.dropna().to_numpy(float)
    k = int(round(1.0 / step))
    Wg = _compositions(k, S.shape[1]) / float(k)
    P = X @ Wg.T
    cum = np.cumprod(1.0 + P, axis=0)
    ann = cum[-1] ** (12.0 / len(X)) - 1.0
    dd = (cum / np.maximum.accumulate(cum, axis=0) - 1.0).min(axis=0)
    okm = dd >= dd_limit
    if not okm.any():
        return {"weights": pd.Series(np.nan, index=S.columns), "ann": float("nan"), "max_dd": float("nan")}
    best = int(np.flatnonzero(okm)[np.argmax(ann[okm])])
    return {"weights": pd.Series(Wg[best], index=S.columns), "ann": float(ann[best]), "max_dd": float(dd[best])}


def attribute_gain(r_new: pd.Series, r_front: pd.Series, state: pd.Series) -> dict:
    """Exact attribution by state of the gain of the new mix over the frontier: dR_t = r_new - r_front, Gain_s = 12 * sum_{t in s} dR_t / N with N every month with both returns, so
    ACTIVE + CASH = total BY CONSTRUCTION (an all-CASH basket has ACTIVE = 0)."""
    d = (pd.Series(r_new) - pd.Series(r_front)).dropna()
    n = len(d)
    s = pd.Series(state).reindex(d.index)
    gain = {k: float(12.0 * d[s == k].sum() / n) if n else float("nan") for k in ("ACTIVE", "CASH")}
    gain["total"] = float(12.0 * d.mean()) if n else float("nan")
    return gain


def brinson_split(S: pd.DataFrame, w_new: pd.Series, w_front: pd.Series) -> "tuple[pd.Series, pd.Series]":
    """Monthly split of the gain into the Convex SLEEVE effect w_c (R_c - R_front) and the base reallocation sum_j (w_j - w*_j)(R_j - R_front); sleeve + reallocation = R_new - R_front each month
    because the weights of the base pools drop by exactly w_c. The Convex column is the one of `w_new` that `w_front` lacks."""
    front_cols, new_cols = list(w_front.index), list(w_new.index)
    r_front = S[front_cols] @ w_front
    conv = [c for c in new_cols if c not in front_cols][0]
    sleeve = w_new[conv] * (S[conv] - r_front)
    realloc = sum((w_new[j] - w_front[j]) * (S[j] - r_front) for j in front_cols)
    return sleeve, realloc


def gain_by_state(S3: pd.DataFrame, basket: pd.Series, state: pd.Series, dd_limit: float = -0.15, step: float = 0.1, column: str = "Convex") -> dict:
    """Gain of the best 4-column mix (the three pools + the basket) over the best 3-column mix, attributed to the OBSERVED ACTIVE / CASH months. This is the statistic of the null and of the
    observed basket alike: the null draws are scored on `Gain_ACTIVE` with the observed state, never on the total."""
    S4 = S3.copy()
    S4[column] = pd.Series(basket).reindex(S3.index)
    S4 = S4.dropna()
    front = optimise_mix(S4[list(S3.columns)], dd_limit, step)
    new = optimise_mix(S4, dd_limit, step)
    r_front = S4[list(S3.columns)] @ front["weights"]
    r_new = S4 @ new["weights"]
    g = attribute_gain(r_new, r_front, pd.Series(state).reindex(S4.index))
    g.update({"ann_front": front["ann"], "ann_new": new["ann"], "dd_new": new["max_dd"], "weights": new["weights"], "weights_front": front["weights"],
              "r_new": r_new, "r_front": r_front})
    return g


# ------------------------------------------------------------------------------------------------------------------ sets and observations
def excluded_sets(cls: pd.DataFrame) -> "tuple[dict, dict]":
    """Per date, the funds the basket may draw from. `cls`: one row per (as_of, sub-portfolio, isin) with `eligible` (bool) and `cls` (`classify_exclusion`). A fund is in set (a) when it has NO
    eligible row in any sub-portfolio and EVERY row is DD_ONLY, in set (b) when no row is eligible and every row is DD_ONLY or SHORT (a fund eligible somewhere is already available to the model).
    Returns ({date: sorted isins} for (a), the same for (b))."""
    d = cls.assign(_dd=cls["cls"].eq("DD_ONLY"), _b=cls["cls"].isin(["DD_ONLY", "SHORT"]), _el=cls["eligible"].astype(bool))
    g = d.groupby(["as_of", "isin"]).agg(el=("_el", "any"), all_dd=("_dd", "all"), all_b=("_b", "all")).reset_index()
    g = g[~g["el"]]
    a = {t: sorted(x["isin"]) for t, x in g[g["all_dd"]].groupby("as_of")}
    b = {t: sorted(x["isin"]) for t, x in g[g["all_b"]].groupby("as_of")}
    return a, b


def primary_cells(rows: pd.DataFrame, sub_order) -> "tuple[pd.Series, pd.Series]":
    """(cell, sub) of every fund: its PRIMARY sub-portfolio is the first of `sub_order` among the sub-portfolios it is scored in, the cell is 'sub|nature'. `rows`: isin, sub, nature."""
    rank = {s: i for i, s in enumerate(sub_order)}
    r = rows.assign(_r=rows["sub"].map(rank)).sort_values(["isin", "_r"], kind="mergesort").drop_duplicates("isin")
    sub = r.set_index("isin")["sub"]
    cell = (r["sub"] + "|" + r["nature"].astype(str)).set_axis(r["isin"].to_numpy())
    return cell, sub


def persistence_observations(returns, r_pool, stress, episodes, cands_by_t: dict, excluded_by_t: dict, cell_of, sub_of, dates, horizon: int = 12):
    """The F1b observations: at every date with candidates AND at least one stress month ahead, one paired difference per candidate cell (`paired_differences`), clustered by the episode of the
    first stress month ahead. Returns (observations [t, cluster, cell, level, d], number of candidate cells at the dates that had a stress month ahead, number of dates skipped for having none).
    A candidate cell without a valid pairing is counted in the first number and absent from the observations: that is what `persistence_verdict` reads as DROPPED."""
    ep = pd.Series(episodes).reindex(returns.index, fill_value=0)
    rows, n_cells, no_forward = [], 0, 0
    for t in dates:
        cands = list(cands_by_t.get(t) or [])
        if not cands:
            continue
        x, first = forward_stress_excess(returns, r_pool, stress, t, horizon)
        if first == 0:
            no_forward += 1
            continue
        cluster = int(ep.iloc[first])
        n_cells += len({cell_of[c] for c in cands if c in cell_of.index})
        for r in paired_differences(cands, excluded_by_t.get(t, []), cell_of, sub_of, x):
            rows.append({"t": t, "cluster": cluster, **r})
    return pd.DataFrame(rows, columns=["t", "cluster", "cell", "level", "d"]), n_cells, no_forward


def basket_by_decision_date(cands_by_t: dict, dates, returns: pd.DataFrame, cash_ret: pd.Series, entry: pd.Series, exit_: pd.Series, cadence: str,
                            min_candidates: int = MIN_CANDIDATES, haircut_share: float = 0.0, stress_path: "pd.Series | None" = None):
    """The fourth column as the optimiser needs it: indexed by the DECISION date t (the return t -> t+1, net of the trading cost paid at t), with its state. Applies the thin-window rule, the
    cadence, the explicit fees and, in the ACTIVE months only, the survivorship haircut `(1 - s) r + s stress_path` (`haircut_share` s = a * k; a month that holds cash holds no fund and is not
    haircut). An ACTIVE month without a finite stress path is refused, never filled. Returns (basket, state, weights)."""
    W = basket_weights(thin_window(cands_by_t, min_candidates), dates, cadence)
    series, state = basket_series(W, returns, cash_ret, entry, exit_)
    shift = pd.offsets.MonthEnd(1)
    basket = pd.Series(series.to_numpy(float), index=series.index - shift)
    state = pd.Series(state.to_numpy(object), index=state.index - shift)
    if haircut_share > 0:
        if stress_path is None:
            raise ValueError("basket_by_decision_date: a haircut needs a stress path")
        active = state.index[state == "ACTIVE"]
        sp = pd.Series(stress_path).reindex(active)
        if not np.isfinite(sp.to_numpy(float)).all():
            raise ValueError("basket_by_decision_date: the stress path is missing in an ACTIVE month; it is never filled")
        basket[active] = (1.0 - haircut_share) * basket[active] + haircut_share * sp
    return basket, state, W
