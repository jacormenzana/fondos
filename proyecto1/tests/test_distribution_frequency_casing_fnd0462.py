# proyecto1/tests/test_distribution_frequency_casing_fnd0462.py
# -*- coding: utf-8 -*-
"""FND-0462 item 2: the extractor emits Distribution_Frequency in upper case and the writer stores Title case, but the allowed-values check ran
before the writer and logged ~157 false 'not in allowed values' rows per full pass. The silent casing normalisation now covers the column.

R-7: no imports of pipeline.py or core.io.
"""
from __future__ import annotations

import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from classify_utils import validate_all_semantic_consistency  # noqa: E402

_RULE = "Allowed-Values:Distribution_Frequency"


@pytest.mark.parametrize("raw, canonical", [
    ("MONTHLY", "Monthly"), ("QUARTERLY", "Quarterly"), ("SEMI-ANNUAL", "Semi-Annual"), ("ANNUAL", "Annual"),
])
def test_upper_case_frequency_is_normalised_without_a_warning(raw, canonical):
    result = validate_all_semantic_consistency({"Accumulation_Policy": "DISTRIBUTION", "Distribution_Frequency": raw})
    assert [w for w in result["warnings"] if w.get("rule") == _RULE] == []
    assert result["corrected_record"]["Distribution_Frequency"] == canonical


def test_a_value_outside_the_catalogue_is_still_reported():
    result = validate_all_semantic_consistency({"Accumulation_Policy": "DISTRIBUTION", "Distribution_Frequency": "WEEKLY"})
    assert [w for w in result["warnings"] if w.get("rule") == _RULE], "a real out-of-catalogue value must still warn"
