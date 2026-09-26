# proyecto1/tests/test_merge_benchmark_sources.py
# -*- coding: utf-8 -*-
"""
Source precedence for the per-ISIN benchmark signal (core.benchmark_normalizer
.merge_benchmark_sources, extracted from pipeline.run_block on 2026-09-26).

R-7: runs without importing pipeline.py or core.io.

The one behaviour change vs the inline code it replaced: a MORNINGSTAR row whose asset_class is NULL
no longer hides a KIID row that has one (5 of 1,939 ISINs that have both sources). Everything else
is asserted unchanged: Morningstar preferred, defaults, non-null disagreements NOT resolved here.
"""
from __future__ import annotations

import os
import sys

_P1 = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _P1 not in sys.path:
    sys.path.insert(0, _P1)

from core.benchmark_normalizer import merge_benchmark_sources  # noqa: E402


def row(isin, source, asset_class, name="N", role=None, conf=None):
    return (isin, source, asset_class, role, name, conf)


def test_morningstar_is_preferred_over_kiid_when_both_have_an_asset_class():
    out = merge_benchmark_sources([
        row("A", "KIID", "Equity", "kiid name", conf="HIGH"),
        row("A", "MORNINGSTAR", "Mixed", "ms name", conf="HIGH"),
    ])
    assert out["A"]["asset_class"] == "Mixed" and out["A"]["benchmark_name"] == "ms name"


def test_a_non_null_disagreement_is_not_resolved_here_morningstar_wins():
    """Decision 2026-09-26: 'Mixed' (fund allocation) vs 'Equity' (benchmark) are two different
    questions; the SC-H rules weigh both through `confidence`. No KIID override."""
    out = merge_benchmark_sources([row("A", "KIID", "Equity"), row("A", "MORNINGSTAR", "Fixed Income")])
    assert out["A"]["asset_class"] == "Fixed Income"


def test_null_morningstar_asset_class_does_not_hide_a_kiid_asset_class():
    out = merge_benchmark_sources([
        row("A", "MORNINGSTAR", None, "unmapped ms name", conf="LOW"),
        row("A", "KIID", "Equity", "kiid name", conf="HIGH"),
    ])
    assert out["A"] == {"asset_class": "Equity", "benchmark_role": "asset_proxy",
                        "benchmark_name": "kiid name", "confidence": "HIGH"}


def test_null_morningstar_asset_class_is_kept_when_kiid_has_none_either():
    out = merge_benchmark_sources([row("A", "MORNINGSTAR", None, "ms"), row("A", "KIID", None, "k")])
    assert out["A"]["benchmark_name"] == "ms" and out["A"]["asset_class"] is None


def test_null_morningstar_asset_class_is_kept_when_there_is_no_kiid_row():
    assert merge_benchmark_sources([row("A", "MORNINGSTAR", None, "ms")])["A"]["benchmark_name"] == "ms"


def test_kiid_only_and_morningstar_only_isins():
    out = merge_benchmark_sources([row("K", "KIID", "Rate"), row("M", "MORNINGSTAR", "Equity")])
    assert out["K"]["asset_class"] == "Rate" and out["M"]["asset_class"] == "Equity"


def test_defaults_are_unchanged():
    out = merge_benchmark_sources([row("M", "MORNINGSTAR", "Equity"), row("K", "KIID", "Equity")])
    assert out["M"]["confidence"] == "HIGH" and out["K"]["confidence"] == "MEDIUM"   # KIID semi-redundant
    assert out["M"]["benchmark_role"] == out["K"]["benchmark_role"] == "asset_proxy"


def test_explicit_role_and_confidence_are_preserved():
    out = merge_benchmark_sources([row("A", "KIID", "Rate", role="hurdle_rate", conf="LOW")])
    assert out["A"]["benchmark_role"] == "hurdle_rate" and out["A"]["confidence"] == "LOW"


def test_other_sources_are_ignored_and_empty_input_is_empty():
    assert merge_benchmark_sources([]) == {}
    assert merge_benchmark_sources([row("A", "OTHER", "Equity")]) == {}


def test_matches_the_previous_inline_implementation_except_for_the_null_case():
    """Cross-check against a verbatim copy of the two-pass loop that lived in pipeline.run_block."""
    def old(rows):
        by: dict = {}
        seen: set = set()
        for i, s, ac, role, name, conf in rows:
            if s == "MORNINGSTAR":
                by[i] = {"asset_class": ac, "benchmark_role": role or "asset_proxy",
                         "benchmark_name": name, "confidence": conf or "HIGH"}
                seen.add(i)
        for i, s, ac, role, name, conf in rows:
            if s == "KIID" and i not in seen:
                by[i] = {"asset_class": ac, "benchmark_role": role or "asset_proxy",
                         "benchmark_name": name, "confidence": conf or "MEDIUM"}
        return by

    rows = [
        row("1", "MORNINGSTAR", "Equity", "a"), row("1", "KIID", "Rate", "b"),
        row("2", "KIID", "Fixed Income", "c"),
        row("3", "MORNINGSTAR", "Mixed", "d", role="hurdle_rate", conf="LOW"),
        row("4", "MORNINGSTAR", None, "e"), row("4", "KIID", None, "f"),
        row("5", "MORNINGSTAR", "Equity", "g"),
    ]
    assert merge_benchmark_sources(rows) == old(rows)
