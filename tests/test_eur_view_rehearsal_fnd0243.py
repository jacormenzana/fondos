# tests/test_eur_view_rehearsal_fnd0243.py
# -*- coding: utf-8 -*-
"""FND-0243 step 6 tool: the rehearsal compares off/on metrics through the production reader, puts the switch back, and refuses
to run without ECB rates. Fakes only (R-7): load_nav / load_ipc replaced, real compute_risk_metrics."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_REPO = Path(__file__).resolve().parents[1]
for _p in (str(_REPO), str(_REPO / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared import config  # noqa: E402
import eur_view_rehearsal as rh  # noqa: E402

IDX = pd.date_range("2018-01-31", periods=72, freq="ME")
NAV = 100 * np.exp(0.005 * np.arange(72) + 0.02 * np.sin(np.arange(72)))
RATE = 1.10 * np.exp(0.004 * np.arange(72))


def _fake_load_nav(conn, isin):
    nav = NAV if isin == "E1" or not config.EUR_NAV_CONVERSION_ENABLED else NAV / RATE
    df = pd.DataFrame({"date": IDX, "nav": nav})
    df.attrs["eur_status"] = ("EUR" if isin == "E1" else "CONVERTED") if config.EUR_NAV_CONVERSION_ENABLED else "DISABLED"
    return df


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setattr(rh, "load_nav", _fake_load_nav)
    monkeypatch.setattr(rh, "load_ipc", lambda conn: pd.DataFrame(columns=["date", "ipc_index"]))
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", False, raising=False)


def test_eur_control_does_not_move_and_a_usd_class_does(fakes):
    df = rh.compare(None, ["E1", "U1"], {"E1": "EUR", "U1": "USD"})
    eur = df[df["isin"] == "E1"]
    usd = df[(df["isin"] == "U1") & (df["metric"] == "return_ann")]
    assert not eur.empty and eur["diff"].abs().max() == 0
    assert usd["diff"].iloc[0] == pytest.approx((1 + (0.005 - 0.004)) ** 12 - 1 - ((1 + 0.005) ** 12 - 1), abs=2e-3)
    assert set(df[df["isin"] == "U1"]["status"]) == {"CONVERTED"}
    assert "EUR control group: max |diff| = 0" in rh.summarize(df)


def test_the_switch_is_put_back_even_when_a_fund_fails(fakes, monkeypatch):
    def boom(conn, isin):
        if config.EUR_NAV_CONVERSION_ENABLED:
            raise RuntimeError("x")
        return _fake_load_nav(conn, isin)
    monkeypatch.setattr(rh, "load_nav", boom)
    with pytest.raises(RuntimeError):
        rh.compare(None, ["U1"], {"U1": "USD"})
    assert config.EUR_NAV_CONVERSION_ENABLED is False


def test_refuses_without_ecb_rates(monkeypatch, capsys):
    class _C:
        def execute(self, sql, params=None):
            return type("R", (), {"fetchone": lambda s: (0,), "fetchall": lambda s: []})()
        def rollback(self): pass
        def close(self): pass
    monkeypatch.setattr(rh, "get_connection", lambda: _C())
    assert rh.main(["--sample"]) == rh.RC_NO_RATES
    assert "macro_discovery --source bce" in capsys.readouterr().out


def test_more_than_the_sample_size_is_rejected(monkeypatch):
    class _C:
        def execute(self, sql, params=None):
            return type("R", (), {"fetchone": lambda s: (5,), "fetchall": lambda s: []})()
        def rollback(self): pass
        def close(self): pass
    monkeypatch.setattr(rh, "get_connection", lambda: _C())
    with pytest.raises(SystemExit):
        rh.main(["--isin", ",".join(f"X{i}" for i in range(rh.SAMPLE_SIZE + 1))])
