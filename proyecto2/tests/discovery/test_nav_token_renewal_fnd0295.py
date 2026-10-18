# proyecto2/tests/discovery/test_nav_token_renewal_fnd0295.py
# -*- coding: utf-8 -*-
"""
FND-0295: the Morningstar bearer token works for ~40 minutes, but the loader renewed it only after 45. An auth error in minutes 40-45 fell into
the "token is recent -> security not accessible" branch with no renewal and no retry: on 2026-10-18 the load reported 752 of 3,739 funds without
data, in three contiguous runs ending at the two renewals (~4 min each). The fix renews BEFORE expiry and treats an auth error from a token older
than a minute as an expired token (renew once, retry once) in both run_load and run_update.

The regression test drives the REAL run_load loop with a scripted clock and a fake server whose token dies at exactly 40 minutes. No DB, no network.
"""
import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).parent
_P2_ROOT = _HERE.parent.parent
_REPO = _P2_ROOT.parent
for _p in (str(_P2_ROOT), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proyecto2.src.discovery import nav_discovery as nd  # noqa: E402

SRC = Path(nd.__file__).read_text(encoding="utf-8")
TOKEN_LIFETIME_S = 40 * 60                       # measured 2026-10-18


# ---------------------------------------------------------------- pure decisions
def test_the_proactive_renewal_happens_before_the_token_can_expire():
    assert nd.TOKEN_PROACTIVE_RENEW_S < TOKEN_LIFETIME_S
    assert nd.token_needs_proactive_renewal(nd.TOKEN_PROACTIVE_RENEW_S - 1) is False
    assert nd.token_needs_proactive_renewal(nd.TOKEN_PROACTIVE_RENEW_S) is True
    assert nd.token_needs_proactive_renewal(0) is False


def test_an_auth_error_from_an_old_token_means_expired_and_from_a_fresh_one_does_not():
    assert nd.auth_error_means_expired_token(nd.TOKEN_MIN_AUTH_RENEW_AGE_S - 1) is False       # a bad ISIN right after a renewal: no storm
    assert nd.auth_error_means_expired_token(nd.TOKEN_MIN_AUTH_RENEW_AGE_S) is True
    assert nd.auth_error_means_expired_token(41 * 60) is True                                    # the window that used to be lost


def test_renew_bearer_returns_the_new_token_or_flags_the_loss_and_keeps_the_old_one():
    assert nd.renew_bearer(lambda: "new", "old", 100.0, now=500.0) == ("new", 500.0, False)

    def boom():
        raise RuntimeError("no token")
    assert nd.renew_bearer(boom, "old", 100.0, now=500.0) == ("old", 100.0, True)


def test_the_old_45_minute_rule_is_gone_from_both_modes():
    assert "_TOKEN_MAX_AGE_S" not in SRC and "45 * 60" not in SRC
    for fn in ("def run_load", "def run_update"):
        start = SRC.index(fn)
        end = SRC.index("\ndef ", start + 10)
        body = SRC[start:end]
        assert "token_needs_proactive_renewal(" in body and "auth_error_means_expired_token(" in body, fn


# ---------------------------------------------------------------- the real run_load loop, scripted clock, token that dies at 40 minutes
class _Result:
    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _Conn:
    def execute(self, sql, params=None):
        return _Result()

    def commit(self):
        pass


class _World:
    """A fake Morningstar: tokens are 'tok-<issued>', rejected (auth) once older than TOKEN_LIFETIME_S; each download takes `step` seconds."""

    def __init__(self, step, bad_isins=()):
        self.now = 1_000_000.0
        self.step = step
        self.bad = set(bad_isins)
        self.renewals = 0
        self.downloads = 0

    def time(self):
        return self.now

    def get_token(self):
        self.renewals += 1
        return f"tok-{self.now}"

    def download(self, code, isin, currency, desde, bearer):
        self.downloads += 1
        self.now += self.step
        issued = float(bearer.split("-", 1)[1])
        if isin in self.bad or self.now - issued > TOKEN_LIFETIME_S:
            return [], "auth", None
        return [{"Date": "2026-10-16", "NAV": 1.0, "NAV_Currency": "EUR"}], None, None


def _drive(monkeypatch, world, isins):
    monkeypatch.setattr(nd.time, "time", world.time)
    monkeypatch.setattr(nd.time, "sleep", lambda s: None)
    monkeypatch.setattr(nd, "_get_bearer_token", world.get_token)
    monkeypatch.setattr(nd, "_download_nav_daily", world.download)
    monkeypatch.setattr(nd, "_auto_freeze_stale_navs", lambda conn, dry_run: None)
    monkeypatch.setattr(nd, "_splice_new_chart_batch", lambda conn, isin, rows: rows)
    monkeypatch.setattr(nd, "_gate_nav_batch", lambda isin, rows: rows)
    monkeypatch.setattr(nd, "_write_nav_rows_daily", lambda conn, rows, dry_run: len(rows))
    monkeypatch.setattr(nd, "_write_nav_rows", lambda conn, rows, dry_run: len(rows))
    monkeypatch.setattr(nd, "_resample_to_monthly", lambda rows: [{"Date": "2026-10-31"}])
    return nd.run_load(_Conn(), isins, "2000-01-01", False, False, bearer_token=f"tok-{world.now}")


def test_a_long_load_does_not_lose_funds_in_the_last_minutes_of_each_token(monkeypatch, capsys):
    """400 funds x 20 s = 133 min, i.e. three tokens of 40 min: the 2026-10-18 shape. Before the fix ~4 min of every token (about 12 funds each)
    came back as 'security no accesible'; now none does, and the token is renewed before it can expire."""
    world = _World(step=20)
    isins = [f"LU{n:010d}" for n in range(400)]
    rc = _drive(monkeypatch, world, isins)
    out = capsys.readouterr().out
    assert rc == 0
    assert "security no accesible" not in out and "Fondos sin datos   : 0" in out
    assert world.renewals >= 4                                           # 133 min / 30 min proactive
    assert world.downloads == 400                                        # no retries were needed: the token never expired


def test_the_scenario_is_sensitive_to_the_old_45_minute_thresholds(monkeypatch, capsys):
    """Guards the test above against being vacuous: with both thresholds set to the OLD 45 minutes the very same loop loses funds."""
    monkeypatch.setattr(nd, "TOKEN_PROACTIVE_RENEW_S", 45 * 60)
    monkeypatch.setattr(nd, "TOKEN_MIN_AUTH_RENEW_AGE_S", 45 * 60)
    world = _World(step=20)
    _drive(monkeypatch, world, [f"LU{n:010d}" for n in range(400)])
    out = capsys.readouterr().out
    assert "security no accesible" in out and "Fondos sin datos   : 0" not in out


def test_the_old_rule_would_have_lost_funds_in_this_scenario():
    """The same world with the OLD policy (renew only above 45 min, no retry on an 'auth' of a younger token) loses the 40-45 min window.
    Documents the failure the scenario reproduces; pure arithmetic on the policy, no loop."""
    step, lifetime, old_max_age = 20, TOKEN_LIFETIME_S, 45 * 60
    now, issued, lost = 0.0, 0.0, 0
    for _ in range(400):
        now += step
        if now - issued > lifetime:                                      # the server rejects
            if now - issued > old_max_age:                               # old policy: renew only now, then retry
                issued = now
            else:
                lost += 1                                                # old policy: classified no_access
    assert lost >= 12                                                    # ~5 min / 20 s per token window


def test_a_bad_isin_right_after_a_renewal_does_not_trigger_a_renewal_storm(monkeypatch, capsys):
    world = _World(step=1, bad_isins={"LU0000000003"})                   # one security that is genuinely not accessible
    rc = _drive(monkeypatch, world, [f"LU{n:010d}" for n in range(8)])
    out = capsys.readouterr().out
    assert rc == 0 and world.renewals == 0                               # the token is a few seconds old: no renewal for a real no_access
    assert "Fondos sin datos   : 1" in out and "security no accesible" in out


def test_a_bad_isin_with_an_old_token_costs_one_renewal_and_one_retry(monkeypatch, capsys):
    world = _World(step=120, bad_isins={"LU0000000002"})                 # 2 minutes per fund: the token is >60 s old at the bad ISIN
    rc = _drive(monkeypatch, world, [f"LU{n:010d}" for n in range(6)])
    out = capsys.readouterr().out
    assert rc == 0 and world.renewals == 1                               # renewed once, retried once, still refused -> no_access
    assert world.downloads == 6 + 1 and "Fondos sin datos   : 1" in out


def test_a_failed_renewal_still_surfaces_as_source_unavailable(monkeypatch, capsys):
    world = _World(step=20)

    def boom():
        raise RuntimeError("no token")
    monkeypatch.setattr(nd.time, "time", world.time)
    monkeypatch.setattr(nd.time, "sleep", lambda s: None)
    monkeypatch.setattr(nd, "_download_nav_daily", world.download)
    for name, fn in (("_auto_freeze_stale_navs", lambda c, d: None), ("_splice_new_chart_batch", lambda c, i, r: r),
                     ("_gate_nav_batch", lambda i, r: r), ("_write_nav_rows_daily", lambda c, r, d: len(r)),
                     ("_write_nav_rows", lambda c, r, d: len(r)), ("_resample_to_monthly", lambda r: [{"Date": "2026-10-31"}])):
        monkeypatch.setattr(nd, name, fn)
    token = f"tok-{world.now}"
    first_get = {"n": 0}

    def get_token():                                                      # the start-up token comes from bearer_token=; every renewal fails
        first_get["n"] += 1
        return boom()
    monkeypatch.setattr(nd, "_get_bearer_token", get_token)
    rc = nd.run_load(_Conn(), [f"LU{n:010d}" for n in range(120)], "2000-01-01", False, False, bearer_token=token)
    assert rc == nd.RC_SOURCE_UNAVAILABLE and first_get["n"] >= 1
