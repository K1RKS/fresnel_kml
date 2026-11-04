#!/usr/bin/env python3
"""Local development server: serves web/ and routes /api/fresnel.cgi to the API handler."""

from __future__ import annotations

import argparse
import mimetypes
import os
import sys
from wsgiref.simple_server import make_server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
sys.path.insert(0, ROOT)

from fresnelkml.webapi import MAX_BODY, handle_request  # noqa: E402


def app(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    if path.endswith("/api/fresnel.cgi"):
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        body = environ["wsgi.input"].read(min(length, MAX_BODY + 1)) if length else b""
        resp = handle_request(environ["REQUEST_METHOD"], environ.get("QUERY_STRING", ""), body)
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
