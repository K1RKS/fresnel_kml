"""HTTP request handling for the web service, independent of CGI or any server."""

from __future__ import annotations

import json
import re
import sys
import traceback
import urllib.parse
from dataclasses import dataclass, field

from . import __version__
from .analysis import AnalysisError, analyze
from .config import ConfigError, load_config
from .coords import LAT, LON, CoordinateError, format_position, parse_coordinate, parse_position
from .elevation import ElevationUnavailable
from .kml import render_kml
from .parsing import RequestError, parse_request

MAX_BODY = 16 * 1024
KML_TYPE = "application/vnd.google-earth.kml+xml; charset=utf-8"
JSON_TYPE = "application/json; charset=utf-8"

USAGE = {
    "service": "fresnelkml",
    "version": __version__,
    "usage": "POST a JSON body with site_a, site_b and optional options/format to get KML back.",
    "example": {
        "site_a": {"name": "TowerA", "lat": 37.0, "lon": -122.0, "agl_m": 30},
        "site_b": {"name": "TowerB", "lat": 37.05, "lon": -121.9, "agl_m": 25},
        "options": {"color": "random"},
        "format": "kml",
    },
    "endpoints": {
        "GET ?sources": "list the configured elevation sources",
        "GET ?parse=TEXT[&axis=lat|lon]": "decode a coordinate or position to decimal degrees",
    },
}


@dataclass
class Response:
    status: int
    body: bytes
    headers: list[tuple[str, str]] = field(default_factory=list)

    @property
    def reason(self) -> str:
        return {200: "OK", 400: "Bad Request", 405: "Method Not Allowed", 413: "Payload Too Large",
                500: "Internal Server Error", 502: "Bad Gateway", 504: "Gateway Timeout"}.get(self.status, "")


def _json(status: int, data) -> Response:
    body = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
    return Response(status, body, [("Content-Type", JSON_TYPE), ("Cache-Control", "no-store")])


def _error(status: int, message: str, field_name: str | None = None) -> Response:
    data = {"error": message}
    if field_name:
        data["field"] = field_name
    return _json(status, data)


def safe_filename(a: str, b: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", f"Fresnel_{a}_{b}")
    stem = re.sub(r"_{2,}", "_", stem).strip("._") or "Fresnel"
    return stem[:120] + ".kml"


def _get(query: str, env) -> Response:
    params = urllib.parse.parse_qs(query, keep_blank_values=True)
    if "sources" in params:
        try:
            cfg = load_config(env=env)
        except ConfigError as exc:
            return _error(500, f"Server configuration error: {exc}")
        return _json(200, {"sources": [{"name": "auto", "type": "chain"}] + cfg.public_sources()})
    if "parse" in params:
        text = params["parse"][0]
        axis = (params.get("axis") or [""])[0]
        try:
            if axis in (LAT, LON):
                value, fmt = parse_coordinate(text, axis)
                return _json(200, {axis: round(value, 7), "format": fmt, "display": f"{value:.7f}", "notes": [],
                                   "warnings": []})
            if axis:
                return _error(400, "axis must be 'lat' or 'lon'.", "axis")
            p = parse_position(text)
        except CoordinateError as exc:
            return _error(400, str(exc), "parse")
        return _json(200, {"lat": round(p.lat, 7), "lon": round(p.lon, 7), "format": p.format,
                           "display": format_position(p.lat, p.lon), "notes": p.notes, "warnings": p.warnings})
    return _json(200, USAGE)


def _post(body: bytes, env) -> Response:
    if len(body) > MAX_BODY:
        return _error(413, f"Request body is larger than {MAX_BODY // 1024} KB.")
    try:
        data = json.loads(body.decode("utf-8") or "null")
    except (UnicodeDecodeError, ValueError):
        return _error(400, "Request body is not valid JSON.")
    try:
        req = parse_request(data)
        cfg = None
        try:
            cfg = load_config(env=env)
        except ConfigError as exc:
            return _error(500, f"Server configuration error: {exc}")
        report = analyze(req.site_a, req.site_b, req.options, config=cfg, env=env)
    except (RequestError, AnalysisError) as exc:
        return _error(400, str(exc), exc.field)
    except ElevationUnavailable as exc:
        return _error(502, f"{exc}. Give asl_m (or ground_m) for each site and set terrain_check to false "
                           "to skip the lookup.")
    except TimeoutError:
        return _error(504, "The request took too long.")

    kml = render_kml(report)
    warnings = req.warnings + report.warnings
    if req.format == "json":
        return _json(200, {"kml": kml, "report": report.to_dict(), "warnings": warnings})
    filename = safe_filename(report.site_a.name, report.site_b.name)
    headers = [("Content-Type", KML_TYPE), ("Content-Disposition", f'attachment; filename="{filename}"'),
               ("Cache-Control", "no-store")]
    if warnings:
        headers.append(("X-Fresnel-Warnings", str(len(warnings))))
    return Response(200, kml.encode("utf-8"), headers)


def handle_request(method: str, query: str = "", body: bytes = b"", env=None) -> Response:
    """Handle one API request. `env` provides FRESNEL_CONFIG and API keys (defaults to os.environ)."""
    try:
        method = (method or "GET").upper()
        if method in ("GET", "HEAD"):
            return _get(query or "", env)
        if method == "POST":
            return _post(body or b"", env)
        resp = _error(405, "Use POST (or GET for usage).")
        resp.headers.append(("Allow", "GET, POST"))
        return resp
    except TimeoutError:
        return _error(504, "The request took too long.")
    except Exception:  # noqa: BLE001 - the CGI layer must always answer with JSON
        traceback.print_exc(file=sys.stderr)
        return _error(500, "Internal error; see the server log.")
