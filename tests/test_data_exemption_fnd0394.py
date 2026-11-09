# tests/test_data_exemption_fnd0394.py
# -*- coding: utf-8 -*-
"""FND-0394: verified-genuine exemptions of the data-suspicion predicate (shared/data_exemption.py and its use in shared/data_suspect.py).

R-7: no pipeline, no DB (a stub connection feeds the scenario dates); synthetic panels (P#5: no fund is named in the logic; the shipped registry is checked as DATA). Pins the owner's rules: an exemption is scoped to named quantities
(never a whole fund), covers the fund family, never exempts a NAV-level flag, warns before it expires, exempts exactly one more build after expiry (grace, derived from dates), then lapses.
"""
import os
import sys
from datetime import date, timedelta

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np
import pandas as pd
import pytest

from shared import config
from shared.data_exemption import (EXEMPTION_MAX_DAYS, attention_lines, effective_exemptions, exemption_state, grace_isins, load_build_dates,
                                   validate_exemptions)
from shared.data_suspect import KIND_DEVIATION, KIND_INTEGRITY, KIND_NAV_JUMP, exempted_hits, find_suspects, load_effective_exemptions

ENTRY = {"ap": "FND-0403", "since": "2026-11-08", "until": "2027-05-07", "quantities": ["sortino|crisis_2022"],
         "reason": "a real +75.7% year (verified genuine)", "evidence": "FND-0403: three publishers agree with the stored NAV"}
UNTIL = date(2027, 5, 7)


# ---- the shipped registry -------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_shipped_registry_is_well_formed_and_holds_the_foundational_case():
    assert validate_exemptions(config.DATA_SUSPECT_EXEMPTIONS) == []
    e = config.DATA_SUSPECT_EXEMPTIONS["ES0116848013"]
    assert e["quantities"] == ["sortino|crisis_2022"] and e["ap"] == "FND-0403"
    assert effective_exemptions(config.DATA_SUSPECT_EXEMPTIONS, date(2026, 11, 9)) == {"ES0116848013": frozenset({"sortino|crisis_2022"})}


def test_the_selection_rule_stays_off_while_the_exemptions_exist():
    assert config.DATA_SUSPECT_ENABLED is False                                   # owner order 2026-11-09: off until the exemption system is tested AND the owner authorises it


@pytest.mark.parametrize("change, fragment", [
    ({"ap": "403"}, "ticket id"),
    ({"reason": "short"}, "reason"),
    ({"evidence": ""}, "evidence"),
    ({"quantities": []}, "quantities"),
    ({"quantities": ["sortino|crisis_2022|1|v1"]}, "quantities"),                # a stored key is not a quantity: the real variant is the same quantity
    ({"quantities": ["sortino"]}, "quantities"),
    ({"since": "tomorrow"}, "ISO dates"),
    ({"until": "2026-11-08"}, "after 'since'"),
    ({"until": "2027-12-31"}, f"at most {EXEMPTION_MAX_DAYS} days"),
])
def test_a_malformed_entry_is_reported(change, fragment):
    assert any(fragment in p for p in validate_exemptions({"ES0116848013": {**ENTRY, **change}}))


def test_a_malformed_isin_is_reported():
    assert any("is not an ISIN" in p for p in validate_exemptions({"XX": ENTRY}))


# ---- the expiry guard -----------------------------------------------------------------------------------------------------------------------------------------------

def test_the_timeline_active_expiring_grace_then_lapsed():
    assert exemption_state(ENTRY, date(2026, 11, 7)) == "NOT_STARTED"
    assert exemption_state(ENTRY, date(2026, 11, 8)) == "ACTIVE"
    assert exemption_state(ENTRY, UNTIL - timedelta(days=8)) == "ACTIVE"
    assert exemption_state(ENTRY, UNTIL - timedelta(days=7)) == "EXPIRING"      # warned at T-7
    assert exemption_state(ENTRY, UNTIL) == "EXPIRING"                          # still exempt on the expiry day itself
    assert exemption_state(ENTRY, UNTIL + timedelta(days=1)) == "GRACE"         # no build in between: the build running now is the grace build


def test_grace_is_exactly_one_build_derived_from_the_scenario_dates():
    builds = [date(2027, 3, 1), date(2027, 5, 10)]                               # the first build AFTER the expiry is on 2027-05-10
    assert exemption_state(ENTRY, date(2027, 5, 10), builds) == "GRACE"          # that build itself: eligible and flagged
    assert exemption_state(ENTRY, date(2027, 5, 10), builds + [date(2027, 5, 10)]) == "GRACE"   # a rerun the same day is still the same build
    assert exemption_state(ENTRY, date(2027, 5, 11), builds) == "LAPSED"         # the next build: the quantities count as suspect again
    assert exemption_state(ENTRY, date(2027, 6, 30), builds) == "LAPSED"
    assert exemption_state(ENTRY, date(2027, 5, 9), [UNTIL]) == "GRACE"          # a build ON the expiry day is not a build after it


def test_effective_exemptions_cover_active_expiring_and_grace_only():
    reg = {"AAA": {**ENTRY}}
    assert "AAA" in effective_exemptions(reg, date(2027, 1, 1))
    assert "AAA" in effective_exemptions(reg, UNTIL)
    assert "AAA" in effective_exemptions(reg, UNTIL + timedelta(days=3))
    assert effective_exemptions(reg, UNTIL + timedelta(days=3), [UNTIL + timedelta(days=1)]) == {}
    assert effective_exemptions(reg, date(2026, 1, 1)) == {}


def test_grace_is_flagged_for_the_notes_and_the_kpis():
    reg = {"ES0116848013": ENTRY}
    assert grace_isins(reg, UNTIL + timedelta(days=2)).keys() == {"ES0116848013"}
    assert "exemption_grace" in grace_isins(reg, UNTIL + timedelta(days=2))["ES0116848013"]
    assert grace_isins(reg, date(2027, 1, 1)) == {}


def test_every_state_that_needs_attention_prints_a_line_and_a_quiet_one_prints_nothing():
    reg = {"ES0116848013": ENTRY}
    assert attention_lines(reg, date(2027, 1, 1)) == []
    assert "expires on 2027-05-07 (3 days)" in attention_lines(reg, UNTIL - timedelta(days=3))[0]
    assert "EXPIRED on 2027-05-07" in attention_lines(reg, UNTIL + timedelta(days=1))[0] and "THIS build only" in attention_lines(reg, UNTIL + timedelta(days=1))[0]
    assert "LAPSED" in attention_lines(reg, date(2027, 6, 1), [date(2027, 5, 9)])[0]
    assert "cannot be read" in attention_lines({"X": {**ENTRY, "until": "never"}}, date(2027, 1, 1))[0]


class _Stub:
    autocommit = True                                               # fail_soft_block is a straight pass-through in autocommit

    def __init__(self, rows=None, boom=False):
        self.rows, self.boom = rows or [], boom

    def execute(self, sql):
        if self.boom:
            raise RuntimeError("no table")
        return self

    def fetchall(self):
        return self.rows


def test_build_dates_come_from_the_scenarios_and_a_failed_read_keeps_the_grace_instead_of_lapsing():
    assert load_build_dates(_Stub([(date(2026, 10, 1),), (None,), (date(2026, 11, 1),)])) == [date(2026, 10, 1), date(2026, 11, 1)]
    assert load_build_dates(_Stub(boom=True)) == []
    assert exemption_state(ENTRY, UNTIL + timedelta(days=30), load_build_dates(_Stub(boom=True))) == "GRACE"


def test_load_effective_exemptions_reads_the_registry_and_the_scenario_dates(monkeypatch):
    monkeypatch.setattr(config, "DATA_SUSPECT_EXEMPTIONS", {"AAA": ENTRY})
    assert load_effective_exemptions(_Stub([(date(2026, 11, 1),)]), date(2026, 12, 1)) == {"AAA": {"sortino|crisis_2022"}}
    assert load_effective_exemptions(_Stub([(date(2027, 5, 8),)]), date(2027, 5, 9)) == {}


# ---- the predicate -------------------------------------------------------------------------------------------------------------------------------------------------

N = 60
KEYS = ["a|h|0|v1", "a|h|1|v1", "b|h|0|v1", "b|h|1|v1"]


def _panel(extremes):
    """60 healthy funds x four keys (quantities `a|h` and `b|h`, nominal and real); `extremes` = {isin: [keys 80 modified-z away]}; every other value of those funds is ordinary."""
    rng = np.random.default_rng(9)
    rows = []
    for k in KEYS:
        vals = rng.normal(0, 1, N)
        med, mad = float(np.median(vals)), float(np.median(np.abs(vals - np.median(vals))))
        rows += [(f"H{i:03d}", "N1", k, float(v), 100) for i, v in enumerate(vals)]
        for isin, ks in extremes.items():
            rows.append((isin, "N1", k, med + 80.0 / 0.6745 * mad if k in ks else float(rng.normal(0, 1)), 100))
    return pd.DataFrame(rows, columns=["isin", "nature", "metric", "value", "source_rows"])


BOTH_A = ["a|h|0|v1", "a|h|1|v1"]
BOTH_AB = BOTH_A + ["b|h|0|v1", "b|h|1|v1"]


def test_without_an_exemption_the_fund_is_a_suspect():
    s = find_suspects(_panel({"X": BOTH_A}), min_metrics=2)
    assert [x.representative for x in s] == ["X"] and s[0].kinds == (KIND_DEVIATION,)


def test_the_exempt_quantity_stops_counting_and_the_fund_is_no_longer_a_suspect():
    assert find_suspects(_panel({"X": BOTH_A}), min_metrics=2, exemptions={"X": {"a|h"}}) == []


def test_the_exemption_is_scoped_to_its_quantities_a_second_extreme_quantity_still_flags_the_fund():
    s = find_suspects(_panel({"X": BOTH_AB}), min_metrics=2, exemptions={"X": {"a|h"}})
    assert [x.representative for x in s] == ["X"] and s[0].n_metrics == 1 and KIND_INTEGRITY not in s[0].kinds     # one quantity left: a deviation
    assert find_suspects(_panel({"X": BOTH_AB}), min_metrics=2, exemptions={"X": {"a|h", "b|h"}}) == []


def test_an_exemption_of_another_quantity_or_another_fund_changes_nothing():
    assert len(find_suspects(_panel({"X": BOTH_A}), min_metrics=2, exemptions={"X": {"b|h"}})) == 1
    assert len(find_suspects(_panel({"X": BOTH_A}), min_metrics=2, exemptions={"Y": {"a|h"}})) == 1


def test_the_exemption_covers_the_share_classes_of_the_family_and_not_other_families():
    fam = {"X": "F1", "Y": "F1", "Z": "F2"}
    panel = _panel({"X": BOTH_A, "Y": BOTH_A, "Z": BOTH_A})
    s = find_suspects(panel, family_of=fam, min_metrics=2, exemptions={"X": {"a|h"}})
    assert [x.family for x in s] == ["F2"]                                         # Y shares X's family: covered; Z does not


def test_a_nav_level_flag_is_never_exempted():
    s = find_suspects(_panel({"X": BOTH_A}), nav_flags={"X": "one-month jump no peer shared"}, min_metrics=2, exemptions={"X": {"a|h"}})
    assert [x.representative for x in s] == ["X"] and s[0].kinds == (KIND_NAV_JUMP,)


def test_every_withdrawn_row_stays_visible():
    out = exempted_hits(_panel({"X": BOTH_AB}), {"X": {"a|h"}})
    assert sorted(out["metric"]) == sorted(BOTH_A) and set(out["isin"]) == {"X"} and set(out["quantity"]) == {"a|h"}
    assert exempted_hits(_panel({"X": BOTH_A}), {}).empty


def test_no_exemption_argument_means_the_behaviour_before_the_ticket():
    panel = _panel({"X": BOTH_AB})
    assert [(s.representative, s.n_metrics) for s in find_suspects(panel, min_metrics=2)] == [("X", 2)]
