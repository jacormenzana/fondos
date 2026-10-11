# tests/test_release_check.py
"""scripts/launch/release_check.py: the release launchers refuse to recompute unless the release is in the CODE (pure, R-7)."""
import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts" / "launch"))

import release_check as rc  # noqa: E402


def _cfg(**over):
    base = {n: True for n in rc.ALL_SWITCHES}
    base.update(over)
    return SimpleNamespace(**base)


def test_ready_when_every_switch_is_true_and_coherent():
    assert rc.problems(_cfg(), [], "20261010") == []


def test_a_switch_left_off_is_reported_by_name():
    out = rc.problems(_cfg(EUR_NAV_CONVERSION_ENABLED=False), [], "20261010")
    assert out == ["EUR_NAV_CONVERSION_ENABLED is not True in shared/config.py"]


def test_a_truthy_non_bool_does_not_count_and_missing_names_are_reported():
    cfg = _cfg(ANNUALIZATION_INTERVAL_ENABLED=1)
    del cfg.SORTINO_MIN_DOWNSIDE_COUNT_ENABLED
    out = rc.problems(cfg, [], "20261010")
    assert len(out) == 2 and any("ANNUALIZATION" in p for p in out) and any("SORTINO" in p for p in out)


def test_coupling_errors_and_an_unreadable_version_block_the_release():
    out = rc.problems(_cfg(), ["coupling broken"], None)
    assert "coupling broken" in out and any("CALC_VERSION" in p for p in out)


def test_the_release_switches_are_the_released_set():
    assert rc.RELEASE_SWITCHES == ("EUR_NAV_CONVERSION_ENABLED", "FX_CONTRIBUTION_EUR_VIEW_ENABLED",
                                   "ANNUALIZATION_INTERVAL_ENABLED", "DEFLATION_MONTH_ALIGN_ENABLED",
                                   "SORTINO_MIN_DOWNSIDE_COUNT_ENABLED")


def test_the_later_by_dates_switch_is_part_of_the_check_and_reported_by_name():
    assert rc.LATER_SWITCHES == ("ANNUALIZATION_BY_DATES_ENABLED",)
    assert set(rc.ALL_SWITCHES) == set(rc.RELEASE_SWITCHES) | set(rc.LATER_SWITCHES)
    out = rc.problems(_cfg(ANNUALIZATION_BY_DATES_ENABLED=False), [], "20261011")
    assert out == ["ANNUALIZATION_BY_DATES_ENABLED is not True in shared/config.py"]
