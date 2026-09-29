"""
tests/test_silent_fill_scan.py — FND-0137 Section C (Anti-Pattern Code Scanning), 2026-09-29.

Static AST guard against the failure class FND-0114 belongs to: an implicit imputation call
(`.bfill()` / `.ffill()` / `.fillna()`) on a pandas Series/DataFrame, used to paper over missing
macro-data alignment without an explicit review. `.bfill()` specifically is a look-ahead-bias
risk on financial time series (it fills a gap with a LATER value) — exactly FND-0114's mechanism
(rolling_stats.py's old positional `ffill().bfill()` picked a later IPC print instead of the
correct earlier one).

Scope, and why the directive's original wording was narrowed (see FND-0137's own commit history
and doc/reglas/AUDITORIA_ESTADISTICA.md Fase F for the fuller discussion):

  - Only the PANDAS calls are scanned. `COALESCE`/`LEFT JOIN` in raw SQL are NOT flagged: P#1
    makes `COALESCE(excluded.col, col)` MANDATORY on every extracted-field upsert, and a blanket
    ban would flag the correct, required pattern everywhere it's used — the wrong rule entirely.
  - Every hit is a HARD failure, not "OK if wrapped in a logging block" (the directive's original
    exemption condition): a log line next to a look-ahead bfill would not have caught FND-0114
    either, and would let a real defect through as long as it announces itself. Exemption is
    instead the file-level allowlist convention this repo already uses everywhere else
    (tests/_allowlist.py: `{key: reason}`, reason cites an FND ticket or starts with 'DESIGN:'),
    reviewed the same way every other static-guard exemption in this repo is reviewed.

Running this scanner for the first time (2026-09-29) surfaced two real findings beyond what it
was written to confirm (deflation.py::deflate_nav, the FND-0114 fix itself, correctly passes):

  - proyecto2/src/calculations/short_horizon.py::_deflate_nav — a SEPARATE, un-migrated local
    reimplementation of NAV deflation (exact-reindex + ffill + bfill), duplicating deflate_nav()
    (P#11/R-1 violation) instead of calling it. Checked materiality before allowlisting: its
    window is always the trailing SHORT_WINDOWS days (near "now"), never reaching back to the
    ES-CPI leading-gap era the way rolling_stats.py's full-history rolling windows did, so this is
    NOT currently producing wrong live data the way FND-0114 was -- but it is the same fragile
    shape, and a future change to SHORT_WINDOWS or MIN_NAV_ROWS could make it live-wrong with no
    warning. Tracked as FND-0138 (OPT, not BUG), not fixed in this session -- root-cause fix is a
    straightforward swap to `deflation.py::deflate_nav()`, deliberately left for its own
    session/validation pass rather than folded into this scanner's commit.
  - proyecto2/src/calculations/rolling_stats.py::compute_rolling_rows (the `_rf_aligned` risk-free
    rate lookup, not the NAV deflation this module also contains) — `.bfill()` on a reindexed
    rate series can silently replace the intended "fall through to the static risk_free_rate
    scalar for out-of-range dates" behavior (the `np.where(_rf_aligned.isna(), risk_free_rate,
    ...)` a few lines below never fires for those rows once bfill() has already replaced their
    NaN with a look-ahead rate). Smaller blast radius than FND-0114 (a risk-free-rate input to
    Sharpe/Sortino, not a multiplicative NAV rebase) but the same anti-pattern shape. Tracked as
    FND-0139 (INV — materiality not yet confirmed against live data), not fixed in this session.

Both are exempted below with their ticket, NOT with 'DESIGN: safe' -- the reason text makes clear
these are open findings, not reviewed-and-accepted patterns, so a later contributor doesn't read
the exemption as an endorsement.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from _allowlist import bad_reasons  # noqa: E402

_SCAN_DIRS = ("proyecto1", "proyecto2", "proyecto3", "shared")
_SKIP_PARTS = {"tests", "__pycache__", "log", "upload"}
_FILL_METHODS = {"bfill", "ffill", "fillna"}

# {key: reason} — key is "file" or "file::function", same convention as test_dialect_coverage.py.
# reason: 'DESIGN: <explanation>' for a reviewed-and-accepted pattern, or 'FND-#### <note>' for a
# real, open, ticketed finding this scanner surfaced but did not fix.
_SILENT_FILL_EXEMPT: dict[str, str] = {
    "proyecto2/src/calculations/deflation.py::deflate_nav":
        "DESIGN: FND-0114's own canonical fix -- merge_asof(backward) already finds the correct "
        "prior value for every in-coverage date; this bfill() only reaches the genuinely-"
        "uncovered LEADING gap (no earlier IPC value exists at all, not a same-side choice), and "
        "is exactly the anchor WINDOW_FISHER_IDENTITY (FND-0137 Section A) verifies row-by-row.",
    "shared/statistical_audit/timeseries.py::build_window_deflation_frame":
        "DESIGN: reproduces deflate_nav()'s exact contract (see above) so the audit frame checks "
        "calculations against their own spec (FND-0137 Section A) -- not independent imputation.",
    "shared/statistical_audit/invariants.py::check_invariant":
        "DESIGN: fillna(False) on a boolean Series already gated by an explicit notna() mask "
        "upstream (the `applicable` variable) -- defensive only, never changes which rows count "
        "as applicable vs. undecidable (P#1/R-4).",
    "proyecto3/src/fund_scorer.py::compute_base_scores":
        "DESIGN: fillna(1.0) on a categorical Fund_Nature->bonus MAP LOOKUP (a neutral 'no bonus' "
        "default for a nature absent from the map), not a time-series gap fill -- no look-ahead "
        "risk, nothing to interpolate.",
    "proyecto3/src/regime_classifier.py":
        "DESIGN: ffill() only (never bfill), propagating each macro indicator's own last known "
        "print forward to align staggered publication end-dates -- no future data ever used "
        "(file-level: identical pattern at classify_current/estimate_regime_at/get_regime_series).",
    "proyecto3/src/monthly_report.py::_build_regimen":
        "DESIGN: same ffill()-only alignment as regime_classifier.py above, read via clf._macro "
        "for the report's own snapshot.",
    "proyecto2/src/calculations/m2_global_builder.py::build_m2_global":
        "DESIGN: ffill() only, explicitly commented in the source (avoids a sharp jump in the M2 "
        "Global sum when a component like CN/JP M2 stops publishing) -- forward-only, no "
        "look-ahead.",
    "proyecto2/src/calculations/rolling_stats.py::resolve_rf_rate":
        "DESIGN: a single-date point lookup (not a rebase anchor applied across a whole series) "
        "with an explicit `fallback` parameter and pytest coverage pinning it against the "
        "vectorized path below -- bounded blast radius, already tested.",
    "proyecto2/src/calculations/short_horizon.py::_deflate_nav":
        "FND-0138 (OPT): separate, un-migrated local deflation reimplementation (P#11/R-1 "
        "violation) instead of calling deflation.py::deflate_nav(). Checked not currently "
        "live-wrong (window is always the trailing SHORT_WINDOWS days, never reaching the "
        "ES-CPI leading-gap era) -- same fragile shape as the FND-0114 defect, root-cause fix is "
        "a straightforward swap, deliberately not done in this scanner-adding commit.",
    "proyecto2/src/calculations/rolling_stats.py::compute_rolling_rows":
        "FND-0139 (INV): the _rf_aligned risk-free-rate bfill() can mask the intended "
        "np.where(...,risk_free_rate,...) fallback for NAV dates before the RF series' own "
        "coverage begins (once bfill() replaces NaN with a look-ahead rate, isna() no longer "
        "routes that row to the safe scalar fallback) -- materiality against live data not yet "
        "checked, not fixed in this session.",
}


def _production_sources():
    for d in _SCAN_DIRS:
        for p in (_ROOT / d).rglob("*.py"):
            rel = p.relative_to(_ROOT)
            if _SKIP_PARTS & set(rel.parts) or p.name.startswith("test_"):
                continue
            yield rel.as_posix(), p


def _parents(tree: ast.AST) -> dict:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _enclosing_function(node: ast.AST, parents: dict) -> str:
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return "<module>"


def find_silent_fill_calls(src: str) -> list[tuple[str, int, str]]:
    """(function, line, method) for every `.bfill(` / `.ffill(` / `.fillna(` CALL — AST-based, so
    a comment or docstring that merely mentions the method (as this file's own docstring does) is
    ignored."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    parents = _parents(tree)
    hits = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in _FILL_METHODS):
            hits.append((_enclosing_function(n, parents), n.lineno, n.func.attr))
    return sorted(hits, key=lambda h: h[1])


def _exempt(rel: str, func: str) -> bool:
    return rel in _SILENT_FILL_EXEMPT or f"{rel}::{func}" in _SILENT_FILL_EXEMPT


def test_no_unreviewed_silent_fill_on_a_pandas_series():
    offenders = [
        f"{rel}::{fn} (line {ln}, .{method}())"
        for rel, p in _production_sources()
        for fn, ln, method in find_silent_fill_calls(p.read_text(encoding="utf-8", errors="replace"))
        if not _exempt(rel, fn)
    ]
    assert not offenders, (
        "An unreviewed .bfill()/.ffill()/.fillna() call on a pandas Series/DataFrame -- .bfill() "
        "in particular is a look-ahead-bias risk on financial time series (FND-0114's exact "
        "mechanism: filling a gap with a LATER value instead of the correct earlier one). Add a "
        "reviewed entry to _SILENT_FILL_EXEMPT (a 'DESIGN: <reason>' for a reviewed-safe pattern, "
        "or an 'FND-#### <note>' for a real, ticketed, open finding) or fix the call:\n  "
        + "\n  ".join(offenders)
    )


def test_silent_fill_exemptions_are_well_formed_and_not_stale():
    assert not bad_reasons(_SILENT_FILL_EXEMPT), (
        "_SILENT_FILL_EXEMPT: reason must cite an FND-#### ticket or start with "
        "'DESIGN: <explanation >= 20 chars>'"
    )
    known_files = {rel for rel, _ in _production_sources()}
    stale = [k for k in _SILENT_FILL_EXEMPT if k.split("::")[0] not in known_files]
    assert not stale, f"_SILENT_FILL_EXEMPT lists files that no longer exist: {stale}"


# --- the rule itself, proven against known-good and known-bad source ---------------------------

_BAD_BFILL = '''
def deflate(nav_df, ipc_df):
    merged = nav_df.merge(ipc_df, on="date", how="left")
    merged["ipc"] = merged["ipc"].ffill().bfill()
    return merged
'''

_MENTIONS_ONLY_IN_PROSE = '''
def f(x):
    """Never call .bfill() here -- see FND-0114."""
    # a .ffill().bfill() chain would be a look-ahead risk
    return x
'''


def test_rule_flags_a_real_call_but_not_a_comment_or_docstring():
    # Both calls of the chain `.ffill().bfill()` are flagged; ast.walk() visits the OUTER call
    # (bfill) before its own child expression (ffill), so that's the order returned for same-line
    # hits -- asserted as a set here since which one is "first" isn't the property under test.
    assert set(find_silent_fill_calls(_BAD_BFILL)) == {("deflate", 4, "ffill"), ("deflate", 4, "bfill")}
    assert find_silent_fill_calls(_MENTIONS_ONLY_IN_PROSE) == []


def test_rule_reports_module_level_calls_as_module():
    src = "df = load()\ndf2 = df.fillna(0)\n"
    assert find_silent_fill_calls(src) == [("<module>", 2, "fillna")]
