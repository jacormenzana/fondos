# proyecto3/tests/test_data_hold_fnd0403.py
# -*- coding: utf-8 -*-
"""FND-0403: the temporary, ticket-bound P3 data hold (shared/data_hold.py) and its effect in the scorer's hard filters. Pure parts (R-7: no DB).

A fund under human review is ineligible while its hold is active, the hold lapses by itself, an absent column (point-in-time frames) never excludes, and the registry shipped in shared/config.py is well formed.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import shared.config as cfg  # noqa: E402
from proyecto3.src import fund_scorer as fs  # noqa: E402
from shared.data_hold import HOLD_MAX_DAYS, hold_reasons, validate_holds  # noqa: E402

GOOD = {"ES0116848013": {"ap": "FND-0403", "since": "2026-11-08", "until": "2027-02-06", "reason": "to be cross-checked with the manager's documents"}}


def _row(**kw):
    base = {"max_dd": -0.05, "return_ann_real": 0.03, "Fund_Nature": "Mixtos", "Credit_Quality": "Investment Grade"}
    base.update(kw)
    return pd.Series(base)


def test_the_shipped_registry_is_well_formed():
    assert validate_holds(cfg.P3_DATA_HOLDS) == []                               # whatever it holds must be valid (ticket, dates within 90 days, a reason)
    assert cfg.P3_DATA_HOLD_ENABLED is True
    assert "ES0116848013" not in cfg.P3_DATA_HOLDS                               # the first hold (FND-0403) was lifted on 2026-11-08 once the verdict was GENUINE
    assert "ES0116848013" not in hold_reasons(cfg.P3_DATA_HOLDS, date(2026, 11, 9))


def test_the_second_hold_names_its_ticket_and_acts_from_the_day_it_was_ordered():
    h = cfg.P3_DATA_HOLDS["IE00B3L10570"]
    assert h["ap"] == "FND-0404" and h["since"] == "2026-11-09"
    assert "IE00B3L10570" not in hold_reasons(cfg.P3_DATA_HOLDS, date(2026, 11, 8))
    assert hold_reasons(cfg.P3_DATA_HOLDS, date(2026, 11, 9))["IE00B3L10570"].startswith("data_hold: FND-0404 until 2027-02-07 (")


def test_a_hold_is_active_between_since_and_until_inclusive_and_lapses_by_itself():
    assert "ES0116848013" not in hold_reasons(GOOD, date(2026, 11, 7))          # not started
    assert "ES0116848013" in hold_reasons(GOOD, date(2026, 11, 8)) and "ES0116848013" in hold_reasons(GOOD, date(2027, 2, 6))
    assert hold_reasons(GOOD, date(2027, 2, 7)) == {}                            # expired: the fund comes back without anyone remembering


def test_the_reason_text_names_the_ticket_and_the_date():
    text = hold_reasons(GOOD, date(2026, 12, 1))["ES0116848013"]
    assert text.startswith("data_hold: FND-0403 until 2027-02-06 (") and "manager's documents" in text


@pytest.mark.parametrize("bad, fragment", [
    ({"XX": {**GOOD["ES0116848013"]}}, "is not an ISIN"),
    ({"ES0116848013": {**GOOD["ES0116848013"], "ap": "403"}}, "ticket id"),
    ({"ES0116848013": {**GOOD["ES0116848013"], "reason": "short"}}, "reason"),
    ({"ES0116848013": {**GOOD["ES0116848013"], "until": "2026-11-08"}}, "after 'since'"),
    ({"ES0116848013": {**GOOD["ES0116848013"], "until": "2027-06-30"}}, f"at most {HOLD_MAX_DAYS} days"),
    ({"ES0116848013": {**GOOD["ES0116848013"], "since": "yesterday"}}, "ISO dates"),
])
def test_a_malformed_hold_is_reported_by_the_validator(bad, fragment):
    assert any(fragment in p for p in validate_holds(bad))


def test_an_entry_with_unreadable_dates_never_holds_but_a_wrong_ticket_text_still_does():
    garbled = {"ES0116848013": {**GOOD["ES0116848013"], "since": "yesterday"}}
    assert hold_reasons(garbled, date(2026, 12, 1)) == {}                          # no readable window: nothing to apply (the validator, run by the registry test, rejects it)
    sloppy = {"ES0116848013": {**GOOD["ES0116848013"], "ap": "403"}}
    assert "ES0116848013" in hold_reasons(sloppy, date(2026, 12, 1))               # a formatting slip must not release a fund under review


def test_the_scorer_excludes_a_held_fund_with_the_reason_and_lets_every_other_fund_through(monkeypatch):
    monkeypatch.setattr(cfg, "P3_DATA_HOLD_ENABLED", True)
    reason = hold_reasons(GOOD, date(2026, 12, 1))["ES0116848013"]
    assert fs.check_hard_filters(_row(data_hold_reason=reason), "Dinamica") == reason
    assert fs.check_hard_filters(_row(data_hold_reason=None), "Dinamica") is None
    assert fs.check_hard_filters(_row(), "Dinamica") is None                      # the column is absent (point-in-time frame): fail-open


def test_the_hold_works_without_the_data_eligibility_switch_and_the_kill_switch_turns_it_off(monkeypatch):
    reason = hold_reasons(GOOD, date(2026, 12, 1))["ES0116848013"]
    monkeypatch.setattr(cfg, "FUND_DATA_ELIGIBILITY_ENABLED", False)
    monkeypatch.setattr(cfg, "P3_DATA_HOLD_ENABLED", True)
    assert fs.check_hard_filters(_row(data_hold_reason=reason), "Equilibrada") == reason
    monkeypatch.setattr(cfg, "P3_DATA_HOLD_ENABLED", False)
    assert fs.check_hard_filters(_row(data_hold_reason=reason), "Equilibrada") is None
