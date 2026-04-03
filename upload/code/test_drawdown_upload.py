import pandas as pd
import numpy as np

from src.calculations.drawdown import (
    compute_drawdown,
    max_drawdown,
    drawdown_duration,
    time_to_recovery,
)


def test_max_drawdown_simple():
    nav = pd.Series([100, 110, 105, 90, 95, 120])
    dd = compute_drawdown(nav)

    assert round(max_drawdown(dd), 4) == -0.1818


def test_drawdown_duration():
    nav = pd.Series([100, 120, 110, 105, 130])
    dd = compute_drawdown(nav)

    assert drawdown_duration(dd) == 2


def test_time_to_recovery_exists():
    nav = pd.Series([100, 120, 90, 130])

    ttr = time_to_recovery(nav)
    assert ttr == 1


def test_time_to_recovery_none():
    nav = pd.Series([100, 120, 90, 95])

    ttr = time_to_recovery(nav)
    assert np.isnan(ttr)
