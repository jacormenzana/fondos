"""§5 catalog_pairs — Block 2 cross-value equality pairs for both domains.
Every pair carries eligibility + min_matches (never a bare abs(a-b)<eps),
per AUDITORIA_ESTADISTICA.md §3 "Igualdad cruzada" / §4 #8.
"""
from __future__ import annotations

from itertools import combinations

import pandas as pd

from .comparisons import PairRule
from .tolerances import FLOAT_IDENTITY_TOLERANCE, IPC_ELIGIBILITY_FLOOR, RATIO_ELIGIBILITY_FLOOR


def _ipc_eligibility(df: pd.DataFrame) -> pd.Series:
    """True where deflation was actually possible — the REAL_EQUALS_NOMINAL
    pair only means anything when IPC was nonzero over the horizon. The caller's
    frame carries 'window_cpi_ann' (the CPI change over each row's own window, FND-0234) or, for callers that
    only have a scalar, 'ipc_yoy'; the per-row column wins. A row whose window CPI is NaN (undecidable) is never
    eligible. With neither column the rule fails closed (never eligible) rather than raising.
    """
    column = "window_cpi_ann" if "window_cpi_ann" in df.columns else "ipc_yoy"
    if column not in df.columns:
        return pd.Series(False, index=df.index)
    return df[column] > IPC_ELIGIBILITY_FLOOR


def _positive_ratio_eligibility(df: pd.DataFrame) -> pd.Series:
    """Rows where Sharpe and Sortino are both clearly POSITIVE (above RATIO_ELIGIBILITY_FLOOR). That is the only place where
    sortino == sharpe means something: with a positive numerator the downside deviation is strictly below the standard deviation
    (dd^2 <= (1/N) * sum_{r<MAR} (r - mean)^2 < sigma^2), so equal ratios there are a defect signature. With a negative numerator
    dd/sigma crosses 1 as the shortfall grows (dd^2 = sigma^2 (1 - 1/N) + (mean - MAR)^2): equality is a coincidence at the
    crossing, 8 of 21,755 live rows. With a numerator near 0 both ratios are near 0. Fails closed without the columns."""
    if "sharpe" not in df.columns or "sortino" not in df.columns:
        return pd.Series(False, index=df.index)
    return (df["sharpe"] > RATIO_ELIGIBILITY_FLOOR) & (df["sortino"] > RATIO_ELIGIBILITY_FLOOR)


P2_PAIRS: dict[str, PairRule] = {
    # IPC_ELIGIBILITY_FLOOR audited 2026-09-13 (tolerances.py): the comparison
    # tolerance is deliberately the SAME constant that gates eligibility above
    # (P#11/DRY) rather than an unrelated bare epsilon — sweep showed no
    # natural floor above the eligibility threshold itself (26 matches at
    # 0.0001 -> 16,121 at 0.06, out of 22,367 eligible rows), so the
    # eligibility floor is the only principled anchor available without a
    # dedicated follow-up investigation of the deflation-magnitude curve.
    "REAL_EQUALS_NOMINAL": PairRule(
        rule_id="REAL_EQUALS_NOMINAL", tolerance=IPC_ELIGIBILITY_FLOOR, min_matches=8,
        eligibility=_ipc_eligibility,
        diagnosis="Deflation not applied (IPC exists but real == nominal)",
    ),
    # FLOAT_IDENTITY_TOLERANCE audited 2026-09-13 (tolerances.py): sweep gave
    # 173/401/2,092/8,575 matches at 0.0001/0.001/0.01/0.06 (of 43,766
    # eligible) — monotonic growth with no rounding-grid plateau, confirming
    # this is an identity test (sortino's downside-dev either collapsed to
    # sharpe's total vol by defect, or the two are genuinely different) and
    # 0.0001 is already the correct value.
    # FND-0234 (2026-10-06): eligibility = both ratios clearly positive. The 25 live matches were 17 near-zero numerators
    # (|ratio| < 3e-4: return_ann within 0.1 pp of the risk-free rate) and 8 negative-numerator coincidences at the dd/sigma = 1
    # crossing; none had a positive numerator, where equality is impossible by construction (see _positive_ratio_eligibility).
    "SHARPE_EQUALS_SORTINO": PairRule(
        rule_id="SHARPE_EQUALS_SORTINO", tolerance=FLOAT_IDENTITY_TOLERANCE, min_matches=8,
        eligibility=_positive_ratio_eligibility,
        diagnosis="Sortino downside-dev collapsed to total vol",
    ),
    # FND-0234 (2026-10-07): tolerance 0.001 -> FLOAT_IDENTITY_TOLERANCE, the class-2 identity constant. Measured on the live
    # 2,810 since_inception rows: |upside - downside| < t holds for 0 / 0 / 1 / 5 / 10 / 55 / 131 rows at t = 1e-9 / 1e-5 / 1e-4 /
    # 5e-4 / 1e-3 / 5e-3 / 1e-2 -- a smooth ~1e4-per-unit density, i.e. chance. At 0.001 the rule expects ~10 matches by luck,
    # above min_matches = 8, so it fired on ordinary ACTIVE funds (Templeton Asian Growth, Pictet Japan, SISF Energy, JPM HY ...:
    # one of the ten is an index fund), not on a defect. A collapsed denominator would make up == down to float precision for
    # many funds at once (0 rows below 1e-9). At 1e-4 the chance count is ~1, well under min_matches.
    "CAPTURE_UP_EQUALS_DOWN": PairRule(
        rule_id="CAPTURE_UP_EQUALS_DOWN", tolerance=FLOAT_IDENTITY_TOLERANCE, min_matches=8,
        diagnosis="Capture denominator defect",
    ),
    # Tolerance NOT recalibrated 2026-09-13 — audited and found NOT to fit any
    # of the three tolerance classes in tolerances.py, NOR a relative
    # (numpy.isclose-style) tolerance (swept up to rtol=0.30, sharpe/sortino/
    # return_ann stayed 50-97% divergent — see AUDITORIA_ESTADISTICA.md §2.9).
    # Root-caused instead of tolerance-tuned: two real causes found — (1) NAV
    # staleness between fund_metrics and fund_metric_timeseries (dominant,
    # affects all 5 metrics; needs the pending full P2 recompute, not a code
    # fix here), and (2) a genuine formula asymmetry for sharpe/sortino only
    # (run_pipeline.py's scalar path used a flat RISK_FREE_RATE_ANN while the
    # timeseries path already used a date-aligned rf_series) — (2) is FIXED
    # (run_pipeline.py + CALC_VERSION bump, §2.9). This rule's tolerance stays
    # at its prior value on purpose: fixing the cause, not widening the
    # number, is the correct resolution once a real cause is identified.
    "SCALAR_EQUALS_TIMESERIES": PairRule(
        rule_id="SCALAR_EQUALS_TIMESERIES", tolerance=0.0001, min_matches=8,
        diagnosis="Snapshot/timeseries divergence (write-path inconsistency)",
    ),
}

# VOL_ANN_EQUALS_SRRI_VOL removed 2026-09-13 (AUDITORIA_ESTADISTICA.md §2.6):
# investigated the live 100%-match finding and confirmed it is NOT a binding
# defect. proyecto2/src/calculations/returns.py:annualized_volatility() and
# srri.py:compute_srri() both compute `returns.std(ddof=1) * sqrt(12)` over
# the identical NAV series for vol_ann(since_inception, real_flag=0) and
# srri_volatility — they are mathematically identical by construction, not
# by a shared-bug coincidence. The pair could never have surfaced a defect
# as specified; it is a catalog error, not a P2 bug. See
# test_statistical_audit_catalogs.py::test_vol_ann_srri_vol_formulas_are_identical_by_design.

_COST_PERCENT_COLUMNS = (
    "Entry_Fee_Pct_Max", "Exit_Fee_Pct_Max", "Management_Fee_Pct",
    "Transaction_Cost_Pct", "Performance_Fee_Pct", "ACI_1Y", "ACI_RHP",
)


def _positive_left(left_column: str):
    def _eligibility(df: pd.DataFrame) -> pd.Series:
        return df[left_column] > 0
    return _eligibility


def _build_cost_pairs() -> dict[str, PairRule]:
    pairs = {}
    for left, right in combinations(_COST_PERCENT_COLUMNS, 2):
        rule_id = f"{left}__EQUALS__{right}"
        pairs[rule_id] = PairRule(
            rule_id=rule_id, tolerance=0.005, min_matches=8,
            eligibility=_positive_left(left),
            diagnosis=f"{left} and {right} hold the same value — binding failure",
        )
    return pairs


COST_PAIRS: dict[str, PairRule] = _build_cost_pairs()
