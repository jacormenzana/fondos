# tests/test_family_key_switch_diff_fnd0246.py
# -*- coding: utf-8 -*-
"""FND-0246: the pure helpers of scripts/audit/family_key_switch_diff.py (the read-only OFF/ON comparison of the family key). No database."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit" / "family_key_switch_diff.py"
_spec = importlib.util.spec_from_file_location("family_key_switch_diff_under_test", _SCRIPT)
sd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sd)


def test_family_map_issues_ids_like_the_builder():
    groups = {("b", "x"): ["I3"], ("a", "y"): ["I1", "I2"]}
    assert sd.family_map(groups) == {"I1": "FAM_000001", "I2": "FAM_000001", "I3": "FAM_000002"}


def test_diff_partitions_reports_merges_and_splits_by_members_not_by_id():
    off = {"A": "F1", "B": "F2", "C": "F3", "D": "F3"}
    on = {"A": "G1", "B": "G1", "C": "G2", "D": "G3"}
    d = sd.diff_partitions(off, on)
    assert d["merges"] == {"G1": ["F1", "F2"]}          # two OFF families become one
    assert d["splits"] == {"F3": ["G2", "G3"]}          # one OFF family is separated
    assert sd.diff_partitions(off, off) == {"merges": {}, "splits": {}}


def test_diff_partitions_ignores_isins_missing_on_one_side():
    assert sd.diff_partitions({"A": "F1"}, {"A": "G1", "Z": "G2"}) == {"merges": {}, "splits": {}}


def test_nature_conflicts_look_at_active_members_only():
    on = {"A": "G1", "B": "G1", "C": "G1"}
    nature = {"A": "Monetario", "B": "Renta Variable", "C": "Mixtos"}
    assert sd.nature_conflicts(on, nature, active={"A", "B"}) == {"G1": ["Monetario", "Renta Variable"]}
    assert sd.nature_conflicts(on, nature, active={"A"}) == {}                      # one active member cannot disagree with itself


def test_representatives_and_held_collisions():
    res = pd.DataFrame({"isin": ["A", "B", "A"], "subportfolio": ["Defensiva", "Defensiva", "Dinamica"]})
    assert sd.representatives(res) == {("A", "Defensiva"), ("B", "Defensiva"), ("A", "Dinamica")}
    assert sd.representatives(pd.DataFrame()) == set() and sd.representatives(None) == set()
    assert sd.held_collisions(["A", "B", "C", "Q"], {"A": "F1", "B": "F1", "C": "F2"}) == {"F1": ["A", "B"]}
