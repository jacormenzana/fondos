"""P3-7 reader of the forward shadow (doc/decisiones/PREREG_P3_7.md section 13, Amendment 1): `scripts/audit/p3/p3_shadow_read.py`.

Invariants pinned: (1) the DEFAULT mode shows integrity fields only: no turnover, cost, selection-difference or traded-notional value of any row reaches the output (a positive whitelist, tested with
distinctive values); (2) the review mode REFUSES, printing none of those fields, until the window of 6 months exists with at most 1 gap; (3) one row per calendar month (the LAST row of the month),
gaps reported, a month counts only when its predecessor month has a row (the first row after a gap is excluded from the chain statistics); (4) turnover is RECOMPUTED from the stored weights, never
read from `traded_notional`; (5) the cost is the entry fee on what is bought plus the exit fee on what is sold, equal to the PIT's `fee_cost_series`, with an unknown fee priced at the median of the known
ones and COUNTED; (6) the three R6 rules are deterministic, inclusive at their thresholds, and say NOT INFORMATIVE when B is absent; (7) the stated fees come from a hash-verified frozen snapshot;
(8) the script is read-only (no write statement, no commit). Synthetic rows only (R-7): no database."""
import ast
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts" / "audit" / "p3"))

import p3_shadow_read as R  # noqa: E402
from proyecto3.src import hysteresis_shadow as hs  # noqa: E402
from proyecto3.src import fee_penalty_shadow as fp  # noqa: E402
from proyecto3.src.pit_cost_check import fee_cost_series  # noqa: E402
from proyecto3.src.pit_p37_snapshot import SnapshotMismatch  # noqa: E402

SCRIPT = _ROOT / "scripts" / "audit" / "p3" / "p3_shadow_read.py"
TRAP = 0.31337          # a value that must never reach the integrity output


def _w(*pairs, sub="Defensiva"):
    return [{"isin": i, "subportfolio": sub, "weight": w, "score": 1.0} for i, w in pairs]


def _row(month, off, chain, day=15, hh="12", band=0.10, h=0.58, n_cand=1432, n_unk=570, scenario=None):
    """A stored row as `fee_penalty_shadow.build_row` shapes it, with TRAP values in every field the integrity mode must not show."""
    def stream(weights):
        return {"weights": weights, "n_funds": len(weights), "traded_notional": TRAP, "one_way_turnover": TRAP, "cost_drag": 0.00042, "funds_in": ["ZZINTRAP"], "funds_out": ["ZZOUTTRAP"],
                "rotation_cost_estimate": 0.00077, "diff_vs_off": {"only_off": [{"isin": "ZZDIFFTRAP"}], "only_on": [], "kept_by_hysteresis": []}}
    return {"v": 1, "scenario_id": scenario or f"cartera_x_{month.replace('-', '')}", "built_at": f"{month}-{day:02d}T{hh}:00:00+00:00", "band": band, "tx_cost_bps": 25.0,
            "calc_versions": {"20261116": 40}, "build_identity": {}, "rotation_gate_enabled": False, "baseline_scenario": "prod", "chain_base_scenario": "prod",
            "off": {"n_funds": len(off), "traded_notional": TRAP, "one_way_turnover": TRAP, "cost_drag": 0.00042, "weights": off},
            "one_step": stream(chain), "chain": stream(chain), "arm": "AC", "l4_h_years": h, "n_scored": 10, "n_candidates": n_cand, "n_fee_unknown": n_unk}


SIX = ["2027-01", "2027-02", "2027-03", "2027-04", "2027-05", "2027-06", "2027-07"]


def _rows(months, off_fn, chain_fn):
    return [_row(m, off_fn(i), chain_fn(i)) for i, m in enumerate(months)]


# ================================================================ (1) the default mode: integrity only ===================================================================
def test_integrity_view_is_a_positive_whitelist():
    r = _row("2027-01", _w(("A", 0.5), ("B", 0.5)) + _w(("C", 1.0), sub="Equilibrada"), _w(("A", 0.5), ("B", 0.5)) + _w(("C", 1.0), sub="Equilibrada"))
    v = R.integrity_view(r)
    assert set(v) == {"status", "month", "scenario_id", "built_at", "band", "l4_h_years", "funds_per_subportfolio", "weight_sums", "calc_versions", "n_candidates", "n_fee_unknown"}
    assert v["status"] == "SHADOW" and v["band"] == 0.10 and v["l4_h_years"] == 0.58 and v["n_candidates"] == 1432 and v["n_fee_unknown"] == 570
    assert v["funds_per_subportfolio"]["off"] == {"Defensiva": 2, "Equilibrada": 1}
    assert v["weight_sums"]["chain"] == pytest.approx(2.0) and v["weight_sums"]["off"] == pytest.approx(2.0)
    assert set(v["weight_sums"]) == {"off", "one_step", "chain"}


def test_default_output_never_carries_turnover_cost_or_selection_fields():
    rows = _rows(SIX[:3], lambda i: _w(("A", 0.5), ("B", 0.5)), lambda i: _w(("A", 0.5), ("C", 0.5)))
    rc, text = R.run([], rows_ac=rows, rows_b=[], n_errors=0)
    assert rc == 0 and "SHADOW" in text and "n_fee_unknown" in text
    for forbidden in (str(TRAP), "0.31337", "0.00042", "0.00077", "ZZINTRAP", "ZZOUTTRAP", "ZZDIFFTRAP", "turnover", "cost", "traded", "diff_vs_off", "kept_by_hysteresis"):
        assert forbidden not in text, forbidden


def test_default_mode_reports_months_gaps_and_error_rows():
    rows = [_row("2027-01", _w(("A", 1.0)), _w(("A", 1.0))), _row("2027-03", _w(("A", 1.0)), _w(("A", 1.0)))]
    rc, text = R.run([], rows_ac=rows, rows_b=[], n_errors=2)
    assert rc == 0 and "gap" in text.lower() and "2027-02" in text and "2 ERROR" in text


def test_no_rows_is_reported_not_crashed():
    rc, text = R.run([], rows_ac=[], rows_b=[], n_errors=0)
    assert rc == 0 and "0 SHADOW row" in text


# ================================================================ (3) one row per month, gaps, counted months ============================================================
def test_last_row_of_the_month_wins():
    early = _row("2027-01", _w(("A", 1.0)), _w(("A", 1.0)), day=3, scenario="early")
    late = _row("2027-01", _w(("A", 1.0)), _w(("A", 1.0)), day=28, scenario="late")
    other = _row("2027-02", _w(("A", 1.0)), _w(("A", 1.0)), scenario="feb")
    by = R.last_per_month([late, other, early])                              # input order must not matter
    assert sorted(by) == ["2027-01", "2027-02"] and by["2027-01"]["scenario_id"] == "late"


def test_gaps_and_counted_months():
    by = {m: object() for m in ["2027-01", "2027-02", "2027-04", "2027-05", "2027-06"]}
    assert R.gaps(by) == ["2027-03"]
    # 2027-01 has no predecessor, 2027-04 follows the gap: both are excluded from the chain statistics
    assert R.counted_months(by) == ["2027-02", "2027-05", "2027-06"]
    assert R.gaps({"2027-01": 1}) == [] and R.counted_months({"2027-01": 1}) == []


def test_year_boundary_months_are_consecutive():
    by = {m: object() for m in ["2026-12", "2027-01"]}
    assert R.counted_months(by) == ["2027-01"] and R.gaps(by) == []


# ================================================================ (2) readiness: the review refuses early ================================================================
def test_readiness_needs_a_six_month_span_and_at_most_one_gap_in_the_window():
    ok = {m: 1 for m in SIX[:6]}
    assert R.readiness(ok)["ready"] is True
    assert R.readiness({m: 1 for m in SIX[:5]})["ready"] is False                                          # five months
    assert R.readiness({m: 1 for m in ["2027-01", "2027-03", "2027-04", "2027-05", "2027-06"]})["ready"] is True    # span 6, one gap
    two = R.readiness({m: 1 for m in ["2027-01", "2027-03", "2027-05", "2027-06"]})                        # two gaps in the window
    assert two["ready"] is False and "postpone" in two["reason"].lower()
    assert R.readiness({})["ready"] is False


def test_review_mode_refuses_before_the_window_exists_and_shows_nothing():
    rows = _rows(SIX[:4], lambda i: _w(("A", 0.5), ("B", 0.5)), lambda i: _w(("A", 0.5), ("C", 0.5)))
    rc, text = R.run(["--review"], rows_ac=rows, rows_b=[], n_errors=0)
    assert rc == 3 and "NOT READY" in text
    for forbidden in ("0.31337", "ZZINTRAP", "turnover", "cost drag"):
        assert forbidden not in text


# ================================================================ (4)(5) turnover and cost ================================================================================
def test_turnover_is_recomputed_from_the_weights_not_the_stored_field():
    new, prev = _w(("A", 0.5), ("C", 0.5)), _w(("A", 0.5), ("B", 0.5))
    assert R.one_way_turnover(new, prev) == pytest.approx(0.5)                                              # TRAP (0.31337) is stored; the recomputation says 0.5
    assert R.one_way_turnover(new, new) == 0.0


def test_pair_cost_equals_the_pit_fee_cost_series():
    prev = _w(("A", 0.4), ("B", 0.6))
    new = _w(("A", 0.5), ("C", 0.5))
    entry = pd.Series({"A": 0.02, "B": 0.03, "C": 0.05})
    exit_ = pd.Series({"A": 0.00, "B": 0.01, "C": 0.00})
    cost, unk = R.pair_cost(new, prev, entry, exit_, 0.04, 0.02)
    W = pd.DataFrame([{"A": 0.4, "B": 0.6, "C": np.nan}, {"A": 0.5, "B": np.nan, "C": 0.5}])
    ref, _ = fee_cost_series(W, entry, exit_, 0.04, 0.02)
    assert cost == pytest.approx(float(ref.iloc[1])) and unk == 0.0
    assert cost == pytest.approx(0.1 * 0.02 + 0.5 * 0.05 + 0.6 * 0.01)


def test_unknown_fee_takes_the_default_and_is_counted():
    prev, new = _w(("A", 1.0)), _w(("B", 1.0))
    entry, exit_ = pd.Series({"A": 0.01}), pd.Series({"A": np.nan})                                         # B unknown on entry; A's exit unknown
    cost, unk = R.pair_cost(new, prev, entry, exit_, 0.04, 0.02)
    assert cost == pytest.approx(1.0 * 0.04 + 1.0 * 0.02)
    assert unk == pytest.approx(1.0)                                                                       # every traded unit touched an unknown fee (traded = 2.0, both sides unknown) -> share 1.0


def test_default_fee_is_the_median_of_the_known_fees_of_the_funds_held():
    fees = pd.DataFrame({"Entry_Fee_Pct": [0.01, 0.03, 0.05, np.nan], "Exit_Fee_Pct": [0.0, 0.0, 0.02, np.nan]}, index=["A", "B", "C", "D"])
    de, dx = R.default_fees(fees, {"A", "B", "C", "D"})
    assert de == pytest.approx(0.03) and dx == pytest.approx(0.0)


def test_unknown_fee_share_of_the_ac_weight():
    fees = pd.DataFrame({"Entry_Fee_Pct": [0.01, np.nan], "Exit_Fee_Pct": [0.0, 0.0]}, index=["A", "B"])
    assert R.unknown_fee_weight_share(_w(("A", 0.7), ("B", 0.3)), fees) == pytest.approx(0.3)
    assert R.unknown_fee_weight_share(_w(("A", 0.7), ("X", 0.3)), fees) == pytest.approx(0.3)             # absent from fund_master = unknown


# ================================================================ (6) R6 ================================================================================================
def _stats(l_to, ac_to, l_c, ac_c, b_c=None):
    return {"L": {"turnover": l_to, "cost_pp": l_c}, "AC": {"turnover": ac_to, "cost_pp": ac_c}, "B": ({"turnover": 0.148, "cost_pp": b_c} if b_c is not None else None)}


def test_r6_all_pass_and_thresholds_are_inclusive():
    out = R.evaluate_r6(_stats(0.322, 0.134, 4.61, 1.00, 2.23))
    assert [out[k] for k in ("rule1", "rule2", "rule3")] == ["PASS", "PASS", "PASS"] and out["verdict"] == "AC cost-confirmed"
    edge = R.evaluate_r6(_stats(0.10, 0.07, 1.50, 1.00, 1.50))                                              # exactly 30 % lower, exactly 0.50 pp lower, exactly 0.50 pp below B
    assert [edge[k] for k in ("rule1", "rule2", "rule3")] == ["PASS", "PASS", "PASS"]
    just = R.evaluate_r6(_stats(0.10, 0.0701, 1.50, 1.0001, 1.50))
    assert [just[k] for k in ("rule1", "rule2", "rule3")] == ["FAIL", "FAIL", "FAIL"] and just["verdict"] != "AC cost-confirmed"


def test_r6_without_b_rule3_is_not_informative_and_never_a_pass():
    out = R.evaluate_r6(_stats(0.322, 0.134, 4.61, 1.00, None))
    assert out["rule3"] == "NOT INFORMATIVE" and out["verdict"] != "AC cost-confirmed"


def test_r6_rule3_failing_with_one_and_two_passing_names_the_band_alone():
    out = R.evaluate_r6(_stats(0.322, 0.134, 4.61, 1.00, 1.2))
    assert out["rule3"] == "FAIL" and "band" in out["verdict"].lower()


# ================================================================ review end to end (ready window) ======================================================================
def _ready_rows():
    months = SIX[:7]
    l = lambda i: _w(("A", 0.5), ("B", 0.5)) if i % 2 == 0 else _w(("C", 0.5), ("D", 0.5))                  # the live arm rotates fully every month
    ac = lambda i: _w(("A", 0.5), ("B", 0.5))                                                              # AC never moves
    b = lambda i: _w(("A", 0.5), ("B", 0.5)) if i % 3 else _w(("A", 0.5), ("C", 0.5))
    return _rows(months, l, ac), _rows(months, l, b)


def test_review_end_to_end_on_a_ready_window(tmp_path):
    ac_rows, b_rows = _ready_rows()
    fees = pd.DataFrame({"Entry_Fee_Pct": [0.02, 0.02, 0.02, 0.02], "Exit_Fee_Pct": [0.01, 0.01, 0.01, 0.01]}, index=["A", "B", "C", "D"])
    rc, text = R.run(["--review", "--snapshot", str(tmp_path / "snap")], rows_ac=ac_rows, rows_b=b_rows, n_errors=0, fees=fees)
    assert rc == 0 and "R6" in text and "rule1" in text.lower()
    assert "PASS" in text and "snapshot" in text.lower()
    # the live arm swaps both funds every month: one-way turnover 1.0; AC never trades: 0.0 -> reduction 100 %
    res = R.review_r6(ac_rows, b_rows, fees)
    assert res["stats"]["L"]["turnover"] == pytest.approx(1.0) and res["stats"]["AC"]["turnover"] == pytest.approx(0.0)
    assert res["rules"]["rule1"] == "PASS" and res["rules"]["rule2"] == "PASS"
    assert res["stats"]["AC"]["cost_pp"] == pytest.approx(0.0)
    assert res["stats"]["L"]["cost_pp"] == pytest.approx(12 * 100 * (1.0 * 0.02 + 1.0 * 0.01))              # buys 1.0 at 2 % + sells 1.0 at 1 %, a year of it


def test_review_uses_only_months_counted_for_all_three_series():
    ac_rows, b_rows = _ready_rows()
    b_rows = [r for r in b_rows if r["built_at"][:7] != "2027-04"]                                         # B misses a month: its counted set shrinks
    fees = pd.DataFrame({"Entry_Fee_Pct": [0.02] * 4, "Exit_Fee_Pct": [0.01] * 4}, index=list("ABCD"))
    res = R.review_r6(ac_rows, b_rows, fees)
    assert "2027-04" not in res["months"] and "2027-05" not in res["months"]                               # 2027-05 follows B's gap: excluded as well


# ================================================================ (7) the frozen snapshot ===============================================================================
def test_fee_snapshot_is_frozen_once_and_verified_on_load(tmp_path):
    fees = pd.DataFrame({"Entry_Fee_Pct": [0.02, 0.03], "Exit_Fee_Pct": [0.0, 0.01]}, index=pd.Index(["A", "B"], name="isin"))
    folder = tmp_path / "snap"
    manifest = R.freeze_fees(folder, fees)
    assert manifest["snapshot_id"] and (folder / "manifest.json").exists()
    back, sid = R.load_fees(folder)
    assert sid == manifest["snapshot_id"] and list(back.columns) == ["Entry_Fee_Pct", "Exit_Fee_Pct"] and back.loc["B", "Entry_Fee_Pct"] == pytest.approx(0.03)
    with pytest.raises(FileExistsError):
        R.freeze_fees(folder, fees)                                                                         # a snapshot is written once
    f = folder / "fees.parquet"
    f.write_bytes(f.read_bytes() + b"x")
    with pytest.raises(SnapshotMismatch):
        R.load_fees(folder)                                                                                 # tampering is detected


# ================================================================ (8) read-only ==========================================================================================
def test_the_script_is_read_only():
    src = SCRIPT.read_text(encoding="utf-8")
    ast.parse(src)
    low = src.lower()
    for bad in ("insert into", "update ", "delete from", ".commit(", "drop ", "alter ", "truncate", "create table"):
        assert bad not in low.replace("# ", "#"), bad
    sqls = [n.value for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.lstrip().upper().startswith(("SELECT", "WITH"))]
    assert sqls and all(s.lstrip().upper().startswith("SELECT") for s in sqls)


def test_streams_are_the_ones_the_shadow_writes():
    assert R.AC_STEP == fp.STEP and R.B_STEP == hs.STEP and R.AC_STEP_ERROR == fp.STEP_ERROR
