# proyecto2/tests/utils/test_family_versions.py
# -*- coding: utf-8 -*-
"""FND-0236: per-family versions, hashes and the per-fund run decision.

R-7: only the pure modules are imported (no pipeline, no DB). The pipeline-wiring checks live in
test_family_versioning_wiring_fnd0236.py.
"""
import hashlib
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pandas as pd
import pytest

from utils import family_versions as fv  # noqa: E402
from utils.fingerprint import compute_input_hash, data_fingerprint  # noqa: E402

CV = "20261004"
MV = "v1"
FLAGS = ("MACRO_VIF_ITERATIVE_ENABLED", "MACRO_FACTOR_CLEAN_ENABLED",
         "PERSISTENCE_FIRST_LAST_NAV_ENABLED", "CAPTURE_MONTH_END_ENABLED")


def _nav(n=60, last=100.0):
    dates = pd.date_range("2018-01-31", periods=n, freq="ME")
    return pd.DataFrame({"date": dates, "nav": [last - (n - 1 - i) * 0.1 for i in range(n)]})


def _ipc(n=60):
    return pd.DataFrame({"date": pd.date_range("2018-01-31", periods=n, freq="ME"), "ipc_index": range(n)})


def _hashes(cv=CV, flags=FLAGS, overrides=None, nav=None, ipc=None):
    fp = data_fingerprint(_nav() if nav is None else nav, _ipc() if ipc is None else ipc)
    return fv.current_family_hashes(fp, MV, cv, flags, overrides)


# ---------------------------------------------------------------- the legacy hash must not move
def test_compute_input_hash_is_byte_identical_to_the_legacy_composition():
    nav, ipc = _nav(), _ipc()
    legacy_raw = (f"{nav['date'].max()}|{len(nav)}|{float(nav['nav'].iloc[-1]):.8f}"
                  f"||{ipc['date'].max()}|{len(ipc)}||{MV}||{CV}")
    assert compute_input_hash(nav, ipc, MV, CV) == hashlib.sha1(legacy_raw.encode()).hexdigest()


def test_data_fingerprint_empty_inputs():
    assert data_fingerprint(None, None) == "EMPTY||NOIPC"


# ---------------------------------------------------------------- tokens
def test_token_without_override_is_the_calc_version():
    assert all(fv.family_token(f, CV, {}) == CV for f in fv.ALL_FAMILIES)


def test_token_with_override_is_suffixed_and_only_for_that_family():
    ov = {"macro": "20261101"}
    assert fv.family_token("macro", CV, ov) == f"{CV}.20261101"
    assert fv.family_token("risk", CV, ov) == CV


def test_default_overrides_are_empty():
    assert fv.FAMILY_CALC_OVERRIDES == {}


def test_unknown_family_is_rejected():
    with pytest.raises(ValueError):
        fv.family_token("nope", CV, {})
    with pytest.raises(ValueError):
        fv.decide_fund(["nope"], {}, _hashes())


# ---------------------------------------------------------------- hashes: what moves what
def test_override_bump_changes_exactly_one_family_hash():
    before, after = _hashes(overrides={}), _hashes(overrides={"macro": "20261101"})
    assert [f for f in fv.ALL_FAMILIES if before[f] != after[f]] == ["macro"]


def test_global_calc_version_bump_changes_every_family_hash():
    before, after = _hashes(cv="20261004"), _hashes(cv="20261101")
    assert all(before[f] != after[f] for f in fv.ALL_FAMILIES)


def test_new_nav_data_changes_every_family_hash():
    before, after = _hashes(), _hashes(nav=_nav(n=61))
    assert all(before[f] != after[f] for f in fv.ALL_FAMILIES)


@pytest.mark.parametrize("flag", FLAGS)
def test_flipping_a_bundle_flag_changes_only_its_family(flag):
    on = _hashes(flags=FLAGS)
    off = _hashes(flags=tuple(f for f in FLAGS if f != flag))
    assert [f for f in fv.ALL_FAMILIES if on[f] != off[f]] == [fv.FLAG_FAMILIES[flag]]


def test_every_bundle_flag_in_config_is_mapped_to_a_family():
    from shared import config
    assert set(config.P2_BUNDLE_FLAGS) == set(fv.FLAG_FAMILIES)
    assert set(fv.FLAG_FAMILIES.values()) <= set(fv.ALL_FAMILIES)


def test_hashes_are_deterministic_and_distinct_per_family():
    a, b = _hashes(), _hashes()
    assert a == b
    assert len(set(a.values())) == len(fv.ALL_FAMILIES)


def test_composite_hash_changes_iff_a_family_hash_changes():
    base = _hashes()
    assert fv.composite_hash(base) == fv.composite_hash(dict(base))
    assert fv.composite_hash(base) != fv.composite_hash(_hashes(overrides={"fx": "x"}))


# ---------------------------------------------------------------- the per-fund decision
ALL = set(fv.ALL_FAMILIES)


def test_unchanged_fund_is_a_cache_hit():
    cur = _hashes()
    d = fv.decide_fund(ALL, dict(cur), cur)
    assert d.cache_hit and not d.run and not d.stamp and not d.seed


def test_macro_only_bump_runs_only_macro():
    stored = _hashes()
    cur = _hashes(overrides={"macro": "20261101"})
    d = fv.decide_fund(ALL, stored, cur)
    assert d.run == frozenset({"macro"}) and d.stamp == frozenset({"macro"}) and not d.cache_hit


def test_global_bump_runs_everything():
    d = fv.decide_fund(ALL, _hashes(cv="20261004"), _hashes(cv="20261101"))
    assert d.run == frozenset(ALL)


def test_scoped_run_only_considers_wanted_families():
    stored, cur = _hashes(), _hashes(cv="20261101")
    d = fv.decide_fund({"macro", "fx"}, stored, cur)
    assert d.run == frozenset({"macro", "fx"}) and d.stamp == frozenset({"macro", "fx"})


def test_scoped_run_on_current_families_is_a_cache_hit_not_a_stamp():
    cur = _hashes()
    d = fv.decide_fund({"macro"}, dict(cur), cur)
    assert d.cache_hit and not d.stamp


def test_family_missing_from_state_is_stale():
    cur = _hashes()
    stored = {f: h for f, h in cur.items() if f != "short"}
    assert fv.decide_fund(ALL, stored, cur).run == frozenset({"short"})


def test_force_runs_every_wanted_family_even_when_current():
    cur = _hashes()
    d = fv.decide_fund({"risk", "macro"}, dict(cur), cur, force=True)
    assert d.run == frozenset({"risk", "macro"}) and d.stamp == d.run and d.reason == "force"


def test_lazy_seed_adopts_all_families_when_legacy_hash_still_matches():
    cur = _hashes()
    d = fv.decide_fund(ALL, {}, cur, legacy_stored="abc", legacy_current="abc")
    assert d.seed and d.cache_hit and not d.run and d.stamp == frozenset(ALL)


def test_lazy_seed_applies_even_to_a_scoped_run():
    cur = _hashes()
    d = fv.decide_fund({"macro"}, {}, cur, legacy_stored="abc", legacy_current="abc")
    assert d.seed and d.stamp == frozenset(ALL) and not d.run


@pytest.mark.parametrize("legacy_stored,legacy_current", [("old", "new"), (None, "new"), (None, None)])
def test_no_state_and_no_matching_legacy_hash_runs_wanted(legacy_stored, legacy_current):
    cur = _hashes()
    d = fv.decide_fund(ALL, {}, cur, legacy_stored=legacy_stored, legacy_current=legacy_current)
    assert d.run == frozenset(ALL) and not d.seed and not d.cache_hit


def test_scoped_stamp_leaves_other_families_for_the_next_full_run():
    """Scoped run on a stateless fund stamps only its families; the next unscoped run computes the rest."""
    cur = _hashes()
    first = fv.decide_fund({"regime"}, {}, cur)
    assert first.stamp == frozenset({"regime"})
    stored = {f: cur[f] for f in first.stamp}
    second = fv.decide_fund(ALL, stored, cur)
    assert second.run == frozenset(ALL - {"regime"})


# ---------------------------------------------------------------- what to stamp after the transaction
def test_a_fund_that_wrote_rows_is_stamped_for_every_evaluated_family():
    cur = _hashes()
    d = fv.decide_fund(ALL, {}, cur)
    assert fv.families_to_stamp(d, {}, cur, rows_written=12) == frozenset(ALL)


def test_a_new_fund_that_wrote_nothing_is_not_stamped():
    cur = _hashes()
    d = fv.decide_fund(ALL, {}, cur)
    assert fv.families_to_stamp(d, {}, cur, rows_written=0) == frozenset()


def test_established_fund_with_a_gated_out_family_is_stamped_even_with_zero_rows():
    """The rehearsal finding: a macro-only bump on a fund whose history is too short for the OLS writes no macro rows;
    without a stamp it would be 'processed' on every later run."""
    stored = _hashes()
    cur = _hashes(overrides={"macro": "20261101"})
    d = fv.decide_fund(ALL, stored, cur)
    assert d.run == frozenset({"macro"})
    assert fv.families_to_stamp(d, stored, cur, rows_written=0) == frozenset({"macro"})


def test_scoped_run_on_an_established_fund_is_stamped_for_its_own_families():
    stored = _hashes()
    cur = _hashes(cv="20261101")
    d = fv.decide_fund({"fx"}, stored, cur)
    assert d.run == frozenset({"fx"})
    # the other families are stale too, so the fund is NOT established: nothing is stamped on zero rows
    assert fv.families_to_stamp(d, stored, cur, rows_written=0) == frozenset()
    assert fv.families_to_stamp(d, stored, cur, rows_written=3) == frozenset({"fx"})
