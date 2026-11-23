"""Package L: the owner's attainable NET band (3.2 %-4.0 % a year, 15 % drawdown cap kept) is reported NEXT TO IPC+M3, never instead of it.

The number is the owner's decision (2026-11-23), not derived from any run: shared/config.py holds it, shared/objective.py classifies a net
annual return against it, the monthly report cover and the PIT summary print it (P#11: one definition, two consumers).
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared import config, objective


def test_band_is_the_owner_decision():
    assert config.PORTFOLIO_NET_BAND_LOW == 0.032
    assert config.PORTFOLIO_NET_BAND_HIGH == 0.040
    assert config.PORTFOLIO_NET_BAND_DD_CAP == 0.15            # the cap is kept, only printed next to the band
    assert config.PORTFOLIO_NET_BAND_LOW < config.PORTFOLIO_NET_BAND_HIGH
    assert config.PORTFOLIO_TARGET_SPREAD == 0.02              # the IPC+M3 reference is untouched


def test_band_position_edges_are_inside():
    assert objective.net_band_position(0.0312) == "BELOW"
    assert objective.net_band_position(0.032) == "INSIDE"
    assert objective.net_band_position(0.036) == "INSIDE"
    assert objective.net_band_position(0.040) == "INSIDE"
    assert objective.net_band_position(0.0401) == "ABOVE"
    assert objective.net_band_position(-0.02) == "BELOW"


def test_band_position_unknown_stays_none():
    assert objective.net_band_position(None) is None
    assert objective.net_band_position(float("nan")) is None


def test_band_text_reads_the_config():
    assert objective.net_band_text() == "3.2%-4.0%"


def _portada(monkeypatch):
    openpyxl = pytest.importorskip("openpyxl")
    from proyecto3.src import monthly_report as mr
    reg = SimpleNamespace(regime="Expansion", ipc_yoy_avg=0.03, cli_eu=101.0, rate_deposit=2.0, oil_yoy=0.01,
                          weight_defensive=0.2, weight_balanced=0.45, weight_dynamic=0.35)
    clf = SimpleNamespace(classify_current=lambda: reg, semaforo=lambda: SimpleNamespace(color="VERDE", signals=[]))
    monkeypatch.setattr(mr, "_scenario_exposure", lambda conn, sid: None)
    monkeypatch.setattr(mr._lifecycle, "declared_cash", lambda conn, sid: 0.0)
    monkeypatch.setattr(mr, "_get_objetivo_ipc_m3", lambda conn: {
        "ipc_yoy": 0.03, "m3_yoy": 0.035, "objetivo_anual": 0.065, "objetivo_mensual": 0.0053,
        "aspiracion_anual": 0.085, "aspiracion_mensual": 0.0068})
    ws = openpyxl.Workbook().active
    mr._build_portada(ws, None, clf, "cartera_x", True)
    return {ws.cell(r, 1).value: ws.cell(r, 2).value for r in range(1, ws.max_row + 1) if ws.cell(r, 1).value}


def test_cover_shows_the_band_next_to_ipc_m3(monkeypatch):
    rows = _portada(monkeypatch)
    assert "Objetivo IPC+M3" in rows and rows["Objetivo IPC+M3"].startswith("6.5%")      # added, not replaced
    band = [v for k, v in rows.items() if k.startswith("Banda neta")]
    assert len(band) == 1
    assert "3.2%-4.0%" in band[0] and "15%" in band[0]                                 # the drawdown cap travels with it


def test_cover_band_row_is_labelled_as_owner_decision(monkeypatch):
    rows = _portada(monkeypatch)
    label = next(k for k in rows if k.startswith("Banda neta"))
    assert "propietario" in label.lower()


def test_pit_band_line_positions_the_simulated_return():
    from proyecto3.src.backtesting import _band_line
    inside = _band_line(0.036, net_of_turnover=True)
    assert "3.2%-4.0%" in inside and "DENTRO" in inside and "3.6%" in inside and "neta de la rotacion" in inside
    assert "DENTRO" in _band_line(0.0344, net_of_turnover=True)                           # the model's 2005-2026 PIT figure
    assert "POR DEBAJO" in _band_line(0.0079, net_of_turnover=True)
    assert "POR ENCIMA" in _band_line(0.0694, net_of_turnover=True)
    gross = _band_line(0.036, net_of_turnover=False)
    assert "sin coste de rotacion" in gross and "neta de la rotacion" not in gross


def test_pit_band_line_with_unknown_return_says_so():
    from proyecto3.src.backtesting import _band_line
    for bad in (None, float("nan")):
        line = _band_line(bad, net_of_turnover=True)
        assert "N/D" in line and not any(w in line for w in ("DENTRO", "POR DEBAJO", "POR ENCIMA"))
