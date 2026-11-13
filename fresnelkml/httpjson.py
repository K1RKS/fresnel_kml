"""Minimal JSON-over-HTTP helper: uses requests when installed, urllib otherwise."""

from __future__ import annotations

import gzip
import json
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


def get_json(url: str, params: dict | None = None, timeout: float = 10.0):
    """GET url with query params and return the decoded JSON body."""
    if _requests is not None:
        try:
            resp = _requests.get(url, params=params, timeout=timeout, headers={"User-Agent": USER_AGENT})
        except _requests.RequestException as exc:
            raise HttpError(f"{url}: {exc}") from exc
        return _requests_json(url, resp)

    full = url
    if params:
        full += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, safe=",|")
    return _urllib_json(url, urllib.request.Request(full, headers={"User-Agent": USER_AGENT}), timeout)


def get_json_conditional(url: str, etag: str | None = None, timeout: float = 10.0) -> tuple[object | None, str | None]:
    """GET url as gzip, revalidating with etag. Returns (body, etag); body is None when the server says 304."""
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    if etag:
        headers["If-None-Match"] = etag
    if _requests is not None:
        try:
            resp = _requests.get(url, timeout=timeout, headers=headers)
        except _requests.RequestException as exc:
            raise HttpError(f"{url}: {exc}") from exc
        if resp.status_code == 304:
            return None, resp.headers.get("ETag") or etag
        return _requests_json(url, resp), resp.headers.get("ETag")

    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
            body = resp.read()
            new_etag = resp.headers.get("ETag")
            if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                body = gzip.decompress(body)
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return None, exc.headers.get("ETag") or etag
        raise HttpError(f"{url}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, EOFError) as exc:
        raise HttpError(f"{url}: {exc}") from exc
    try:
        return json.loads(body), new_etag
    except ValueError as exc:
        raise HttpError(f"{url}: response is not JSON") from exc


def post_form_json(url: str, form: dict, timeout: float = 10.0):
    """POST url with a form-encoded body and return the decoded JSON body."""
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
