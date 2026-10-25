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

# How the live feed stores these yields in series_macro (FND-0294 step 2): one indicator, geography = the ISO code used above. The loaders
# (macro_discovery) and the monitor both derive their country lists from here, so a country added to a rule cannot silently lack a feed.
YIELD_INDICATOR = "yield_10y_gov"
EURO_YIELD_COUNTRIES = tuple(dict.fromkeys((BASE,) + PERIPHERY + (CORE,)))                 # ECB IRS 10y convergence-criterion yields
NON_EURO_YIELD_COUNTRIES = tuple(c for c in SHOCK_COUNTRIES if c not in EURO_YIELD_COUNTRIES)   # FRED / OECD IRLTLT01 10y yields

RULE_PARAMS: dict = {
    "periphery_level_pp": 2.0,                          # R1: IT or ES spread over DE at/above this (state)
    "periphery_widening_pp": 0.5, "widening_months": 3,  # R2: IT or ES spread widened by this in 3 months (event)
    "core_percentile": 90.0, "core_percentile_years": 10, "core_widening_pp": 0.2, "core_min_months": 60,   # R3: FR-DE
    "shock_strict_pp": 1.0, "shock_pp": 0.75, "shock_months": 3,                                            # R4 / R5: any 10y rises this in 3 months
}

# Monitor v2 (FND-0317): reporting aids only, NOT rules. A feed one month behind is normal publication lag (both feeds carried the newest month ~24 days
# after its end), so the lag warning starts at two. Reachability needs a history long enough to have seen a stress episode (20 years); a shorter one is
# "not assessed" rather than a false alarm.
MONITOR_LAG_WARN_MONTHS = 2
REACHABILITY_MIN_MONTHS = 240

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


# ---------------------------------------------------------------------------------------------------- live monitor (FND-0294 step 5)
# MONITOR-ONLY and informational: the lines below go to the cycle report ([ATENCION], owner decision 2026-10-24) and nothing else reads them.
# No threshold lives here: the rules are build_rules over RULE_PARAMS, exactly the ones the evidence run scored.
REQUIRED_COUNTRIES = tuple(dict.fromkeys((BASE,) + PERIPHERY + (CORE,) + SHOCK_COUNTRIES))


def yields_from_rows(rows) -> dict:
    """[(date, geography, value)] of series_macro yield_10y_gov (month-start stamps) -> {country: {'YYYY-MM': percent}}."""
    y: dict = {}
    for d, geo, value in rows:
        if value is not None:
            y.setdefault(geo, {})[str(d)[:7]] = float(value)
    return y


def _month_index(month: str) -> int:
    return int(month[:4]) * 12 + int(month[5:7]) - 1


def headroom(y: Mapping[str, Mapping[str, float]], month: str, p: Mapping | None = None) -> dict:
    """How far, in percentage points, each rule is from firing at `month`: {rule id: pp still missing}.

    0.0 exactly when the rule is ON; None when it cannot be expressed in pp (inputs missing, or R3 whose percentile leg is off: widening alone cannot
    switch it on). The distance is measured on the rule's own metric against the SAME pinned threshold, so it adds no threshold. Reporting only."""
    p = RULE_PARAMS if p is None else p
    k_w, k_s = p["widening_months"], p["shock_months"]

    def gap(threshold, values):
        vals = [v for v in values if v is not None]
        return None if not vals else round(max(0.0, threshold - max(vals)), 4)

    out = {
        "R1": gap(p["periphery_level_pp"], [_spread(y, c, month) for c in PERIPHERY]),
        "R2": gap(p["periphery_widening_pp"], [_spread_change(y, c, month, k_w) for c in PERIPHERY]),
        "R4": gap(p["shock_strict_pp"], [_shock(y, c, month, k_s) for c in SHOCK_COUNTRIES]),
        "R5": gap(p["shock_pp"], [_shock(y, c, month, k_s) for c in SHOCK_COUNTRIES]),
        "R3": None,
    }
    core = {m: _spread(y, CORE, m) for m in y.get(CORE, {}) if _spread(y, CORE, m) is not None}
    if month in core and back(month, k_w) in core and len([m for m in core if m <= month]) >= p["core_min_months"]:
        if percentile_rank(month, core, p["core_percentile_years"]) >= p["core_percentile"]:
            out["R3"] = gap(p["core_widening_pp"], [core[month] - core[back(month, k_w)]])
    return out


def monitor(y: Mapping[str, Mapping[str, float]], p: Mapping | None = None) -> dict:
    """State of every rule in the newest month for which ALL required countries have a yield.

    {'state': 'OK'|'INCOMPLETE'|'NO_DATA', 'month', 'rules': {id: bool}, 'labels': {id: text}, 'readings': {...}, 'missing': [countries],
     'headroom': {id: pp}, 'lagging': {country: months behind the newest feed}, 'reach_assessed': bool, 'reach_months': int, 'unreachable': [ids]}.
    Missing data is a state of its own, never a silent 'all clear': with no series, or without a country a rule needs, nothing is evaluated.
    The v2 fields (FND-0317) are reporting aids: they read the rules, they never change them."""
    missing = [c for c in REQUIRED_COUNTRIES if not y.get(c)]
    if missing:
        return {"state": "NO_DATA" if len(missing) == len(REQUIRED_COUNTRIES) else "INCOMPLETE", "month": None, "missing": missing,
                "rules": {}, "labels": {}, "readings": {}, "headroom": {}, "lagging": {}, "reach_assessed": False, "reach_months": 0,
                "unreachable": []}
    month = min(max(y[c]) for c in REQUIRED_COUNTRIES)
    newest = max(max(y[c]) for c in REQUIRED_COUNTRIES)
    lagging = {c: _month_index(newest) - _month_index(max(y[c])) for c in REQUIRED_COUNTRIES if max(y[c]) < newest}
    rules = build_rules(y, p)
    k = (RULE_PARAMS if p is None else p)["shock_months"]
    shocks = [x for c in SHOCK_COUNTRIES if (x := _shock(y, c, month, k)) is not None]
    history = evaluate(y, p)
    assessed = len(history["months"]) >= REACHABILITY_MIN_MONTHS
    unreachable = [rid for rid, r in history["rules"].items() if r["score"]["flagged"] == 0] if assessed else []
    return {"state": "OK", "month": month, "missing": [],
            "rules": {rid: bool(pred(month)) for rid, (_, _, pred) in rules.items()},
            "labels": {rid: label for rid, (label, _, _) in rules.items()},
            "readings": {"IT-DE": _spread(y, "IT", month), "ES-DE": _spread(y, "ES", month), "FR-DE": _spread(y, CORE, month),
                         "max_shock_3m": max(shocks) if shocks else None},
            "headroom": headroom(y, month, p), "lagging": lagging, "newest_month": newest,
            "reach_assessed": assessed, "reach_months": len(history["months"]), "unreachable": unreachable}


# ---------------------------------------------------------------- context readings (FND-0317)
# READINGS ONLY: no threshold, no rule, never an [ATENCION] item. They give the yield moves a context from series already in series_macro.
CONTEXT_INFLATION = {"DE": "EU", "US": "US", "JP": "JP"}    # 10y country -> ipc_index geography (the Bund is read against euro-area HICP)
CONTEXT_MAX_CPI_LAG_MONTHS = 3                              # a longer CPI gap is "n/d", never a stale number presented as current


def ipc_from_rows(rows) -> dict:
    """[(date, geography, value)] of series_macro ipc_index -> {geography: {'YYYY-MM': index}} (nulls dropped)."""
    out: dict = {}
    for d, geo, value in rows:
        if value is not None:
            out.setdefault(geo, {})[str(d)[:7]] = float(value)
    return out


def debt_from_rows(rows) -> dict:
    """[(date, geography, value)] of series_macro debt_gdp_pct (annual) -> {geography: {'YYYY': percent of GDP}}."""
    out: dict = {}
    for d, geo, value in rows:
        if value is not None:
            out.setdefault(geo, {})[str(d)[:4]] = float(value)
    return out


def _yoy_pct(series: Mapping[str, float], month: str):
    a, b = series.get(month), series.get(back(month, 12))
    return None if a is None or not b else 100.0 * (a / b - 1.0)


def context_readings(y: Mapping, ipc: Mapping, debt: Mapping, month: str) -> dict:
    """{'month', 'real_yield': {cc: {'month','nominal','inflation','real'} | None}, 'debt_gdp': {geo: (year, pct)}}.

    Real yield = nominal 10y - 12-month CPI/HICP inflation, taken at the latest month <= `month` (at most CONTEXT_MAX_CPI_LAG_MONTHS back) where both
    exist, and the month used is reported. Debt/GDP is the latest published year. Pure; no rule reads this."""
    real: dict = {}
    for cc, geo in CONTEXT_INFLATION.items():
        real[cc] = None
        for k in range(CONTEXT_MAX_CPI_LAG_MONTHS + 1):
            t = back(month, k)
            infl, nominal = _yoy_pct(ipc.get(geo, {}), t), y.get(cc, {}).get(t)
            if infl is not None and nominal is not None:
                real[cc] = {"month": t, "nominal": nominal, "inflation": infl, "real": nominal - infl}
                break
    return {"month": month, "real_yield": real, "debt_gdp": {geo: (max(s), s[max(s)]) for geo, s in debt.items() if s}}


def context_line(ctx: Mapping) -> str:
    """One report line for the context readings (Spanish like the rest of the report)."""
    parts = []
    for cc in CONTEXT_INFLATION:
        r = ctx["real_yield"].get(cc)
        parts.append(f"{cc} n/d" if r is None else
                     f"{cc} {r['real']:+.2f}% (10y {r['nominal']:.2f} - IPC {r['inflation']:.1f}%, {r['month']})")
    debt = ", ".join(f"{geo} {pct:.1f}% ({yr})" for geo, (yr, pct) in sorted(ctx["debt_gdp"].items())) or "n/d"
    return f"Contexto soberano (FND-0317, solo lectura, sin umbrales) {ctx['month']}: rendimiento real 10y {'; '.join(parts)}; deuda/PIB {debt}"


def _pp(x) -> str:
    return "n/d" if x is None else f"{x:.2f}pp"


def status_line(res: Mapping) -> str:
    """One line for the report body: the month and every rule on/off with the readings behind them."""
    if res.get("state") != "OK":
        return f"Estres soberano (FND-0294, solo monitor): {res.get('state')}"
    r = res["readings"]
    on = " ".join(f"{rid}={'ON' if v else 'off'}" for rid, v in res["rules"].items())
    room = " ".join(f"{rid} +{h:.2f}pp" for rid, h in (res.get("headroom") or {}).items() if h is not None and h > 0 and not res["rules"].get(rid))
    margin = f"; margen hasta activarse: {room}" if room else ""
    return (f"Estres soberano (FND-0294, solo monitor) {res['month']}: {on}; IT-DE {_pp(r['IT-DE'])} ES-DE {_pp(r['ES-DE'])} "
            f"FR-DE {_pp(r['FR-DE'])}; mayor subida 10y en 3 meses {_pp(r['max_shock_3m'])}{margin}")


def attention_lines(res: Mapping) -> list:
    """[ATENCION] sentences: missing data, and every rule that is on (R3 as WATCH: it has no validated precedent, 1/6 episodes)."""
    tail = "Solo monitor: ninguna ponderacion ni puerta depende de esto (FND-0294, hasta la validacion FND-0193)"
    state = res.get("state")
    if state == "NO_DATA":
        return ["Estres soberano (FND-0294): no hay series yield_10y_gov en series_macro; cargarlas con macro_discovery --source bce y "
                "--source fred. El monitor no evalua nada hasta entonces"]
    if state == "INCOMPLETE":
        return [f"Estres soberano (FND-0294): faltan los rendimientos de {', '.join(res['missing'])}: el monitor no puede evaluar las reglas"]
    out = []
    r = res["readings"]
    for rid, on in res["rules"].items():
        if not on:
            continue
        if rid == "R3":
            out.append(f"VIGILANCIA estres soberano R3 ({res['month']}): FR-DE {_pp(r['FR-DE'])} en su decil superior de 10 anos y ampliandose; "
                       f"sin precedente validado (1 de 6 episodios del backtest). {tail}")
        else:
            out.append(f"ESTRES SOBERANO {rid} activa ({res['month']}): {res['labels'][rid]}. IT-DE {_pp(r['IT-DE'])}, ES-DE {_pp(r['ES-DE'])}, "
                       f"mayor subida 10y en 3 meses {_pp(r['max_shock_3m'])}. {tail}")
    late = {c: n for c, n in (res.get("lagging") or {}).items() if n >= MONITOR_LAG_WARN_MONTHS}
    if late:
        who = ", ".join(f"{c} ({n} meses)" for c, n in sorted(late.items()))
        out.append(f"Estres soberano (FND-0294): el mes de evaluacion {res['month']} lo retiene la fuente de {who}; el dato mas reciente es "
                   f"{res.get('newest_month')}. Revisar la carga de esa serie (macro_discovery) antes de fiarse de las reglas")
    if res.get("unreachable"):
        out.append(f"Estres soberano (FND-0294): las reglas {', '.join(res['unreachable'])} nunca se activaron en {res['reach_months']} meses de "
                   f"historia: su umbral podria ser inalcanzable con estos datos. Solo informativo; no se cambia ningun umbral sin una nueva "
                   f"corrida de evidencia")
    return out
