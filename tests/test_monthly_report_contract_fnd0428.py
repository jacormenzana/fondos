# tests/test_monthly_report_contract_fnd0428.py
# -*- coding: utf-8 -*-
"""FND-0428: the contract of sheet 1_Cartera of the monthly report. Hermetic (R-7): no database; the sheet's own helpers are exercised directly and its source is scanned
for the two patterns that caused the incident (a header typed by hand for a metric column; the objective subtracted from a REAL return)."""
import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from proyecto3.src import monthly_report as mr

SRC = Path(mr.__file__).read_text(encoding="utf-8")


def _cartera_source() -> str:
    tree = ast.parse(SRC)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_build_cartera")
    return ast.get_source_segment(SRC, fn)


def test_the_old_unscoped_headers_are_gone_from_the_sheet():
    tree = ast.parse(SRC)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_build_cartera")
    literals = {n.value for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)}      # comments are not string constants
    for old in ("Retorno real %", "Exceso obj %", "Max DD %", "Obj IPC+M3 %", "Divisa contrib %"):
        assert old not in literals, old


def test_every_metric_header_comes_from_the_label_source():
    body = _cartera_source()
    assert body.count("_lab(") >= 12 and "_rep(" in body


def test_the_excess_is_checked_like_for_like_and_never_uses_the_real_return():
    body = _cartera_source()
    assert "excess_is_like_for_like" in body
    assert "float(ret_real) - obj_anual" not in body


def test_percentage_columns_are_the_ones_the_sheet_labels_as_percentages():
    # 8 real since-inception, 9 max dd, 10 ytd, 11 1y, 16 objective, 17 excess, 18 fx contribution
    assert mr._PCT_COLS == {8, 9, 10, 11, 16, 17, 18}


def _nav(first="2018-01-31", last="2026-10-31", g=0.004):
    d = pd.date_range(first, last, freq="ME")
    return pd.DataFrame({"date": d, "nav": 100.0 * (1.0 + g) ** np.arange(len(d))})


def _truth(nav):
    from shared import report_reconciliation as rr
    return rr.recompute_headline(nav)


def test_the_gate_passes_when_the_stored_values_match_the_nav():
    nav = _nav()
    t = _truth(nav)
    mr._reconcile_or_raise([("X", {"ytd": t["ytd"], "ret_1y": t["ret_1y"], "ret_si_nominal": t["ret_si_nominal"]}, nav)])


def test_the_gate_fails_the_build_on_a_planted_mismatch():
    nav = _nav()
    t = _truth(nav)
    with pytest.raises(RuntimeError, match=r"\[GATE:REPORT_RECONCILIATION\].*X ret_1y"):
        mr._reconcile_or_raise([("X", {"ret_1y": t["ret_1y"] + 0.01}, nav)])      # 100 bp


def test_the_gate_can_be_switched_off(monkeypatch):
    nav = _nav()
    monkeypatch.setattr(mr, "REPORT_RECONCILIATION_ENABLED", False)
    mr._reconcile_or_raise([("X", {"ret_1y": 0.99}, nav)])


def test_frac_is_none_safe():
    assert mr._frac(None) is None and mr._frac(5.0) == pytest.approx(0.05)
