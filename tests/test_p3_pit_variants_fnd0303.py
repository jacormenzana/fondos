"""FND-0303 orchestrator: determinism gate first, one fresh interpreter + one overlay FILE per variant, machine-readable status."""
import hashlib
import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "launch"))

import p3_pit_variants as pv   # noqa: E402

STUB = textwrap.dedent('''
    import hashlib, json, os, sys, time
    from pathlib import Path
    argv = sys.argv[1:]
    out = Path(argv[argv.index("--out-dir") + 1])
    mode = os.environ.get("STUB_MODE", "ok")
    ov = os.environ.get("FONDOS_SCORER_PARAMS_OVERLAY", "")
    me = os.environ.get("STUB_NAME_HINT", "")
    if mode == "hang":
        time.sleep(600)
    if mode == "guard":
        sys.exit(107)
    if mode == "fail_overlay" and ov:
        sys.exit(3)
    out.mkdir(parents=True, exist_ok=True)
    (out / "inputs").mkdir(exist_ok=True)
    (out / "inputs" / "nav.txt").write_text(os.environ.get("STUB_INPUTS", "same"), encoding="utf-8")
    ov_hash = hashlib.sha256(Path(ov).read_bytes()).hexdigest() if ov else "base"
    noise = str(time.time_ns()) if mode == "random" else ""
    (out / "series_stats.json").write_text(json.dumps({"overlay": ov_hash, "noise": noise, "sharpe": 0.68}), encoding="utf-8")
    (out / "pit_backtest_table.csv").write_text("date,ret\\n2005-01-31,0.01\\n" + noise, encoding="utf-8")
''')


@pytest.fixture
def stub(tmp_path):
    p = tmp_path / "stub_runner.py"
    p.write_text(STUB, encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("STUB_MODE", "STUB_INPUTS", pv.OVERLAY_ENV):
        monkeypatch.delenv(k, raising=False)


def _main(tmp_path, stub, *argv):
    return pv.main(["--out-root", str(tmp_path / "out"), "--no-guard", *argv], runner=stub, guard=None)


def _status(tmp_path, label):
    return json.loads((tmp_path / "out" / label / "variant_status.json").read_text(encoding="utf-8"))


def test_gate_passes_on_a_deterministic_runner_and_runs_no_variant(tmp_path, stub):
    assert _main(tmp_path, stub, "--gate-only") == pv.RC_OK
    gate = json.loads((tmp_path / "out" / "gate.json").read_text(encoding="utf-8"))
    assert gate["status"] == "PASS" and gate["v0_series_stats_sha256"]
    assert not (tmp_path / "out" / "V1").exists()


def test_gate_fails_when_v0_is_not_reproducible_and_nothing_else_runs(tmp_path, stub, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "random")
    assert _main(tmp_path, stub, "--variants", "V1,V3") == pv.RC_GATE_FAILED
    gate = json.loads((tmp_path / "out" / "gate.json").read_text(encoding="utf-8"))
    assert gate["status"] == "FAIL" and any("differs" in r for r in gate["reasons"])
    assert not (tmp_path / "out" / "V1").exists() and not (tmp_path / "out" / "V3").exists()


def test_each_variant_gets_its_own_overlay_file_and_nothing_leaks(tmp_path, stub):
    assert _main(tmp_path, stub, "--variants", "V1,V3") == pv.RC_OK
    v0, v1, v3 = (_status(tmp_path, n) for n in ("V0_gate_A", "V1", "V3"))
    assert v0["overlay_path"] is None
    for v, key in ((v1, "MIN_REAL_RETURN_BY_SUB"), (v3, "MAX_DRAWDOWN_BY_SUB")):
        assert Path(v["overlay_path"]).is_absolute() and Path(v["overlay_path"]).is_file()
        assert key in Path(v["overlay_path"]).read_text(encoding="utf-8")
        assert v["overlay_sha256"] == hashlib.sha256(Path(v["overlay_path"]).read_bytes()).hexdigest()
    assert v1["overlay_sha256"] != v3["overlay_sha256"]
    assert "MAX_DRAWDOWN_BY_SUB" not in Path(v1["overlay_path"]).read_text(encoding="utf-8")       # V1 carries only its own change
    assert len({v0["series_stats_sha256"], v1["series_stats_sha256"], v3["series_stats_sha256"]}) == 3
    assert os.environ.get(pv.OVERLAY_ENV) is None                                                   # the parent process is untouched


def test_a_failed_variant_is_recorded_and_the_next_one_still_runs(tmp_path, stub, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "fail_overlay")           # any run WITH an overlay exits 3; V0 (no overlay) is fine
    assert _main(tmp_path, stub, "--variants", "V1,V3") == pv.RC_VARIANT_FAILED
    assert _status(tmp_path, "V1")["status"] == "failed" and _status(tmp_path, "V1")["exit_code"] == 3
    assert _status(tmp_path, "V3")["status"] == "failed"      # it ran (and failed on its own), it was not skipped
    assert (tmp_path / "out" / "V3" / "variant_status.json").is_file()


def test_timeout_and_guard_kill_are_distinct_statuses(tmp_path, stub, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "hang")
    s = pv.run_variant("V0", tmp_path / "t", "V0_t", stub, None, 2)
    assert s["status"] == "timeout" and s["exit_code"] is None and s["series_stats_sha256"] is None
    monkeypatch.setenv("STUB_MODE", "guard")
    assert pv.run_variant("V0", tmp_path / "t", "V0_g", stub, None, 60)["status"] == "oom"


def test_v2_variants_are_not_available_when_the_code_is_withdrawn(tmp_path, stub, monkeypatch):
    monkeypatch.setattr(pv, "V2_AVAILABLE", False)
    assert _main(tmp_path, stub, "--variants", "V2") == pv.RC_OK
    s = _status(tmp_path, "V2")
    assert s["status"] == "not_available" and "series_stats_sha256" not in s and "reason" in s


def test_expected_v0_hash_is_enforced(tmp_path, stub):
    assert _main(tmp_path, stub, "--gate-only") == pv.RC_OK
    good = json.loads((tmp_path / "out" / "gate.json").read_text(encoding="utf-8"))["v0_series_stats_sha256"]
    other = tmp_path / "second"
    assert pv.main(["--out-root", str(other), "--gate-only", "--expect-v0-hash", good], runner=stub, guard=None) == pv.RC_OK
    assert pv.main(["--out-root", str(tmp_path / "third"), "--gate-only", "--expect-v0-hash", "0" * 64], runner=stub, guard=None) \
        == pv.RC_GATE_FAILED


def test_gate_requires_identical_inputs_and_successful_runs():
    base = {"label": "a", "status": "success", "series_stats_sha256": "s", "table_sha256": "t", "inputs_sha256": "i", "git": {}}
    assert pv.evaluate_gate(base, {**base, "label": "b"})["status"] == "PASS"
    assert pv.evaluate_gate(base, {**base, "label": "b", "inputs_sha256": "other"})["status"] == "FAIL"
    assert pv.evaluate_gate(base, {**base, "label": "b", "status": "timeout"})["status"] == "FAIL"
    assert pv.evaluate_gate(base, {**base, "label": "b", "table_sha256": "x"})["status"] == "FAIL"


def test_a_gate_is_reusable_only_for_the_same_clean_commit():
    gate = {"status": "PASS", "git": {"commit": "abc"}}
    assert pv.gate_reusable(gate, {"commit": "abc", "clean": True}) is None
    assert "now at" in pv.gate_reusable(gate, {"commit": "def", "clean": True})
    assert "tracked changes" in pv.gate_reusable(gate, {"commit": "abc", "clean": False})
    assert "not a PASS" in pv.gate_reusable({"status": "FAIL"}, {"commit": "abc", "clean": True})


def test_variants_on_different_inputs_than_v0_are_flagged_incomparable():
    v0 = {"v0_inputs_sha256": "i"}
    assert pv.comparable(v0, {"variant": "V1", "status": "success", "inputs_sha256": "i"}) is None
    assert "INCOMPARABLE" in pv.comparable(v0, {"variant": "V1", "status": "success", "inputs_sha256": "j"})
    assert pv.comparable(v0, {"variant": "V1", "status": "failed", "inputs_sha256": "j"}) is None


def test_unknown_or_nothing_to_do_is_a_usage_error(tmp_path, stub):
    assert _main(tmp_path, stub, "--variants", "V9") == pv.RC_GATE_FAILED
    assert pv.main(["--out-root", str(tmp_path / "x")], runner=stub, guard=None) == pv.RC_GATE_FAILED
    assert pv.main(["--dry-run", "--variants", "V1"], runner=stub, guard=None) == pv.RC_OK


def test_the_real_command_uses_an_isolated_interpreter_and_the_guard():
    cmd = pv.build_command(Path("C:/x/V1"), pv.RUNNER, guard=pv.GUARD)
    joined = " ".join(cmd)
    assert "guarded_run.ps1" in joined and "-I" in joined and "--hysteresis-band 0.10" in joined and "--cache-dir" in joined
    plain = pv.build_command(Path("C:/x/V1"), pv.RUNNER, guard=None)
    assert plain[1] == "-I" and "--out-dir" in plain and plain[plain.index("--hysteresis-grid") + 1] == ""


def test_v2_variants_carry_the_common_window_parameter_in_their_overlay(tmp_path, stub):
    assert pv.V2_AVAILABLE is True
    assert _main(tmp_path, stub, "--variants", "V2,V123") == pv.RC_OK
    for name in ("V2", "V123"):
        s = _status(tmp_path, name)
        assert s["status"] == "success"
        assert "COMMON_WINDOW_MONTHS: 60" in Path(s["overlay_path"]).read_text(encoding="utf-8")
    assert "MIN_REAL_RETURN_BY_SUB" not in Path(_status(tmp_path, "V2")["overlay_path"]).read_text(encoding="utf-8")
    assert "MIN_REAL_RETURN_BY_SUB" in Path(_status(tmp_path, "V123")["overlay_path"]).read_text(encoding="utf-8")
