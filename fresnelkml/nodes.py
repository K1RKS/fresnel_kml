"""Mesh node lists from Nodographer meshinfo (nodes.json, status.json), slimmed for the site picker and cached on disk."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time

from . import httpjson
from .config import NODES_FILE

STATUS_FILE = "status.json"
STATUS_TIMEOUT_S = 5.0


def _num(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _text(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _position(data: dict) -> tuple[float, float] | tuple[None, None]:
    lat, lon = _num(data.get("lat")), _num(data.get("lon"))
    if lat is None or lon is None or (lat == 0 and lon == 0) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None
    return lat, lon


def _slim_link(link) -> dict | None:
    # Only RF links can be a Fresnel path; tunnels and DTD would bloat the list
    if not isinstance(link, dict) or link.get("linkType") != "RF" or not _text(link.get("id")):
        return None
    lat, lon = _position(link)
    return {"id": link["id"], "name": _text(link.get("hostname")) or link["id"], "type": "RF",
            "distance_km": _num(link.get("distance_km")), "bearing_deg": _num(link.get("bearing_deg")),
            "signal_dbm": _num(link.get("signal_dbm")), "noise_dbm": _num(link.get("noise_dbm")),
            "lat": lat, "lon": lon}


def slim_node(data) -> dict | None:
    """The picker fields of one nodeInfo[i]["data"], or None when it has no id, name or usable position."""
    if not isinstance(data, dict) or not _text(data.get("id")) or not _text(data.get("node")):
        return None
    lat, lon = _position(data)
    if lat is None:
        return None
    ant = data.get("antenna") if isinstance(data.get("antenna"), dict) else {}
    meshrf = data.get("meshrf") if isinstance(data.get("meshrf"), dict) else {}
    return {
        "id": data["id"],
        "name": data["node"],
        "lat": lat,
        "lon": lon,
        "height_m": _num(ant.get("height_m")),
        "azimuth_deg": _num(ant.get("azimuth_deg")),
        "elevation_deg": _num(ant.get("elevation_deg")),
        "gain_dbi": _num(ant.get("gain_dbi")),
        "beamwidth_deg": _num(ant.get("beamwidth_deg")),
        "antenna": _text(ant.get("description")),
        "freq_mhz": _num(meshrf.get("freq")) or None,
        "chanbw_mhz": _num(meshrf.get("chanbw")) or None,
        "band": str(data.get("band") or ""),
        "last_seen": _text(data.get("last_seen")),
        "links": [l for l in (_slim_link(x) for x in data.get("links") or []) if l],
    }


def slim_feed(doc) -> tuple[list[dict], int | None]:
    """All usable nodes of a meshinfo nodes.json and its generation time (ms), or ValueError if it isn't one."""
    if not isinstance(doc, dict) or doc.get("generator") != "Nodographer" or not isinstance(doc.get("nodeInfo"), list):
        raise ValueError("not a Nodographer nodes.json")
    nodes = []
    for entry in doc["nodeInfo"]:
        node = slim_node(entry.get("data") if isinstance(entry, dict) else None)
        if node:
            nodes.append(node)
    generated = doc.get("date")
    return nodes, (int(generated) if isinstance(generated, (int, float)) and not isinstance(generated, bool) else None)


def _cache_path(cache_dir: str | None, url: str) -> str:
    # Not the fresnelkml-nodes-* name of older versions, which may share the cache folder and feed URL
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return os.path.join(cache_dir or tempfile.gettempdir(), f"fresnelkml-meshinfo-nodes-{digest}.json")


def _read_cache(path: str, url: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("url") != url or not isinstance(data.get("nodes"), list):
        return None
    if not isinstance(data.get("fetched"), (int, float)):
        return None
    return data


def _write_cache(path: str, data: dict) -> None:
    try:
        fd, tmp = tempfile.mkstemp(prefix=".fresnelkml-meshinfo-nodes-", dir=os.path.dirname(path))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError:
        pass  # a cache we can't write only costs a refetch next time


def _age(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 120:
        return f"{minutes} min"
    hours = minutes // 60
    return f"{hours} h" if hours < 48 else f"{hours // 24} days"


def _ago(seconds: float) -> str:
    return "under a minute ago" if seconds < 60 else f"{_age(seconds)} ago"


def _reason(exc: Exception, url: str) -> str:
    text = str(exc).replace(url, "").lstrip(": ")
    errno = re.search(r"\[Errno -?\d+\] ([^'\")\]]+)", text)
    if errno:
        return errno.group(1).strip().rstrip(".")
    return (text[:97] + "...") if len(text) > 100 else (text or "unreachable")


def _is_stale(data: dict, config, now: float) -> bool:
    generated = data.get("generated")
    return bool(config.nodes_stale_after_s and isinstance(generated, (int, float))
                and now - generated / 1000 > config.nodes_stale_after_s)


def _status_note(feed: dict, timeout: float) -> str:
    """Why the poller's data is old, from meshinfo's status.json; empty when it can't be read (only the API has it)."""
    try:
        status, _ = httpjson.get_json_conditional(feed["base"] + STATUS_FILE, timeout=min(timeout, STATUS_TIMEOUT_S))
    except httpjson.HttpError:
        return ""
    if not isinstance(status, dict):
        return ""
    error = _text(status.get("lastError"))
    if error:
        return f"; the last scan failed: {error[:200]}"
    if status.get("state") == "failed":
        return "; the last scan failed"
    if status.get("state") == "scanning":
        return "; a scan is in progress"
    return ""


def _load_feed(feed: dict, config, now: float) -> tuple[dict | None, str | None]:
    url = feed["base"] + NODES_FILE
    path = _cache_path(config.cache_dir, url)
    cached = _read_cache(path, url)
    if cached and 0 <= now - cached["fetched"] < config.nodes_cache_ttl_s:
        return cached, None
    timeout = config.nodes_timeout_s or config.timeout_s
    try:
        doc, etag = httpjson.get_json_conditional(url, cached.get("etag") if cached else None, timeout=timeout)
        if doc is None:
            if not cached:
                raise httpjson.HttpError(f"{url}: 304 without a cached copy")
            data = {**cached, "fetched": now, "etag": etag}
        else:
            nodes, generated = slim_feed(doc)
            data = {"url": url, "fetched": now, "etag": etag, "generated": generated, "nodes": nodes}
    except (httpjson.HttpError, ValueError) as exc:
        reason = _reason(exc, url)
        if cached:
            return cached, f"{feed['name']}: feed unavailable ({reason}), showing data from {_ago(now - cached['fetched'])}."
        return None, f"{feed['name']}: feed unavailable ({reason})."
    data["status_note"] = _status_note(feed, timeout) if _is_stale(data, config, now) else ""
    _write_cache(path, data)
    return data, None


def load_nodes(config, now=time.time) -> tuple[list[dict], list[dict], list[str]]:
    """(nodes, feeds, warnings) for the configured feeds. A failing feed is a warning, never an exception."""
    t = now()
    seen: dict[str, dict] = {}
    feeds, warnings = [], []
    for feed in config.node_feeds:
        data, warning = _load_feed(feed, config, t)
        if warning:
            warnings.append(warning)
        if data is None:
            continue
        if _is_stale(data, config, t):
            warnings.append(f"{feed['name']}: mesh data is {_age(t - data['generated'] / 1000)} old"
                            f"{data.get('status_note') or ''}.")
        count = 0
        for node in data["nodes"]:
            if node.get("id") in seen:
                continue
            seen[node["id"]] = {**node, "feed": feed["name"]}
            count += 1
        feeds.append({"name": feed["name"], "generated": data.get("generated"), "count": count,
                      "age_s": round(max(0.0, t - data["fetched"]))})
    nodes = sorted(seen.values(), key=lambda n: n["name"].lower())
    return nodes, feeds, warnings
