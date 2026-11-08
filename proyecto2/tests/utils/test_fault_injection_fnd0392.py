# proyecto2/tests/utils/test_fault_injection_fnd0392.py
# -*- coding: utf-8 -*-
"""FND-0392 (WS0.5 of FND-0391): the corruption classes, the hermetic harness and the fixture builder's selection.

R-7: no pipeline, no DB. The harness is loaded by path (it is a script) and runs on the committed fixture tests/fixtures/fault_injection/nav_sample.csv.gz, so these tests prove the measurement
itself: the real defect is flagged, no healthy sample series is, and every class a validator is expected to catch is caught at the 0.95 bar. They sit in proyecto2/tests because the harness imports
proyecto2's `src` package; a root test would shadow proyecto3's `src` in a mixed pytest session.
"""
import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for _p in (_ROOT, _ROOT / "proyecto2"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import numpy as np
import pandas as pd
import pytest

from shared.fault_injection import CORRUPTIONS, choose_month, inject


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, _ROOT.joinpath(*rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


harness = _load("fault_injection_run", ("scripts", "audit", "fault_injection_run.py"))
builder = _load("fault_injection_fixture", ("scripts", "audit", "fault_injection_fixture.py"))


def _nav(n=60, start="2020-01-31", seed=5):
    rng = np.random.default_rng(seed)
    vals = [100.0]
    for r in rng.normal(0.004, 0.02, n - 1):
        vals.append(vals[-1] * (1.0 + r))
    return pd.DataFrame({"date": pd.date_range(start, periods=n, freq="ME"), "nav": vals})


# ------------------------------------------------------------------------------------------ the injectors
@pytest.mark.parametrize("name", sorted(CORRUPTIONS))
def test_every_class_returns_a_copy_and_never_touches_its_input(name):
    nav = _nav()
    before = nav.copy()
    out = inject(nav, name, 30)
    pd.testing.assert_frame_equal(nav, before)
    assert out is not nav and list(out.columns) == ["date", "nav"]
    pd.testing.assert_frame_equal(inject(nav, name, 30), out)                   # deterministic: no random numbers


def test_the_scale_unit_and_level_classes_act_exactly_from_month_k():
    nav, k = _nav(), 30
    for name, factor in (("scale_up_x100", 100.0), ("unit_shift_div100", 0.01), ("wrong_currency_step", 1.12), ("level_shift_50", 1.5), ("level_shift_100", 2.0), ("level_shift_150", 2.5)):
        out = inject(nav, name, k)
        assert np.allclose(out["nav"][:k], nav["nav"][:k]) and np.allclose(out["nav"][k:], nav["nav"][k:] * factor), name


def test_frozen_then_catchup_is_twelve_equal_months_then_seventy_percent_and_the_path_continues():
    nav, k = _nav(), 20
    out = inject(nav, "frozen_then_catchup", k)
    base = nav["nav"][k - 1]
    assert np.allclose(out["nav"][k - 1:k + 12], base)                           # 13 identical observations, the run FND-0383 counts
    assert abs(out["nav"][k + 12] / base - 1.7) < 1e-12
    assert np.allclose(out["nav"][k + 13:].to_numpy() / out["nav"][k + 12:-1].to_numpy(), nav["nav"][k + 13:].to_numpy() / nav["nav"][k + 12:-1].to_numpy())


def test_duplicated_month_adds_one_row_with_the_same_date():
    nav = _nav()
    out = inject(nav, "duplicated_month", 30)
    assert len(out) == len(nav) + 1 and out["date"][30] == out["date"][31] and out["nav"][30] == out["nav"][31]


def test_smooth_nav_collapses_the_volatility_after_k_and_keeps_the_start():
    nav, k = _nav(n=120), 40
    out = inject(nav, "smooth_nav", k)
    assert np.allclose(out["nav"][:k], nav["nav"][:k])
    r_before, r_after = nav["nav"].pct_change()[k:].std(), out["nav"].pct_change()[k:].std()
    assert r_after < 0.2 * r_before


def test_too_short_a_tail_or_an_unknown_class_is_refused():
    nav = _nav(n=40)
    with pytest.raises(ValueError):
        inject(nav, "frozen_then_catchup", 30)                                     # k + 14 > 40
    with pytest.raises(ValueError):
        inject(nav, "no_such_class", 10)
    with pytest.raises(ValueError):
        inject(nav, "scale_up_x100", 0)


def test_choose_month_is_deterministic_and_honours_the_tail_and_the_predicate():
    nav = _nav(n=60)
    assert choose_month(nav, "scale_up_x100", lambda k: True) == 30
    assert choose_month(nav, "scale_up_x100", lambda k: k >= 40) == 40
    assert choose_month(nav, "scale_up_x100", lambda k: k > 100) is None
    assert choose_month(nav, "frozen_then_catchup", lambda k: True) == 30
    assert choose_month(nav, "frozen_then_catchup", lambda k: k > 46) is None   # 60 - 14 = 46 is the last month that leaves the tail


def test_every_expected_detector_is_a_registered_detector():
    registered = set(harness.DETECTORS)
    for c in CORRUPTIONS.values():
        assert set(c.expected) <= registered, c.name


# ------------------------------------------------------------------------------------------ the harness on the committed fixture
@pytest.fixture(scope="module")
def report():
    return harness.run_harness(harness.load_fixture(), min_recall=0.95)


def test_the_fixture_is_the_documented_sample(report):
    assert report["funds"]["sample"] == 39 and report["funds"]["peers"] >= 200
    assert set(report["real_positive"]) and report["min_peers"] == 10


def test_the_real_defect_is_flagged_by_the_new_validator_and_no_healthy_sample_series_is(report):
    assert all("validate_nav_unshared_jump" in flagged for flagged in report["real_positive"].values())
    assert report["baseline_false_positive_funds"] == {}


def test_every_class_a_validator_is_expected_to_catch_is_caught_at_the_bar(report):
    for name, r in report["classes"].items():
        if r["expected"]:
            assert r["met"] is True, (name, r["detectors"])
            assert r["any_detected"] / max(r["injected"], 1) >= 0.95


def test_the_known_gaps_are_reported_as_gaps_not_hidden(report):
    gaps = {n for n, r in report["classes"].items() if not r["expected"]}
    assert {"duplicated_month", "wrong_currency_step", "smooth_nav"} <= gaps
    assert all(report["classes"][g]["any_detected"] == 0 for g in gaps)             # nothing catches them today: the report must say so, WS2/WS3 close them
    assert all(report["classes"][g]["met"] is None for g in gaps)


def test_an_injected_defect_changes_what_p2_would_publish(report):
    assert report["classes"]["scale_up_x100"]["median_vol_ratio"] > 10
    assert report["classes"]["level_shift_100"]["median_ret_after"] > report["classes"]["level_shift_100"]["median_ret_before"]


def test_render_and_the_exit_code_gate():
    text = harness.render(harness.run_harness(harness.load_fixture()))
    assert "level_shift_50" in text and "baseline flags" in text
    assert harness.main(["--min-recall", "0.95"]) == 0
    assert harness.main(["--min-recall", "1.01"]) == 1                              # an impossible bar fails the run: the gate is wired


def test_a_series_a_detector_already_rejects_cannot_be_credited(report):
    r = harness.run_harness(harness.load_fixture(), detectors={"always_bad": lambda nav, isin, peers: (False, "bad")}, corruptions=["level_shift_100"])
    d = r["classes"]["level_shift_100"]["detectors"]["always_bad"]
    assert d["detected"] == 0 and d["not_attributable"] == r["classes"]["level_shift_100"]["injected"] and d["recall"] is None


# ------------------------------------------------------------------------------------------ the fixture builder's selection
def _rows():
    rows = []
    for n_idx, nature in enumerate(builder.QUOTAS):
        for i in range(80):
            rows.append((f"N{n_idx}F{i:03d}", nature, f"{(i * 7919) % 1000:04d}", 100 if i != 3 else 30))
    rows.append(("POS000000001", "Renta Variable", "0000", 32))
    return rows


def test_selection_is_deterministic_filters_by_history_and_keeps_the_quotas():
    a = builder.select(_rows(), ["POS000000001"])
    assert a == builder.select(list(reversed(_rows())), ["POS000000001"])
    assert a["POS000000001"] == "real_positive"                                     # 32 rows: enough for a positive (24), too few for a sample (48)
    sample = [i for i, r in a.items() if r == "sample"]
    assert len(sample) + 1 == builder.SAMPLE_SIZE
    assert all(f"N{n_idx}F003" not in a for n_idx in range(len(builder.QUOTAS)))                 # the 30-row fund is neither sample nor peer
    assert max(sum(1 for i, r in a.items() if r == "peer" and i.startswith(f"N{n_idx}F")) for n_idx in range(len(builder.QUOTAS))) <= builder.PEERS_PER_NATURE


def test_a_positive_that_is_not_in_the_universe_stops_the_build():
    with pytest.raises(SystemExit):
        builder.select(_rows(), ["NOT_IN_UNIVERSE"])
