# proyecto2/tests/discovery/test_nav_discover_delta_fnd0296.py
# -*- coding: utf-8 -*-
"""
FND-0296: `nav_discovery --mode discover --skip-if-recent` was all-or-nothing (skip only if >= 90% of nav_sources had last_checked = today). After the
2026-10-18 partial load (81% refreshed) the guard did not trigger and the launcher re-resolved all 3,763 ISINs (~95 min) to refresh ~750 funds that
already had their Morningstar code. Now discover resolves only ISINs with no nav_sources row, a status other than OK, or no code; --force resolves all.

run_discover is driven for real with a fake connection and a counting stand-in for the Morningstar lookup. No DB, no network (R-7).
"""
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).parent
_P2_ROOT = _HERE.parent.parent
_REPO = _P2_ROOT.parent
for _p in (str(_P2_ROOT), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proyecto2.src.discovery import nav_discovery as nd  # noqa: E402


# ---------------------------------------------------------------- the pure rule
def test_resolved_means_status_ok_and_a_non_blank_code():
    assert nd._is_resolved(("OK", "F000001ATI")) is True
    assert nd._is_resolved(("OK", "")) is False                 # inconsistent row: OK without a code
    assert nd._is_resolved(("OK", "  ")) is False
    assert nd._is_resolved(("OK", None)) is False
    assert nd._is_resolved(("NOT_FOUND", "F000001ATI")) is False
    assert nd._is_resolved(("ERROR", "F1")) is False
    assert nd._is_resolved(None) is False


def test_candidates_are_new_not_ok_or_codeless_isins_only():
    existing = {"A": ("OK", "F1"), "B": ("NOT_FOUND", ""), "C": ("OK", ""), "D": ("ERROR", "F4")}
    assert nd.discover_candidates(["A", "B", "C", "D", "E"], existing) == ["B", "C", "D", "E"]    # E has no row at all


def test_force_resolves_everything_and_keeps_the_order():
    existing = {"A": ("OK", "F1")}
    assert nd.discover_candidates(["B", "A"], existing, force=True) == ["B", "A"]


# ---------------------------------------------------------------- the real run_discover loop
class _Conn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        assert "FROM nav_sources" in sql
        rows = self._rows
        return type("R", (), {"fetchall": lambda s: rows})()

    def commit(self):
        pass


class _Lookup:
    def __init__(self, missing=()):
        self.calls, self.written, self.missing = [], [], set(missing)

    def resolve(self, isin):
        self.calls.append(isin)
        return None if isin in self.missing else {"code": f"F-{isin}", "name": f"Fund {isin}"}

    def write(self, conn, isin, source, code, a, b, c, status, dry_run):
        self.written.append((isin, status))


def _run(monkeypatch, isins, rows, lookup, **kw):
    monkeypatch.setattr(nd, "_resolve_isin", lookup.resolve)
    monkeypatch.setattr(nd, "_write_nav_source", lookup.write)
    monkeypatch.setattr(nd.time, "sleep", lambda s: None)
    return nd.run_discover(_Conn(rows), isins, dry_run=False, verbose=False, **kw)


def test_the_2026_10_18_numbers_resolve_24_funds_not_3763(monkeypatch, capsys):
    """3,739 funds OK with a code + 24 NOT_FOUND (what nav_sources held after the partial load): only the 24 are looked up."""
    isins = [f"LU{n:010d}" for n in range(3763)]
    rows = [(i, "OK", f"F{n}") for n, i in enumerate(isins[:3739])] + [(i, "NOT_FOUND", "") for i in isins[3739:]]
    lookup = _Lookup(missing=isins[3739:])
    _run(monkeypatch, isins, rows, lookup, skip_if_recent=True)
    out = capsys.readouterr().out
    assert sorted(set(lookup.calls)) == isins[3739:] and len(set(lookup.calls)) == 24      # a lookup that returns nothing is retried once -> 48 calls
    assert len(lookup.calls) == 48
    assert "24 de 3763 ISINs requieren resolucion" in out and "3739 ya resueltos se omiten" in out


def test_a_new_fund_without_a_row_is_resolved_and_written(monkeypatch):
    lookup = _Lookup()
    _run(monkeypatch, ["NEW", "OLD"], [("OLD", "OK", "F1")], lookup, skip_if_recent=True)
    assert lookup.calls == ["NEW"] and lookup.written == [("NEW", "OK")]


def test_nothing_to_resolve_returns_without_a_single_lookup(monkeypatch, capsys):
    lookup = _Lookup()
    _run(monkeypatch, ["A", "B"], [("A", "OK", "F1"), ("B", "OK", "F2")], lookup, skip_if_recent=True)
    assert lookup.calls == [] and "Nada que resolver" in capsys.readouterr().out


def test_force_with_skip_if_recent_re_resolves_every_isin(monkeypatch):
    lookup = _Lookup()
    _run(monkeypatch, ["A", "B"], [("A", "OK", "F1"), ("B", "OK", "F2")], lookup, skip_if_recent=True, force=True)
    assert lookup.calls == ["A", "B"]


def test_without_skip_if_recent_the_historic_full_discover_is_unchanged(monkeypatch):
    lookup = _Lookup()
    _run(monkeypatch, ["A", "B"], [("A", "OK", "F1"), ("B", "OK", "F2")], lookup)
    assert lookup.calls == ["A", "B"]


def test_a_failed_lookup_of_a_resolved_fund_cannot_demote_it():
    """The upsert keeps a resolved code and its OK status when a later lookup says NOT_FOUND (P#1 COALESCE): the SQL contract this rule leans on."""
    sql = Path(nd.__file__).read_text(encoding="utf-8")
    start = sql.index("def _write_nav_source")
    body = sql[start:sql.index("def _normalize_nav_scale")]
    assert "COALESCE(NULLIF(excluded.source_id, ''), nav_sources.source_id)" in body
    assert "THEN nav_sources.status" in body


def test_the_launcher_still_passes_skip_if_recent_and_the_help_documents_force():
    launcher = (_REPO / "scripts" / "launch" / "P2_discoverLoadMetrics.bat").read_text(encoding="utf-8", errors="replace")
    assert "--mode discover --skip-if-recent" in launcher
    src = Path(nd.__file__).read_text(encoding="utf-8")
    assert "skip_if_recent=getattr(args, 'skip_if_recent', False), force=args.force" in src
