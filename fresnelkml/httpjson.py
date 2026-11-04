"""Minimal JSON-over-HTTP helper: uses requests when installed, urllib otherwise."""

from __future__ import annotations

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
        if resp.status_code != 200:
            raise HttpError(f"{url}: HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise HttpError(f"{url}: response is not JSON") from exc

    full = url
    if params:
        full += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, safe=",|")
    req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
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
