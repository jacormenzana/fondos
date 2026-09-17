# proyecto3/tests/test_regime_classifier_tree.py
# -*- coding: utf-8 -*-
"""
Table-driven tests for RegimeClassifier._classify_row (proyecto3/src/regime_classifier.py).

Closes P3 optimization plan defect #15: _classify_row had zero unit tests anywhere
in the repo despite being the sole authority for every regime label the platform
persists (fund_metrics regime suffixes, portfolio_scenarios.macro_regime, the
monthly report, the backtester). This is the regression harness Phase 1 and
Phase 5 (orthogonalization) are validated against.

R-7: imports ONLY regime_classifier -- no pipeline.py, no core.io, no DB.
_classify_row is a pure function of 7 floats; every case here supplies exactly
the inputs that decide the branch under test and leaves every other input at a
value (or None) that cannot influence the outcome, so each test isolates one
edge of the decision tree in proyecto3/src/regime_classifier.py:285-340.

Run from repo root:
    python -m pytest proyecto3/tests/test_regime_classifier_tree.py -v
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]  # c:\desarrollo\fondos
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.regime_classifier import (
    _classify_row,
    OIL_SHOCK_THRESHOLD,
    IPC_HIGH_THRESHOLD,
    IPC_MOD_THRESHOLD,
    CLI_EXPANSION,
    RATE_LOW_THRESHOLD,
    SPREAD_HY_CRISIS,
    VIX_YOY_CRISIS,
)


# ============================================================
# 1. Crisis_Financiera override (highest priority; requires BOTH conditions)
# ============================================================

def test_crisis_financiera_fires_when_both_conditions_breached():
    result = _classify_row(
        oil_yoy=None, ipc_yoy_avg=None, cli_eu=None,
        rate_deposit=None, d_rate_3m=None,
        spread_hy=SPREAD_HY_CRISIS + 0.01, vix_yoy=VIX_YOY_CRISIS + 0.01,
    )
    assert result == "Crisis_Financiera"


def test_crisis_financiera_does_not_fire_on_vix_alone():
    # spread_hy breached, vix_yoy just under -> falls through to the base case
    # (oil below shock threshold, cli/ipc/rate all None -> Expansion).
    result = _classify_row(
        oil_yoy=0.0, ipc_yoy_avg=0.0, cli_eu=CLI_EXPANSION,
        rate_deposit=None, d_rate_3m=None,
        spread_hy=SPREAD_HY_CRISIS + 0.01, vix_yoy=VIX_YOY_CRISIS - 0.01,
    )
    assert result == "Expansion"


def test_crisis_financiera_does_not_fire_on_spread_alone():
    result = _classify_row(
        oil_yoy=0.0, ipc_yoy_avg=0.0, cli_eu=CLI_EXPANSION,
        rate_deposit=None, d_rate_3m=None,
        spread_hy=SPREAD_HY_CRISIS - 0.01, vix_yoy=VIX_YOY_CRISIS + 0.01,
    )
    assert result == "Expansion"


def test_crisis_financiera_skipped_when_either_input_missing():
    # Either signal None -> the "and" guard short-circuits -> crisis check
    # never runs, regardless of how extreme the other signal is.
    result = _classify_row(
        oil_yoy=0.0, ipc_yoy_avg=0.0, cli_eu=CLI_EXPANSION,
        rate_deposit=None, d_rate_3m=None,
        spread_hy=None, vix_yoy=VIX_YOY_CRISIS + 10.0,
    )
    assert result == "Expansion"


# ============================================================
# 2. Shock_Energetico override
# ============================================================

def test_shock_energetico_fires_above_threshold():
    result = _classify_row(
        oil_yoy=OIL_SHOCK_THRESHOLD + 0.0001, ipc_yoy_avg=None, cli_eu=None,
        rate_deposit=None, d_rate_3m=None,
    )
    assert result == "Shock_Energetico"


def test_shock_energetico_does_not_fire_at_or_below_threshold():
    result = _classify_row(
        oil_yoy=OIL_SHOCK_THRESHOLD - 0.0001, ipc_yoy_avg=0.0, cli_eu=CLI_EXPANSION,
        rate_deposit=None, d_rate_3m=None,
    )
    assert result == "Expansion"


# ============================================================
# 3. Weak cycle (CLI < 100): Estanflacion vs Contraccion
# ============================================================

def test_estanflacion_when_weak_cycle_and_ipc_above_mod_threshold():
    result = _classify_row(
        oil_yoy=0.0, ipc_yoy_avg=IPC_MOD_THRESHOLD + 0.0001,
        cli_eu=CLI_EXPANSION - 0.01,
        rate_deposit=None, d_rate_3m=None,
    )
    assert result == "Estanflacion"


def test_contraccion_when_weak_cycle_and_ipc_at_or_below_mod_threshold():
    result = _classify_row(
        oil_yoy=0.0, ipc_yoy_avg=IPC_MOD_THRESHOLD - 0.0001,
        cli_eu=CLI_EXPANSION - 0.01,
        rate_deposit=None, d_rate_3m=None,
    )
    assert result == "Contraccion"


def test_contraccion_when_weak_cycle_and_ipc_missing():
    result = _classify_row(
        oil_yoy=0.0, ipc_yoy_avg=None,
        cli_eu=CLI_EXPANSION - 0.01,
        rate_deposit=None, d_rate_3m=None,
    )
    assert result == "Contraccion"


# ============================================================
# 4. Positive cycle (CLI >= 100), IPC high: Recalentamiento[_Tardio]
# ============================================================

def test_recalentamiento_tardio_when_rates_rising():
    result = _classify_row(
        oil_yoy=0.0, ipc_yoy_avg=IPC_HIGH_THRESHOLD + 0.0001,
        cli_eu=CLI_EXPANSION,
        rate_deposit=None, d_rate_3m=0.25,
    )
    assert result == "Recalentamiento_Tardio"


def test_recalentamiento_when_rates_not_rising():
    result = _classify_row(
        oil_yoy=0.0, ipc_yoy_avg=IPC_HIGH_THRESHOLD + 0.0001,
        cli_eu=CLI_EXPANSION,
        rate_deposit=None, d_rate_3m=-0.25,
    )
    assert result == "Recalentamiento"


def test_recalentamiento_when_d_rate_missing():
    # d_rate_3m None -> "not None and not nan" guard fails -> falls to the
    # plain Recalentamiento branch, not _Tardio.
    result = _classify_row(
        oil_yoy=0.0, ipc_yoy_avg=IPC_HIGH_THRESHOLD + 0.0001,
        cli_eu=CLI_EXPANSION,
        rate_deposit=None, d_rate_3m=None,
    )
    assert result == "Recalentamiento"


def test_no_overheating_branch_at_or_below_ipc_high_threshold():
    # ipc not > 4% -> skips the whole "ciclo positivo alto" block -> falls to
    # the rate_deposit / base-case tail.
    result = _classify_row(
        oil_yoy=0.0, ipc_yoy_avg=IPC_HIGH_THRESHOLD - 0.0001,
        cli_eu=CLI_EXPANSION,
        rate_deposit=None, d_rate_3m=None,
    )
    assert result == "Expansion"


# ============================================================
# 5. Base case tail: low-rate desinflacion vs plain Expansion
# ============================================================

def test_low_rate_desinflacion_still_reads_as_contraccion_at_extreme_zirp():
    # Value chosen to sit below RATE_LOW_THRESHOLD both before AND after the
    # Phase-1c unit-bug fix (0.01 -> 1.0 pp), so this pins behaviour that is
    # stable across the fix -- unlike the boundary test below.
    result = _classify_row(
        oil_yoy=0.0, ipc_yoy_avg=0.0, cli_eu=CLI_EXPANSION,
        rate_deposit=0.005, d_rate_3m=None,
    )
    assert result == "Contraccion"


def test_expansion_base_case_when_nothing_else_matches():
    result = _classify_row(
        oil_yoy=0.0, ipc_yoy_avg=0.0, cli_eu=CLI_EXPANSION,
        rate_deposit=None, d_rate_3m=None,
    )
    assert result == "Expansion"


# ============================================================
# 6. Pre-fix defects, pinned as xfail(strict=True) -- Phase 1 flips these green.
#    A strict xfail that starts PASSING fails the suite, which is the fence
#    that proves the corresponding Phase-1 fix actually landed.
# ============================================================

def test_missing_cli_should_fail_safe_to_contraccion():
    # Phase 1f (fixed): missing CLI used to fall through to the
    # positive-cycle branch (implicit bullish default) instead of failing
    # safe. Was pinned xfail(strict=True) pre-fix; now asserts directly.
    result = _classify_row(
        oil_yoy=None, ipc_yoy_avg=None, cli_eu=None,
        rate_deposit=None, d_rate_3m=None,
    )
    assert result == "Contraccion"


def test_rate_unit_bug_boundary_0_5pp():
    # Phase 1c (fixed): RATE_LOW_THRESHOLD=0.01 was a unit bug -- rate_deposit
    # is stored in percentage points, so 0.01 meant "<0.01pp" not "<1%". At
    # rate_deposit=0.5 (0.5pp), the old code read 0.5 !< 0.01 and fell to
    # Expansion; fixed (threshold=1.0) it reads 0.5 < 1.0 -> Contraccion.
    # Was pinned xfail(strict=True) pre-fix; now asserts directly.
    result = _classify_row(
        oil_yoy=0.0, ipc_yoy_avg=0.0, cli_eu=CLI_EXPANSION,
        rate_deposit=0.5, d_rate_3m=None,
    )
    assert result == "Contraccion"
