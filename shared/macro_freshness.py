# shared/macro_freshness.py
# -*- coding: utf-8 -*-
"""
Per-series staleness of the macro table (bronze.series_macro) -- FND-0256.

Why: on 2026-10-17 six macro series were found frozen for months or years (euro-area CPI 2025-12, Japan CPI 2021-06, China CPI 2024-03,
M2 JP 2017-02, M2 CN 2019-08, China policy rate effectively since 2015) and NOTHING noticed: the P3 freshness gate looks at the newest point of the
regime inputs only, and ipc_yoy_avg is a mean(axis=1) of the ES and EU legs, so a dead EU leg left the input "fresh" on the Spanish leg alone.

This module judges EVERY series against its own limit and every composite regime input leg by leg. The limit is a property of the series, not a
single number: a series is dated by its stamp (month start for monthly data, 1 January for annual, quarter start for quarterly), so the age of a
perfectly current annual series reaches ~850 days. Each limit below is "worst normal age = publication lag + stamping offset" plus one month of slack.

Pure module: no DB, no network, no pipeline imports (R-7). scripts/audit/macro_freshness_check.py and the cycle report read the DB and call it.
Monitoring only: nothing here stops a run (enforcement in the P3 gate is a separate owner decision).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping

# Applied to a series with no entry below; such a series is reported as `default` so the gap is visible (a test asserts that every indicator the
# loaders write has an explicit entry).
DEFAULT_TTL_DAYS = 100

# (indicator, geography) -> max age, in days, of the NEWEST stamped point. geography "*" = every geography of that indicator; a specific entry wins.
MACRO_TTL_DAYS: dict = {
    # --- monthly price indices: published 2-6 weeks after the month, stamped on the month start (INE, FRED, ECB/Eurostat, OECD) ---
    ("ipc_index", "*"): 100,
    ("cli", "*"): 110,                         # OECD CLI: 6-8 weeks
    # --- money ---
    ("m2_level", "US"): 100, ("m2_level", "EU"): 100,
    ("m2_level", "CN"): 140,                   # OECD monetary aggregates lag ~2.5 months
    ("m2_level", "JP"): 75,                    # Bank of Japan: ~2 weeks after the month
    ("m3_level", "*"): 100, ("m3_yoy", "*"): 100, ("m2_yoy", "*"): 140, ("m2_global_yoy", "*"): 140,   # derived: follow the slowest component
    # --- policy rates (monthly, forward-filled from decisions) ---
    ("rate_policy", "*"): 60, ("rate_deposit", "*"): 60,
    ("rate_policy", "JP"): 100,                # OECD-style feed via FRED: 1-2 months (the DB held 2026-08 on 2026-10-17, normal)
    ("rate_policy", "CN"): 75,                 # BIS CBPOL: early the following month
    ("unemployment", "*"): 75,
    # --- market series resampled monthly (the current month is stamped as soon as it has a day of data) ---
    ("oil_wti", "*"): 45, ("vix", "*"): 45, ("spread_hy", "*"): 45, ("dxy", "*"): 45,
    ("spread_ig", "*"): 75, ("term_spread", "*"): 75,                   # published as monthly series: the current month appears after it ends
    ("gold", "*"): 110, ("copper", "*"): 140,                           # IMF / LBMA monthly via FRED: 1-2 months
    # --- FX ---
    ("fx_usd_eur", "*"): 75, ("fx_usd_gbp", "*"): 75, ("fx_jpy_usd", "*"): 75, ("fx_cny_usd", "*"): 75,    # FRED monthly averages
    ("fx_eom_eur", "*"): 45, ("fx_d_eur", "*"): 14,                     # ECB end-of-month and daily reference rates (NAV conversion)
    # --- low frequency, stamped at the START of the period ---
    ("gdp_nom_eur", "*"): 270,                 # quarterly: stamp = quarter start; worst age ~ quarter + 70 days of lag + the next quarter
    ("debt_gdp_pct", "*"): 900, ("deficit_gdp_pct", "*"): 900,         # annual: stamp = 1 Jan of the year; the next year arrives in April, 2+ years later
}

# Regime inputs that are an AVERAGE of several legs: each leg is judged on its own, because one dead leg leaves the average looking alive.
COMPOSITE_INPUTS: dict = {
    "ipc_yoy_avg": (("ipc_index", "ES"), ("ipc_index", "EU")),
}


@dataclass(frozen=True)
class SeriesStatus:
    indicator: str
    geography: str
    newest: date
    age_days: int
    limit_days: int
    stale: bool
    limit_source: str          # "explicit" | "default"


def ttl_for(indicator: str, geography: str) -> tuple:
    """(limit_days, 'explicit'|'default') for a series: the (indicator, geography) entry, else (indicator, '*'), else DEFAULT_TTL_DAYS."""
    for key in ((indicator, geography), (indicator, "*")):
        if key in MACRO_TTL_DAYS:
            return MACRO_TTL_DAYS[key], "explicit"
    return DEFAULT_TTL_DAYS, "default"


def assess(latest: Mapping, today: date) -> list:
    """One SeriesStatus per (indicator, geography) in `latest` ({(indicator, geography): newest stamped date}); the most stale first.
    A stamp in the future (the current month is stamped on its first day) counts as age 0."""
    out = []
    for (ind, geo), newest in latest.items():
        limit, src = ttl_for(ind, geo)
        age = max((today - newest).days, 0)
        out.append(SeriesStatus(ind, geo, newest, age, limit, age > limit, src))
    return sorted(out, key=lambda s: (not s.stale, -(s.age_days - s.limit_days), s.indicator, s.geography))


def composite_legs(statuses: list) -> dict:
    """{composite input: {'legs': {'ES': 'OK'|'STALE'|'MISSING', ...}, 'state': 'OK'|'DEGRADED'|'BLIND'}}.

    DEGRADED = some legs alive and some not (the average silently becomes the live legs alone: ipc_yoy_avg == ES only);
    BLIND = no live leg. A leg absent from the table is MISSING and counts as not alive."""
    by_key = {(s.indicator, s.geography): s for s in statuses}
    out = {}
    for name, legs in COMPOSITE_INPUTS.items():
        state_by_leg = {}
        for ind, geo in legs:
            s = by_key.get((ind, geo))
            state_by_leg[geo] = "MISSING" if s is None else ("STALE" if s.stale else "OK")
        alive = [g for g, v in state_by_leg.items() if v == "OK"]
        state = "OK" if len(alive) == len(legs) else ("DEGRADED" if alive else "BLIND")
        out[name] = {"legs": state_by_leg, "state": state}
    return out


def attention_lines(statuses: list, composites: dict) -> list:
    """Sentences for the cycle report ([ATENCION] lines): stale series, degraded composite inputs, series judged by the default limit."""
    lines = []
    stale = [s for s in statuses if s.stale]
    if stale:
        lines.append(f"{len(stale)} series macro caducadas (edad > limite propio): " + "; ".join(
            f"{s.indicator}/{s.geography} {s.newest:%Y-%m} ({s.age_days} d > {s.limit_days})" for s in stale[:8])
            + (" ..." if len(stale) > 8 else "") + " -- scripts/audit/macro_freshness_check.py; buscar primero un cambio de proveedor (FND-0256)")
    for name, c in composites.items():
        if c["state"] != "OK":
            legs = ", ".join(f"{g}={v}" for g, v in c["legs"].items())
            lines.append(f"entrada de regimen {name} {c['state']}: {legs}"
                         + (" (la media usa solo las patas vivas)" if c["state"] == "DEGRADED" else " (sin ninguna pata viva)"))
    defaults = [s for s in statuses if s.limit_source == "default"]
    if defaults:
        lines.append(f"{len(defaults)} series sin limite de frescura propio (usan {DEFAULT_TTL_DAYS} d): "
                     + ", ".join(f"{s.indicator}/{s.geography}" for s in defaults[:6]) + " -- anadirlas a shared/macro_freshness.py")
    return lines
