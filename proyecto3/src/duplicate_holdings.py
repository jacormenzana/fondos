# proyecto3/src/duplicate_holdings.py
# -*- coding: utf-8 -*-
"""FND-0446: which holdings of a scenario are the SAME fund (two share classes of one sub-fund), for the HARD promotion gate GATE:DUPLICATE_FUND
(proyecto3/src/scenario_lifecycle.py::evaluate_promotion, control T-DUPLICATE-FUND).

Two holdings are one fund when:
  1. they share a fund_family_id (the family builder's key: classes of one sub-fund), or
  2. they come from the same management company and their monthly returns are near-identical over enough common months, in EITHER view:
       * EUR view (shared/eur_nav.py): two UNHEDGED classes of one portfolio earn the same in EUR whatever their class currency;
       * local view (each class in its own currency): a HEDGED class earns what its base class earns in the base currency, plus a carry difference.
     Two different portfolios match in neither view.
Rule 2 does not rest on fund_family_id, the key that split the EUR and USD classes of ABN US ESG (FND-0444); it names no fund (P#5). Thresholds
(shared/config.py DUPLICATE_FUND_*) from the read-only calibration of 2026-11-26 over the live universe (last 36 months): the ABN pair has EUR-view
correlation 0.998 and a mean absolute monthly difference of 0.18 pp, inside correlation >= 0.995 and difference <= 0.20 pp.

Volatility floor (DUPLICATE_FUND_MIN_VOL, 2026-11-27): a view where either series moves less than the floor a year cannot identify a sub-fund (two
different money-market or floating-rate funds of one manager match), so rule 2 does not use it. Rule 1 is unaffected.

The same rule serves the portfolio BUILDER (twins_of), the fallback for the split family key (FND-0444): the shared selection function skips a candidate
whose twin is already chosen.

family_collisions counts the split pairs of the whole universe for the closing check of FND-0444; it is the only reader of the allowlist of distinct sub-funds, which selection does not use.

The top of the module is pure (R-7); the loaders import the database helpers inside their functions.
"""
from __future__ import annotations

import math
import re
from datetime import date, timedelta

VIEWS = ("eur", "loc")
ALLOWLIST_MAX_DAYS = 180
_ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")
_TICKET = re.compile(r"FND-[0-9]{4}")
Q_UNIVERSE_HOLDINGS = ("SELECT isin, fund_family_id, management_company FROM fund_master "
                       "WHERE in_current_universe = 1 AND management_company IS NOT NULL AND btrim(management_company) <> ''")


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# pure
# ---------------------------------------------------------------------------------------------------------------------------------------------------
def similarity(x: dict, y: dict) -> "tuple[float | None, float | None, int]":
    """(Pearson correlation, mean absolute difference, number of common months) of two {month: return} series. Correlation is None when either
    series is constant over the common months or there are fewer than 2 of them."""
    common = [m for m in x if m in y and x[m] is not None and y[m] is not None]
    n = len(common)
    if n == 0:
        return None, None, 0
    a = [float(x[m]) for m in common]
    b = [float(y[m]) for m in common]
    mad = sum(abs(p - q) for p, q in zip(a, b)) / n
    if n < 2:
        return None, mad, n
    ma, mb = sum(a) / n, sum(b) / n
    sab = sum((p - ma) * (q - mb) for p, q in zip(a, b))
    saa = sum((p - ma) ** 2 for p in a)
    sbb = sum((q - mb) ** 2 for q in b)
    if saa <= 0 or sbb <= 0:
        return None, mad, n
    return sab / math.sqrt(saa * sbb), mad, n


def annual_vol(values: list) -> float:
    """Annualised standard deviation of monthly returns (0.0 for fewer than 2 values)."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1)) * math.sqrt(12)


def _manager_key(name) -> "str | None":
    key = " ".join(str(name).split()).upper() if name is not None else ""
    return key or None


def duplicate_pairs(holdings: list, returns: dict, *, min_corr: float, max_mad: float, min_months: int, min_vol: float = 0.0) -> list:
    """[(isin_a, isin_b, reason)] of the pairs of `holdings` that are one fund, isin_a < isin_b, sorted. `holdings` = [{isin, family, manager}];
    `returns` = {view: {isin: {month: return}}} for the views in VIEWS (a missing view or fund is simply not compared)."""
    by_isin = {h["isin"]: h for h in holdings}
    isins = sorted(by_isin)
    out = []
    for i, a in enumerate(isins):
        for b in isins[i + 1:]:
            fa, fb = by_isin[a].get("family"), by_isin[b].get("family")
            if fa and fa == fb:
                out.append((a, b, f"same fund_family_id {fa}"))
                continue
            ma, mb = _manager_key(by_isin[a].get("manager")), _manager_key(by_isin[b].get("manager"))
            if ma is None or ma != mb:
                continue
            for view in VIEWS:
                series = returns.get(view) or {}
                if a not in series or b not in series:
                    continue
                common = [m for m in series[a] if m in series[b]]
                if min_vol > 0 and min(annual_vol([series[a][m] for m in common]), annual_vol([series[b][m] for m in common])) < min_vol:
                    continue                                     # too flat to identify a sub-fund
                corr, mad, n = similarity(series[a], series[b])
                if n >= min_months and corr is not None and corr >= min_corr and mad <= max_mad:
                    out.append((a, b, f"returns {view}: corr {corr:.4f}, mean |diff| {mad * 100:.3f} pp over {n} months, same manager"))
                    break
    return out


def twins_from_pairs(pairs: list) -> dict:
    """{isin: frozenset of its twins} from duplicate_pairs output. Symmetric and NOT transitive: only the pairs the rule found."""
    out: dict = {}
    for a, b, _why in pairs:
        out.setdefault(a, set()).add(b)
        out.setdefault(b, set()).add(a)
    return {k: frozenset(v) for k, v in out.items()}


def _pairs(holdings: list, returns: dict) -> list:
    """duplicate_pairs with the parameters of shared/config.py (one definition for the gate and the builder)."""
    from shared import config as cfg
    return duplicate_pairs(holdings, returns, min_corr=cfg.DUPLICATE_FUND_MIN_CORR, max_mad=cfg.DUPLICATE_FUND_MAX_MAD,
                           min_months=cfg.DUPLICATE_FUND_MIN_MONTHS, min_vol=cfg.DUPLICATE_FUND_MIN_VOL)


def monthly_returns(rows) -> dict:
    """{isin: {'YYYY-MM': return}} from rows (isin, date, nav): the last NAV of each calendar month, then the change from the previous month present."""
    last: dict = {}
    for isin, d, nav in rows:
        if nav is None:
            continue
        key = str(d)[:7]
        cur = last.setdefault(isin, {}).get(key)
        if cur is None or str(d) >= cur[0]:
            last[isin][key] = (str(d), float(nav))
    out = {}
    for isin, months in last.items():
        keys = sorted(months)
        out[isin] = {k: months[k][1] / months[p][1] - 1.0 for p, k in zip(keys, keys[1:]) if months[p][1]}
    return out


def _couple_problems(e: dict) -> list:
    a, b = tuple(e.get("a") or ()), tuple(e.get("b") or ())
    out = []
    if not _TICKET.fullmatch(str(e.get("ap", ""))):
        out.append(f"'ap' {e.get('ap')!r} is not a ticket id")
    if not a or not b:
        out.append("each side needs at least one fund")
    if set(a) & set(b):
        out.append("a fund is on both sides")
    if any(not _ISIN.fullmatch(str(i)) for i in (*a, *b)):
        out.append("an entry is not an ISIN")
    if len(str(e.get("evidence", "")).strip()) < 20:
        out.append("'evidence' must say what proves the two are different sub-funds")
    try:
        since, until = date.fromisoformat(str(e.get("since"))), date.fromisoformat(str(e.get("until")))
    except ValueError:
        return out + ["'since' and 'until' must be ISO dates"]
    if until <= since:
        out.append("'until' must be after 'since'")
    elif until - since > timedelta(days=ALLOWLIST_MAX_DAYS):
        out.append(f"a couple lasts at most {ALLOWLIST_MAX_DAYS} days")
    return out


def allowlist_problems(allowlist) -> list:
    """What is wrong with the allowlist of distinct sub-funds (empty = fine)."""
    return [f"couple {n}: {why}" for n, e in enumerate(allowlist, 1) for why in _couple_problems(e)]


def allowed_pairs(allowlist, on: date) -> set:
    """{frozenset((isin, isin))} of the well-formed couples in force on `on` (since <= on <= until): every fund of side a with every fund of side b."""
    out = set()
    for e in allowlist:
        if _couple_problems(e) or not date.fromisoformat(e["since"]) <= on <= date.fromisoformat(e["until"]):
            continue
        out |= {frozenset((a, b)) for a in e["a"] for b in e["b"]}
    return out


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# database
# ---------------------------------------------------------------------------------------------------------------------------------------------------
def load_holdings(conn, isins: list) -> list:
    rows = conn.execute("SELECT isin, fund_family_id, management_company FROM fund_master WHERE isin = ANY(%s)", (list(isins),)).fetchall()
    return [{"isin": r[0], "family": r[1], "manager": r[2]} for r in rows]


def load_returns(conn, isins: list, window_months: int) -> dict:
    """{'loc': ..., 'eur': ...} monthly returns of `isins` over the last `window_months` (+1 for the first change) of fund_nav_monthly."""
    import pandas as pd
    from shared.eur_nav import apply_eur_view_frame
    rows = conn.execute(
        "SELECT isin, date, nav FROM fund_nav_monthly WHERE isin = ANY(%s) AND date >= (CURRENT_DATE - make_interval(months => %s))",
        (list(isins), int(window_months) + 1)).fetchall()
    rows = [(r[0], r[1], r[2]) for r in rows]
    frame = pd.DataFrame(rows, columns=["isin", "date", "nav"])
    if not frame.empty:
        frame["nav"] = frame["nav"].astype(float)
    eur = apply_eur_view_frame(conn, frame.copy()) if not frame.empty else frame
    return {"loc": monthly_returns(rows), "eur": monthly_returns(eur[["isin", "date", "nav"]].itertuples(index=False, name=None))}


def family_collisions(conn, on: date) -> "dict | None":
    """{'split': n, 'allowed': n, 'examples': [(isin, isin)]} over the active universe: pairs of one manager that rule 2 calls one fund while their fund_family_id differ or are missing; `allowed` are
    the pairs of an allowlist couple in force on `on` (not counted). None when it cannot be judged: the EUR view is off (a EUR and a USD class would never match) or no fund has enough months."""
    from shared import config as cfg
    if not cfg.EUR_NAV_CONVERSION_ENABLED:
        return None
    groups: dict = {}
    for isin, family, manager in conn.execute(Q_UNIVERSE_HOLDINGS).fetchall():
        groups.setdefault(_manager_key(manager), []).append({"isin": isin, "family": family, "manager": manager})
    groups = {k: v for k, v in groups.items() if len(v) > 1 and (len({h["family"] for h in v}) > 1 or any(not h["family"] for h in v))}
    isins = sorted(h["isin"] for v in groups.values() for h in v)
    returns = load_returns(conn, isins, cfg.DUPLICATE_FUND_WINDOW_MONTHS) if isins else {}
    if not any(len(series) >= cfg.DUPLICATE_FUND_MIN_MONTHS for series in (returns.get("loc") or {}).values()):
        return None
    allowed = allowed_pairs(cfg.FAMILY_COLLISION_ALLOWLIST, on)
    split, excused = [], 0
    for holdings in groups.values():
        family = {h["isin"]: h["family"] for h in holdings}
        for a, b, _why in _pairs(holdings, returns):
            if family[a] and family[a] == family[b]:
                continue
            if frozenset((a, b)) in allowed:
                excused += 1
            else:
                split.append((a, b))
    return {"split": len(split), "allowed": excused, "examples": sorted(split)[:5]}


def check_scenario(conn, isins) -> "tuple[list | None, str | None]":
    """(pairs, problem) for the scenario's funds: the duplicate pairs, or (None, why) when the inputs could not be read (the gate then fails closed)."""
    isins = sorted(set(isins))
    if len(isins) < 2:
        return [], None
    from shared import config as cfg
    try:
        holdings = load_holdings(conn, isins)
        returns = load_returns(conn, isins, cfg.DUPLICATE_FUND_WINDOW_MONTHS)
        return _pairs(holdings, returns), None
    except Exception as exc:                                     # noqa: BLE001 -- the gate fails closed on unreadable inputs
        conn.rollback()
        return None, f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"


def twins_of(conn, isins) -> dict:
    """{isin: frozenset(twins)} among `isins`, for the builder. Returns are read only for managers with two or more of them (rule 2 needs one manager).
    RAISES when an input cannot be read: a build must not silently select without the rule."""
    isins = sorted(set(isins))
    if len(isins) < 2:
        return {}
    from shared import config as cfg
    holdings = load_holdings(conn, isins)
    count: dict = {}
    for h in holdings:
        key = _manager_key(h["manager"])
        if key is not None:
            count[key] = count.get(key, 0) + 1
    shared_mgr = sorted(h["isin"] for h in holdings if count.get(_manager_key(h["manager"]), 0) > 1)
    returns = load_returns(conn, shared_mgr, cfg.DUPLICATE_FUND_WINDOW_MONTHS) if shared_mgr else {}
    return twins_from_pairs(_pairs(holdings, returns))
