"""
proyecto1/conftest.py — re-registers the shared Postgres test fixtures (plan §5a,
shared/testing/pg_fixtures.py) for this project specifically.

Defensive addition (2026-09-20): proyecto1 has no pytest.ini today, so its tests currently inherit
the repo-root conftest.py's fixture registration for free (rootdir is the repo root when nothing
pins it elsewhere). That's a coincidence of the current directory layout, not a guarantee — the
moment anyone adds proyecto1/pytest.ini (for its own pythonpath/marker config, the same reason
proyecto2/pytest.ini and proyecto3/pytest.ini exist), rootdir pins to proyecto1/ and the repo-root
conftest.py silently stops loading (confirmed mechanism: proyecto2/conftest.py's own docstring).
That would break every one of proyecto1's PG-backed tests (test_sqlite_writer_pg_upsert.py,
test_p1_kiid_sync_pg.py, etc.) with "fixture 'pg_conn' not found" — a regression with no connection
to whatever change actually added the pytest.ini. This file closes that gap before it can happen.
"""

pytest_plugins = ["shared.testing.pg_fixtures"]
