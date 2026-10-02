# proyecto2/tests/calculations/test_m2_global_builder_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration Phase 5c (2026-09-20) — dedicated regression test for
src/calculations/m2_global_builder.py::build_m2_global(). Commits internally (two DELETE +
executemany-upsert blocks against series_macro), so this uses pg_conn_module_schema/pg_session_conn
-- never bare pg_conn/SAVEPOINT -- same discipline as every other commit-owning function in this
migration. This module had zero test coverage (SQLite or Postgres) before this port.
"""
from __future__ import annotations

from src.calculations.m2_global_builder import build_m2_global


def _make_series_macro(conn):
    conn.execute("""
        CREATE TABLE series_macro (
            date        date NOT NULL,
            indicator   text NOT NULL,
            geography   text NOT NULL,
            value       double precision,
            unit        text,
            source      text,
            load_ts     timestamptz DEFAULT now(),
            PRIMARY KEY (date, indicator, geography)
        )
    """)


def _seed_minimal_m2_inputs(conn):
    """US + EU levels + fx, enough for build_m2_global to compute a global YoY series (needs >=13
    months of core components to produce a first pct_change(12) row)."""
    # 14 consecutive month-end dates (avoids day-in-month edge cases).
    dates = [
        "2024-01-31", "2024-02-29", "2024-03-31", "2024-04-30", "2024-05-31", "2024-06-30",
        "2024-07-31", "2024-08-31", "2024-09-30", "2024-10-31", "2024-11-30", "2024-12-31",
        "2025-01-31", "2025-02-28",
    ]
    for idx, d in enumerate(dates):
        us_level = 21000.0 + idx * 10
        eu_level = 15000.0 + idx * 8   # millones EUR
        fx = 1.08
        conn.execute(
            "INSERT INTO series_macro (date, indicator, geography, value, unit, source) "
            "VALUES (%s, 'm2_level', 'US', %s, 'usd_bn', 'FRED')",
            (d, us_level),
        )
        conn.execute(
            "INSERT INTO series_macro (date, indicator, geography, value, unit, source) "
            "VALUES (%s, 'm2_level', 'EU', %s, 'eur_mn', 'BCE')",
            (d, eu_level),
        )
        conn.execute(
            "INSERT INTO series_macro (date, indicator, geography, value, unit, source) "
            "VALUES (%s, 'fx_usd_eur', 'GLOBAL', %s, 'ratio', 'FRED')",
            (d, fx),
        )


def test_build_m2_global_persists_and_is_rerunnable(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_series_macro(conn)
    _seed_minimal_m2_inputs(conn)

    n1 = build_m2_global(conn, dry_run=False)
    assert n1 > 0

    rows = conn.execute(
        "SELECT date, value, unit, source FROM series_macro "
        "WHERE indicator='m2_global_yoy' AND geography='GLOBAL' ORDER BY date"
    ).fetchall()
    assert len(rows) >= 1
    assert all(r[2] == "pct" and r[3] == "CALC" for r in rows)

    us_yoy = conn.execute(
        "SELECT COUNT(*) FROM series_macro WHERE indicator='m2_yoy' AND geography='US' AND source='CALC'"
    ).fetchone()[0]
    assert us_yoy >= 1

    # Rerun (DELETE + reinsert) must not duplicate or error — idempotent at the row-count level.
    n2 = build_m2_global(conn, dry_run=False)
    assert n2 == n1
    rows2 = conn.execute(
        "SELECT COUNT(*) FROM series_macro WHERE indicator='m2_global_yoy' AND geography='GLOBAL'"
    ).fetchone()[0]
    assert rows2 == len(rows)


def test_build_m2_global_dry_run_writes_nothing(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_series_macro(conn)
    _seed_minimal_m2_inputs(conn)

    n = build_m2_global(conn, dry_run=True)
    assert n > 0
    count = conn.execute(
        "SELECT COUNT(*) FROM series_macro WHERE indicator='m2_global_yoy'"
    ).fetchone()[0]
    assert count == 0


# ---------------------------------------------------------------------------
# FND-0176: refresh a stale / zero-filled m2_global_yoy, not only a missing one
# ---------------------------------------------------------------------------

def test_m2_global_is_degenerate_rules():
    import pandas as pd
    from src.calculations.m2_global_builder import m2_global_is_degenerate

    d = list(pd.date_range("2024-01-31", periods=14, freq="ME"))
    latest = pd.Timestamp("2025-02-28")
    ok = [1.5] * 14
    assert m2_global_is_degenerate(d, ok, latest) == (False, "")
    assert m2_global_is_degenerate([], [], latest)[0] is True                        # absent
    stale = m2_global_is_degenerate(d[:8], ok[:8], latest)                            # ends 6 months early
    assert stale[0] is True and "desfasada" in stale[1]
    zeros = m2_global_is_degenerate(d, [1.0, 2.0] + [0.0] * 12, latest)               # live case: zero tail
    assert zeros[0] is True and "cero" in zeros[1]
    assert m2_global_is_degenerate(d, [0.0] * 5 + [1.0] * 9, latest)[0] is False      # old zeros, healthy tail
    assert m2_global_is_degenerate(d, [0.0] * 11 + [1.0] * 3, latest)[0] is False     # zero run shorter than 12


def test_only_global_refresh_replaces_degenerate_series_and_leaves_m2_yoy(pg_session_conn, pg_conn_module_schema):
    from src.calculations.m2_global_builder import m2_global_needs_rebuild

    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_series_macro(conn)
    _seed_minimal_m2_inputs(conn)
    for d in ("2024-03-31", "2024-04-30", "2024-05-31", "2024-06-30", "2024-07-31", "2024-08-31",
              "2024-09-30", "2024-10-31", "2024-11-30", "2024-12-31", "2025-01-31", "2025-02-28"):
        conn.execute(
            "INSERT INTO series_macro (date, indicator, geography, value, unit, source) "
            "VALUES (%s, 'm2_global_yoy', 'GLOBAL', 0.0, 'pct', 'CALC')", (d,))
    conn.execute(
        "INSERT INTO series_macro (date, indicator, geography, value, unit, source) "
        "VALUES ('2024-02-29', 'm2_yoy', 'US', 99.0, 'pct', 'CALC')")

    assert m2_global_needs_rebuild(conn)[0] is True                                   # 12 exact zeros at the tail

    n = build_m2_global(conn, dry_run=False, only_global=True)
    assert n > 0
    vals = [r[0] for r in conn.execute(
        "SELECT value FROM series_macro WHERE indicator='m2_global_yoy' AND geography='GLOBAL'").fetchall()]
    assert vals and any(abs(v) > 1e-9 for v in vals)                                  # real values replaced the zeros
    assert conn.execute(                                                              # m2_yoy US untouched
        "SELECT value FROM series_macro WHERE indicator='m2_yoy' AND geography='US'").fetchone()[0] == 99.0
    assert m2_global_needs_rebuild(conn)[0] is False


def test_japan_plain_yen_level_does_not_swamp_global_yoy(pg_session_conn, pg_conn_module_schema):
    """FND-0176: MYAGM2JPM189N is in plain JPY (~9e14). The builder used to divide by 1_000, making Japan
    ~6e9 'bn USD' and the global YoY ~0 whenever Japan was flat. With a flat JP level and growing
    US/EU/CN, the global YoY must reflect the growing components (>5%), not Japan's constant."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_series_macro(conn)
    dates = ["2024-01-31", "2024-02-29", "2024-03-31", "2024-04-30", "2024-05-31", "2024-06-30",
             "2024-07-31", "2024-08-31", "2024-09-30", "2024-10-31", "2024-11-30", "2024-12-31",
             "2025-01-31", "2025-02-28"]
    for i, d in enumerate(dates):
        g = 1.01 ** i                                                   # ~12.7% a year
        rows = [("m2_level", "US", 21000.0 * g, "usd_bn"),
                ("m2_level", "EU", 15_000_000.0 * g, "eur_mn"),
                ("m2_level", "CN", 2.0e14 * g, "cny_mn"),               # plain CNY
                ("m2_level", "JP", 9.0e14, "jpy_mn"),                   # plain JPY, FLAT
                ("fx_usd_eur", "GLOBAL", 1.08, "ratio"),
                ("fx_cny_usd", "GLOBAL", 7.0, "ratio"),
                ("fx_jpy_usd", "GLOBAL", 150.0, "ratio")]
        for ind, geo, val, unit in rows:
            conn.execute("INSERT INTO series_macro (date, indicator, geography, value, unit, source) "
                         "VALUES (%s,%s,%s,%s,%s,'TEST')", (d, ind, geo, val, unit))

    assert build_m2_global(conn, dry_run=False, only_global=True) > 0
    last = conn.execute("SELECT value FROM series_macro WHERE indicator='m2_global_yoy' AND geography='GLOBAL' "
                        "ORDER BY date DESC LIMIT 1").fetchone()[0]
    assert last > 5.0, f"global YoY {last} ~ 0 means Japan swamps the sum (unit-scale bug)"


def test_series_tail_health_flags_zero_and_frozen_tails():
    from src.calculations.m2_global_builder import series_tail_health

    healthy = [1.0, 2.0, 3.0, 4.0] * 20
    h = series_tail_health(healthy)
    assert h[12]["zero_share"] == 0.0 and h[12]["std"] > 1.0 and h[60]["n"] == 60

    zero_tail = [5.0, -3.0, 8.0] * 30 + [0.0] * 60                    # plausible overall, dead tail (the live case)
    z = series_tail_health(zero_tail)
    assert z[12]["zero_share"] == 1.0 and z[12]["std"] == 0.0 and z[60]["zero_share"] == 1.0

    frozen = [1.0] * 5 + [2.5] * 60                                   # constant but not zero
    f = series_tail_health(frozen)
    assert f[12]["zero_share"] == 0.0 and f[12]["std"] == 0.0

    assert series_tail_health([])[12]["n"] == 0
