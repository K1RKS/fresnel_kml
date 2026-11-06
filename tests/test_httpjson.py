import json
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import fresnelkml.httpjson as httpjson_module

from conftest import REAL_GET_JSON

REAL_POST_FORM = httpjson_module.post_form_json


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/fail"):
            self.send_response(503)
            self.end_headers()
            return
        body = json.dumps({"path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.mark.parametrize("use_requests", [False, True])
def test_get_json_both_transports(server, monkeypatch, use_requests):
    if not use_requests:
        monkeypatch.setattr(httpjson_module, "_requests", None)
    elif httpjson_module._requests is None:
        pytest.skip("requests not installed")
    data = REAL_GET_JSON(f"{server}/v1/srtm30m", {"locations": "37.000000,-122.000000|37.1,-122.1"})
    assert data["path"] in ("/v1/srtm30m?locations=37.000000,-122.000000|37.1,-122.1",
                            "/v1/srtm30m?locations=37.000000%2C-122.000000%7C37.1%2C-122.1")
    with pytest.raises(httpjson_module.HttpError, match="503"):
        REAL_GET_JSON(f"{server}/fail")


def test_emscripten_uses_pyxhr_without_user_agent(monkeypatch):
    calls = []

    class Response:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload

        def json(self):
            if not isinstance(self._payload, (dict, list)):
                raise ValueError("not json")
            return self._payload

    class Pyxhr:
        @staticmethod
        def get(url, **kwargs):
            calls.append(("GET", url, kwargs))
            return Response(200, {"ok": True})

        @staticmethod
        def post(url, **kwargs):
            calls.append(("POST", url, kwargs))
            if "bad" in (kwargs.get("data") or ""):
                return Response(0, None)
            return Response(503, {"nope": True})

    http = types.ModuleType("pyodide.http")
    http.pyxhr = Pyxhr
    monkeypatch.setitem(sys.modules, "pyodide", types.ModuleType("pyodide"))
    monkeypatch.setitem(sys.modules, "pyodide.http", http)
    monkeypatch.setattr(httpjson_module.sys, "platform", "emscripten")

    assert REAL_GET_JSON("https://example.test/v1", {"locations": "1,2|3,4"}) == {"ok": True}
    method, url, kwargs = calls[0]
    assert method == "GET" and url.endswith("?locations=1,2|3,4")
    assert "headers" not in kwargs

    with pytest.raises(httpjson_module.HttpError, match="HTTP 503"):
        REAL_POST_FORM("https://example.test/getSamples", {"f": "json"})
    assert calls[1][2]["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert "User-Agent" not in calls[1][2]["headers"]
    with pytest.raises(httpjson_module.HttpError, match="network error"):
        REAL_POST_FORM("https://example.test/getSamples", {"f": "bad"})
