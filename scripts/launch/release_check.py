#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
release_check.py -- read-only preflight of the 2026-10-10 release launchers (FND-0246.bat, FND-0246_V2.bat).

The release is a CODE state, not an environment one: the switches are constants in shared/config.py (a `set X=1` in a .bat is
ignored -- the first version of FND-0246.bat did exactly that and would have recomputed everything with the switches OFF). This
check refuses to start the recompute unless:
  * every switch of the release is True in shared/config.py,
  * shared.eur_nav.release_coupling_errors() is empty (the EUR NAV view and the FND-0235 FX view move together, and the EUR view
    needs the first/last peer NAV),
  * CALC_VERSION can be read (the launchers pass it to the beta gate).
It prints CALC_VERSION and the effective calc version the run will stamp in the fingerprint. Nothing is written.

    python scripts/launch/release_check.py

Exit codes: 0 ready | 1 the configuration is not the release one (nothing should be recomputed).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# The switches released together on 2026-10-10 (owner decision, commit f1de8d5).
RELEASE_SWITCHES = (
    "EUR_NAV_CONVERSION_ENABLED",           # FND-0243
    "FX_CONTRIBUTION_EUR_VIEW_ENABLED",     # FND-0235
    "ANNUALIZATION_INTERVAL_ENABLED",       # FND-0240
    "DEFLATION_MONTH_ALIGN_ENABLED",        # FND-0241
    "SORTINO_MIN_DOWNSIDE_COUNT_ENABLED",   # FND-0242
)
RC_OK, RC_NOT_READY = 0, 1


def problems(cfg, coupling_errors: list, calc_version: "str | None") -> list:
    """Pure: why the configuration is not the release one (empty = ready)."""
    out = [f"{name} is not True in shared/config.py" for name in RELEASE_SWITCHES if getattr(cfg, name, None) is not True]
    out += list(coupling_errors)
    if not calc_version:
        out.append("CALC_VERSION could not be read from proyecto2/src/pipeline/run_pipeline.py")
    return out


def main() -> int:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts" / "launch"))
    from shared import config
    from shared.eur_nav import release_coupling_errors
    from p1p2_state import calc_version
    sys.path.insert(0, str(ROOT / "proyecto2"))
    from src.utils.fingerprint import effective_calc_version

    cv = calc_version()
    found = problems(config, release_coupling_errors(config), cv)
    for name in RELEASE_SWITCHES:
        print(f"  {name:<36} {getattr(config, name, None)}")
    if cv:
        print(f"  CALC_VERSION {cv}  (fingerprint: {effective_calc_version(cv)})")
    if found:
        print("\nRELEASE CHECK: FAIL -- nothing should be recomputed:")
        for p in found:
            print(f"  - {p}")
        return RC_NOT_READY
    print("\nRELEASE CHECK: OK")
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
