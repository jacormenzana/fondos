"""
tests/test_run_block_retired_modes.py -- the by-block / Excel-master path of run_block.py is RETIRED (FND-0247).

2026-10-08: one accidental run of `run_block.py --block monetarios --master <Excel>` rewrote 37 live fund_master rows (heuristic_block, management_company, natures)
and its universe reconcile, fed with the Excel master, re-activated ~277 retired funds. The guard refuses the path BEFORE any connection is opened (there is no
dry-run to fall back to and no override), and pipeline.run_block refuses nature_first=False for every caller, not only the CLI.
"""
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "proyecto1"):                      # P1 modules import each other as `core.*`
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import run_block as rb  # noqa: E402


class _Connected(Exception):
    """Raised by the stubbed get_connection: reaching it means the CLI got past every validation."""


def _boom():
    raise _Connected("get_connection() was called")


@pytest.fixture()
def cli(monkeypatch):
    monkeypatch.setattr(rb, "get_connection", _boom)

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["run_block.py", *argv])
        return rb.main()
    return run


# ---------------------------------------------------------------- the retired modes never reach the database
@pytest.mark.parametrize("argv", [
    ["--block", "monetarios", "--master", "x.xlsx"],            # the exact incident invocation
    ["--block", "mixtos", "--master-db"],                       # the block path with the sanctioned universe is retired too
    ["--nature-first", "--master", "x.xlsx"],                   # the Excel master as the universe, in any combination
    ["--block", "mixtos", "--master", "x.xlsx", "--list-isin", "LU0232465467", "--sample", "5"],
])
def test_block_and_excel_master_are_refused_before_any_connection(cli, capsys, argv):
    with pytest.raises(SystemExit) as e:
        cli(*argv)                                              # a _Connected would propagate as a different exception and fail this test
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "RETIRED" in err and "FND-0247" in err and "--nature-first --master-db" in err and "no connection was opened" in err


def test_a_mode_is_required_and_so_is_the_harvest_universe(cli, capsys):
    for argv in ([], ["--master-db"], ["--nature-first"]):
        with pytest.raises(SystemExit) as e:
            cli(*argv)
        assert e.value.code == 2, argv
    assert "--master-db is required" in capsys.readouterr().err


def test_family_refresh_still_excludes_a_passed_set(cli):
    for argv in (["--family-nature-refresh", "--master-db", "--list-isin", "A"], ["--family-nature-refresh", "--master-db", "--sample", "3"]):
        with pytest.raises(SystemExit) as e:
            cli(*argv)
        assert e.value.code == 2


# ---------------------------------------------------------------- the sanctioned modes still get through to the connection
@pytest.mark.parametrize("argv", [
    ["--nature-first", "--master-db"],
    ["--nature-first", "--master-db", "--list-isin", "LU0232465467,LU1873127366"],
    ["--family-nature-refresh", "--master-db"],
    ["--nature-first", "--master-db", "--recompute-costs", "--list-isin", "LU0232465467"],
])
def test_the_nature_first_modes_pass_validation_and_reach_the_connection(cli, argv):
    with pytest.raises(_Connected):
        cli(*argv)


# ---------------------------------------------------------------- the function refuses it for every caller
def test_pipeline_run_block_refuses_the_by_block_mode_without_touching_the_connection():
    from core.pipeline import run_block

    class Untouchable:
        def __getattr__(self, name):
            raise AssertionError(f"the connection was used: {name}")
    with pytest.raises(NotImplementedError, match="RETIRED.*FND-0247.*No database statement was executed"):
        run_block(None, None, Untouchable(), nature_first=False)


# ---------------------------------------------------------------- end to end, as a human would type it
def test_the_command_line_exits_2_and_prints_no_database_banner():
    r = subprocess.run([sys.executable, "-X", "utf8", "run_block.py", "--block", "monetarios", "--master", "x.xlsx"],
                       cwd=str(_ROOT / "proyecto1"), capture_output=True, text=True, errors="replace", timeout=120,
                       env={k: v for k, v in __import__("os").environ.items() if not k.startswith("FONDOS_")})
    assert r.returncode == 2, r.stdout + r.stderr
    assert "RETIRED" in r.stderr and "[DB] backend" not in r.stdout + r.stderr
