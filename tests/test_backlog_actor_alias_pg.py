# tests/test_backlog_actor_alias_pg.py
# -*- coding: utf-8 -*-
"""Actor alias store of the backlog (FND-0330; db/pg_backlog/25_actor_alias.sql, applied by scripts/ops/migrate_backlog_v2.py --phase alias).

Hermetic: loads 00_schema.sql, the links block and the alias block into the throwaway container inside a savepoint that is always rolled
back (pg_conn). Covers the seed, idempotence, the CHECK, the append-only trigger, both views, the client read API, the prerequisite
guard and the staged rollback (which must leave logs and link events untouched).

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_actor_alias_pg.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import migrate_backlog_links as links  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402
from shared import backlog_client as bc  # noqa: E402

SCHEMA_SQL = (_ROOT / "db" / "pg_backlog" / "00_schema.sql").read_text(encoding="utf-8")


@pytest.fixture
def db(pg_conn):
    pg_conn.execute(SCHEMA_SQL)
    links.migrate(pg_conn)
    mig.migrate(pg_conn, "alias")
    pg_conn.execute("INSERT INTO backlog.BACKLOG (action_point_id, title, project_code, nature, object_type, status, priority) "
                    "VALUES ('FND-9001', 't1', 'FND', 'BUG', 'N/A', 'OPEN', 'LOW'), ('FND-9002', 't2', 'FND', 'BUG', 'N/A', 'OPEN', 'LOW')")
    return pg_conn


def _rejects(conn, sql, params=()):
    with pytest.raises(psycopg.errors.Error):
        with conn.transaction():
            conn.execute(sql, params)


def _log(conn, who, apid="FND-9001"):
    conn.execute("INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (%s, %s, 'c')", (apid, who))


def test_seed_has_the_13_spellings_and_the_kind_is_derived(db):
    rows = dict(db.execute("SELECT raw_name, kind FROM backlog.actor_alias").fetchall())
    assert len(rows) == 13
    assert rows["jacor"] == "human" and rows["claude-opus-5-5"] == "agent" and rows["SYSTEM_RELEASE"] == "system"


def test_migration_is_idempotent_and_never_duplicates(db):
    before = db.execute("SELECT count(*) FROM backlog.actor_alias").fetchone()[0]
    mig.migrate(db, "alias")
    assert db.execute("SELECT count(*) FROM backlog.actor_alias").fetchone()[0] == before == 13
    assert mig.existing(db, "alias") == {"tables": ["actor_alias"], "views": ["v_actor_unmapped", "v_backlog_log_actor"], "missing_requires": []}


def test_check_rejects_a_non_canonical_identity(db):
    ins = "INSERT INTO backlog.actor_alias (raw_name, canonical) VALUES (%s, %s)"
    for bad in ("claude", "agent:", "robot:x", "agent:with space", ""):
        _rejects(db, ins, ("newbie", bad))
    db.execute(ins, ("newbie", "agent:claude-opus-5-5"))          # a valid one still goes in


def test_the_mapping_is_append_only(db):
    _rejects(db, "UPDATE backlog.actor_alias SET canonical = 'human:somebody' WHERE raw_name = 'claude'")
    _rejects(db, "DELETE FROM backlog.actor_alias WHERE raw_name = 'claude'")
    assert db.execute("SELECT canonical FROM backlog.actor_alias WHERE raw_name = 'claude'").fetchone()[0] == "agent:claude"


def test_log_view_maps_aliases_keeps_canonical_and_flags_the_rest(db):
    for who in ("claude", "agent:claude-opus-5-5", "newbie"):
        _log(db, who)
    got = {r[0]: (r[1], r[2]) for r in db.execute("SELECT raw_actor, actor, mapped FROM backlog.v_backlog_log_actor")}
    assert got["claude"] == ("agent:claude", True)
    assert got["agent:claude-opus-5-5"] == ("agent:claude-opus-5-5", True)         # canonical maps to itself, no alias row needed
    assert got["newbie"] == ("unmapped:newbie", False)


def test_unmapped_view_covers_logs_and_link_events_and_ignores_mapped_or_canonical(db):
    _log(db, "claude")                                         # alias
    _log(db, "agent:claude-opus-5-5")                           # canonical
    _log(db, "newbie")
    _log(db, "newbie", "FND-9002")
    db.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username) "
               "VALUES ('FND-9001', 'FND-9002', 'RELATED', 'PROPOSED', 'e', 'ghost')")
    rows = {r[0]: r[1] for r in db.execute("SELECT raw_actor, n_rows FROM backlog.v_actor_unmapped")}
    assert rows == {"newbie": 2, "ghost": 1}


def test_client_read_api_over_the_store(db):
    assert bc.actor_store_present(conn=db) is True
    aliases = bc.list_actor_aliases(conn=db)
    assert aliases["claude-code"] == "agent:claude" and len(aliases) == 13
    assert bc.unmapped_actors(conn=db) == []
    _log(db, "stranger")
    assert [m["raw_actor"] for m in bc.unmapped_actors(conn=db)] == ["stranger"]


def test_store_is_reported_absent_before_the_phase(pg_conn):
    pg_conn.execute(SCHEMA_SQL)
    links.migrate(pg_conn)
    assert bc.actor_store_present(conn=pg_conn) is False
    assert mig.existing(pg_conn, "alias")["tables"] == []


def test_phase_refuses_to_run_without_its_prerequisites(pg_conn):
    pg_conn.execute(SCHEMA_SQL)                                  # no links store
    with pytest.raises(RuntimeError, match="backlog_link_event"):
        mig.migrate(pg_conn, "alias")


def test_history_fingerprint_is_stable_across_apply_and_rollback_and_ignores_later_rows(pg_conn):
    pg_conn.execute(SCHEMA_SQL)
    links.migrate(pg_conn)
    pg_conn.execute("INSERT INTO backlog.BACKLOG (action_point_id, title, project_code, nature, object_type, status, priority) "
                    "VALUES ('FND-9001', 't1', 'FND', 'BUG', 'N/A', 'OPEN', 'LOW'), ('FND-9002', 't2', 'FND', 'BUG', 'N/A', 'OPEN', 'LOW')")
    _log(pg_conn, "claude")
    _log(pg_conn, "jacor", "FND-9002")
    pg_conn.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username) "
                    "VALUES ('FND-9001', 'FND-9002', 'RELATED', 'PROPOSED', 'e', 'claude')")
    before = bc.history_fingerprint(conn=pg_conn)
    assert before["logs"] == 2 and before["events"] == 1 and len(before["logs_md5"]) == 32
    mig.migrate(pg_conn, "alias")
    assert bc.history_fingerprint(conn=pg_conn) == before                      # the phase touched no history
    mig.migrate(pg_conn, "alias", down=True)
    assert bc.history_fingerprint(conn=pg_conn) == before                      # nor did the rollback
    _log(pg_conn, "newer")                                                     # appended later: higher id
    bounded = bc.history_fingerprint(max_log_id=before["max_log_id"], max_event_id=before["max_event_id"], conn=pg_conn)
    assert bounded == before                                                   # bounded by the baseline ids, new rows do not move it
    assert bc.history_fingerprint(conn=pg_conn)["logs"] == 3
    pg_conn.execute("UPDATE backlog.BACKLOG_LOGS SET comment = 'edited' WHERE log_id = %s", (before["max_log_id"],))
    assert bc.history_fingerprint(max_log_id=before["max_log_id"], conn=pg_conn)["logs_md5"] != before["logs_md5"]   # a rewrite IS detected


def test_rollback_removes_the_phase_and_leaves_history_untouched(db):
    _log(db, "claude")
    db.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username) "
               "VALUES ('FND-9001', 'FND-9002', 'RELATED', 'PROPOSED', 'e', 'jacor')")
    logs = db.execute("SELECT count(*), min(username) FROM backlog.BACKLOG_LOGS").fetchone()
    evs = db.execute("SELECT count(*), min(username) FROM backlog.backlog_link_event").fetchone()
    mig.migrate(db, "alias", down=True)
    assert mig.existing(db, "alias") == {"tables": [], "views": [], "missing_requires": []}
    assert db.execute("SELECT count(*), min(username) FROM backlog.BACKLOG_LOGS").fetchone() == logs
    assert db.execute("SELECT count(*), min(username) FROM backlog.backlog_link_event").fetchone() == evs
    assert db.execute("SELECT count(*) FROM pg_proc WHERE proname = 'trg_actor_alias_immutable'").fetchone()[0] == 0
    mig.migrate(db, "alias")                                     # and the phase can be applied again
    assert db.execute("SELECT count(*) FROM backlog.actor_alias").fetchone()[0] == 13
