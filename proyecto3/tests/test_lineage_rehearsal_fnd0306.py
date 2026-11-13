# proyecto3/tests/test_lineage_rehearsal_fnd0306.py
# -*- coding: utf-8 -*-
"""
FND-0306: the deliberate-mismatch REHEARSAL, end to end through the real launcher (scripts/launch/p3_promote_scenario.py main) with the lifecycle test's connection
double: the DRAFT's stamps are in its notes JSON, the evidence header is crafted, and the comparison functions, the gate and the shadow row are the real ones.

    python -m pytest proyecto3/tests/test_lineage_rehearsal_fnd0306.py -v
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import shared.config as cfg  # noqa: E402
from shared.governance import lineage as ln  # noqa: E402
from test_scenario_lifecycle import _CliConn, _run_cli  # noqa: E402

RUN_1, RUN_2 = "P2-20261112_194930-2984d0", "P2-20261113_090000-bbbbbb"
ROWS = [("A", "20261112", RUN_1, 40), ("B", "20261112", RUN_1, 38), ("Z", "20261112", RUN_1, 30)]          # A and B are the scenario's funds
BUILD = {"git_commit": "2a91f2d", "git_clean": True, "config_hash": "v1:aaaaaaaaaaaa"}
WEIGHTS = {"A": 0.5, "B": 0.45}                                                                           # + 0.05 declared cash: a clean, promotable DRAFT


def _stamp(rows):
    return {"version": "v1", "universe": ln.fingerprint(rows).as_dict(), "scenario": ln.fingerprint([r for r in rows if r[0] in WEIGHTS]).as_dict()}


def _notes(stamp="default", build="default"):
    notes = {"cash_weight": 0.05}
    if stamp is not None:
        notes["metrics_lineage"] = _stamp(ROWS) if stamp == "default" else stamp
    if build is not None:
        notes["build_identity"] = BUILD if build == "default" else build
    return json.dumps(notes)


class _LinConn(_CliConn):
    """Answers the lineage aggregate from `lineage_rows` (the stored metrics TODAY); `broken` makes the read fail like an unreadable store."""

    def __init__(self, *a, lineage_rows=ROWS, broken=False, **kw):
        super().__init__(*a, **kw)
        self.lineage_rows, self.broken = lineage_rows, broken

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("SELECT isin, algorithm_version, batch_id, COUNT(*) FROM fund_metrics"):
            self.executed.append(s)
            if self.broken:
                raise RuntimeError("lineage store unreadable")
            rows = [r for r in self.lineage_rows if not params or r[0] in params[0]]
            return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: rows)
        return super().execute(sql, params)


def _header(rows=ROWS, **over):
    base = {"git_commit": "2a91f2d6d53c", "git_clean": True, "config_hash": "v1:aaaaaaaaaaaa", "catalog_version": "c1",
            "manifest": {"calc_version": "20261112", "metrics_lineage": ln.fingerprint(rows).as_dict()}}
    base.update(over)
    return base


def _rehearse(monkeypatch, capsys, *, on, notes=None, header=None, argv=("d",), **conn_kw):
    import p3_promote_scenario as cli
    monkeypatch.setattr(cfg, "PROMOTION_AUDIT_IDENTITY_GATE_ENABLED", on, raising=False)
    monkeypatch.setattr(cli, "_evidence_header", lambda conn, run_id: header if header is not None else _header())
    conn = _LinConn({"d": "DRAFT"}, WEIGHTS, ccy="EUR", notes=notes if notes is not None else _notes(), **conn_kw)
    rc, promoted = _run_cli(monkeypatch, conn, list(argv))
    return rc, promoted, capsys.readouterr().out, conn


def _shadow_rows(conn):
    return [s for s in conn.executed if "'SHADOW'" in s]


def test_with_the_switch_off_even_a_total_mismatch_promotes_and_leaves_one_shadow_row(monkeypatch, capsys):
    other = [("A", "20261113", RUN_2, 41)] + ROWS[1:]                                  # P2 rewrote a fund of the scenario after the build
    rc, promoted, out, conn = _rehearse(monkeypatch, capsys, on=False, header=_header(other, config_hash="v1:bbbbbbbbbbbb"))
    assert rc == 0 and promoted and "NO PROMOVIBLE" not in out
    assert "sombra: PROMOTION_AUDIT_IDENTITY_GATE_ENABLED apagado, no bloquea" in out
    assert "DRAFT vs auditoria de evidencia (universo): MISMATCH" in out and "DRAFT vs auditoria de evidencia (codigo y configuracion): MISMATCH" in out
    assert len(_shadow_rows(conn)) == 1


def test_with_the_switch_off_a_dry_run_prints_the_verdicts_and_writes_nothing(monkeypatch, capsys):
    rc, promoted, out, conn = _rehearse(monkeypatch, capsys, on=False, argv=("d", "--dry-run"))
    assert rc == 0 and not promoted and "DRY RUN: promovible" in out and "MATCH" in out and not _shadow_rows(conn)


def test_with_the_switch_on_identical_data_and_identity_promote(monkeypatch, capsys):
    rc, promoted, out, conn = _rehearse(monkeypatch, capsys, on=True)
    assert rc == 0 and promoted and "(puerta)" in out and "MISMATCH" not in out and not _shadow_rows(conn)


def test_the_deliberate_mismatch_an_audit_that_judged_other_data_refuses_and_a_reasoned_override_lets_it_through(monkeypatch, capsys):
    other = [("A", "20261113", RUN_2, 40)] + ROWS[1:]
    rc, promoted, out, _ = _rehearse(monkeypatch, capsys, on=True, header=_header(other))
    assert rc == 1 and not promoted and "[GATE:METRICS_LINEAGE]" in out and "MISMATCH" in out and "[GATE:AUDIT_IDENTITY]" not in out
    rc, promoted, out, _ = _rehearse(monkeypatch, capsys, on=True, header=_header(other),
                                     argv=("d", "--accept-alarms", "audit re-run is scheduled", "--ap", "FND-0306"))
    assert rc == 0 and promoted and "[OVERRIDE:METRICS_LINEAGE] FND-0306" in out


def test_a_configuration_change_between_the_build_and_the_audit_refuses_under_its_own_code(monkeypatch, capsys):
    rc, promoted, out, _ = _rehearse(monkeypatch, capsys, on=True, header=_header(config_hash="v1:bbbbbbbbbbbb"))
    assert rc == 1 and not promoted and "[GATE:AUDIT_IDENTITY]" in out and "configuration differs" in out and "[GATE:METRICS_LINEAGE]" not in out


def test_a_draft_built_before_the_feature_is_refused_never_waved_through(monkeypatch, capsys):
    rc, promoted, out, _ = _rehearse(monkeypatch, capsys, on=True, notes=_notes(stamp=None, build=None))
    assert rc == 1 and not promoted and "[GATE:METRICS_LINEAGE]" in out and "[GATE:AUDIT_IDENTITY]" in out and "UNKNOWN" in out


def test_an_unreadable_lineage_fails_closed_when_the_gate_is_on_and_only_warns_when_it_is_off(monkeypatch, capsys):
    rc, promoted, out, _ = _rehearse(monkeypatch, capsys, on=True, broken=True)
    assert rc == 1 and not promoted and "NO LEGIBLE" in out and "[GATE:METRICS_LINEAGE]" in out and "[GATE:AUDIT_IDENTITY]" in out
    rc, promoted, out, conn = _rehearse(monkeypatch, capsys, on=False, broken=True)
    assert rc == 0 and promoted and "NO LEGIBLE" in out and not _shadow_rows(conn)


def test_a_recompute_of_a_fund_outside_the_scenario_after_the_build_moves_only_the_now_universe_verdict(monkeypatch, capsys):
    later = ROWS[:2] + [("Z", "20261113", RUN_2, 30)]
    rc, promoted, out, conn = _rehearse(monkeypatch, capsys, on=True, lineage_rows=later)
    assert rc == 0 and promoted                                                         # the gate uses DRAFT vs audit, which still matches
    assert "DRAFT vs ahora (universo): MISMATCH" in out and "DRAFT vs ahora (fondos de la cartera): MATCH" in out
