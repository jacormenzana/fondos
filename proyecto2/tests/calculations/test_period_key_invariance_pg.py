# proyecto2/tests/calculations/test_period_key_invariance_pg.py
# -*- coding: utf-8 -*-
"""
R-9 invariance contract: an indicator family must give the SAME answer whether its NAV is stamped on the last BUSINESS day of
each month or on the calendar MONTH-END. The months are the same months and the NAV values are the same values; only the label
differs. A family whose output moves is reading the label instead of the period.

This is the hermetic, per-family counterpart of the 2026-10-18 reliability study
(`out/audit/p2_reliability_20261018/REPORT.md`), which measured the same property on 257 real funds through `run_pipeline.run`.
The study is the evidence; this file is the gate: it runs in the hermetic Postgres suite with no credentials, on a toy database
of 20 funds, and it fails the build when a family that is invariant today stops being invariant.

Macro and the rolling time series were NOT invariant until FND-0300 (NAV labelled at the reader, `NAV_MONTH_END_ALIGN_ENABLED`):
`compute_macro_sensitivity` inner-joined RAW NAV dates to the month-end factor index and the `DateOffset(months=N)` cutoff gave a
window of 12 or 13 returns by stamp. Their tests were `xfail(strict=True)`, the open-defect record in executable form, and the
FND-0300 commit removed the markers. **Never silence a family by editing the assertion.**

Tolerances follow what the study measured, not what would be convenient:
  * exact families are compared at rtol 1e-9;
  * `capture` at 1% relative -- 76% of its values differ at the 1e-5 level because the EUR-view FX rate is read on the NAV's own
    DAY, and a daily rate legitimately differs between the 27th and the 28th (R-9 rule 8: the daily series keeps its dates);
  * `persistence` at the family level only. The study measured 30% of individual values moving (Spearman 0.988, median change 0,
    4 of 162 funds crossing the 0.6 bonus) -- boundary noise from the DateOffset window walk (removed by FND-0300's calendar-month windows), so this file
    pins the aggregate and does not pretend the per-value figure is clean.

R-7: no `run_pipeline` / `core.io` imports -- every family is driven through its own entry point.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_P2 = Path(__file__).resolve().parent.parent.parent
_REPO = _P2.parent
for _p in (str(_P2), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared import config, eur_nav                                                              # noqa: E402
from shared.period_key import to_period_key                                                     # noqa: E402
from src.calculations import (capture_ratios, currency_factor, deflation, macro_sensitivity,    # noqa: E402
                              momentum, peer_panel, persistence, regime_returns, risk_metrics,
                              rolling_stats)

NATURE = "Mixtos"
START, END = "2014-01-31", "2026-09-30"
ROLLING = {"rolling_1y": 12, "rolling_3y": 36}

#: Which of the toy funds report on the last business day. The live database is 28.7% off the month-end (39.6% of the 2026
#: rows), and the study's stratified sample was 206 weekday / 51 month-end; a majority-weekday panel is the harder case.
WEEKDAY_FUNDS = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}


def _month_ends(start=START, end=END):
    return list(pd.date_range(start, end, freq="ME"))


def _stamp(month_end: pd.Timestamp, weekday: bool) -> pd.Timestamp:
    """The date this fund would carry for that month: the last business day, or the calendar month-end itself."""
    if not weekday:
        return month_end
    d = month_end
    while d.weekday() >= 5:                                    # Saturday / Sunday -> walk back to Friday
        d -= pd.Timedelta(days=1)
    return d


def _build(conn, snapped: bool, seed: int = 11):
    """Build the toy database. `snapped=True` writes every NAV on its calendar month-end; otherwise the weekday funds keep
    their last-business-day stamp. The NAV VALUES and the set of months are identical in both builds -- that is the whole
    experiment: if a result moves, only the label moved."""
    rng = np.random.RandomState(seed)
    for t in ("fund_master", "series_macro", "series_inflation", "fund_nav_monthly", "fund_metrics"):
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.execute("CREATE TABLE fund_master (isin text PRIMARY KEY, fund_nature text, fund_currency varchar(3))")
    conn.execute("CREATE TABLE series_macro (date date, indicator text, geography text, value double precision, unit text, "
                 "source text, PRIMARY KEY (date, indicator, geography))")
    conn.execute("CREATE TABLE series_inflation (date date, ipc_index double precision, PRIMARY KEY (date))")
    conn.execute("CREATE TABLE fund_nav_monthly (isin text, date date, nav double precision, nav_currency varchar(3), "
                 "PRIMARY KEY (isin, date))")
    conn.execute("CREATE TABLE fund_metrics (isin text, metric text, horizon text, real_flag int, value double precision, "
                 "PRIMARY KEY (isin, metric, horizon, real_flag))")

    months = _month_ends()
    funds, nav_rows, master, metric_rows = [], [], [], []
    for i in range(20):
        isin = f"IV{i:02d}"
        ccy = "USD" if i % 5 == 1 else "EUR"
        weekday = i in WEEKDAY_FUNDS
        funds.append((isin, weekday))
        master.append((isin, NATURE, ccy))
        level = 100.0 * (1 + rng.rand())
        for k, me in enumerate(months):
            level *= 1 + rng.normal(0.004, 0.035)
            if i % 7 == 3 and k % 13 == 6:
                continue                                        # a genuinely missing month, in both builds alike
            d = me if snapped else _stamp(me, weekday)
            nav_rows.append((isin, d.date(), float(level), None))
        # stored peer scalars momentum reads back; identical in both builds (they are not NAV-dated)
        for horizon in ("rolling_1y", "rolling_3y"):
            metric_rows.append((isin, "return_ann", horizon, 0, float(rng.normal(0.05, 0.03))))

    with conn.cursor() as cur:
        cur.executemany("INSERT INTO fund_master VALUES (%s,%s,%s)", master)
        cur.executemany("INSERT INTO fund_nav_monthly VALUES (%s,%s,%s,%s)", nav_rows)
        cur.executemany("INSERT INTO fund_metrics VALUES (%s,%s,%s,%s,%s)", metric_rows)
        # CPI on the calendar month-end, as series_inflation stores it (100% month-start in series_macro's sibling table;
        # db_readers.load_ipc hands deflate_nav month-end dates -- FND-0241).
        cur.executemany("INSERT INTO series_inflation VALUES (%s,%s)",
                        [(me.date(), 100.0 * (1.02 ** (k / 12))) for k, me in enumerate(months)])
        # Macro factors on the MONTH-START stamp series_macro really uses for the OLS factor set.
        macro = []
        for k, me in enumerate(months):
            ms = me.replace(day=1).date()
            macro += [(ms, "rate_deposit", "EU", float(rng.normal(0.01, 0.002)), "pct", "BCE"),
                      (ms, "m3_yoy", "EU", float(rng.normal(4.0, 1.0)), "pct", "BCE"),
                      (ms, "ipc_index", "ES", 100.0 * (1.02 ** (k / 12)), "idx", "INE"),
                      (ms, "ipc_index", "EU", 100.0 * (1.019 ** (k / 12)), "idx", "EUROSTAT"),
                      (ms, "oil_wti", "GLOBAL", float(60 + rng.normal(0, 8)), "usd", "FRED"),
                      (ms, "vix", "GLOBAL", float(18 + rng.normal(0, 4)), "idx", "FRED"),
                      # the MONTHLY cross the currency factor reads (currency_factor.load_fx_eur_divisa), month-start
                      # stamped exactly as series_macro holds it live
                      (ms, "fx_usd_eur", "GLOBAL", float(1.10 * (1 + 0.004 * k)), "usd_per_eur", "BCE")]
        cur.executemany("INSERT INTO series_macro VALUES (%s,%s,%s,%s,%s,%s)", macro)
        days = pd.bdate_range("2013-12-01", "2026-09-30")
        cur.executemany("INSERT INTO series_macro VALUES (%s,%s,%s,%s,'ccy_per_eur','BCE')",
                        [(d.date(), config.EUR_FX_DAILY_INDICATOR, "USD", 1.10 * (1 + 0.0003 * k))
                         for k, d in enumerate(days)])
    conn.commit()
    eur_nav.reset_cache()
    peer_panel.reset()
    return [f[0] for f in funds]


@pytest.fixture
def both(pg_session_conn, pg_conn_module_schema):
    """Run a callable against the raw-stamped database and against the snapped one, and hand back the two results."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")

    def run(fn):
        out = []
        for snapped in (False, True):
            isins = _build(conn, snapped=snapped)
            peer_panel.reset()
            eur_nav.reset_cache()
            out.append(fn(conn, isins))
        return out[0], out[1]

    yield run
    peer_panel.reset()
    eur_nav.reset_cache()


def _nav(conn, isin):
    """The NAV a calculation receives in production: the stored rows, then the reader's month-end labelling step
    (`db_readers.load_nav` applies `align_nav_long` after the EUR view; identity while NAV_MONTH_END_ALIGN_ENABLED is off).
    Without it the macro and rolling families would be fed raw stamps, which production no longer does (FND-0300)."""
    from shared.nav_month_end import align_nav_long
    rows = conn.execute("SELECT date, nav FROM fund_nav_monthly WHERE isin = %s ORDER BY date", (isin,)).fetchall()
    return align_nav_long(pd.DataFrame(rows, columns=["date", "nav"]).assign(date=lambda d: pd.to_datetime(d["date"])))


def _ipc(conn):
    rows = conn.execute("SELECT date, ipc_index FROM series_inflation ORDER BY date").fetchall()
    return pd.DataFrame(rows, columns=["date", "ipc_index"]).assign(date=lambda d: pd.to_datetime(d["date"]))


def _as_map(pairs) -> dict:
    """(metric, value, real_flag) triples or (metric, value) pairs -> {key: value}, skipping Nones."""
    out = {}
    for row in pairs:
        if isinstance(row, dict):
            out[(row.get("metric"), row.get("date"), row.get("window"))] = row.get("value")
        elif len(row) >= 3:
            out[(row[0], row[2])] = row[1]
        else:
            out[(row[0],)] = row[1]
    return {k: v for k, v in out.items() if v is not None}


def _assert_same(raw: dict, snap: dict, *, rtol: float, label: str, min_compared: int = 1):
    """Every key present in both must agree within `rtol`; the key SETS must agree too (a family that silently stops
    producing a metric when the stamps change has not passed)."""
    assert set(raw) == set(snap), (
        f"{label}: the set of produced values depends on the stamp convention "
        f"(only raw: {sorted(set(raw) - set(snap))[:6]}; only snapped: {sorted(set(snap) - set(raw))[:6]})")
    assert len(raw) >= min_compared, f"{label}: only {len(raw)} values compared -- the fixture produced nothing to test"
    bad = []
    for key, value in raw.items():
        other = snap[key]
        if value is None or other is None:
            continue
        scale = max(abs(float(value)), abs(float(other)), 1e-12)
        if abs(float(value) - float(other)) / scale > rtol:
            bad.append(f"{key}: raw={value!r} snapped={other!r}")
    assert not bad, (f"{label}: {len(bad)} of {len(raw)} values move when the NAV stamp moves to the month-end "
                     f"(rtol {rtol}):\n  " + "\n  ".join(sorted(bad)[:12]))


# --------------------------------------------------------------------------------------------------------------------
# Invariant families -- these pass today and must keep passing
# --------------------------------------------------------------------------------------------------------------------

def test_risk_and_returns_since_inception_are_invariant(both):
    def run(conn, isins):
        out = {}
        for isin in isins:
            df = risk_metrics.compute_risk_metrics(_nav(conn, isin), _ipc(conn))
            for row in df.itertuples(index=False):
                out[(isin, row.metric, row.real_flag)] = row.value
        return out
    raw, snap = both(run)
    _assert_same(raw, snap, rtol=1e-9, label="risk / returns (since inception)", min_compared=100)


def test_rolling_first_order_values_are_invariant(both):
    """What `fund_metrics` stores for a rolling horizon, and what P3's Layer 2 actually reads: the window is cut the way
    run_pipeline cuts it, then the scalar metrics are computed on it. The study measured 0.0% of these moving for
    weekday funds -- the DateOffset defect lives in the TIME SERIES, not here."""
    def run(conn, isins):
        out = {}
        for isin in isins:
            nav_df = _nav(conn, isin)
            for horizon, months in ROLLING.items():
                cutoff = nav_df["date"].max() - pd.DateOffset(months=months)
                window = nav_df[nav_df["date"] > cutoff].reset_index(drop=True)
                if len(window) < 5:
                    continue
                df = risk_metrics.compute_risk_metrics(window, _ipc(conn))
                for row in df.itertuples(index=False):
                    out[(isin, horizon, row.metric, row.real_flag)] = row.value
        return out
    raw, snap = both(run)
    _assert_same(raw, snap, rtol=1e-9, label="rolling first-order (fund_metrics)", min_compared=100)


def test_regime_returns_are_invariant(both):
    def run(conn, isins):
        months = _month_ends()
        regime_df = pd.DataFrame(
            {"regime": ["Expansion" if (k // 7) % 2 == 0 else "Contraccion" for k in range(len(months))]},
            index=pd.DatetimeIndex(months))
        out = {}
        for isin in isins:
            for metric, value, real_flag in regime_returns.compute_regime_returns(_nav(conn, isin), regime_df):
                out[(isin, metric, real_flag)] = value
        return out
    raw, snap = both(run)
    _assert_same(raw, snap, rtol=1e-9, label="regime", min_compared=40)


def test_capture_ratios_are_invariant(both):
    """1% relative: the EUR view reads the daily FX rate on the NAV's own DAY, so a 27th and a 28th legitimately differ
    at the 1e-5 level (R-9 rule 8). A real alignment defect would move these by far more."""
    def run(conn, isins):
        out = {}
        for isin in isins:
            for metric, value, real_flag in capture_ratios.compute_capture_ratios(isin, NATURE, _nav(conn, isin), conn):
                out[(isin, metric, real_flag)] = value
        return out
    raw, snap = both(run)
    _assert_same(raw, snap, rtol=0.01, label="capture", min_compared=20)


def test_momentum_is_invariant(both):
    """The peers are STORED scalars in fund_metrics, identical in both builds, so only the fund's own window can move.
    FND-0297 (a peer read without ORDER BY) is not reachable here: the comparison is between two runs of the same code
    over the same rows, and the fixture seeds one row per (isin, metric, horizon)."""
    def run(conn, isins):
        out = {}
        for isin in isins:
            for metric, value, real_flag in momentum.compute_momentum(isin, NATURE, _nav(conn, isin), conn):
                out[(isin, metric, real_flag)] = value
        return out
    raw, snap = both(run)
    _assert_same(raw, snap, rtol=1e-9, label="momentum", min_compared=20)


def test_fx_currency_factor_is_invariant(both):
    def run(conn, isins):
        out = {}
        for isin in isins:
            ccy = conn.execute("SELECT fund_currency FROM fund_master WHERE isin = %s", (isin,)).fetchone()[0]
            if ccy == "EUR":
                continue
            for metric, value, real_flag in currency_factor.compute_currency_factor(
                    isin, ccy, None, _nav(conn, isin), conn):
                out[(isin, metric, real_flag)] = value
        return out
    raw, snap = both(run)
    _assert_same(raw, snap, rtol=0.01, label="fx / currency factor", min_compared=4)


def test_persistence_is_invariant_at_the_family_level(both):
    """FND-0300: before the calendar-month windows the DateOffset window walk made individual values move at the boundary (30% in the study, median change
    0, Spearman 0.988). What must not move is the family's aggregate -- if the mean alpha_persistence over the panel
    shifted, the window walk would be dropping real months, not just shuffling edges."""
    def run(conn, isins):
        values = []
        for isin in isins:
            for metric, value, _real in persistence.compute_persistence(isin, NATURE, _nav(conn, isin), conn):
                if metric == "alpha_persistence" and value is not None:
                    values.append(float(value))
        return {("alpha_persistence", "mean"): float(np.mean(values)) if values else None,
                ("alpha_persistence", "n"): float(len(values))}
    raw, snap = both(run)
    assert raw[("alpha_persistence", "n")] == snap[("alpha_persistence", "n")], (
        "persistence produced a different NUMBER of values under the two stamp conventions -- that is not boundary noise")
    _assert_same(raw, snap, rtol=0.05, label="persistence (family aggregate)", min_compared=2)


def test_daily_deflation_is_untouched_by_the_monthly_convention(both):
    """R-9 rule 8: a DAILY series keeps its daily dates. `deflate_nav(align_month_end=False)` is the documented daily
    path (short_horizon), and it must give the same answer whatever the MONTHLY funds are stamped with."""
    def run(conn, _isins):
        days = pd.date_range("2024-01-02", "2024-06-28", freq="B")
        daily = pd.DataFrame({"date": days, "nav": 100.0 + np.arange(len(days)) * 0.1})
        out = deflation.deflate_nav(daily, _ipc(conn), align_month_end=False)
        return {(str(d.date()),): float(v) for d, v in zip(out["date"], out["nav_real"])}
    raw, snap = both(run)
    _assert_same(raw, snap, rtol=1e-12, label="deflation (daily path)", min_compared=100)


# --------------------------------------------------------------------------------------------------------------------
# Known violations -- xfail(strict=True): the fix removes the marker, nothing else
# --------------------------------------------------------------------------------------------------------------------

def test_macro_betas_are_invariant(both):
    def run(conn, isins):
        macro_df = macro_sensitivity.load_macro_factors(conn)
        assert not macro_df.empty, "the fixture produced no macro factors -- the test would pass vacuously"
        out = {}
        for isin in isins:
            for metric, value, real_flag in macro_sensitivity.compute_macro_sensitivity(_nav(conn, isin), macro_df):
                out[(isin, metric, real_flag)] = value
        return out
    raw, snap = both(run)
    _assert_same(raw, snap, rtol=1e-9, label="macro betas", min_compared=20)


def test_rolling_timeseries_rows_are_invariant(both):
    def run(conn, isins):
        out = {}
        for isin in isins:
            rows = rolling_stats.compute_rolling_rows(isin, _nav(conn, isin), ROLLING, ipc_df=_ipc(conn))
            for row in rows:
                # keyed on the row's own PERIOD, not its stamp -- otherwise the two runs would not even share keys and
                # the test would fail for the wrong reason (a key mismatch instead of a value difference).
                key = (isin, row["metric"], row["window"], str(to_period_key([row["date"]])[0].date()))
                out[key] = row.get("value_nominal", row.get("value"))
        return out
    raw, snap = both(run)
    _assert_same(raw, snap, rtol=1e-9, label="rolling time series", min_compared=500)
