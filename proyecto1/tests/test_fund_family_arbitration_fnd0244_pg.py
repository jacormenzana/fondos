# proyecto1/tests/test_fund_family_arbitration_fnd0244_pg.py
# -*- coding: utf-8 -*-
"""FND-0244 part 2 on Postgres: correct_family_inconsistencies() applies rule 4 from the real SQL (KIID text, NAV count, benchmark merge,
realized-volatility band) and logs it. The builder commits, so: pg_conn_module_schema (throwaway schema), never the SAVEPOINT fixture."""
from __future__ import annotations

import importlib.util
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
_P1_DIR = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
for _p in (_P1_DIR, os.path.normpath(os.path.join(_TESTS_DIR, "..", ".."))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_spec = importlib.util.spec_from_file_location("ffb_arbitration_pg_test", os.path.join(_CORE_DIR, "fund_family_builder.py"))
_ffb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ffb)

PAD = "This document provides key information about this investment product. " * 4
BOND_ST = PAD + ("Objectives and investment policy. The objective of the investment policy is to generate a return in euro. In order to "
                 "achieve this, the fund invests in government and corporate bonds denominated in or hedged against the euro. The average "
                 "duration of the fund is a maximum of 12 months and is achieved by employing suitable derivatives, among other things.")


def _tables(conn):
    conn.execute("""CREATE TABLE fund_master (isin TEXT PRIMARY KEY, fund_name TEXT, management_company TEXT, fund_nature TEXT,
        fund_family_id TEXT, data_quality_flag TEXT, srri_quality_flag TEXT, family TEXT, fund_currency TEXT, benchmark_declared TEXT)""")
    conn.execute("CREATE TABLE ingestion_log (id SERIAL PRIMARY KEY, isin TEXT, step TEXT, status TEXT, message TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE fund_kiid_metadata (isin TEXT, kiid_class INTEGER, raw_kiid_text TEXT)")
    conn.execute("CREATE TABLE fund_nav_monthly (isin TEXT, date DATE, nav DOUBLE PRECISION)")
    conn.execute("""CREATE TABLE fund_benchmarks (isin TEXT, source TEXT, asset_class TEXT, benchmark_role TEXT, benchmark_name TEXT,
        confidence TEXT)""")
    conn.execute("CREATE TABLE fund_metrics (isin TEXT, metric TEXT, horizon TEXT, real_flag INTEGER, value DOUBLE PRECISION)")


def _fund(conn, isin, name, nature, ccy, text=BOND_ST, band=2, nav=60):
    conn.execute("INSERT INTO fund_master (isin, fund_name, management_company, fund_nature, fund_family_id, data_quality_flag, "
                 "srri_quality_flag, fund_currency) VALUES (%s,%s,'MC',%s,'FAM_000001','OK','HIGH',%s)", (isin, name, nature, ccy))
    conn.execute("INSERT INTO fund_kiid_metadata VALUES (%s, 1, %s)", (isin, text))
    conn.execute("INSERT INTO fund_metrics VALUES (%s, 'srri_nav', 'since_inception', 0, %s)", (isin, float(band)))
    for i in range(nav):
        conn.execute("INSERT INTO fund_nav_monthly VALUES (%s, make_date(2020 + %s / 12, 1 + %s %% 12, 1), 100)", (isin, i, i))


def test_rule_4_aligns_the_class_that_the_individual_evidence_flipped(pg_conn_module_schema, pg_session_conn):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _tables(conn)
    _fund(conn, "IE000000EUR1", "DWS FLOAT RATE NOTE LD EUR INC", "Renta Fija Corto Plazo", "EUR")
    _fund(conn, "LU000000USD2", "DWS FLOAT RATE NOTE LD USD INC", "Renta Fija Flexible", "USD", nav=200)
    conn.commit()
    n = _ffb.correct_family_inconsistencies(conn)
    assert n == 1
    assert dict(conn.execute("SELECT isin, fund_nature FROM fund_master").fetchall()) == {
        "IE000000EUR1": "Renta Fija Corto Plazo", "LU000000USD2": "Renta Fija Corto Plazo"}
    assert conn.execute("SELECT count(*) FROM ingestion_log WHERE step = 'FAMILY_NATURE_CORRECTION' AND isin = 'LU000000USD2'").fetchone()[0] == 1


def test_a_family_that_cannot_be_decided_is_left_untouched_and_the_run_survives(pg_conn_module_schema, pg_session_conn):
    """Non-adjacent natures: no correction. Also proves the evidence loader's SQL runs against the real column names."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _tables(conn)
    _fund(conn, "IE000000EUR1", "ACME FUND EUR", "Renta Variable", "EUR")
    _fund(conn, "LU000000USD2", "ACME FUND USD", "Monetario", "USD")
    conn.commit()
    assert _ffb.correct_family_inconsistencies(conn) == 0
    assert dict(conn.execute("SELECT isin, fund_nature FROM fund_master").fetchall()) == {"IE000000EUR1": "Renta Variable", "LU000000USD2": "Monetario"}


def test_a_missing_evidence_table_does_not_abort_the_transaction(pg_conn_module_schema, pg_session_conn):
    """The older builder tests create only fund_master: rule 4's loader fails inside its SAVEPOINT and the rest of the run goes on."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    conn.execute("""CREATE TABLE fund_master (isin TEXT PRIMARY KEY, fund_name TEXT, management_company TEXT, fund_nature TEXT,
        fund_family_id TEXT, data_quality_flag TEXT, srri_quality_flag TEXT, family TEXT)""")
    conn.execute("CREATE TABLE ingestion_log (id SERIAL PRIMARY KEY, isin TEXT, step TEXT, status TEXT, message TEXT, created_at TEXT)")
    conn.execute("INSERT INTO fund_master VALUES ('A','X EUR','MC','Mixtos','FAM_000001','OK','HIGH',NULL), "
                 "('B','X USD','MC','Renta Variable','FAM_000001','OK','HIGH',NULL)")
    conn.commit()
    assert _ffb.correct_family_inconsistencies(conn) == 0
    assert conn.execute("SELECT count(*) FROM fund_master").fetchone()[0] == 2          # the connection is still usable
