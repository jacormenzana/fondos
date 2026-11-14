# -*- coding: utf-8 -*-
"""Package H: the owner-run applier of the derived fee flags migration (pure parts; the script itself is never run by a session)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("migrate_fee_flags", ROOT / "scripts" / "mig" / "fondos" / "migrate_fund_master_derived_fee_flags.py")
mig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mig)


def test_the_statement_is_the_one_alter_table_of_the_migration_file():
    sql = mig.ddl_statement(mig.MIGRATION.read_text(encoding="utf-8"))
    assert sql.upper().startswith("ALTER TABLE SILVER.FUND_MASTER") and sql.count(";") == 1
    assert "DROP COLUMN fee_known_flag" in sql and sql.count("GENERATED ALWAYS AS") == 2 and "exit_fee_known_flag" in sql
    assert "BEGIN" not in sql and "COMMIT" not in sql        # the caller owns the transaction


def test_the_migration_expression_is_the_shared_one():
    """The file is generated from shared.fee_state.fee_state_sql; if either changes without the other, this fails."""
    from shared.fee_state import fee_state_sql
    sql = " ".join(mig.MIGRATION.read_text(encoding="utf-8").split())
    assert " ".join(fee_state_sql("entry_fee_pct", "entry_fee_pct_max", "entry").split()) in sql
    assert " ".join(fee_state_sql("exit_fee_pct", "exit_fee_pct_max", "exit").split()) in sql


def test_an_edited_file_that_is_no_longer_one_statement_is_refused():
    with pytest.raises(ValueError):
        mig.ddl_statement("ALTER TABLE silver.fund_master ADD COLUMN x int;\nALTER TABLE silver.fund_master DROP COLUMN y;")
    with pytest.raises(ValueError):
        mig.ddl_statement("DROP TABLE silver.fund_master;")
