#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
migrate_audit_finding_isin.py -- create control.audit_finding_isin (FND-0234(d)), the ISINs behind each group-level audit finding,
written by scripts/audit/run_statistical_audit.py --persist.

    python scripts/ops/migrate_audit_finding_isin.py            # DRY RUN: read-only
    python scripts/ops/migrate_audit_finding_isin.py --apply    # CREATE TABLE IF NOT EXISTS

Same guards and conventions as migrate_fund_metric_family_state.py (it is the same code, parameterised by table): owner DSN, the
DDL read from db/pg/35_control.sql (single source of truth), refuses while a cycle appears to be running, never prints the DSN,
safe to re-run. No backfill: rows are written by the next audit --persist run (earlier runs keep no ISIN detail, and the drift
report says so). Until the table exists, --persist skips the detail and says so; nothing else depends on it.

Exit codes: 0 ok (or dry run), 1 apply failed, 2 usage or connection error, 3 refused (a cycle appears to be running).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from migrate_fund_metric_family_state import main  # noqa: E402

TABLE = "control.audit_finding_isin"

if __name__ == "__main__":
    sys.exit(main(table=TABLE))
