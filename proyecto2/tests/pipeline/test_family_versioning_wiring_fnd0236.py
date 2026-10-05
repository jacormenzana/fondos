"""FND-0236: how run_pipeline is wired to the per-family versions (no database).

The switch (shared.config.FAMILY_VERSIONING_ENABLED) is off by default, so every check that matters for production
today is "nothing changed": the same algorithm_version, the same OLS gate, the same families.
"""
import re
import sys
from pathlib import Path

import pytest

from proyecto2.src.pipeline import run_pipeline as rp
from proyecto2.src.utils import family_versions as fv

SRC = Path(rp.__file__).read_text(encoding="utf-8")


@pytest.fixture
def switch(monkeypatch):
    from shared import config

    # run_pipeline imports the pure module as `src.utils.family_versions` (proyecto2/ on sys.path), a different module
    # object from `proyecto2.src.utils.family_versions` imported above: patch the copy the pipeline really uses.
    live = sys.modules[rp.family_token.__module__]

    def _set(on, overrides=None):
        monkeypatch.setattr(config, "FAMILY_VERSIONING_ENABLED", on, raising=False)
        monkeypatch.setattr(live, "FAMILY_CALC_OVERRIDES", dict(overrides or {}))
    return _set


def test_switch_is_a_plain_boolean():
    """A code constant, flipped by the owner after migrate_fund_metric_family_state.py --apply (not an env var)."""
    from shared import config
    assert isinstance(config.FAMILY_VERSIONING_ENABLED, bool)


def test_all_metric_families_is_the_single_definition():
    assert rp._ALL_METRIC_FAMILIES == frozenset(fv.ALL_FAMILIES)


def test_algo_is_calc_version_for_every_family_while_off_even_with_overrides(switch):
    switch(False, {"macro": "20261101"})
    assert {rp._algo(f) for f in fv.ALL_FAMILIES} == {rp.CALC_VERSION}


def test_algo_is_calc_version_for_every_family_while_on_without_overrides(switch):
    switch(True, {})
    assert {rp._algo(f) for f in fv.ALL_FAMILIES} == {rp.CALC_VERSION}


def test_algo_carries_the_override_only_for_its_family(switch):
    switch(True, {"macro": "20261101"})
    assert rp._algo("macro") == f"{rp.CALC_VERSION}.20261101"
    assert rp._algo("risk") == rp.CALC_VERSION


def test_no_write_site_still_uses_the_bare_calc_version():
    assert "algorithm_version=CALC_VERSION" not in SRC


def test_every_write_site_names_a_known_family():
    names = re.findall(r'algorithm_version=_algo\((?:"(\w+)"|spec\.name)\)', SRC)
    assert len(names) >= 7                                   # risk, short, macro, 3x rolling, registry families
    assert {n for n in names if n} <= set(fv.ALL_FAMILIES)
    assert "algorithm_version=_algo(spec.name)" in SRC


def test_fund_loop_gates_on_the_per_fund_selector():
    """Inside the per-fund loop every family gate is _wantf (the decision's families); only the post-loop
    cross-sectional snapshot keeps the run-level _want."""
    loop = SRC[SRC.index("# ---- Since inception"):SRC.index("# v25: consumir flag RECALCULATE_METRICS")]
    assert "_want(" not in loop
    assert loop.count("_wantf(") >= 6


class _Conn:
    def __init__(self, row):
        self._row = row

    def execute(self, *_a, **_k):
        return self

    def fetchone(self):
        return self._row


def test_ols_gate_off_compares_with_calc_version(switch):
    switch(False, {"macro": "20261101"})
    assert rp._ols_is_fresh(_Conn(("2026-4", 100, rp.CALC_VERSION)), "X", 100, "2026-4") is True


def test_ols_gate_on_goes_stale_when_only_the_macro_token_changes(switch):
    switch(True, {"macro": "20261101"})
    old = _Conn(("2026-4", 100, rp.CALC_VERSION))                     # computed before the macro-only bump
    new = _Conn(("2026-4", 100, f"{rp.CALC_VERSION}.20261101"))
    assert rp._ols_is_fresh(old, "X", 100, "2026-4") is False
    assert rp._ols_is_fresh(new, "X", 100, "2026-4") is True


def test_the_cross_sectional_snapshot_needs_a_rolling_run_when_versioning_is_on():
    assert 'fam_run_counts["rolling"] > 0' in SRC


def test_run_summary_keeps_the_legacy_keys_and_appends_family_counters():
    assert SRC.count("{_fam_summary()}") == 2                          # [RUN END] log line and RUN_SUMMARY row
    assert "ols_funds={n_ols_funds} cond_guard_funds={n_cond_funds}" in SRC
