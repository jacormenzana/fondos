# proyecto1/tests/test_statistical_audit_rca.py
# -*- coding: utf-8 -*-
"""FND-0251 Commit C: deterministic RCA-E tag. Only mechanical stale states (NAV age, metric version) are tagged; nothing else is ever
inferred, and the tag changes no severity, class or gate. Pure functions + the loader over a fake connection (R-7: no DB)."""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import date
from types import SimpleNamespace

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.statistical_audit.rca import RCA_E_PREFIX, load_stale_states, stale_states, tag_findings  # noqa: E402

AS_OF = date(2026, 10, 11)


def _f(isins, rcc="deflator mismatch", **kw):
    d = {"rule_id": "WINDOW_FISHER_IDENTITY", "severity": "ALARM", "rule_class": "HARD_INVARIANT",
         "violating_isins": tuple(isins), "root_cause_candidate": rcc}
    d.update(kw)
    return d


# ---------------- the states ----------------

def test_a_nav_older_than_the_limit_is_a_state_and_the_limit_is_inclusive():
    got = stale_states({"OLD": date(2022, 2, 28), "EDGE": date(2026, 8, 12), "OVER": date(2026, 8, 11)}, {}, None, AS_OF, 60)
    assert set(got) == {"OLD", "OVER"}                       # 60 d (EDGE) is not stale, 61 d is
    assert got["OLD"].startswith("NAV 1686 d old")


def test_metrics_left_on_an_older_algorithm_version_are_a_state():
    got = stale_states({}, {"FROZEN": "20261003", "LIVE": "20261010", "NONE": None}, "20261010", AS_OF, 60)
    assert got == {"FROZEN": "metrics at algorithm_version 20261003 < 20261010"}


def test_a_fund_with_no_nav_and_no_metrics_makes_no_claim():
    assert stale_states({"X": None}, {"X": None}, "20261010", AS_OF, 60) == {}
    assert stale_states({}, {"Y": "20261003"}, None, AS_OF, 60) == {}          # no current version known: nothing to compare against


def test_the_nav_state_wins_when_both_apply():
    got = stale_states({"A": date(2022, 2, 28)}, {"A": "20261003"}, "20261010", AS_OF, 60)
    assert got["A"].startswith("NAV")


# ---------------- tagging ----------------

STATES = {"A": "NAV 1686 d old (limit 60 d)", "B": "metrics at algorithm_version 20261003 < 20261010"}


def test_a_finding_is_tagged_only_when_every_violating_isin_is_in_a_mechanical_state():
    all_in, one_out, none_named = _f(["A", "B"]), _f(["A", "NEW"]), _f([])
    n = tag_findings([all_in, one_out, none_named], STATES)
    assert n == 1
    assert all_in["root_cause_candidate"].startswith(RCA_E_PREFIX) and "all 2 violating funds" in all_in["root_cause_candidate"]
    assert "stale NAV" in all_in["root_cause_candidate"] and "stale metric version" in all_in["root_cause_candidate"]
    assert all_in["root_cause_candidate"].endswith("deflator mismatch")              # the detector's own hypothesis is kept
    assert one_out["root_cause_candidate"] == "deflator mismatch"                    # UNCLASSIFIED: left exactly as it was
    assert none_named["root_cause_candidate"] == "deflator mismatch"


def test_the_tag_changes_nothing_a_gate_reads_and_is_idempotent():
    f = _f(["A"])
    before = {k: f[k] for k in ("rule_id", "severity", "rule_class", "violating_isins")}
    assert tag_findings([f], STATES) == 1
    assert {k: f[k] for k in before} == before
    once = f["root_cause_candidate"]
    assert tag_findings([f], STATES) == 0 and f["root_cause_candidate"] == once      # re-running does not stack prefixes


def test_a_finding_without_a_prior_hypothesis_is_tagged_cleanly():
    f = _f(["A"], rcc=None)
    tag_findings([f], STATES)
    assert f["root_cause_candidate"].startswith("RCA-E: all 1 violating funds") and not f["root_cause_candidate"].endswith(". ")


def test_no_market_or_engineering_label_exists():
    import shared.statistical_audit.rca as rca
    assert not hasattr(rca, "RCA_M") and not hasattr(rca, "RCA_I")


# ---------------- the loader ----------------

class _Conn:
    def __init__(self, nav, algo, current):
        self.nav, self.algo, self.current, self.sql = nav, algo, current, []

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        self.sql.append(s)
        rows = []
        if s.startswith("SELECT isin, MAX(date) FROM fund_nav_monthly"):
            rows = [(i, d) for i, d in self.nav.items() if i in params[0]]
        elif s.startswith("SELECT isin, MAX(algorithm_version) FROM fund_metrics"):
            rows = [(i, v) for i, v in self.algo.items() if i in params[0]]
        elif s.startswith("SELECT MAX(algorithm_version) FROM fund_metrics"):
            rows = [(self.current,)]
        return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: list(rows))


def test_loader_combines_nav_age_and_version_and_is_read_only():
    conn = _Conn({"A": date(2022, 2, 28), "OK": date(2026, 9, 30)}, {"A": "20261003", "B": "20261003", "OK": "20261010"}, "20261010")
    got = load_stale_states(conn, ["A", "B", "OK"], 60, AS_OF)
    assert set(got) == {"A", "B"} and got["A"].startswith("NAV")
    assert got["B"].startswith("metrics at algorithm_version")
    assert conn.sql and all(s.startswith("SELECT") for s in conn.sql)


def test_loader_with_no_isins_asks_nothing():
    conn = _Conn({}, {}, "20261010")
    assert load_stale_states(conn, [], 60, AS_OF) == {} and conn.sql == []


def test_the_audit_runner_applies_it_without_ever_failing_the_audit():
    spec = importlib.util.spec_from_file_location("run_statistical_audit_rca", os.path.join(_ROOT, "scripts", "audit", "run_statistical_audit.py"))
    runner = importlib.util.module_from_spec(spec)
    sys.modules["run_statistical_audit_rca"] = runner
    spec.loader.exec_module(runner)
    run = runner.AuditRun("p2_metrics")
    run.findings = [_f(["A"])]

    class _Boom:
        def execute(self, *a, **k):
            raise RuntimeError("db gone")

        def rollback(self):
            pass
    runner._apply_rca_e(_Boom(), run)                          # swallowed: the audit goes on, the finding is untouched
    assert run.rca_e_tagged == 0 and run.findings[0]["root_cause_candidate"] == "deflator mismatch"
    runner._apply_rca_e(_Conn({"A": date(2022, 2, 28)}, {}, None), run)
    assert run.rca_e_tagged == 1 and run.findings[0]["root_cause_candidate"].startswith("RCA-E:")
