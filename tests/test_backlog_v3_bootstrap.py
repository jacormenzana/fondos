# tests/test_backlog_v3_bootstrap.py
# -*- coding: utf-8 -*-
"""Backlog v3 bootstrap (O-V3-1): the manifest and the plan / apply logic of scripts/mig/backlog_v3_bootstrap.py. No database, no network: the client is a fake (R-7)."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "mig")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytest.importorskip("yaml")
import backlog_v3_bootstrap as bs  # noqa: E402
from shared import backlog_client as real  # noqa: E402

HUMAN, AGENT = "human:j.cormenzana_owner", "agent:claude-sonnet-5-5"


@pytest.fixture(scope="module")
def data():
    return bs.load_manifest()


def test_the_manifest_has_the_epic_and_the_seven_workstreams_with_unique_keys_and_titles(data):
    keys = [t["key"] for t in data["tickets"]]
    assert keys == ["EPIC", "W1", "W2", "W3", "W4", "W4B", "W5", "W6"]
    assert len({t["title"] for t in data["tickets"]}) == 8


def test_every_workstream_is_split_from_the_epic_and_the_depends_on_order_is_acyclic_with_w2_first(data):
    split = {ln["from"] for ln in data["links"] if ln["type"] == "SPLIT_FROM" and ln["to"] == "EPIC"}
    assert split == {"W1", "W2", "W3", "W4", "W4B", "W5", "W6"}
    deps = {}
    for ln in data["links"]:
        if ln["type"] == "DEPENDS_ON":
            deps.setdefault(ln["from"], set()).add(ln["to"])
    assert "W2" not in deps and "W1" not in deps                      # nothing precedes W2 (the first to build) and W1 is already delivered

    def reaches(a, b, seen=()):
        return any(n == b or (n not in seen and reaches(n, b, seen + (n,))) for n in deps.get(a, ()))
    assert not [k for k in deps if reaches(k, k)], "a DEPENDS_ON cycle"
    assert {"W3", "W4", "W4B", "W5"} <= {k for k, v in deps.items() if "W2" in v}


def test_existing_tickets_named_in_the_texts_are_exactly_the_ones_checked_by_the_overlap_discovery(data):
    """open_ticket() proposes a RELATED link for every FND-nnnn named in a text: pin the set so a typo cannot link the wrong ticket."""
    named = set(re.findall(r"FND-\d{4}", " ".join(t["description"] + " " + t["first_log"] for t in data["tickets"])))
    assert named == {"FND-0264", "FND-0331", "FND-0334", "FND-0350", "FND-0351"}


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["tickets"][1].update(title=d["tickets"][2]["title"]), "duplicate ticket titles"),
    (lambda d: d["tickets"][1].update(nature="FEATURE"), "nature"),
    (lambda d: d["tickets"][1].update(priority="URGENT"), "priority"),
    (lambda d: d["tickets"][1].update(first_log=" "), "missing"),
    (lambda d: d["links"][0].update(to="W9"), "neither a key"),
    (lambda d: d["links"][0].update(type="BLOCKS"), "not allowed"),
    (lambda d: d["links"][0].update(evidence=""), "evidence"),
])
def test_a_bad_manifest_is_refused_before_anything_is_planned(tmp_path, data, mutate, message):
    import copy
    import yaml
    bad = copy.deepcopy(data)
    mutate(bad)
    path = tmp_path / "m.yaml"
    path.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        bs.load_manifest(path)


def test_confirmed_is_a_persons_decision_so_any_other_actor_records_proposed():
    assert bs.effective_status("CONFIRMED", HUMAN) == "CONFIRMED"
    assert bs.effective_status("CONFIRMED", AGENT) == "PROPOSED" and bs.effective_status("CONFIRMED", "system:sync") == "PROPOSED"
    assert bs.effective_status("PROPOSED", HUMAN) == "PROPOSED"


def _existing(data, first=100):
    return {t["title"]: f"FND-{first + i:04d}" for i, t in enumerate(data["tickets"])}


def test_on_an_empty_backlog_everything_is_to_do(data):
    plan = bs.build_plan(data, {}, {}, HUMAN)
    assert [s for _, s, _ in plan["tickets"]] == ["TODO"] * 8 and [s for _, s, _ in plan["links"]] == ["TODO"] * len(data["links"])


def test_a_second_run_changes_nothing(data):
    ids = _existing(data)
    key_id = {t["key"]: ids[t["title"]] for t in data["tickets"]}
    done = {(key_id[ln["from"]], key_id[ln["to"]], ln["type"]): ln["status"] for ln in data["links"]}
    plan = bs.build_plan(data, ids, done, HUMAN)
    assert all(s == "DONE" for _, s, _ in plan["tickets"]) and all(s == "DONE" for _, s, _ in plan["links"])


def test_a_rejected_link_is_never_overridden_and_a_stronger_state_is_not_downgraded(data):
    ids = _existing(data)
    key_id = {t["key"]: ids[t["title"]] for t in data["tickets"]}
    first, second = data["links"][0], data["links"][1]
    current = {(key_id[first["from"]], key_id[first["to"]], first["type"]): "REJECTED",
               (key_id[second["from"]], key_id[second["to"]], second["type"]): "CONFIRMED"}
    plan = bs.build_plan(data, ids, current, AGENT)                       # an agent would only PROPOSE
    states = {(ln["from"], ln["to"], ln["type"]): s for ln, s, _ in plan["links"]}
    assert states[(first["from"], first["to"], first["type"])].startswith("SKIP")
    assert states[(second["from"], second["to"], second["type"])] == "DONE"


def test_a_proposed_link_is_confirmed_when_a_person_runs_it(data):
    ids = _existing(data)
    key_id = {t["key"]: ids[t["title"]] for t in data["tickets"]}
    ln = data["links"][0]
    plan = bs.build_plan(data, ids, {(key_id[ln["from"]], key_id[ln["to"]], ln["type"]): "PROPOSED"}, HUMAN)
    assert next(s for row, s, _ in plan["links"] if row is ln) == "TODO"


class FakeClient:
    """The write helpers of backlog_client, recording their calls."""

    def __init__(self, fail_titles=()):
        self.opened, self.links, self.fail_titles, self.n = [], [], set(fail_titles), 100
        self.make_session_id = real.make_session_id

    def open_ticket(self, **kw):
        if kw["title"] in self.fail_titles:
            raise RuntimeError("refused\nby the database")
        self.n += 1
        self.opened.append(kw)
        return real.TicketOpened(f"FND-{self.n:04d}")

    def record_link_event(self, **kw):
        self.links.append(kw)
        return len(self.links)


def test_apply_opens_in_manifest_order_with_a_valid_session_and_links_the_real_ids(data):
    fake = FakeClient()
    plan = bs.build_plan(data, {}, {}, HUMAN)
    out = bs.apply_plan(data, plan, fake, actor=HUMAN)
    assert [o["title"] for o in fake.opened] == [t["title"] for t in data["tickets"]] and not out["failed"] and out["links"] == len(data["links"])
    assert all(re.match(r"^FND_\d{8}_DEV_BACKLOG-V3-BOOTSTRAP$", o["session_id"]) and o["actor"] == HUMAN for o in fake.opened)
    assert all("\n" not in o["description"] and "  " not in o["first_log"] for o in fake.opened)          # the folded YAML text is one clean line
    epic_id = out["opened"]["EPIC"]
    assert {(k["from_id"], k["to_id"]) for k in fake.links if k["link_type"] == "SPLIT_FROM"} == {(out["opened"][w], epic_id) for w in ("W1", "W2", "W3", "W4", "W4B", "W5", "W6")}
    assert {k["status"] for k in fake.links} == {"CONFIRMED"} and all(k["username"] == HUMAN for k in fake.links)


def test_a_refused_ticket_skips_its_links_and_the_others_still_go(data, capsys):
    w3 = next(t["title"] for t in data["tickets"] if t["key"] == "W3")
    fake = FakeClient(fail_titles={w3})
    out = bs.apply_plan(data, bs.build_plan(data, {}, {}, HUMAN), fake, actor=HUMAN)
    assert out["failed"] == ["W3"] and len(fake.opened) == 7
    assert all(w3 not in str(k) for k in fake.links) and out["links"] == sum(1 for ln in data["links"] if "W3" not in (ln["from"], ln["to"]))
    assert "SKIPPED (an endpoint was not opened)" in capsys.readouterr().err


def test_an_agent_run_records_every_link_as_proposed(data):
    fake = FakeClient()
    bs.apply_plan(data, bs.build_plan(data, {}, {}, AGENT), fake, actor=AGENT)
    assert {k["status"] for k in fake.links} == {"PROPOSED"}


def test_resuming_after_a_partial_run_opens_only_what_is_missing(data):
    ids = _existing(data)
    missing = next(t for t in data["tickets"] if t["key"] == "W5")
    partial = {t: i for t, i in ids.items() if t != missing["title"]}
    fake = FakeClient()
    out = bs.apply_plan(data, bs.build_plan(data, partial, {}, HUMAN), fake, actor=HUMAN)
    assert [o["title"] for o in fake.opened] == [missing["title"]] and set(out["opened"]) == {"W5"}
