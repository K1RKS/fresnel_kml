"""Turning JSON requests, CLI site strings and CSV rows into Site and Options objects."""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, field, fields

from .analysis import Options, Site
from .coords import LAT, LON, CoordinateError, parse_coordinate, parse_position
from .units import FEET

SITE_KEYS = {"name", "lat", "lon", "position", "agl_m", "asl_m", "ground_m"}
REQUEST_KEYS = {"site_a", "site_b", "options", "format"}
OPTION_KEYS = {f.name for f in fields(Options)} | {"units", "tree_clearance_m"}
IGNORED_OPTION_KEYS = {"google_key"}


class RequestError(ValueError):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


@dataclass
class ParsedRequest:
    site_a: Site
    site_b: Site
    options: Options
    units: str = "m"
    format: str = "kml"
    warnings: list[str] = field(default_factory=list)


def _number(value, where: str) -> float:
    if isinstance(value, bool):
        raise RequestError(f"{where} must be a number.", where)
    if isinstance(value, (int, float)):
        v = float(value)
    elif isinstance(value, str):
        try:
            v = float(value.strip())
        except ValueError:
            raise RequestError(f"{where} must be a number.", where) from None
    else:
        raise RequestError(f"{where} must be a number.", where)
    if not math.isfinite(v):
        raise RequestError(f"{where} must be a finite number.", where)
    return v


def _integer(value, where: str) -> int:
    v = _number(value, where)
    if v != int(v):
        raise RequestError(f"{where} must be a whole number.", where)
    return int(v)


def _boolean(value, where: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false", "1", "0", "yes", "no", "on", "off"):
        return value.lower() in ("true", "1", "yes", "on")
    raise RequestError(f"{where} must be true or false.", where)


def _units(value) -> str:
    if value not in ("m", "ft"):
        raise RequestError("options.units must be 'm' or 'ft'.", "options.units")
    return value


def resolve_position(where: str, *, lat=None, lon=None, position=None) -> tuple[float, float, str, list[str]]:
    """Return (lat, lon, detected format, warnings) from either lat/lon or a position string."""
    has_latlon = lat is not None or lon is not None
    if position is not None and has_latlon:
        raise RequestError(f"{where}: give either lat and lon, or position, not both.", f"{where}.position")
    try:
        if position is not None:
            parsed = parse_position(position)
            return parsed.lat, parsed.lon, parsed.format, list(parsed.warnings)
        if lat is None or lon is None:
            raise RequestError(f"{where}: lat and lon (or position) are required.", f"{where}.lat")
        lat_v, lat_fmt = _coordinate(lat, LAT, f"{where}.lat")
        lon_v, lon_fmt = _coordinate(lon, LON, f"{where}.lon")
        return lat_v, lon_v, lat_fmt if lat_fmt == lon_fmt else "mixed", []
    except CoordinateError as exc:
        raise RequestError(f"{where}: {exc}", f"{where}.position") from None


def _coordinate(value, axis: str, where: str) -> tuple[float, str]:
    try:
        return parse_coordinate(value, axis)
    except CoordinateError as exc:
        raise RequestError(f"{where}: {exc}", where) from None


def parse_site_dict(data, where: str, units: str = "m") -> tuple[Site, list[str]]:
    if not isinstance(data, dict):
        raise RequestError(f"{where} must be an object.", where)
    unknown = set(data) - SITE_KEYS
    if unknown:
        raise RequestError(f"{where} has unknown field(s): {', '.join(sorted(unknown))}.", where)
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise RequestError(f"{where}.name is required.", f"{where}.name")
    lat, lon, fmt, warnings = resolve_position(where, lat=data.get("lat"), lon=data.get("lon"),
                                               position=data.get("position"))
    scale = FEET if units == "ft" else 1.0
    heights = {}
    for key in ("agl_m", "asl_m", "ground_m"):
        if data.get(key) is not None:
            heights[key] = _number(data[key], f"{where}.{key}") * scale
    if ("agl_m" in heights) == ("asl_m" in heights):
        raise RequestError(f"{where} needs exactly one of agl_m (above ground) or asl_m (above sea level).",
                           f"{where}.agl_m")
    site = Site(name=name.strip(), lat=lat, lon=lon, position_format=fmt, **heights)
    return site, [f"{site.name}: {w}" for w in warnings]


def parse_options_dict(data) -> tuple[Options, str, list[str]]:
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise RequestError("options must be an object.", "options")
    warnings = []
    unknown = set(data) - OPTION_KEYS - IGNORED_OPTION_KEYS
    if unknown:
        raise RequestError(f"options has unknown field(s): {', '.join(sorted(unknown))}.", "options")
    if "google_key" in data:
        warnings.append("options.google_key is ignored; the server's own key is used.")
    units = _units(data.get("units", "m"))
    opt = Options()
    if data.get("tree_clearance_m") is not None:  # shorthand for the same clearance at both sites
        given = {k: v for k, v in data.items() if k != "tree_clearance_m"}
        data = {**given, **{k: data["tree_clearance_m"] for k in ("tree_clearance_a_m", "tree_clearance_b_m")
                            if given.get(k) is None}}
    numbers = ("freq_ghz", "k", "clutter_m", "tree_height_m", "tree_clearance_a_m", "tree_clearance_b_m")
    lengths = ("clutter_m", "tree_height_m", "tree_clearance_a_m", "tree_clearance_b_m")  # feet when units is "ft"
    integers = ("zone", "stations", "ring_points", "ring_every")
    strings = ("elev_source", "style", "mode", "color", "coord_display")
    for key in numbers:
        if data.get(key) is not None:
            value = _number(data[key], f"options.{key}")
            setattr(opt, key, value * FEET if units == "ft" and key in lengths else value)
    for key in integers:
        if data.get(key) is not None:
            setattr(opt, key, _integer(data[key], f"options.{key}"))
    for key in strings:
        if data.get(key) is not None:
            if not isinstance(data[key], str):
                raise RequestError(f"options.{key} must be a string.", f"options.{key}")
            setattr(opt, key, data[key].strip().lower() if key != "elev_source" else data[key].strip())
    if data.get("sixty") is not None:
        opt.sixty = _boolean(data["sixty"], "options.sixty")
    if data.get("trees") is not None:
        opt.trees = _boolean(data["trees"], "options.trees")
    if "terrain_check" in data:
        tc = data["terrain_check"]
        opt.terrain_check = None if tc is None or tc == "auto" else _boolean(tc, "options.terrain_check")
    opt.units = units
    return opt, units, warnings


def parse_request(data) -> ParsedRequest:
    if not isinstance(data, dict):
        raise RequestError("Request body must be a JSON object.", None)
    unknown = set(data) - REQUEST_KEYS
    if unknown:
        raise RequestError(f"Unknown field(s): {', '.join(sorted(unknown))}.", None)
    opt, units, warnings = parse_options_dict(data.get("options"))
    fmt = data.get("format", "kml")
    if fmt not in ("kml", "json"):
        raise RequestError("format must be 'kml' or 'json'.", "format")
    for key in ("site_a", "site_b"):
        if key not in data:
            raise RequestError(f"{key} is required.", key)
    site_a, wa = parse_site_dict(data["site_a"], "site_a", units)
    site_b, wb = parse_site_dict(data["site_b"], "site_b", units)
    return ParsedRequest(site_a, site_b, opt, units, fmt, warnings + wa + wb)


_HEIGHT_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)\s*(agl|asl)?$", re.I)


def parse_height(text: str, where: str, units: str = "m") -> dict:
    m = _HEIGHT_RE.match(text.strip())
    if not m:
        raise RequestError(f"{where}: height '{text}' must be a number, optionally followed by 'agl' or 'asl'.",
                           where)
    value = float(m.group(1)) * (FEET if units == "ft" else 1.0)
    ref = (m.group(2) or "agl").lower()
    return {"agl_m": value} if ref == "agl" else {"asl_m": value}


def parse_site_string(text: str, where: str = "site", units: str = "m",
                      ground_m: float | None = None) -> tuple[Site, list[str]]:
    """'Name,LAT,LON,HEIGHT' or 'Name,POSITION,HEIGHT'; HEIGHT may end in 'agl' or 'asl'."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) == 4:
        name, lat, lon, height = parts
        lat_v, lon_v, fmt, warnings = resolve_position(where, lat=lat, lon=lon)
    elif len(parts) == 3:
        name, position, height = parts
        lat_v, lon_v, fmt, warnings = resolve_position(where, position=position)
    else:
        raise RequestError(f"{where}: expected 'Name,LAT,LON,HEIGHT' or 'Name,POSITION,HEIGHT'.", where)
    if not name:
        raise RequestError(f"{where}: name is required.", where)
    heights = parse_height(height, where, units)
    if ground_m is not None:
        heights["ground_m"] = ground_m * (FEET if units == "ft" else 1.0)
    site = Site(name=name, lat=lat_v, lon=lon_v, position_format=fmt, **heights)
    return site, [f"{name}: {w}" for w in warnings]


def read_sites_csv(path: str, units: str = "m") -> tuple[list[Site], list[str]]:
    """Read two sites from a CSV with columns name, lat, lon (or position), agl_m or asl_m, [ground_m]."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = [r for r in csv.DictReader(fh) if any((v or "").strip() for v in r.values())]
    if len(rows) != 2:
        raise RequestError(f"{path}: expected exactly 2 site rows, found {len(rows)}.", "csv")
    sites, warnings = [], []
    for i, row in enumerate(rows, 1):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        data = {k: row[k] for k in SITE_KEYS if row.get(k)}
        site, w = parse_site_dict(data, f"csv row {i}", units)
        sites.append(site)
        warnings += w
    return sites, warnings
