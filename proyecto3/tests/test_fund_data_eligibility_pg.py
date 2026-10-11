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

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import shared.config as cfg  # noqa: E402
from proyecto3.src import fund_scorer as fs  # noqa: E402

AS_OF = date(2026, 10, 11)


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
