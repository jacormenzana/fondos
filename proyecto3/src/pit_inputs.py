# proyecto3/src/pit_inputs.py
# -*- coding: utf-8 -*-
"""
DB readers for the point-in-time backtester -- FND-0159 d3.

Read-only, SQL-scoped by ISIN list (`isins=None` = whole universe) instead of the old unfiltered full-history
pivot (FND-0191). Literal SQL only (the EXPLAIN sweep verifies it). NAV dates are kept RAW (P2 aligns IPC on
the raw date; see pit_metrics) while NAV_MONTH_END_ALIGN_ENABLED is off, and carry P2's month-end labels when it is on (FND-0300,
shared/nav_month_end.py). Daily NAV is streamed in ISIN chunks: it is ~14M rows for the full universe.
"""

import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.score_candidates import fx_exposed_map
from shared import config as _config
from shared.config import REGION_IPC
from shared.doc_eligibility import wrong_doc_reasons   # FND-0323 D1
from shared.eur_nav import apply_eur_view_frame   # FND-0243
from shared.nav_month_end import MONTH_CLOSE_TOLERANCE_DAYS, align_nav_long   # FND-0300

ATTRIBUTE_FRAME_COLUMNS = ["ISIN", "Fund_Name", "Fund_Nature", "srri_kiid", "Investment_Focus", "Credit_Quality",
                           "Ongoing_Charge", "SRRI_Quality_Flag", "fund_family_id", "Management_Company",
                           "In_Current_Universe"]


def load_nav_panel(conn: "psycopg.Connection", isins: "list | None" = None) -> pd.DataFrame:
    """Wide monthly NAV panel: raw-date DatetimeIndex x ISIN (NaN where a fund has no observation)."""
    if isins is None:
        rows = conn.execute("SELECT isin, date, nav FROM fund_nav_monthly ORDER BY date").fetchall()
    else:
        rows = conn.execute(
            "SELECT isin, date, nav FROM fund_nav_monthly WHERE isin = ANY(%s) ORDER BY date", (list(isins),)
        ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([tuple(r) for r in rows], columns=["isin", "date", "nav"])
    df["date"] = pd.to_datetime(df["date"])
    df["nav"] = df["nav"].astype(float)
    df = apply_eur_view_frame(conn, df)   # FND-0243: EUR view, identity while the switch is off
    df = align_nav_long(df, key="isin")   # FND-0300: month-end label of each month-closing NAV (P2 load_nav does the same), identity while off
    if df.empty:
        return pd.DataFrame()
    wide = df.pivot_table(index="date", columns="isin", values="nav", aggfunc="last").sort_index()
    wide.columns.name = None
    return wide


def load_attributes(conn: "psycopg.Connection", isins: "list | None" = None) -> pd.DataFrame:
    """fund_master attributes for scoring, ALL funds (In_Current_Universe kept as a column, not filtered):
    the PIT universe includes retired funds (FND-0198). Static = today's values (documented look-ahead)."""
    if isins is None:
        rows = conn.execute("""
            SELECT ISIN, Fund_Name, Fund_Nature, SRRI AS srri_kiid, Investment_Focus, Credit_Quality,
                   Ongoing_Charge_Recurrent AS Ongoing_Charge, SRRI_Quality_Flag, fund_family_id,
                   Management_Company, In_Current_Universe
            FROM fund_master
        """).fetchall()
    else:
        rows = conn.execute("""
            SELECT ISIN, Fund_Name, Fund_Nature, SRRI AS srri_kiid, Investment_Focus, Credit_Quality,
                   Ongoing_Charge_Recurrent AS Ongoing_Charge, SRRI_Quality_Flag, fund_family_id,
                   Management_Company, In_Current_Universe
            FROM fund_master WHERE ISIN = ANY(%s)
        """, (list(isins),)).fetchall()
    df = pd.DataFrame([tuple(r) for r in rows], columns=ATTRIBUTE_FRAME_COLUMNS).set_index("ISIN")
    df.index.name = "isin"
    if _config.P3_WRONG_DOC_EXCLUSION_ENABLED:
        # FND-0323 D1: the scorer reads this column (check_hard_filters), so unlike a currency flag it MUST be part of the score cache key, which hashes
        # this frame. Only with the switch ON: OFF the frame is exactly what it was. KIID_Status has no history: today's status at every date.
        reasons = wrong_doc_reasons(conn)
        df["doc_status_reason"] = pd.Series([reasons.get(i) for i in df.index], index=df.index, dtype=object)
    return df


def load_fx_exposed(conn: "psycopg.Connection", isins) -> "pd.Series | None":
    """Boolean Series (index isin): does the fund count against the non-EUR budget (today's class currency / hedging, like every static
    attribute of the PIT universe -- documented look-ahead). None when the construction constraint is switched off.

    Kept OUT of `load_attributes`: that frame is hashed into the score cache keys and scoring never reads currency, so adding columns
    there would invalidate every cached PIT run for nothing."""
    flags = fx_exposed_map(conn, isins)
    if flags is None:
        return None
    s = pd.Series(flags, dtype=bool)
    s.index.name = "isin"
    return s


def load_ipc(conn: "psycopg.Connection", geography: str = REGION_IPC) -> pd.DataFrame:
    """IPC index (series_inflation), date + ipc_index, month-end dated -- same as P2's load_ipc."""
    rows = conn.execute(
        "SELECT date, ipc_index FROM series_inflation WHERE geography = %s ORDER BY date", (geography,)
    ).fetchall()
    df = pd.DataFrame([tuple(r) for r in rows], columns=["date", "ipc_index"])
    df["date"] = pd.to_datetime(df["date"]) + pd.offsets.MonthEnd(0)
    df["ipc_index"] = df["ipc_index"].astype(float)
    return df


def load_rate_deposit(conn: "psycopg.Connection") -> pd.DataFrame:
    """ECB deposit rate (series_macro rate_deposit/EU) as date + rate (DECIMAL, e.g. 0.04) -- the risk-free
    rate of the Sharpe (P2 load_rf_rate) AND the yield of the backtest's cash leg (FND-0197)."""
    rows = conn.execute(
        "SELECT date, value FROM series_macro WHERE indicator = 'rate_deposit' AND geography = 'EU' ORDER BY date"
    ).fetchall()
    df = pd.DataFrame([tuple(r) for r in rows], columns=["date", "rate"])
    df["date"] = pd.to_datetime(df["date"]) + pd.offsets.MonthEnd(0)
    df["rate"] = df["rate"].astype(float) / 100.0
    return df


def snap_nav_to_month_end(nav: pd.DataFrame) -> pd.DataFrame:
    """FND-0193 alignment test (run R4): the wide NAV panel with every raw date moved to the calendar month-end of its month. Funds that
    report on the last business day and funds that report on the calendar month-end then share one row per month; a fund with two
    observations in one month keeps the later one (groupby.last skips NaN, so each fund keeps its own value). Idempotent."""
    if nav.empty:
        return nav
    idx = pd.DatetimeIndex(nav.index)
    out = nav.groupby(idx + pd.offsets.MonthEnd(0)).last()
    out.index.name = nav.index.name
    # A trailing PARTIAL month (NAV only up to the 15th) would become a month-end label and be read as a complete month by
    # last_complete_month_end. Drop it exactly when the raw panel would not count it as complete (more than 5 days before its month-end),
    # so R4 differs from R1 only by alignment, never by one extra, incomplete month.
    last_raw = idx.max()
    if (last_raw + pd.offsets.MonthEnd(0) - last_raw).days > MONTH_CLOSE_TOLERANCE_DAYS:
        out = out.iloc[:-1]
    return out


SNAPSHOT_DIR = "inputs"
_SNAPSHOT_FILES = ("nav", "attrs", "rate", "fx_exposed", "regime_hist")


def save_inputs_snapshot(run_dir, inputs, regime_hist: pd.DataFrame, target: "pd.Series | None" = None) -> list:
    """FND-0193: freeze the DB-side inputs of a PIT run next to its scores (<run>/inputs/*.parquet), so an analysis of the
    saved run reads exactly what the run saw even after a NAV load or a macro refresh moved the database (the 2026-10-17
    run no longer reproduced: one regime label had changed). Returns the written file names."""
    d = Path(run_dir) / SNAPSHOT_DIR
    d.mkdir(parents=True, exist_ok=True)
    frames = {"nav": inputs.nav, "attrs": inputs.attrs, "rate": inputs.rate if inputs.rate is not None else pd.DataFrame(columns=["date", "rate"]),
              "regime_hist": regime_hist}
    if getattr(inputs, "fx_exposed", None) is not None:
        frames["fx_exposed"] = inputs.fx_exposed.rename("fx_exposed").to_frame()
    if target is not None:
        frames["target"] = target.rename("target").to_frame()
    written = []
    for name, df in frames.items():
        out = df.copy()
        out.columns = [str(c) for c in out.columns]
        out.to_parquet(d / f"{name}.parquet")
        written.append(f"{name}.parquet")
    return written


def has_inputs_snapshot(run_dir) -> bool:
    d = Path(run_dir) / SNAPSHOT_DIR
    return all((d / f"{n}.parquet").exists() for n in ("nav", "attrs", "rate", "regime_hist"))


def load_inputs_snapshot(run_dir) -> "tuple[object, pd.DataFrame, pd.Series | None]":
    """(PitInputs, regime history, IPC+M3 target or None) from <run>/inputs/. Inverse of save_inputs_snapshot; ipc stays None
    (the portfolio / return layers do not need it)."""
    from proyecto3.src.pit_run import PitInputs
    d = Path(run_dir) / SNAPSHOT_DIR
    rate = pd.read_parquet(d / "rate.parquet")
    fx = pd.read_parquet(d / "fx_exposed.parquet")["fx_exposed"].astype(bool) if (d / "fx_exposed.parquet").exists() else None
    inputs = PitInputs(nav=pd.read_parquet(d / "nav.parquet"), attrs=pd.read_parquet(d / "attrs.parquet"), ipc=None,
                       rate=rate if len(rate) else None, fx_exposed=fx)
    target = pd.read_parquet(d / "target.parquet")["target"] if (d / "target.parquet").exists() else None
    return inputs, pd.read_parquet(d / "regime_hist.parquet").sort_index(), target


def iter_daily_chunks(conn: "psycopg.Connection", isins: list, chunk_size: int = 300):
    """Yield long frames [isin, date, nav] of daily NAV for successive ISIN chunks (bounded memory)."""
    isins = sorted(isins)
    for i in range(0, len(isins), chunk_size):
        part = isins[i:i + chunk_size]
        rows = conn.execute(
            "SELECT isin, date, nav FROM fund_nav_daily WHERE isin = ANY(%s) ORDER BY isin, date", (part,)
        ).fetchall()
        df = pd.DataFrame([tuple(r) for r in rows], columns=["isin", "date", "nav"])
        df["date"] = pd.to_datetime(df["date"])
        df["nav"] = df["nav"].astype(float)
        yield apply_eur_view_frame(conn, df)   # FND-0243: EUR view, identity while the switch is off
