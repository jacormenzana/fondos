"""FND-0422: the cycle report says when the nature classifier was not given its volatility band or Morningstar class (a swallowed batch-load failure).
Pure functions and a fake connection; no database."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "launch"))
import p1p2_cycle_report as cr  # noqa: E402

HEALTHY_DISPATCH = (2921, 37, 357)          # the 11-13 pass: 1.3% vol None, 12.2% msbench None
FAILED_DISPATCH = (2880, 2880, 2880)        # the 11-14 passes: 100% both
AVAILABLE = (2943, 2900, 2600)              # 1.5% lack a band, 11.7% lack an asset class


def test_a_healthy_pass_is_ok_and_raises_no_attention():
    v = cr.nature_evidence_verdict(HEALTHY_DISPATCH, AVAILABLE)
    assert v["state"] == "ok" and v["missing"] == []
    assert cr.nature_evidence_attention(v) == []
    assert "vol None 1.3%" in cr.nature_evidence_line(v)


def test_the_fnd_0422_pass_is_missing_both_sources():
    v = cr.nature_evidence_verdict(FAILED_DISPATCH, AVAILABLE)
    assert v["state"] == "missing" and v["missing"] == ["vol", "msbench"]
    items = cr.nature_evidence_attention(v)
    assert len(items) == 1 and "SC-H" in items[0] and "OPT-B3" in items[0]


def test_one_source_missing_names_only_that_source():
    v = cr.nature_evidence_verdict((2900, 2900, 330), AVAILABLE)
    assert v["missing"] == ["vol"]
    assert "msbench" not in cr.nature_evidence_attention(v)[0].split("(")[0]


def test_a_targeted_pass_says_nothing():
    v = cr.nature_evidence_verdict((12, 12, 12), AVAILABLE)
    assert v["state"] == "too_few" and cr.nature_evidence_attention(v) == []
    assert "pocos para medir" in cr.nature_evidence_line(v)


def test_a_universe_that_truly_lacks_the_evidence_is_not_flagged():
    # 60% of funds have no band in the database: 60% None in the dispatches is the truth, not a failed load.
    v = cr.nature_evidence_verdict((2900, 1740, 340), (2943, 1177, 2600))
    assert v["state"] == "ok"


def test_tolerance_boundary():
    assert cr.nature_evidence_verdict((1000, 250, 0), (1000, 1000, 1000))["missing"] == ["vol"]     # 25 points over
    assert cr.nature_evidence_verdict((1000, 90, 0), (1000, 1000, 1000))["missing"] == []           # 9 points over


class _Cur:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, rows, fail=False):
        self.rows, self.fail, self.calls, self.rolled_back = rows, fail, [], False

    def execute(self, sql, params=()):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append((sql, tuple(params)))
        return _Cur(self.rows[sql])

    def rollback(self):
        self.rolled_back = True


def test_collect_passes_the_cycle_start_and_returns_the_verdict():
    conn = _Conn({cr.Q_EVIDENCE_DISPATCH: FAILED_DISPATCH, cr.Q_EVIDENCE_AVAILABLE: AVAILABLE})
    v = cr.collect_nature_evidence(conn, date(2026, 11, 14))
    assert v["state"] == "missing"
    assert (cr.Q_EVIDENCE_DISPATCH, (date(2026, 11, 14),)) in conn.calls


def test_collect_is_fail_soft():
    conn = _Conn({}, fail=True)
    v = cr.collect_nature_evidence(conn, date(2026, 11, 14))
    assert "RuntimeError" in v["error"] and conn.rolled_back
    assert cr.nature_evidence_attention(v) == [] and "no disponible" in cr.nature_evidence_line(v)
