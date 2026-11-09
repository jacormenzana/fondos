# -*- coding: utf-8 -*-
"""Read-only report of the data-suspicion predicate on the live store, with and without the verified-genuine exemptions (FND-0394).

    python scripts/audit/data_suspect_report.py

Prints: the suspects WITHOUT exemptions, the exemptions in force and their state, the rows the exemptions withdraw, the suspects WITH them, and the exemption warnings. It writes nothing (the session is read-only) and nothing in the selection
reads the predicate while `shared.config.DATA_SUSPECT_ENABLED` is False: this is the diff to read BEFORE anyone proposes switching it on (like eligibility_switch_diff.py for FND-0251).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import config as _cfg                                                       # noqa: E402
from shared.data_exemption import attention_lines, exemption_state, load_build_dates, validate_exemptions   # noqa: E402
from shared.data_suspect import exempted_hits, find_suspects, load_effective_exemptions, load_panel         # noqa: E402
from shared.db import get_connection                                                    # noqa: E402
from shared.family_scope import load_family_map                                         # noqa: E402


def _line(s) -> str:
    top = ", ".join(f"{m} z={z} n={n}" for m, z, n in s.detail["top_metrics"][:2])
    return f"  {s.representative} ({'+'.join(s.kinds)}; {s.n_metrics} quantity/ies; max|z| {s.max_abs_z}): {top}"


def main() -> int:
    conn = get_connection()
    conn.read_only = True
    today = date.today()
    panel = load_panel(conn)
    family_of = load_family_map(conn)
    builds = load_build_dates(conn)
    problems = validate_exemptions(_cfg.DATA_SUSPECT_EXEMPTIONS)
    exemptions = load_effective_exemptions(conn, today)
    print(f"panel: {len(panel)} rows, {panel['isin'].nunique()} funds; thresholds: |z| >= {_cfg.DATA_SUSPECT_MIN_DEV}, integrity >= {_cfg.DATA_SUSPECT_MIN_FLAGGED_METRICS} quantities; DATA_SUSPECT_ENABLED = {_cfg.DATA_SUSPECT_ENABLED}")
    print("registry problems:", problems or "none")
    before = find_suspects(panel, None, family_of)
    print(f"\nSUSPECTS WITHOUT exemptions ({len(before)}):")
    for s in before:
        print(_line(s))
    print("\nEXEMPTIONS:")
    for isin, e in sorted(_cfg.DATA_SUSPECT_EXEMPTIONS.items()):
        print(f"  {isin}: {exemption_state(e, today, builds, _cfg.DATA_SUSPECT_EXEMPTION_WARN_DAYS)} until {e['until']} ({e['ap']}) quantities {e['quantities']}")
    out = exempted_hits(panel, exemptions, family_of)
    print(f"\nROWS WITHDRAWN by the exemptions ({len(out)}):")
    for r in out.sort_values(["isin", "metric"]).itertuples():
        print(f"  {r.isin} {r.metric} z={round(float(r.z), 1)} source_rows={r.source_rows}")
    after = find_suspects(panel, None, family_of, exemptions=exemptions)
    print(f"\nSUSPECTS WITH exemptions ({len(after)}):")
    for s in after:
        print(_line(s))
    gone = sorted({s.representative for s in before} - {s.representative for s in after})
    print(f"\nno longer suspect: {gone}")
    for line in attention_lines(_cfg.DATA_SUSPECT_EXEMPTIONS, today, builds, _cfg.DATA_SUSPECT_EXEMPTION_WARN_DAYS):
        print("WARNING:", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
