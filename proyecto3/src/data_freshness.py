# proyecto3/src/data_freshness.py
# -*- coding: utf-8 -*-
"""
Universe-level data-freshness gate for the P3 build (FND-0098).

P3 scores and builds a portfolio from whatever is in the database; nothing checked that the
inputs were current, so a build on outdated NAV / macro / universe data would succeed silently.
`evaluate_freshness` is a pure function (R-7: no DB, no pipeline imports) over already-loaded
dates; `load_freshness_inputs` does the reads; `format_report` renders the verdict.

The gate is universe-level on purpose: a handful of permanently frozen funds (FND-0041) must not
block every run, so NAV freshness is a low percentile over the active universe, not a max/min.
Limits live in shared/config.py (P3_FRESHNESS_MAX_AGE_DAYS).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Mapping, Optional

import pandas as pd


@dataclass(frozen=True)
class FreshnessCheck:
    name: str                    # e.g. "nav_p10", "macro:oil_yoy", "harvest"
    newest: Optional[date]       # the date being judged (None = no data at all)
    age_days: Optional[int]      # None when there is no data
    max_age_days: int
    ok: bool
    detail: str = ""
    show_age: bool = True        # False for non-date checks (metric-version uniformity)


def _age(newest: Optional[date], today: date) -> Optional[int]:
    if newest is None:
        return None
    # Macro series are indexed at month-end, so the current month is "in the future": age 0.
    return max(0, (today - newest).days)


def _check(name: str, newest: Optional[date], today: date, max_age: int, detail: str = "") -> FreshnessCheck:
    age = _age(newest, today)
    return FreshnessCheck(name, newest, age, max_age, age is not None and age <= max_age, detail)


def _to_date(value) -> Optional[date]:
    """Postgres returns date objects, SQLite text, pandas Timestamps; NaT/None/'' -> None."""
    if value is None or value == "":
        return None
    ts = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(ts) else ts.date()


def parse_harvest_ts(value) -> Optional[date]:
    """db_document_catalogue.harvest_ts is 'YYYYMMDD_HHMMSS' text."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:8], "%Y%m%d").date()
    except ValueError:
        return None


def evaluate_freshness(
    nav_last_dates: Iterable,
    macro_last_dates: Mapping[str, object],
    harvest_ts,
    today: date,
    limits: Mapping[str, int],
    nav_percentile: float,
    release_lag_indicators: Iterable[str],
    metric_versions: Optional[Iterable] = None,
    min_uniform_share: float = 1.0,
) -> list[FreshnessCheck]:
    """Judge NAV, regime-input macro and harvest freshness. Never raises on missing data:
    a missing date is a FAILED check (no data is the stalest case)."""
    checks: list[FreshnessCheck] = []

    navs = [d for d in (_to_date(v) for v in nav_last_dates) if d is not None]
    if navs:
        p = pd.Series(pd.to_datetime(navs)).quantile(nav_percentile).date()
        share = sum(1 for d in navs if (today - d).days <= limits["nav_p10"]) / len(navs)
        checks.append(_check("nav_p10", p, today, limits["nav_p10"],
                             f"{len(navs)} active funds; {share:.1%} within {limits['nav_p10']}d"))
    else:
        checks.append(_check("nav_p10", None, today, limits["nav_p10"], "no NAV rows for the active universe"))

    lag = set(release_lag_indicators)
    for col, last in macro_last_dates.items():
        key = "macro_release" if col in lag else "macro_market"
        checks.append(_check(f"macro:{col}", _to_date(last), today, limits[key], key))

    checks.append(_check("harvest", parse_harvest_ts(harvest_ts), today, limits["harvest"],
                         "newest db_document_catalogue harvest_ts"))
    if metric_versions is not None:
        checks.append(_uniformity_check(list(metric_versions), min_uniform_share))
    return checks


def _uniformity_check(versions: list, min_share: float) -> FreshnessCheck:
    """Metrics computed under different CALC_VERSIONs are not comparable (peer percentiles, momentum
    rank and the P3 score mix them), so require that nearly every active fund sits on ONE version.
    A partial refresh after a CALC_VERSION bump (or a sample run) breaks this on purpose."""
    counts = Counter(str(v) for v in versions if v is not None)
    total = sum(counts.values())
    if not total:
        return FreshnessCheck("metrics_calc_version", None, None, 0, False,
                              "no return_ann metrics for the active universe", show_age=False)
    dominant, n = counts.most_common(1)[0]
    share = n / total
    others = ", ".join(f"{v}:{k}" for v, k in counts.most_common()[1:4]) or "none"
    dated = [v for v in counts if v.isdigit()]            # 'PRE_V26_UNKNOWN' etc. are not versions
    newest = max(dated) if dated else dominant
    note = "" if dominant == newest else f"; NEWER version {newest} exists on {counts[newest]} funds"
    return FreshnessCheck(
        "metrics_calc_version", None, None, 0, share >= min_share,
        f"{share:.1%} of {total} active funds on CALC_VERSION {dominant} (min {min_share:.0%}); others: {others}{note}",
        show_age=False)


def load_freshness_inputs(conn) -> tuple[list, object, list]:
    """(per-fund newest monthly NAV dates of the active universe, newest harvest_ts, per-fund
    CALC_VERSION of the deflated since-inception return_ann row).

    Plain SQL valid on both backends (unquoted lowercase identifiers resolve on Postgres and
    SQLite alike); dates are normalised by evaluate_freshness.
    """
    nav_rows = conn.execute(
        "SELECT n.isin, MAX(n.date) FROM fund_nav_monthly n "
        "JOIN fund_master fm ON fm.isin = n.isin "
        "WHERE fm.in_current_universe = 1 GROUP BY n.isin"
    ).fetchall()
    harvest = conn.execute("SELECT MAX(harvest_ts) FROM db_document_catalogue").fetchone()
    ver_rows = conn.execute(
        "SELECT fm.isin, fm.algorithm_version FROM fund_metrics fm "
        "JOIN fund_master m ON m.isin = fm.isin "
        "WHERE m.in_current_universe = 1 AND fm.metric = 'return_ann' "
        "AND fm.horizon = 'since_inception' AND fm.real_flag = 1"
    ).fetchall()
    return [r[1] for r in nav_rows], (harvest[0] if harvest else None), [r[1] for r in ver_rows]


def family_refresh_check(pending: list, limit: int = 50) -> FreshnessCheck:
    """FND-0244 follow-up lock (pure): is any ACTIVE fund still waiting for the refresh of its nature-derived attributes?

    The family builder rewrites a share class's Fund_Nature; its profile / style / credit quality / duration keep the values of the
    OLD nature until `run_block.py --family-nature-refresh` recomputes them, and the scorer would rank the fund on a mix. `pending` is
    shared.family_refresh.pending_family_refresh (active, non-WRONG_DOC funds only: a retired fund feeds no output). Any pending fund
    is STALE (binary on purpose); the detail carries the count and the first `limit` ISINs."""
    from shared.family_refresh import summarize_pending
    return FreshnessCheck("family_refresh_pending", None, None, 0, not pending,
                          summarize_pending(pending, limit) + ("" if not pending else
                                                               " -- run `run_block.py --family-nature-refresh` (P1_discoverAllFunds.bat does it) BEFORE P3"),
                          show_age=False)


def fx_view_canary_check(pairs: list, tol: float = 0.003, strong: float = 0.01, min_canaries: int = 8,
                         min_share: float = 0.9) -> FreshnessCheck:
    """FND-0235 lock: were the stored fx_contribution_ann values computed under FX_CONTRIBUTION_EUR_VIEW_ENABLED? (pure)

    `pairs` = [(isin, stored, recomputed)] where `recomputed` is the EUR-view value computed NOW from the same NAV and FX series. The
    legacy metric has the OPPOSITE sign, so on a fund whose |recomputed| >= `strong` (1 pp/yr) a legacy row differs by >= 2 pp, far above the
    `tol` (0.3 pp) that a NAV added since the last P2 run can move it. Only those canaries count; fewer than `min_canaries` of them, or fewer than
    `min_share` matching, fails the lock (fail-closed: an unverifiable state is not a verified one). A presence check cannot do this: the
    legacy rows carry the same metric name."""
    strong_pairs = [(i, s, r) for i, s, r in pairs if r is not None and abs(r) >= strong]
    name = "fx_view_canary"
    if len(strong_pairs) < min_canaries:
        return FreshnessCheck(name, None, None, 0, False,
                              f"inconclusive: only {len(strong_pairs)} canary funds with |fx| >= {strong:.0%} (need {min_canaries}); "
                              f"cannot verify the fx family was recomputed under FX_CONTRIBUTION_EUR_VIEW_ENABLED", show_age=False)
    ok_n = sum(1 for _, s, r in strong_pairs if s is not None and abs(s - r) <= tol)
    share = ok_n / len(strong_pairs)
    return FreshnessCheck(
        name, None, None, 0, share >= min_share,
        f"{ok_n}/{len(strong_pairs)} canary funds ({share:.0%}, min {min_share:.0%}) carry the EUR-view fx_contribution_ann"
        + ("" if share >= min_share else " -- the stored values are still the legacy (opposite-sign) metric: "
           "run the P2 recompute of the fx family BEFORE P3"), show_age=False)


def load_fx_canary_pairs(conn, n_candidates: int = 80, n_canaries: int = 25) -> list:
    """[(isin, stored fx_contribution_ann, EUR-view value recomputed now)] for a deterministic sample of the FX-exposed active funds."""
    from proyecto2.src.calculations.currency_factor import compute_currency_factor
    rows = conn.execute(
        "SELECT m.isin, m.fund_currency, m.hedging_policy, m.asset_currency, f.value "
        "FROM fund_metrics f JOIN fund_master m ON m.isin = f.isin "
        "WHERE m.in_current_universe = 1 AND f.metric = 'fx_contribution_ann' "
        "AND f.horizon = 'since_inception' AND f.real_flag = 0 ORDER BY md5(m.isin) LIMIT %s", (n_candidates,)
    ).fetchall()
    pairs = []
    for isin, fund_ccy, hedging, asset_ccy, stored in rows:
        nav = conn.execute("SELECT date, nav FROM fund_nav_monthly WHERE isin = %s ORDER BY date", (isin,)).fetchall()
        nav_df = pd.DataFrame(nav, columns=["date", "nav"])
        nav_df["date"] = pd.to_datetime(nav_df["date"])
        nav_df["nav"] = nav_df["nav"].astype(float)
        out = {m: v for m, v, _ in compute_currency_factor(isin, fund_ccy, hedging, nav_df, conn, asset_currency=asset_ccy)}
        if "fx_contribution_ann" in out:
            pairs.append((isin, None if stored is None else float(stored), float(out["fx_contribution_ann"])))
        if len(pairs) >= n_canaries * 4:           # enough candidates to find the strong ones; the verdict filters them
            break
    return pairs


def check_universe_freshness(conn, classifier, today: Optional[date] = None) -> list[FreshnessCheck]:
    """Read the inputs and evaluate them with the limits from shared/config.py."""
    from shared import config as _config
    from shared.config import (
        P3_FRESHNESS_MAX_AGE_DAYS, P3_MACRO_RELEASE_LAG_INDICATORS, P3_MIN_UNIFORM_METRICS_SHARE,
        P3_NAV_UNIVERSE_PERCENTILE,
    )
    nav_dates, harvest_ts, versions = load_freshness_inputs(conn)
    checks = evaluate_freshness(
        nav_dates, classifier.input_last_dates(), harvest_ts,
        today or date.today(), P3_FRESHNESS_MAX_AGE_DAYS,
        P3_NAV_UNIVERSE_PERCENTILE, P3_MACRO_RELEASE_LAG_INDICATORS,
        metric_versions=versions, min_uniform_share=P3_MIN_UNIFORM_METRICS_SHARE,
    )
    from shared.family_refresh import pending_family_refresh
    checks = list(checks) + [family_refresh_check(pending_family_refresh(conn))]
    if _config.FX_CONTRIBUTION_EUR_VIEW_ENABLED:      # FND-0235: the scorer reads fx_contribution_ann; refuse a legacy-signed one
        checks = list(checks) + [fx_view_canary_check(load_fx_canary_pairs(conn))]
    return checks


def stale_checks(checks: Iterable[FreshnessCheck]) -> list[FreshnessCheck]:
    return [c for c in checks if not c.ok]


def format_report(checks: list[FreshnessCheck]) -> str:
    lines = ["DATA FRESHNESS (P3 gate)"]
    for c in checks:
        tag = 'OK ' if c.ok else 'STALE'
        if not c.show_age:
            lines.append(f"  [{tag}] {c.name:24s} {c.detail}")
            continue
        newest = c.newest.isoformat() if c.newest else "sin datos"
        age = f"{c.age_days}d" if c.age_days is not None else "n/a"
        lines.append(f"  [{tag}] {c.name:24s} newest={newest:10s} "
                     f"age={age:>5s} (max {c.max_age_days}d)  {c.detail}")
    return "\n".join(lines)
