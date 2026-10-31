# -*- coding: utf-8 -*-
"""
backlog_scratch.py -- the programmatic guard of the staging rule (Backlog v2, FND-0351; NORMAS_BACKLOG.md 9; owner mitigation 2026-10-28).

Synthetic testing that burns production sequence ids is BANNED: every script that alters state to simulate load or to replay history calls `assert_scratch(conn, who=...)` BEFORE its first
statement. It refuses unless the connection is NOT a live target read from the environment (host, port and database name of FONDOS_BACKLOG_PG_DSN, FONDOS_BACKLOG_PG_DSN_HUMAN, FONDOS_PG_DSN and the
owner DSN, with the localhost aliases equal) AND it is not on the SAME SERVER (host and port) as any live target AND its database name is on an exact allow-list (`fondos_test`, `backlog_staging_*`). A negative test such as "the name is not production" cannot work
here: the live backlog is called `gestion` (127.0.0.1:5436), so the identification is positive. A convention is forgotten and a typo bypasses it; this is checked by the code.

This module holds the guard so that every scenario shares ONE implementation (P#11); scripts/audit/backlog_phase_b_replay.py, its first user, re-exports these names.
"""
from __future__ import annotations

import os

SCRATCH_DBNAMES = ("fondos_test",)                 # the throwaway database of the hermetic container (scripts/ops/run_pg_tests.py)
SCRATCH_DBPREFIXES = ("backlog_staging_",)         # the staging databases of FND-0351
_LOCAL = {"localhost", "127.0.0.1", "::1", ""}
_LIVE_DSN_VARS = ("FONDOS_BACKLOG_PG_DSN", "FONDOS_BACKLOG_PG_DSN_HUMAN", "FONDOS_PG_DSN", "FONDOS_PG_DSN_OWNER")


def _norm_host(h: str) -> str:
    h = (h or "").strip().lower()
    return "local" if h in _LOCAL else h


def live_targets(environ: dict) -> set:
    """{(host, port, dbname)} of every live database this repository knows from its environment: the backlog (gestion) and the fondos store, read from the DSNs. Never prints a DSN."""
    from psycopg.conninfo import conninfo_to_dict
    out = set()
    for var in _LIVE_DSN_VARS:
        dsn = environ.get(var)
        if not dsn:
            continue
        d = conninfo_to_dict(dsn)
        names = {d.get("dbname") or ""}
        if var == "FONDOS_PG_DSN_OWNER":                      # the owner DSN is pointed at `gestion` for the backlog: both names are live
            names.add("gestion")
        for name in names:
            out.add((_norm_host(d.get("host", "")), str(d.get("port") or "5432"), name))
    return out


def scratch_verdict(host: str, port, dbname: str, live: set) -> tuple:
    """(ok, why): a database is a scratch one only if it is NOT a live target (host, port, name; localhost aliases equal) AND its name is on the allow-list (a positive identification:
    'not named production' is not enough, the live backlog is called gestion). Pure."""
    key = (_norm_host(host), str(port), dbname or "")
    if key in live or any(k[1] == key[1] and k[2] == key[2] for k in live):
        return False, f"{dbname!r} on port {port} is a LIVE database of this environment"
    if any(k[0] == key[0] and k[1] == key[1] for k in live):         # AGENTS.md: never share a server between tests and live data (WAL, roles, disk, locks)
        return False, f"{dbname!r} is on the SAME SERVER ({key[0]}:{port}) as a LIVE database: a scratch database never shares a server with live data"
    if dbname in SCRATCH_DBNAMES or any((dbname or "").startswith(p) for p in SCRATCH_DBPREFIXES):
        return True, "scratch"
    return False, f"{dbname!r} is not an allow-listed scratch database name {SCRATCH_DBNAMES + tuple(p + '*' for p in SCRATCH_DBPREFIXES)}"


def assert_scratch(conn, environ: dict | None = None, who: str = "backlog_phase_b_replay") -> None:
    """Refuse to go on unless `conn` is a scratch database: the caller alters state or calls the real routines, which is only ever acceptable on a throwaway copy. `who` names the caller in the
    message. Raises RuntimeError; touches nothing."""
    info = conn.info
    ok, why = scratch_verdict(info.host or "", info.port, info.dbname, live_targets(dict(os.environ) if environ is None else environ))
    if not ok:
        raise RuntimeError(f"{who} refuses to run: {why}")
