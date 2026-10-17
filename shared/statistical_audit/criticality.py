# shared/statistical_audit/criticality.py
# -*- coding: utf-8 -*-
"""
Criticality model of the statistical audit (FND-0262, GOV-03; doc/reglas/AUDITORIA_ESTADISTICA.md 6.5, GOBIERNO_CONTROLES.md D-INVARIANT /
D-IDENTITY / D-TS-DUP). Pure; the ONE place that says which finding is a defect.

    CRITICAL  a defect: a wrong or corrupt value that a consumer reads. Refuses a promotion when it touches a fund of the scenario
              (shared/statistical_audit/gating.py), makes `--mode check` exit 1, and is tolerated only as an accepted residual
              (accept_audit_residual.py: ticket + expiry). Its finding MUST name its ISINs (`violating_isins`): a defect nobody can
              place is assumed to touch the scenario.
    MAJOR     a value outside its plausible (not impossible) range: listed for review, never blocks.
    MINOR     a statistical anomaly or context (outliers, shape, concentration, coverage): information, never blocks.

Classification is by finding class, block and rule, per DOMAIN -- the same `block` does not mean the same thing in both audits:

  * p2_metrics, BLOCK2 = cross-value IDENTITIES (real vs nominal, scalar vs timeseries, Sharpe vs Sortino, capture up vs down): when one
    trips, a stored number is wrong -> CRITICAL.
  * cost_attributes, BLOCK2 = `X__EQUALS__Y` pairs of two fee components: POPULATION-level coincidence detectors (one row per pair, no
    ISIN, e.g. "19/1858 eligible rows within tolerance"). They hint at a copy bug but equal values are legitimate (both 0.5%): MINOR.
    Treating them as defects made the cost gate refuse every scenario in every past run (FND-0265 replay, 60/60).

The three legacy sets (gating.BLOCKING_*) are views of the data below, so a change here is a change of the gate and of the test that
maps every blocking class to a catalogue control.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

CRITICAL = "CRITICAL"
MAJOR = "MAJOR"
MINOR = "MINOR"
LEVELS = (MINOR, MAJOR, CRITICAL)                  # increasing severity

DOMAIN_P2 = "p2_metrics"
DOMAIN_COSTS = "cost_attributes"
DOMAINS = (DOMAIN_P2, DOMAIN_COSTS)

# --- what is CRITICAL ---------------------------------------------------------------------------------------------------------------
CRITICAL_RULE_CLASSES = frozenset({"HARD_INVARIANT"})
CRITICAL_RULE_IDS = frozenset({"TIMESERIES_DUPLICATE"})
# block -> the domains where a finding of that block is a defect irrespective of its class (see the module docstring)
CRITICAL_BLOCKS_BY_DOMAIN = {DOMAIN_P2: frozenset({"BLOCK2"})}

PLAUSIBILITY_CLASS = "PLAUSIBILITY"

CONSEQUENCE = {
    CRITICAL: ("refuses a promotion when on a fund of the scenario; --mode check exits 1; tolerated only as an accepted residual "
               "(ticket + expiry)"),
    MAJOR: "listed for review; never blocks",
    MINOR: "information; never blocks",
}
# What a finding of each level must carry to be actionable (checked by the tests on the emitting code and on the persisted rows).
EXPECTED_EVIDENCE = {
    CRITICAL: "violating_isins (a defect that cannot be placed is assumed to touch every scenario), the observed count and the rule's bound",
    MAJOR: "the observed value or count and the bound it left",
    MINOR: "the statistic that triggered it",
}


@dataclass(frozen=True)
class Rating:
    level: str
    consequence: str
    basis: str                              # which clause of the model decided it

    @property
    def blocks(self) -> bool:
        return self.level == CRITICAL


def critical_blocks(domain: Optional[str] = None) -> frozenset:
    """Blocks whose findings are defects in `domain`; domain None = in ANY domain (the legacy, domain-agnostic view)."""
    if domain is None:
        return frozenset().union(*CRITICAL_BLOCKS_BY_DOMAIN.values())
    return CRITICAL_BLOCKS_BY_DOMAIN.get(domain, frozenset())


def classify(domain: Optional[str], rule_class: Optional[str], block: Optional[str], rule_id: Optional[str]) -> Rating:
    """The level of one finding. `domain` None keeps the legacy domain-agnostic behaviour (any domain's critical block counts)."""
    if rule_class in CRITICAL_RULE_CLASSES:
        return Rating(CRITICAL, CONSEQUENCE[CRITICAL], f"rule class {rule_class}")
    if rule_id in CRITICAL_RULE_IDS:
        return Rating(CRITICAL, CONSEQUENCE[CRITICAL], f"rule {rule_id}")
    if block in critical_blocks(domain):
        return Rating(CRITICAL, CONSEQUENCE[CRITICAL], f"{block} is an identity block in {domain or 'a domain'}")
    if rule_class == PLAUSIBILITY_CLASS:
        return Rating(MAJOR, CONSEQUENCE[MAJOR], "plausibility bound")
    return Rating(MINOR, CONSEQUENCE[MINOR], "statistical anomaly or context")


def is_critical(domain: Optional[str], rule_class: Optional[str], block: Optional[str], rule_id: Optional[str]) -> bool:
    return classify(domain, rule_class, block, rule_id).blocks
