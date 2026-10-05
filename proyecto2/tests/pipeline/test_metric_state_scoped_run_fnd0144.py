"""FND-0144: a scoped --metrics run must not stamp fund_metric_state's per-fund hash."""
import re
from pathlib import Path

import pytest

from proyecto2.src.pipeline import run_pipeline as rp


def test_unscoped_run_covers_all_families():
    assert rp._covers_all_families(None) is True


def test_full_explicit_list_covers_all_families():
    assert rp._covers_all_families(sorted(rp._ALL_METRIC_FAMILIES)) is True


@pytest.mark.parametrize("subset", [["regime"], [], ["risk", "macro"],
                                    sorted(rp._ALL_METRIC_FAMILIES - {"short"})])
def test_scoped_run_does_not_cover_all_families(subset):
    assert rp._covers_all_families(subset) is False


def test_state_upsert_is_guarded_by_family_coverage():
    """Every _upsert_metric_state call in the fund loop must be conditional on what the run covered.

    Two legitimate call sites since FND-0236, in source order: the per-family one (switch on), which stamps the legacy
    row with a composite hash only when every family is current, and the legacy one under `_covers_all_families(...)`.
    """
    src = Path(rp.__file__).read_text(encoding="utf-8")
    calls = [m.start() for m in re.finditer(r"^\s+_upsert_metric_state\(conn, isin", src, re.M)]
    assert len(calls) == 2
    versioned, legacy = (src[max(0, c - 900):c] for c in calls)
    assert "_fam_dec is not None" in versioned and "all(f in _stamp" in versioned
    assert "_covers_all_families(metrics_filter)" in legacy
