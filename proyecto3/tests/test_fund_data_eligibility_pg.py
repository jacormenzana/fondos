# proyecto3/tests/test_fund_data_eligibility_pg.py
# -*- coding: utf-8 -*-
"""FND-0251 Commit B on Postgres: the eligibility inputs (newest monthly NAV, active accepted residuals) and the loader's switch.
Throwaway per-test schema (same discipline as test_p3_reads_pg.py); everything is read-only.

    python scripts/ops/run_pg_tests.py -- proyecto3/tests/test_fund_data_eligibility_pg.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import shared.config as cfg  # noqa: E402
from proyecto3.src import fund_scorer as fs  # noqa: E402

AS_OF = date(2026, 10, 11)


@pytest.fixture(autouse=True)
def _pin_the_document_rule_off(monkeypatch):
    """FND-0323 D1: this module's toy schema has no fund_kiid_metadata and tests the data rule; the document rule has its own tests
    (test_wrong_doc_eligibility_pg.py), so it is stated OFF here instead of inherited from the default."""
    monkeypatch.setattr(cfg, "P3_WRONG_DOC_EXCLUSION_ENABLED", False)


def _world(conn, schema, with_residuals=True):
    conn.execute(f"SET search_path = {schema}")
    conn.execute("""CREATE TABLE fund_master (isin text PRIMARY KEY, fund_name text, fund_nature text, srri smallint, investment_focus text,
                    credit_quality text, ongoing_charge_recurrent double precision, srri_quality_flag text, fund_family_id text,
                    in_current_universe smallint)""")
    conn.execute("""CREATE TABLE fund_metrics (isin text NOT NULL, metric text NOT NULL, horizon text NOT NULL, value double precision,
                    real_flag smallint NOT NULL DEFAULT 0, metric_version text NOT NULL DEFAULT 'v1',
                    PRIMARY KEY (isin, metric, horizon, real_flag, metric_version))""")
    conn.execute("CREATE TABLE fund_nav_monthly (isin text NOT NULL, date date NOT NULL, nav double precision, PRIMARY KEY (isin, date))")
    conn.execute("INSERT INTO fund_master VALUES ('FRESH','Fresh','Mixtos',4,'Broad','Investment Grade',0.01,'OK','f1',1), "
                 "('STALE','Stale','Mixtos',4,'Broad','Investment Grade',0.01,'OK','f2',1), "
                 "('RESID','Resid','Mixtos',4,'Broad','Investment Grade',0.01,'OK','f3',1), "
                 "('NONAV','NoNav','Mixtos',4,'Broad','Investment Grade',0.01,'OK','f4',1)")
    for isin in ("FRESH", "STALE", "RESID", "NONAV"):
        conn.execute("INSERT INTO fund_metrics (isin, metric, horizon, value, real_flag) VALUES "
                     "(%s, 'return_ann', 'since_inception', 0.05, 1), (%s, 'sharpe', 'since_inception', 1.0, 0)", (isin, isin))
    conn.execute("INSERT INTO fund_nav_monthly VALUES ('FRESH','2026-09-30',1), ('FRESH','2026-08-31',1), ('STALE','2025-12-31',1), "
                 "('RESID','2026-09-30',1)")
    if with_residuals:
        conn.execute("""CREATE TABLE audit_accepted_finding (domain text NOT NULL, rule_id text NOT NULL, isin varchar(12) NOT NULL,
                        ap_id text NOT NULL, reason text NOT NULL, accepted_at date NOT NULL DEFAULT current_date, accepted_by text,
                        expires_at date NOT NULL, PRIMARY KEY (domain, rule_id, isin))""")
        ins = ("INSERT INTO audit_accepted_finding (domain, rule_id, isin, ap_id, reason, accepted_at, expires_at) "
               "VALUES (%s, %s, %s, %s, 'known', %s, %s)")
        past, future = AS_OF - timedelta(days=30), AS_OF + timedelta(days=60)
        conn.execute(ins, ("p2_metrics", "WINDOW_FISHER_IDENTITY", "RESID", "FND-0250", past, future))          # active
        conn.execute(ins, ("p2_metrics", "REAL_EQUALS_NOMINAL_sharpe", "RESID", "FND-0252", past, future))      # active, 2nd rule
        conn.execute(ins, ("p2_metrics", "WINDOW_FISHER_IDENTITY", "FRESH", "FND-0250", AS_OF - timedelta(days=90),
                           AS_OF - timedelta(days=1)))                                                         # EXPIRED: must not count
        conn.execute(ins, ("cost_attributes", "OC_NOT_CONTAMINATED", "FRESH", "FND-0034", past, future))        # other domain: must not count
    conn.commit()


def test_inputs_carry_the_newest_nav_age_and_only_active_residuals_of_the_p2_domain(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _world(conn, pg_conn_module_schema)
    got = fs.load_eligibility_inputs(conn, as_of=AS_OF)
    assert list(got.columns) == fs.ELIGIBILITY_COLUMNS
    assert got.loc["FRESH", "nav_age_days"] == 11 and got.loc["FRESH", "nav_last_date"] == date(2026, 9, 30)
    assert got.loc["STALE", "nav_age_days"] == (AS_OF - date(2025, 12, 31)).days
    assert pd.isna(got.loc["FRESH", "residual_reason"])                          # expired + other-domain acceptances do not count
    assert got.loc["RESID", "residual_reason"] == ("REAL_EQUALS_NOMINAL_sharpe (FND-0252, hasta 2026-12-10); "
                                                   "WINDOW_FISHER_IDENTITY (FND-0250, hasta 2026-12-10)")


def test_a_missing_residual_table_means_no_residuals_not_an_error(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _world(conn, pg_conn_module_schema, with_residuals=False)
    got = fs.load_eligibility_inputs(conn, as_of=AS_OF)
    assert got["residual_reason"].isna().all() and got.loc["FRESH", "nav_age_days"] == 11


def test_the_loader_attaches_the_inputs_only_when_the_switch_is_on(pg_session_conn, pg_conn_module_schema, monkeypatch):
    conn = pg_session_conn
    _world(conn, pg_conn_module_schema)
    monkeypatch.setattr(cfg, "FUND_DATA_ELIGIBILITY_ENABLED", False)
    off = fs.load_fund_metrics_for_scoring(conn)
    assert not set(fs.ELIGIBILITY_COLUMNS) & set(off.columns)                    # OFF: the frame is exactly what it was before
    monkeypatch.setattr(cfg, "FUND_DATA_ELIGIBILITY_ENABLED", True)
    on = fs.load_fund_metrics_for_scoring(conn)
    assert set(fs.ELIGIBILITY_COLUMNS) <= set(on.columns) and set(on.index) == set(off.index)
    assert on.loc["NONAV", "nav_age_days"] != on.loc["NONAV", "nav_age_days"]    # NaN: a fund with no monthly NAV is not "stale"
    assert isinstance(on.loc["RESID", "residual_reason"], str)


def test_with_the_switch_on_the_scorer_excludes_exactly_the_stale_and_the_residual_fund(pg_session_conn, pg_conn_module_schema, monkeypatch):
    conn = pg_session_conn
    _world(conn, pg_conn_module_schema)
    monkeypatch.setattr(cfg, "FUND_DATA_ELIGIBILITY_ENABLED", True)
    monkeypatch.setattr(fs, "date", type("D", (), {"today": staticmethod(lambda: AS_OF)}))   # the loader's as_of default, deterministic
    df = fs.load_fund_metrics_for_scoring(conn)
    res = fs.score_funds_from_df(df, "Expansion", verbose=False)
    # a fund is scored once per sub-portfolio that contains its nature: judge it across all its rows
    elig = res.groupby("isin")["eligible"].agg(lambda x: bool(x.astype(bool).all()))
    reason = res.groupby("isin")["exclusion_reason"].agg(lambda x: sorted({v for v in x if v})[0] if any(x) else None)
    assert elig["FRESH"] and elig["NONAV"]
    assert not elig["STALE"] and reason["STALE"].startswith("nav_stale: last NAV 2025-12-31")
    assert not elig["RESID"] and reason["RESID"].startswith("accepted_residual: REAL_EQUALS_NOMINAL_sharpe (FND-0252")
    assert not res[res["isin"] == "FRESH"]["score_final"].isna().any()


# ---- NAV continuity (item C) ----------------------------------------------------------------------------------------------------

def _months(start_year, start_month, n, skip=()):
    out = []
    y, m = start_year, start_month
    for i in range(n):
        if i not in skip:
            out.append(f"{y:04d}-{m:02d}-28")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _seed_nav(conn, isin, dates):
    for d in dates:
        conn.execute("INSERT INTO fund_nav_monthly VALUES (%s, %s, 1.0)", (isin, d))


def test_continuity_flags_only_sparse_or_long_outage_series_and_names_the_numbers(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _world(conn, pg_conn_module_schema)
    _seed_nav(conn, "FULL", _months(2023, 1, 36))                                   # 36/36: no gap
    _seed_nav(conn, "SPARSE", _months(2023, 1, 36, skip={i for i in range(36) if i % 3}))   # one row a quarter: 12/34 = 35% coverage
    _seed_nav(conn, "OUTAGE", _months(2023, 1, 40, skip=set(range(10, 16))))        # 34/40 = 85% coverage, ONE hole of 6 months
    _seed_nav(conn, "SHORTHOLE", _months(2023, 1, 40, skip={10, 11, 12}))           # 37/40 = 92%, hole of 3 months: tolerated
    conn.commit()
    got = fs.load_eligibility_inputs(conn, as_of=AS_OF)["nav_gap_reason"]
    assert pd.isna(got.get("FULL")) and pd.isna(got.get("SHORTHOLE"))
    assert got["SPARSE"].startswith("nav_gap: coverage 35% (12/34 months, 2023-01..2025-10)")
    assert "longest gap 2 months" in got["SPARSE"] and "11 gaps" in got["SPARSE"]
    assert got["OUTAGE"].startswith("nav_gap: coverage 85% (34/40 months, 2023-01..2026-04)")
    assert "longest gap 6 months, 1 gaps" in got["OUTAGE"]
    assert pd.isna(got.get("FRESH"))                                                # the base world's consecutive months


def test_a_one_row_fund_and_a_two_month_fund_are_not_flagged(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _world(conn, pg_conn_module_schema)
    got = fs.load_eligibility_inputs(conn, as_of=AS_OF)["nav_gap_reason"]
    assert got.dropna().empty                                                       # STALE (1 row), RESID (1 row), FRESH (2 consecutive)


# ---- FND-0254: PIT parity -------------------------------------------------------------------------------------------------------

def test_the_pit_panel_path_returns_exactly_the_live_reasons_from_the_same_rows(pg_session_conn, pg_conn_module_schema):
    """Two implementations of ONE rule (live SQL aggregation vs the PIT wide-panel evaluation, both calling shared.nav_continuity):
    seeded with the same rows they must return the same set of funds AND the same reason text, byte for byte."""
    from shared.nav_continuity import reasons_by_date
    conn = pg_session_conn
    _world(conn, pg_conn_module_schema)
    _seed_nav(conn, "FULL", _months(2023, 1, 36))
    _seed_nav(conn, "SPARSE", _months(2023, 1, 36, skip={i for i in range(36) if i % 3}))
    _seed_nav(conn, "OUTAGE", _months(2023, 1, 40, skip=set(range(10, 16))))
    _seed_nav(conn, "SHORTHOLE", _months(2023, 1, 40, skip={10, 11, 12}))
    _seed_nav(conn, "DUPMONTH", _months(2023, 1, 24, skip=set(range(8, 18))) + ["2023-02-15"])   # two rows in Feb-2023, one 10-month hole
    conn.commit()
    live = fs._nav_gap_reasons(conn)
    rows = conn.execute("SELECT isin, date, nav FROM fund_nav_monthly ORDER BY date").fetchall()
    long = pd.DataFrame([tuple(r) for r in rows], columns=["isin", "date", "nav"])
    long["date"] = pd.to_datetime(long["date"])
    wide = long.pivot_table(index="date", columns="isin", values="nav", aggfunc="last").sort_index()
    at = pd.DatetimeIndex([wide.index.max()])
    pit = reasons_by_date(wide, at, cfg.P3_FUND_NAV_MIN_COVERAGE, cfg.P3_FUND_NAV_MAX_GAP_RUN_MONTHS).iloc[0].dropna().to_dict()
    assert {"SPARSE", "OUTAGE", "DUPMONTH"} <= set(live)                       # the fixture really exercises the rule
    assert pit == live
