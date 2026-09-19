"""
proyecto3/conftest.py — re-registers the shared Postgres test fixtures (plan §5a,
shared/testing/pg_fixtures.py) for this project specifically. Same reasoning as
proyecto2/conftest.py: proyecto3/pytest.ini pins rootdir here when tests are invoked
`cd proyecto3 && python -m pytest tests/`, and pytest's conftest.py discovery does not climb
above rootdir — so the repo-root conftest.py alone is not enough.
"""

pytest_plugins = ["shared.testing.pg_fixtures"]
