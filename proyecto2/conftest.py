"""
proyecto2/conftest.py — re-registers the shared Postgres test fixtures (plan §5a,
shared/testing/pg_fixtures.py) for this project specifically.

Why this duplicates the registration already in the repo-root conftest.py: pytest.ini's presence
in this directory makes pytest treat `proyecto2/` as the rootdir when tests are invoked the way
AGENTS.md documents (`cd proyecto2 && python -m pytest tests/`) — and pytest's conftest.py
discovery does not climb above rootdir (confcutdir defaults to rootdir), so the repo-root
conftest.py is silently NOT loaded in that invocation. Confirmed empirically 2026-09-18: `pytest
--fixtures` from within proyecto2 does not list `pg_conn` without this file. Without it, every
proyecto2 test ported onto the PG fixture (plan §5c) would fail with "fixture 'pg_conn' not
found" the moment someone runs tests the documented way, not the repo-root way.
"""

pytest_plugins = ["shared.testing.pg_fixtures"]
