"""Package L, P3-7 (doc/decisiones/PREREG_P3_7.md): the runner `scripts/audit/p3/p3_p37_trials.py`, the parts that decide whether the long run may start.
Invariants pinned: (1) the eight arms are exactly the breakdown signed by the owner (section 11 item 3: 4 single factors, 3 combinations at band 0.10, 1 at band 0.20 + penalty), with the Holm family
and level of section 9 and the penalty H of section 4; (2) the IDENTITY GATE passes only on an exact reproduction of the sealed run (scores, eligible set, monthly gross return, turnover to the two decimals
the sealed table prints) with every shipped switch OFF, and fails on ANY one difference; (3) the trials are refused until `identity.json` says the baseline passed; (4) the default of a shipped switch being
ON blocks the gate. Synthetic frames only (R-7)."""
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

import p3_p37_trials as R  # noqa: E402
from proyecto3.src import pit_p37_snapshot as SNAP  # noqa: E402


# ================================================================ the arms ============================================================================================
def test_the_eight_arms_are_the_breakdown_the_owner_signed():
    assert R.TRIALS == ["A1", "A2", "B", "C", "AB", "AC", "ABC", "D"] and len(set(R.TRIALS)) == 8
    single = [n for n in R.TRIALS if sum([R.ARMS[n]["band"] > 0, R.ARMS[n]["cls"], R.ARMS[n]["pen"]]) == 1]
    band10 = [n for n in R.TRIALS if R.ARMS[n]["band"] == 0.10 and sum([R.ARMS[n]["cls"], R.ARMS[n]["pen"]]) >= 1]
    band20_pen = [n for n in R.TRIALS if R.ARMS[n]["band"] == 0.20 and (R.ARMS[n]["cls"] or R.ARMS[n]["pen"])]
    assert single == ["A1", "A2", "B", "C"] and band10 == ["AB", "AC", "ABC"] and band20_pen == ["D"]
    assert R.ARMS["T0_baseline"] == {"band": 0.0, "cls": False, "pen": False}
    assert "T0_baseline" not in R.TRIALS                                         # the baseline is the reference, not a trial of the Holm family
    assert {R.ARMS[n]["band"] for n in R.ARMS} == {0.0, 0.10, 0.20}


def test_the_holm_family_and_the_penalty_horizon_are_the_preregistered_values():
    assert R.HOLM_TRIALS == 8 and R.HOLM_LEVEL == pytest.approx(0.00625)
    assert R.PENALTY_H_YEARS == 0.58
    assert (R.MATERIALITY_PP, R.DEGRADE_MAX_PP, R.STRESS_REL_PP) == (0.5, 0.35, 0.5)


def test_the_penalty_arms_turn_on_only_l4_and_the_others_turn_nothing_on():
    for n, spec in R.ARMS.items():
        cfg = R.levers_of(spec)
        assert (cfg.l1_q, cfg.l2_z0, cfg.l3_k) == (None, None, None), n          # L1, L2 and L3 are closed: never in a P3-7 arm
        assert (cfg.l4_h_years == R.PENALTY_H_YEARS) == spec["pen"], n
        assert cfg.any_on == spec["pen"], n


# ================================================================ the identity gate ===================================================================================
def world(n=12):
    idx = pd.date_range("2020-01-31", periods=n, freq="ME")
    series = pd.DataFrame({"gross": np.linspace(-0.01, 0.02, n), "turnover": np.round(np.linspace(0.05, 0.4, n), 2)}, index=idx)
    table = pd.DataFrame({"gross_1m": series["gross"].to_numpy(), "turnover": series["turnover"].to_numpy()}, index=idx)
    meta = {"baseline_vs_sealed": {"coverage": 1.0, "max_abs_score_base_diff": 0.0, "eligible_agreement": 1.0}}
    return meta, series, table, {"P3_L1_PEER_FILTER_ENABLED": False, "PORTFOLIO_HYSTERESIS_ENABLED": False}


def test_an_exact_reproduction_passes_the_gate():
    meta, series, table, sw = world()
    g = R.identity_gate(meta, series, table, sw)
    assert g["ok"] and all(g["checks"].values())


def test_the_sealed_turnover_rounded_to_two_decimals_is_still_an_exact_reproduction():
    meta, series, table, sw = world()
    series["turnover"] = table["turnover"] + 0.0049                              # our turnover has more digits than the sealed table prints
    assert R.identity_gate(meta, series, table, sw)["checks"]["turnover_identical"]
    series["turnover"] = table["turnover"] + 0.0051
    assert not R.identity_gate(meta, series, table, sw)["checks"]["turnover_identical"]


@pytest.mark.parametrize("mutate,check", [
    (lambda m, s, t, w: m["baseline_vs_sealed"].update(coverage=0.999), "coverage_is_1"),
    (lambda m, s, t, w: m["baseline_vs_sealed"].update(max_abs_score_base_diff=1e-15), "score_base_identical"),
    (lambda m, s, t, w: m["baseline_vs_sealed"].update(eligible_agreement=0.9999), "eligible_identical"),
    (lambda m, s, t, w: s.__setitem__("gross", s["gross"] + 1e-6), "gross_identical"),
    (lambda m, s, t, w: w.update(PORTFOLIO_HYSTERESIS_ENABLED=True), "switches_default_off"),
    (lambda m, s, t, w: t.__setitem__("gross_1m", t["gross_1m"].where(t.index != t.index[3])), "same_dates"),
])
def test_any_single_difference_fails_the_gate(mutate, check):
    meta, series, table, sw = world()
    mutate(meta, series, table, sw)
    g = R.identity_gate(meta, series, table, sw)
    assert not g["ok"] and not g["checks"][check]


# ================================================================ the gate before step 4 =============================================================================
def test_the_gate_is_closed_without_identity_json_or_with_a_failed_one(tmp_path):
    ok, why = R.gate_open(tmp_path)
    assert not ok and "identity.json is missing" in why
    (tmp_path / "identity.json").write_text(json.dumps({"ok": False, "checks": {"gross_identical": False}}), encoding="utf-8")
    ok, why = R.gate_open(tmp_path)
    assert not ok and "FAILED" in why
    (tmp_path / "identity.json").write_text(json.dumps({"ok": True, "checks": {}}), encoding="utf-8")
    assert R.gate_open(tmp_path)[0]


def test_a_trial_is_refused_until_the_baseline_passed_and_never_reads_a_missing_snapshot(tmp_path, capsys):
    base = tmp_path / "stamp"
    argv = ["--run-dir", str(tmp_path / "run"), "--stage", "build", "--trial", "A1", "--stamp", "stamp", "--out-root", str(tmp_path)]
    assert R.main(argv) == 3                                                     # no snapshot yet
    ipc = pd.DataFrame({"date": pd.to_datetime(["2020-01-31"]), "ipc_index": [100.0]})
    fees = pd.DataFrame({"Entry_Fee_Pct": [0.0], "Exit_Fee_Pct": [0.0]}, index=pd.Index(["A"], name="isin"))
    SNAP.freeze_inputs(base / "snapshot", ipc, fees, pd.Series(["Europe"], index=pd.Index(["A"], name="isin")))
    assert R.main(argv) == 5                                                     # snapshot present, baseline not passed: the long run does not start
    assert "identity.json is missing" in capsys.readouterr().out
    (base / "identity.json").write_text(json.dumps({"ok": False, "checks": {}}), encoding="utf-8")
    assert R.main(argv) == 5


def test_an_unknown_trial_name_is_refused(tmp_path):
    base = tmp_path / "stamp"
    ipc = pd.DataFrame({"date": pd.to_datetime(["2020-01-31"]), "ipc_index": [100.0]})
    fees = pd.DataFrame({"Entry_Fee_Pct": [0.0], "Exit_Fee_Pct": [0.0]}, index=pd.Index(["A"], name="isin"))
    SNAP.freeze_inputs(base / "snapshot", ipc, fees, pd.Series(["Europe"], index=pd.Index(["A"], name="isin")))
    assert R.main(["--run-dir", str(tmp_path / "run"), "--stage", "build", "--trial", "L1_q20", "--stamp", "stamp", "--out-root", str(tmp_path)]) == 4


def test_a_month_without_a_forward_return_in_both_series_is_an_exact_reproduction():
    """The last evaluation date has no forward return: NaN in the sealed table AND in the re-run. Identical NaN pattern plus identical values elsewhere is a reproduction; NaN in only one of them is not."""
    meta, series, table, sw = world()
    series.loc[series.index[-1], "gross"] = np.nan
    table.loc[table.index[-1], "gross_1m"] = np.nan
    g = R.identity_gate(meta, series, table, sw)
    assert g["ok"] and g["checks"]["same_dates"] and g["max_abs_gross_diff"] == 0.0
    table.loc[table.index[-1], "gross_1m"] = 0.01                                # only the sealed one has a value there: not the same months
    assert not R.identity_gate(meta, series, table, sw)["checks"]["same_dates"]
    series.loc[series.index[-1], "gross"] = 0.01
    table.loc[table.index[-1], "gross_1m"] = np.nan                              # only ours has a value: not the same months either
    assert not R.identity_gate(meta, series, table, sw)["checks"]["same_dates"]


def test_a_sealed_table_that_lacks_a_date_of_the_rerun_fails_the_gate():
    meta, series, table, sw = world()
    g = R.identity_gate(meta, series, table.iloc[:-2], sw)
    assert not g["ok"] and not g["checks"]["same_dates"]


def test_the_identity_stage_re_evaluates_the_gate_from_the_saved_baseline(tmp_path):
    meta, series, table, sw = world()
    folder = tmp_path / "stamp" / "T0_baseline"
    folder.mkdir(parents=True)
    series.to_csv(folder / "series.csv", index_label="date")
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()
    table.assign(date=table.index).to_csv(run / "pit_backtest_table.csv", index=False)
    g = R.reevaluate_identity(tmp_path / "stamp", run)
    assert g["ok"] and json.loads((tmp_path / "stamp" / "identity.json").read_text(encoding="utf-8"))["ok"] is True


# ================================================================ the last month has no forward return: NaN must never empty a comparison (found by the smoke run) ===================
def test_the_pool_statistics_ignore_the_trailing_month_without_a_forward_return(tmp_path):
    idx = pd.date_range("2020-01-31", periods=24, freq="ME")
    rng = np.random.default_rng(4)
    d = pd.concat([pd.DataFrame({"draw": k, "date": idx, "gross": 0.0, "turnover": 0.2, "net25": 0.0, "netfee": rng.normal(0.003, 0.01, 24)}) for k in range(3)], ignore_index=True)
    d.loc[d["date"] == idx[-1], "netfee"] = np.nan                                 # the last date of every draw
    (tmp_path / "pools").mkdir()
    for nm in ("persistent_b10", "redraw"):
        d.to_parquet(tmp_path / "pools" / f"{nm}.parquet")
    out = R._pool_stats(tmp_path, 0.10)
    assert out is not None and np.isfinite(out["persistent"]).all() and len(out["persistent"]) == 3 and np.isfinite(out["redraw"]).all()
    assert out["persistent_turnover"] == pytest.approx(0.2)


def test_a_missing_pool_file_gives_no_statistics_and_never_a_partial_verdict(tmp_path):
    (tmp_path / "pools").mkdir()
    assert R._pool_stats(tmp_path, 0.10) is None


def test_the_amendment_5_verdict_and_the_arm_evaluation_survive_a_trailing_nan_month():
    from proyecto3.src import pit_p37_eval as V
    b = make_series_tail(seed=2, turnover=0.32)
    b["netfee"] = b["gross"] - 0.0030
    v = b.copy()
    v["turnover"] = 0.15
    v["netfee"] = v["gross"] - 0.0010
    v["net25"] = v["gross"] - 0.0025 * 0.15
    for df in (b, v):                                                              # the real series: the last formation date has no forward return
        df.iloc[-1, df.columns.get_indexer(["gross", "net25", "netfee"])] = np.nan
    r = V.amendment5_verdict(v, b)
    assert np.isfinite(r["ret_diff_pp"]) and np.isfinite(r["maxdd_fee"]) and r["pass"] is True


def make_series_tail(seed=1, turnover=0.30):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2010-01-31", periods=120, freq="ME")
    gross = pd.Series(0.004 + rng.normal(0, 0.01, 120), index=idx)
    return pd.DataFrame({"gross": gross, "turnover": pd.Series(turnover, index=idx), "cash": 0.0005, "net25": gross - 0.0025 * turnover, "netfee": gross - 0.002})
