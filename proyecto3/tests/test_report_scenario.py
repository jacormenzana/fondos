# proyecto3/tests/test_report_scenario.py
# -*- coding: utf-8 -*-
"""generate_report follows the scenario lifecycle: PRODUCTION by default, fail-closed, non-production flagged and kept apart; the build id
is unique. The sheet builders are replaced by recorders (the cover/cartera SQL has its own coverage); R-7: no DB."""
import sys
from datetime import date
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "launch")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytest.importorskip("openpyxl")
from proyecto3.src import monthly_report as mr  # noqa: E402
from proyecto3.src import scenario_lifecycle as lc  # noqa: E402


@pytest.fixture
def report(monkeypatch, tmp_path):
    calls = {"portada": [], "cartera": [], "rotacion": []}
    monkeypatch.setattr(mr, "RegimeClassifier", lambda conn: object())
    monkeypatch.setattr(mr, "_build_portada", lambda ws, conn, clf, sid, is_prod=True: calls["portada"].append((sid, is_prod)))
    monkeypatch.setattr(mr, "_build_cartera", lambda ws, conn, sid: calls["cartera"].append(sid))
    monkeypatch.setattr(mr, "_build_regimen", lambda ws, clf: None)
    monkeypatch.setattr(mr, "_build_backtesting", lambda ws, conn: None)
    monkeypatch.setattr(mr, "_build_rotacion", lambda ws, conn, sid: calls["rotacion"].append(sid))
    monkeypatch.setattr(mr, "_build_macro_indicadores", lambda ws, conn: None)
    return calls, tmp_path


def test_default_reports_the_production_scenario_with_the_official_name(monkeypatch, report):
    calls, out = report
    monkeypatch.setattr(lc, "resolve_report_scenario", lambda conn, explicit=None: ("prod_1", True))
    path = mr.generate_report(object(), output_dir=str(out))
    assert path.name == f"informe_cartera_{date.today():%Y%m%d}.xlsx" and path.exists()
    assert path.parent == out                                               # the official report sits in the output directory itself
    assert calls["portada"] == [("prod_1", True)] and calls["cartera"] == ["prod_1"] and calls["rotacion"] == ["prod_1"]


def test_explicit_non_production_scenario_is_flagged_and_does_not_overwrite_the_official_report(monkeypatch, report):
    calls, out = report
    monkeypatch.setattr(lc, "resolve_report_scenario", lambda conn, explicit=None: (explicit, False))
    path = mr.generate_report(object(), output_dir=str(out), scenario_id="cartera_x_b1")
    assert path.name == f"informe_cartera_{date.today():%Y%m%d}_cartera_x_b1.xlsx" and path.exists()
    assert path.parent == out / mr.DRAFTS_SUBDIR                            # never next to the official report
    assert calls["portada"] == [("cartera_x_b1", False)]


def test_no_production_scenario_fails_closed_before_creating_anything(monkeypatch, report):
    calls, out = report
    target = out / "nueva"

    def _none(conn, explicit=None):
        raise lc.NoProductionScenario("sin escenario en PRODUCCION")
    monkeypatch.setattr(lc, "resolve_report_scenario", _none)
    with pytest.raises(lc.NoProductionScenario):
        mr.generate_report(object(), output_dir=str(target))
    assert not target.exists() and not any(calls.values())


def test_the_hard_wired_default_is_gone():
    import inspect
    sig = inspect.signature(mr.generate_report)
    assert sig.parameters["scenario_id"].default is None
    assert "shock_energia_2026Q1" not in inspect.getsource(mr)


def test_build_default_id_is_unique_per_build_and_never_the_monthly_collision():
    import p3_build_portfolio as b
    from datetime import datetime
    assert b._default_scenario_id("Shock_Energetico", date(2026, 10, 11)).startswith("cartera_shock_energetico_")
    ids = {lc.build_scenario_id("Shock_Energetico", datetime(2026, 10, 11, 0, 0, s)) for s in range(5)}
    assert len(ids) == 5
