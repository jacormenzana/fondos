"""
tests/test_family_refresh.py -- the pure side of the family-nature refresh and of the audited P3 bypass (FND-0244 follow-up). R-7: no DB.

The SQL side (pending selection, atomic DONE row, audit rows) is in tests/test_family_refresh_pg.py.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import family_refresh as fr  # noqa: E402
from shared import gate_bypass as gb  # noqa: E402

from proyecto3.src import data_freshness as df  # noqa: E402


# ---------------------------------------------------------------- summaries and reachability
def test_summarize_pending_is_bounded():
    assert fr.summarize_pending([]) == "0 pending"
    assert fr.summarize_pending(["A", "B"]) == "2 pending: A, B"
    many = [f"I{i:03d}" for i in range(120)]
    s = fr.summarize_pending(many, limit=50)
    assert s.startswith("120 pending: I000") and "I049" in s and "I050" not in s and s.endswith("(+70 more)")


def test_split_reachable_keeps_master_order_and_reports_the_unreachable():
    reachable, unreachable = fr.split_reachable(["C", "A", "Z"], ["A", "B", "C"])
    assert reachable == ["A", "C"] and unreachable == ["Z"]
    assert fr.split_reachable([], ["A"]) == ([], [])
    assert fr.split_reachable(["A"], []) == ([], ["A"])


# ---------------------------------------------------------------- the family nature wins in a refresh pass
def test_the_family_nature_overrides_the_own_vote_and_the_own_vote_stays_visible():
    nature, trace, overridden = fr.apply_family_nature(
        "Renta Variable", "Mixtos", {"reason": "kiid+vol", "name": 0.0}, 0.62)
    assert (nature, overridden) == ("Mixtos", True)
    assert "Mixtos" in trace["reason"] and "own evidence: Renta Variable" in trace["reason"] and "kiid+vol" in trace["reason"]
    assert trace["name"] == 0.0                                                   # the other trace keys are kept


def test_the_same_nature_is_marked_overridden_but_the_trace_is_untouched():
    trace = {"reason": "kiid"}
    assert fr.apply_family_nature("Mixtos", "Mixtos", trace, 0.9) == ("Mixtos", trace, True)


def test_without_a_family_nature_the_own_resolution_is_untouched():
    trace = {"reason": "kiid"}
    for fam in (None, ""):
        assert fr.apply_family_nature("Mixtos", fam, trace, 0.9) == ("Mixtos", trace, False)


# ---------------------------------------------------------------- the P3 gate check
def test_the_gate_is_binary_and_names_the_funds():
    ok = df.family_refresh_check([])
    assert ok.ok and ok.name == "family_refresh_pending" and not ok.show_age and ok.detail == "0 pending"
    bad = df.family_refresh_check(["IE00B4PTJ249", "LU1615060362"])
    assert not bad.ok and "2 pending: IE00B4PTJ249, LU1615060362" in bad.detail and "--family-nature-refresh" in bad.detail


def test_a_pending_family_refresh_makes_the_report_stale():
    checks = [df.family_refresh_check(["X"])]
    assert [c.name for c in df.stale_checks(checks)] == ["family_refresh_pending"]
    assert "[STALE] family_refresh_pending" in df.format_report(checks)


# ---------------------------------------------------------------- the bypass audit rows
STALE = [("family_refresh_pending", "2 pending: A, B"), ("nav_p10", "newest=2026-08-31")]


def test_bypass_rows_have_one_row_per_check_and_one_per_pending_fund():
    rows = gb.bypass_rows(STALE, "cartera_x_202610", "owner", "host1", ["A", "B"])
    assert [(r[0], r[1]) for r in rows] == [(None, "family_refresh_pending"), (None, "nav_p10"),
                                            ("A", "family_refresh_pending"), ("B", "family_refresh_pending")]
    assert all("owner@host1" in r[2] and "cartera_x_202610" in r[2] for r in rows)
    assert "pending family refresh: 2 funds (A, B)" in rows[0][2]                # only the family check lists the funds
    assert "pending family refresh" not in rows[1][2]


def test_bypass_rows_without_pending_funds_are_check_rows_only():
    rows = gb.bypass_rows([("nav_p10", "old")], "s", "u", "h", [])
    assert len(rows) == 1 and rows[0][0] is None


def test_the_isin_list_in_a_message_is_bounded():
    pending = [f"I{i:03d}" for i in range(80)]
    msg = gb.check_message("family_refresh_pending", "80 pending", "s", "u", "h", pending, limit=50)
    assert "I049" in msg and "I050" not in msg and msg.endswith(", ...)")
    assert len(gb.bypass_rows([("family_refresh_pending", "x")], "s", "u", "h", pending)) == 1 + 80   # per-fund rows are NOT truncated


def test_constants_are_the_step_names_the_view_and_the_gate_read():
    assert gb.STEP_BYPASS == "P3_STALE_BYPASS" == gb.FLAG_CODE and gb.AUDIT_DOMAIN == "p3_gate"
    assert fr.STEP_CORRECTION == "FAMILY_NATURE_CORRECTION" and fr.STEP_DONE == "FAMILY_REFRESH_DONE"
