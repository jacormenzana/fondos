#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
migrate_audit_run.py -- create control.audit_run (FND-0261), the header of each persisted statistical-audit run (scope, completeness
status, catalog version, git sha, config hash), written by scripts/audit/run_statistical_audit.py --persist in the same transaction
as the run's rows.

    python scripts/ops/migrate_audit_run.py            # DRY RUN: read-only
    python scripts/ops/migrate_audit_run.py --apply    # CREATE TABLE IF NOT EXISTS

Same guards and conventions as migrate_fund_metric_family_state.py (it is the same code, parameterised by table): owner DSN, the
DDL read from db/pg/35_control.sql (single source of truth), refuses while a cycle appears to be running, never prints the DSN,
safe to re-run. No backfill: runs persisted before the table have no header and are never evidence once
shared.config.AUDIT_COMPLETENESS_CONTRACT_ENABLED is on (persist one full audit after applying, BEFORE flipping the switch).

Run ORDER: no cycle running -> this script with --apply -> one `run_statistical_audit.py --domain p2 --persist` (writes the first
header; read its completeness line) -> review a shadow cycle's statuses -> flip the switch. ROLLBACK: switch back to False; the table
may stay.

Exit codes: 0 ok (or dry run), 1 apply failed, 2 usage or connection error, 3 refused (a cycle appears to be running).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from migrate_fund_metric_family_state import main  # noqa: E402

TABLE = "control.audit_run"

if __name__ == "__main__":
    sys.exit(main(table=TABLE))
