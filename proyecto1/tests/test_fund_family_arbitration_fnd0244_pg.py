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
        fund_family_id TEXT, data_quality_flag TEXT, srri_quality_flag TEXT, family TEXT, fund_currency TEXT, benchmark_declared TEXT,
        in_current_universe INTEGER DEFAULT 1)""")
    conn.execute("CREATE TABLE ingestion_log (id SERIAL PRIMARY KEY, isin TEXT, step TEXT, status TEXT, message TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE fund_kiid_metadata (isin TEXT, kiid_class INTEGER, raw_kiid_text TEXT)")
    conn.execute("CREATE TABLE fund_nav_monthly (isin TEXT, date DATE, nav DOUBLE PRECISION)")
    conn.execute("""CREATE TABLE fund_benchmarks (isin TEXT, source TEXT, asset_class TEXT, benchmark_role TEXT, benchmark_name TEXT,
        confidence TEXT)""")
    conn.execute("CREATE TABLE fund_metrics (isin TEXT, metric TEXT, horizon TEXT, real_flag INTEGER, value DOUBLE PRECISION)")


def _fund(conn, isin, name, nature, ccy, text=BOND_ST, band=2, nav=60, active=1, fam="FAM_000001"):
    conn.execute("INSERT INTO fund_master (isin, fund_name, management_company, fund_nature, fund_family_id, data_quality_flag, "
                 "srri_quality_flag, fund_currency, in_current_universe) VALUES (%s,%s,'MC',%s,%s,'OK','HIGH',%s,%s)",
                 (isin, name, nature, fam, ccy, active))
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
        fund_family_id TEXT, data_quality_flag TEXT, srri_quality_flag TEXT, family TEXT, in_current_universe INTEGER DEFAULT 1)""")
    conn.execute("CREATE TABLE ingestion_log (id SERIAL PRIMARY KEY, isin TEXT, step TEXT, status TEXT, message TEXT, created_at TEXT)")
    conn.execute("INSERT INTO fund_master VALUES ('A','X EUR','MC','Mixtos','FAM_000001','OK','HIGH',NULL), "
                 "('B','X USD','MC','Renta Variable','FAM_000001','OK','HIGH',NULL)")
    conn.commit()
    assert _ffb.correct_family_inconsistencies(conn) == 0
    assert conn.execute("SELECT count(*) FROM fund_master").fetchone()[0] == 2          # the connection is still usable


# ---------------------------------------------------------------- inactive-only families (Franklin Alt St)
def test_a_retired_family_with_non_adjacent_natures_is_arbitrated_but_an_active_one_is_not(pg_conn_module_schema, pg_session_conn):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _tables(conn)
    # retired family: Monetario vs Renta Fija Corto Plazo are adjacent, so use a non-adjacent pair with the same bond-short-duration text
    _fund(conn, "IE000000EUR1", "DWS FLOAT RATE NOTE W EUR", "Renta Fija Corto Plazo", "EUR", active=0)
    _fund(conn, "LU000000EUR2", "DWS FLOAT RATE NOTE I EURHDG", "Alternativo", "EUR", active=0)
    conn.commit()
    assert _ffb.correct_family_inconsistencies(conn) == 1
    assert dict(conn.execute("SELECT isin, fund_nature FROM fund_master").fetchall()) == {
        "IE000000EUR1": "Renta Fija Corto Plazo", "LU000000EUR2": "Renta Fija Corto Plazo"}


def test_an_active_family_with_non_adjacent_natures_stays_untouched(pg_conn_module_schema, pg_session_conn):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _tables(conn)
    _fund(conn, "IE000000EUR1", "DWS FLOAT RATE NOTE W EUR", "Renta Fija Corto Plazo", "EUR", active=1)
    _fund(conn, "LU000000EUR2", "DWS FLOAT RATE NOTE I EURHDG", "Alternativo", "EUR", active=0)       # ONE active member is enough to keep it flagged
    conn.commit()
    assert _ffb.correct_family_inconsistencies(conn) == 0
    assert dict(conn.execute("SELECT isin, fund_nature FROM fund_master").fetchall()) == {
        "IE000000EUR1": "Renta Fija Corto Plazo", "LU000000EUR2": "Alternativo"}


def test_the_consistency_warning_ignores_families_that_are_inconsistent_only_through_retired_funds(pg_conn_module_schema, pg_session_conn):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _tables(conn)
    _fund(conn, "A", "FUND A", "Mixtos", "EUR", active=1)
    _fund(conn, "B", "FUND B", "Renta Variable", "EUR", active=0)                            # inconsistent only through the retired class
    conn.commit()
    assert len(_ffb._validate_family_consistency(conn)) == 1
    assert _ffb._validate_family_consistency(conn, active_only=True) == []


# ---------------------------------------------------------------- regression matrix (Franklin Alt St and neighbours), ONE call over a mixed catalogue
def _natures(conn):
    return dict(conn.execute("SELECT isin, fund_nature FROM fund_master").fetchall())


def _corrections(conn):
    return conn.execute("SELECT count(*) FROM ingestion_log WHERE step = 'FAMILY_NATURE_CORRECTION'").fetchone()[0]


def _mixed_catalogue(conn):
    # F_RETIRED: non-adjacent natures, no active member (Franklin Alt St shape) -> arbitrated by the EUR reference class
    _fund(conn, "RET_EUR_1", "DWS FLOAT RATE NOTE W EUR", "Renta Fija Corto Plazo", "EUR", active=0, fam="F_RETIRED")
    _fund(conn, "RET_EUR_2", "DWS FLOAT RATE NOTE I EURHDG", "Alternativo", "EUR", active=0, fam="F_RETIRED")
    # F_ADJACENT: adjacent natures, ACTIVE -> rule 4 as before (the inactive rule must not be needed nor interfere)
    _fund(conn, "ADJ_EUR_1", "DWS FLOAT RATE NOTE LD EUR INC", "Renta Fija Corto Plazo", "EUR", fam="F_ADJACENT")
    _fund(conn, "ADJ_USD_2", "DWS FLOAT RATE NOTE LD USD INC", "Renta Fija Flexible", "USD", nav=200, fam="F_ADJACENT")
    # F_ONE_ACTIVE: non-adjacent natures, ONE active member -> stays flagged and untouched
    _fund(conn, "ONE_EUR_1", "GAMMA FLOAT NOTE W EUR", "Renta Fija Corto Plazo", "EUR", active=1, fam="F_ONE_ACTIVE")
    _fund(conn, "ONE_EUR_2", "GAMMA FLOAT NOTE I EURHDG", "Alternativo", "EUR", active=0, fam="F_ONE_ACTIVE")
    # F_RESTANTES: retired, 3 classes, Restantes among non-adjacent natures -> rule 4 leaves it alone (Restantes never wins, never decides)
    _fund(conn, "RST_EUR_1", "DELTA FLOAT NOTE W EUR", "Restantes", "EUR", active=0, fam="F_RESTANTES")
    _fund(conn, "RST_EUR_2", "DELTA FLOAT NOTE I EURHDG", "Renta Fija Corto Plazo", "EUR", active=0, fam="F_RESTANTES")
    _fund(conn, "RST_EUR_3", "DELTA FLOAT NOTE R EUR", "Alternativo", "EUR", active=0, fam="F_RESTANTES")
    # F_RESTANTES_PAIR: a two-class family with Restantes is settled by the older rule 2-bis-A (the specific nature wins, Restantes is overwritten)
    _fund(conn, "RSP_EUR_1", "EPSILON FLOAT NOTE W EUR", "Restantes", "EUR", active=0, fam="F_RESTANTES_PAIR")
    _fund(conn, "RSP_EUR_2", "EPSILON FLOAT NOTE I EUR", "Renta Fija Corto Plazo", "EUR", active=0, fam="F_RESTANTES_PAIR")
    # F_TWO_ACTIVE: non-adjacent natures, every member active -> stays flagged and untouched (the key probably merged two funds)
    _fund(conn, "TWO_EUR_1", "ACME FUND EUR", "Renta Variable", "EUR", fam="F_TWO_ACTIVE")
    _fund(conn, "TWO_USD_2", "ACME FUND USD", "Monetario", "USD", fam="F_TWO_ACTIVE")
    conn.commit()


def test_the_matrix_one_call_over_a_mixed_catalogue(pg_conn_module_schema, pg_session_conn):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _tables(conn)
    _mixed_catalogue(conn)
    assert _ffb.correct_family_inconsistencies(conn) == 3          # F_RETIRED + F_ADJACENT (rule 4) and F_RESTANTES_PAIR (rule 2-bis-A), nothing else
    assert _natures(conn) == {
        "RET_EUR_1": "Renta Fija Corto Plazo", "RET_EUR_2": "Renta Fija Corto Plazo",        # inactive-only family arbitrated by the EUR class
        "ADJ_EUR_1": "Renta Fija Corto Plazo", "ADJ_USD_2": "Renta Fija Corto Plazo",        # active adjacent family: rule 4 untouched by the new rule
        "ONE_EUR_1": "Renta Fija Corto Plazo", "ONE_EUR_2": "Alternativo",                   # one active member keeps the flag
        "TWO_EUR_1": "Renta Variable", "TWO_USD_2": "Monetario",                             # all active, non-adjacent: untouched
        "RST_EUR_1": "Restantes", "RST_EUR_2": "Renta Fija Corto Plazo", "RST_EUR_3": "Alternativo",   # Restantes never decides: untouched
        "RSP_EUR_1": "Renta Fija Corto Plazo", "RSP_EUR_2": "Renta Fija Corto Plazo"}        # ... and as a pair it is the one overwritten
    logged = {r[0] for r in conn.execute("SELECT isin FROM ingestion_log WHERE step = 'FAMILY_NATURE_CORRECTION'").fetchall()}
    assert logged == {"RET_EUR_2", "ADJ_USD_2", "RSP_EUR_1"}


def test_the_matrix_rerun_is_a_no_op_without_duplicate_log_rows(pg_conn_module_schema, pg_session_conn):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _tables(conn)
    _mixed_catalogue(conn)
    assert _ffb.correct_family_inconsistencies(conn) == 3
    after_first, rows_first = _natures(conn), _corrections(conn)
    assert _ffb.correct_family_inconsistencies(conn) == 0
    assert _natures(conn) == after_first and _corrections(conn) == rows_first == 3


def test_a_member_turning_active_between_two_runs_stops_the_inactive_only_arbitration_and_nothing_is_sticky(pg_conn_module_schema, pg_session_conn):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _tables(conn)
    _fund(conn, "FRK_EUR_1", "DWS FLOAT RATE NOTE W EUR", "Renta Fija Corto Plazo", "EUR", active=0, fam="F_FRANKLIN")
    _fund(conn, "FRK_EUR_2", "DWS FLOAT RATE NOTE I EURHDG", "Alternativo", "EUR", active=0, fam="F_FRANKLIN")
    conn.execute("UPDATE fund_master SET in_current_universe = 1 WHERE isin = 'FRK_EUR_2'")        # the retired class comes back
    conn.commit()
    assert _ffb.correct_family_inconsistencies(conn) == 0                                          # one active member: not arbitrated
    assert _natures(conn) == {"FRK_EUR_1": "Renta Fija Corto Plazo", "FRK_EUR_2": "Alternativo"}
    conn.execute("UPDATE fund_master SET in_current_universe = 0 WHERE isin = 'FRK_EUR_2'")        # retired again: the rule applies again
    conn.commit()
    assert _ffb.correct_family_inconsistencies(conn) == 1
    assert _natures(conn) == {"FRK_EUR_1": "Renta Fija Corto Plazo", "FRK_EUR_2": "Renta Fija Corto Plazo"}


def test_the_post_correction_warning_after_the_matrix_counts_only_the_active_flagged_family(pg_conn_module_schema, pg_session_conn):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _tables(conn)
    _mixed_catalogue(conn)
    _ffb.correct_family_inconsistencies(conn)
    flagged_active = {row[0] for row in _ffb._validate_family_consistency(conn, active_only=True)}
    flagged_all = {row[0] for row in _ffb._validate_family_consistency(conn)}
    assert flagged_active == {"F_TWO_ACTIVE"}                       # the only family still inconsistent through ACTIVE members
    assert flagged_all == {"F_TWO_ACTIVE", "F_ONE_ACTIVE", "F_RESTANTES"}      # retired / mixed-through-retired ones are noise, not an alarm
