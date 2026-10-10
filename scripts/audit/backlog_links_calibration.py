# -*- coding: utf-8 -*-
"""
Calibration of the automatic-incident overlap policy (owner directive D-1; NORMAS_BACKLOG.md §7).

Before `BACKLOG_INCIDENT_AUTO_APPEND_ENABLED` is switched on, the high-confidence tier of
`shared/backlog_overlap.classify_incident` must make ZERO false merges on:
  (a) the historical replay -- every ticket of the backlog treated as an incident arriving at its own creation_ts, against the
      tickets that were open at that moment. A HIGH is a false merge unless the pair is a known positive (the ticket was closed
      as DUPLICATED naming that target, or a CONFIRMED DUPLICATES link joins them). The history holds almost no auto-detected
      reincidences, so this set measures how often the policy would have merged two DIFFERENT tickets;
  (b) the synthetic fixture tests/fixtures/backlog_incident_cases.json -- hard positives and hard negatives (same module /
      different defect, same defect / different wording, launcher RC texts, truncated tracebacks).

    python scripts/audit/backlog_links_calibration.py            # report (read-only)
    python scripts/audit/backlog_links_calibration.py --gate     # same, exit 1 on any false merge / fixture failure

Read-only. The pure functions take plain lists of dicts (unit-tested in tests/test_backlog_links_calibration.py); only `main`
reads the backlog, through shared/backlog_client.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import shared.config as cfg  # noqa: E402  (autoloads the repo .env)
from shared import backlog_client as bc  # noqa: E402
from shared.backlog_overlap import classify_incident, mentioned_ids  # noqa: E402

FIXTURE = _ROOT / "tests" / "fixtures" / "backlog_incident_cases.json"
_OPEN_AT = ("OPEN", "TODO", "IN_PROGRESS", "BLOCKED")


def state_as_of(tickets: list[dict], ts: datetime, *, exclude: Optional[str] = None) -> list[dict]:
    """Tickets that existed and had not been closed at `ts`. The backlog keeps creation_ts / closure_ts but not a status history, so
    "open at ts" is creation_ts <= ts < closure_ts (a ticket parked as DEFERRED later still counts as open then)."""
    out = []
    for t in tickets:
        if t["action_point_id"] == exclude or t["creation_ts"] is None or t["creation_ts"] > ts:
            continue
        if t.get("closure_ts") is not None and t["closure_ts"] <= ts:
            continue
        out.append(t)
    return out


def known_positives(tickets: list[dict], links: list[dict]) -> set[tuple]:
    """(incident ticket, target) pairs for which appending the incident to the target is the right call."""
    ids = {t["action_point_id"] for t in tickets}
    pos = set()
    for t in tickets:
        if t.get("solution_type") == "DUPLICATED":
            text = f"{t.get('title') or ''} {t.get('scenario_description') or ''}"
            pos.update((t["action_point_id"], o) for o in mentioned_ids(text) & ids if o != t["action_point_id"])
    for l in links:
        if l["status"] == "CONFIRMED" and l["link_type"] == "DUPLICATES":
            pos.add((l["from_id"], l["to_id"]))
    return pos


def replay(tickets: list[dict], positives: set[tuple], *, high: float, medium: float) -> dict:
    """(a): classify each ticket as a new incident against the state at its creation. Returns counts and the HIGH firings."""
    tiers = {"HIGH": 0, "MEDIUM": 0, "NONE": 0}
    firings, false_merges = [], []
    for t in sorted(tickets, key=lambda x: x["action_point_id"]):
        text = t.get("scenario_description") or t.get("title") or ""
        res = classify_incident(scenario=text, object_name=t.get("object_name") or "",
                                candidates=state_as_of(tickets, t["creation_ts"], exclude=t["action_point_id"]),
                                high_cosine=high, medium_cosine=medium)
        tiers[res["tier"]] += 1
        if res["tier"] == "HIGH":
            row = {"incident": t["action_point_id"], "target": res["ticket"], "reason": res["reason"], "score": res["score"]}
            firings.append(row)
            if (t["action_point_id"], res["ticket"]) not in positives:
                false_merges.append(row)
    return {"tiers": tiers, "high": firings, "false_merges": false_merges, "replayed": len(tickets)}


def run_fixture(cases: list[dict], *, high: float, medium: float) -> list[str]:
    """(b): the synthetic suite. Returns one message per failed case (empty = pass)."""
    bad = []
    for c in cases:
        res = classify_incident(scenario=c["incident"]["scenario"], object_name=c["incident"]["object_name"],
                                candidates=c["open"], high_cosine=high, medium_cosine=medium)
        if c.get("never_high") and res["tier"] == "HIGH":
            bad.append(f"{c['name']}: FALSE MERGE into {res['ticket']} ({res['reason']})")
        elif "expect" in c and res["tier"] != c["expect"]:
            bad.append(f"{c['name']}: expected {c['expect']}, got {res['tier']} ({res['reason']})")
    return bad


def load_fixture(path: Path = FIXTURE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gate", action="store_true", help="exit 1 on any false merge or fixture failure")
    a = ap.parse_args(argv)
    high, medium = cfg.BACKLOG_INCIDENT_HIGH_COSINE, cfg.BACKLOG_INCIDENT_MEDIUM_COSINE
    print(f"thresholds: HIGH cosine >= {high}, MEDIUM >= {medium}  "
          f"(switch BACKLOG_INCIDENT_AUTO_APPEND_ENABLED = {cfg.BACKLOG_INCIDENT_AUTO_APPEND_ENABLED})")
    fixture_bad = run_fixture(load_fixture()["cases"], high=high, medium=medium)
    print(f"\n(b) synthetic fixture: {len(load_fixture()['cases'])} cases, {len(fixture_bad)} failed")
    for m in fixture_bad:
        print("  FAIL " + m)
    try:
        tickets = bc.list_tickets()
        links = bc.list_links() if bc.links_store_present() else []
    except bc.BacklogUnavailable as exc:
        print(f"\n(a) historical replay: BACKLOG UNAVAILABLE ({exc})")
        return 2
    pos = known_positives(tickets, links)
    res = replay(tickets, pos, high=high, medium=medium)
    print(f"\n(a) historical replay: {res['replayed']} tickets against the state at their creation; "
          f"known positives {len(pos)}; tiers {res['tiers']}")
    for r in res["high"]:
        mark = "ok " if (r["incident"], r["target"]) in pos else "FALSE MERGE"
        print(f"  HIGH {mark:<11} {r['incident']} -> {r['target']}  score {r['score']}  {r['reason']}")
    n_bad = len(res["false_merges"]) + len(fixture_bad)
    print(f"\nGATE: {'PASS (zero false merges)' if not n_bad else f'FAIL ({n_bad})'}")
    return 1 if (a.gate and n_bad) else 0


if __name__ == "__main__":
    sys.exit(main())
