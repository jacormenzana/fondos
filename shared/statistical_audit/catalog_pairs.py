"""§5 catalog_pairs — Block 2 cross-value equality pairs for both domains.
Every pair carries eligibility + min_matches (never a bare abs(a-b)<eps),
per AUDITORIA_ESTADISTICA.md §3 "Igualdad cruzada" / §4 #8.
"""
from __future__ import annotations

from itertools import combinations

import pandas as pd

from .comparisons import PairRule
from .tolerances import FLOAT_IDENTITY_TOLERANCE, IPC_ELIGIBILITY_FLOOR


def _ipc_eligibility(df: pd.DataFrame) -> pd.Series:
    """True where deflation was actually possible — the REAL_EQUALS_NOMINAL
    pair only means anything when IPC was nonzero over the horizon. Caller's
    input frame must carry an 'ipc_yoy' column for this rule to fire; absent
    that column it fails closed (never eligible) rather than raising.
    """
    if "ipc_yoy" not in df.columns:
        return pd.Series(False, index=df.index)
    return df["ipc_yoy"] > IPC_ELIGIBILITY_FLOOR


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
    "SHARPE_EQUALS_SORTINO": PairRule(
        rule_id="SHARPE_EQUALS_SORTINO", tolerance=FLOAT_IDENTITY_TOLERANCE, min_matches=8,
        diagnosis="Sortino downside-dev collapsed to total vol",
    ),
    "CAPTURE_UP_EQUALS_DOWN": PairRule(
        rule_id="CAPTURE_UP_EQUALS_DOWN", tolerance=0.001, min_matches=8,
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
