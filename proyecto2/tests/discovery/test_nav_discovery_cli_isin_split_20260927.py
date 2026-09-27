# proyecto2/tests/discovery/test_nav_discovery_cli_isin_split_20260927.py
# -*- coding: utf-8 -*-
"""
FND-0096 (2026-09-27): `nav_discovery.py --mode recalculate-monthly --isin A,B` treated the whole
`--isin` value as ONE ISIN literally named "A,B" (`[args.isin.strip().upper()]`), instead of
splitting on comma like every other mode does. `--mode update` (the sibling branch right above it
in main()) already split correctly via the shared `isins` list built once near the top of main();
recalculate-monthly had its own, buggy, one-off wrapping instead of reusing it.

These tests exercise main()'s CLI dispatch directly (mocking get_connection/DB setup and
run_recalculate_monthly, since main() has no return value and talks to a real DB otherwise) rather
than only the already-correct run_recalculate_monthly() itself, since the bug was in how main()
called it, not in that function. main() also redirects sys.stdout/stderr to a file-backed _Tee for
the run's log — patched to a passthrough here so the test neither writes a real log file nor leaves
stdout/stderr permanently swapped if something goes wrong.

R-7 compliant: no pipeline.py / core.io imports.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_HERE = Path(__file__).parent
_P2_ROOT = _HERE.parent.parent  # proyecto2/
_REPO = _P2_ROOT.parent         # c:/desarrollo/fondos
for _p in (str(_P2_ROOT), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proyecto2.src.discovery import nav_discovery as nd  # noqa: E402


class _PassthroughTee:
    """Stands in for nav_discovery._Tee: no real log file, writes straight to the original stream."""
    def __init__(self, path, orig):
        self._orig = orig

    def write(self, s):
        self._orig.write(s)

    def flush(self):
        self._orig.flush()

    def fileno(self):
        return self._orig.fileno()

    def close(self):
        pass


def _run_main_with(argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["nav_discovery.py"] + argv)
    orig_stdout, orig_stderr = sys.stdout, sys.stderr
    fake_conn = MagicMock()
    try:
        with patch.object(nd, "get_connection", return_value=fake_conn), \
             patch.object(nd, "_ensure_data_status_column"), \
             patch.object(nd, "_Tee", _PassthroughTee), \
             patch.object(nd, "run_recalculate_monthly") as mock_rcm:
            nd.main()
    finally:
        sys.stdout, sys.stderr = orig_stdout, orig_stderr
    return mock_rcm


def test_recalculate_monthly_splits_a_comma_separated_isin_list(monkeypatch):
    mock_rcm = _run_main_with(
        ["--mode", "recalculate-monthly", "--isin", "lu1234567890,fr0011365212"], monkeypatch)
    mock_rcm.assert_called_once()
    _, kwargs = mock_rcm.call_args
    assert kwargs["isins"] == ["LU1234567890", "FR0011365212"], kwargs["isins"]


def test_recalculate_monthly_still_works_with_a_single_isin(monkeypatch):
    mock_rcm = _run_main_with(
        ["--mode", "recalculate-monthly", "--isin", "lu1234567890"], monkeypatch)
    _, kwargs = mock_rcm.call_args
    assert kwargs["isins"] == ["LU1234567890"]


def test_recalculate_monthly_with_no_isin_passes_none(monkeypatch):
    mock_rcm = _run_main_with(["--mode", "recalculate-monthly"], monkeypatch)
    _, kwargs = mock_rcm.call_args
    assert kwargs["isins"] is None
