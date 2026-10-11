# proyecto3/tests/test_scenario_lifecycle.py
# -*- coding: utf-8 -*-
"""Scenario lifecycle (DRAFT / PRODUCTION / ARCHIVED), pure parts + the promotion CLI with fakes (R-7: no DB, no pipeline imports)."""
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "launch")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proyecto3.src import scenario_lifecycle as lc  # noqa: E402


# ---------------- ids ----------------

def test_scenario_id_is_unique_per_build_and_keeps_the_legacy_prefix():
    a = lc.build_scenario_id("Shock_Energetico", datetime(2026, 10, 11, 0, 0, 11))
    b = lc.build_scenario_id("Shock_Energetico", datetime(2026, 10, 11, 0, 0, 12))
    assert a == "cartera_shock_energetico_202610_b20261011000011"
    assert a != b and a.startswith("cartera_shock_energetico_202610")


def test_scenario_id_slug_is_filesystem_and_sql_safe():
    assert lc.build_scenario_id("Crisis Financiera / EU!", datetime(2026, 1, 2, 3, 4, 5)) == "cartera_crisis_financiera_eu_202601_b20260102030405"
    assert lc.build_scenario_id("", datetime(2026, 1, 2, 3, 4, 5)).startswith("cartera_desconocido_")


# ---------------- resolution (fake connection) ----------------

class _Conn:
    """Answers the few queries of the lifecycle module from a dict {scenario_id: status}; `column=False` = DDL not applied."""

    def __init__(self, scenarios, column=True):
        self.scenarios, self.column = scenarios, column

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        rows = []
        if "FROM pg_attribute" in s:
            rows = [(1,)] if self.column else []
        elif s.startswith("SELECT status FROM portfolio_scenarios WHERE scenario_id"):
            rows = [(self.scenarios[params[0]],)] if params[0] in self.scenarios else []
        elif s.startswith("SELECT 1 FROM portfolio_scenarios WHERE scenario_id"):
            rows = [(1,)] if params[0] in self.scenarios else []
        elif "WHERE status = 'PRODUCTION' AND scenario_id <>" in s:
            rows = [(k,) for k, v in self.scenarios.items() if v == "PRODUCTION" and k != (params[0] or "")]
        elif "WHERE status = 'DRAFT'" in s:
            rows = [(k,) for k, v in sorted(self.scenarios.items(), reverse=True) if v == "DRAFT"][:params[0]]
        return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: rows)


def test_report_reads_production_by_default():
    sid, prod = lc.resolve_report_scenario(_Conn({"old": "ARCHIVED", "p": "PRODUCTION", "d": "DRAFT"}))
    assert (sid, prod) == ("p", True)


def test_no_production_fails_closed_and_names_the_drafts():
    with pytest.raises(lc.NoProductionScenario) as e:
        lc.resolve_report_scenario(_Conn({"d1": "DRAFT", "d2": "DRAFT", "a": "ARCHIVED"}))
    assert "p3_promote_scenario.py" in str(e.value) and "d2" in str(e.value) and "--scenario" in str(e.value)


def test_without_the_ddl_the_report_fails_closed_unless_a_scenario_is_explicit():
    c = _Conn({"legacy": "ignored"}, column=False)
    with pytest.raises(lc.NoProductionScenario, match="migrate_portfolio_scenario_status"):
        lc.resolve_report_scenario(c)
    assert lc.resolve_report_scenario(c, "legacy") == ("legacy", False)


def test_explicit_scenario_is_flagged_non_production_and_must_exist():
    c = _Conn({"p": "PRODUCTION", "d": "DRAFT"})
    assert lc.resolve_report_scenario(c, "d") == ("d", False)
    assert lc.resolve_report_scenario(c, "p") == ("p", True)
    with pytest.raises(lc.NoProductionScenario, match="no existe"):
        lc.resolve_report_scenario(c, "nope")


# ---------------- promotion gates ----------------

def _ok(**over):
    base = dict(status="DRAFT", n_funds=30, weight_sum=1.0, stale_checks=[], exposure_non_eur=0.2, budget=0.3, alarms=0)
    base.update(over)
    return lc.promotion_blockers(**base)


def test_a_clean_draft_is_promotable():
    assert _ok() == []


@pytest.mark.parametrize("over, fragment", [
    (dict(status=None), "no existe"),
    (dict(status="PRODUCTION"), "solo un DRAFT"),
    (dict(status="ARCHIVED"), "solo un DRAFT"),
    (dict(n_funds=0), "no tiene pesos"),
    (dict(weight_sum=0.9), "suman 0.9000"),
    (dict(stale_checks=["nav_p10", "fx_coverage"]), "nav_p10, fx_coverage"),
    (dict(exposure_non_eur=0.791), "79.1%"),
    (dict(alarms=3), "3 hallazgos ALARM"),
])
def test_each_gate_blocks_on_its_own(over, fragment):
    out = _ok(**over)
    assert len(out) == 1 and fragment in out[0]


def test_alarm_override_needs_a_non_blank_reason():
    assert _ok(alarms=2, accept_alarms="congelados RU/EE, FND-0041") == []
    assert len(_ok(alarms=2, accept_alarms="")) == 1
    assert len(_ok(alarms=2, accept_alarms="   ")) == 1


def test_exposure_budget_is_a_hard_gate_with_no_override_parameter():
    import inspect
    assert "accept_exposure" not in inspect.signature(lc.promotion_blockers).parameters
    out = _ok(exposure_non_eur=0.79, accept_alarms="un motivo cualquiera")
    assert len(out) == 1 and "puerta dura" in out[0]


def test_the_budget_edge_is_inclusive():
    assert _ok(exposure_non_eur=0.30) == []


# ---------------- declared structural cash (owner decision 2026-10-11: declared, capped at 10%, hard gate) ----------------

@pytest.mark.parametrize("notes, expected", [
    ('{"cash_weight": 0.04, "regime": "x"}', 0.04),
    ('{"regime": "x"}', 0.0),                      # scenario built before the declaration existed
    (None, 0.0), ("", 0.0), ("not json", 0.0), ('{"cash_weight": "n/a"}', 0.0), ('{"cash_weight": -0.2}', 0.0), ("[1, 2]", 0.0),
])
def test_declared_cash_is_read_from_the_notes_and_never_negative(notes, expected):
    assert lc.parse_declared_cash(notes) == expected


def test_declared_cash_completes_the_weights_up_to_the_cap():
    assert _ok(weight_sum=0.95, declared_cash=0.05, max_cash=0.10) == []
    assert _ok(weight_sum=0.90, declared_cash=0.10, max_cash=0.10) == []            # the cap edge is inclusive
    # an UNDECLARED shortfall is still an error, with the old text
    out = _ok(weight_sum=0.95, declared_cash=0.0, max_cash=0.10)
    assert len(out) == 1 and "suman 0.9500, no 1.0" in out[0]
    # declared cash that does not complete the weights is also an error (the declaration cannot hide a missing position)
    out = _ok(weight_sum=0.90, declared_cash=0.05, max_cash=0.10)
    assert len(out) == 1 and "liquidez declarada 0.0500" in out[0]


def test_cash_above_the_cap_is_a_hard_gate_with_the_way_out_in_the_message():
    out = _ok(weight_sum=0.88, declared_cash=0.12, max_cash=0.10, accept_alarms="un motivo no cubre la liquidez")
    assert len(out) == 1
    assert "12.0%" in out[0] and "tope 10%" in out[0] and "sin override" in out[0] and "no expuestos" in out[0]


def test_cash_cap_defaults_to_the_config_constant():
    from shared.config import MAX_PORTFOLIO_CASH_WEIGHT
    over = MAX_PORTFOLIO_CASH_WEIGHT + 0.05
    out = lc.promotion_blockers(status="DRAFT", n_funds=10, weight_sum=1.0 - over, stale_checks=[], exposure_non_eur=0.0,
                                budget=0.30, alarms=0, declared_cash=over)
    assert len(out) == 1 and "liquidez estructural" in out[0]


# ---------------- the CLI (fakes) ----------------

class _CliConn(_Conn):
    def __init__(self, scenarios, weights, ccy="USD", notes=None, **kw):
        super().__init__(scenarios, **kw)
        self.weights, self.executed, self.committed, self.ccy, self.notes = weights, [], False, ccy, notes

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        self.executed.append(s)
        if s.startswith("SELECT isin, weight FROM portfolio_weights"):
            rows = list(self.weights.items())
            return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: rows)
        if s.startswith("SELECT notes FROM portfolio_scenarios"):
            rows = [(self.notes,)] if self.notes is not None else []
            return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: rows)
        if s.startswith("SELECT isin, fund_currency, hedging_policy, asset_currency FROM fund_master"):
            rows = [(i, self.ccy, "Unhedged", self.ccy) for i in params[0]]
            return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: rows)
        return super().execute(sql, params)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


def _run_cli(monkeypatch, conn, argv, alarms=(None, None, 0), stale=()):
    import p3_promote_scenario as cli
    import shared.db
    import proyecto3.src.data_freshness as df
    import proyecto3.src.regime_classifier as rcl
    monkeypatch.setattr(shared.db, "get_connection", lambda: conn)
    monkeypatch.setattr(df, "check_universe_freshness", lambda c, clf: [SimpleNamespace(name=n, ok=False) for n in stale])
    monkeypatch.setattr(df, "stale_checks", lambda checks: list(checks))
    monkeypatch.setattr(rcl, "RegimeClassifier", lambda c: object())
    monkeypatch.setattr(cli, "latest_alarms", lambda c, domain=cli.AUDIT_DOMAIN: alarms)
    promoted = []
    monkeypatch.setattr(lc, "promote", lambda c, sid, user, note="": promoted.append((sid, note)) or "prev")
    return cli.main(argv), promoted


def test_cli_blocks_a_usd_heavy_draft_and_has_no_exposure_override(monkeypatch, capsys):
    conn = _CliConn({"d": "DRAFT"}, {"A": 0.5, "B": 0.5})
    rc, promoted = _run_cli(monkeypatch, conn, ["d"])
    assert rc == 1 and not promoted and "NO PROMOVIBLE" in capsys.readouterr().out
    rc, promoted = _run_cli(monkeypatch, conn, ["d", "--accept-alarms", "no cubre la exposicion"])   # the other override does not help
    assert rc == 1 and not promoted
    with pytest.raises(SystemExit):                                                                  # the flag no longer exists
        _run_cli(monkeypatch, conn, ["d", "--accept-exposure", "decision del comite"])
    assert not promoted


def test_cli_blocked_attempt_leaves_one_audit_row_and_dry_run_leaves_none(monkeypatch, capsys):
    conn = _CliConn({"d": "DRAFT"}, {"A": 0.5, "B": 0.5})                           # USD-heavy: blocked by the exposure gate
    rc, promoted = _run_cli(monkeypatch, conn, ["d", "--dry-run"])
    assert rc == 1 and not [s for s in conn.executed if s.startswith("INSERT INTO ingestion_log")]
    rc, promoted = _run_cli(monkeypatch, conn, ["d"])
    inserts = [s for s in conn.executed if s.startswith("INSERT INTO ingestion_log")]
    assert rc == 1 and not promoted and len(inserts) == 1 and conn.committed
    out = capsys.readouterr().out
    assert "NO PROMOVIBLE" in out and "Liquidez estructural declarada" in out


def test_cli_refusal_stands_even_if_the_audit_write_fails(monkeypatch, capsys):
    conn = _CliConn({"d": "DRAFT"}, {"A": 0.5, "B": 0.5})
    monkeypatch.setattr(lc, "record_blocked", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone")))
    rc, promoted = _run_cli(monkeypatch, conn, ["d"])
    assert rc == 1 and not promoted and "no se pudo registrar" in capsys.readouterr().out


def test_cli_declared_cash_inside_the_cap_promotes_and_above_it_does_not(monkeypatch, capsys):
    ok = _CliConn({"d": "DRAFT"}, {"A": 0.5, "B": 0.45}, ccy="EUR", notes='{"cash_weight": 0.05}')   # 0.95 + 0.05 declared
    rc, promoted = _run_cli(monkeypatch, ok, ["d"])
    assert rc == 0 and promoted
    capsys.readouterr()
    low = _CliConn({"d": "DRAFT"}, {"A": 0.4, "B": 0.4}, ccy="EUR", notes='{"cash_weight": 0.20}')    # 0.80 + 0.20 declared > 10% cap
    rc, promoted = _run_cli(monkeypatch, low, ["d"])
    out = capsys.readouterr().out
    assert rc == 1 and not promoted and "excede el tope" in out
    assert any(s.startswith("INSERT INTO ingestion_log") for s in low.executed)


def test_cli_promotes_a_draft_inside_the_budget(monkeypatch, capsys):
    conn = _CliConn({"d": "DRAFT"}, {"A": 0.5, "B": 0.5}, ccy="EUR")
    rc, promoted = _run_cli(monkeypatch, conn, ["d"])
    assert rc == 0 and promoted and promoted[0][0] == "d" and "OVERRIDES" not in promoted[0][1]


def test_cli_dry_run_changes_nothing_and_missing_scenario_exits_3(monkeypatch, capsys):
    conn = _CliConn({"d": "DRAFT"}, {"A": 1.0}, ccy="EUR")
    rc, promoted = _run_cli(monkeypatch, conn, ["d", "--dry-run"])
    assert rc == 0 and not promoted and "DRY RUN" in capsys.readouterr().out
    rc, _ = _run_cli(monkeypatch, _CliConn({}, {}), ["nope"])
    assert rc == lc.EXIT_NO_SCENARIO


def test_cli_refuses_without_the_lifecycle_ddl(monkeypatch, capsys):
    rc, promoted = _run_cli(monkeypatch, _CliConn({"d": "DRAFT"}, {"A": 1.0}, column=False), ["d"])
    assert rc == lc.EXIT_NO_SCENARIO and not promoted and "migrate_portfolio_scenario_status" in capsys.readouterr().out
