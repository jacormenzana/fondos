# -*- coding: utf-8 -*-
"""Read-only report of the data-suspicion predicate on the live store, with and without the verified-genuine exemptions (FND-0394).

    python scripts/audit/data_suspect_report.py [--diff]

Prints: the suspects WITHOUT exemptions, the exemptions in force and their state, the rows the exemptions withdraw, the suspects WITH them, and the exemption warnings. It writes nothing (the session is read-only) and nothing in the selection
reads the predicate while `shared.config.DATA_SUSPECT_ENABLED` is False: this is the diff to read BEFORE anyone proposes switching it on (like eligibility_switch_diff.py for FND-0251).
`--diff` adds the eligible-set diff of the live scorer with the switch OFF and ON (in memory, nothing persisted): which funds lose eligibility, per sub-portfolio, and which of them are in PRODUCTION.
"""
from __future__ import annotations

import argparse
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


def selection_diff(conn) -> None:
    """The live scorer's eligible set with DATA_SUSPECT_ENABLED OFF versus ON, on the stored metrics of today, for the regime of the PRODUCTION scenario. Read-only: scores are computed in memory."""
    from proyecto3.src import fund_scorer as fs
    row = conn.execute("SELECT s.scenario_id, s.macro_regime FROM gold.portfolio_scenarios s WHERE s.status = 'PRODUCTION' ORDER BY s.created_at DESC LIMIT 1").fetchone()
    regime = row[1] if row else "Expansion"
    prod = {r[0] for r in conn.execute("SELECT isin FROM gold.portfolio_weights WHERE scenario_id = %s", (row[0],)).fetchall()} if row else set()
    saved = _cfg.DATA_SUSPECT_ENABLED
    try:
        _cfg.DATA_SUSPECT_ENABLED = False
        off = fs.score_funds_from_df(fs.load_fund_metrics_for_scoring(conn, regime=regime), regime, verbose=False)
        _cfg.DATA_SUSPECT_ENABLED = True
        on_frame = fs.load_fund_metrics_for_scoring(conn, regime=regime)
        on = fs.score_funds_from_df(on_frame, regime, verbose=False)
    finally:
        _cfg.DATA_SUSPECT_ENABLED = saved
    key = ["isin", "subportfolio"]
    m = off.merge(on, on=key, suffixes=("_off", "_on"))
    lost = m[m["eligible_off"] & ~m["eligible_on"]]
    gained = m[~m["eligible_off"] & m["eligible_on"]]
    print(f"\nELIGIBLE-SET DIFF (regime {regime}; PRODUCTION {row[0] if row else '-'}): OFF eligible {int(off['eligible'].sum())}, ON eligible {int(on['eligible'].sum())}; "
          f"lose {len(lost)}, gain {len(gained)}")
    for r in lost.itertuples():
        print(f"  loses {r.isin} in {r.subportfolio}{'  << IN PRODUCTION' if r.isin in prod else ''}: {str(r.exclusion_reason_on)[:140]}")
    graced = on_frame["exemption_grace"].dropna() if "exemption_grace" in on_frame.columns else []
    print(f"funds in exemption grace: {len(graced)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--diff", action="store_true", help="also print the eligible-set diff of the live scorer, switch OFF versus ON")
    args = ap.parse_args()
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
    if args.diff:
        selection_diff(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
