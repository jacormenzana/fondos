#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
migrate_audit_accepted_finding.py -- create control.audit_accepted_finding (FND-0234(e)), the accepted-residual baseline
read by scripts/audit/run_statistical_audit.py.

    python scripts/ops/migrate_audit_accepted_finding.py            # DRY RUN: read-only
    python scripts/ops/migrate_audit_accepted_finding.py --apply    # CREATE TABLE IF NOT EXISTS

Same guards and conventions as migrate_fund_metric_family_state.py (it is the same code, parameterised by table): owner DSN,
the DDL read from db/pg/35_control.sql (single source of truth), refuses while a cycle appears to be running, never prints the
DSN, safe to re-run. No backfill: acceptances are added explicitly with scripts/audit/accept_audit_residual.py.
Until the table exists the audit simply reports no accepted residuals (it says so on stderr); nothing else depends on it.

Exit codes: 0 ok (or dry run), 1 apply failed, 2 usage or connection error, 3 refused (a cycle appears to be running).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from migrate_fund_metric_family_state import main  # noqa: E402

TABLE = "control.audit_accepted_finding"

if __name__ == "__main__":
    sys.exit(main(table=TABLE))
