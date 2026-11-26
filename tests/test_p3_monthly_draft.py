"""FND-0447: the scheduled monthly DRAFT-only P3 build. The capture check and the record (scripts/launch/p3_monthly_draft.py), and the property the owner's
standing order of 2026-11-26 rests on: nothing in the scheduled chain promotes a scenario to PRODUCTION. No database: fake connections only (R-7)."""
from __future__ import annotations

import ast
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "launch"))
import p3_monthly_draft as M  # noqa: E402

LAUNCH = ROOT / "scripts" / "launch"
NOW = datetime(2026, 12, 1, 8, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------------------------------------------------------------------- pure
def test_the_draft_is_always_required_and_a_stream_only_while_its_switch_is_on():
    assert M.required_items(SimpleNamespace()) == ["DRAFT"]
    cfg = SimpleNamespace(P3_FEE_PENALTY_SHADOW_ENABLED=True, P3_HYSTERESIS_SHADOW_ENABLED=False)
    assert M.required_items(cfg) == ["DRAFT", "P3_FEE_PENALTY_SHADOW"]
    cfg = SimpleNamespace(P3_FEE_PENALTY_SHADOW_ENABLED=True, P3_HYSTERESIS_SHADOW_ENABLED=True, P3_BASELINE_SHADOW_ENABLED=True)
    assert M.required_items(cfg) == ["DRAFT", "P3_FEE_PENALTY_SHADOW", "P3_HYSTERESIS_SHADOW", "P3_BASELINE_SHADOW"]


def test_a_truthy_value_that_is_not_true_does_not_make_a_stream_required():
    assert M.required_items(SimpleNamespace(P3_FEE_PENALTY_SHADOW_ENABLED="False")) == ["DRAFT"]


def test_the_live_config_lists_both_shadow_streams_that_are_on_today():
    import shared.config as cfg
    want = ["DRAFT"] + [step for switch, step in M.SHADOW_STREAMS if getattr(cfg, switch, False) is True]
    assert M.required_items(cfg) == want


def test_the_month_is_the_utc_month_like_the_r6_reader():
    assert M.month_of("2026-12-01T00:30:00+00:00") == "2026-12"
    local_after_midnight = datetime(2026, 12, 1, 0, 30, tzinfo=timezone(timedelta(hours=1)))       # 23:30 UTC on 30 November
    assert M.month_of(local_after_midnight) == "2026-11"
    assert M.month_of(datetime(2026, 12, 15, 9, 0)) == "2026-12"                                     # a naive datetime is UTC


def test_the_month_rule_is_the_readers_own():
    sys.path.insert(0, str(ROOT / "scripts" / "audit" / "p3"))
    import p3_shadow_read as R
    row = {"built_at": "2027-05-31T23:59:59+00:00"}
    assert R.month_of(row) == M.month_of(row["built_at"])


def test_missing_items():
    have = {"DRAFT": {"2026-12"}, "P3_FEE_PENALTY_SHADOW": {"2026-11"}, "P3_HYSTERESIS_SHADOW": {"2026-11", "2026-12"}}
    req = ["DRAFT", "P3_FEE_PENALTY_SHADOW", "P3_HYSTERESIS_SHADOW"]
    assert M.missing_items(have, req, "2026-12") == ["P3_FEE_PENALTY_SHADOW"]
    assert M.missing_items(have, req, "2026-11") == ["DRAFT"]
    assert M.missing_items({}, req, "2026-12") == req


@pytest.mark.parametrize("rc, missing, want", [
    (0, [], ("OK", 0)),
    (0, ["P3_FEE_PENALTY_SHADOW"], ("INCOMPLETE", M.EXIT_NOT_CAPTURED)),
    (2, ["DRAFT"], ("FAILED", 0)),
    (105, [], ("FAILED", 0)),
])
def test_outcome(rc, missing, want):
    assert M.outcome(rc, missing) == want


def test_the_helpers_codes_never_collide_with_the_build_or_the_launcher():
    assert M.EXIT_CAPTURED != M.EXIT_NOT_CAPTURED
    assert M.EXIT_CAPTURED not in (0, 2) and M.EXIT_NOT_CAPTURED not in (0, 2)           # 2 = P3_EXIT_STALE_INPUTS of the build
    assert all(1 <= c <= 99 for c in (M.EXIT_CAPTURED, M.EXIT_NOT_CAPTURED))            # tool range: 100-199 belongs to the launcher


# ---------------------------------------------------------------------------------------------------------------------------- database, faked
class _Cur:
    def __init__(self, value): self.value = value
    def fetchone(self): return (self.value,)


class _Conn:
    def __init__(self, drafts_this_month: int):
        self.drafts, self.sql, self.commits, self.closed = drafts_this_month, [], 0, False
    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        return _Cur(self.drafts if "portfolio_scenarios" in sql else None)
    def commit(self): self.commits += 1
    def close(self): self.closed = True


def _rows(*months):
    return [{"built_at": f"{m}-15T10:00:00+00:00"} for m in months]


@pytest.fixture()
def fake(monkeypatch):
    from proyecto3.src import hysteresis_shadow as hs
    state = {"rows": {}, "conn": None}
    monkeypatch.setattr(hs, "read_rows", lambda conn, limit=120, step=hs.STEP: state["rows"].get(step, []))
    import shared.db
    monkeypatch.setattr(shared.db, "get_connection", lambda: state["conn"])
    import shared.config as cfg
    monkeypatch.setattr(cfg, "P3_FEE_PENALTY_SHADOW_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg, "P3_HYSTERESIS_SHADOW_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg, "P3_BASELINE_SHADOW_ENABLED", False, raising=False)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None): return NOW
    monkeypatch.setattr(M, "datetime", _Frozen)
    return state


def test_months_by_item_reads_the_draft_since_the_first_of_the_utc_month(fake):
    conn = _Conn(drafts_this_month=1)
    fake["rows"] = {"P3_FEE_PENALTY_SHADOW": _rows("2026-11", "2026-12")}
    got = M.months_by_item(conn, ["DRAFT", "P3_FEE_PENALTY_SHADOW"], NOW)
    assert got == {"DRAFT": {"2026-12"}, "P3_FEE_PENALTY_SHADOW": {"2026-11", "2026-12"}}
    (sql, params), = [s for s in conn.sql if "portfolio_scenarios" in s[0]]
    assert "built_at >= %s" in sql and params == (datetime(2026, 12, 1, tzinfo=timezone.utc),)


def test_status_says_build_when_a_stream_has_no_row_this_month(fake, capsys):
    fake["conn"] = _Conn(drafts_this_month=1)
    fake["rows"] = {"P3_FEE_PENALTY_SHADOW": _rows("2026-12"), "P3_HYSTERESIS_SHADOW": _rows("2026-11")}
    assert M.main(["status"]) == 0
    assert "build needed" in capsys.readouterr().out and fake["conn"].closed and fake["conn"].commits == 0


def test_status_says_build_when_no_draft_was_built_this_month(fake):
    fake["conn"] = _Conn(drafts_this_month=0)
    fake["rows"] = {"P3_FEE_PENALTY_SHADOW": _rows("2026-12"), "P3_HYSTERESIS_SHADOW": _rows("2026-12")}
    assert M.main(["status"]) == 0


def test_status_says_captured_when_the_draft_and_every_stream_have_this_months_row(fake):
    fake["conn"] = _Conn(drafts_this_month=1)
    fake["rows"] = {"P3_FEE_PENALTY_SHADOW": _rows("2026-12"), "P3_HYSTERESIS_SHADOW": _rows("2026-12")}
    assert M.main(["status"]) == M.EXIT_CAPTURED
    assert not [s for s in fake["conn"].sql if s[0].lstrip().upper().startswith("INSERT")]     # status writes nothing


def test_a_stream_switched_off_is_not_waited_for(fake, monkeypatch):
    import shared.config as cfg
    monkeypatch.setattr(cfg, "P3_HYSTERESIS_SHADOW_ENABLED", False)
    fake["conn"] = _Conn(drafts_this_month=1)
    fake["rows"] = {"P3_FEE_PENALTY_SHADOW": _rows("2026-12")}
    assert M.main(["status"]) == M.EXIT_CAPTURED


@pytest.mark.parametrize("rc, streams_done, want_status, want_exit", [
    (0, True, "OK", 0),
    (0, False, "INCOMPLETE", M.EXIT_NOT_CAPTURED),
    (2, False, "FAILED", 0),
])
def test_record_writes_one_row_with_the_outcome(fake, rc, streams_done, want_status, want_exit):
    fake["conn"] = _Conn(drafts_this_month=1)
    month = "2026-12" if streams_done else "2026-11"
    fake["rows"] = {"P3_FEE_PENALTY_SHADOW": _rows(month), "P3_HYSTERESIS_SHADOW": _rows(month)}
    assert M.main(["record", "--rc", str(rc)]) == want_exit
    inserts = [s for s in fake["conn"].sql if s[0].lstrip().upper().startswith("INSERT")]
    assert len(inserts) == 1 and fake["conn"].commits == 1
    step, status, message = inserts[0][1]
    assert (step, status) == ("P3_MONTHLY_DRAFT", want_status)
    body = json.loads(message)
    assert body["month"] == "2026-12" and body["build_rc"] == rc and body["v"] == M.ROW_VERSION


# ------------------------------------------------------------------------------------- the standing order: the schedule never promotes
_CHAIN_BATS = ("P3_monthlyDraft.bat", "P3_runCycle.bat", "P3_buildPortfolio.bat", "P3_generateReport.bat", "P2_audit.bat")
_CHAIN_PY = ("p3_monthly_draft.py", "p3_build_portfolio.py", "p3_generate_report.py")


@pytest.mark.parametrize("name", _CHAIN_BATS)
def test_no_launcher_of_the_scheduled_chain_invokes_the_promotion(name):
    code = [ln for ln in (LAUNCH / name).read_text(encoding="ascii", errors="replace").splitlines() if not ln.lstrip().startswith("::")]
    assert not [ln for ln in code if re.search(r"promote", ln, re.I)], f"{name} reaches the promotion"


@pytest.mark.parametrize("name", _CHAIN_PY)
def test_no_python_entry_of_the_scheduled_chain_calls_promote_or_imports_the_promoter(name):
    tree = ast.parse((LAUNCH / name).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            called = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            assert "promote" not in called.lower(), f"{name} calls {called}()"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
            assert not [n for n in names if "p3_promote_scenario" in n or n == "promote"], f"{name} imports the promoter"


def test_the_monthly_launcher_runs_the_draft_cycle_and_records_after_it():
    text = (LAUNCH / "P3_monthlyDraft.bat").read_text(encoding="ascii")
    status, cycle, record = (text.index("p3_monthly_draft.py status"), text.index('call "%LAUNCH%\\P3_runCycle.bat"'),
                             text.index("p3_monthly_draft.py record --rc"))
    assert status < cycle < record
    assert "--allow-stale" not in text, "the schedule never bypasses the P3 freshness gate"


# ------------------------------------------------------------------------------------------------------------- the scheduled task
REG = ROOT / "scripts" / "mnt" / "sched" / "register_p3_monthly_draft.ps1"


def test_the_registration_names_the_task_the_ticket_probes_and_runs_the_monthly_launcher():
    text = REG.read_text(encoding="utf-8")
    assert "'FondosP3MonthlyDraft'" in text                                    # task_ok condition of FND-0447
    assert "P3_monthlyDraft.bat" in text and "--allow-stale" not in text
    assert "C:\\desarrollo" not in text and "c:\\desarrollo" not in text.lower(), "the repo path is derived from the script's own location"
    assert "-Register" in text and "-Unregister" in text                       # a dry run by default
