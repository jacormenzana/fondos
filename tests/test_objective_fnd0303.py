"""FND-0303: the portfolio objective has one definition (shared/objective.py); the report and the PIT target read it."""
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared import config, objective


def test_spread_is_the_owner_decision():
    assert config.PORTFOLIO_TARGET_SPREAD == 0.02
    assert not hasattr(config, "MIN_REAL_RETURN_TARGET")      # the unused 11% constant is gone


def test_objective_scalar_and_missing_m3():
    assert objective.objective_annual(0.04, 3.5) == pytest.approx(0.075)
    assert objective.objective_annual(0.04, None) == pytest.approx(0.04)       # without M3 only the IPC
    assert objective.objective_annual(None, 3.5) is None
    assert objective.objective_annual(float("nan"), 3.5) is None


def test_objective_series_matches_the_old_pit_formula():
    idx = pd.date_range("2024-01-31", periods=4, freq="ME")
    ipc = pd.Series([0.03, np.nan, 0.04, 0.05], index=idx)
    m3 = pd.Series([4.0, 3.0, np.nan, 2.0], index=idx)
    old = (ipc + (m3 / 100.0).fillna(0.0)).where(ipc.notna())
    pd.testing.assert_series_equal(objective.objective_annual(ipc, m3), old)
    pd.testing.assert_series_equal(objective.objective_annual(ipc, None), ipc.where(ipc.notna()))


def test_aspiration_and_monthly_equivalent():
    base = objective.objective_annual(0.0436, 3.49)
    assert objective.aspiration_annual(base) == pytest.approx(base + 0.02)
    assert objective.aspiration_annual(None) is None
    m = objective.monthly_equivalent(0.0785)
    assert (1 + m) ** 12 - 1 == pytest.approx(0.0785)
    assert objective.monthly_equivalent(None) is None


def test_yoy_from_levels_guards():
    assert objective.yoy_from_levels(105.4, 100.42) == pytest.approx(0.04959, abs=1e-4)
    assert objective.yoy_from_levels(None, 100.0) is None
    assert objective.yoy_from_levels(100.0, None) is None
    assert objective.yoy_from_levels(100.0, 0.0) is None


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _FakeConn:
    """Answers the three queries of _get_objetivo_ipc_m3 from a dict of index levels."""

    def __init__(self, levels, m3=3.5, drop=()):
        self.levels, self.m3, self.drop = levels, m3, set(drop)

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if "MAX(date)" in s:
            return _Res([(max(d for (g, d) in self.levels if g == "ES"),)])
        if "m3_yoy" in s:
            return _Res([(self.m3,)] if self.m3 is not None else [])
        last, = {params[0]}
        ago = last.replace(year=last.year - 1)
        return _Res([(g, d, v) for (g, d), v in self.levels.items() if d in (last, ago) and (g, d) not in self.drop])


def _levels():
    return {("ES", dt.date(2025, 9, 1)): 100.42, ("ES", dt.date(2026, 9, 1)): 105.40,
            ("EU", dt.date(2025, 9, 1)): 130.00, ("EU", dt.date(2026, 9, 1)): 134.31,
            # a month in between: a row-offset implementation would pick it up if rows were missing
            ("ES", dt.date(2026, 8, 1)): 104.74}


def test_report_target_is_ipc_mean_plus_m3_with_a_date_based_base():
    from proyecto3.src.monthly_report import _get_objetivo_ipc_m3
    out = _get_objetivo_ipc_m3(_FakeConn(_levels()))
    es, eu = 105.40 / 100.42 - 1, 134.31 / 130.00 - 1
    assert out["ipc_yoy"] == pytest.approx((es + eu) / 2)
    assert out["objetivo_anual"] == pytest.approx((es + eu) / 2 + 0.035)
    assert out["aspiracion_anual"] == pytest.approx(out["objetivo_anual"] + config.PORTFOLIO_TARGET_SPREAD)
    assert (1 + out["objetivo_mensual"]) ** 12 - 1 == pytest.approx(out["objetivo_anual"])


def test_report_target_does_not_shift_the_base_when_a_year_ago_row_is_missing():
    """The old LIMIT 1 OFFSET 12 on ES rows picked whatever row sat 12 rows back; now a missing base month drops that geography."""
    from proyecto3.src.monthly_report import _get_objetivo_ipc_m3
    out = _get_objetivo_ipc_m3(_FakeConn(_levels(), drop=[("EU", dt.date(2025, 9, 1))]))
    assert out["ipc_yoy"] == pytest.approx(105.40 / 100.42 - 1)                # ES alone, not a mis-based EU


def test_report_target_without_m3_or_ipc():
    from proyecto3.src.monthly_report import _get_objetivo_ipc_m3
    out = _get_objetivo_ipc_m3(_FakeConn(_levels(), m3=None))
    assert out["objetivo_anual"] == pytest.approx(out["ipc_yoy"])
    gone = _get_objetivo_ipc_m3(_FakeConn(_levels(), drop=[("ES", dt.date(2025, 9, 1)), ("EU", dt.date(2025, 9, 1))]))
    assert gone["ipc_yoy"] is None and gone["objetivo_anual"] is None and gone["aspiracion_anual"] is None
