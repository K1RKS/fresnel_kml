"""HTTP request handling for the web service, independent of CGI or any server."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import sys
import threading
import traceback
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass, field

from . import __version__
from .analysis import AnalysisError, analyze
from .config import ConfigError, build_chain, load_config
from .coords import LAT, LON, CoordinateError, format_position, parse_coordinate, parse_position
from .elevation import ElevationUnavailable
from .kml import render_kml
from .nodes import load_nodes
from .parsing import RequestError, parse_request, resolve_position

MAX_BODY = 16 * 1024
KML_TYPE = "application/vnd.google-earth.kml+xml; charset=utf-8"
JSON_TYPE = "application/json; charset=utf-8"
NDJSON_TYPE = "application/x-ndjson; charset=utf-8"  # not in gzip_types, so NGINX passes lines straight on

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
        "GET ?map": "list the configured map tile servers",
        "GET ?nodes": "list mesh nodes from the configured Nodographer feeds",
        "GET ?parse=TEXT[&axis=lat|lon]": "decode a coordinate or position to decimal degrees",
        "GET ?ground&lat=LAT&lon=LON (or &position=TEXT)[&source=NAME]": "look up the ground elevation at one point",
    },
}


@dataclass
class Response:
    status: int
    body: bytes
    headers: list[tuple[str, str]] = field(default_factory=list)
    stream: Iterator[bytes] | None = None  # sent after `body`, flushed chunk by chunk, with no Content-Length

    @property
    def reason(self) -> str:
        return {200: "OK", 304: "Not Modified", 400: "Bad Request", 405: "Method Not Allowed", 413: "Payload Too Large",
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


def _ground(params: dict, env) -> Response:
    one = {k: v[0] for k, v in params.items()}
    try:
        lat, lon, _, _ = resolve_position("ground", lat=one.get("lat"), lon=one.get("lon"),
                                          position=one.get("position"))
    except RequestError as exc:
        return _error(400, str(exc), exc.field)
    try:
        cfg = load_config(env=env)
    except ConfigError as exc:
        return _error(500, f"Server configuration error: {exc}")
    source = one.get("source") or "auto"
    try:
        chain = build_chain(cfg, only=source, env=env)
    except KeyError:
        return _error(400, f"Unknown elevation source '{source}'.", "source")
    try:
        found = chain.lookup([(lat, lon)])[0]
    except ElevationUnavailable as exc:
        return _error(502, f"{exc}.")
    body = json.dumps({"lat": round(lat, 7), "lon": round(lon, 7), "ground_m": round(found.elevation, 2),
                       "source": found.source, "assumed_zero": found.assumed_zero}).encode("utf-8")
    return Response(200, body, [("Content-Type", JSON_TYPE), ("Cache-Control", "max-age=86400")])


def _etag_matches(header: str, etag: str) -> bool:
    # NGINX turns a strong ETag weak (W/"...") when it gzips the response
    tags = [t.strip() for t in header.split(",")]
    return "*" in tags or etag in (t[2:] if t.startswith("W/") else t for t in tags)


def _nodes(cfg, env) -> Response:
    nodes, feeds, warnings = load_nodes(cfg)
    # age_s changes every second, so it stays out of the ETag
    content = [nodes, warnings, [(f["name"], f["generated"], f["count"]) for f in feeds]]
    etag = '"%s"' % hashlib.sha1(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    headers = [("Cache-Control", "max-age=60"), ("ETag", etag)]
    if _etag_matches((os.environ if env is None else env).get("HTTP_IF_NONE_MATCH") or "", etag):
        return Response(304, b"", headers)
    body = json.dumps({"feeds": feeds, "nodes": nodes, "warnings": warnings}, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")
    return Response(200, body, [("Content-Type", JSON_TYPE)] + headers)


def _get(query: str, env) -> Response:
    params = urllib.parse.parse_qs(query, keep_blank_values=True)
    if "ground" in params:
        return _ground(params, env)
    if "sources" in params or "map" in params or "nodes" in params:
        try:
            cfg = load_config(env=env)
        except ConfigError as exc:
            return _error(500, f"Server configuration error: {exc}")
        if "nodes" in params:
            return _nodes(cfg, env)
        if "map" in params:
            return _json(200, cfg.public_map())
        return _json(200, {"sources": [{"name": "auto", "type": "chain"}] + cfg.public_sources(),
                           "surface": cfg.surface.public()})
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


def _failure(exc: Exception) -> Response:
    if isinstance(exc, AnalysisError):
        return _error(400, str(exc), exc.field)
    if isinstance(exc, ElevationUnavailable):
        return _error(502, f"{exc}. Give asl_m (or ground_m) for each site and set terrain_check to false "
                           "to skip the lookup.")
    if isinstance(exc, TimeoutError):
        return _error(504, "The request took too long.")
    traceback.print_exc(file=sys.stderr)
    return _error(500, "Internal error; see the server log.")


def _line(item: dict) -> bytes:
    return (json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _progress_stream(req, cfg, env) -> Iterator[bytes]:
    """One JSON object per line: {"progress": {stage, done, total}} while working, then
    {"result": {kml, report, warnings}} or {"error", "field", "status"}."""
    events: queue.Queue = queue.Queue()

    def progress(stage: str, done: int | None = None, total: int | None = None) -> None:
        events.put({"progress": {"stage": stage, "done": done, "total": total}})

    def work() -> None:
        try:
            report = analyze(req.site_a, req.site_b, req.options, config=cfg, env=env, progress=progress)
            progress("report")
            events.put({"result": {"kml": render_kml(report), "report": report.to_dict(),
                                   "warnings": req.warnings + report.warnings}})
        except Exception as exc:  # noqa: BLE001 - every failure becomes an error line
            resp = _failure(exc)
            events.put({**json.loads(resp.body), "status": resp.status})
        finally:
            events.put(None)

    yield _line({"progress": {"stage": "start", "done": None, "total": None}})
    threading.Thread(target=work, daemon=True).start()
    while True:
        try:
            item = events.get(timeout=1.0)
        except queue.Empty:
            continue
        if item is None:
            return
        yield _line(item)


def _post(body: bytes, env) -> Response:
    if len(body) > MAX_BODY:
        return _error(413, f"Request body is larger than {MAX_BODY // 1024} KB.")
    try:
        data = json.loads(body.decode("utf-8") or "null")
    except (UnicodeDecodeError, ValueError):
        return _error(400, "Request body is not valid JSON.")
    try:
        req = parse_request(data)
    except RequestError as exc:
        return _error(400, str(exc), exc.field)
    try:
        cfg = load_config(env=env)
    except ConfigError as exc:
        return _error(500, f"Server configuration error: {exc}")
    if req.progress:
        return Response(200, b"", [("Content-Type", NDJSON_TYPE), ("Cache-Control", "no-store"),
                                   ("X-Accel-Buffering", "no")], stream=_progress_stream(req, cfg, env))
    try:
        report = analyze(req.site_a, req.site_b, req.options, config=cfg, env=env)
    except (AnalysisError, ElevationUnavailable, TimeoutError) as exc:
        return _failure(exc)

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
