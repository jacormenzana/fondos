# proyecto2/tests/utils/test_validate_nav_stale_flat_20261108.py
# -*- coding: utf-8 -*-
"""FND-0383: a monthly NAV that holds one value for 12+ months and then jumps 40%+ is a stale carry-forward, not a price.

R-7: only validators.py is imported (no pipeline, no DB). The real series that motivated it (LU0987206850: 61.2742 for 23 months, then 103.92) and the cohort it must NOT catch
(the Eastern Europe funds re-priced after a 5-9 month dealing suspension in 2022) are built as fixtures, with no ISIN in the code under test (P#5).
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for _p in (_ROOT, _ROOT / "proyecto2"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pandas as pd
import pytest

from src.utils.validators import validate_nav, validate_nav_stale_flat


def _series(values, start="2020-01-31"):
    return pd.DataFrame({"date": pd.date_range(start, periods=len(values), freq="ME"), "nav": [float(v) for v in values]})


def test_a_year_of_identical_navs_followed_by_a_seventy_percent_jump_is_rejected_and_the_message_names_it():
    nav = _series([50, 52, 51] + [61.2742] * 23 + [103.92279, 104.5, 105.0])
    assert validate_nav(nav)[0] is True                                     # the 8x test of validate_nav does not see it: that is the defect
    ok, msg = validate_nav_stale_flat(nav)
    assert ok is False and "23" in msg and "+70%" in msg and "61.2742" in msg


def test_a_downward_catch_up_after_a_year_of_flat_prices_is_rejected_too():
    ok, msg = validate_nav_stale_flat(_series([10] * 12 + [5.5, 5.6]))
    assert ok is False and "-45%" in msg


@pytest.mark.parametrize("flat", [3, 5, 9, 11])
def test_a_shorter_run_is_a_suspension_that_was_re_priced_for_real_and_passes(flat):
    assert validate_nav_stale_flat(_series([19.15] + [19.15] * (flat - 1) + [9.64, 9.7, 9.9])) == (True, None)


def test_a_flat_year_followed_by_an_ordinary_move_passes():
    assert validate_nav_stale_flat(_series([100] * 14 + [103, 104])) == (True, None)           # +3 %: a money-market style fund, not a catch-up
    assert validate_nav_stale_flat(_series([100] * 14 + [139, 140])) == (True, None)           # +39 % is under the cut


def test_the_cut_is_strict_on_both_parameters_and_a_flat_tail_without_a_jump_passes():
    assert validate_nav_stale_flat(_series([10] * 12 + [14.01]))[0] is False                   # exactly 12 flat months, +40.1 %
    assert validate_nav_stale_flat(_series([10] * 12 + [14.0])) == (True, None)               # +40.0 % is not more than 40 %
    assert validate_nav_stale_flat(_series([10] * 30)) == (True, None)                         # frozen to the end: that is STALE_FROZEN's business, not this rule


def test_a_series_not_longer_than_the_run_cannot_trigger_and_is_accepted():
    assert validate_nav_stale_flat(_series([10] * 6 + [30])) == (True, None)
    assert validate_nav_stale_flat(_series([])) == (True, None)


def test_the_kill_switch_and_the_parameters_are_honoured():
    nav = _series([10] * 12 + [20, 21])
    assert validate_nav_stale_flat(nav, enabled=False) == (True, None)
    assert validate_nav_stale_flat(nav, min_run=13) == (True, None)
    assert validate_nav_stale_flat(nav, min_jump=1.5) == (True, None)
    assert validate_nav_stale_flat(nav, min_run=12, min_jump=0.4, enabled=True)[0] is False


def test_the_defaults_come_from_the_config_and_the_pipeline_calls_the_check_on_the_monthly_series_only():
    from shared import config
    assert config.NAV_STALE_FLAT_ENABLED is True and config.NAV_STALE_FLAT_MIN_RUN == 12 and config.NAV_STALE_FLAT_MIN_JUMP == 0.40
    src = (_ROOT / "proyecto2" / "src" / "pipeline" / "run_pipeline.py").read_text(encoding="utf-8")
    assert src.count("validate_nav_stale_flat(") == 1                                          # the daily window (validate_nav(nav_sh)) keeps validate_nav alone
    assert "ok_sh, err_sh = validate_nav(nav_sh)" in src
