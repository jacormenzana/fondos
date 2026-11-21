# proyecto3/src/ranking_levers.py
# -*- coding: utf-8 -*-
"""The four ranking-layer levers of PREREG_P3_5A (doc/decisiones/PREREG_P3_5A.md, Package L step F): ONE implementation for the live build and the point-in-time backtest (R3). PURE
(pandas / numpy, no database, no pipeline import: R-7); `fund_scorer.score_funds_from_df` and `compute_base_scores` call it, so a lever is applied by the very function production runs.

Every lever ships OFF (R2): `LeverConfig()` is all-off and `LeverConfig.from_config()` reads the four `P3_L*_ENABLED` switches of shared/config.py (all False). With every lever off the scorer's
behaviour and every PIT cache key are exactly what they were.

  L1  peer-relative hard filters (Equilibrada and Dinamica only): a fund is excluded if it is in the worst share q of its (date, sub-portfolio, nature) cell on the ROLLING 60-month maximum
      drawdown (the since-inception value for a fund without one) or on the since-inception real return, or if it breaches an absolute backstop. A cell with fewer than MIN_CELL funds with a
      value keeps the ABSOLUTE filter. `check_hard_filters` still reads the RAW `return_ann_real`.
  L2  overextension penalty (Equilibrada only): a robust z-score of the annualised trailing 3-year NOMINAL return inside the (date, nature) cell; above z0 the final score is multiplied by 0.80.
  L3  recovery modulation (all sub-portfolios): the state of the equal-weight pool of the eligible funds (a function of the pool series only, never the regime label); in `recovery` the nature
      bonus b becomes 1 + (b - 1) k and the max_dd weight becomes w k, the other weights rescaled proportionally. The parameters are the ones frozen by hash in the preregistration
      (`pit_p35a_prep.L3_PARAMS`; a test pins that these constants equal it).
  L4  one-off fee penalty: `return_ann_real_net = return_ann_real - (entry + exit) / H` deducted BEFORE the within-nature percentile normalisation of the return; an unknown fee takes the
      median penalty of the cell (counted by the caller, never silent).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# ---- constants fixed by the preregistration -------------------------------------------------------------------------------------------------------------------------
L1_SUBS = ("Equilibrada", "Dinamica")
L1_DD_BACKSTOP = {"Equilibrada": -0.60, "Dinamica": -0.80}      # twice the current absolute drawdown limit (-0.30 / -0.40)
L1_RET_BACKSTOP_DELTA = 0.05                                    # the current real-return limit minus 5 pp
MIN_CELL = 15                                                   # a cell needs this many funds with a value for a relative rule (the cell size of FND-0429); fewer keeps the absolute filter
L2_SUBS = ("Equilibrada",)
L2_MULT = 0.80                                                  # 1 / 1.25, the inverse of the largest nature bonus
MAD_SCALE = 1.4826                                              # a MAD to a standard deviation under normality
DD60_COLUMN = "max_dd__rolling_5y"                              # P2: fund_metrics max_dd, horizon rolling_5y, real_flag 0 (the stored 60-month window)
RET36_COLUMN = "return_ann__rolling_3y"                         # P2: fund_metrics return_ann, horizon rolling_3y, real_flag 0 (annualised nominal)
FEE_COLUMNS = ("Entry_Fee_Pct", "Exit_Fee_Pct")                 # fund_master, ratios (0.05 = 5 %)

# L3: the frozen parameters (pit_p35a_prep.L3_PARAMS)
L3_TRAILING_MONTHS = 12
L3_STRESS_THRESHOLD = 0.03
L3_RECOVERY_RETURN_MONTHS = 3
L3_RECOVERY_CONFIRMATIONS = 2
L3_MIN_REBOUND = 0.05


@dataclass(frozen=True)
class LeverConfig:
    """Which levers are on and at which value; None = OFF. Values are the preregistered ones: l1_q in {0.20, 0.35}, l2_z0 in {2.0, 1.5}, l3_k in {0.5, 0.0}, l4_h_years in {3.0, 2.0}."""
    l1_q: "float | None" = None
    l2_z0: "float | None" = None
    l3_k: "float | None" = None
    l4_h_years: "float | None" = None

    @property
    def any_on(self) -> bool:
        return any(v is not None for v in (self.l1_q, self.l2_z0, self.l3_k, self.l4_h_years))

    def key(self) -> dict:
        """Everything that can change a score through a lever, for a cache key. Only meaningful when `any_on`; an all-off config must add NOTHING to a key (the warm caches stay valid)."""
        return {"l1_q": self.l1_q, "l2_z0": self.l2_z0, "l3_k": self.l3_k, "l4_h_years": self.l4_h_years,
                "const": {"l1_dd_backstop": L1_DD_BACKSTOP, "l1_ret_delta": L1_RET_BACKSTOP_DELTA, "min_cell": MIN_CELL, "l2_mult": L2_MULT, "mad": MAD_SCALE,
                          "l3": [L3_TRAILING_MONTHS, L3_STRESS_THRESHOLD, L3_RECOVERY_RETURN_MONTHS, L3_RECOVERY_CONFIRMATIONS, L3_MIN_REBOUND]}}

    @classmethod
    def from_config(cls, cfg=None) -> "LeverConfig":
        """The levers the live configuration switches on (shared.config; all OFF by default)."""
        if cfg is None:
            from shared import config as cfg
        return cls(l1_q=float(cfg.P3_L1_PEER_CUT) if cfg.P3_L1_PEER_FILTER_ENABLED else None,
                   l2_z0=float(cfg.P3_L2_Z0) if cfg.P3_L2_OVEREXTENSION_ENABLED else None,
                   l3_k=float(cfg.P3_L3_RECOVERY_K) if cfg.P3_L3_RECOVERY_MODULATION_ENABLED else None,
                   l4_h_years=float(cfg.P3_L4_H_YEARS) if cfg.P3_L4_FEE_PENALTY_ENABLED else None)


@dataclass(frozen=True)
class LeverInputs:
    """What the PIT must hand `pit_scores` for the levers that are on (the live build gets the same information from P2 and the database): `dd60` / `ret36` = dates x ISIN frames of the trailing 60-month
    maximum drawdown and the trailing 36-month annualised NOMINAL return (`pit_metrics.trailing_risk_metrics`, pinned equal to P2's stored rolling_5y / rolling_3y by
    scripts/audit/p3/p3_rolling_parity.py); `fees` = ISIN-indexed Entry_Fee_Pct / Exit_Fee_Pct (today's values, a documented look-ahead); `nav_m` = the month-end NAV panel (L3's pool)."""
    dd60: "pd.DataFrame | None" = None
    ret36: "pd.DataFrame | None" = None
    fees: "pd.DataFrame | None" = None
    nav_m: "pd.DataFrame | None" = None


def metric_columns(levers: LeverConfig) -> tuple:
    """The extra metric columns a frame must carry for the levers that are on (the live loader reads them from P2, the PIT builds them from its trailing frames). Empty when all are off."""
    cols = []
    if levers.l1_q is not None:
        cols.append(DD60_COLUMN)
    if levers.l2_z0 is not None:
        cols.append(RET36_COLUMN)
    if levers.l4_h_years is not None:
        cols.extend(FEE_COLUMNS)
    return tuple(cols)


# ---- L1 ----------------------------------------------------------------------------------------------------------------------------------------------------------------
def l1_peer_reasons(df: pd.DataFrame, sub: str, q: float, dd_limit: float, ret_limit: float) -> pd.Series:
    """Exclusion reason (str) or None for each fund of one sub-portfolio's frame under L1. Sub-portfolios outside L1_SUBS get None everywhere (their absolute filters stay).
    Order: absolute backstops, then the peer-relative drawdown and real-return tests; a cell too small for a relative test applies the ABSOLUTE limit instead (`dd_limit` / `ret_limit`).
    A missing metric never excludes (fail-open, as the absolute filters)."""
    out = pd.Series(None, index=df.index, dtype=object)
    if sub not in L1_SUBS or not 0.0 < q < 1.0:
        return out
    dd = df[DD60_COLUMN] if DD60_COLUMN in df.columns else pd.Series(np.nan, index=df.index, dtype=float)
    dd = dd.where(dd.notna(), df["max_dd"] if "max_dd" in df.columns else np.nan).astype(float)          # the since-inception value for a fund without a rolling one
    ret = (df["return_ann_real"] if "return_ann_real" in df.columns else pd.Series(np.nan, index=df.index)).astype(float)
    dd_back, ret_back = L1_DD_BACKSTOP[sub], ret_limit - L1_RET_BACKSTOP_DELTA

    def put(mask: pd.Series, text) -> None:
        todo = mask & out.isna()
        for i in todo.index[todo]:
            out.at[i] = text(i)

    put(dd.lt(dd_back), lambda i: f"l1_backstop: drawdown {dd[i]:.2f} < {dd_back} ({sub})")
    put(ret.lt(ret_back), lambda i: f"l1_backstop: return_ann_real={ret[i]:.3f} < {ret_back:.2f} ({sub})")
    nature = df["Fund_Nature"] if "Fund_Nature" in df.columns else pd.Series("all", index=df.index)
    for nat, cell in df.groupby(nature, sort=False).groups.items():
        cell = pd.Index(cell)
        for series, absolute, label, fmt in ((dd, dd_limit, "drawdown", ".2f"), (ret, ret_limit, "return_ann_real", ".3f")):
            vals = series.loc[cell]
            valid = vals.notna()
            n = int(valid.sum())
            mask = pd.Series(False, index=df.index)                                              # a full-index mask for this cell
            if n >= MIN_CELL:
                frac_worse = (vals.rank(method="min", ascending=True) - 1.0) / n                 # share of the cell strictly worse than the fund
                mask.loc[cell[((frac_worse < q) & valid).to_numpy()]] = True
                put(mask, lambda i, v=series, l=label, f=fmt, nn=n, nt=nat: f"l1_peer_{l}: {v[i]:{f}} in the worst {q:.0%} of its cell ({sub}, {nt}, n={nn})")
            else:
                mask.loc[cell[vals.lt(absolute).to_numpy()]] = True
                put(mask, lambda i, v=series, l=label, a=absolute, f=fmt: f"{l}={v[i]:{f}} < {a} ({sub}, cell below {MIN_CELL})")
    return out


# ---- L2 ----------------------------------------------------------------------------------------------------------------------------------------------------------------
def l2_multiplier(df: pd.DataFrame, sub: str, z0: float) -> pd.Series:
    """Score multiplier of each fund under L2: L2_MULT where the robust z-score of the annualised trailing 3-year nominal return inside the (sub-portfolio, nature) cell exceeds `z0`, else 1.0.
    Only the sub-portfolios of L2_SUBS; a cell with fewer than MIN_CELL funds with a value, or a zero MAD, or a missing metric leaves 1.0."""
    out = pd.Series(1.0, index=df.index)
    if sub not in L2_SUBS or RET36_COLUMN not in df.columns:
        return out
    x = df[RET36_COLUMN].astype(float)
    nature = df["Fund_Nature"] if "Fund_Nature" in df.columns else pd.Series("all", index=df.index)
    for _nat, cell in df.groupby(nature, sort=False).groups.items():
        v = x.loc[cell].dropna()
        if len(v) < MIN_CELL:
            continue
        med = float(v.median())
        scale = MAD_SCALE * float((v - med).abs().median())
        if not scale > 0:
            continue
        z = (v - med) / scale
        out.loc[z.index[z > z0]] = L2_MULT
    return out


# ---- L3 ----------------------------------------------------------------------------------------------------------------------------------------------------------------
def month_end_rows(nav: pd.DataFrame) -> pd.DataFrame:
    """The rows of a NAV panel that are month-ends (the PIT panel keeps a stray mid-month NAV on its real date; it is not a month)."""
    return nav[nav.index.is_month_end]


def latest_complete_month_end(nav_m: pd.DataFrame, isins, share: float = 0.5):
    """The latest month-end of a month-end NAV panel at which at least `share` of `isins` have a NAV (the live build runs mid-month, when the newest month is still partial); None if there is none."""
    if nav_m is None or nav_m.empty or not len(isins):
        return None
    counts = nav_m.reindex(columns=list(isins)).notna().sum(axis=1)
    ok = counts[counts >= share * len(isins)]
    return None if ok.empty else pd.Timestamp(ok.index.max())


def pool_returns_from_nav(nav_m: pd.DataFrame, isins, t, months: int = L3_TRAILING_MONTHS) -> pd.Series:
    """Monthly returns of the equal-weight pool of `isins` over the `months` month-ends ending at `t` (month-end NAV panel; a fund without both NAVs of a month drops out of that month's mean,
    never filled). NaN where no fund has a return."""
    grid = pd.date_range(end=pd.Timestamp(t), periods=months + 1, freq="ME")
    navs = nav_m.reindex(index=grid, columns=list(isins))
    return navs.pct_change(fill_method=None).iloc[1:].mean(axis=1, skipna=True)


def pool_state(returns: pd.Series) -> str:
    """`normal`, `stress` or `recovery` from the pool's last L3_TRAILING_MONTHS monthly returns (a function of the pool series only). Levels start at 1 and the trailing high / low are taken over the
    13 month-end levels of the window. stress = the level is more than L3_STRESS_THRESHOLD below the high; recovery = stress AND the 3-month return is positive at L3_RECOVERY_CONFIRMATIONS
    consecutive month-ends AND the level is at least L3_MIN_REBOUND above the low. Fewer than L3_TRAILING_MONTHS finite returns: `normal` (no modulation on missing data)."""
    r = pd.Series(returns).astype(float).iloc[-L3_TRAILING_MONTHS:]
    if len(r) < L3_TRAILING_MONTHS or not np.isfinite(r.to_numpy()).all():
        return "normal"
    level = np.concatenate(([1.0], np.cumprod(1.0 + r.to_numpy())))
    stress = level[-1] < (1.0 - L3_STRESS_THRESHOLD) * level.max()
    if not stress:
        return "normal"
    k = L3_RECOVERY_RETURN_MONTHS
    ok = all(level[-1 - j] / level[-1 - j - k] - 1.0 > 0 for j in range(L3_RECOVERY_CONFIRMATIONS))
    return "recovery" if (ok and level[-1] >= (1.0 + L3_MIN_REBOUND) * level.min()) else "stress"


def modulate_bonus(bonus: dict, k: float) -> dict:
    """In recovery b -> 1 + (b - 1) k (k = 1 is the identity, k = 0 removes the bonus)."""
    return {nat: 1.0 + (float(b) - 1.0) * k for nat, b in bonus.items()}


def modulate_weights(weights: dict, k: float, key: str = "max_dd") -> dict:
    """In recovery w_dd -> w_dd k and the other weights rescaled proportionally so that the total is unchanged (k = 1 is the identity)."""
    if key not in weights:
        return dict(weights)
    total = float(sum(weights.values()))
    w_dd = float(weights[key])
    new_dd = w_dd * k
    rest = total - w_dd
    scale = (total - new_dd) / rest if rest > 0 else 1.0
    return {m: (new_dd if m == key else float(w) * scale) for m, w in weights.items()}


# ---- L4 ----------------------------------------------------------------------------------------------------------------------------------------------------------------
def l4_penalty(df: pd.DataFrame, h_years: float) -> "tuple[pd.Series, int]":
    """Annual penalty (entry + exit) / H of each fund of one cell (a ratio, like `return_ann_real`), and the number of funds whose fee was unknown. A fee is unknown when either side is missing,
    negative or non-finite; such a fund takes the MEDIAN penalty of the known ones of the same cell (counted, never silent). No known fee in the cell: NaN penalties (the metric stays raw)."""
    if not all(c in df.columns for c in FEE_COLUMNS):
        return pd.Series(np.nan, index=df.index, dtype=float), int(len(df))
    entry, exit_ = (pd.to_numeric(df[c], errors="coerce") for c in FEE_COLUMNS)
    known = entry.notna() & exit_.notna() & np.isfinite(entry) & np.isfinite(exit_) & (entry >= 0) & (exit_ >= 0)
    pen = ((entry + exit_) / h_years).where(known)
    n_unknown = int((~known).sum())
    if known.any():
        pen = pen.where(known, float(pen[known].median()))
    return pen.astype(float), n_unknown
