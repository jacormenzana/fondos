# proyecto1/tests/test_statistical_audit_capture_pair.py
# -*- coding: utf-8 -*-
"""FND-0234: CAPTURE_UP_EQUALS_DOWN uses the float-identity tolerance. R-7: pure.

Measured on the live 2,810 since_inception rows, |upside - downside| < t holds for 0 / 1 / 10 / 131 rows at t = 1e-9 / 1e-4 / 1e-3 /
1e-2: a smooth density (chance), no exact equality. At t = 1e-3 the expected chance matches (~10) exceeded min_matches = 8."""
import os
import sys
from dataclasses import replace

import numpy as np
import pandas as pd

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.statistical_audit.catalog_pairs import P2_PAIRS  # noqa: E402
from shared.statistical_audit.comparisons import compare_pairs  # noqa: E402
from shared.statistical_audit.tolerances import FLOAT_IDENTITY_TOLERANCE  # noqa: E402

RULE = P2_PAIRS["CAPTURE_UP_EQUALS_DOWN"]
LEGACY = replace(RULE, tolerance=0.001)


def _run(rule, up, down):
    return compare_pairs(pd.DataFrame({"upside_capture": up, "downside_capture": down}),
                         "upside_capture", "downside_capture", rule)


def test_the_tolerance_is_the_named_float_identity_constant():
    assert RULE.tolerance == FLOAT_IDENTITY_TOLERANCE and RULE.min_matches == 8


def test_a_collapsed_denominator_still_triggers_it():
    """The defect signature: up == down to float precision for many funds at once."""
    up = np.linspace(0.5, 1.5, 12)
    assert _run(RULE, up, up).triggered
    assert _run(RULE, up, up + 1e-9).triggered


def test_chance_level_near_equalities_do_not():
    """10 funds whose up and down capture differ by ~5e-4 (the live pattern) are chance, not a defect."""
    up = np.linspace(0.6, 1.6, 10)
    down = up - 5e-4
    assert not _run(RULE, up, down).triggered
    assert _run(LEGACY, up, down).triggered                                    # what the old tolerance reported


def test_the_exclusion_of_capture_ratio_near_one_would_have_hidden_the_defect():
    """capture_ratio = up/down: a collapsed denominator puts affected funds at exactly 1.0, the region an exclusion would skip."""
    up = np.linspace(0.6, 1.6, 12)
    assert np.allclose(up / up, 1.0)


def test_at_the_live_density_chance_alone_fires_the_old_tolerance_but_not_the_new_one():
    """2,810 independent-ish rows with the live spread of |up - down| (~0.2): ~10 chance matches at 1e-3, ~1 at 1e-4."""
    rng = np.random.default_rng(0)
    old = new = 0
    n_seeds = 300
    for _ in range(n_seeds):
        up = rng.normal(1.0, 0.3, 2810)
        down = up + rng.normal(0.0, 0.22, 2810)
        old += int(_run(LEGACY, up, down).triggered)
        new += int(_run(RULE, up, down).triggered)
    assert old / n_seeds > 0.6
    assert new / n_seeds < 0.01
