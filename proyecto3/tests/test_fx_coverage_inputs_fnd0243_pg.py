# proyecto3/tests/test_fx_coverage_inputs_fnd0243_pg.py
# -*- coding: utf-8 -*-
"""FND-0243 step 5 on Postgres: load_fx_coverage_inputs reads, per non-EUR class currency of the ACTIVE universe, the newest NAV and
the newest ECB daily rate, and counts the active funds with NAV and unknown class currency.

    python scripts/ops/run_pg_tests.py -- proyecto3/tests/test_fx_coverage_inputs_fnd0243_pg.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.config import EUR_FX_DAILY_INDICATOR, EUR_FX_MONTHLY_INDICATOR  # noqa: E402
from proyecto3.src.data_freshness import fx_coverage_check, load_fx_coverage_inputs  # noqa: E402


def test_coverage_inputs_per_currency_and_unknown_count(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    for t in ("fund_master", "fund_nav_monthly", "series_macro"):
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.execute("CREATE TABLE fund_master (isin text PRIMARY KEY, fund_currency varchar(3), in_current_universe smallint)")
    conn.execute("CREATE TABLE fund_nav_monthly (isin text, date date, nav double precision, PRIMARY KEY (isin, date))")
    conn.execute("CREATE TABLE series_macro (date date, indicator text, geography text, value double precision)")
    conn.execute("""INSERT INTO fund_master VALUES
        ('U1','USD',1), ('U2','usd',1), ('U3','USD',0), ('G1','GBP',1), ('E1','EUR',1), ('N1',NULL,1), ('N2',NULL,0), ('N3',NULL,1)""")
    conn.execute("""INSERT INTO fund_nav_monthly VALUES
        ('U1','2026-09-30',1), ('U2','2026-10-07',1), ('U3','2026-12-31',1), ('G1','2026-09-30',1), ('E1','2026-10-07',1),
        ('N1','2026-09-30',1), ('N2','2026-09-30',1)""")                    # N3 has no NAV: not counted
    conn.execute("INSERT INTO series_macro VALUES ('2026-10-06', %s, 'USD', 1.13), ('2026-10-02', %s, 'USD', 1.14), "
                 "('2026-10-31', %s, 'GBP', 0.85)", (EUR_FX_DAILY_INDICATOR, EUR_FX_DAILY_INDICATOR, EUR_FX_MONTHLY_INDICATOR))

    per_ccy, unknown = load_fx_coverage_inputs(conn)
    assert per_ccy == {"USD": (date(2026, 10, 7), date(2026, 10, 6), 2),   # retired U3 ignored; lowercase 'usd' folded
                       "GBP": (date(2026, 9, 30), None, 1)}                # monthly-only rates are not daily coverage
    assert unknown == 1
    verdict = fx_coverage_check(per_ccy, unknown, 7)
    assert not verdict.ok and "GBP: no ECB rates" in verdict.detail and "USD" not in verdict.detail.split(" -- ")[0]
