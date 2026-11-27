"""FND-0444, step 1: the closing check `db_check family_class_collisions`. It counts the in-universe pairs of one manager that the promotion gate's own returns rule calls one fund while they carry a
different (or no) fund_family_id, except the couples of sub-funds proven distinct by their documents (shared.config.FAMILY_COLLISION_ALLOWLIST, with a ticket and an expiry). Owner rulings of
2026-11-27: the allowlist is used by this check ONLY (builder, gate and shadows are frozen until the R6 verdict, FND-0455). No database (R-7): fake connections and synthetic returns."""
from __future__ import annotations

import ast
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "gov" / "backlog"))
import rfd_matrix_probe as m  # noqa: E402
from proyecto3.src import duplicate_holdings as dh  # noqa: E402
from shared import config as cfg  # noqa: E402

TODAY = date(2026, 12, 1)
COUPLE = {"ap": "FND-0444", "since": "2026-11-27", "until": "2027-05-26", "a": ("LU0000000011",), "b": ("LU0000000022", "LU0000000033"), "evidence": "two product names on the two documents"}
MONTHS = [f"{2024 + i // 12}-{i % 12 + 1:02d}" for i in range(30)]


def _series(scale=1.0, shift=0.0):
    return {mo: scale * (0.02 if i % 2 else -0.015) + shift for i, mo in enumerate(MONTHS)}


# ---- the allowlist -------------------------------------------------------------------------------------------------------------------------------------------------
def test_a_couple_allows_every_fund_of_one_side_with_every_fund_of_the_other_while_it_is_in_force():
    got = dh.allowed_pairs((COUPLE,), TODAY)
    assert got == {frozenset(("LU0000000011", "LU0000000022")), frozenset(("LU0000000011", "LU0000000033"))}
    assert dh.allowed_pairs((COUPLE,), date(2027, 5, 26)) == got                                           # the last day still counts
    assert dh.allowed_pairs((COUPLE,), date(2027, 5, 27)) == set() and dh.allowed_pairs((COUPLE,), date(2026, 11, 26)) == set()


@pytest.mark.parametrize("change, word", [
    ({"ap": "x"}, "ticket"), ({"a": ()}, "side"), ({"b": ("LU0000000011",)}, "both sides"), ({"a": ("SHORT",)}, "ISIN"), ({"evidence": "no"}, "evidence"),
    ({"until": "2027-05-27"}, "180"), ({"until": "2026-11-01"}, "after"), ({"since": "soon"}, "dates"),
])
def test_a_malformed_couple_is_reported_and_allows_nothing(change, word):
    bad = {**COUPLE, **change}
    assert any(word in p for p in dh.allowlist_problems((bad,))), dh.allowlist_problems((bad,))
    assert dh.allowed_pairs((bad,), TODAY) == set()


def test_the_registered_allowlist_is_valid_and_leaves_the_unresolved_and_the_control_pairs_counted():
    reg = cfg.FAMILY_COLLISION_ALLOWLIST
    assert dh.allowlist_problems(reg) == [] and len(reg) == 9
    assert {e["ap"] for e in reg} == {"FND-0444"} and {e["since"] for e in reg} == {"2026-11-27"} and {e["until"] for e in reg} == {"2027-05-26"}
    funds = {i for e in reg for i in (*e["a"], *e["b"])}
    assert not funds & {"LU0041580167", "LU2402148576", "LU0085953304"}                                     # the unresolved pairs stay counted
    assert not funds & {"LU1670606760", "LU1670606927"}                                                     # the control: two classes of one sub-fund


# ---- the count -----------------------------------------------------------------------------------------------------------------------------------------------------
class _Conn:
    def __init__(self, holdings):
        self.holdings = holdings

    def execute(self, sql, params=()):
        assert "FROM fund_master" in sql and "in_current_universe = 1" in sql
        rows = list(self.holdings)
        return type("C", (), {"fetchall": lambda s: rows})()


def _count(monkeypatch, holdings, series, allowlist=(), on=TODAY):
    monkeypatch.setattr(cfg, "EUR_NAV_CONVERSION_ENABLED", True)
    monkeypatch.setattr(cfg, "FAMILY_COLLISION_ALLOWLIST", allowlist)
    monkeypatch.setattr(dh, "load_returns", lambda conn, isins, window: {"eur": {i: series[i] for i in isins if i in series}, "loc": {i: series[i] for i in isins if i in series}})
    return dh.family_collisions(_Conn(holdings), on)


HOLD = [("LU0000000011", "FAM_1", "Acme"), ("LU0000000022", "FAM_2", "ACME "), ("LU0000000044", "FAM_2", "Acme"), ("LU0000000055", "FAM_9", "Other house")]


def test_split_pairs_are_counted_and_the_same_family_or_another_manager_is_not(monkeypatch):
    series = {i: _series() for i, _f, _mgr in HOLD}
    got = _count(monkeypatch, HOLD, series)
    # 11-22 and 11-44 are split; 22-44 share a family; 55 belongs to another manager
    assert got["split"] == 2 and got["allowed"] == 0 and got["examples"] == [("LU0000000011", "LU0000000022"), ("LU0000000011", "LU0000000044")]


def test_an_allowlisted_pair_is_not_counted_but_reported_and_it_comes_back_when_the_couple_expires(monkeypatch):
    series = {i: _series() for i, _f, _mgr in HOLD}
    got = _count(monkeypatch, HOLD, series, allowlist=(COUPLE,))
    assert got["split"] == 1 and got["allowed"] == 1
    got = _count(monkeypatch, HOLD, series, allowlist=(COUPLE,), on=date(2027, 6, 1))
    assert got["split"] == 2 and got["allowed"] == 0


def test_flat_funds_are_not_judged(monkeypatch):
    series = {i: _series(scale=0.001) for i, _f, _mgr in HOLD}
    assert _count(monkeypatch, HOLD, series)["split"] == 0


def test_it_cannot_tell_without_returns_or_without_the_eur_view(monkeypatch):
    assert _count(monkeypatch, HOLD, {}) is None
    series = {i: dict(list(_series().items())[:5]) for i, _f, _mgr in HOLD}                                  # under the minimum of common months
    assert _count(monkeypatch, HOLD, series) is None
    monkeypatch.setattr(cfg, "EUR_NAV_CONVERSION_ENABLED", False)
    assert dh.family_collisions(_Conn(HOLD), TODAY) is None


# ---- the db_check --------------------------------------------------------------------------------------------------------------------------------------------------
def test_the_check_is_registered_and_takes_no_argument():
    assert m.validate_db_check(["family_class_collisions"]) == []
    assert m.validate_db_check(["family_class_collisions", 1])
    assert "family_class_collisions" in m.validate_db_check(["nope"])[0]


@pytest.mark.parametrize("result, want, text", [
    ({"split": 0, "allowed": 47, "examples": []}, True, "47"),
    ({"split": 2, "allowed": 0, "examples": [("LU0000000011", "LU0000000022")]}, False, "LU0000000011/LU0000000022"),
    (None, None, "cannot tell"),
])
def test_the_probe_passes_only_at_zero_and_never_without_evidence(monkeypatch, result, want, text):
    monkeypatch.setattr(dh, "family_collisions", lambda conn, on: result)
    ok, detail = m.probe_db_check(object(), "family_class_collisions")                                        # object(): a fall-through to the audit check would fail
    assert ok is want and text in detail


def test_the_allowlist_is_read_by_this_check_only():
    """Owner ruling 2026-11-27: selection is frozen, so neither the builder's twin skip nor the promotion gate may read the allowlist (FND-0455)."""
    tree = ast.parse((ROOT / "proyecto3" / "src" / "duplicate_holdings.py").read_text(encoding="utf-8"))
    readers = {fn.name for fn in tree.body if isinstance(fn, ast.FunctionDef) for n in ast.walk(fn) if isinstance(n, ast.Attribute) and n.attr == "FAMILY_COLLISION_ALLOWLIST"}
    assert readers == {"family_collisions"}
    for name in ("portfolio_builder.py", "portfolio_engine.py", "scenario_lifecycle.py"):
        src = (ROOT / "proyecto3" / "src" / name).read_text(encoding="utf-8")
        assert "FAMILY_COLLISION_ALLOWLIST" not in src and "family_collisions" not in src and "allowed_pairs" not in src, name
