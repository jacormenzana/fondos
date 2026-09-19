"""
Repo-root conftest.py — registers the shared Postgres test fixtures (plan §5a) for every test
suite under this repo (proyecto1/tests, proyecto2/tests, proyecto3/tests). pytest auto-discovers
conftest.py files in every parent directory of a collected test file up to the rootdir, so this
single file makes `pg_conn` / `pg_conn_module_schema` available everywhere without each project
needing its own copy.

This does NOT replace proyecto2/pytest.ini or proyecto3/pytest.ini (their `pythonpath = . ..`
settings still apply) — conftest.py and pytest.ini are independent, additive pytest mechanisms.

New here vs. pre-existing: nothing about the current SQLite-based suite changes by this file's
mere presence — `pg_conn` et al. are new, opt-in fixtures. A test that doesn't request them is
completely unaffected.
"""

pytest_plugins = ["shared.testing.pg_fixtures"]
