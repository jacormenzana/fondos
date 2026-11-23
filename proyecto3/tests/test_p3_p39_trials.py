"""Package L, P3-9 (doc/decisiones/PREREG_P3_9_LAYER3.md sections 3 and 8): the runner `scripts/audit/p3/p3_p39_trials.py`, the parts that decide whether the long run may start.
Invariants pinned: (1) the arms are exactly the preregistered ones: the all-off baseline T0, the base AC (band 0.10 + the fee penalty at H 0.58 y, the P3-7 candidate) and the SIX trial arms on top
of it (N5, N0, E5, E0, NE5, NE0 with the signed values), the Holm family and level of section 5; (2) the identity gate has TWO parts, both must hold before a trial runs: T0 reproduces the sealed
run (the P3-7 gate, with the two new switches in the list of those that must be OFF) and the re-run base AC reproduces the AC series of the P3-7 run; a one-month difference fails it; (3) the trial
arms are refused until `identity_p39.json` says both passed, the base arms are not; (4) the runner reads the DB-derived inputs only from a verified frozen snapshot. Synthetic frames only (R-7)."""
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

import p3_p39_trials as R  # noqa: E402
from proyecto3.src import ranking_levers as RL  # noqa: E402


# ================================================================ the arms ============================================================================================
def test_the_arms_are_the_preregistered_ones():
    assert list(R.ARMS) == ["T0_baseline", "AC_base", "N5", "N0", "E5", "E0", "NE5", "NE0"]
    assert R.TRIALS == ["N5", "N0", "E5", "E0", "NE5", "NE0"] and len(R.TRIALS) == R.HOLM_TRIALS == 6
    assert R.HOLM_LEVEL == pytest.approx(0.05 / 6)
    assert RL.LeverConfig() == R.levers_of(R.ARMS["T0_baseline"])
    base = RL.LeverConfig(l4_h_years=0.58)
    assert R.levers_of(R.ARMS["AC_base"]) == base
    expect = {"N5": dict(l5_nature_k=0.5), "N0": dict(l5_nature_k=0.0), "E5": dict(l6_chase_f=0.5), "E0": dict(l6_chase_f=0.0),
              "NE5": dict(l5_nature_k=0.5, l6_chase_f=0.5), "NE0": dict(l5_nature_k=0.0, l6_chase_f=0.0)}
    for name, extra in expect.items():
        assert R.levers_of(R.ARMS[name]) == RL.LeverConfig(l4_h_years=0.58, **extra), name
        assert R.ARMS[name]["band"] == 0.10 and R.ARMS[name]["pen"] is True and R.ARMS[name]["cls"] is False


def test_every_trial_arm_sits_on_top_of_the_base_arm_and_the_base_has_no_layer_3_lever():
    b = R.levers_of(R.ARMS["AC_base"])
    assert b.l5_nature_k is None and b.l6_chase_f is None and R.ARMS["AC_base"]["band"] == 0.10
    for n in R.TRIALS:
        c = R.levers_of(R.ARMS[n])
        assert (c.l1_q, c.l2_z0, c.l3_k, c.l4_h_years) == (b.l1_q, b.l2_z0, b.l3_k, b.l4_h_years) and (c.l5_nature_k is not None or c.l6_chase_f is not None)


def test_the_switches_that_must_be_off_include_the_two_new_ones():
    names = set(R.default_switches39())
    assert {"P3_L5_NATURE_SCALE_ENABLED", "P3_L6_CHASE_CONTROL_ENABLED", "P3_L4_FEE_PENALTY_ENABLED", "PORTFOLIO_HYSTERESIS_ENABLED"} <= names
    assert not any(R.default_switches39().values())


# ================================================================ the AC identity ======================================================================================
def _series(n=40, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2005-01-31", periods=n, freq="ME")
    s = pd.DataFrame({"gross": rng.normal(0.004, 0.02, n), "turnover": rng.uniform(0.05, 0.3, n)}, index=idx)
    s.iloc[-1] = np.nan                                                          # the last month has no forward return
    return s


def test_ac_identity_passes_on_an_exact_reproduction_and_fails_on_any_difference():
    ref = _series()
    assert R.ac_identity(ref.copy(), ref)["ok"] is True
    bad = ref.copy()
    bad.iloc[10, 0] += 1e-6
    r = R.ac_identity(bad, ref)
    assert r["ok"] is False and r["checks"]["gross_identical"] is False and r["checks"]["turnover_identical"] is True
    bad2 = ref.copy()
    bad2.iloc[5, 1] += 1e-3
    assert R.ac_identity(bad2, ref)["checks"]["turnover_identical"] is False


def test_ac_identity_fails_on_other_dates_or_a_different_nan_pattern():
    ref = _series()
    assert R.ac_identity(ref.iloc[1:], ref)["ok"] is False                        # a month missing in the re-run
    nan = ref.copy()
    nan.iloc[3, 0] = np.nan
    assert R.ac_identity(nan, ref)["ok"] is False                                 # a one-sided NaN is not a match


# ================================================================ the gate ===========================================================================================
def test_the_trial_gate_needs_both_parts(tmp_path):
    ok, why = R.gate_open39(tmp_path)
    assert ok is False and "identity_p39.json" in why
    (tmp_path / "identity_p39.json").write_text(json.dumps({"ok": False, "t0": {"ok": True}, "ac": {"ok": False}}), encoding="utf-8")
    assert R.gate_open39(tmp_path)[0] is False
    (tmp_path / "identity_p39.json").write_text(json.dumps({"ok": True, "t0": {"ok": True}, "ac": {"ok": True}}), encoding="utf-8")
    assert R.gate_open39(tmp_path)[0] is True
    (tmp_path / "identity_p39.json").write_text(json.dumps({"ok": True, "t0": {"ok": True}, "ac": {"ok": False}}), encoding="utf-8")
    assert R.gate_open39(tmp_path)[0] is False                                    # `ok` alone is not trusted: both parts are read


def test_combined_identity_is_the_conjunction_of_both_parts():
    assert R.combine_identity({"ok": True}, {"ok": True})["ok"] is True
    assert R.combine_identity({"ok": True}, {"ok": False})["ok"] is False
    assert R.combine_identity({"ok": False}, {"ok": True})["ok"] is False


def _stub_build(monkeypatch):
    calls = []
    monkeypatch.setattr(R.P, "build_arm", lambda args, name, spec, out, base_dir, **kw: calls.append(name) or (out.mkdir(parents=True, exist_ok=True), (out / "meta.json").write_text("{}")))
    return calls


def _frozen(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "manifest.json").write_text("{}")
    return snap


def test_trial_arms_are_refused_without_the_gate_and_base_arms_are_not(tmp_path, monkeypatch):
    calls = _stub_build(monkeypatch)
    snap = _frozen(tmp_path)
    base = ["--run-dir", str(tmp_path / "run"), "--stage", "build", "--out-root", str(tmp_path / "out"), "--stamp", "s", "--snapshot-dir", str(snap)]
    assert R.main(base + ["--trial", "N5"]) == 5 and calls == []                  # refused: identity_p39.json missing
    assert R.main(base + ["--trial", "T0_baseline"]) == 0 and R.main(base + ["--trial", "AC_base"]) == 0
    assert calls == ["T0_baseline", "AC_base"]
    d = tmp_path / "out" / "s"
    (d / "identity_p39.json").write_text(json.dumps({"ok": True, "t0": {"ok": True}, "ac": {"ok": True}}), encoding="utf-8")
    assert R.main(base + ["--trial", "N5"]) == 0 and calls[-1] == "N5"


def test_the_runner_refuses_an_unverified_or_missing_snapshot(tmp_path, monkeypatch):
    _stub_build(monkeypatch)
    args = ["--run-dir", str(tmp_path / "run"), "--stage", "build", "--trial", "T0_baseline", "--out-root", str(tmp_path / "out"), "--stamp", "s", "--snapshot-dir", str(tmp_path / "nope")]
    assert R.main(args) == 3


def test_unknown_trial_is_refused(tmp_path, monkeypatch):
    _stub_build(monkeypatch)
    snap = _frozen(tmp_path)
    assert R.main(["--run-dir", str(tmp_path / "run"), "--stage", "build", "--trial", "ZZ", "--out-root", str(tmp_path / "out"), "--stamp", "s", "--snapshot-dir", str(snap)]) == 4


def test_an_all_nan_series_proves_nothing():
    ref = _series()
    empty = ref.copy()
    empty[:] = np.nan
    assert R.ac_identity(empty.copy(), empty)["ok"] is False                      # equal NaN patterns with no value at all is not a reproduction
