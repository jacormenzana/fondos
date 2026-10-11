# proyecto3/tests/test_scenario_lifecycle.py
# -*- coding: utf-8 -*-
"""Scenario lifecycle (DRAFT / PRODUCTION / ARCHIVED), pure parts + the promotion CLI with fakes (R-7: no DB, no pipeline imports)."""
import sys
from datetime import datetime, timezone
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

from shared.statistical_audit.gating import AuditScope, DefectFinding  # noqa: E402

AUDIT_AT = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
BUILT_AT = datetime(2026, 10, 11, 11, 14, tzinfo=timezone.utc)      # the DRAFT was built BEFORE the audit ran


def _scope(**over):
    base = dict(run_id="run1", run_ts=AUDIT_AT)
    base.update(over)
    return AuditScope(**base)


def _ok(**over):
    base = dict(status="DRAFT", n_funds=30, weight_sum=1.0, stale_checks=[], exposure_non_eur=0.2, budget=0.3,
                audit=_scope(), built_at=BUILT_AT)
    base.update(over)
    return lc.promotion_blockers(**base)


def _verdict(**over):
    base = dict(status="DRAFT", n_funds=30, weight_sum=1.0, stale_checks=[], exposure_non_eur=0.2, budget=0.3,
                audit=_scope(), built_at=BUILT_AT)
    base.update(over)
    return lc.evaluate_promotion(**base)


def _defect(isins=("LU0001",), rule="WINDOW_FISHER_IDENTITY"):
    return DefectFinding(rule, rule, "40/2809 rows violate", tuple(isins))


def test_a_clean_draft_is_promotable():
    assert _ok() == []


@pytest.mark.parametrize("over, code, fragment", [
    (dict(status=None), "STATUS", "no existe"),
    (dict(status="PRODUCTION"), "STATUS", "solo un DRAFT"),
    (dict(status="ARCHIVED"), "STATUS", "solo un DRAFT"),
    (dict(n_funds=0), "WEIGHTS", "no tiene pesos"),
    (dict(weight_sum=0.9), "WEIGHTS", "suman 0.9000"),
    (dict(stale_checks=["nav_p10", "fx_coverage"]), "FRESHNESS", "nav_p10, fx_coverage"),
    (dict(exposure_non_eur=0.791), "FX", "79.1%"),
])
def test_each_gate_blocks_on_its_own_with_its_code(over, code, fragment):
    out = _ok(**over)
    assert len(out) == 1 and fragment in out[0] and out[0].startswith(f"[GATE:{code}]")


def test_the_audit_argument_is_required_so_a_caller_cannot_forget_the_data_gate():
    with pytest.raises(TypeError):
        lc.promotion_blockers(status="DRAFT", n_funds=1, weight_sum=1.0, stale_checks=[], exposure_non_eur=0.0, budget=0.3)


def test_exposure_budget_is_a_hard_gate_with_no_override_parameter():
    import inspect
    assert "accept_exposure" not in inspect.signature(lc.evaluate_promotion).parameters
    out = _ok(exposure_non_eur=0.79, accept_alarms="un motivo cualquiera", accept_ap="FND-0251")
    assert len(out) == 1 and "puerta dura" in out[0]


def test_the_budget_edge_is_inclusive():
    assert _ok(exposure_non_eur=0.30) == []


# ---- data gates (FND-0251) ----

def test_statistical_anomalies_on_the_scenario_never_block():
    anomalies = [(f"LU{i:04d}", "OUTLIER_MAD_Z", "sharpe|since_inception") for i in range(5000)]
    assert _ok(audit=_scope(anomalies_on_scenario=anomalies)) == []


def test_an_open_hard_defect_on_a_scenario_fund_blocks_and_names_the_funds():
    out = _ok(audit=_scope(on_scenario=[_defect(("LU0001", "LU0002"))]))
    assert len(out) == 2                                                    # the defect + the way out
    assert out[0].startswith("[GATE:DATA_SCENARIO]") and "LU0001, LU0002" in out[0] and "WINDOW_FISHER_IDENTITY" in out[0]
    assert "--accept-alarms" in out[1] and "--ap" in out[1]


def test_an_unplaceable_defect_is_assumed_to_touch_the_scenario():
    out = _ok(audit=_scope(on_scenario=[_defect(())]))
    assert out[0].startswith("[GATE:DATA_SCENARIO]") and "sin ISIN atribuible" in out[0]


def test_the_override_needs_a_reason_and_a_ticket_and_the_ticket_must_exist():
    scope = _scope(on_scenario=[_defect()])
    assert _ok(audit=scope, accept_alarms="congelados RU/EE", accept_ap="FND-0250") == []
    assert _ok(audit=scope, accept_alarms="congelados RU/EE", accept_ap="FND-0250", ap_exists=None) == []     # backlog unreachable: warn only
    for reason, ap, exists, code in [("", "FND-0250", True, "DATA_SCENARIO"), ("   ", "FND-0250", True, "DATA_SCENARIO"),
                                     ("why", None, None, "OVERRIDE_AP"), ("why", "0250", None, "OVERRIDE_AP"),
                                     ("why", "FND-0250", False, "OVERRIDE_AP")]:
        out = _ok(audit=scope, accept_alarms=reason, accept_ap=ap, ap_exists=exists)
        assert out and out[0].startswith("[GATE:DATA_SCENARIO]") and any(f"[GATE:{code}]" in b for b in out), (reason, ap, exists)


def test_the_verdict_lists_what_the_override_covered():
    v = _verdict(audit=_scope(on_scenario=[_defect()], held_out_on_scenario=("LU0009",)), accept_alarms="why", accept_ap="FND-0250")
    assert v.blockers == [] and {c for c, _ in v.overridden} == {"DATA_SCENARIO", "DATA_COVERAGE"}


def test_a_universe_only_defect_blocks_and_no_flag_covers_it():
    scope = _scope(universe_only=[_defect(("RU0001", "RU0002"))])
    out = _ok(audit=scope, accept_alarms="why", accept_ap="FND-0250", ap_exists=True)
    assert len(out) == 1 and out[0].startswith("[GATE:DATA_UNIVERSE]") and "accept_audit_residual.py" in out[0] and "sin override" in out[0]


def test_an_active_residual_on_a_scenario_fund_blocks_and_can_be_overridden_with_a_ticket():
    scope = _scope(residuals_on_scenario=[("WINDOW_FISHER_IDENTITY", "LU0001", "FND-0250", datetime(2027, 1, 1).date())])
    out = _ok(audit=scope)
    assert out[0].startswith("[GATE:RESIDUAL_SCENARIO]") and "LU0001" in out[0] and "FND-0250" in out[0]
    assert _ok(audit=scope, accept_alarms="why", accept_ap="FND-0251") == []


def test_the_audit_must_exist_and_be_newer_than_the_build_and_no_flag_covers_it():
    out = _ok(audit=AuditScope(), accept_alarms="why", accept_ap="FND-0251")
    assert len(out) == 1 and out[0].startswith("[GATE:AUDIT_STALE]") and "ninguna auditoria" in out[0]
    old = _scope(run_ts=datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc))
    out = _ok(audit=old, accept_alarms="why", accept_ap="FND-0251")
    assert len(out) == 1 and out[0].startswith("[GATE:AUDIT_STALE]") and "anterior a la construccion" in out[0]
    assert _ok(audit=_scope(run_ts=BUILT_AT)) == []                          # same instant: not older
    assert _ok(audit=old, built_at=None) == []                               # build time unknown (pre-lifecycle DRAFT): the CLI says so


@pytest.mark.parametrize("over, fragment", [
    (dict(coverage="UNKNOWN", coverage_detail="held out 51 but 0 persisted"), "DESCONOCIDA"),
    (dict(held_out_on_scenario=("LU0007", "LU0008")), "2 fondos de la cartera quedaron fuera"),
    (dict(outside_population=("LU0005",)), "ya no estan en el universo auditado"),
])
def test_funds_the_audit_did_not_check_block_with_the_coverage_code_and_take_the_ticket_override(over, fragment):
    scope = _scope(**over)
    out = _ok(audit=scope)
    assert out[0].startswith("[GATE:DATA_COVERAGE]") and fragment in out[0]
    assert _ok(audit=scope, accept_alarms="why", accept_ap="FND-0251") == []


def test_every_blocker_carries_a_gate_code():
    out = _ok(status="ARCHIVED", n_funds=0, stale_checks=["x"], exposure_non_eur=0.9, declared_cash=0.5, max_cash=0.1,
              audit=_scope(on_scenario=[_defect()], universe_only=[_defect(("RU1",))], coverage="UNKNOWN", coverage_detail="d"))
    coded = [b for b in out if b.startswith("[GATE:")]
    assert len(coded) >= 8 and all(b.startswith("[GATE:") or b.startswith("(") for b in out)


def test_the_signoff_line_says_what_was_looked_at_and_what_was_let_through():
    scope = _scope(on_scenario=[_defect(("LU0001",))], universe_only=[_defect(("RU1", "RU2"))],
                   anomalies_on_scenario=[("LU0001", "OUTLIER_MAD_Z", "sharpe|x")] * 3,
                   covered_by_residual=[("WINDOW_FISHER_IDENTITY", "RU3", "FND-0250", datetime(2027, 1, 4).date())])
    line = lc.signoff_line(scope, [("DATA_SCENARIO", "t")], "FND-0251", "motivo claro")
    for fragment in ("audit run1", "defects on scenario 1", "universe-only open 2", "anomalies on scenario 3 (informational)",
                     "covered by residual 1 ISINs (FND-0250, until 2027-01-04)", "[OVERRIDE:DATA_SCENARIO] FND-0251: motivo claro"):
        assert fragment in line, fragment


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
    out = _ok(weight_sum=0.88, declared_cash=0.12, max_cash=0.10, accept_alarms="un motivo no cubre la liquidez", accept_ap="FND-0251")
    assert len(out) == 1
    assert "12.0%" in out[0] and "tope 10%" in out[0] and "sin override" in out[0] and "no expuestos" in out[0]


def test_cash_cap_defaults_to_the_config_constant():
    from shared.config import MAX_PORTFOLIO_CASH_WEIGHT
    over = MAX_PORTFOLIO_CASH_WEIGHT + 0.05
    out = lc.promotion_blockers(status="DRAFT", n_funds=10, weight_sum=1.0 - over, stale_checks=[], exposure_non_eur=0.0,
                                budget=0.30, audit=_scope(), declared_cash=over)
    assert len(out) == 1 and "liquidez estructural" in out[0]


# ---------------- the CLI (fakes) ----------------

class _CliConn(_Conn):
    def __init__(self, scenarios, weights, ccy="USD", notes=None, built_at=BUILT_AT, **kw):
        super().__init__(scenarios, **kw)
        self.weights, self.executed, self.committed, self.ccy, self.notes = weights, [], False, ccy, notes
        self.built_at = built_at

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        self.executed.append(s)
        if s.startswith("SELECT isin, weight FROM portfolio_weights"):
            rows = list(self.weights.items())
            return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: rows)
        if s.startswith("SELECT notes FROM portfolio_scenarios"):
            rows = [(self.notes,)] if self.notes is not None else []
            return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: rows)
        if s.startswith("SELECT built_at FROM portfolio_scenarios"):
            rows = [(self.built_at,)]
            return SimpleNamespace(fetchone=lambda: rows[0], fetchall=lambda: rows)
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


def _run_cli(monkeypatch, conn, argv, scope=None, stale=(), ap_status="OPEN", residual_lines=()):
    import p3_promote_scenario as cli
    import shared.db
    import shared.backlog_client as bc
    import proyecto3.src.data_freshness as df
    import proyecto3.src.regime_classifier as rcl
    from shared.statistical_audit import accepted_residuals as ar, gating
    scope = scope if scope is not None else _scope()
    monkeypatch.setattr(shared.db, "get_connection", lambda: conn)
    monkeypatch.setattr(df, "check_universe_freshness", lambda c, clf: [SimpleNamespace(name=n, ok=False) for n in stale])
    monkeypatch.setattr(df, "stale_checks", lambda checks: list(checks))
    monkeypatch.setattr(rcl, "RegimeClassifier", lambda c: object())
    monkeypatch.setattr(gating, "load_scope", lambda c, domain, isins, accepted=None: scope)
    monkeypatch.setattr(ar, "load_accepted", lambda c, domain, today=None: {})
    monkeypatch.setattr(ar, "attention_lines", lambda *a, **k: list(residual_lines))
    monkeypatch.setattr(bc, "get_action_point_status", lambda ap_id: ap_status)
    monkeypatch.setattr(cli, "_git_state", lambda: "git abc1234 clean")
    promoted = []
    monkeypatch.setattr(lc, "promote", lambda c, sid, user, note="": promoted.append((sid, note)) or "prev")
    return cli.main(argv), promoted


def test_cli_blocks_a_usd_heavy_draft_and_has_no_exposure_override(monkeypatch, capsys):
    conn = _CliConn({"d": "DRAFT"}, {"A": 0.5, "B": 0.5})
    rc, promoted = _run_cli(monkeypatch, conn, ["d"])
    assert rc == 1 and not promoted and "NO PROMOVIBLE" in capsys.readouterr().out
    rc, promoted = _run_cli(monkeypatch, conn, ["d", "--accept-alarms", "no cubre la exposicion", "--ap", "FND-0251"])   # does not help
    assert rc == 1 and not promoted
    with pytest.raises(SystemExit):                                                                  # the flag no longer exists
        _run_cli(monkeypatch, conn, ["d", "--accept-exposure", "decision del comite"])
    assert not promoted


def test_cli_blocked_attempt_leaves_one_audit_row_with_gate_codes_and_dry_run_leaves_none(monkeypatch, capsys):
    conn = _CliConn({"d": "DRAFT"}, {"A": 0.5, "B": 0.5})                           # USD-heavy: blocked by the exposure gate
    rc, promoted = _run_cli(monkeypatch, conn, ["d", "--dry-run"])
    assert rc == 1 and not [s for s in conn.executed if s.startswith("INSERT INTO ingestion_log")]
    rc, promoted = _run_cli(monkeypatch, conn, ["d"])
    inserts = [s for s in conn.executed if s.startswith("INSERT INTO ingestion_log")]
    assert rc == 1 and not promoted and len(inserts) == 1 and conn.committed
    out = capsys.readouterr().out
    assert "NO PROMOVIBLE" in out and "Liquidez estructural declarada" in out and "[GATE:FX]" in out


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


def test_cli_promotes_a_draft_inside_the_budget_and_the_note_carries_the_data_signoff(monkeypatch, capsys):
    conn = _CliConn({"d": "DRAFT"}, {"A": 0.5, "B": 0.5}, ccy="EUR")
    rc, promoted = _run_cli(monkeypatch, conn, ["d"])
    assert rc == 0 and promoted and promoted[0][0] == "d"
    note = promoted[0][1]
    assert "OVERRIDE" not in note and "audit run1" in note and "git abc1234 clean" in note and "non-EUR exposure" in note


def test_cli_dry_run_changes_nothing_and_missing_scenario_exits_3(monkeypatch, capsys):
    conn = _CliConn({"d": "DRAFT"}, {"A": 1.0}, ccy="EUR")
    rc, promoted = _run_cli(monkeypatch, conn, ["d", "--dry-run"])
    assert rc == 0 and not promoted and "DRY RUN" in capsys.readouterr().out
    rc, _ = _run_cli(monkeypatch, _CliConn({}, {}), ["nope"])
    assert rc == lc.EXIT_NO_SCENARIO


def test_cli_refuses_without_the_lifecycle_ddl(monkeypatch, capsys):
    rc, promoted = _run_cli(monkeypatch, _CliConn({"d": "DRAFT"}, {"A": 1.0}, column=False), ["d"])
    assert rc == lc.EXIT_NO_SCENARIO and not promoted and "migrate_portfolio_scenario_status" in capsys.readouterr().out


# ---- the data gates through the CLI (FND-0251) ----

def test_cli_lists_anomalies_on_the_scenario_but_they_do_not_block(monkeypatch, capsys):
    scope = _scope(anomalies_on_scenario=[("A", "OUTLIER_MAD_Z", "sharpe_slope|1y"), ("A", "OUTLIER_MAD_Z", "sortino|3y")])
    rc, promoted = _run_cli(monkeypatch, _CliConn({"d": "DRAFT"}, {"A": 0.5, "B": 0.5}, ccy="EUR"), ["d"], scope=scope)
    out = capsys.readouterr().out
    assert rc == 0 and promoted and "no bloquean" in out and "A: 2 (sharpe_slope, sortino)" in out


def test_cli_blocks_a_defect_on_a_scenario_fund_and_the_override_needs_the_ticket(monkeypatch, capsys):
    conn = _CliConn({"d": "DRAFT"}, {"A": 0.5, "B": 0.5}, ccy="EUR")
    scope = _scope(on_scenario=[_defect(("A",))])
    rc, promoted = _run_cli(monkeypatch, conn, ["d"], scope=scope)
    assert rc == 1 and not promoted and "[GATE:DATA_SCENARIO]" in capsys.readouterr().out
    rc, promoted = _run_cli(monkeypatch, conn, ["d", "--accept-alarms", "falso positivo FND-0234"], scope=scope)   # reason, no ticket
    assert rc == 1 and not promoted and "[GATE:OVERRIDE_AP]" in capsys.readouterr().out
    rc, promoted = _run_cli(monkeypatch, conn, ["d", "--accept-alarms", "falso positivo FND-0234", "--ap", "FND-0234"], scope=scope)
    out = capsys.readouterr().out
    assert rc == 0 and promoted and "[OVERRIDE:DATA_SCENARIO] FND-0234" in out
    assert "[OVERRIDE:DATA_SCENARIO] FND-0234: falso positivo FND-0234" in promoted[0][1]


def test_cli_refuses_an_override_whose_ticket_is_not_in_the_backlog_but_only_warns_when_it_cannot_ask(monkeypatch, capsys):
    conn = _CliConn({"d": "DRAFT"}, {"A": 0.5, "B": 0.5}, ccy="EUR")
    scope = _scope(on_scenario=[_defect(("A",))])
    argv = ["d", "--accept-alarms", "motivo", "--ap", "FND-9999"]
    rc, promoted = _run_cli(monkeypatch, conn, argv, scope=scope, ap_status="NOT_FOUND")
    assert rc == 1 and not promoted and "no existe en gestion.backlog" in capsys.readouterr().out
    rc, promoted = _run_cli(monkeypatch, conn, argv, scope=scope, ap_status="LOOKUP_FAILED")
    assert rc == 0 and promoted and "no se pudo comprobar FND-9999" in capsys.readouterr().out


def test_cli_universe_only_defect_blocks_even_with_an_override_and_names_the_way_out(monkeypatch, capsys):
    scope = _scope(universe_only=[_defect(("RU1",))])
    rc, promoted = _run_cli(monkeypatch, _CliConn({"d": "DRAFT"}, {"A": 1.0}, ccy="EUR"),
                            ["d", "--accept-alarms", "motivo", "--ap", "FND-0250"], scope=scope)
    out = capsys.readouterr().out
    assert rc == 1 and not promoted and "[GATE:DATA_UNIVERSE]" in out and "accept_audit_residual.py" in out


def test_cli_a_covered_universe_defect_does_not_block_and_shows_in_the_signoff(monkeypatch, capsys):
    scope = _scope(covered_by_residual=[("WINDOW_FISHER_IDENTITY", "RU1", "FND-0250", datetime(2027, 1, 4).date())])
    rc, promoted = _run_cli(monkeypatch, _CliConn({"d": "DRAFT"}, {"A": 1.0}, ccy="EUR"), ["d"], scope=scope)
    assert rc == 0 and promoted and "covered by residual 1 ISINs (FND-0250, until 2027-01-04)" in promoted[0][1]


def test_cli_a_stale_audit_blocks_and_no_flag_helps(monkeypatch, capsys):
    scope = _scope(run_ts=datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc))
    rc, promoted = _run_cli(monkeypatch, _CliConn({"d": "DRAFT"}, {"A": 1.0}, ccy="EUR"),
                            ["d", "--accept-alarms", "motivo", "--ap", "FND-0251"], scope=scope)
    assert rc == 1 and not promoted and "[GATE:AUDIT_STALE]" in capsys.readouterr().out


def test_cli_prints_the_residual_expiry_warnings_even_in_a_dry_run(monkeypatch, capsys):
    rc, _ = _run_cli(monkeypatch, _CliConn({"d": "DRAFT"}, {"A": 1.0}, ccy="EUR"), ["d", "--dry-run"],
                     residual_lines=["residual aceptado WINDOW_FISHER_IDENTITY (6 ISINs, FND-0250) caduca el 2026-10-30 (19 dias)"])
    assert rc == 0 and "AVISO: residual aceptado WINDOW_FISHER_IDENTITY" in capsys.readouterr().out
