"""Minimal JSON-over-HTTP helper: uses requests when installed, urllib otherwise.

In the browser (Pyodide) both of those need sockets, so requests go through
pyodide.http.pyxhr, which is a synchronous XMLHttpRequest. The import happens
inside the call so CPython never loads Pyodide. Browsers ignore a script-set
User-Agent, and synchronous XHR cannot set a timeout, so neither is sent.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

try:
    import requests as _requests
except ImportError:  # pragma: no cover - depends on the environment
    _requests = None

USER_AGENT = "fresnelkml/0.1 (+https://github.com/K1RKS/fresnel_kml)"


class HttpError(Exception):
    pass


def _query_url(url: str, params: dict | None) -> str:
    if not params:
        return url
    # Commas and pipes stay literal: elevation APIs treat them as list separators.
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params, safe=",|")


def _browser_json(method: str, url: str, params: dict | None, data: str | None, headers: dict | None):
    from pyodide.http import pyxhr

    full = _query_url(url, params)
    try:
        if method == "GET":
            resp = pyxhr.get(full)
        else:
            resp = pyxhr.post(full, data=data, headers=headers or {})
    except Exception as exc:
        raise HttpError(f"{url}: {exc}") from exc
    if resp.status_code == 0:
        raise HttpError(f"{url}: network error")
    if resp.status_code != 200:
        raise HttpError(f"{url}: HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise HttpError(f"{url}: response is not JSON") from exc


def get_json(url: str, params: dict | None = None, timeout: float = 10.0):
    """GET url with query params and return the decoded JSON body."""
    if sys.platform == "emscripten":
        return _browser_json("GET", url, params, None, None)
    if _requests is not None:
        try:
            resp = _requests.get(url, params=params, timeout=timeout, headers={"User-Agent": USER_AGENT})
        except _requests.RequestException as exc:
            raise HttpError(f"{url}: {exc}") from exc
        return _requests_json(url, resp)

    return _urllib_json(url, urllib.request.Request(_query_url(url, params), headers={"User-Agent": USER_AGENT}),
                        timeout)


def post_form_json(url: str, form: dict, timeout: float = 10.0):
    """POST url with a form-encoded body and return the decoded JSON body."""
    if sys.platform == "emscripten":
        return _browser_json("POST", url, None, urllib.parse.urlencode(form),
                             {"Content-Type": "application/x-www-form-urlencoded"})
    if _requests is not None:
        try:
            resp = _requests.post(url, data=form, timeout=timeout, headers={"User-Agent": USER_AGENT})
        except _requests.RequestException as exc:
            raise HttpError(f"{url}: {exc}") from exc
        return _requests_json(url, resp)
    body = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"})
    return _urllib_json(url, req, timeout)


def _requests_json(url: str, resp):
    if resp.status_code != 200:
        raise HttpError(f"{url}: HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise HttpError(f"{url}: response is not JSON") from exc


def _urllib_json(url: str, req, timeout: float):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        raise HttpError(f"{url}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise HttpError(f"{url}: {exc}") from exc
    try:
        return json.loads(body)
    except ValueError as exc:
        raise HttpError(f"{url}: response is not JSON") from exc
