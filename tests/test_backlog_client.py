# -*- coding: utf-8 -*-
"""
Tests for shared/backlog_client.py (Fase 3, Postgres Action-Points Backlog plan, 2026-09-21).

Runnable standalone (R-7 discipline): no pipeline.py / core.io import needed.

- test_noop_when_dsn_unset / test_capture_exceptions_reraises_when_dsn_unset: pure unit tests,
  no network — verify the module degrades to a silent no-op and never swallows the wrapped
  exception when FONDOS_BACKLOG_PG_DSN isn't configured (the default state for every existing
  pipeline run today).
- test_dedup_against_live_gestion_db: integration test against a real `gestion` Postgres
  instance, skipped unless FONDOS_BACKLOG_PG_DSN is set. Uses a UUID-suffixed object_name so it
  can never collide with a real ticket, and always deletes what it created (BACKLOG_LOGS cascade
  on delete) in a finally block regardless of outcome.
- test_fallback_log_written_on_unreachable_server: hardening pass (post code-review) — points
  FONDOS_BACKLOG_PG_DSN at a port nothing listens on, so the Postgres write fails, and checks the
  incident lands in the local JSONL fallback log instead of being silently lost.
- test_check_dsn_*: the read-only --check-dsn diagnostic, both the failure path (no DSN) and the
  success path (skipped unless FONDOS_BACKLOG_PG_DSN is set).
"""
from __future__ import annotations

import os
import uuid

import pytest

from shared import backlog_client

try:
    import psycopg
except ImportError:
    psycopg = None


def test_noop_when_dsn_unset(monkeypatch):
    monkeypatch.delenv("FONDOS_BACKLOG_PG_DSN", raising=False)
    result = backlog_client.report_incident(
        object_name="unit_test_object", scenario_description="should not be written anywhere",
    )
    assert result is None


def test_capture_exceptions_reraises_when_dsn_unset(monkeypatch):
    monkeypatch.delenv("FONDOS_BACKLOG_PG_DSN", raising=False)
    with pytest.raises(ValueError, match="boom"):
        with backlog_client.capture_exceptions(object_name="unit_test_object"):
            raise ValueError("boom")


def test_capture_exceptions_does_not_swallow_on_report_failure(monkeypatch):
    # Even if report_incident itself explodes internally, capture_exceptions must still
    # surface the ORIGINAL exception, not the reporting failure.
    monkeypatch.setattr(
        backlog_client, "report_incident",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("backlog write exploded")),
    )
    with pytest.raises(ValueError, match="original failure"):
        with backlog_client.capture_exceptions(object_name="unit_test_object"):
            raise ValueError("original failure")


@pytest.mark.skipif(
    not os.environ.get("FONDOS_BACKLOG_PG_DSN") or psycopg is None,
    reason="FONDOS_BACKLOG_PG_DSN not set or psycopg3 not installed — set it to a live "
           "gestion database to run this integration test",
)
def test_dedup_against_live_gestion_db():
    dsn = os.environ["FONDOS_BACKLOG_PG_DSN"]
    object_name = f"test_backlog_client_{uuid.uuid4().hex[:12]}"
    try:
        apid1 = backlog_client.report_incident(
            object_name=object_name, scenario_description="first occurrence",
            project_code="FND", object_type="N/A",
        )
        assert apid1 is not None

        apid2 = backlog_client.report_incident(
            object_name=object_name, scenario_description="second occurrence (should dedup)",
            project_code="FND", object_type="N/A",
        )
        assert apid2 == apid1, "second incident on the same open object_name must reuse the ticket"

        with psycopg.connect(dsn) as conn:
            (n_tickets,) = conn.execute(
                "SELECT count(*) FROM backlog.BACKLOG WHERE object_name = %s", (object_name,)
            ).fetchone()
            assert n_tickets == 1

            (n_logs,) = conn.execute(
                "SELECT count(*) FROM backlog.BACKLOG_LOGS WHERE action_point_id = %s", (apid1,)
            ).fetchone()
            assert n_logs == 2  # auto-opened + reincidence
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM backlog.BACKLOG WHERE object_name = %s", (object_name,))


def test_fallback_log_written_on_unreachable_server(monkeypatch, tmp_path):
    fallback_path = tmp_path / "fallback.jsonl"
    monkeypatch.setenv("FONDOS_BACKLOG_PG_DSN", "postgresql://postgres@127.0.0.1:1/gestion")
    monkeypatch.setenv("FONDOS_BACKLOG_FALLBACK_LOG", str(fallback_path))

    result = backlog_client.report_incident(
        object_name="unreachable_server_test", scenario_description="server unreachable",
    )
    assert result is None  # write to Postgres failed, as expected
    assert fallback_path.exists(), "a failed Postgres write must still land in the fallback log"

    lines = fallback_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    import json
    record = json.loads(lines[0])
    assert record["object_name"] == "unreachable_server_test"
    assert record["scenario_description"] == "server unreachable"
    assert "logged_at" in record


def test_check_dsn_fails_cleanly_when_unset(monkeypatch, capsys):
    monkeypatch.delenv("FONDOS_BACKLOG_PG_DSN", raising=False)
    assert backlog_client.check_dsn(verbose=True) is False
    assert "FONDOS_BACKLOG_PG_DSN is not set" in capsys.readouterr().out


@pytest.mark.skipif(
    not os.environ.get("FONDOS_BACKLOG_PG_DSN") or psycopg is None,
    reason="FONDOS_BACKLOG_PG_DSN not set or psycopg3 not installed",
)
def test_check_dsn_succeeds_against_live_gestion_db(capsys):
    assert backlog_client.check_dsn(verbose=True) is True
    out = capsys.readouterr().out
    assert "backlog.BACKLOG readable" in out
    assert "backlog.BACKLOG_LOGS readable" in out


@pytest.mark.skipif(
    not os.environ.get("FONDOS_BACKLOG_PG_DSN") or psycopg is None,
    reason="FONDOS_BACKLOG_PG_DSN not set or psycopg3 not installed",
)
def test_sp_close_release_dry_run_does_not_mutate():
    dsn = os.environ["FONDOS_BACKLOG_PG_DSN"]
    release = f"vTEST_{uuid.uuid4().hex[:8]}"
    apid = None
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            apid = conn.execute(
                "SELECT 'FND-' || LPAD(nextval('backlog.seq_backlog_fnd')::text,4,'0')"
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO backlog.BACKLOG
                   (action_point_id,title,project_code,nature,object_type,status,priority,
                    reporter,release_version)
                   VALUES (%s,'dry-run test ticket','FND','TSK','N/A','READY_FOR_DEPLOY','LOW',
                           'PYTEST',%s)""",
                (apid, release),
            )

            conn.execute("CALL backlog.sp_close_release(%s, %s, TRUE)", ("does-not-match", "PYTEST"))
            status_untouched = conn.execute(
                "SELECT status FROM backlog.BACKLOG WHERE action_point_id = %s", (apid,)
            ).fetchone()[0]
            assert status_untouched == "READY_FOR_DEPLOY"

            conn.execute("CALL backlog.sp_close_release(%s, %s, TRUE)", (release, "PYTEST"))
            status_after_dry_run = conn.execute(
                "SELECT status, closure_ts FROM backlog.BACKLOG WHERE action_point_id = %s", (apid,)
            ).fetchone()
            assert status_after_dry_run == ("READY_FOR_DEPLOY", None), "dry_run=TRUE must not mutate anything"

            conn.execute("CALL backlog.sp_close_release(%s, %s, FALSE)", (release, "PYTEST"))
            status_after_real_run = conn.execute(
                "SELECT status, closure_ts IS NOT NULL FROM backlog.BACKLOG WHERE action_point_id = %s",
                (apid,),
            ).fetchone()
            assert status_after_real_run == ("CLOSED", True)
    finally:
        if apid:
            with psycopg.connect(dsn, autocommit=True) as conn:
                conn.execute("DELETE FROM backlog.BACKLOG WHERE action_point_id = %s", (apid,))
