"""§5 catalog_pairs — Block 2 cross-value equality pairs for both domains.
Every pair carries eligibility + min_matches (never a bare abs(a-b)<eps),
per AUDITORIA_ESTADISTICA.md §3 "Igualdad cruzada" / §4 #8.
"""
from __future__ import annotations

from itertools import combinations

import pandas as pd

from .comparisons import PairRule


def _ipc_eligibility(df: pd.DataFrame) -> pd.Series:
    """True where deflation was actually possible — the REAL_EQUALS_NOMINAL
    pair only means anything when IPC was nonzero over the horizon. Caller's
    input frame must carry an 'ipc_yoy' column for this rule to fire; absent
    that column it fails closed (never eligible) rather than raising.
    """
    if "ipc_yoy" not in df.columns:
        return pd.Series(False, index=df.index)
    return df["ipc_yoy"] > 0.001


P2_PAIRS: dict[str, PairRule] = {
    "REAL_EQUALS_NOMINAL": PairRule(
        rule_id="REAL_EQUALS_NOMINAL", tolerance=0.0001, min_matches=8,
        eligibility=_ipc_eligibility,
        diagnosis="Deflation not applied (IPC exists but real == nominal)",
    ),
    "SHARPE_EQUALS_SORTINO": PairRule(
        rule_id="SHARPE_EQUALS_SORTINO", tolerance=0.0001, min_matches=8,
        diagnosis="Sortino downside-dev collapsed to total vol",
    ),
    "CAPTURE_UP_EQUALS_DOWN": PairRule(
        rule_id="CAPTURE_UP_EQUALS_DOWN", tolerance=0.001, min_matches=8,
        diagnosis="Capture denominator defect",
    ),
    "SCALAR_EQUALS_TIMESERIES": PairRule(
        rule_id="SCALAR_EQUALS_TIMESERIES", tolerance=0.0001, min_matches=8,
        diagnosis="Snapshot/timeseries divergence (write-path inconsistency)",
    ),
    "VOL_ANN_EQUALS_SRRI_VOL": PairRule(
        rule_id="VOL_ANN_EQUALS_SRRI_VOL", tolerance=0.0001, min_matches=8,
        diagnosis="SRRI pipeline consumed stale vol",
    ),
}

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
