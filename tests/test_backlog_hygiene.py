# tests/test_backlog_hygiene.py
# -*- coding: utf-8 -*-
"""FND-0323: scripts/audit/backlog_hygiene.py -- pure parts (R-7: no DB, no git). The cases are the ones that motivated it: the SIMPLIFY commit d5397af
changed MULT_* keys that FND-0258 cited (premise sweep), and FND-0235 sat BLOCKED after its release (stale status)."""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_spec = importlib.util.spec_from_file_location("backlog_hygiene", _ROOT / "scripts" / "audit" / "backlog_hygiene.py")
bh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bh)

DIFF = """diff --git a/proyecto3/config/scorer_params.yaml b/proyecto3/config/scorer_params.yaml
--- a/proyecto3/config/scorer_params.yaml
+++ b/proyecto3/config/scorer_params.yaml
@@ -1,4 +1,4 @@
 FX_CONTRIBUTION_LIMIT: 0.6
-MULT_FX_MALUS: 0.8
+MULT_FX_MALUS: 1.0
-MULT_REGIME_RETURN_BONUS: 1.2
+MULT_REGIME_RETURN_BONUS: 1.0
+PORTFOLIO_WEIGHT_METHOD = "equal"   # was score
 context line with UNCHANGED_CONTEXT_FLAG
"""


def test_changed_identifiers_reads_only_added_and_removed_lines_and_skips_headers():
    assert bh.changed_identifiers(DIFF) == ["MULT_FX_MALUS", "MULT_REGIME_RETURN_BONUS", "PORTFOLIO_WEIGHT_METHOD"]


def test_a_word_without_an_underscore_or_in_lowercase_is_not_an_identifier():
    assert bh.changed_identifiers("+CHECK = 1\n+lower_case_name = 2\n+ABC_ = 3\n") == []


def _t(i, status, title="", desc=""):
    return {"action_point_id": i, "status": status, "title": title, "scenario_description": desc, "creation_ts": datetime(2026, 10, 1, 9, 0)}


def _l(i, comment, ts=datetime(2026, 10, 10, 9, 0)):
    return {"action_point_id": i, "comment": comment, "log_ts": ts}


def test_the_sweep_finds_a_pending_ticket_that_cites_a_changed_identifier_in_any_of_its_texts():
    tickets = [_t("FND-0258", "OPEN", title="regime evidence", desc="the multiplier MULT_REGIME_RETURN_BONUS was 1.8"),
               _t("FND-0300", "IN_PROGRESS", title="other"), _t("FND-0301", "CLOSED", desc="MULT_FX_MALUS")]
    logs = [_l("FND-0300", "see PORTFOLIO_WEIGHT_METHOD for the weights")]
    got = bh.tickets_citing(["MULT_FX_MALUS", "MULT_REGIME_RETURN_BONUS", "PORTFOLIO_WEIGHT_METHOD"], tickets, logs)
    assert [(g["id"], g["found"]) for g in got] == [("FND-0258", ["MULT_REGIME_RETURN_BONUS"]), ("FND-0300", ["PORTFOLIO_WEIGHT_METHOD"])]
    both = bh.tickets_citing(["MULT_FX_MALUS"], tickets, logs, include_closed=True)
    assert [g["id"] for g in both] == ["FND-0301"]


def test_the_sweep_matches_whole_tokens_only():
    tickets = [_t("FND-1", "OPEN", desc="MULT_FX_MALUS_EXTRA is a different name")]
    assert bh.tickets_citing(["MULT_FX_MALUS"], tickets, []) == []


def test_a_ticket_citing_no_changed_identifier_is_not_found_the_documented_limit():
    tickets = [_t("FND-0257", "OPEN", desc="the constraint changes the portfolio in 79% of months with score-proportional weights")]
    assert bh.tickets_citing(["PORTFOLIO_WEIGHT_METHOD"], tickets, []) == []


def test_stale_status_lists_only_watched_statuses_idle_for_the_threshold_most_idle_first():
    tickets = [_t("FND-0235", "BLOCKED"), _t("FND-0243", "IN_PROGRESS"), _t("FND-0300", "OPEN"), _t("FND-0310", "IN_PROGRESS"), _t("FND-0311", "CLOSED")]
    logs = [_l("FND-0235", "x", datetime(2026, 10, 9, 22, 0)), _l("FND-0243", "x", datetime(2026, 10, 18, 14, 0)),
            _l("FND-0243", "newer", datetime(2026, 10, 19, 9, 0)), _l("FND-0300", "x", datetime(2026, 9, 1, 9, 0))]
    got = bh.stale_status(tickets, logs, today=date(2026, 10, 25), days=14)
    assert [(g["id"], g["idle_days"]) for g in got] == [("FND-0310", 24), ("FND-0235", 16)]       # OPEN and CLOSED are never listed; 0243 is only 6 days idle
    assert bh.stale_status(tickets, logs, today=date(2026, 10, 25), days=30) == []


def test_a_watched_ticket_without_logs_counts_from_its_creation():
    got = bh.stale_status([_t("FND-9", "BLOCKED")], [], today=date(2026, 10, 25), days=14)
    assert got == [{"id": "FND-9", "status": "BLOCKED", "idle_days": 24, "last_log": "2026-10-01"}]


@pytest.mark.parametrize("bad", ["--output=x", "a;rm", "a b", "..HEAD", "-p"])
def test_git_diff_refuses_a_revision_that_could_inject_an_option(bad):
    with pytest.raises((ValueError, RuntimeError)):
        bh.git_diff(bad, ["shared/config.py"])


def test_the_cli_prints_both_reports_from_the_client_readers_and_exits_zero(monkeypatch, capsys):
    from shared import backlog_client as bc
    tickets = [_t("FND-0258", "OPEN", desc="MULT_FX_MALUS was 0.8"), _t("FND-0235", "BLOCKED")]
    logs = [_l("FND-0235", "held", datetime(2026, 10, 1, 9, 0))]
    monkeypatch.setattr(bc, "list_tickets", lambda **kw: tickets)
    monkeypatch.setattr(bc, "list_logs", lambda **kw: logs)
    monkeypatch.setattr(bh, "git_diff", lambda rev, paths: "-MULT_FX_MALUS: 0.8\n+MULT_FX_MALUS: 1.0\n")
    assert bh.main(["--premise-sweep", "abc^..abc"]) == 0
    out = capsys.readouterr().out
    assert "1 identifiers changed" in out and "FND-0258 [OPEN] MULT_FX_MALUS" in out
    assert bh.main(["--stale-status", "--days", "1"]) == 0
    assert "FND-0235 [BLOCKED] idle" in capsys.readouterr().out


def test_the_tool_has_no_sql_and_no_write():
    src = (_ROOT / "scripts" / "audit" / "backlog_hygiene.py").read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    for word in ("INSERT ", "UPDATE ", "DELETE ", "SELECT ", "execute("):
        assert word not in code
