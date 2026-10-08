"""
tests/test_cycle_telemetry_ingest.py -- P2 counters into the cycle telemetry, and the end-of-cycle flag catalogue (FND-0239 stage 3). R-7: pure.

Three guards on the one fragile seam (the RUN_SUMMARY text that P2 writes and this ingest parses):
  1. unit tests on sample rows (match / every missing key / extra keys tolerated / non-numeric);
  2. the format drift is VISIBLE: a flag + a structured warning, never a silent skip;
  3. a CONTRACT test that reads proyecto2/src/pipeline/run_pipeline.py and fails if a key the parser requires is no longer in its RUN_SUMMARY.
"""
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import cycle_telemetry_flags as fl  # noqa: E402
from shared import cycle_telemetry_ingest as ing  # noqa: E402

GOOD = ("run_id=P2-20261008 processed=120 skipped=2900 errors=0 warnings=3 defl_skipped=1 written=48000 quarantined=0 "
        "ols_funds=120 cond_guard_funds=2 betas_nulled=15 elapsed=1834s")


# ---------------------------------------------------------------- the strict parser
def test_a_well_formed_summary_parses_every_required_key():
    values, problems = ing.parse_run_summary(GOOD)
    assert problems == [] and set(values) == set(ing.RUN_SUMMARY_REQUIRED)
    assert values["processed"] == 120 and values["elapsed"] == 1834.0 and values["written"] == 48000


def test_extra_keys_and_a_family_suffix_are_tolerated():
    values, problems = ing.parse_run_summary(GOOD + " fam_recomputed=metrics:3 new_key=whatever")
    assert problems == [] and values is not None


@pytest.mark.parametrize("key", ing.RUN_SUMMARY_REQUIRED)
def test_each_missing_key_is_reported_by_name(key):
    broken = re.sub(rf"\b{key}=\S+\s?", "", GOOD)
    values, problems = ing.parse_run_summary(broken)
    assert values is None and problems == [f"missing key {key}"]


def test_a_non_numeric_value_and_a_foreign_message_are_problems_not_crashes():
    assert ing.parse_run_summary(GOOD.replace("processed=120", "processed=many"))[1] == ["non-numeric processed='many'"]
    assert ing.parse_run_summary("something else entirely")[1] == ["message does not start with run_id="]
    assert ing.parse_run_summary(None)[1] == ["message does not start with run_id="]
    assert ing.parse_run_summary("")[0] is None


def test_metrics_use_the_seeded_metric_codes_and_add_the_cache_hit_share():
    values, _ = ing.parse_run_summary(GOOD)
    m = ing.metrics_from_summary(values)
    assert m["p2_processed"] == 120 and m["p2_written_rows"] == 48000 and m["p2_elapsed_s"] == 1834.0 and m["ols_funds"] == 120
    assert m["cache_hit_pct"] == pytest.approx(100.0 * 2900 / 3020)
    zero, _ = ing.parse_run_summary(GOOD.replace("processed=120", "processed=0").replace("skipped=2900", "skipped=0"))
    assert "cache_hit_pct" not in ing.metrics_from_summary(zero)               # no 0/0


def test_every_metric_the_ingest_emits_is_seeded_in_the_ddl():
    ddl = (_ROOT / "db" / "pg" / "35_control.sql").read_text(encoding="utf-8")
    seeded = set(re.findall(r"\('([a-z0-9_]+)',\s*'[^']+',\s*'[a-z]+',\s*'(?:LOWER_BETTER|HIGHER_BETTER|NEUTRAL)'", ddl))
    for code in list(ing.METRIC_OF_KEY.values()) + ["cache_hit_pct"]:
        assert code in seeded, code


# ---------------------------------------------------------------- the contract with run_pipeline
def test_contract_every_required_key_is_in_the_run_summary_run_pipeline_writes():
    src = (_ROOT / "proyecto2" / "src" / "pipeline" / "run_pipeline.py").read_text(encoding="utf-8")
    blocks = re.findall(r"'RUN_SUMMARY'.*?\(\s*status,.*?RUN_BATCH_ID,\s*\)", src, re.S)
    assert blocks, "the RUN_SUMMARY INSERT of run_pipeline.py was not found: update this contract test AND shared/cycle_telemetry_ingest.py"
    body = blocks[0]
    assert "run_id=" in body, "RUN_SUMMARY no longer starts with run_id="
    for key in ing.RUN_SUMMARY_REQUIRED:
        assert re.search(rf"\b{key}=", body), (f"run_pipeline.py's RUN_SUMMARY lost the key `{key}`: the telemetry ingest would flag every cycle as "
                                              "INGEST_FORMAT_DRIFT. Update shared/cycle_telemetry_ingest.RUN_SUMMARY_REQUIRED or restore the key.")


def test_contract_the_backfill_markers_keep_their_names_and_the_recomputed_field():
    src = (_ROOT / "proyecto2" / "src" / "pipeline" / "run_pipeline.py").read_text(encoding="utf-8")
    assert "'BACKFILL_START'" in src and "'BACKFILL_END'" in src and "recomputed=" in src


# ---------------------------------------------------------------- analyse a window
def _r(i, step, message, batch="B1", status="OK"):
    return (i, step, status, message, batch)


def test_a_clean_run_yields_metrics_and_no_flags():
    res = ing.analyze([_r(1, "RUN_SUMMARY", GOOD)], {"ended": True, "rc": 0})
    assert res["flags"] == [] and res["metrics"]["p2_processed"] == 120 and res["summaries"] == 1


def test_format_drift_is_a_flag_with_the_row_and_the_problem_not_a_silent_skip():
    res = ing.analyze([_r(7, "RUN_SUMMARY", GOOD.replace("written=48000 ", ""))], {"ended": True, "rc": 0})
    (flag,) = res["flags"]
    assert flag[:3] == ("INGEST_FORMAT_DRIFT", "WARN", "B1") and "row 7" in flag[3] and "missing key written" in flag[3]
    assert res["metrics"] == {} and res["summaries"] == 1                       # no wrong metric from a row we could not read


def test_the_last_valid_summary_supplies_the_metrics_after_a_drifted_one():
    rows = [_r(1, "RUN_SUMMARY", GOOD.replace("processed=120", "processed=5"), batch="B1"),
            _r(2, "RUN_SUMMARY", "garbage", batch="B2")]
    res = ing.analyze(rows, {"ended": True, "rc": 0})
    assert res["metrics"]["p2_processed"] == 5 and [f[0] for f in res["flags"]] == ["INGEST_FORMAT_DRIFT"]


def test_a_killed_run_without_a_summary_is_a_crash_flag_distinct_from_drift():
    res = ing.analyze([], {"ended": True, "rc": 137})
    (flag,) = res["flags"]
    assert flag[:3] == ("INGEST_CRASH_DETECTED", "HIGH", "window") and "rc=137" in flag[3]
    assert ing.analyze([], {"ended": False, "rc": None})["flags"] == []          # the step has not ended: nothing to conclude
    assert ing.analyze([], None)["flags"] == []


def test_a_backfill_start_without_its_end_is_a_crash_flag_per_batch():
    rows = [_r(1, "BACKFILL_START", "CALC_VERSION drift: stored=A current=B", batch="B9"),
            _r(2, "RUN_SUMMARY", GOOD, batch="B9")]
    flags = ing.analyze(rows, {"ended": True, "rc": 0})["flags"]
    assert [(f[0], f[1], f[2]) for f in flags] == [("INGEST_CRASH_DETECTED", "HIGH", "B9")]
    rows.append(_r(3, "BACKFILL_END", "batch_id=B9 recomputed=120 errors=0 elapsed=1834s", batch="B9"))
    assert ing.analyze(rows, {"ended": True, "rc": 0})["flags"] == []


def test_a_backfill_that_recomputed_nothing_or_ran_no_ols_is_flagged():
    end0 = [_r(1, "BACKFILL_START", "--force flag", batch="B1"), _r(2, "RUN_SUMMARY", GOOD, batch="B1"),
            _r(3, "BACKFILL_END", "batch_id=B1 recomputed=0 errors=0 elapsed=3s", batch="B1")]
    assert [f[:3] for f in ing.analyze(end0, {"ended": True, "rc": 0})["flags"]] == [("BACKFILL_NO_WORK", "WARN", "B1")]
    no_ols = [_r(1, "BACKFILL_START", "CALC_VERSION drift: stored=A current=B", batch="B2"),
              _r(2, "RUN_SUMMARY", GOOD.replace("ols_funds=120", "ols_funds=0"), batch="B2"),
              _r(3, "BACKFILL_END", "batch_id=B2 recomputed=120 errors=0 elapsed=9s", batch="B2")]
    flags = ing.analyze(no_ols, {"ended": True, "rc": 0})["flags"]
    assert [f[:3] for f in flags] == [("BACKFILL_NO_WORK", "WARN", "B2")] and "ols_funds=0" in flags[0][3]
    forced_zero_ols = [r if r[1] != "BACKFILL_START" else _r(1, "BACKFILL_START", "--force flag", batch="B2") for r in no_ols]
    assert ing.analyze(forced_zero_ols, {"ended": True, "rc": 0})["flags"] == []     # ols_funds=0 is only suspicious on a version-drift backfill


def test_errors_with_nothing_processed_or_written_is_high():
    bad = GOOD.replace("processed=120", "processed=0").replace("errors=0", "errors=9").replace("written=48000", "written=0")
    (flag,) = ing.analyze([_r(1, "RUN_SUMMARY", bad)], {"ended": True, "rc": 0})["flags"]
    assert flag[:3] == ("RUN_ERROR_ZERO_WORK", "HIGH", "B1")


def test_audit_counts_default_to_zero_and_ignore_case():
    assert ing.audit_counts([("ALARM", 3), ("warn", 10)]) == {"n_alarm": 3, "n_warn": 10, "n_info": 0}
    assert ing.audit_counts([]) == {"n_alarm": 0, "n_warn": 0, "n_info": 0}


# ---------------------------------------------------------------- the flag catalogue
def _step(code, kind="PIPELINE", status="OK", rc=0, dur=100.0, ratio=1.0, alarm=3.0, hard=None):
    return {"step_code": code, "step_kind": kind, "status": status, "rc": rc, "dur_s": dur, "ratio": ratio, "alarm_ratio": alarm, "hard_max_s": hard}


def test_a_slow_step_is_warn_over_the_ratio_and_high_over_the_alarm_or_the_hard_ceiling():
    assert fl.rule_step_slow([_step("P1_BENCH")]) == []
    (w,) = fl.rule_step_slow([_step("P2_CALC", status="WARN", dur=200.0, ratio=2.0)])
    assert w[:3] == ("STEP_SLOW", "WARN", "P2_CALC") and w[4] == 2.0
    (h,) = fl.rule_step_slow([_step("P2_CALC", status="WARN", dur=400.0, ratio=4.0)])
    assert h[1] == "HIGH"
    (hard,) = fl.rule_step_slow([_step("P2_CALC", status="OK", dur=40000.0, ratio=None, hard=32400.0)])
    assert hard[1] == "HIGH" and "hard ceiling" in hard[3]
    assert fl.rule_step_slow([_step("P2_CALC", status="FAILED", rc=1, dur=99999.0, ratio=9.0)]) == []      # a failed step has its own signal


def test_a_failed_diagnostic_in_an_ok_cycle_is_flagged_and_only_then():
    steps = [_step("BETA_COMPARE", kind="DIAGNOSTIC", status="FAILED", rc=2), _step("CYCLE_REPORT", kind="DIAGNOSTIC"), _step("P2_CALC", status="FAILED", rc=1)]
    out = fl.rule_diag_failed_cycle_ok(steps, "OK")
    assert [(f[0], f[2]) for f in out] == [("DIAG_FAILED_CYCLE_OK", "BETA_COMPARE")]
    assert fl.rule_diag_failed_cycle_ok(steps, "FAILED") == []


def test_pending_family_refresh_is_high_and_bounded():
    assert fl.rule_family_refresh_pending([]) == []
    (f,) = fl.rule_family_refresh_pending([f"I{i:03d}" for i in range(30)])
    assert f[:3] == ("FAMILY_REFRESH_PENDING", "HIGH", "") and "30 active funds" in f[3] and "(+10 more)" in f[3] and f[4] == 30.0


def test_a_family_below_full_version_coverage_is_flagged_per_family():
    metrics = [{"metric_code": "family_version_coverage_pct", "scope": "sortino", "value_num": 62.5},
               {"metric_code": "family_version_coverage_pct", "scope": "returns", "value_num": 100.0},
               {"metric_code": "p2_errors", "scope": "", "value_num": 4.0}]
    (f,) = fl.rule_family_version_stale(metrics)
    assert f[:3] == ("FAMILY_VERSION_STALE", "WARN", "sortino") and "62.5%" in f[3]


def test_evaluate_combines_the_rules_and_every_code_is_in_the_catalogue():
    facts = {"steps": [_step("P2_CALC", status="WARN", dur=300.0, ratio=2.0), _step("BETA_COMPARE", kind="DIAGNOSTIC", status="FAILED", rc=1)],
             "metrics": [], "pending_family_refresh": ["A"]}
    codes = {f[0] for f in fl.evaluate(facts, "OK")}
    assert codes == {"STEP_SLOW", "DIAG_FAILED_CYCLE_OK", "FAMILY_REFRESH_PENDING"} and codes <= set(fl.CATALOGUE)
    for code in ("BACKFILL_NO_WORK", "RUN_ERROR_ZERO_WORK", "INGEST_FORMAT_DRIFT", "INGEST_CRASH_DETECTED", "P3_STALE_BYPASS"):
        assert code in fl.CATALOGUE                                             # raised by the ingest / the bypass audit, listed here for one view


# ---------------------------------------------------------------- the command line the launchers call
def test_the_new_subcommands_do_nothing_without_a_cycle_and_never_fail_a_launcher(monkeypatch, capsys):
    from shared import cycle_telemetry as ct
    monkeypatch.delenv("FONDOS_CYCLE_ID", raising=False)
    for argv in (["ingest-p2"], ["evaluate-flags", "--status", "OK"], ["audit-run", "p2_metrics", "pre", "r1"],
                 ["audit-run"], ["audit-run", "p2_metrics", "middle", "r1"], ["evaluate-flags", "--nope"]):
        assert ct.main(argv) == 0, argv
    assert capsys.readouterr().out == ""


def test_the_new_subcommands_survive_a_database_that_is_down(monkeypatch, capsys):
    from shared import cycle_telemetry as ct

    def down():
        raise RuntimeError("db down")

    monkeypatch.setenv("FONDOS_CYCLE_ID", "c1")
    monkeypatch.setattr(ct, "_connect", lambda *a, **k: down())
    for argv in (["ingest-p2"], ["ingest-p2", "--since", "2026-10-08T00:00:00+00:00"], ["evaluate-flags", "--status", "FAILED"],
                 ["audit-run", "cost_attributes", "post", "post_1_costs", "--baseline", "pre_1_costs"]):
        assert ct.main(argv) == 0, argv
    assert capsys.readouterr().out == ""
