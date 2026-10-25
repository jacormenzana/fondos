# proyecto3/tests/test_wrong_doc_eligibility.py
# -*- coding: utf-8 -*-
"""FND-0323 D1: an ISIN with KIID_Status = WRONG_DOC is ineligible in P3 (live scorer AND point-in-time backtest) behind
P3_WRONG_DOC_EXCLUSION_ENABLED. Pure parts (R-7: no DB, no pipeline.py / core.io): the shared reason, the hard filter, the scoring core,
the PIT scoring frame and the PIT attributes loader (fake connection). The real SQL is in test_wrong_doc_eligibility_pg.py.

    python -m pytest proyecto3/tests/test_wrong_doc_eligibility.py -v
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import shared.config as cfg  # noqa: E402
from proyecto3.src import fund_scorer as fs  # noqa: E402
from proyecto3.src import pit_inputs  # noqa: E402
from proyecto3.src.pit_candidates import pit_scores  # noqa: E402
from shared import doc_eligibility as de  # noqa: E402


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pc = _load(_ROOT / "proyecto3" / "tests" / "test_pit_candidates.py", "pit_candidates_fixture")      # its synthetic universe + helpers
sw = _load(_ROOT / "scripts" / "audit" / "eligibility_switch_diff.py", "eligibility_switch_diff")    # diff_eligibility


def _row(**kw):
    base = {"max_dd": -0.05, "return_ann_real": 0.03, "Fund_Nature": "Mixtos", "Credit_Quality": "Investment Grade"}
    base.update(kw)
    return pd.Series(base)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(cfg, "P3_WRONG_DOC_EXCLUSION_ENABLED", True)


# ---- the shared reason --------------------------------------------------------------------------------------------------------------

def test_the_documented_default_of_the_switch_is_off():
    """Pins the DOCUMENTED default (shared/config.py, proyecto3/AGENTS.md): flipping it is a reviewed commit that changes this test."""
    src = (_ROOT / "shared" / "config.py").read_text(encoding="utf-8")
    node = next(n for n in ast.parse(src).body if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "P3_WRONG_DOC_EXCLUSION_ENABLED")
    assert node.value.value is False


def test_the_reason_has_a_stable_code_prefix_and_names_the_status():
    assert de.REASON_TEXT.startswith("wrong_doc: ") and "KIID_Status=WRONG_DOC" in de.REASON_TEXT


def test_wrong_doc_reasons_maps_every_wrong_doc_isin_and_skips_null_ones():
    class Conn:
        sql = None

        def execute(self, sql, params=()):
            Conn.sql = sql
            return type("Cur", (), {"fetchall": lambda s: [("AAA",), ("BBB",), (None,)]})()

    assert de.wrong_doc_reasons(Conn()) == {"AAA": de.REASON_TEXT, "BBB": de.REASON_TEXT}
    assert "WRONG_DOC" in Conn.sql and "fund_kiid_metadata" in Conn.sql


# ---- the hard filter ----------------------------------------------------------------------------------------------------------------

def test_off_changes_nothing_even_for_a_wrong_doc_fund(monkeypatch):
    monkeypatch.setattr(cfg, "P3_WRONG_DOC_EXCLUSION_ENABLED", False)
    assert fs.check_hard_filters(_row(doc_status_reason=de.REASON_TEXT), "Equilibrada") is None


def test_on_excludes_a_wrong_doc_fund_with_the_shared_reason(on):
    assert fs.check_hard_filters(_row(doc_status_reason=de.REASON_TEXT), "Equilibrada") == de.REASON_TEXT


@pytest.mark.parametrize("value", [None, np.nan, "", 0, False])
def test_on_is_fail_open_for_absent_or_empty_inputs(on, value):
    assert fs.check_hard_filters(_row(doc_status_reason=value), "Equilibrada") is None
    assert fs.check_hard_filters(_row(), "Equilibrada") is None                       # column absent (a PIT frame built with the switch OFF)


def test_the_document_reason_comes_before_every_other_reason(on, monkeypatch):
    monkeypatch.setattr(cfg, "FUND_DATA_ELIGIBILITY_ENABLED", True)
    row = _row(doc_status_reason=de.REASON_TEXT, nav_age_days=900.0, nav_last_date="2024-04-30", max_dd=-0.9)
    assert fs.check_hard_filters(row, "Defensiva") == de.REASON_TEXT


def test_on_leaves_the_other_filters_untouched_for_a_valid_document(on):
    assert fs.check_hard_filters(_row(max_dd=-0.9), "Defensiva").startswith("max_drawdown")
    assert fs.check_hard_filters(_row(), "Defensiva") is None


# ---- the scoring core ---------------------------------------------------------------------------------------------------------------

def _frame(with_column: bool) -> pd.DataFrame:
    idx = pd.Index(["OK1", "OK2", "BAD", "OK3"], name="isin")
    df = pd.DataFrame({
        "Fund_Name": list("abcd"), "Fund_Nature": ["Mixtos"] * 4, "fund_family_id": ["f1", "f2", "f3", "f4"],
        "return_ann_real": [0.04, 0.05, 0.09, 0.03], "sharpe": [1.0, 1.1, 1.9, 0.9], "max_dd": [-0.05, -0.06, -0.04, -0.05],
    }, index=idx)
    if with_column:
        df["doc_status_reason"] = [None, None, de.REASON_TEXT, None]
    return df


def test_score_funds_from_df_excludes_only_the_wrong_doc_fund(monkeypatch):
    monkeypatch.setattr(cfg, "P3_WRONG_DOC_EXCLUSION_ENABLED", False)
    off = fs.score_funds_from_df(_frame(True), "Expansion", verbose=False)
    monkeypatch.setattr(cfg, "P3_WRONG_DOC_EXCLUSION_ENABLED", True)
    on = fs.score_funds_from_df(_frame(True), "Expansion", verbose=False)
    bad = on[on["isin"] == "BAD"]
    assert len(bad) and not bad["eligible"].any() and (bad["score_final"] == 0.0).all()
    assert (bad["exclusion_reason"] == de.REASON_TEXT).all()
    changed = sw.diff_eligibility(off, on)
    assert set(changed["isin"]) == {"BAD"} and (changed["change"] == "excluded").all()
    keep = ["isin", "subportfolio", "score_base", "score_final", "eligible"]
    pd.testing.assert_frame_equal(off[off["isin"] != "BAD"][keep].reset_index(drop=True),
                                  on[on["isin"] != "BAD"][keep].reset_index(drop=True))    # normalisation of the others does not move


def test_off_with_the_column_is_identical_to_the_frame_without_it(monkeypatch):
    monkeypatch.setattr(cfg, "P3_WRONG_DOC_EXCLUSION_ENABLED", False)
    a = fs.score_funds_from_df(_frame(True), "Expansion", verbose=False)
    b = fs.score_funds_from_df(_frame(False), "Expansion", verbose=False)
    pd.testing.assert_frame_equal(a.drop(columns=["detail"]), b.drop(columns=["detail"]))


# ---- point in time: the SAME rule through the scorer's own filter -------------------------------------------------------------------

@pytest.fixture(scope="module")
def world():
    panel, nature, attrs, regimes = pc._universe()
    risk, peers, mom = pc._inputs(panel, nature, pc.AT)
    return panel, nature, attrs, regimes, risk, peers, mom


def _run(world, attrs=None, **kw):
    panel, nature, a, regimes, risk, peers, mom = world
    return pit_scores(pc.AT, risk, peers, a if attrs is None else attrs, regimes, momentum=mom, log_universe=False, **kw)


def _flagged_attrs(world):
    attrs = world[2].copy()
    attrs["doc_status_reason"] = pd.Series([de.REASON_TEXT if i == "RV0" else None for i in attrs.index], index=attrs.index, dtype=object)
    return attrs


def test_pit_on_excludes_the_wrong_doc_fund_at_every_date_and_touches_nothing_else(world, monkeypatch):
    monkeypatch.setattr(cfg, "P3_WRONG_DOC_EXCLUSION_ENABLED", False)
    base, _ = _run(world)
    monkeypatch.setattr(cfg, "P3_WRONG_DOC_EXCLUSION_ENABLED", True)
    got, _ = _run(world, attrs=_flagged_attrs(world))
    flagged = got[got["isin"] == "RV0"]
    assert len(flagged) and not flagged["eligible"].any() and (flagged["exclusion_reason"] == de.REASON_TEXT).all()
    assert (flagged["score_final"] == 0.0).all()
    key = ["as_of", "isin", "subportfolio"]
    cmp = base.merge(got, on=key, suffixes=("_base", "_doc"))
    rest = cmp[cmp["isin"] != "RV0"]
    assert len(rest) and (rest["score_final_base"] == rest["score_final_doc"]).all() and (rest["eligible_base"] == rest["eligible_doc"]).all()


def test_pit_off_ignores_a_column_left_in_a_snapshot(world, monkeypatch):
    """A self-contained run snapshot carries attrs.parquet; if it was written with the switch ON and is replayed with it OFF, the rule is absent."""
    monkeypatch.setattr(cfg, "P3_WRONG_DOC_EXCLUSION_ENABLED", False)
    a, ua = _run(world)
    b, ub = _run(world, attrs=_flagged_attrs(world))
    pd.testing.assert_frame_equal(a, b)
    pd.testing.assert_frame_equal(ua, ub)


def test_pit_on_without_the_column_is_identical_to_off(world, monkeypatch):
    monkeypatch.setattr(cfg, "P3_WRONG_DOC_EXCLUSION_ENABLED", False)
    a, _ = _run(world)
    monkeypatch.setattr(cfg, "P3_WRONG_DOC_EXCLUSION_ENABLED", True)
    b, _ = _run(world)
    pd.testing.assert_frame_equal(a, b)


# ---- the PIT attributes loader (it feeds the score cache key) -----------------------------------------------------------------------

class _Conn:
    """fund_master rows in ATTRIBUTE_FRAME_COLUMNS order, then the WRONG_DOC ISINs; records the statements."""
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append(sql)
        if "fund_kiid_metadata" in sql:
            rows = [("BBB",)]
        else:
            rows = [("AAA", "a", "Mixtos", 4, "Broad", "Investment Grade", 0.01, "OK", "f1", "M1", 1),
                    ("BBB", "b", "Mixtos", 4, "Broad", "Investment Grade", 0.01, "OK", "f2", "M2", 1)]
        return type("Cur", (), {"fetchall": lambda s: rows})()


def test_load_attributes_adds_the_column_only_with_the_switch_on(monkeypatch):
    monkeypatch.setattr(cfg, "P3_WRONG_DOC_EXCLUSION_ENABLED", False)
    conn = _Conn()
    off = pit_inputs.load_attributes(conn)
    assert "doc_status_reason" not in off.columns and list(off.columns) == pit_inputs.ATTRIBUTE_FRAME_COLUMNS[1:]
    assert len(conn.statements) == 1                                                  # OFF: no extra query
    monkeypatch.setattr(cfg, "P3_WRONG_DOC_EXCLUSION_ENABLED", True)
    on = pit_inputs.load_attributes(_Conn())
    assert on.loc["BBB", "doc_status_reason"] == de.REASON_TEXT and on.loc["AAA", "doc_status_reason"] is None
    pd.testing.assert_frame_equal(on.drop(columns="doc_status_reason"), off)           # nothing else moves


# ---- the validation script's own logic (scripts/audit/wrong_doc_switch_diff.py) ------------------------------------------------------

wd_diff = _load(_ROOT / "scripts" / "audit" / "wrong_doc_switch_diff.py", "wrong_doc_switch_diff")


def test_pick_sample_takes_the_wrong_doc_funds_first_and_fills_with_controls_up_to_forty():
    rows = [(f"W{i}", "WRONG_DOC", "h") for i in range(5)] + [(f"C{i}", "CONTROL", "h") for i in range(40)]
    wd, sample = wd_diff.pick_sample(rows)
    assert wd == [f"W{i}" for i in range(5)] and len(sample) == 40 and sample[:5] == wd and sample[5] == "C0"


def test_pick_sample_caps_the_wrong_doc_part():
    rows = [(f"W{i}", "WRONG_DOC", "h") for i in range(15)] + [(f"C{i}", "CONTROL", "h") for i in range(40)]
    wd, sample = wd_diff.pick_sample(rows)
    assert len(wd) == wd_diff.SAMPLE_WD and len(sample) == 40


def _res(rows):
    return pd.DataFrame(rows, columns=["isin", "subportfolio", "eligible", "score_final", "exclusion_reason"])


def test_compare_scores_separates_a_clean_exclusion_from_every_kind_of_violation():
    off = _res([("W", "Dinamica", True, 0.5, None), ("X", "Dinamica", True, 0.4, None), ("Y", "Dinamica", True, 0.3, None),
                ("Z", "Dinamica", True, 0.2, None), ("OK", "Dinamica", True, 0.1, None)])
    on = _res([("W", "Dinamica", False, 0.0, de.REASON_TEXT),          # the intended exclusion
               ("X", "Dinamica", False, 0.0, "nav_stale: x"),           # a non-WRONG_DOC fund excluded and with another reason
               ("Y", "Dinamica", True, 0.31, None),                     # a score that moved
               ("Z", "Dinamica", True, 0.2, None), ("OK", "Dinamica", True, 0.1, None)])
    facts = wd_diff.compare_scores(off, on, {"W"}, de.REASON_TEXT, ["isin", "subportfolio"])
    assert set(facts["newly_ineligible"]["isin"]) == {"W", "X"}
    assert set(facts["foreign"]["isin"]) == {"X"} and set(facts["bad_reason"]["isin"]) == {"X"}
    assert set(facts["moved"]["isin"]) == {"X", "Y"}
    clean = wd_diff.compare_scores(off, _res([("W", "Dinamica", False, 0.0, de.REASON_TEXT)] + [tuple(r) for r in off.iloc[1:].itertuples(index=False)]),
                                   {"W"}, de.REASON_TEXT, ["isin", "subportfolio"])
    assert clean["foreign"].empty and clean["bad_reason"].empty and clean["moved"].empty and len(clean["newly_ineligible"]) == 1


def test_a_sibling_taking_the_family_slot_is_information_not_a_violation():
    off = _res([("W", "Dinamica", True, 0.5, None)])
    on = _res([("SIB", "Dinamica", True, 0.45, None)])
    facts = wd_diff.compare_scores(off, on, {"W"}, de.REASON_TEXT, ["isin", "subportfolio"])
    assert len(facts["family_only"]) == 2 and facts["foreign"].empty and facts["moved"].empty


def test_the_sample_query_is_one_literal_statement_that_reads_only_the_status_table():
    q = wd_diff.Q_SAMPLE
    assert "fund_kiid_metadata" in q and "WRONG_DOC" in q and "LIMIT 10" in q and "INSERT" not in q.upper() and "UPDATE" not in q.upper()


def test_the_attrs_frame_hash_changes_when_the_rule_is_on():
    """Scores depend on the column, so it must be part of the PIT score cache key (pit_run hashes inputs.attrs)."""
    from proyecto3.src.pit_cache import content_hash
    base = pc._universe()[2]
    flagged = base.copy()
    flagged["doc_status_reason"] = pd.Series([de.REASON_TEXT if i == "RV0" else None for i in flagged.index], index=flagged.index, dtype=object)
    assert content_hash(base) != content_hash(flagged)
