# proyecto2/tests/discovery/test_nav_currency_no_default_fnd0243.py
# -*- coding: utf-8 -*-
"""
FND-0243 step 2: NAV_Currency is a copy of fund_master.fund_currency (verified 2026-10-09: no fund has a value
that differs, none comes from Morningstar). An unknown class currency must stay unknown (NULL) instead of being
written as 'EUR', which silently scored USD/GBP classes with a NULL Fund_Currency as euro funds.
No network: requests.get is replaced by a stub returning a chartservice-shaped payload.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

_HERE = Path(__file__).parent
_P2_ROOT = _HERE.parent.parent
_REPO = _P2_ROOT.parent
for _p in (str(_P2_ROOT), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proyecto2.src.discovery import nav_discovery as nd  # noqa: E402

_PAYLOAD = [{"series": [{"date": "2026-09-29", "totalReturn": 101.5}, {"date": "2026-09-30", "totalReturn": 101.9}]}]


def _stub_get(monkeypatch):
    resp = MagicMock(status_code=200)
    resp.json.return_value = _PAYLOAD
    monkeypatch.setattr(nd.requests, "get", lambda *a, **k: resp)


def test_daily_download_keeps_an_unknown_currency_unknown(monkeypatch):
    _stub_get(monkeypatch)
    rows, err, _ = nd._download_nav_daily("F0XXX", "IE00TEST0001", None, "2026-09-01", "token")
    assert err == "" and len(rows) == 2
    assert {r["NAV_Currency"] for r in rows} == {None}


def test_daily_download_copies_a_known_currency(monkeypatch):
    _stub_get(monkeypatch)
    rows, _, _ = nd._download_nav_daily("F0XXX", "IE00TEST0001", "USD", "2026-09-01", "token")
    assert {r["NAV_Currency"] for r in rows} == {"USD"}


def test_no_eur_default_left_in_the_loader_source():
    src = Path(nd.__file__).read_text(encoding="utf-8")
    assert 'currency_map.get(isin, "EUR")' not in src
    assert '(r[1] or "EUR")' not in src
    assert 'currency or "EUR"' not in src
