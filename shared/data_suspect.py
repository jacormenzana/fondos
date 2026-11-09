"""One deterministic predicate for "this fund's published numbers cannot be trusted as they stand" (FND-0394, WS2 of FND-0391; plan amendments A3 G-01, A7).

First slice, PURE: it judges a panel of stored metrics (and the NAV-level flags the caller already has) and returns suspects, one per fund FAMILY. No database, no selection wiring: the build-time
eligibility, the promotion gate and the PIT backtest will call it behind `shared.config.DATA_SUSPECT_ENABLED` (ships OFF); the audit's incident rules (WS3) will call the same function, so no consumer
depends on when the audit last ran (plan G-01) and no two consumers can disagree (P#11).

What the 2026-11-08 forensics of the 22 funds with |MAD-Z| >= 50 taught, and what this module therefore does and does not do (FND-0393):
  * 1 of 22 was a NAV defect (a +92.9% one-month level shift no peer made). That signal is NAV-level and already has its validator (`validate_nav_unshared_jump`); this module takes its result as input
    (`nav_flags`) instead of re-deriving it, and counts it as its own tier.
  * 16 of 22 were SMALL-SAMPLE metrics (a 3-year rolling Sharpe slope computed on 3 points where the fund's nature has 36; a regime metric on half the usual history). A deviation tier on raw robust z-scores
    would flag them all and drown the real cases. So a metric COUNTS only when its sample is adequate: not both below the nature's 10th percentile AND below half its median of `source_rows`.
  * Judging a fund inside its own Fund_Nature did NOT remove the extremes (21 of 22 were as extreme or more), so the baseline here is the universe, as the audit uses; the nature enters only through the
    sample-size reference, where it matters.
Tiers (a fund is a suspect when any applies): NAV_JUMP (caller-supplied flag), DEVIATION (one adequate-sample metric with |z| >= min_dev), INTEGRITY (>= min_metrics distinct adequate-sample QUANTITIES at
|z| >= min_dev, where the nominal and real variants of one metric and horizon are one quantity (`quantity_of`); money-market funds excluded, they are structurally extreme on ratio metrics). Correlated
quantities that differ in name (a Sharpe and a Sortino of one horizon) still count apart: WS3 (FND-0395) refines the unit further. Findings are reduced to FAMILIES (`shared.family_scope`), so three share classes are one suspect.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional

import numpy as np
import pandas as pd

from shared import config as _cfg
from shared.family_scope import collapse_flags

MAD_SCALE = 0.6745            # modified z = 0.6745 * (x - median) / MAD (the definition the audit's OUTLIER_MAD_Z uses)
KIND_NAV_JUMP, KIND_DEVIATION, KIND_INTEGRITY = "NAV_JUMP", "DEVIATION", "INTEGRITY"
PANEL_COLUMNS = ("isin", "nature", "metric", "value", "source_rows")


def robust_z(x: float, values) -> Optional[float]:
    """Modified z of x against `values` (median / MAD). None when it cannot be computed (fewer than 5 finite values, or MAD = 0)."""
    v = np.asarray([a for a in values if a is not None and np.isfinite(a)], dtype=float)
    if len(v) < 5:
        return None
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med)))
    return None if mad == 0 else MAD_SCALE * (x - med) / mad


def quantity_of(metric_key) -> str:
    """The QUANTITY a stored metric key measures: 'metric|horizon' of a key 'metric|horizon|real_flag|version'. The nominal (real_flag 0) and the real (real_flag 1) variants, and any metric_version, of the
    same metric and horizon are ONE quantity (owner order 2026-11-08: ES0116848013's two keys were the nominal and real sortino of crisis_2022, counted twice). A key that is not in the four-part
    form is its own quantity. Pure."""
    parts = str(metric_key).split("|")
    return "|".join(parts[:2]) if len(parts) >= 4 else str(metric_key)


def score_panel(panel: pd.DataFrame, small_sample_percentile: Optional[float] = None, small_sample_fraction: Optional[float] = None) -> pd.DataFrame:
    """The panel (columns PANEL_COLUMNS; one row per fund and metric key, e.g. 'sharpe_slope|rolling_3y|0|v1') plus `z` (universe robust z of the metric, NaN when undefined) and `adequate`
    (True when the row's `source_rows` is NOT both below the nature's `small_sample_percentile` and below `small_sample_fraction` of the nature's median for that metric; unknown `source_rows` counts as
    adequate: absence of evidence is not a small sample). Pure; the input is not modified."""
    small_sample_percentile = _cfg.DATA_SUSPECT_SMALL_SAMPLE_PERCENTILE if small_sample_percentile is None else small_sample_percentile
    small_sample_fraction = _cfg.DATA_SUSPECT_SMALL_SAMPLE_FRACTION if small_sample_fraction is None else small_sample_fraction
    missing = [c for c in PANEL_COLUMNS if c not in panel.columns]
    if missing:
        raise ValueError(f"panel lacks columns {missing}")
    df = panel[list(PANEL_COLUMNS)].copy()
    df = df[np.isfinite(df["value"].astype(float))]
    g = df.groupby("metric")["value"]
    med = g.transform("median")
    mad = (df["value"] - med).abs().groupby(df["metric"]).transform("median")
    n = g.transform("count")
    df["z"] = np.where((mad > 0) & (n >= 5), MAD_SCALE * (df["value"] - med) / mad.replace(0, np.nan), np.nan)
    sr = df["source_rows"].astype(float)
    gn = sr.groupby([df["metric"], df["nature"]])
    p_lo = gn.transform(lambda s: np.nanpercentile(s, small_sample_percentile) if s.notna().sum() >= 5 else np.nan)
    p_mid = gn.transform("median")
    small = sr.notna() & p_lo.notna() & (sr < p_lo) & (sr < small_sample_fraction * p_mid)
    df["adequate"] = ~small
    return df


@dataclass
class Suspect:
    family: str
    isins: tuple
    representative: str
    kinds: tuple
    n_metrics: int = 0
    max_abs_z: float = 0.0
    detail: dict = field(default_factory=dict)


def find_suspects(panel: pd.DataFrame, nav_flags: Optional[Mapping[str, str]] = None, family_of: Optional[Mapping] = None, *, min_dev: Optional[float] = None,
                  min_metrics: Optional[int] = None, exclude_natures: Iterable[str] = ("Monetario",), small_sample_percentile: Optional[float] = None,
                  small_sample_fraction: Optional[float] = None) -> list:
    """The suspects of a panel, one per fund family, most extreme first. `nav_flags` = {isin: reason} from the NAV-level validators (e.g. the unshared one-month jump); `family_of` = {isin: family id}
    (an ISIN without one is a family of one). Deterministic and independent of row order."""
    min_dev = _cfg.DATA_SUSPECT_MIN_DEV if min_dev is None else min_dev
    min_metrics = _cfg.DATA_SUSPECT_MIN_FLAGGED_METRICS if min_metrics is None else min_metrics
    nav_flags, family_of = dict(nav_flags or {}), dict(family_of or {})
    scored = score_panel(panel, small_sample_percentile, small_sample_fraction)
    hit = scored[(scored["z"].abs() >= min_dev) & scored["adequate"]]
    hit = hit.assign(quantity=hit["metric"].map(quantity_of))
    per_fund = hit.groupby("isin").agg(n_metrics=("quantity", "nunique"), max_abs_z=("z", lambda s: float(s.abs().max())))   # n_metrics = distinct QUANTITIES, not keys
    nature_of = scored.drop_duplicates("isin").set_index("isin")["nature"].to_dict()
    kinds: dict = {}
    for isin in nav_flags:
        kinds.setdefault(isin, []).append(KIND_NAV_JUMP)
    for isin, row in per_fund.iterrows():
        kinds.setdefault(isin, []).append(KIND_DEVIATION)
        if row["n_metrics"] >= min_metrics and nature_of.get(isin) not in set(exclude_natures):
            kinds[isin].append(KIND_INTEGRITY)
    flags = {i: float(per_fund["max_abs_z"].get(i, 0.0)) for i in kinds}
    out = []
    for fam, info in collapse_flags(flags, family_of).items():
        members = info["isins"]
        ks = sorted({k for i in members for k in kinds[i]})
        top = hit[hit["isin"] == info["representative"]].assign(a=lambda d: d["z"].abs()).sort_values(["a", "metric"], ascending=[False, True]).head(3)
        out.append(Suspect(family=fam, isins=members, representative=info["representative"], kinds=tuple(ks),
                           n_metrics=int(per_fund["n_metrics"].get(info["representative"], 0)), max_abs_z=round(float(info["score"]), 1),
                           detail={"nav_flag": {i: nav_flags[i] for i in members if i in nav_flags},
                                   "top_metrics": [(r.metric, round(float(r.z), 1), None if pd.isna(r.source_rows) else int(r.source_rows)) for r in top.itertuples()]}))
    return sorted(out, key=lambda s: (-s.max_abs_z, s.family))


def load_panel(conn: "psycopg.Connection") -> pd.DataFrame:
    """The in-universe panel of stored metrics for find_suspects: one row per (fund, metric key 'metric|horizon|real_flag|version'), with `source_rows`. Read-only."""
    rows = conn.execute(
        "SELECT m.isin, f.fund_nature, m.metric || '|' || m.horizon || '|' || m.real_flag || '|' || m.metric_version, m.value, m.source_rows "
        "FROM fund_metrics m JOIN fund_master f ON f.isin = m.isin WHERE f.in_current_universe = 1 AND f.fund_nature IS NOT NULL AND m.value IS NOT NULL").fetchall()
    return pd.DataFrame([tuple(r) for r in rows], columns=list(PANEL_COLUMNS))
