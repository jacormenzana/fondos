"""FND-0448: the scheduled monthly P1+P2 cycle (the NAV refresh of the monthly DRAFT-only build). The window and freshness rule and the record
(scripts/launch/p1p2_monthly_refresh.py), and the limits of the owner's standing order of 2026-11-26 (ingestion and calculation only): the chain never
promotes, never syncs or retires KIIDs. FND-0451: a cycle that loaded NAV and died before the P2 calculation is retried. FND-0452: while this month has no DRAFT the refresh also acts outside
the window. No database: fake connections only (R-7)."""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "launch"))
import p1p2_monthly_refresh as M  # noqa: E402

LAUNCH = ROOT / "scripts" / "launch"
LIMITS = {"nav_p10": 14, "harvest": 45, "macro_market": 45, "macro_release": 100}


# --------------------------------------------------------------------------------------------------------------------------------------- pure
@pytest.mark.parametrize("day, want", [(1, False), (24, False), (25, True), (28, True), (30, True)])
def test_the_window_opens_on_day_25_and_runs_to_the_end_of_the_month(day, want):
    assert M.in_window(date(2026, 11, day)) is want


@pytest.mark.parametrize("today, want", [
    (date(2026, 11, 26), date(2026, 12, 4)),
    (date(2026, 12, 31), date(2027, 1, 4)),           # year end
    (date(2027, 1, 25), date(2027, 2, 4)),
    (date(2027, 2, 28), date(2027, 3, 4)),
])
def test_the_judge_day_is_the_last_retry_day_of_the_next_build(today, want):
    assert M.judge_day(today) == want


def test_the_judge_day_follows_the_build_retry_days():
    assert M.judge_day(date(2026, 11, 26)).day == 1 + M.BUILD_RETRY_DAYS


def test_stale_reasons_use_the_gate_limits_on_the_judge_day():
    on = date(2026, 12, 4)
    assert M.stale_reasons({"nav_p10": date(2026, 11, 20), "harvest": date(2026, 11, 21)}, LIMITS, on) == []        # age 14 = the limit passes
    got = M.stale_reasons({"nav_p10": date(2026, 11, 19), "harvest": date(2026, 11, 21)}, LIMITS, on)
    assert len(got) == 1 and got[0].startswith("nav_p10")                                                            # age 15 > 14
    got = M.stale_reasons({"nav_p10": date(2026, 11, 25), "harvest": date(2026, 10, 19)}, LIMITS, on)
    assert len(got) == 1 and got[0].startswith("harvest")                                                            # age 46 > 45


def test_a_missing_date_is_stale():
    assert len(M.stale_reasons({}, LIMITS, date(2026, 12, 4))) == 2


@pytest.mark.parametrize("rc, reasons, want", [
    (0, [], ("OK", 0)),
    (0, ["nav_p10 ..."], ("STALE", M.EXIT_STILL_STALE)),
    (3, [], ("FAILED", 0)),
    (107, ["nav_p10 ..."], ("FAILED", 0)),
])
def test_outcome(rc, reasons, want):
    assert M.outcome(rc, reasons) == want


def test_the_helpers_codes_stay_in_the_tool_range_and_apart():
    codes = (M.EXIT_NOTHING_TO_DO, M.EXIT_STILL_STALE)
    assert len(set(codes)) == 2 and all(1 <= c <= 99 for c in codes)
    import p3_monthly_draft as D
    assert M.EXIT_STILL_STALE not in (D.EXIT_CAPTURED, D.EXIT_NOT_CAPTURED)


def test_the_limits_are_the_p3_gates_own():
    from shared.config import P3_FRESHNESS_MAX_AGE_DAYS
    assert all(name in P3_FRESHNESS_MAX_AGE_DAYS for name in M.CHECKS)


def test_newest_dates_uses_the_gates_own_percentile(monkeypatch):
    from proyecto3.src import data_freshness as F
    navs = [date(2026, 11, d) for d in range(1, 21)]                  # p10 of these is the gate's quantile, not a second copy
    monkeypatch.setattr(F, "load_freshness_inputs", lambda conn: (navs, "20261121_101500", None))
    got = M.newest_dates(object(), date(2026, 11, 26))
    from shared.config import P3_NAV_UNIVERSE_PERCENTILE
    want = F.evaluate_freshness(navs, {}, "20261121_101500", date(2026, 11, 26), LIMITS, P3_NAV_UNIVERSE_PERCENTILE, ())
    assert got["nav_p10"] == next(c.newest for c in want if c.name == "nav_p10")
    assert got["harvest"] == date(2026, 11, 21)


# ---------------------------------------------------------------------------------------------------------------------------- main, faked
class _Conn:
    def __init__(self): self.sql, self.commits, self.closed = [], 0, False
    def execute(self, sql, params=()): self.sql.append((sql, params))
    def commit(self): self.commits += 1
    def close(self): self.closed = True


def _t(hour, day=20):
    return datetime(2026, 11, day, hour, 0, tzinfo=timezone.utc)


@pytest.fixture()
def fake(monkeypatch):
    state = {"today": date(2026, 11, 26), "newest": {}, "conn": _Conn(), "captured": True, "stamps": (_t(8), _t(21))}
    monkeypatch.setattr(M, "newest_dates", lambda conn, today: state["newest"])
    monkeypatch.setattr(M, "draft_captured", lambda conn, now: state["captured"])
    monkeypatch.setattr(M, "p2_stamps", lambda conn: state["stamps"])
    import shared.db
    monkeypatch.setattr(shared.db, "get_connection", lambda: state["conn"])

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            d = state["today"]
            return datetime(d.year, d.month, d.day, 12, 0, tzinfo=timezone.utc)

        def astimezone(self, tz=None):
            return self
    monkeypatch.setattr(M, "datetime", _Frozen)
    return state


def test_status_outside_the_window_with_the_month_captured_has_nothing_to_do(fake):
    fake["today"] = date(2026, 11, 10)
    fake["newest"] = {}                                   # even with no data at all
    assert M.main(["status"]) == M.EXIT_NOTHING_TO_DO
    assert fake["conn"].closed and not fake["conn"].sql


def test_status_in_the_window_with_stale_nav_says_run(fake):
    fake["newest"] = {"nav_p10": date(2026, 11, 19), "harvest": date(2026, 11, 21)}
    assert M.main(["status"]) == 0
    assert fake["conn"].closed and not fake["conn"].sql


def test_status_in_the_window_with_fresh_data_says_nothing_to_do(fake):
    fake["newest"] = {"nav_p10": date(2026, 11, 24), "harvest": date(2026, 11, 25)}
    assert M.main(["status"]) == M.EXIT_NOTHING_TO_DO


@pytest.mark.parametrize("rc, nav, want_status, want_exit", [
    (0, date(2026, 11, 24), "OK", 0),
    (0, date(2026, 11, 19), "STALE", M.EXIT_STILL_STALE),
    (104, date(2026, 11, 19), "FAILED", 0),
])
def test_record_writes_one_row_with_the_outcome(fake, rc, nav, want_status, want_exit):
    fake["newest"] = {"nav_p10": nav, "harvest": date(2026, 11, 25)}
    assert M.main(["record", "--rc", str(rc)]) == want_exit
    (sql, params), = fake["conn"].sql
    assert sql.lstrip().upper().startswith("INSERT INTO INGESTION_LOG") and fake["conn"].commits == 1
    step, status, message = params
    assert (step, status) == ("P1P2_MONTHLY_REFRESH", want_status)
    body = json.loads(message)
    assert body["cycle_rc"] == rc and body["judge_day"] == "2026-12-04" and body["v"] == M.ROW_VERSION


# ------------------------------------------------------------------------- the standing order: ingestion and calculation only
def _code(name: str) -> list:
    return [ln for ln in (LAUNCH / name).read_text(encoding="ascii", errors="replace").splitlines() if not ln.lstrip().startswith("::")]


def test_the_monthly_refresh_never_syncs_or_retires_kiids_and_never_promotes():
    code = "\n".join(_code("P1P2_monthlyRefresh.bat"))
    for forbidden in ("--harvest-sync", "--harvest-retire", "promote", "--from-any", "--force"):
        assert forbidden not in code, forbidden


def test_the_monthly_refresh_runs_the_p1p2_cycle_with_its_defaults_and_records_after_it():
    text = (LAUNCH / "P1P2_monthlyRefresh.bat").read_text(encoding="ascii")
    calls = list(re.finditer(r'^call "%LAUNCH%\\P1P2_runCycle\.bat"\s*$', text, re.M))
    assert len(calls) == 1, "the cycle is called once, with its defaults (no argument)"
    status, record = text.index("p1p2_monthly_refresh.py status"), text.index("p1p2_monthly_refresh.py record --rc")
    assert status < calls[0].start() < record


def test_the_p1p2_cycle_does_not_sync_or_retire_kiids_by_default():
    """The harvest's --sync / --retire-orphans reach the harvest only behind the cycle's explicit flags, which the monthly refresh never passes."""
    code = _code("P1P2_runCycle.bat")
    sets = [ln.strip() for ln in code if re.search(r'set "HARVEST_ARGS=.*(--sync|--retire-orphans)', ln)]
    assert sets and all(re.match(r"if defined FLAG_harvest_(sync|retire) set ", ln) for ln in sets), sets


REG = ROOT / "scripts" / "mnt" / "sched" / "register_monthly_cycle_tasks.ps1"


def test_the_registration_names_the_task_the_ticket_probes_and_runs_the_monthly_refresh():
    text = REG.read_text(encoding="utf-8")
    assert "Name = 'FondosP1P2MonthlyRefresh'; Bat = 'P1P2_monthlyRefresh.bat'" in text     # task_ok condition of FND-0448
    assert "c:\\desarrollo" not in text.lower(), "the repo path is derived from the script's own location"


# ------------------------------------------------------------------------- FND-0452: this month's build is still missing
@pytest.mark.parametrize("today, captured, want", [
    (date(2026, 12, 25), True, date(2027, 1, 4)),           # in the window: the next build, whatever this month's state
    (date(2026, 12, 25), False, date(2027, 1, 4)),
    (date(2027, 1, 7), True, None),                         # outside, captured
    (date(2027, 1, 7), False, date(2027, 1, 8)),            # outside, missing: tomorrow morning's build must pass
    (date(2027, 1, 24), False, date(2027, 1, 25)),
])
def test_the_day_the_data_must_still_pass_on(today, captured, want):
    assert M.plan_day(today, captured) == want


@pytest.mark.parametrize("today, nav, want", [
    (date(2027, 1, 2), date(2026, 12, 23), M.EXIT_NOTHING_TO_DO),    # tomorrow's build still accepts the data
    (date(2027, 1, 7), date(2026, 12, 23), 0),                       # age 16 on the 8th: refresh instead of waiting for the 25th
])
def test_a_missed_january_build_is_refreshed_outside_the_window(fake, today, nav, want):
    fake["today"], fake["captured"] = today, False
    fake["newest"] = {"nav_p10": nav, "harvest": date(2026, 12, 26)}
    assert M.main(["status"]) == want


# ------------------------------------------------------------------------- FND-0451: NAV loaded, P2 calculation not completed
@pytest.mark.parametrize("nav_write, full_run, stale", [
    (_t(8), _t(21), False),
    (_t(21), _t(8), True),                 # NAV written after the newest full run: the calculation did not complete
    (_t(8), None, True),                   # no full run on record
    (None, _t(8), True),                   # no NAV at all
])
def test_a_p2_run_must_follow_the_newest_nav_write(nav_write, full_run, stale):
    got = M.p2_reasons(nav_write, full_run)
    assert bool(got) is stale and all(r.startswith("p2") for r in got)


def test_fresh_dates_with_an_unfinished_calculation_run_the_cycle_again(fake):
    fake["newest"] = {"nav_p10": date(2026, 11, 24), "harvest": date(2026, 11, 25)}
    fake["stamps"] = (_t(21), _t(8))
    assert M.main(["status"]) == 0


def test_a_cycle_that_ends_0_without_a_full_p2_run_is_recorded_stale(fake):
    fake["newest"] = {"nav_p10": date(2026, 11, 24), "harvest": date(2026, 11, 25)}
    fake["stamps"] = (_t(21), None)
    assert M.main(["record", "--rc", "0"]) == M.EXIT_STILL_STALE


def test_a_cycle_that_crosses_midnight_into_the_new_month_is_judged_for_that_months_build(fake):
    fake["today"], fake["captured"] = date(2027, 1, 1), False
    fake["newest"] = {"nav_p10": date(2026, 12, 30), "harvest": date(2026, 12, 31)}
    assert M.main(["record", "--rc", "0"]) == 0
    assert json.loads(fake["conn"].sql[0][1][2])["judge_day"] == "2027-01-02"


# ------------------------------------------------------------------------- the stamps
def test_a_full_run_is_one_that_processed_the_active_universe():
    class _C:
        def __init__(self): self.calls = []

        def execute(self, sql, params=()):
            self.calls.append((sql, params))
            value = _t(8) if "ingested_at" in sql else (2000 if "COUNT(DISTINCT" in sql else _t(21))
            return type("Cur", (), {"fetchone": lambda s: (value,)})()

    from shared.config import P3_MIN_UNIFORM_METRICS_SHARE
    c = _C()
    assert M.p2_stamps(c) == (_t(8), _t(21))
    (sql, params) = c.calls[-1]
    assert "RUN_SUMMARY" in sql and params == (math.ceil(2000 * P3_MIN_UNIFORM_METRICS_SHARE),)       # a 40-fund sample run is not a full run
