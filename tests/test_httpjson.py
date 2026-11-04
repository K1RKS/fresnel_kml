import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import fresnelkml.httpjson as httpjson_module

from conftest import REAL_GET_JSON


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
