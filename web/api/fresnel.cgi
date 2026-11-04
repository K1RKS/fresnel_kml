#!/usr/bin/env python3
"""CGI entry point, run by fcgiwrap behind NGINX."""

import os
import signal
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.normpath(os.path.join(HERE, "..", "lib"))
if os.path.isdir(LIB):
    sys.path.insert(0, LIB)
else:  # running from the source tree
    sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..")))

from fresnelkml.webapi import MAX_BODY, handle_request  # noqa: E402

TIME_LIMIT_S = 45


def _timeout(signum, frame):
    raise TimeoutError()


def main() -> None:
    method = os.environ.get("REQUEST_METHOD", "GET")
    query = os.environ.get("QUERY_STRING", "")
    try:
        length = max(0, int(os.environ.get("CONTENT_LENGTH") or 0))
    except ValueError:
        length = 0
    body = sys.stdin.buffer.read(min(length, MAX_BODY + 1)) if length else b""

    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _timeout)
        signal.alarm(TIME_LIMIT_S)
    resp = handle_request(method, query, body)
    if hasattr(signal, "SIGALRM"):
        signal.alarm(0)

    out = sys.stdout.buffer
    out.write(f"Status: {resp.status} {resp.reason}\r\n".encode("ascii"))
    for name, value in resp.headers:
        out.write(f"{name}: {value}\r\n".encode("utf-8"))
    out.write(f"Content-Length: {len(resp.body)}\r\n\r\n".encode("ascii"))
    if method.upper() != "HEAD":
        out.write(resp.body)
    out.flush()


if __name__ == "__main__":
    main()
