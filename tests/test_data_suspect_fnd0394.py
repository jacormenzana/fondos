# tests/test_data_suspect_fnd0394.py
# -*- coding: utf-8 -*-
"""FND-0394 (WS2 of FND-0391): the shared suspicion predicate (shared/data_suspect.py), pure parts.

R-7: no pipeline, no DB; synthetic panels only (no fund is named in the logic, P#5). What the forensics of 2026-11-08 demand is tested explicitly: a metric computed on a small sample must NOT count
(16 of the 22 extreme funds were exactly that), the NAV-level flag is its own tier, share classes are one suspect, money-market funds never reach the INTEGRITY tier, and the result does not depend on row order.
"""
import os
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np
import pandas as pd
import pytest

from shared import config
from shared.data_suspect import KIND_DEVIATION, KIND_INTEGRITY, KIND_NAV_JUMP, find_suspects, robust_z, score_panel

N_METRICS, N_FUNDS = 10, 60


def _panel(extremes=None, nature="N1", seed=11):
    """60 healthy funds x 10 metrics (normal values, source_rows ~100). `extremes` = {isin: {"metrics": [..indices..], "source_rows": n, "nature": nat}}: those funds get a value 80 modified-z away."""
    rng = np.random.default_rng(seed)
    rows = []
    base = {m: rng.normal(0, 1, N_FUNDS) for m in range(N_METRICS)}
    for m, vals in base.items():
        med, mad = float(np.median(vals)), float(np.median(np.abs(vals - np.median(vals))))
        for i, v in enumerate(vals):
            rows.append((f"H{i:03d}", nature, f"metric{m}|since_inception|0|v1", float(v), int(100 + rng.integers(-5, 6))))
        for isin, spec in (extremes or {}).items():
            if m in spec["metrics"]:
                rows.append((isin, spec.get("nature", nature), f"metric{m}|since_inception|0|v1", med + 80.0 / 0.6745 * mad, spec["source_rows"]))
            else:
                rows.append((isin, spec.get("nature", nature), f"metric{m}|since_inception|0|v1", float(rng.normal(0, 1)), 100))
    return pd.DataFrame(rows, columns=["isin", "nature", "metric", "value", "source_rows"])


def test_robust_z_is_the_modified_z_and_refuses_what_it_cannot_judge():
    vals = [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert robust_z(5, vals) == pytest.approx(0.0) and robust_z(9, vals) == pytest.approx(0.6745 * 4 / 2)
    assert robust_z(1, [1, 2, 3]) is None and robust_z(1, [3] * 6) is None


def test_a_metric_on_a_small_sample_does_not_count_but_the_same_value_on_an_adequate_one_does():
    panel = _panel({"SMALL": {"metrics": [0], "source_rows": 3}, "BIG": {"metrics": [0], "source_rows": 100}})
    s = score_panel(panel)
    small, big = s[(s["isin"] == "SMALL") & (s["metric"] == "metric0|since_inception|0|v1")].iloc[0], s[(s["isin"] == "BIG") & (s["metric"] == "metric0|since_inception|0|v1")].iloc[0]
    assert abs(small["z"]) > 50 and abs(big["z"]) > 50                               # equally extreme ...
    assert bool(small["adequate"]) is False and bool(big["adequate"]) is True       # ... only one rests on a usable sample
    suspects = find_suspects(panel)
    assert [x.representative for x in suspects] == ["BIG"] and suspects[0].kinds == (KIND_DEVIATION,)


def test_a_sample_is_small_only_below_the_p10_and_below_half_the_median():
    panel = _panel({"EDGE": {"metrics": [0], "source_rows": 96}})                   # a little under the p10 (~96-98) but nowhere near half the median (100)
    s = score_panel(panel)
    assert bool(s[(s["isin"] == "EDGE") & (s["metric"] == "metric0|since_inception|0|v1")].iloc[0]["adequate"]) is True


def test_unknown_source_rows_count_as_adequate_not_as_small():
    panel = _panel({"UNK": {"metrics": [0], "source_rows": 100}})
    panel.loc[panel["isin"] == "UNK", "source_rows"] = np.nan
    assert [x.representative for x in find_suspects(panel)] == ["UNK"]


def test_integrity_needs_enough_distinct_adequate_metrics_and_never_applies_to_money_market():
    many, few = list(range(9)), list(range(config.DATA_SUSPECT_MIN_FLAGGED_METRICS - 1))     # one metric short of the configured threshold
    panel = pd.concat([_panel({"MANY": {"metrics": many, "source_rows": 100}, "FEW": {"metrics": few, "source_rows": 100}}),
                       _panel({"MMF": {"metrics": many, "source_rows": 100, "nature": "Monetario"}}, nature="Monetario", seed=12)], ignore_index=True)
    by = {x.representative: x for x in find_suspects(panel)}
    assert KIND_INTEGRITY in by["MANY"].kinds and by["MANY"].n_metrics == 9
    assert by["FEW"].kinds == (KIND_DEVIATION,)                                      # below the threshold: a deviation, not an integrity case
    assert by["MMF"].kinds == (KIND_DEVIATION,)                                      # structurally extreme ratios: never INTEGRITY


def test_small_sample_metrics_do_not_build_an_integrity_case():
    panel = _panel({"YOUNG": {"metrics": list(range(10)), "source_rows": 3}})       # ten extreme metrics, all on 3 observations: a young fund, not a defect
    assert find_suspects(panel) == []


def test_the_nav_flag_is_its_own_tier_even_when_every_metric_is_ordinary():
    panel = _panel()
    suspects = find_suspects(panel, nav_flags={"H007": "NAV +93% in 2026-08, peers +2%"})
    assert [(x.representative, x.kinds) for x in suspects] == [("H007", (KIND_NAV_JUMP,))]
    assert suspects[0].detail["nav_flag"] == {"H007": "NAV +93% in 2026-08, peers +2%"}


def test_share_classes_of_one_family_are_one_suspect_with_the_most_extreme_as_representative():
    panel = _panel({"CLS_A": {"metrics": [0, 1], "source_rows": 100}, "CLS_B": {"metrics": [0], "source_rows": 100}, "CLS_C": {"metrics": [0, 1, 2], "source_rows": 100}})
    family = {"CLS_A": "FAM_X", "CLS_B": "FAM_X", "CLS_C": "FAM_X"}
    suspects = find_suspects(panel, family_of=family)
    assert len(suspects) == 1 and suspects[0].family == "FAM_X" and suspects[0].isins == ("CLS_A", "CLS_B", "CLS_C")
    assert suspects[0].representative in ("CLS_A", "CLS_B", "CLS_C")
    assert len(find_suspects(panel)) == 3                                            # without a family map every class stands alone


def test_the_result_does_not_depend_on_row_order_and_the_input_is_not_modified():
    panel = _panel({"X1": {"metrics": [0, 1], "source_rows": 100}, "X2": {"metrics": list(range(9)), "source_rows": 100}})
    before = panel.copy()
    a = find_suspects(panel)
    b = find_suspects(panel.sample(frac=1.0, random_state=3).reset_index(drop=True))
    assert [(x.family, x.kinds, x.max_abs_z) for x in a] == [(x.family, x.kinds, x.max_abs_z) for x in b]
    pd.testing.assert_frame_equal(panel, before)
    assert a[0].max_abs_z >= a[-1].max_abs_z                                         # most extreme first


def test_an_empty_or_malformed_panel_is_handled_not_guessed():
    assert find_suspects(pd.DataFrame(columns=["isin", "nature", "metric", "value", "source_rows"])) == []
    with pytest.raises(ValueError):
        score_panel(pd.DataFrame({"isin": ["A"], "value": [1.0]}))


def test_defaults_come_from_the_config_and_the_selection_switch_ships_off():
    assert config.DATA_SUSPECT_ENABLED is False                                      # nothing reads it yet: the selection effect is WS2's later step, behind this switch
    assert (config.DATA_SUSPECT_MIN_DEV, config.DATA_SUSPECT_MIN_FLAGGED_METRICS) == (50.0, 2)
    assert (config.DATA_SUSPECT_SMALL_SAMPLE_PERCENTILE, config.DATA_SUSPECT_SMALL_SAMPLE_FRACTION) == (10.0, 0.5)
    panel = _panel({"X": {"metrics": [0], "source_rows": 100}})
    assert find_suspects(panel, min_dev=1000.0) == []                                # an explicit parameter beats the default
