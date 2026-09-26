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
    return checks


def load_freshness_inputs(conn) -> tuple[list, object]:
    """(per-fund newest monthly NAV dates of the active universe, newest harvest_ts).

    Plain SQL valid on both backends (unquoted lowercase identifiers resolve on Postgres and
    SQLite alike); dates are normalised by evaluate_freshness.
    """
    nav_rows = conn.execute(
        "SELECT n.isin, MAX(n.date) FROM fund_nav_monthly n "
        "JOIN fund_master fm ON fm.isin = n.isin "
        "WHERE fm.in_current_universe = 1 GROUP BY n.isin"
    ).fetchall()
    harvest = conn.execute("SELECT MAX(harvest_ts) FROM db_document_catalogue").fetchone()
    return [r[1] for r in nav_rows], (harvest[0] if harvest else None)


def check_universe_freshness(conn, classifier, today: Optional[date] = None) -> list[FreshnessCheck]:
    """Read the inputs and evaluate them with the limits from shared/config.py."""
    from shared.config import (
        P3_FRESHNESS_MAX_AGE_DAYS, P3_MACRO_RELEASE_LAG_INDICATORS, P3_NAV_UNIVERSE_PERCENTILE,
    )
    nav_dates, harvest_ts = load_freshness_inputs(conn)
    return evaluate_freshness(
        nav_dates, classifier.input_last_dates(), harvest_ts,
        today or date.today(), P3_FRESHNESS_MAX_AGE_DAYS,
        P3_NAV_UNIVERSE_PERCENTILE, P3_MACRO_RELEASE_LAG_INDICATORS,
    )


def stale_checks(checks: Iterable[FreshnessCheck]) -> list[FreshnessCheck]:
    return [c for c in checks if not c.ok]


def format_report(checks: list[FreshnessCheck]) -> str:
    lines = ["DATA FRESHNESS (P3 gate)"]
    for c in checks:
        newest = c.newest.isoformat() if c.newest else "sin datos"
        age = f"{c.age_days}d" if c.age_days is not None else "n/a"
        lines.append(f"  [{'OK ' if c.ok else 'STALE'}] {c.name:24s} newest={newest:10s} "
                     f"age={age:>5s} (max {c.max_age_days}d)  {c.detail}")
    return "\n".join(lines)
