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

The top of the module is pure (R-7); the loaders import the database helpers inside their functions.
"""
from __future__ import annotations

import math

VIEWS = ("eur", "loc")


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


def _manager_key(name) -> "str | None":
    key = " ".join(str(name).split()).upper() if name is not None else ""
    return key or None


def duplicate_pairs(holdings: list, returns: dict, *, min_corr: float, max_mad: float, min_months: int) -> list:
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
                corr, mad, n = similarity(series[a], series[b])
                if n >= min_months and corr is not None and corr >= min_corr and mad <= max_mad:
                    out.append((a, b, f"returns {view}: corr {corr:.4f}, mean |diff| {mad * 100:.3f} pp over {n} months, same manager"))
                    break
    return out


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


def check_scenario(conn, isins) -> "tuple[list | None, str | None]":
    """(pairs, problem) for the scenario's funds: the duplicate pairs, or (None, why) when the inputs could not be read (the gate then fails closed)."""
    isins = sorted(set(isins))
    if len(isins) < 2:
        return [], None
    from shared import config as cfg
    try:
        holdings = load_holdings(conn, isins)
        returns = load_returns(conn, isins, cfg.DUPLICATE_FUND_WINDOW_MONTHS)
        return duplicate_pairs(holdings, returns, min_corr=cfg.DUPLICATE_FUND_MIN_CORR, max_mad=cfg.DUPLICATE_FUND_MAX_MAD,
                               min_months=cfg.DUPLICATE_FUND_MIN_MONTHS), None
    except Exception as exc:                                     # noqa: BLE001 -- the gate fails closed on unreadable inputs
        conn.rollback()
        return None, f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
