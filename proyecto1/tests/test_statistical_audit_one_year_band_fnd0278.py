# proyecto1/tests/test_statistical_audit_one_year_band_fnd0278.py
# -*- coding: utf-8 -*-
"""FND-0278 -- the one-year relation band of ANNUAL_EQUALS_TOTAL_AT_1Y / ANNUAL_LE_ACCUMULATED (R-7: the real engine on small frames; no pipeline, no DB).

At horizon 1 the KID's annual impact and total are the same cost, but the KID publishes them on slightly different bases (1-decimal ACI; the older template assumes the moderate
first-year return): LU1683489089 6.7 % vs 6.37 % (ratio 1.05), IE0031575503 10.6 % vs 9.14 % (ratio 1.16). Owner decision 2026-10-17: accept annual / total in [0.9, 1.25] at
horizon 1 only. Live evidence: 164 of 198 one-year rows outside the 0.06 pp tolerance sit at 0.956..1.160; none in [0.77, 0.95) or [1.20, 2.50); 34 are extraction errors.
"""
import os
import sys

import pandas as pd
import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.statistical_audit.catalog_invariants import COST_INVARIANTS  # noqa: E402
from shared.statistical_audit.invariants import check_invariant  # noqa: E402
from shared.statistical_audit.tolerances import KID_ONE_YEAR_RATIO_MAX, KID_ONE_YEAR_RATIO_MIN  # noqa: E402

RULES = {r.rule_id: r for r in COST_INVARIANTS}


def _violations(rule_id, annual, total, horizon):
    df = pd.DataFrame([{"Annual_Impact_Pct": annual, "Total_Costs_Pct": total, "Horizon_Years": horizon}])
    return check_invariant(df, RULES[rule_id]).n_violations


def test_the_band_is_the_owners_narrow_one():
    assert (KID_ONE_YEAR_RATIO_MIN, KID_ONE_YEAR_RATIO_MAX) == (0.9, 1.25)


@pytest.mark.parametrize("annual, total, why", [
    (6.7, 6.37, "LU1683489089 (ratio 1.05): the KID's own convention"),
    (10.6, 9.14, "IE0031575503 (ratio 1.16): moderate-scenario template"),
    (4.6, 4.56, "within the 0.06 pp rounding tolerance already"),
    (9.0, 10.0, "ratio exactly 0.9: the lower edge is accepted"),
    (12.5, 10.0, "ratio exactly 1.25: the upper edge is accepted"),
])
def test_the_equality_rule_accepts_what_the_kid_publishes(annual, total, why):
    assert _violations("ANNUAL_EQUALS_TOTAL_AT_1Y", annual, total, 1) == 0, why


@pytest.mark.parametrize("annual, total, why", [
    (4.6, 0.04, "a total of 4 EUR read for a 4.6 % impact (ratio 115): an extraction error"),
    (4.6, 0.46, "a total off by a factor of 10"),
    (8.9, 10.0, "ratio 0.89: just below the band"),
    (12.6, 10.0, "ratio 1.26: just above the band"),
    (3.0, 1.0, "ratio 3: three times"),
    (2.0, 1.0, "ratio 2: a doubling is NOT accepted (the wide 0.8-2.0 band was refused)"),
    (5.0, 0.0, "a zero total with a positive impact is a defect (and must not divide by zero)"),
])
def test_the_equality_rule_still_flags_real_errors(annual, total, why):
    assert _violations("ANNUAL_EQUALS_TOTAL_AT_1Y", annual, total, 1) == 1, why


def test_the_accumulated_rule_uses_the_band_only_at_one_year():
    assert _violations("ANNUAL_LE_ACCUMULATED", 6.7, 6.37, 1) == 0          # 1 year: within 1.25 x
    assert _violations("ANNUAL_LE_ACCUMULATED", 10.6, 9.14, 1) == 0
    assert _violations("ANNUAL_LE_ACCUMULATED", 12.6, 10.0, 1) == 1          # 1 year, above the band
    assert _violations("ANNUAL_LE_ACCUMULATED", 3.3, 2.16, 3) == 1           # LU1734046979's truncated 216 EUR at 3 years: the strict relation holds beyond 1 year
    assert _violations("ANNUAL_LE_ACCUMULATED", 3.3, 3.3 * 1.1, 3) == 0      # a consistent multi-year row passes
    assert _violations("ANNUAL_LE_ACCUMULATED", 3.3, 3.3 * 0.98, 3) == 1     # strict beyond 1 year: 1.25 x does NOT apply (3.3 > 3.234 + 0.06)


def test_a_lower_annual_impact_than_the_total_is_fine_for_the_accumulated_rule():
    assert _violations("ANNUAL_LE_ACCUMULATED", 1.0, 5.0, 1) == 0
    assert _violations("ANNUAL_LE_ACCUMULATED", 1.0, 5.0, 5) == 0
