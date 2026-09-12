# proyecto1/tests/test_statistical_audit_catalogs.py
# -*- coding: utf-8 -*-
"""Tests unitarios de los catalogos declarativos (doc/reglas/
AUDITORIA_ESTADISTICA.md §5): catalog_cost_columns, catalog_metrics,
catalog_pairs, catalog_invariants.

Cumple R-7: sin importar pipeline.py ni core.io.
"""

import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from shared.statistical_audit.catalog_cost_columns import COST_COLUMNS
from shared.statistical_audit.catalog_invariants import COST_INVARIANTS, P2_INVARIANTS
from shared.statistical_audit.catalog_metrics import get_metric_spec
from shared.statistical_audit.catalog_pairs import COST_PAIRS, P2_PAIRS
from shared.statistical_audit.invariants import check_invariant

import pandas as pd


class TestCostColumnsCatalog:
    def test_covers_every_column_named_in_the_cost_skill_scope(self):
        expected = {
            "Ongoing_Charge_Recurrent", "Entry_Fee_Pct", "Entry_Fee_Pct_Max",
            "Exit_Fee_Pct", "Exit_Fee_Pct_Max", "Management_Fee_Pct",
            "Transaction_Cost_Pct", "Performance_Fee_Pct", "Performance_Fee_Basis",
            "ACI_1Y", "ACI_RHP", "Cost_RHP_Years",
            "Horizon_Years", "Total_Costs_EUR", "Total_Costs_Pct", "Annual_Impact_Pct",
        }
        assert expected <= set(COST_COLUMNS)

    def test_scale_split_matches_block7_ratio_vs_integer_percent(self):
        assert COST_COLUMNS["Ongoing_Charge_Recurrent"].scale == "ratio"
        assert COST_COLUMNS["Entry_Fee_Pct"].scale == "ratio"
        assert COST_COLUMNS["ACI_RHP"].scale == "integer_percent"
        assert COST_COLUMNS["Management_Fee_Pct"].scale == "integer_percent"

    def test_hard_bound_and_plausibility_bound_kept_separate(self):
        # Management_Fee_Pct: DDL CHECK caps at 10 (HARD_INVARIANT), the
        # cost skill's Block 7 plausibility range is 0-25 — both retained.
        spec = COST_COLUMNS["Management_Fee_Pct"]
        assert spec.hard_bound.max_value == 10
        assert spec.plausibility_bound.max_value == 25


class TestMetricsCatalog:
    def test_vol_ann_is_continuous_positive_with_peer_segmentation(self):
        spec = get_metric_spec("vol_ann")
        assert spec.statistical_type == "continuous_positive"
        assert "PEER" in spec.segmentations

    def test_beta_prefix_rule_applies(self):
        spec = get_metric_spec("beta_rate_eu")
        assert spec.statistical_type == "continuous_signed"
        assert not spec.supports_cv

    def test_regime_suffixed_metric_inherits_base_but_expects_high_kurtosis(self):
        base = get_metric_spec("vol_ann")
        regime = get_metric_spec("vol_ann_expansion")
        assert regime.statistical_type == base.statistical_type
        assert regime.high_kurtosis_expected
        assert not base.high_kurtosis_expected

    def test_unknown_metric_falls_back_to_conservative_default(self):
        spec = get_metric_spec("some_future_metric_not_yet_cataloged")
        assert spec.statistical_type == "continuous_signed"


class TestPairsCatalog:
    def test_p2_pairs_cover_the_five_block2_rows(self):
        assert set(P2_PAIRS) == {
            "REAL_EQUALS_NOMINAL", "SHARPE_EQUALS_SORTINO", "CAPTURE_UP_EQUALS_DOWN",
            "SCALAR_EQUALS_TIMESERIES", "VOL_ANN_EQUALS_SRRI_VOL",
        }

    def test_cost_pairs_generates_all_21_unordered_combinations(self):
        # C(7,2) = 21 — every unordered pair of the 7 percent-scale columns.
        assert len(COST_PAIRS) == 21

    def test_every_pair_rule_carries_min_matches_and_tolerance(self):
        for rule in {**P2_PAIRS, **COST_PAIRS}.values():
            assert rule.min_matches >= 1
            assert rule.tolerance > 0


class TestInvariantsCatalogAgainstEngine:
    def test_catalog_sortino_rule_is_the_corrected_sign_conditioned_version(self):
        rule = next(r for r in P2_INVARIANTS if r.rule_id == "SORTINO_VS_SHARPE_UP")
        assert rule.when == "excess_return > 0"

        df = pd.DataFrame({"sharpe": [-0.5], "sortino": [-1.0], "excess_return": [-0.10]})
        result = check_invariant(df, rule)
        assert result.n_applicable == 0  # the doc's own counterexample is excluded

    def test_catalog_oc_contamination_rule_fires_on_known_pattern(self):
        rule = next(r for r in COST_INVARIANTS if r.rule_id == "OC_NOT_CONTAMINATED")
        df = pd.DataFrame({"Ongoing_Charge_Recurrent": [0.0272], "ACI_RHP": [2.72]})
        result = check_invariant(df, rule)
        assert result.n_violations == 1

    def test_catalog_oc_contamination_rule_silent_on_clean_data(self):
        rule = next(r for r in COST_INVARIANTS if r.rule_id == "OC_NOT_CONTAMINATED")
        df = pd.DataFrame({"Ongoing_Charge_Recurrent": [0.0272], "ACI_RHP": [5.10]})
        result = check_invariant(df, rule)
        assert result.n_violations == 0
