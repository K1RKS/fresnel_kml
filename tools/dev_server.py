#!/usr/bin/env python3
"""Local development server: serves web/, the fresnelkml package, and the optional CGI API."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from wsgiref.simple_server import make_server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
PKG = os.path.join(ROOT, "fresnelkml")
sys.path.insert(0, ROOT)

from fresnelkml.webapi import MAX_BODY, handle_request  # noqa: E402

mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("text/javascript", ".mjs")


def _bytes(start_response, status, data, content_type):
    start_response(status, [("Content-Type", content_type), ("Content-Length", str(len(data)))])
    return [data]


def package_response(path):
    """The worker loads the package from /lib/fresnelkml/. The dev tree keeps it outside web/."""
    if path == "/lib/fresnelkml.json":
        names = sorted(name for name in os.listdir(PKG) if name.endswith(".py"))
        return json.dumps(names).encode("utf-8"), "application/json"
    prefix = "/lib/fresnelkml/"
    if not path.startswith(prefix):
        return None
    name = path[len(prefix):]
    if "/" in name or not name.endswith(".py"):
        return None
    full = os.path.join(PKG, name)
    if not os.path.isfile(full):
        return None
    with open(full, "rb") as fh:
        return fh.read(), "text/plain; charset=utf-8"


def app(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    packaged = package_response(path)
    if packaged is not None:
        return _bytes(start_response, "200 OK", packaged[0], packaged[1])
    if path.endswith("/api/fresnel.cgi"):
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        body = environ["wsgi.input"].read(min(length, MAX_BODY + 1)) if length else b""
        resp = handle_request(environ["REQUEST_METHOD"], environ.get("QUERY_STRING", ""), body)
        if resp.stream is not None:
            start_response(f"{resp.status} {resp.reason}", resp.headers)
            return resp.stream
        start_response(f"{resp.status} {resp.reason}", resp.headers + [("Content-Length", str(len(resp.body)))])
        return [resp.body]

    rel = path.lstrip("/") or "index.html"
    if rel.endswith("/"):
        rel += "index.html"
    full = os.path.normpath(os.path.join(WEB, rel))
    if not full.startswith(WEB + os.sep) or not os.path.isfile(full):
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"not found"]
    ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
    with open(full, "rb") as fh:
        data = fh.read()
    start_response("200 OK", [("Content-Type", ctype), ("Content-Length", str(len(data)))])
    return [data]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)
    with make_server(args.host, args.port, app) as httpd:
        print(f"Serving http://{args.host}:{args.port}/  (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
