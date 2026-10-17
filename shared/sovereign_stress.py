# shared/sovereign_stress.py
# -*- coding: utf-8 -*-
"""
Sovereign-stress rules and their evidence run (FND-0294).

The regime model reads only market-wide stress (HY spread, VIX); a sovereign-debt event would show there late. These are the candidate
early-warning rules for the two kinds of sovereign stress the data can show -- euro-area periphery/core spread stress and global
government-yield shocks -- plus the scoring that says how they would have behaved on the known episodes.

HOW THEY WERE CHOSEN (2026-10-17, ticket FND-0294): thresholds are round numbers DECLARED BEFORE scoring and are NOT optimised; a change to one is a
deliberate edit of RULE_PARAMS (a test pins them). The first scoring run covered 1991-2026 and was misleading (pre-euro years and Greece alone gave
39% false alarms; a persistent state rule "hit" an episode only because it was already on), so scoring is restricted to the euro era and every rule is
judged separately, with its false-alarm months counted outside ALL labelled episodes. The episodes are labelled from history, not from the data.

Pure module: no DB, no network (R-7). scripts/audit/sovereign_stress_backtest.py fetches the public data and calls it. MONITOR-ONLY: nothing in
the pipeline reads this yet, and no portfolio weight may depend on it before the out-of-sample validation (FND-0193).
"""

from __future__ import annotations

from typing import Callable, Mapping

# Month keys are 'YYYY-MM'. A series is {month: yield in percent}. 10y government yields (ECB IRS for the euro area, FRED/OECD for US, GB, JP).
EURO_ERA_START = "1999-01"
BASE = "DE"                                             # the euro-area benchmark for spreads
PERIPHERY = ("IT", "ES")                                # PT and GR are excluded on purpose: small issuers, and Greece alone keeps a >2pp spread for a decade
CORE = "FR"
SHOCK_COUNTRIES = ("DE", "US", "GB", "JP")

RULE_PARAMS: dict = {
    "periphery_level_pp": 2.0,                          # R1: IT or ES spread over DE at/above this (state)
    "periphery_widening_pp": 0.5, "widening_months": 3,  # R2: IT or ES spread widened by this in 3 months (event)
    "core_percentile": 90.0, "core_percentile_years": 10, "core_widening_pp": 0.2, "core_min_months": 60,   # R3: FR-DE
    "shock_strict_pp": 1.0, "shock_pp": 0.75, "shock_months": 3,                                            # R4 / R5: any 10y rises this in 3 months
}

# Labelled from history (not from the data); windows are scored with +-3 months of slack.
EPISODES: dict = {
    "euro sovereign crisis": ("2010-05", "2012-09"),
    "taper tantrum": ("2013-05", "2013-09"),
    "Italy BTP spike": ("2018-05", "2018-06"),
    "Covid dash for cash": ("2020-03", "2020-04"),
    "UK gilt / global yield surge": ("2022-09", "2022-10"),
    "US yield spike (term premium)": ("2023-08", "2023-10"),
}


def back(month: str, k: int) -> str:
    """The month k months before `month` ('YYYY-MM')."""
    n = int(month[:4]) * 12 + int(month[5:7]) - 1 - k
    return f"{n // 12:04d}-{n % 12 + 1:02d}"


def within(month: str, start: str, end: str, slack: int = 3) -> bool:
    """Is `month` inside [start - slack, end + slack] months?"""
    return back(start, slack) <= month <= back(end, -slack)


def percentile_rank(month: str, series: Mapping[str, float], years: int) -> float:
    """Share (0-100) of the last `years` years of `series` up to and including `month` that is <= its value at `month`."""
    hist = [series[k] for k in sorted(series) if k <= month][-years * 12:]
    return 100.0 * sum(x <= series[month] for x in hist) / len(hist)


def _spread(y: Mapping, country: str, month: str):
    s, b = y.get(country, {}), y.get(BASE, {})
    return s[month] - b[month] if month in s and month in b else None


def _spread_change(y: Mapping, country: str, month: str, k: int):
    now, then = _spread(y, country, month), _spread(y, country, back(month, k))
    return None if now is None or then is None else now - then


def _shock(y: Mapping, country: str, month: str, k: int):
    s = y.get(country, {})
    return s[month] - s[back(month, k)] if month in s and back(month, k) in s else None


def build_rules(y: Mapping[str, Mapping[str, float]], p: Mapping | None = None) -> dict:
    """{rule id: (label, kind, predicate(month) -> bool)} over the yield series `y` ({country: {month: percent}}). A predicate is False,
    never an error, for a month where its inputs are missing."""
    p = RULE_PARAMS if p is None else p
    k_w, k_s = p["widening_months"], p["shock_months"]
    core = {m: _spread(y, CORE, m) for m in y.get(CORE, {}) if _spread(y, CORE, m) is not None}

    def r1(m):
        xs = [_spread(y, c, m) for c in PERIPHERY]
        return any(x is not None and x >= p["periphery_level_pp"] for x in xs)

    def r2(m):
        xs = [_spread_change(y, c, m, k_w) for c in PERIPHERY]
        return any(x is not None and x >= p["periphery_widening_pp"] for x in xs)

    def r3(m):
        if m not in core or back(m, k_w) not in core or len([k for k in core if k <= m]) < p["core_min_months"]:
            return False
        return (percentile_rank(m, core, p["core_percentile_years"]) >= p["core_percentile"]
                and core[m] - core[back(m, k_w)] >= p["core_widening_pp"])

    def shock(threshold):
        def rule(m):
            return any((x := _shock(y, c, m, k_s)) is not None and x >= threshold for c in SHOCK_COUNTRIES)
        return rule

    return {
        "R1": ("IT or ES spread over DE >= level (state)", "state", r1),
        "R2": ("IT or ES spread widened in 3 months (event)", "event", r2),
        "R3": ("FR-DE spread in its top decile and widening (WATCH: no validated precedent)", "event", r3),
        "R4": ("any 10y yield up >= 1.0pp in 3 months", "event", shock(p["shock_strict_pp"])),
        "R5": ("any 10y yield up >= 0.75pp in 3 months", "event", shock(p["shock_pp"])),
    }


def flagged_months(rule: Callable[[str], bool], months: list) -> list:
    return [m for m in months if rule(m)]


def score(flags: set, months: list, episodes: Mapping | None = None, slack: int = 3) -> dict:
    """How one rule behaved: flagged months inside each episode window, and the false-alarm months (flagged, inside NO episode window).
    `months` is the evaluation sample (the euro era); flags outside it are ignored."""
    eps = EPISODES if episodes is None else episodes
    sample = set(months)
    fl = {m for m in flags if m in sample}
    per = {name: sum(1 for m in fl if within(m, a, b, slack)) for name, (a, b) in eps.items()}
    false = sorted(m for m in fl if not any(within(m, a, b, slack) for a, b in eps.values()))
    return {"per_episode": per, "detected": sum(1 for v in per.values() if v), "episodes": len(eps), "false_months": false,
            "false_pct": 100.0 * len(false) / len(months) if months else 0.0, "flagged": len(fl)}


def evaluate(y: Mapping[str, Mapping[str, float]], p: Mapping | None = None, start: str = EURO_ERA_START) -> dict:
    """Run every rule over the months where the benchmark and the main series exist, from `start`. Returns
    {'months': [...], 'rules': {id: {'label', 'kind', 'score', 'now': bool}}}. Nothing is tuned here."""
    needed = [BASE, CORE, *PERIPHERY, *SHOCK_COUNTRIES]
    months = sorted(set.intersection(*(set(y.get(c, {})) for c in needed if c in y))) if all(c in y for c in needed) else []
    months = [m for m in months if m >= start]
    out = {}
    for rid, (label, kind, fn) in build_rules(y, p).items():
        flags = set(flagged_months(fn, months))
        out[rid] = {"label": label, "kind": kind, "score": score(flags, months), "now": bool(months) and months[-1] in flags}
    return {"months": months, "rules": out}
