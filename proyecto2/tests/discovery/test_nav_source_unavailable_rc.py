# proyecto2/tests/discovery/test_nav_source_unavailable_rc.py
# -*- coding: utf-8 -*-
"""
2026-10-10: the NAV load aborted with "Bearer token no disponible. Pipeline abortado." (Playwright browser missing, token_chart()
failing) and still exited 0, so P2_discoverLoadMetrics reported LOAD=0 for a load that wrote nothing (silent break, FND-0239
class). Now: token unavailable at start or lost mid-run -> RC_SOURCE_UNAVAILABLE (2), propagated by main() to the process exit
code; per-fund 'no data' outcomes stay non-fatal. No DB, no network (R-7).
"""
import ast
import re
import sys
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


def test_the_code_is_non_zero_and_distinct_from_success():
    assert nd.RC_SOURCE_UNAVAILABLE == 2


def test_token_exit_code_helper(capsys):
    assert nd._token_exit_code(False, "run_load") == 0
    assert capsys.readouterr().err == ""
    assert nd._token_exit_code(True, "run_update") == nd.RC_SOURCE_UNAVAILABLE
    assert "run_update" in capsys.readouterr().err


def test_every_abort_returns_the_code_and_every_renewal_failure_is_flagged():
    # the two start-up aborts
    aborts = re.findall(r"Pipeline (?:update )?abortado\.\"[^\n]*\n\s*return([^\n]*)", SRC)
    assert len(aborts) == 2 and all(a.strip() == "RC_SOURCE_UNAVAILABLE" for a in aborts)
    # every mid-run renewal in run_load / run_update goes through renew_bearer (FND-0295) and its `lost` flag reaches token_lost; a bare
    # `except RuntimeError` left in the function (the parallel-mode retry phase) must still set token_lost itself
    tree = ast.parse(SRC)
    for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("run_load", "run_update")):
        body = ast.unparse(fn)
        assert "renew_bearer(" in body, fn.name
        assert body.count("renew_bearer(") == body.count("token_lost = token_lost or _lost"), \
            f"{fn.name}: every renew_bearer call must feed token_lost"
        handlers = [h for h in ast.walk(fn) if isinstance(h, ast.ExceptHandler)
                    and isinstance(h.type, ast.Name) and h.type.id == "RuntimeError"]
        mid_run = [h for h in handlers if not any(isinstance(s, ast.Return) for s in h.body)]   # the start abort returns
        for h in mid_run:
            assigns = [s for s in h.body if isinstance(s, ast.Assign) and getattr(s.targets[0], "id", None) == "token_lost"]
            assert assigns, f"{fn.name}: a mid-run token renewal failure must set token_lost (line {h.lineno})"
        returns = [r for r in ast.walk(fn) if isinstance(r, ast.Return) and r.value is not None]
        assert any("_token_exit_code" in ast.unparse(r.value) for r in returns), fn.name


class _Conn:
    def execute(self, sql, params=None):
        return type("R", (), {"fetchall": lambda s: [], "fetchone": lambda s: None})()

    def close(self):
        pass


@pytest.mark.parametrize("mode, fn", [("load", "run_load"), ("update", "run_update")])
@pytest.mark.parametrize("rc", [0, 2])
def test_main_propagates_the_run_code(monkeypatch, mode, fn, rc):
    monkeypatch.setattr(nd, "_Tee", lambda path, stream: stream)            # no log files from a test
    monkeypatch.setattr(nd, "get_connection", lambda: _Conn())
    monkeypatch.setattr(nd, "_ensure_data_status_column", lambda conn: None)
    monkeypatch.setattr(nd, fn, lambda *a, **k: rc)
    monkeypatch.setattr(sys, "argv", ["nav_discovery", "--mode", mode, "--isin", "IE00TEST0001"])
    out, err = sys.stdout, sys.stderr
    try:
        assert nd.main() == rc
    finally:
        sys.stdout, sys.stderr = out, err


def test_the_entry_point_exits_with_main_s_code():
    assert re.search(r'if __name__ == "__main__":[\s\S]*sys\.exit\(main\(\)\)', SRC)
