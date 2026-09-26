# proyecto1/tests/test_benchmark_loader_hardening_pg.py
# -*- coding: utf-8 -*-
"""
Regression + hardening tests for src.loaders.benchmark_loader (2026-09-26 post-cutover incident).

Incident: P1_P2_Complete.bat aborted in PASO 1 because run_gap_analysis() selected a non-aggregated
column with GROUP BY (SQLite tolerates it, Postgres raises GroupingError). The loader was then
hardened: payload-shape validation (pydantic) separate from junk-string detection, a Postgres-only
negative cache with backoff, an anomaly circuit-breaker, an endpoint probe, and telemetry rows in
ingestion_log. Pure tests run anywhere; the `_pg` ones need FONDOS_TEST_PG_DSN.

The functions under test call conn.commit(), so the PG tests run on `pg_conn_module_schema` (a
throwaway schema) and switch the session connection out of autocommit for the duration — the
production connection is non-autocommit and log_ingestion() relies on SAVEPOINT.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_LOADERS_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "src", "loaders"))
_P1_DIR = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
if _P1_DIR not in sys.path:
    sys.path.insert(0, _P1_DIR)

_spec = importlib.util.spec_from_file_location(
    "benchmark_loader_hardening_test", os.path.join(_LOADERS_DIR, "benchmark_loader.py")
)
_bl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bl)


# ─── shared helpers ──────────────────────────────────────────────────────────────────────────────

def _res(kind: str, name: str | None = None, **kw) -> dict:
    """A _fetch_benchmark_direct-shaped result."""
    d = {"benchmark_name": name, "raw_text": None, "error": None, "kind": kind,
         "rejected_raw": None, "schema_error": None}
    d.update(kw)
    return d


def _fake_fetch(mapping: dict, default: dict | None = None):
    calls: list[str] = []

    def fetch(ms_id: str) -> dict:
        calls.append(ms_id)
        return mapping.get(ms_id, default if default is not None else _res("NONE"))

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


class _FakeResponse:
    def __init__(self, status=200, payload=None, json_error=False):
        self.status_code = status
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._payload


# ─── pure tests: semantic classification ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("MSCI World Index",  ("MSCI World Index", "OK")),
    ("  S&P 500 TR  ",    ("S&P 500 TR", "OK")),
    (None,                (None, "NONE")),
    ("",                  (None, "NONE")),
    ("   ",               (None, "NONE")),
    ("None",              (None, "PLACEHOLDER")),
    ("NULL",              (None, "PLACEHOLDER")),
    ("n/a",               (None, "PLACEHOLDER")),
    ("TBD",               (None, "PLACEHOLDER")),
    ("Unclassified",      (None, "PLACEHOLDER")),
    ("Not Benchmarked",   (None, "PLACEHOLDER")),
    ("--",                (None, "PLACEHOLDER")),
    ("ABC",               (None, "PLACEHOLDER")),      # < 4 chars
    ("1234",              (None, "PLACEHOLDER")),      # no letter
    ("****",              (None, "PLACEHOLDER")),      # punctuation only
])
def test_classify_index_name(raw, expected):
    assert _bl._classify_index_name(raw) == expected


def test_negative_backoff_is_7_30_90_then_stays_90():
    now = datetime(2026, 9, 26, tzinfo=timezone.utc)
    days = [(_bl._negative_next_check(n, now) - now).days for n in (1, 2, 3, 4, 10)]
    assert days == [7, 30, 90, 90, 90]


# ─── pure tests: payload SHAPE (pydantic) is separate from junk detection ────────────────────────

def _fetch_with(monkeypatch, response):
    monkeypatch.setattr(_bl.requests, "get", lambda *a, **k: response)
    return _bl._fetch_benchmark_direct("F00000TEST")


def test_fetch_valid_payload(monkeypatch):
    r = _fetch_with(monkeypatch, _FakeResponse(payload={"indexName": "MSCI World Index",
                                                         "categoryName": "Global Large-Cap Blend Equity"}))
    assert r["kind"] == "OK" and r["benchmark_name"] == "MSCI World Index"
    assert r["error"] is None and r["schema_error"] is None
    assert r["raw_text"] == "Global Large-Cap Blend Equity"


@pytest.mark.parametrize("payload", [{}, {"indexName": None}, {"indexName": ""}, {"other": 1}])
def test_fetch_no_benchmark_shapes_are_none_not_errors(monkeypatch, payload):
    r = _fetch_with(monkeypatch, _FakeResponse(payload=payload))
    assert r["kind"] == "NONE" and r["benchmark_name"] is None
    assert r["error"] is None and r["schema_error"] is None


def test_fetch_placeholder_is_reported_with_raw_value(monkeypatch):
    r = _fetch_with(monkeypatch, _FakeResponse(payload={"indexName": "TBD"}))
    assert r["kind"] == "PLACEHOLDER" and r["benchmark_name"] is None
    assert r["rejected_raw"] == "TBD"


@pytest.mark.parametrize("payload", [
    {"indexName": {"name": "MSCI World"}},   # object where a string is expected
    {"indexName": ["MSCI World"]},           # list
    {"indexName": 12345},                    # number is NOT coerced to a string
    [],                                       # body is not an object
    "not an object",
])
def test_fetch_wrong_shape_is_a_schema_error_never_no_benchmark(monkeypatch, payload):
    r = _fetch_with(monkeypatch, _FakeResponse(payload=payload))
    assert r["kind"] == "SCHEMA" and r["schema_error"]
    assert r["benchmark_name"] is None and r["error"] is None


def test_fetch_non_json_body_is_a_schema_error(monkeypatch):
    r = _fetch_with(monkeypatch, _FakeResponse(json_error=True))
    assert r["kind"] == "SCHEMA"


def test_fetch_http_error_is_retryable_error_not_schema(monkeypatch):
    r = _fetch_with(monkeypatch, _FakeResponse(status=503))
    assert r["kind"] == "ERROR" and r["error"] == "HTTP 503"


# ─── PG fixtures ─────────────────────────────────────────────────────────────────────────────────

@contextmanager
def _non_autocommit(conn):
    """pg_conn_module_schema yields an autocommit connection; production uses a normal one (and
    log_ingestion() opens a SAVEPOINT, which autocommit rejects)."""
    conn.autocommit = False
    try:
        yield
    finally:
        conn.rollback()
        conn.autocommit = True


def _make_schema(conn, schema: str) -> None:
    conn.execute(f"SET search_path = {schema}")
    conn.execute("CREATE TABLE fund_master (isin text PRIMARY KEY, benchmark_declared text)")
    conn.execute("""
        CREATE TABLE fund_benchmarks (
            isin text NOT NULL, source text NOT NULL, benchmark_raw text, benchmark_id text,
            benchmark_name text, provider text, asset_class text, confidence text,
            benchmark_role text DEFAULT 'asset_proxy', extracted_at text,
            PRIMARY KEY (isin, source)
        )""")
    conn.execute("""
        CREATE TABLE nav_sources (isin text PRIMARY KEY, source text, source_id text, status text)""")
    conn.execute("""
        CREATE TABLE ingestion_log (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            isin text, step text, status text, message text, created_at timestamptz)""")
    conn.execute("""
        CREATE TABLE benchmark_ms_checks (
            isin text PRIMARY KEY, n_misses integer NOT NULL,
            last_checked_at timestamptz NOT NULL, next_check_at timestamptz NOT NULL)""")


def _add_nav(conn, *isins):
    for i in isins:
        conn.execute("INSERT INTO nav_sources (isin, source, source_id, status) "
                     "VALUES (%s, 'MORNINGSTAR', %s, 'OK')", (i, f"MS_{i}"))


def _cache(conn) -> dict:
    return {r[0]: r[1] for r in conn.execute("SELECT isin, n_misses FROM benchmark_ms_checks")}


def _events(conn, status=None) -> list:
    sql = "SELECT isin, status, message FROM ingestion_log WHERE step = 'BENCH_MS'"
    if status:
        return conn.execute(sql + " AND status = %s ORDER BY id", (status,)).fetchall()
    return conn.execute(sql + " ORDER BY id").fetchall()


def _run(conn, isins, fetch):
    return _bl.run_benchmark_load(conn, [(i, f"MS_{i}") for i in isins], fetch=fetch,
                                  verbose=False, throttle=False)


# ─── PG: the incident regression ─────────────────────────────────────────────────────────────────

def test_run_gap_analysis_runs_on_postgres(pg_conn, capsys):
    """The exact failure of 2026-09-26: `SELECT benchmark_id, benchmark_name, COUNT(*) ... GROUP BY
    benchmark_id` raised GroupingError on Postgres. Read-only, so the bare pg_conn is safe."""
    pg_conn.execute("CREATE TABLE fund_master (isin text PRIMARY KEY, benchmark_declared text)")
    pg_conn.execute("""
        CREATE TABLE fund_benchmarks (
            isin text NOT NULL, source text NOT NULL, benchmark_raw text, benchmark_id text,
            benchmark_name text, provider text, asset_class text, confidence text,
            benchmark_role text DEFAULT 'asset_proxy', extracted_at text, PRIMARY KEY (isin, source))""")
    pg_conn.execute("""
        CREATE TABLE ingestion_log (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            isin text, step text, status text, message text, created_at timestamptz)""")
    pg_conn.execute("INSERT INTO fund_master VALUES ('A','X'), ('B',NULL), ('C',NULL)")
    pg_conn.execute("""
        INSERT INTO fund_benchmarks (isin, source, benchmark_raw, benchmark_id, benchmark_name) VALUES
        ('A','MORNINGSTAR','MSCI World','MSCI_WORLD','MSCI World Index'),
        ('B','MORNINGSTAR','MSCI World','MSCI_WORLD','MSCI World Index'),
        ('C','MORNINGSTAR','S&P 500','SP500','S&P 500 Index')""")

    _bl.run_gap_analysis(pg_conn)

    out = capsys.readouterr().out
    assert "   2x  MSCI_WORLD" in out and "   1x  SP500" in out
    assert "WARN" not in out.split("Top 10")[1].split("Eventos")[0]   # 1 name per id: no anomaly flag


def test_run_gap_analysis_flags_an_id_with_two_names(pg_conn, capsys):
    pg_conn.execute("CREATE TABLE fund_master (isin text PRIMARY KEY, benchmark_declared text)")
    pg_conn.execute("""
        CREATE TABLE fund_benchmarks (
            isin text NOT NULL, source text NOT NULL, benchmark_raw text, benchmark_id text,
            benchmark_name text, provider text, asset_class text, confidence text,
            benchmark_role text DEFAULT 'asset_proxy', extracted_at text, PRIMARY KEY (isin, source))""")
    pg_conn.execute("""
        CREATE TABLE ingestion_log (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            isin text, step text, status text, message text, created_at timestamptz)""")
    pg_conn.execute("INSERT INTO fund_master VALUES ('A',NULL), ('B',NULL)")
    pg_conn.execute("""
        INSERT INTO fund_benchmarks (isin, source, benchmark_id, benchmark_name) VALUES
        ('A','MORNINGSTAR','ID1','Name One'), ('B','MORNINGSTAR','ID1','Name Two')""")
    _bl.run_gap_analysis(pg_conn)
    assert "[WARN 2 nombres/id]" in capsys.readouterr().out


# ─── PG: negative cache ──────────────────────────────────────────────────────────────────────────

def test_get_isins_skips_cached_negatives_until_next_check(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _make_schema(conn, pg_conn_module_schema)
    _add_nav(conn, "AAA", "BBB", "CCC")
    now = datetime.now(timezone.utc)
    conn.execute("INSERT INTO benchmark_ms_checks VALUES ('AAA', 1, %s, %s)",
                 (now, now + timedelta(days=5)))    # still cached
    conn.execute("INSERT INTO benchmark_ms_checks VALUES ('BBB', 1, %s, %s)",
                 (now - timedelta(days=9), now - timedelta(days=2)))   # due again

    with _non_autocommit(conn):
        assert [i for i, _ in _bl._get_isins_for_load(conn, only_missing=True)] == ["BBB", "CCC"]
        assert [i for i, _ in _bl._get_isins_for_load(
            conn, only_missing=True, recheck_negatives=True)] == ["AAA", "BBB", "CCC"]
        # `load` mode (only_missing False) is never filtered by the cache
        assert len(_bl._get_isins_for_load(conn, only_missing=False)) == 3


def test_record_negative_backs_off_and_clear_removes(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _make_schema(conn, pg_conn_module_schema)
    _add_nav(conn, "AAA")
    now = datetime.now(timezone.utc)
    with _non_autocommit(conn):
        seen = []
        for _ in range(3):
            _bl._record_negative(conn, "AAA", now)
            n, nxt = conn.execute(
                "SELECT n_misses, next_check_at FROM benchmark_ms_checks WHERE isin = 'AAA'").fetchone()
            seen.append((n, (nxt - now).days))
        assert seen == [(1, 7), (2, 30), (3, 90)]
        _bl._clear_negative(conn, "AAA")
        assert _cache(conn) == {}


def test_load_records_misses_and_clears_a_found_benchmark(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _make_schema(conn, pg_conn_module_schema)
    _add_nav(conn, "MISS", "HIT")
    now = datetime.now(timezone.utc)
    conn.execute("INSERT INTO benchmark_ms_checks VALUES ('HIT', 2, %s, %s)", (now, now))
    fetch = _fake_fetch({"MS_HIT": _res("OK", "MSCI World Index")})
    with _non_autocommit(conn):
        counters = _run(conn, ["MISS", "HIT"], fetch)
        assert counters["ok"] == 1 and counters["no_benchmark"] == 1
        assert counters["negatives_recorded"] == 1
        assert _cache(conn) == {"MISS": 1}                       # HIT was cleared
        assert conn.execute("SELECT source FROM fund_benchmarks WHERE isin = 'HIT'").fetchone()[0] \
            == "MORNINGSTAR"


def test_placeholder_is_counted_logged_and_treated_as_no_benchmark(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _make_schema(conn, pg_conn_module_schema)
    _add_nav(conn, "PLC")
    fetch = _fake_fetch({"MS_PLC": _res("PLACEHOLDER", rejected_raw="TBD")})
    with _non_autocommit(conn):
        counters = _run(conn, ["PLC"], fetch)
        assert counters["placeholder"] == 1 and counters["no_benchmark"] == 1
        assert _events(conn, "PLACEHOLDER") == [("PLC", "PLACEHOLDER", "TBD")]
        assert _cache(conn) == {"PLC": 1}
        assert conn.execute("SELECT COUNT(*) FROM fund_benchmarks").fetchone()[0] == 0


# ─── PG: endpoint probe, schema errors, circuit-breaker ─────────────────────────────────────────

def test_probe_failure_suppresses_negatives(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _make_schema(conn, pg_conn_module_schema)
    _add_nav(conn, "K1", "K2", "NEW")
    conn.execute("""INSERT INTO fund_benchmarks (isin, source, benchmark_raw, benchmark_id)
                    VALUES ('K1','MORNINGSTAR','x','ID1'), ('K2','MORNINGSTAR','y','ID2')""")
    fetch = _fake_fetch({}, default=_res("NONE"))     # endpoint stopped returning indexName
    with _non_autocommit(conn):
        counters = _run(conn, ["NEW"], fetch)
        assert counters["probe"] == "failed"
        assert counters["no_benchmark"] == 1 and counters["negatives_recorded"] == 0
        assert _cache(conn) == {}


def test_probe_passes_when_a_known_isin_still_resolves(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _make_schema(conn, pg_conn_module_schema)
    _add_nav(conn, "K1", "K2", "NEW")
    conn.execute("""INSERT INTO fund_benchmarks (isin, source, benchmark_raw, benchmark_id)
                    VALUES ('K1','MORNINGSTAR','x','ID1'), ('K2','MORNINGSTAR','y','ID2')""")
    fetch = _fake_fetch({"MS_K1": _res("OK", "MSCI World Index")}, default=_res("NONE"))
    with _non_autocommit(conn):
        counters = _run(conn, ["NEW"], fetch)
        assert counters["probe"] == "ok"
        assert _cache(conn) == {"NEW": 1}


def test_probe_is_skipped_on_a_fresh_database_and_the_cache_still_works(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _make_schema(conn, pg_conn_module_schema)
    _add_nav(conn, "NEW")
    with _non_autocommit(conn):
        counters = _run(conn, ["NEW"], _fake_fetch({}))
        assert counters["probe"] == "skipped"
        assert _cache(conn) == {"NEW": 1}


def test_schema_errors_are_never_cached_and_stop_negatives_after_the_limit(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _make_schema(conn, pg_conn_module_schema)
    limit = _bl.BENCH_SCHEMA_ERROR_LIMIT
    bad = [f"BAD{i:02d}" for i in range(limit + 1)]
    _add_nav(conn, *bad, "AFTER")
    fetch = _fake_fetch({f"MS_{i}": _res("SCHEMA", schema_error="indexName: bad type") for i in bad},
                        default=_res("NONE"))
    with _non_autocommit(conn):
        counters = _run(conn, [*bad, "AFTER"], fetch)
        assert counters["schema_error"] == limit + 1
        assert len(_events(conn, "SCHEMA")) == limit + 1
        # none of the schema errors is cached, and once the limit is exceeded a later genuine
        # "no benchmark" is not cached either (the endpoint contract is in doubt)
        assert _cache(conn) == {}
        assert counters["no_benchmark"] == 1 and counters["negatives_recorded"] == 0


def test_breaker_quarantines_a_new_name_returned_for_many_funds(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _make_schema(conn, pg_conn_module_schema)
    n = _bl.BENCH_ANOMALY_MIN_FUNDS + 5
    isins = [f"NEW{i:03d}" for i in range(n)]
    _add_nav(conn, *isins)
    fetch = _fake_fetch({f"MS_{i}": _res("OK", "Brand New Vendor Junk Label") for i in isins})
    with _non_autocommit(conn):
        counters = _run(conn, isins, fetch)
        written = conn.execute("SELECT COUNT(*) FROM fund_benchmarks").fetchone()[0]
        assert written == _bl.BENCH_ANOMALY_MIN_FUNDS          # the rest were held back
        assert counters["quarantined"] == n - _bl.BENCH_ANOMALY_MIN_FUNDS
        anomalies = _events(conn, "ANOMALY")
        assert len(anomalies) == 1 and anomalies[0][0] is None
        assert "Brand New Vendor Junk Label" in anomalies[0][2]
        assert _cache(conn) == {}                                # quarantined funds retry next run


def test_breaker_never_trips_on_an_already_known_benchmark(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _make_schema(conn, pg_conn_module_schema)
    conn.execute("""INSERT INTO fund_benchmarks (isin, source, benchmark_raw, benchmark_id)
                    VALUES ('SEED','MORNINGSTAR','MSCI World Index','MSCI_WORLD')""")
    n = _bl.BENCH_ANOMALY_MIN_FUNDS + 15
    isins = [f"BULK{i:03d}" for i in range(n)]
    _add_nav(conn, *isins)
    fetch = _fake_fetch({f"MS_{i}": _res("OK", "MSCI World Index") for i in isins})
    with _non_autocommit(conn):
        counters = _run(conn, isins, fetch)
        assert counters["quarantined"] == 0 and counters["ok"] == n
        assert _events(conn, "ANOMALY") == []


# ─── network guard (2026-09-26: an outage made 610 of 687 calls fail and the run exited 0) ───────

def _c(**kw):
    base = {"error": 0, "attempted": 0, "aborted": False, "total": 0}
    base.update(kw)
    return base


def test_verdict_is_none_for_a_healthy_run_and_for_a_few_isolated_errors():
    assert _bl._network_verdict(_c(error=0, attempted=687)) is None
    assert _bl._network_verdict(_c(error=2, attempted=30)) is None       # 6.7% <= 10%
    assert _bl._network_verdict(_c(error=5, attempted=10)) is None       # too few calls to judge a rate


def test_verdict_flags_a_failure_rate_over_the_limit():
    v = _bl._network_verdict(_c(error=610, attempted=687))
    assert v and "610 de 687" in v
    assert _bl._network_verdict(_c(error=4, attempted=30)) is not None   # 13%
    assert _bl._network_verdict(_c(error=3, attempted=30)) is None       # exactly 10%: allowed


def test_verdict_flags_an_aborted_run_whatever_the_rate():
    assert "detenida" in _bl._network_verdict(_c(error=10, attempted=10, aborted=True))


def test_an_outage_stops_the_run_after_the_consecutive_limit_without_caching_anything(
    pg_session_conn, pg_conn_module_schema,
):
    conn = pg_session_conn
    _make_schema(conn, pg_conn_module_schema)
    isins = [f"OUT{i:03d}" for i in range(_bl.BENCH_CONSECUTIVE_ERROR_LIMIT + 15)]
    _add_nav(conn, *isins)
    fetch = _fake_fetch({}, default=_res("ERROR", error="HTTPSConnectionPool: Max retries exceeded"))
    with _non_autocommit(conn):
        counters = _run(conn, isins, fetch)
        assert counters["aborted"] is True
        assert counters["attempted"] == _bl.BENCH_CONSECUTIVE_ERROR_LIMIT       # stopped early
        assert len(fetch.calls) == _bl.BENCH_CONSECUTIVE_ERROR_LIMIT
        assert _cache(conn) == {}                                               # errors never cached
        assert _bl._network_verdict(counters)


def test_isolated_errors_do_not_stop_the_run(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _make_schema(conn, pg_conn_module_schema)
    isins = [f"MIX{i:03d}" for i in range(30)]
    _add_nav(conn, *isins)
    # every 5th call fails: never 10 in a row, so no abort; 6/30 = 20% > 10% so the verdict still fails
    mapping = {f"MS_{i}": _res("ERROR", error="HTTP 503") for n, i in enumerate(isins) if n % 5 == 4}
    with _non_autocommit(conn):
        counters = _run(conn, isins, _fake_fetch(mapping, default=_res("NONE")))
        assert counters["aborted"] is False and counters["attempted"] == 30 and counters["error"] == 6
        assert _bl._network_verdict(counters)


def test_a_success_between_errors_resets_the_consecutive_counter(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _make_schema(conn, pg_conn_module_schema)
    limit = _bl.BENCH_CONSECUTIVE_ERROR_LIMIT
    isins = [f"RST{i:03d}" for i in range(2 * limit)]
    _add_nav(conn, *isins)
    mapping = {f"MS_{i}": _res("ERROR", error="HTTP 503")
               for n, i in enumerate(isins) if n != limit - 1}       # limit-1 errors, 1 success, then errors
    mapping = {k: v for k, v in mapping.items()}
    with _non_autocommit(conn):
        counters = _run(conn, isins, _fake_fetch(mapping, default=_res("NONE")))
        # limit-1 consecutive errors, one success, then limit more consecutive errors -> aborts in the 2nd streak
        assert counters["aborted"] is True and counters["attempted"] == (limit - 1) + 1 + limit
