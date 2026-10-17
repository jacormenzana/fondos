# proyecto1/tests/test_statistical_audit_criticality.py
# -*- coding: utf-8 -*-
"""FND-0262 (GOV-03): the criticality model (shared/statistical_audit/criticality.py) and the plan's exit criterion -- every CRITICAL rule
has a reproducible positive and negative case and one stated consequence, and every CRITICAL finding names its funds. Pure (R-7)."""
from __future__ import annotations

import ast
import importlib.util
import os
import sys
from itertools import product
from types import SimpleNamespace

import pandas as pd
import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.statistical_audit import completeness as cpl  # noqa: E402
from shared.statistical_audit import criticality as crit  # noqa: E402
from shared.statistical_audit import gating  # noqa: E402
from shared.statistical_audit.catalog_cost_columns import COST_COLUMNS  # noqa: E402
from shared.statistical_audit.catalog_group_checks import COST_GROUP_CHECKS  # noqa: E402
from shared.statistical_audit.catalog_invariants import COST_INVARIANTS, P2_INVARIANTS  # noqa: E402
from shared.statistical_audit.catalog_pairs import P2_PAIRS  # noqa: E402
from shared.statistical_audit.comparisons import compare_pairs  # noqa: E402
from shared.statistical_audit.group_checks import check_group_constancy  # noqa: E402
from shared.statistical_audit.invariants import BoundRule, check_bounds, check_invariant  # noqa: E402

_RUNNER_PATH = os.path.join(_ROOT, "scripts", "audit", "run_statistical_audit.py")
_spec = importlib.util.spec_from_file_location("run_statistical_audit_crit", _RUNNER_PATH)
runner = importlib.util.module_from_spec(_spec)
sys.modules["run_statistical_audit_crit"] = runner
_spec.loader.exec_module(runner)

P2, COSTS = crit.DOMAIN_P2, crit.DOMAIN_COSTS
CLASSES = ("HARD_INVARIANT", "PLAUSIBILITY", "STATISTICAL_ANOMALY", None)
BLOCKS = tuple(f"BLOCK{i}" for i in range(1, 8)) + (None,)
RULE_IDS = ("TIMESERIES_DUPLICATE", "ANY_RULE", None)


# --- the model ----------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("domain,rule_class,block,rule_id,level", [
    (P2, "HARD_INVARIANT", "BLOCK5", "WINDOW_FISHER_IDENTITY", crit.CRITICAL),
    (COSTS, "HARD_INVARIANT", "BLOCK5", "MGMT_LE_TOTAL", crit.CRITICAL),
    (P2, "STATISTICAL_ANOMALY", "BLOCK5", "TIMESERIES_DUPLICATE", crit.CRITICAL),
    (P2, "STATISTICAL_ANOMALY", "BLOCK2", "SCALAR_EQUALS_TIMESERIES_sharpe_rolling_1y", crit.CRITICAL),   # a P2 identity
    (COSTS, "STATISTICAL_ANOMALY", "BLOCK2", "Entry_Fee_Pct_Max__EQUALS__ACI_RHP", crit.MINOR),          # a cost coincidence
    (P2, "PLAUSIBILITY", "BLOCK5", "SHARPE_BOUND", crit.MAJOR),
    (COSTS, "PLAUSIBILITY", "BLOCK6", "SCHEDULE_EUR_MISPARSE", crit.MAJOR),
    (P2, "STATISTICAL_ANOMALY", "BLOCK4", "OUTLIER_MAD_Z", crit.MINOR),
    (None, "STATISTICAL_ANOMALY", "BLOCK2", "X", crit.CRITICAL),                                          # legacy view
])
def test_classification(domain, rule_class, block, rule_id, level):
    r = crit.classify(domain, rule_class, block, rule_id)
    assert r.level == level and r.consequence == crit.CONSEQUENCE[level] and r.basis


def test_every_level_states_one_consequence_and_the_evidence_it_needs():
    assert set(crit.CONSEQUENCE) == set(crit.EXPECTED_EVIDENCE) == set(crit.LEVELS)
    assert all(crit.CONSEQUENCE[lv].strip() and crit.EXPECTED_EVIDENCE[lv].strip() for lv in crit.LEVELS)


def test_the_p2_decision_is_unchanged_for_every_class_block_and_rule():
    # zero P2 delta by construction: the domain-aware predicate equals the legacy one over the whole space
    for rule_class, block, rule_id in product(CLASSES, BLOCKS, RULE_IDS):
        assert gating.is_blocking(rule_class, block, rule_id, P2) == gating.is_blocking(rule_class, block, rule_id), (rule_class, block, rule_id)


def test_the_cost_decision_differs_only_for_its_block2_coincidence_pairs():
    for rule_class, block, rule_id in product(CLASSES, BLOCKS, RULE_IDS):
        legacy, new = gating.is_blocking(rule_class, block, rule_id), gating.is_blocking(rule_class, block, rule_id, COSTS)
        expected_change = block == "BLOCK2" and rule_class != "HARD_INVARIANT" and rule_id != "TIMESERIES_DUPLICATE"
        assert (legacy != new) == expected_change, (rule_class, block, rule_id)


def test_the_legacy_sets_are_views_of_the_model():
    assert gating.BLOCKING_RULE_CLASSES == crit.CRITICAL_RULE_CLASSES
    assert gating.BLOCKING_RULE_IDS == crit.CRITICAL_RULE_IDS
    assert gating.BLOCKING_BLOCKS == crit.critical_blocks() == frozenset({"BLOCK2"})
    assert crit.critical_blocks(COSTS) == frozenset() and crit.critical_blocks(P2) == frozenset({"BLOCK2"})


def test_the_model_domains_are_the_runners():
    src = open(_RUNNER_PATH, encoding="utf-8").read()
    assert 'AuditRun(domain="p2_metrics")' in src and 'AuditRun(domain="cost_attributes")' in src
    assert set(crit.DOMAINS) == {"p2_metrics", "cost_attributes"}


# --- the consequences, end to end -----------------------------------------------------------------------------------------------------
COST_PAIR = {"rule_id": "Entry_Fee_Pct_Max__EQUALS__ACI_RHP", "rule_class": "STATISTICAL_ANOMALY", "block": "BLOCK2", "group_key": "x",
             "isin": None, "evidence": "19/1858 eligible rows within tolerance", "severity": "WARN"}


def test_a_cost_coincidence_pair_no_longer_lands_on_every_scenario():
    cost = gating.scope_findings([COST_PAIR], {}, ["LU1"], {}, domain=COSTS)
    assert cost.on_scenario == [] and cost.universe_only == []
    legacy = gating.scope_findings([COST_PAIR], {}, ["LU1"], {})                   # what the D8 replay hit 60 times out of 60
    assert [d.rule_id for d in legacy.on_scenario] == [COST_PAIR["rule_id"]]


def test_a_cost_run_with_only_coincidence_pairs_is_not_a_fail():
    assert cpl.run_status([], [COST_PAIR], cpl.SCOPE_FULL, COSTS) == cpl.STATE_PASS_WITH_WARNINGS
    assert cpl.run_status([], [COST_PAIR], cpl.SCOPE_FULL) == cpl.STATE_FAIL      # legacy view


def test_the_runner_check_mode_follows_the_run_domain():
    cost_run, p2_run = runner.AuditRun(domain="cost_attributes"), runner.AuditRun(domain="p2_metrics")
    cost_run.findings.append(COST_PAIR)
    p2_run.findings.append({**COST_PAIR, "rule_id": "SCALAR_EQUALS_TIMESERIES_sharpe_rolling_1y"})
    assert not runner._has_blocking_findings(cost_run) and runner._has_blocking_findings(p2_run)


# --- exit criterion: a positive and a negative case for every CRITICAL catalogue rule -------------------------------------------------
INVARIANT_CASES = {   # rule_id -> (violating row, passing row), evaluated by the real engine (invariants.check_invariant)
    "ACI_AMORTISATION_ORDER": ({"ACI_RHP": 2.0, "ACI_1Y": 1.0}, {"ACI_RHP": 1.0, "ACI_1Y": 2.0}),
    "ACI_MUST_AMORTISE": ({"ACI_1Y": 1.5, "ACI_RHP": 1.5, "Cost_RHP_Years": 5, "Entry_Fee_Pct": 1.0},
                          {"ACI_1Y": 1.5, "ACI_RHP": 1.2, "Cost_RHP_Years": 5, "Entry_Fee_Pct": 1.0}),
    "MGMT_LE_TOTAL": ({"Management_Fee_Pct": 2.0, "ACI_RHP": 1.0, "Cost_RHP_Years": 5.0}, {"Management_Fee_Pct": 0.5, "ACI_RHP": 1.0, "Cost_RHP_Years": 5.0}),
    "TXN_LE_TOTAL": ({"Transaction_Cost_Pct": 2.0, "ACI_RHP": 1.0, "Cost_RHP_Years": 5.0}, {"Transaction_Cost_Pct": 0.1, "ACI_RHP": 1.0, "Cost_RHP_Years": 5.0}),
    "ANNUAL_LE_ACCUMULATED": ({"Annual_Impact_Pct": 2.0, "Total_Costs_Pct": 1.0, "Horizon_Years": 5}, {"Annual_Impact_Pct": 1.0, "Total_Costs_Pct": 1.0, "Horizon_Years": 5}),
    "ANNUAL_EQUALS_TOTAL_AT_1Y": ({"Annual_Impact_Pct": 1.5, "Total_Costs_Pct": 1.0, "Horizon_Years": 1},
                                  {"Annual_Impact_Pct": 1.0, "Total_Costs_Pct": 1.02, "Horizon_Years": 1}),
    "OC_NOT_CONTAMINATED": ({"Ongoing_Charge_Recurrent": 0.015, "ACI_RHP": 1.5}, {"Ongoing_Charge_Recurrent": 0.012, "ACI_RHP": 1.5}),
    "MAX_DD_RANGE": ({"max_dd": 0.1}, {"max_dd": -0.2}),
    "SHORT_MAX_DD_RANGE": ({"short_max_drawdown": -1.5}, {"short_max_drawdown": -0.05}),
    "VOL_ANN_NONNEG": ({"vol_ann": -0.1}, {"vol_ann": 0.1}),
    "SRRI_VOL_NONNEG": ({"srri_volatility": -0.1}, {"srri_volatility": 0.1}),
    "SRRI_NAV_RANGE": ({"srri_nav": 8}, {"srri_nav": 4}),
    "MONTH_SHARE_OVERFLOW": ({"pct_positive_months": 0.7, "pct_negative_months": 0.5}, {"pct_positive_months": 0.6, "pct_negative_months": 0.4}),
    "CAPTURE_CLAMP": ({"upside_capture": 6.0, "downside_capture": 1.0}, {"upside_capture": 1.1, "downside_capture": 0.9}),
    "MACRO_R2_RANGE": ({"macro_r2": 1.2}, {"macro_r2": 0.4}),
    "DEFLATION_ORDER": ({"return_ann_real": 0.06, "return_ann_nominal": 0.05, "window_cpi_ann": 0.02},
                        {"return_ann_real": 0.03, "return_ann_nominal": 0.05, "window_cpi_ann": 0.02}),
    "FROZEN_NAV_ZERO_VOL": ({"vol_ann": 0.0, "periodic_return_variance": 0.01}, {"vol_ann": 0.1, "periodic_return_variance": 0.01}),
    "WINDOW_DEFLATION_STRICT": ({"w_return_real": 0.05, "w_return_nominal": 0.05, "w_cpi_ann": 0.02, "w_nominal_gap": 0.0},
                                {"w_return_real": 0.03, "w_return_nominal": 0.05, "w_cpi_ann": 0.02, "w_nominal_gap": 0.0}),
    "WINDOW_FISHER_IDENTITY": ({"w_deflator_implied": 1.05, "w_deflator_expected": 1.0, "w_nominal_gap": 0.0},
                               {"w_deflator_implied": 1.0, "w_deflator_expected": 1.0, "w_nominal_gap": 0.0}),
}
for _r in P2_INVARIANTS:                                       # the seven regime counters share one shape
    if _r.rule_id.startswith("N_OBS_NONNEG_"):
        _col = "n_obs_" + _r.rule_id[len("N_OBS_NONNEG_"):].lower()
        INVARIANT_CASES[_r.rule_id] = ({_col: -1}, {_col: 3})

CRITICAL_INVARIANTS = [r for r in COST_INVARIANTS + P2_INVARIANTS if r.bound_type == "HARD_INVARIANT"]


def test_every_critical_invariant_has_a_case():
    missing = sorted(r.rule_id for r in CRITICAL_INVARIANTS if r.rule_id not in INVARIANT_CASES)
    assert missing == [], f"add a (violating, passing) case for: {missing}"


@pytest.mark.parametrize("rule", CRITICAL_INVARIANTS, ids=lambda r: r.rule_id)
def test_each_critical_invariant_fires_on_its_positive_case_and_not_on_its_negative(rule):
    bad, good = INVARIANT_CASES[rule.rule_id]
    assert check_invariant(pd.DataFrame([bad]), rule).n_violations == 1
    ok = check_invariant(pd.DataFrame([good]), rule)
    assert (ok.n_applicable, ok.n_violations) == (1, 0)
    assert crit.classify(None, rule.bound_type, "BLOCK5", rule.rule_id).level == crit.CRITICAL


@pytest.mark.parametrize("rule", COST_GROUP_CHECKS, ids=lambda r: r.rule_id)
def test_each_group_constancy_rule_has_both_cases(rule):
    same = pd.DataFrame({rule.group_column: ["LU1", "LU1"], rule.value_column: [1.2, 1.2]})
    varies = pd.DataFrame({rule.group_column: ["LU1", "LU1"], rule.value_column: [1.2, 1.9]})
    assert check_group_constancy(same, rule).n_violating_groups == 1
    assert check_group_constancy(varies, rule).n_violating_groups == 0


HARD_BOUNDS = [(c, s.hard_bound) for c, s in COST_COLUMNS.items() if s.hard_bound is not None]


@pytest.mark.parametrize("column,bound", HARD_BOUNDS, ids=[c for c, _ in HARD_BOUNDS])
def test_each_hard_bound_has_both_cases(column, bound):
    rule = BoundRule(column, bound.min_value, bound.max_value, bound.bound_type)
    outside = pd.DataFrame({"ISIN": ["LU1"], column: [bound.max_value + 1]})
    inside = pd.DataFrame({"ISIN": ["LU1"], column: [(bound.min_value + bound.max_value) / 2]})
    assert check_bounds(outside, column, rule).n_breaches == 1 and check_bounds(inside, column, rule).n_breaches == 0
    assert bound.bound_type == "HARD_INVARIANT"


PAIR_FRAMES = {   # P2 BLOCK2 identity pairs (CRITICAL in the P2 domain): column names + the extra columns their eligibility reads
    "REAL_EQUALS_NOMINAL": ("nominal", "real", {"window_cpi_ann": 0.02}),
    "SHARPE_EQUALS_SORTINO": ("sharpe", "sortino", {}),
    "CAPTURE_UP_EQUALS_DOWN": ("upside_capture", "downside_capture", {}),
}


@pytest.mark.parametrize("rule_id", sorted(PAIR_FRAMES))
def test_each_p2_identity_pair_has_both_cases(rule_id):
    rule, (left, right, extra) = P2_PAIRS[rule_id], PAIR_FRAMES[rule_id]
    n = rule.min_matches
    equal = pd.DataFrame({"isin": [f"LU{i}" for i in range(n)], left: [0.5] * n, right: [0.5] * n, **{k: [v] * n for k, v in extra.items()}})
    apart = equal.assign(**{right: [0.9] * n})
    assert compare_pairs(equal, left, right, rule).triggered and not compare_pairs(apart, left, right, rule).triggered


def _merged(n_divergent, n_total=20):
    return pd.DataFrame({"isin": [f"LU{i:02d}" for i in range(n_total)], "scalar_value": [0.5] * n_total,
                         "ts_value": [0.9] * n_divergent + [0.5] * (n_total - n_divergent)})


def test_scalar_vs_timeseries_has_both_cases_and_names_its_funds():
    hit = runner._scalar_vs_timeseries_finding("sharpe", "rolling_1y", _merged(8))
    assert hit is not None and hit["rule_id"] == "SCALAR_EQUALS_TIMESERIES_sharpe_rolling_1y" and len(hit["violating_isins"]) == 8
    assert runner._scalar_vs_timeseries_finding("sharpe", "rolling_1y", _merged(7)) is None          # below significance
    assert runner._scalar_vs_timeseries_finding("sharpe", "rolling_1y", _merged(0)) is None


class _SeriesConn:
    """Summary rows (isin, real_flag, min_date, max_date, n_dates, n_rows, n_null_batch, n_below_min_obs) for every (metric, window)."""
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *_a, **_k):
        return SimpleNamespace(fetchall=lambda: list(self.rows))


def test_timeseries_duplicate_and_gap_have_both_cases_and_name_their_funds():
    run = runner.AuditRun(domain="p2_metrics")
    runner._run_timeseries_integrity(run, _SeriesConn([
        ("LU1", 0, "2020-01-31", "2020-03-31", 3, 4, 0, 0),     # duplicate: 4 rows over 3 dates
        ("LU2", 0, "2020-01-31", "2020-04-30", 3, 3, 0, 0),     # gap: 4 months spanned, 3 present
        ("LU3", 0, "2020-01-31", "2020-03-31", 3, 3, 0, 0),     # clean
    ]))
    dup = [f for f in run.findings if f["rule_id"] == "TIMESERIES_DUPLICATE"]
    gap = [f for f in run.findings if f["rule_id"] == "TIMESERIES_GAP"]
    assert dup and all(f["violating_isins"] == ("LU1",) for f in dup)
    assert gap and all(f["violating_isins"] == ("LU2",) for f in gap)
    clean = runner.AuditRun(domain="p2_metrics")
    runner._run_timeseries_integrity(clean, _SeriesConn([("LU3", 0, "2020-01-31", "2020-03-31", 3, 3, 0, 0)]))
    assert not [f for f in clean.findings if f["rule_id"] in ("TIMESERIES_DUPLICATE", "TIMESERIES_GAP")]


def test_beta_orphan_names_the_funds_it_flags():
    run = runner.AuditRun(domain="p2_metrics")
    cols = ["isin", "metric", "horizon", "value", "real_flag", "metric_version", "batch_id"]
    runner._run_beta_orphan_check(run, pd.DataFrame([
        ["LU1", "beta_oil", "since_inception", 0.1, 0, "v1", "b1"], ["LU1", "beta_gold", "since_inception", 0.2, 0, "v1", "b2"],
        ["LU1", "macro_r2", "since_inception", 0.5, 0, "v1", "b1"],
        ["LU2", "beta_oil", "since_inception", 0.1, 0, "v1", "b1"], ["LU2", "macro_r2", "since_inception", 0.5, 0, "v1", "b9"],
        ["LU3", "beta_oil", "since_inception", 0.1, 0, "v1", "b1"], ["LU3", "macro_r2", "since_inception", 0.5, 0, "v1", "b1"],
    ], columns=cols))
    (f,) = [f for f in run.findings if f["rule_id"] == "BETA_ORPHAN_BATCH"]
    assert f["violating_isins"] == ("LU1", "LU2")              # internal split + diverging from macro_r2; LU3 is healthy


def test_a_cost_hard_bound_breach_names_its_funds_whatever_the_isin_column_case():
    run = runner.AuditRun(domain="cost_attributes")
    frame = pd.DataFrame({"ISIN": ["LU1", "LU2"], "Management_Fee_Pct": [12.0, 1.0]})
    runner._block7(run, "Management_Fee_Pct", frame, "Management_Fee_Pct", BoundRule("Management_Fee_Pct", 0, 10, "HARD_INVARIANT"))
    assert run.findings[0]["violating_isins"] == ("LU1",)


# Procedural CRITICAL rules whose positive and negative cases already live in the runner's own test module.
PROCEDURAL_CASES = {
    "SCHEDULE_MULTIPLE_RHP_ROWS": ("test_multiple_rhp_rows_flagged", "test_single_rhp_row_not_flagged"),
    "SCHEDULE_RHP_ACI_MISMATCH": ("test_rhp_aci_mismatch_flagged", "test_rhp_aci_agreement_within_tolerance_not_flagged"),
    "SCHEDULE_ACI_RHP_ORPHAN": ("test_aci_rhp_orphan_flagged_when_no_rhp_row_exists", "test_aci_rhp_orphan_not_flagged_when_aci_rhp_is_null"),
    "BETA_ORPHAN_BATCH": ("test_beta_orphan_flagged_on_internal_batch_split", "test_beta_orphan_not_flagged_when_batches_consistent"),
}
COVERED_HERE = {"TIMESERIES_DUPLICATE"}


def _runner_emitters():
    """[(function, rule_id expression, rule_class expression, block expression, has violating_isins)] for every finding dict in the runner."""
    out = []
    for fn in [n for n in ast.walk(ast.parse(open(_RUNNER_PATH, encoding="utf-8").read())) if isinstance(n, ast.FunctionDef)]:
        for node in ast.walk(fn):
            if isinstance(node, ast.Dict):
                d = {k.value: ast.unparse(v) for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)}
                if "rule_id" in d and "rule_class" in d:
                    out.append((fn.name, d["rule_id"], d["rule_class"], d.get("block"), "violating_isins" in d))
    return out


def test_every_literal_critical_rule_the_runner_emits_has_its_cases():
    literal = {rid.strip("'") for _fn, rid, cls, _b, _i in _runner_emitters() if cls == "'HARD_INVARIANT'" and rid.startswith("'")}
    names = {n.name for n in ast.walk(ast.parse(open(os.path.join(_ROOT, "proyecto1", "tests", "test_statistical_audit_runner_dialect.py"),
                                                      encoding="utf-8").read())) if isinstance(n, ast.FunctionDef)}
    for rule_id in literal:
        assert rule_id in PROCEDURAL_CASES or rule_id in COVERED_HERE, f"{rule_id}: add a positive and a negative case"
    for rule_id, (positive, negative) in PROCEDURAL_CASES.items():
        assert positive in names and negative in names, rule_id


COST_COINCIDENCE_EMITTER = "run_cost_audit"                   # the cost X__EQUALS__Y pairs: MINOR by the model, need no ISINs


def test_every_critical_capable_emitter_names_its_funds():
    for fn, rule_id, cls, block, has_isins in _runner_emitters():
        critical_capable = "HARD_INVARIANT" in cls or "bound_type" in cls or block == "'BLOCK2'"
        if critical_capable and fn != COST_COINCIDENCE_EMITTER:
            assert has_isins, f"{fn}: {rule_id} can be CRITICAL but its finding carries no violating_isins"


# --- ALGORITHM_VERSION_UNIFORMITY (D4) --------------------------------------------------------------------------------------------
class _RollbackConn:
    def rollback(self):
        pass


def _uniformity(versions=None, raises=False, isins=None):
    run = runner.AuditRun(domain="p2_metrics")

    def loader(_conn):
        if raises:
            raise RuntimeError("relation does not exist")
        return [], None, versions

    runner._run_algorithm_version_uniformity(run, _RollbackConn(), isins=isins, loader=loader)
    return run


def test_version_uniformity_flags_a_partial_refresh_as_major_and_passes_a_uniform_universe():
    split = _uniformity(["20261011"] * 90 + ["20261003"] * 10)
    (f,) = split.findings
    assert f["rule_id"] == "ALGORITHM_VERSION_UNIFORMITY" and "90.0%" in f["evidence"]
    assert crit.classify(P2, f["rule_class"], f["block"], f["rule_id"]).level == crit.MAJOR
    assert _uniformity(["20261011"] * 2873 + ["20261003"] * 6).findings == []      # live 2026-10-12: 99.8% >= 98%


def test_version_uniformity_is_not_evaluated_on_a_sample_and_a_missing_input_is_missing():
    assert _uniformity(["x"], isins=["LU1"]).skips[0].kind == cpl.KIND_NOT_APPLICABLE
    assert _uniformity([]).skips[0].kind == cpl.KIND_EMPTY                         # required: resolves to MISSING in a FULL run
    assert cpl.resolve(_uniformity([]).skips[0], cpl.SCOPE_FULL) == cpl.RESOLVED_MISSING
    assert _uniformity(raises=True).skips[0].kind == cpl.KIND_MISSING


# --- exit criterion: no tolerance lacks a justification line -----------------------------------------------------------------------
_TOL_PATH = os.path.join(_ROOT, "shared", "statistical_audit", "tolerances.py")


def _tolerance_constants():
    """{name: (value, has_comment_directly_above)} for every module-level float constant / alias of tolerances.py."""
    import shared.statistical_audit.tolerances as tol
    lines = open(_TOL_PATH, encoding="utf-8").read().splitlines()
    out = {}
    for node in ast.parse("\n".join(lines)).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            value = getattr(tol, name)
            if isinstance(value, float):
                out[name] = (value, lines[node.lineno - 2].lstrip().startswith("#"))
    return out


def test_every_named_tolerance_has_a_comment_directly_above_it():
    constants = _tolerance_constants()
    assert {"KID_ROUNDING_TOLERANCE_PP", "FLOAT_IDENTITY_TOLERANCE", "IPC_ELIGIBILITY_FLOOR", "RATIO_ELIGIBILITY_FLOOR",
            "COST_PAIR_TOLERANCE_PP", "SCALAR_TIMESERIES_TOLERANCE"} <= set(constants)
    bare = sorted(n for n, (_v, commented) in constants.items() if not commented)
    assert bare == [], f"tolerances without a justification comment: {bare}"


def test_every_pair_rule_tolerance_is_a_named_constant_not_a_literal():
    from shared.statistical_audit.catalog_pairs import COST_PAIRS
    named = {v for v, _c in _tolerance_constants().values()}
    for rule_id, rule in {**P2_PAIRS, **COST_PAIRS}.items():
        assert rule.tolerance in named, f"{rule_id}: tolerance {rule.tolerance} is not a tolerances.py constant"
    src = open(os.path.join(_ROOT, "shared", "statistical_audit", "catalog_pairs.py"), encoding="utf-8").read()
    assert "tolerance=0." not in src, "a PairRule carries a literal tolerance: name and justify it in tolerances.py"


def test_the_scalar_timeseries_tolerance_is_the_float_identity_class_with_eight_orders_of_margin():
    import shared.statistical_audit.tolerances as tol
    assert tol.SCALAR_TIMESERIES_TOLERANCE == tol.FLOAT_IDENTITY_TOLERANCE == P2_PAIRS["SCALAR_EQUALS_TIMESERIES"].tolerance
    assert tol.SCALAR_TIMESERIES_TOLERANCE / 1.4e-12 > 1e7                       # the measured worst divergence (2026-10-16)


def test_invariant_expressions_carry_no_unnamed_small_tolerance_literal():
    # a tolerance inside an expression must come from tolerances.py (f-string); the one documented exception is the periodic-return-variance
    # floor of FROZEN_NAV_ZERO_VOL (squared-return units, justified inline in catalog_invariants.py)
    import re
    import shared.statistical_audit.tolerances as tol
    named = {repr(v) for v, _c in _tolerance_constants().values()} | {str(v) for v, _c in _tolerance_constants().values()}
    small = re.compile(r"(?<![\w.])0\.\d*[1-9]\d*(?![\w.])")
    offenders = {}
    for r in COST_INVARIANTS + P2_INVARIANTS:
        for text in (r.expression, r.when or ""):
            for literal in small.findall(text):
                if literal not in named and r.rule_id != "FROZEN_NAV_ZERO_VOL":
                    offenders.setdefault(r.rule_id, []).append(literal)
    assert offenders == {}, f"unnamed tolerance literals in invariant expressions: {offenders}"
    assert tol.FLOAT_IDENTITY_TOLERANCE == 0.0001
