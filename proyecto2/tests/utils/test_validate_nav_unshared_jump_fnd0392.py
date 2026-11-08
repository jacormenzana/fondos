# proyecto2/tests/utils/test_validate_nav_unshared_jump_fnd0392.py
# -*- coding: utf-8 -*-
"""FND-0392 (WS0.5 of FND-0391): a monthly NAV that moves 50 %+ in ONE calendar month while its Fund_Nature peers did not is a level shift, not a price.

R-7: only validators.py and peer_returns.py are imported (no pipeline, no DB). The pattern that motivated it (a fund doubling in one month, +92.9 %, never reversing, while 400 peers moved a few
percent) and the cases it must NOT catch (a market-wide move, too few peers, a step over a NAV gap) are built as fixtures, with no ISIN in the code under test (P#5).
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for _p in (_ROOT, _ROOT / "proyecto2"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import numpy as np
import pandas as pd

from src.utils.peer_returns import build_peer_returns, one_month_returns
from src.utils.validators import find_unshared_jumps, validate_nav, validate_nav_stale_flat, validate_nav_unshared_jump

NATURE = "Renta Variable"


def _nav(values, start="2022-01-31", stamp_shift_days=0):
    dates = pd.date_range(start, periods=len(values), freq="ME") - pd.Timedelta(days=stamp_shift_days)
    return pd.DataFrame({"date": dates, "nav": [float(v) for v in values]})


def _walk(rets, start_value=100.0):
    vals = [start_value]
    for r in rets:
        vals.append(vals[-1] * (1.0 + r))
    return vals


def _panel(n_peers=30, months=40, seed=7, shock_month=None, shock=0.0, stamp_shift_days=0, own=None):
    """A PeerReturns of n_peers funds of NATURE with small monthly returns; `shock` is added to EVERY peer in `shock_month` (an index into the months), i.e. a market-wide move.
    `own` = (isin, nav_df): one more fund of the same nature, put in the panel (to test the leave-one-out)."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_peers):
        rets = rng.normal(0.004, 0.02, months - 1)
        if shock_month is not None:
            rets[shock_month - 1] += shock
        nav = _nav(_walk(rets), stamp_shift_days=stamp_shift_days)
        rows.append(nav.assign(isin=f"PEER{i:03d}", nature=NATURE))
    if own is not None:
        rows.append(own[1].assign(isin=own[0], nature=NATURE))
    return build_peer_returns(pd.concat(rows, ignore_index=True))


def _target(jump_at=30, jump=0.929, months=40, seed=3):
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.004, 0.02, months - 1)
    rets[jump_at - 1] = jump
    return _nav(_walk(rets))


def test_a_one_month_level_shift_that_no_peer_made_is_flagged_and_the_message_names_it():
    nav = _target()
    peers = _panel(own=("TARGET", nav))
    assert validate_nav(nav)[0] is True and validate_nav_stale_flat(nav)[0] is True        # the two existing validators do not see it: that is the defect
    ok, msg = validate_nav_unshared_jump(nav, peers, "TARGET")
    assert ok is False and "+93%" in msg and "pares" in msg and "0 se movieron" in msg


def test_a_market_wide_move_of_the_same_size_is_not_flagged():
    nav = _target(jump=0.60)
    peers = _panel(shock_month=30, shock=0.60, own=("TARGET", nav))
    assert validate_nav_unshared_jump(nav, peers, "TARGET") == (True, None)


def test_a_negative_unshared_move_is_flagged_too():
    nav = _target(jump=-0.60)
    ok, msg = validate_nav_unshared_jump(nav, _panel(own=("TARGET", nav)), "TARGET")
    assert ok is False and "-60%" in msg


def test_fewer_than_the_minimum_peers_is_not_decidable_never_a_finding():
    nav = _target()
    assert validate_nav_unshared_jump(nav, _panel(n_peers=8, own=("TARGET", nav)), "TARGET") == (True, None)
    assert validate_nav_unshared_jump(nav, _panel(n_peers=8, own=("TARGET", nav)), "TARGET", min_peers=5)[0] is False


def test_the_fund_never_counts_as_its_own_peer():
    nav = _target()
    peers = _panel(n_peers=9, own=("TARGET", nav))                  # 9 others + itself = 10 funds: only 9 peers
    assert validate_nav_unshared_jump(nav, peers, "TARGET") == (True, None)
    assert len(peers.peers_at("TARGET", pd.Timestamp("2022-01-31") + pd.offsets.MonthEnd(30))) <= 9


def test_a_step_over_a_gap_in_the_nav_is_not_a_one_month_move():
    nav = _target()
    gapped = nav.drop(index=29).reset_index(drop=True)                # month 29 missing: the +93 % now spans two calendar months
    assert validate_nav_unshared_jump(gapped, _panel(own=("TARGET", gapped)), "TARGET") == (True, None)


def test_the_month_is_the_period_key_not_the_stamp():
    nav = _target()
    peers = _panel(stamp_shift_days=2, own=("TARGET", nav))           # peers stamped on the last business day, the fund on the calendar month-end
    ok, _msg = validate_nav_unshared_jump(nav, peers, "TARGET")
    assert ok is False


def test_thresholds_and_defaults_are_parameters():
    nav = _target()
    peers = _panel(own=("TARGET", nav))
    assert validate_nav_unshared_jump(nav, peers, "TARGET", min_abs_ret=1.0) == (True, None)           # +92.9 % is under a 100 % cut
    jumps = find_unshared_jumps(nav, peers, "TARGET")
    assert len(jumps) == 1 and jumps[0]["n_peers"] == 30 and jumps[0]["peer_share"] == 0.0 and abs(jumps[0]["ret"] - 0.929) < 1e-9


def test_a_fund_with_no_nature_or_an_empty_series_is_never_a_finding():
    peers = _panel()
    assert validate_nav_unshared_jump(_target(), peers, "NO_SUCH_FUND") == (True, None)
    assert validate_nav_unshared_jump(_nav([]), peers, "X") == (True, None)
    assert validate_nav_unshared_jump(_nav([10.0]), peers, "X") == (True, None)


def test_one_month_returns_skip_gaps_and_keep_the_last_row_of_a_month():
    nav = pd.DataFrame({"date": pd.to_datetime(["2022-01-31", "2022-02-28", "2022-04-29", "2022-05-31", "2022-05-30"]),
                        "nav": [100.0, 110.0, 121.0, 133.1, 133.1]})
    r = one_month_returns(nav)
    assert np.isclose(r[pd.Timestamp("2022-02-28")], 0.10)
    assert np.isnan(r[pd.Timestamp("2022-04-30")])                    # March is missing: April is not a one-month return
    assert np.isclose(r[pd.Timestamp("2022-05-31")], 133.1 / 121.0 - 1.0)


def test_the_kill_switch_defaults_and_the_pipeline_wiring():
    from shared import config
    assert config.NAV_UNSHARED_JUMP_MODE == "OFF"                     # ships OFF: WS1 (FND-0393) calibrates the thresholds before SHADOW
    assert (config.NAV_UNSHARED_JUMP_MIN_ABS_RET, config.NAV_UNSHARED_JUMP_MIN_PEERS, config.NAV_UNSHARED_JUMP_MAX_PEER_SHARE) == (0.35, 10, 0.05)
    src = (_ROOT / "proyecto2" / "src" / "pipeline" / "run_pipeline.py").read_text(encoding="utf-8")
    assert src.count("validate_nav_unshared_jump(") == 1 and "NAV_JUMP_SHADOW" in src             # one call, on the monthly series only
    assert src.index("validate_nav_stale_flat(nav_df)") < src.index("validate_nav_unshared_jump(nav_df")   # the cheap rules decide first
