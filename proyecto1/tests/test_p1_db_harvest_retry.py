# proyecto1/tests/test_p1_db_harvest_retry.py
# -*- coding: utf-8 -*-
"""
Regression for the 2026-10-26 P1_runCycle abort: a single transient DNS failure
(NameResolutionError, getaddrinfo 11001) in harvest.p1_db_harvest._get() aborted the whole
cycle because the call had no retry. _get() now retries transient connection errors and 5xx
with backoff. Tests use a throwaway local HTTP server -- no network, no DB.
"""
from __future__ import annotations

import importlib.util
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import requests

_HARVEST = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "harvest", "p1_db_harvest.py")
)
_spec = importlib.util.spec_from_file_location("p1_db_harvest_retry_test", _HARVEST)
_pdh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pdh)


@pytest.fixture
def server(monkeypatch):
    """Local server answering the scripted status codes in order, then 200."""
    monkeypatch.setattr(_pdh, "HTTP_BACKOFF_S", 0)
    state = {"script": [], "hits": 0}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            state["hits"] += 1
            code = state["script"].pop(0) if state["script"] else 200
            self.send_response(code)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{srv.server_port}/catalogo.xml"
    yield state
    srv.shutdown()
    srv.server_close()


def test_transient_5xx_is_retried_until_success(server):
    server["script"] = [503, 503]
    resp = _pdh._get(server["url"], timeout=5)
    assert resp.status_code == 200
    assert server["hits"] == 3


def test_persistent_5xx_still_raises_after_bounded_attempts(server):
    server["script"] = [503] * 50
    with pytest.raises(requests.HTTPError):
        _pdh._get(server["url"], timeout=5)
    assert server["hits"] == _pdh.HTTP_RETRIES + 1


def test_connection_failure_is_retried_then_raised(monkeypatch):
    """A refused connection (same urllib3 'connect' retry path as a DNS failure) is retried."""
    monkeypatch.setattr(_pdh, "HTTP_BACKOFF_S", 0)
    calls = {"n": 0}
    from urllib3.connectionpool import HTTPConnectionPool
    real = HTTPConnectionPool._new_conn

    def counting(self):
        calls["n"] += 1
        return real(self)

    monkeypatch.setattr(HTTPConnectionPool, "_new_conn", counting)
    with pytest.raises(requests.ConnectionError):
        _pdh._get("http://127.0.0.1:1/catalogo.xml", timeout=2)
    assert calls["n"] == _pdh.HTTP_RETRIES + 1
