# tests/test_metric_labels_report_reconciliation_fnd0428.py
# -*- coding: utf-8 -*-
"""FND-0428: the report's label source (`shared.metric_labels`) and the independent reconciliation (`shared.report_reconciliation`).

Pure and hermetic (R-7): no database, no pipeline. The cross-check against P2's own return functions is in
proyecto2/tests/calculations/test_report_reconciliation_matches_p2_fnd0428.py."""
import numpy as np
import pandas as pd
import pytest

from shared import metric_labels as ml
from shared import report_reconciliation as rr


# ------------------------------------------------------------------ labels
def test_every_declared_label_is_unique_and_non_empty():
    labels = [m.label for m in ml._LABELS] + [m.label for m in ml.REPORT_ONLY_LABELS.values()]
    assert all(labels) and len(labels) == len(set(labels))


def test_the_incident_column_now_says_what_it_is():
    text = ml.label_for("return_ann", "since_inception", 1)
    assert "anualiz" in text and "real" in text and "inicio" in text
    assert "anualiz" in ml.metric_label("return_ann", "since_inception", 1).definition.lower()


def test_a_since_inception_label_always_says_so():
    for m in ml._LABELS:
        if m.horizon == "since_inception":
            assert "inicio" in m.label, m.label


def test_short_horizon_and_period_labels_carry_their_window():
    assert "YTD" in ml.label_for("return_cum", "ytd", 0)
    assert "1A" in ml.label_for("return_ann", "rolling_1y", 0)
    assert "6M" in ml.label_for("short_max_drawdown", "rolling_6m", 0)


def test_an_undeclared_column_is_a_loud_error():
    with pytest.raises(KeyError, match="no report label declared"):
        ml.label_for("return_ann", "rolling_5y", 0)


def test_the_legend_lists_every_header_with_its_definition():
    lines = ml.legend_lines()
    for m in ml._LABELS:
        assert any(line.startswith(m.label + ":") for line in lines)
    for m in ml.REPORT_ONLY_LABELS.values():
        assert any(line.startswith(m.label + ":") for line in lines)


# ---------------------------------------------------------- reconciliation
def _monthly(first="2023-10-31", last="2026-10-31", g=0.005):
    d = pd.date_range(first, last, freq="ME")
    return pd.DataFrame({"date": d, "nav": 100.0 * (1.0 + g) ** np.arange(len(d))})


def test_recompute_matches_the_closed_form_on_a_dense_series():
    nav = _monthly()
    out = rr.recompute_headline(nav)
    assert out["ret_1y"] == pytest.approx(1.005 ** 12 - 1.0)             # 12 month-end rows span 11 periods: (1.005^11)^(12/11) - 1
    assert out["ret_si_nominal"] == pytest.approx(1.005 ** 12 - 1.0)
    assert out["ytd"] == pytest.approx(1.005 ** 10 - 1.0)                # 2025-12-31 -> 2026-10-31 is 10 months


def test_reconcile_accepts_a_value_inside_the_tolerance_and_flags_one_outside():
    nav = _monthly()
    truth = rr.recompute_headline(nav)
    assert rr.reconcile("X", {"ret_1y": truth["ret_1y"] + 0.0004}, nav) == []            # 4 bp
    bad = rr.reconcile("X", {"ret_1y": truth["ret_1y"] + 0.0006}, nav)                   # 6 bp
    assert len(bad) == 1 and bad[0].metric == "ret_1y" and bad[0].diff_bp == pytest.approx(6.0, abs=0.01)
    assert "6.0 bp" in bad[0].text()


def test_a_stored_value_the_nav_cannot_reproduce_is_a_mismatch_and_none_makes_no_claim():
    one_row = pd.DataFrame({"date": pd.to_datetime(["2026-10-31"]), "nav": [100.0]})
    bad = rr.reconcile("X", {"ret_1y": 0.05, "ytd": None}, one_row)
    assert [b.metric for b in bad] == ["ret_1y"] and bad[0].recomputed is None
    assert rr.reconcile("X", {}, one_row) == []


def test_the_planted_defect_of_the_incident_fails_the_check():
    # the since-inception ANNUALIZED REAL figure shown as if it were a 12-month nominal return
    nav = _monthly(g=0.003)
    wrong = {"ret_1y": 0.0808}
    assert rr.reconcile("IE00008TZ2E8", wrong, nav)


def test_excess_must_be_nominal_minus_nominal():
    assert rr.excess_is_like_for_like(-3.04, 3.68, 6.72)                  # 3.68 - 6.72
    assert not rr.excess_is_like_for_like(1.36, 3.68, 6.72)               # a REAL return (8.08) minus the objective
    assert rr.excess_is_like_for_like(None, None, 6.72)
    assert not rr.excess_is_like_for_like(1.0, None, 6.72)


def test_conventions_are_arguments_not_hidden_state():
    nav = _monthly()
    rows = rr.recompute_headline(nav, by_dates=False, interval=False)["ret_si_nominal"]
    dates = rr.recompute_headline(nav, by_dates=True, interval=True)["ret_si_nominal"]
    assert rows != dates                                                   # N/12 years vs (N-1)/12
