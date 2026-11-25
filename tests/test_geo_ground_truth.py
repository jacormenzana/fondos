"""FND-0441 Step 1: the sampling, excerpt and holdout-lock pieces of scripts/audit/p1/geo_ground_truth.py (pure, no database)."""
import sys
from collections import Counter
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts" / "audit" / "p1"))

import geo_ground_truth as G  # noqa: E402


def _items(n_a=60, n_b=30, n_c=10):
    out = []
    for k, n in (("ES|RV", n_a), ("EN|RV", n_b), ("ES|MM", n_c)):
        out += [{"isin": f"{k}-{i:03d}", "stratum": k} for i in range(n)]
    return out


def test_sample_is_proportional_deterministic_and_has_the_asked_size():
    s = G.stratified_sample(_items(), 20)
    assert len(s) == 20 and Counter(x["stratum"] for x in s) == {"ES|RV": 12, "EN|RV": 6, "ES|MM": 2}
    assert [x["isin"] for x in s] == [x["isin"] for x in G.stratified_sample(list(reversed(_items())), 20)]       # input order never matters


def test_the_remainder_goes_to_the_largest_fraction():
    s = G.stratified_sample(_items(50, 30, 20), 7)                      # quotas 3.5 / 2.1 / 1.4: the seventh fund goes to the first stratum
    assert Counter(x["stratum"] for x in s) == {"ES|RV": 4, "EN|RV": 2, "ES|MM": 1}


def test_sample_order_is_the_md5_of_the_isin_not_the_isin():
    s = G.stratified_sample([{"isin": f"A{i:03d}", "stratum": "x"} for i in range(50)], 10)
    got = {x["isin"] for x in s}
    assert got != {f"A{i:03d}" for i in range(10)}
    assert got == set(sorted((f"A{i:03d}" for i in range(50)), key=G.md5_key)[:10])


def test_excluded_isins_are_never_sampled():
    items = _items()
    first = {x["isin"] for x in G.stratified_sample(items, 20)}
    second = {x["isin"] for x in G.stratified_sample(items, 20, exclude=first)}
    assert not first & second and len(second) == 20


def test_a_sample_larger_than_the_population_is_the_population():
    assert len(G.stratified_sample(_items(3, 2, 1), 50)) == 6


def test_excerpt_starts_at_the_objective_section_and_is_bounded():
    text = "Documento de datos fundamentales. Finalidad ... " * 5 + "Objetivos El fondo invierte en renta variable de todo el mundo. " + "x" * 3000
    out = G.objective_excerpt(text, 200)
    assert out.startswith("Objetivos El fondo invierte") and len(out) <= 200
    assert G.objective_excerpt("Product. Investment objective: the Fund invests in Asia. " + "y" * 900, 100).startswith("Investment objective")
    assert G.objective_excerpt("no section heading here " * 20, 50) == ("no section heading here " * 20)[:50]
    assert G.objective_excerpt(None, 50) == ""


def test_holdout_stays_locked_until_a_commit_declares_the_patterns_frozen():
    assert G.holdout_unlocked(["feat: x", "docs: GEO patterns discussed"]) is False
    assert G.holdout_unlocked(["fix(p1): patterns\n\nGEO-PATTERNS-FROZEN FND-0441"]) is True
    with pytest.raises(PermissionError):
        G.assert_readable("holdout", frozen=False)
    G.assert_readable("holdout", frozen=True)
    G.assert_readable("dev", frozen=False)
    G.assert_readable("kiidonly", frozen=False)
