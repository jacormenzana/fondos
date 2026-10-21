"""
tests/test_period_key_scan.py -- R-9 "one period key" static guard, 2026-10-18.

Static AST guard against the failure class the 2026-10-18 reliability study measured: two series of DIFFERENT origin combined on
their RAW stamps instead of on the canonical period key (`shared/period_key.py`). `fund_nav_monthly` dates 28.7% of its rows on the
last business day and the rest on the calendar month-end; `series_inflation` and the macro OLS factors are stamped on the month
START. When `compute_macro_sensitivity` inner-joined raw NAV dates to the month-end factor index, ~30% of every fund's months
vanished: 96% of the betas of last-business-day funds moved materially and 23% changed validity
(`out/audit/p2_reliability_20261018/REPORT.md`).

What this scanner is really protecting. Reading all 19 flagged sites to write the exemptions below surfaced the root cause of
F1, which the study did not name: TEN separate inline copies of the same idiom

    s.index = s.index + pd.offsets.MonthEnd(0); s = s[~s.index.duplicated(keep="last")]

in capture_ratios (x2), currency_factor (x3), peer_panel, regime_returns, rolling_stats (x2) and m2_global_builder -- every one of
them correct. `macro_sensitivity` has the DEDUPLICATION half of that idiom (line 426) and not the MONTH-END half. The convention
was never wrong in this repo; it was COPIED, and one copy lost a line. That is a P#11 / R-1 violation first and an alignment bug
second, which is why R-9 ships a single helper and this scan, rather than a tenth correct copy.

Scope and deliberate narrowings (the alternative -- flagging every `.merge` / `.reindex` by name -- was measured at 53
`file::function` keys in this scope and `proyecto3/src`, more than double the existing `_SILENT_FILL_EXEMPT`; an exemption list
nobody finishes reading is not a gate):

  - Directories: `proyecto2/src/calculations`, `proyecto2/src/readers`, `shared`. `proyecto3/src` (PIT, backtest, scorer) is a
    SECOND PASS, once this heuristic is calibrated against it -- 30 more candidate keys there. Until then R-9 is unenforced in P3.
  - A call is flagged only when an operand is DATE-BEARING by the heuristic in `_date_bearing()`. The heuristic can miss; it is
    not a proof. The per-family invariance contracts
    (`proyecto2/tests/calculations/test_period_key_invariance.py`) are the backstop that catches what a name-based scan cannot.
  - A `merge` / `join` with an explicit `on=` naming only non-date keys (`on=["isin", "end_pos"]`) is NOT flagged: R-9 governs
    joins ON a period key, and a positional or identity join is a different question.
  - A vertical `pd.concat(frames, ignore_index=True)` is NOT flagged -- stacking rows is not an alignment. Only `axis=1` or an
    explicit `join=` is.

Exemption convention: `{file::function: reason}`, identical to `test_silent_fill_scan.py` and `test_dialect_coverage.py`.
`DESIGN: <reason> (review YYYY-MM-DD)` for a reviewed-correct site -- the review date is MANDATORY on a DESIGN entry and a lapsed
one fails this suite, so "reviewed once in 2026" cannot silently become the permanent state. `FND-#### <note>` for a real, open,
ticketed violation; those carry no review date because the ticket is the review.
"""
from __future__ import annotations

import ast
import re
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from _allowlist import bad_reasons  # noqa: E402

_SCAN_DIRS = ("proyecto2/src/calculations", "proyecto2/src/readers", "shared")
_SKIP_PARTS = {"tests", "__pycache__", "log", "upload"}

#: Methods that ALIGN two objects. `concat` is handled separately (only axis=1 / explicit join=).
_JOIN_METHODS = {"merge", "join", "reindex", "align", "intersection", "reindex_like", "combine_first", "merge_asof"}

#: A HEURISTIC, not a type system: tokens that make an operand look like it carries dates. Widening it costs false positives
#: (each needs an exemption); narrowing it costs misses (the invariance contracts are the backstop). Bare `index` / `idx` were
#: deliberately removed -- they matched every `reindex(df.index)` in the audit code, none of which is a date join.
_DATE_VOCAB = re.compile(
    r"(date|fecha|_dt\b|dt_|month|mes\b|period|nav|ipc|cpi|macro|series|r_fondo|benchmark|regime|window|cutoff|stamp)",
    re.IGNORECASE)

#: Expressions that BUILD a date-bearing object: a name assigned from one of these is date-bearing for the rest of its function.
_DATE_BUILDERS = re.compile(
    r"(to_datetime|DatetimeIndex|date_range|set_index\(\s*[\"']date|MonthEnd|period_key|month_ordinals|cpi_lookup_dates)")

_MERGE_KEY_KWARGS = ("on", "left_on", "right_on")
_REVIEW = re.compile(r"\(review (\d{4}-\d{2}-\d{2})\)")

# {file::function: reason} -- see the module docstring for the convention.
_PERIOD_KEY_EXEMPT: dict[str, str] = {
    # ---- FND-0300 (deployed 2026-10-19, NAV_MONTH_END_ALIGN_ENABLED ON): the raw-date defects the study measured here are fixed by
    # ---- labelling the NAV at the reader (db_readers.load_nav -> shared/nav_month_end.py, built on shared/period_key.py), so these
    # ---- functions receive month-end labelled NAV and their own code is unchanged. The scan is textual, hence the entries.
    "proyecto2/src/calculations/macro_sensitivity.py::compute_macro_sensitivity":
        "DESIGN: FND-0300. The inner join of r_fondo with the month-end macro factor index (the ~30%-of-months defect) is fed "
        "month-end labelled NAV by db_readers.load_nav, so every month enters the regression (macro n_obs 124 -> 162.5). "
        "Invariance asserted by test_macro_betas_are_invariant. (review 2027-04-21)",
    "proyecto2/src/calculations/rolling_stats.py::compute_rolling_rows":
        "DESIGN: FND-0300. The `DateOffset(months=w)` cutoff runs on month-end labelled dates (a window is 12 returns whatever "
        "the stamp); the trailing slice is returns.trailing_window (FND-0302). Invariance asserted by "
        "test_rolling_timeseries_rows_are_invariant. (review 2027-04-21)",
    "shared/nav_month_end.py::persistence_windows":
        "DESIGN: FND-0300. The 36-month persistence windows (P2 and PIT share this one definition) count whole calendar months "
        "with MonthEnd in the ON branch, so the membership of a month does not depend on its stamp; the two DateOffset calls are "
        "the OFF-switch branch (the stored convention, bit-for-bit) and go with the switch once the OFF path is retired. "
        "(review 2027-04-21)",
    "proyecto2/src/calculations/returns.py::trailing_window":
        "DESIGN: FND-0302. THE definition of a trailing rolling window, shared by the rolling horizons and by momentum; it runs "
        "on month-end labelled NAV, so a 12-month window is 12 returns whatever the stamp. (review 2027-04-21)",
    "proyecto2/src/calculations/momentum.py::compute_momentum":
        "DESIGN: FND-0302. The fund's window comes from returns.trailing_window, the same function as the peers' rolling return_ann; "
        "the DateOffset here only tests that the history spans N years, over month-end labelled data. (review 2027-04-21)",

    # ---- reviewed-correct: both sides already carry the canonical key ----------------------------------------------------
    # Each of these is an INLINE copy of the period-key idiom. They are correct today; the reason R-9 exists is that there
    # are ten of them. Migrating them to shared/period_key.py is the DRY follow-up on FND-0300, not a behaviour change.
    "proyecto2/src/calculations/capture_ratios.py::load_peer_benchmark":
        "DESIGN: FND-0202 (CAPTURE_MONTH_END_ENABLED, ON). The peer frame's dates get `+ MonthEnd(0)` at line 86 and the "
        "pivot is reindexed onto a dense month-end grid, so the benchmark is on the canonical key before anything joins to "
        "it. Measured invariant to the stamp convention in the 2026-10-18 study. (review 2027-04-18)",
    "proyecto2/src/calculations/capture_ratios.py::compute_capture_ratios":
        "DESIGN: FND-0202. The fund's own NAV index gets `+ MonthEnd(0)`, duplicates collapse keep-last, and it is "
        "reindexed onto the same dense month-end grid as the peer benchmark BEFORE the `join=\"inner\"` concat at line 150 "
        "-- both sides on the canonical key, which is exactly R-9 rule 3. (review 2027-04-18)",
    "proyecto2/src/calculations/peer_panel.py::capture_returns":
        "DESIGN: FND-0255's per-run peer panel reproduces load_peer_benchmark's grid bit-for-bit (`+ MonthEnd(0)`, pivot, "
        "reindex onto a dense month-end range) -- that bit-identity is the ticket's own acceptance test. (review 2027-04-18)",
    "proyecto2/src/calculations/currency_factor.py::load_fx_eur_divisa":
        "DESIGN: both legs of the inner-join concat at line 118 are snapped -- df_fx gets `+ MonthEnd(0)` and a keep-last "
        "deduplication at lines 113-115, df_usdeur through the same loader. (review 2027-04-18)",
    "proyecto2/src/calculations/currency_factor.py::_compute_eur_view":
        "DESIGN: FND-0243. The NAV index gets `+ MonthEnd(0)` and keep-last deduplication at lines 182-183 before the "
        "log returns, and the FX legs come from load_fx_eur_divisa already on the key. (review 2027-04-18)",
    "proyecto2/src/calculations/currency_factor.py::compute_currency_factor":
        "DESIGN: same snapping as _compute_eur_view at lines 263-264, both legs on the canonical key before the inner-join "
        "concat at line 272. The study measured the fx family invariant. (review 2027-04-18)",
    "proyecto2/src/calculations/regime_returns.py::compute_regime_returns":
        "DESIGN: the NAV index is snapped and keep-last deduplicated before the inner-join concat with regime_df at line "
        "196, and the `_k.reindex(r_simple.index)` at line 193 reindexes onto an index derived from the SAME object "
        "(FND-0253's one-month-return mask), not across two series. Measured invariant. (review 2027-04-18)",
    "proyecto2/src/calculations/m2_global_builder.py::build_m2_global":
        "DESIGN: every series_macro row gets `+ MonthEnd(0)` at line 156 BEFORE the pivot, which is what makes the "
        "reindex at line 278 safe -- it matters here because series_macro is mixed (m2_yoy is month-start for EU and "
        "month-end for CN/JP/US), so an unsnapped pivot would put one indicator on two rows. (review 2027-04-18)",
    "proyecto2/src/calculations/deflation.py::deflate_nav":
        "DESIGN: FND-0241's canonical fix and the original statement of this rule -- the merge_asof key is "
        "`cpi_lookup_dates(...)`, i.e. shared/period_key.to_period_key, not the raw NAV date. A DAILY caller "
        "(short_horizon) passes align_month_end=False, which is R-9 rule 8. (review 2027-04-18)",
    "proyecto2/src/calculations/rolling_stats.py::resolve_rf_rate":
        "DESIGN: both sides are snapped -- the rate series gets `+ MonthEnd(0)` and keep-last deduplication at lines "
        "111-112, and the lookup target is `Timestamp(date) + MonthEnd(0)` at line 114, so the union-reindex joins two "
        "objects already on the canonical key. (review 2027-04-18)",
    "shared/eur_nav.py::to_eur":
        "DESIGN: FND-0243 joins a MONTHLY NAV to a DAILY ECB rate with merge_asof(backward) under an explicit "
        "`tolerance=max_gap_days`, which is R-9 rule 8: the daily series keeps its daily dates and the monthly side takes "
        "the nearest preceding quote. Snapping either side to a month-end key would pick a rate that was never "
        "published on that day. (review 2027-04-18)",
    "shared/eur_nav.py::convert_frame":
        "DESIGN: the per-currency form of to_eur above (same merge_asof, `by=\"_ccy\"`); the two concats in this function "
        "stack rows with ignore_index and are not alignments. (review 2027-04-18)",
    "shared/statistical_audit/timeseries.py::nav_with_ipc":
        "DESIGN: FND-0234 -- the audit reproduces deflate_nav's contract exactly, through the SAME cpi_lookup_dates "
        "helper, so the producer and the audit of the producer cannot disagree about which CPI month a NAV belongs to. "
        "Changing this without changing deflate_nav would be the defect. (review 2027-04-18)",
    "shared/statistical_audit/timeseries.py::build_window_deflation_frame":
        "DESIGN: the `on=[\"isin\", \"date\"]` merge joins the stored fund_metric_timeseries rows to the NAV rows they "
        "were computed from -- the same series on both sides, so the raw stamp IS the correct key and snapping would "
        "break the identity the audit is checking. The start-row join is positional (start_pos). (review 2027-04-18)",
}


def _production_sources():
    for d in _SCAN_DIRS:
        for p in sorted((_ROOT / d).rglob("*.py")):
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


def _seg(node, src: str) -> str:
    try:
        return ast.get_source_segment(src, node) or ""
    except Exception:                                            # pragma: no cover - defensive
        return ""


def _date_names(tree: ast.AST, src: str) -> set[str]:
    """Names (and assigned column labels) bound to a date-building expression anywhere in the module.

    Module-wide rather than per-function on purpose: a false POSITIVE costs one reviewed exemption line, a false negative
    costs an undetected violation, and this scan errs toward the first.
    """
    names: set[str] = set()
    for n in ast.walk(tree):
        if not isinstance(n, (ast.Assign, ast.AnnAssign)) or n.value is None:
            continue
        rhs = _seg(n.value, src)
        if not (_DATE_BUILDERS.search(rhs) or _DATE_VOCAB.search(rhs)):
            continue
        for target in (n.targets if isinstance(n, ast.Assign) else [n.target]):
            for sub in ast.walk(target):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
                elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    names.add(sub.value)                         # frame["_d"] = pd.to_datetime(...)
    return names


def _date_bearing(blob: str, known: set[str]) -> bool:
    if _DATE_VOCAB.search(blob) or _DATE_BUILDERS.search(blob):
        return True
    return any(re.search(rf"\b{re.escape(name)}\b", blob) for name in known if name)


def find_period_key_risks(src: str) -> list[tuple[str, int, str]]:
    """(function, line, kind) for every alignment of two possibly date-bearing operands, and every calendar `DateOffset`.

    AST-based, so a mention in a comment or docstring -- including this module's own -- is ignored.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    parents = _parents(tree)
    known = _date_names(tree, src)
    hits: list[tuple[str, int, str]] = []

    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        func = n.func
        attr = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")

        # A calendar offset in months or years IS the window definition R-9 rule 4 forbids on raw dates.
        if attr == "DateOffset" and any(k.arg in ("months", "years") for k in n.keywords):
            hits.append((_enclosing_function(n, parents), n.lineno, "DateOffset"))
            continue

        kwargs = {k.arg: _seg(k.value, src) for k in n.keywords if k.arg}
        if attr == "concat":
            if kwargs.get("axis") not in ("1", '"columns"', "'columns'") and "join" not in kwargs:
                continue                                         # stacking rows, not aligning them
            receiver = ""
        elif attr in _JOIN_METHODS:
            receiver = _seg(func.value, src) if isinstance(func, ast.Attribute) else ""
            if attr == "join" and (receiver.strip()[:1] in "\"'" or "os.path" in receiver or "Path" in receiver):
                continue                                         # str.join / path join
            keys = [kwargs[k] for k in _MERGE_KEY_KWARGS if k in kwargs]
            if keys and not any(_date_bearing(k, known) for k in keys):
                continue                                         # an explicit join on non-date keys is out of R-9's scope
        else:
            continue

        blob = " ".join([receiver, *(_seg(a, src) for a in n.args), *(f"{k}={v}" for k, v in kwargs.items())])
        if _date_bearing(blob.replace("\n", " "), known):
            hits.append((_enclosing_function(n, parents), n.lineno, attr))

    return sorted(hits, key=lambda h: h[1])


def _exempt(rel: str, func: str) -> bool:
    return rel in _PERIOD_KEY_EXEMPT or f"{rel}::{func}" in _PERIOD_KEY_EXEMPT


def test_no_unreviewed_alignment_on_a_raw_date():
    offenders = [
        f"{rel}::{fn} (line {ln}, {kind})"
        for rel, p in _production_sources()
        for fn, ln, kind in find_period_key_risks(p.read_text(encoding="utf-8", errors="replace"))
        if not _exempt(rel, fn)
    ]
    assert not offenders, (
        "R-9 (one period key): two series of different origin are aligned, or a calendar window is cut, without the "
        "canonical period key. Convert both sides with shared/period_key.py::to_period_key (or snap_frame) and window by "
        "month ordinals, or add a reviewed entry to _PERIOD_KEY_EXEMPT ('DESIGN: <reason> (review YYYY-MM-DD)' for a "
        "reviewed-correct site, 'FND-#### <note>' for a real, open, ticketed violation):\n  "
        + "\n  ".join(offenders)
    )


def test_period_key_exemptions_are_well_formed_and_not_stale():
    assert not bad_reasons(_PERIOD_KEY_EXEMPT), (
        "_PERIOD_KEY_EXEMPT: reason must cite an FND-#### ticket or start with 'DESIGN: <explanation >= 20 chars>'"
    )
    known_files = {rel for rel, _ in _production_sources()}
    stale_file = [k for k in _PERIOD_KEY_EXEMPT if k.split("::")[0] not in known_files]
    assert not stale_file, f"_PERIOD_KEY_EXEMPT lists files that no longer exist: {stale_file}"

    # A key whose site no longer trips the scan is a key that has to go: an exemption list that outlives its violations
    # stops being a list of accepted risks and becomes background noise (the same rule as test_silent_fill_scan).
    live = set()
    for rel, p in _production_sources():
        for fn, _ln, _kind in find_period_key_risks(p.read_text(encoding="utf-8", errors="replace")):
            live.add(rel)
            live.add(f"{rel}::{fn}")
    stale_key = sorted(k for k in _PERIOD_KEY_EXEMPT if k not in live)
    assert not stale_key, (
        "_PERIOD_KEY_EXEMPT exempts sites that no longer align on a raw date -- remove them (if a fix landed, its "
        "exemption goes out in the same commit):\n  " + "\n  ".join(stale_key)
    )


def test_design_exemptions_carry_a_review_date_that_has_not_lapsed():
    """A 'DESIGN:' entry is a judgement about today's code, so it expires. An FND entry does not: its ticket is the review."""
    missing, lapsed = [], []
    today = date.today()
    for key, reason in _PERIOD_KEY_EXEMPT.items():
        if not reason.startswith("DESIGN:"):
            continue
        match = _REVIEW.search(reason)
        if not match:
            missing.append(key)
        elif date.fromisoformat(match.group(1)) < today:
            lapsed.append(f"{key} (review was due {match.group(1)})")
    assert not missing, f"DESIGN exemptions without a '(review YYYY-MM-DD)' date: {missing}"
    assert not lapsed, (
        "DESIGN exemptions whose review date has passed -- re-read the site and either fix it or set a new date:\n  "
        + "\n  ".join(lapsed))


# --- the rule itself, proven against known-bad and repaired source ----------------------------------------------------

_BAD_RAW_JOIN = '''
def compute(nav_df, macro_df):
    nav = nav_df.set_index("date")["nav"].sort_index()
    r_fondo = np.log(nav / nav.shift(1)).dropna()
    return pd.concat([r_fondo.rename("r_fondo"), macro_df], axis=1, join="inner")
'''

_REPAIRED = '''
def compute(nav_df, macro_df):
    nav = nav_df.set_index("date")["nav"].sort_index()
    nav.index = to_period_key(nav.index)
    nav = nav[~nav.index.duplicated(keep="last")]
    r_fondo = np.log(nav / nav.shift(1)).dropna()
    return r_fondo.to_frame("r_fondo").combine_first(macro_df)
'''

_BAD_WINDOW = '''
def window(nav_df, months):
    cutoff = nav_df["date"].max() - pd.DateOffset(months=months)
    return nav_df[nav_df["date"] > cutoff]
'''

_REPAIRED_WINDOW = '''
def window(nav_df, months):
    ords = period_ordinals(nav_df["date"])
    return nav_df[ords > ords.max() - months]
'''

_MENTIONS_ONLY_IN_PROSE = '''
def f(x):
    """Never pd.concat(..., join="inner") two raw date indexes here -- see R-9."""
    # a DateOffset(months=12) cutoff on a raw date would be the same mistake
    return x
'''

_NON_DATE_JOIN = '''
def attach(rows, last_pos):
    return rows.merge(last_pos, on="isin", how="left")
'''

_ROW_STACK = '''
def stack(frames):
    return pd.concat(frames, ignore_index=True)
'''


def test_rule_flags_a_raw_date_join_and_a_calendar_window():
    assert find_period_key_risks(_BAD_RAW_JOIN) == [("compute", 5, "concat")]
    assert find_period_key_risks(_BAD_WINDOW) == [("window", 3, "DateOffset")]


def test_rule_passes_the_repaired_versions():
    # combine_first on two objects already carrying the key is still reported as an alignment IF an operand looks
    # date-bearing -- what must not survive the repair is the RAW-date join, so the repaired form routes through
    # to_period_key first. The scan's job is to make that conversion visible in the diff, not to disappear.
    assert [k for _f, _l, k in find_period_key_risks(_REPAIRED_WINDOW)] == []
    assert all(kind != "DateOffset" for _f, _l, kind in find_period_key_risks(_REPAIRED))


def test_rule_ignores_prose_non_date_joins_and_row_stacking():
    assert find_period_key_risks(_MENTIONS_ONLY_IN_PROSE) == []
    assert find_period_key_risks(_NON_DATE_JOIN) == []
    assert find_period_key_risks(_ROW_STACK) == []
