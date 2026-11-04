"""Mesh node lists from Nodographer nodes.json feeds, slimmed for the site picker and cached on disk."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time

from . import httpjson

_BLANK = ("", "None", "none", "Not Available")


def _num(value) -> float | None:
    if value is None or isinstance(value, bool) or value in _BLANK:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _short_host(name) -> str:
    return re.sub(r"\.local\.mesh\.?$", "", str(name or "").strip(), flags=re.IGNORECASE)


def _slim_link(link) -> dict | None:
    # Only RF links can be a Fresnel path; tunnels and DTD would bloat the list
    if not isinstance(link, dict) or str(link.get("linkType") or "").upper() != "RF":
        return None
    host = _short_host(link.get("hostname"))
    lid = str(link.get("id") or host.lower())
    if not lid:
        return None
    return {"id": lid, "name": host or lid, "type": str(link.get("linkType") or ""), "distance_km": _num(link.get("distance_km")),
            "bearing_deg": _num(link.get("bearing_deg")), "signal_dbm": _num(link.get("signal_dbm"))}


def slim_node(data) -> dict | None:
    """The picker fields of one nodeInfo[i]["data"], or None when it has no usable position."""
    if not isinstance(data, dict):
        return None
    lat, lon = _num(data.get("lat")), _num(data.get("lon"))
    if lat is None or lon is None or (lat == 0 and lon == 0) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    name = _short_host(data.get("node"))
    if not name:
        return None
    ant = data.get("antenna") if isinstance(data.get("antenna"), dict) else {}
    meshrf = data.get("meshrf") if isinstance(data.get("meshrf"), dict) else {}
    freq = _num(meshrf.get("freq"))
    desc = ant.get("description")
    links = [l for l in (_slim_link(x) for x in data.get("links") or []) if l]
    return {
        "id": str(data.get("id") or name.lower()),
        "name": name,
        "lat": lat,
        "lon": lon,
        "height_m": _num(ant.get("height_m")),
        "azimuth_deg": _num(ant.get("azimuth_deg")),
        "elevation_deg": _num(ant.get("elevation_deg")),
        "gain_dbi": _num(ant.get("gain_dbi")),
        "beamwidth_deg": _num(ant.get("beamwidth_deg")),
        "antenna": desc if isinstance(desc, str) and desc else None,
        "freq_mhz": freq if freq else None,
        "band": str(data.get("band") or ""),
        "links": links,
    }


def slim_feed(doc) -> tuple[list[dict], int | None]:
    """All usable nodes of a feed document and its generation time (ms), or ValueError if it isn't a feed."""
    if not isinstance(doc, dict) or not isinstance(doc.get("nodeInfo"), list):
        raise ValueError("response has no nodeInfo list")
    nodes = []
    for entry in doc["nodeInfo"]:
        node = slim_node(entry.get("data") if isinstance(entry, dict) else None)
        if node:
            nodes.append(node)
    generated = doc.get("date")
    return nodes, (int(generated) if isinstance(generated, (int, float)) and not isinstance(generated, bool) else None)


def _cache_path(cache_dir: str | None, url: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return os.path.join(cache_dir or tempfile.gettempdir(), f"fresnelkml-nodes-{digest}.json")


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
        fd, tmp = tempfile.mkstemp(prefix=".fresnelkml-nodes-", dir=os.path.dirname(path))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError:
        pass  # a cache we can't write only costs a refetch next time


def _ago(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "under a minute ago"
    if minutes < 120:
        return f"{minutes} min ago"
    return f"{minutes // 60} h ago"


def _reason(exc: Exception, url: str) -> str:
    text = str(exc).replace(url, "").lstrip(": ")
    errno = re.search(r"\[Errno -?\d+\] ([^'\")\]]+)", text)
    if errno:
        return errno.group(1).strip().rstrip(".")
    return (text[:97] + "...") if len(text) > 100 else (text or "unreachable")


def _load_feed(feed: dict, config, now: float) -> tuple[dict | None, str | None]:
    path = _cache_path(config.cache_dir, feed["url"])
    cached = _read_cache(path, feed["url"])
    if cached and 0 <= now - cached["fetched"] < config.nodes_cache_ttl_s:
        return cached, None
    timeout = config.nodes_timeout_s or config.timeout_s
    try:
        nodes, generated = slim_feed(httpjson.get_json(feed["url"], timeout=timeout))
    except (httpjson.HttpError, ValueError) as exc:
        reason = _reason(exc, feed["url"])
        if cached:
            return cached, f"{feed['name']}: feed unavailable ({reason}), showing data from {_ago(now - cached['fetched'])}."
        return None, f"{feed['name']}: feed unavailable ({reason})."
    data = {"url": feed["url"], "fetched": now, "generated": generated, "nodes": nodes}
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
