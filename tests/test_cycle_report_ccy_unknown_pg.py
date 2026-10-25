# tests/test_cycle_report_ccy_unknown_pg.py
# -*- coding: utf-8 -*-
"""FND-0323 S5: the cycle report's "active funds with NAV and no class currency" query leaves out WRONG_DOC ISINs (out of scope by owner rule
2026-10-25) and still reports every other one. Real statement, throwaway per-test schema; read-only.

    python scripts/ops/run_pg_tests.py -- tests/test_cycle_report_ccy_unknown_pg.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "scripts" / "launch"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_spec = importlib.util.spec_from_file_location("p1p2_cycle_report_ccy", _ROOT / "scripts" / "launch" / "p1p2_cycle_report.py")
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)


def _world(conn, schema):
    conn.execute(f"SET search_path = {schema}")
    conn.execute("DROP TABLE IF EXISTS fund_master, fund_nav_monthly, fund_kiid_metadata")
    conn.execute("CREATE TABLE fund_master (isin text PRIMARY KEY, fund_currency text, in_current_universe smallint)")
    conn.execute("CREATE TABLE fund_nav_monthly (isin text NOT NULL, date date NOT NULL, nav double precision, PRIMARY KEY (isin, date))")
    conn.execute("CREATE TABLE fund_kiid_metadata (isin text PRIMARY KEY, kiid_status text)")
    conn.execute("""INSERT INTO fund_master VALUES
        ('UNK_OK', NULL, 1), ('UNK_BAD', NULL, 1), ('UNK_NOKIID', NULL, 1), ('UNK_NONAV', NULL, 1),
        ('UNK_RETIRED', NULL, 0), ('KNOWN', 'EUR', 1)""")
    conn.execute("INSERT INTO fund_nav_monthly VALUES ('UNK_OK','2026-09-30',1), ('UNK_BAD','2026-09-30',1), ('UNK_NOKIID','2026-09-30',1), "
                 "('UNK_RETIRED','2026-09-30',1), ('KNOWN','2026-09-30',1)")
    conn.execute("INSERT INTO fund_kiid_metadata VALUES ('UNK_OK','OK'), ('UNK_BAD','WRONG_DOC'), ('KNOWN','OK')")   # UNK_NOKIID has no metadata row at all
    conn.commit()


def test_the_query_reports_unknown_class_currency_except_for_wrong_doc_funds(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _world(conn, pg_conn_module_schema)
    got = [r[0] for r in conn.execute(cr.Q_CLASS_CCY_UNKNOWN).fetchall()]
    # UNK_BAD (WRONG_DOC) is out of scope; a fund with no metadata row is NOT excluded (only an explicit WRONG_DOC is); no NAV / retired / known never listed
    assert got == ["UNK_NOKIID", "UNK_OK"]
