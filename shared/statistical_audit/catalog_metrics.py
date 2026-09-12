"""§5 catalog_metrics — statistical-type classification for fund_metrics /
fund_metric_timeseries. get_metric_spec() resolves any metric name (§2 scope,
auditStatisticalDataDistributionP2Metrics.md — 111 metrics in production) to
a MetricSpec via explicit overrides, then a beta_*/n_obs_* prefix rule, then
a regime-suffix rule, then a conservative default.

Regime-suffixed variants of the 5 curated metrics (return_ann_<regime>, etc.)
inherit their base metric's statistical type but are marked
high_kurtosis_expected — few observations per regime is expected by design,
not a defect (AUDITORIA_ESTADISTICA.md §3, "kurtosis"). Exact regime-suffix
strings are not hardcoded here (unverified in this session); the structural
signal "name starts with a curated base metric plus an underscore" is used
instead, which is both simpler and safer than guessing the 7 literal strings.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

StatisticalType = Literal[
    "continuous_positive", "continuous_signed", "bounded_unit",
    "discrete_ordinal", "count",
]


@dataclass(frozen=True)
class MetricSpec:
    metric: str
    statistical_type: StatisticalType
    supports_moments: bool
    supports_cv: bool
    supports_mass_points: bool
    min_n: int
    cv_epsilon: float
    segmentations: tuple[str, ...] = ("GLOBAL",)
    high_kurtosis_expected: bool = False


_DEFAULT_CONTINUOUS_SIGNED = MetricSpec(
    "*", "continuous_signed", True, True, True, 30, 1e-6, ("GLOBAL", "PEER"),
)

METRIC_OVERRIDES: dict[str, MetricSpec] = {
    "vol_ann": MetricSpec("vol_ann", "continuous_positive", True, True, True, 30, 1e-6, ("GLOBAL", "PEER")),
    "srri_volatility": MetricSpec("srri_volatility", "continuous_positive", True, True, True, 30, 1e-6, ("GLOBAL", "PEER")),
    "max_dd": MetricSpec("max_dd", "continuous_signed", True, False, True, 30, 1e-6, ("GLOBAL", "PEER")),
    "return_ann": MetricSpec("return_ann", "continuous_signed", True, True, True, 30, 1e-6, ("GLOBAL", "PEER")),
    "sharpe": MetricSpec("sharpe", "continuous_signed", True, True, True, 30, 1e-6, ("GLOBAL", "PEER")),
    "sortino": MetricSpec("sortino", "continuous_signed", True, True, True, 30, 1e-6, ("GLOBAL", "PEER")),
    "ret_vol_simple": MetricSpec("ret_vol_simple", "continuous_signed", True, True, True, 30, 1e-6, ("GLOBAL", "PEER")),
    "drawdown_duration": MetricSpec("drawdown_duration", "count", False, False, True, 1, 1e-6, ("GLOBAL",)),
    "time_to_recovery": MetricSpec("time_to_recovery", "count", False, False, True, 1, 1e-6, ("GLOBAL",)),
    "srri_nav": MetricSpec("srri_nav", "discrete_ordinal", False, False, True, 1, 1e-6, ("GLOBAL", "PEER")),
    "pct_positive_months": MetricSpec("pct_positive_months", "bounded_unit", True, False, True, 30, 1e-6, ("GLOBAL",)),
    "pct_negative_months": MetricSpec("pct_negative_months", "bounded_unit", True, False, True, 30, 1e-6, ("GLOBAL",)),
    "pct_severe_loss_months": MetricSpec("pct_severe_loss_months", "bounded_unit", True, False, True, 30, 1e-6, ("GLOBAL",)),
    "worst_month": MetricSpec("worst_month", "continuous_signed", True, False, True, 30, 1e-6, ("GLOBAL",)),
    "upside_capture": MetricSpec("upside_capture", "continuous_signed", True, False, True, 30, 1e-6, ("GLOBAL", "PEER")),
    "downside_capture": MetricSpec("downside_capture", "continuous_signed", True, False, True, 30, 1e-6, ("GLOBAL", "PEER")),
    "capture_ratio": MetricSpec("capture_ratio", "continuous_signed", True, False, True, 30, 1e-6, ("GLOBAL", "PEER")),
    "momentum_1y": MetricSpec("momentum_1y", "continuous_signed", True, False, True, 30, 1e-6, ("GLOBAL",)),
    "momentum_3y": MetricSpec("momentum_3y", "continuous_signed", True, False, True, 30, 1e-6, ("GLOBAL",)),
    "momentum_rank": MetricSpec("momentum_rank", "bounded_unit", True, False, True, 30, 1e-6, ("GLOBAL",)),
    "alpha_persistence": MetricSpec("alpha_persistence", "bounded_unit", True, False, True, 30, 1e-6, ("GLOBAL",)),
    "alpha_persistence_n": MetricSpec("alpha_persistence_n", "count", False, False, True, 1, 1e-6, ("GLOBAL",)),
    "fx_contribution_ann": MetricSpec("fx_contribution_ann", "continuous_signed", True, False, True, 30, 1e-6, ("GLOBAL",)),
    "fx_contribution_pct": MetricSpec("fx_contribution_pct", "bounded_unit", True, False, True, 30, 1e-6, ("GLOBAL",)),
    "fx_volatility_ann": MetricSpec("fx_volatility_ann", "continuous_positive", True, True, True, 30, 1e-6, ("GLOBAL",)),
    "macro_r2": MetricSpec("macro_r2", "bounded_unit", True, False, True, 30, 1e-6, ("GLOBAL",)),
    "macro_alpha": MetricSpec("macro_alpha", "continuous_signed", True, False, True, 30, 1e-6, ("GLOBAL",)),
    "macro_n_obs": MetricSpec("macro_n_obs", "count", False, False, True, 1, 1e-6, ("GLOBAL",)),
    "energy_sensitivity_pct": MetricSpec("energy_sensitivity_pct", "bounded_unit", True, False, True, 30, 1e-6, ("GLOBAL",)),
    "hy_spread_sensitivity_pct": MetricSpec("hy_spread_sensitivity_pct", "bounded_unit", True, False, True, 30, 1e-6, ("GLOBAL",)),
    "regime_coverage_ratio": MetricSpec("regime_coverage_ratio", "bounded_unit", True, False, True, 30, 1e-6, ("GLOBAL",)),
    "crisis_stress_score_mdd": MetricSpec("crisis_stress_score_mdd", "continuous_signed", True, False, True, 15, 1e-6, ("GLOBAL",)),
    "crisis_stress_score_ttr": MetricSpec("crisis_stress_score_ttr", "count", False, False, True, 15, 1e-6, ("GLOBAL",)),
}

_PREFIX_RULES: tuple[tuple[str, MetricSpec], ...] = (
    ("beta_", MetricSpec("beta_*", "continuous_signed", True, False, True, 30, 1e-6, ("GLOBAL",))),
    ("n_obs_", MetricSpec("n_obs_*", "count", False, False, True, 1, 1e-6, ("GLOBAL",))),
)

_REGIME_METRIC_BASES = ("return_ann", "vol_ann", "sharpe", "sortino", "max_dd")


def get_metric_spec(metric: str) -> MetricSpec:
    if metric in METRIC_OVERRIDES:
        return METRIC_OVERRIDES[metric]

    for prefix, spec in _PREFIX_RULES:
        if metric.startswith(prefix):
            return spec

    for base in _REGIME_METRIC_BASES:
        if metric != base and metric.startswith(base + "_"):
            base_spec = METRIC_OVERRIDES.get(base, _DEFAULT_CONTINUOUS_SIGNED)
            return replace(base_spec, metric=metric, high_kurtosis_expected=True)

    return replace(_DEFAULT_CONTINUOUS_SIGNED, metric=metric)
