"""Coordinate parsing and formatting.

Decimal degrees on WGS-84 are the default. Also accepted for a single
latitude or longitude: hemisphere letters, degrees-minutes-seconds (DMS) and
degrees-decimal-minutes (DDM). Accepted for a whole position: a lat,lon pair
in any of those forms, ISO 6709, geo URIs, UTM, MGRS, Maidenhead locators and
full Plus codes.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

LAT, LON = "lat", "lon"
COARSE_CELL_M = 100.0
_M_PER_DEG = 111_320.0


class CoordinateError(ValueError):
    pass


@dataclass
class ParsedPosition:
    lat: float
    lon: float
    format: str
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ParsedCoordinate:
    value: float
    axis: str | None  # axis implied by a hemisphere letter, if any
    format: str


_REPLACEMENTS = (
    ("\u2032", "'"), ("\u2019", "'"), ("\u2018", "'"), ("\u00b4", "'"), ("`", "'"),
    ("\u2033", '"'), ("\u201d", '"'), ("\u201c", '"'), ("''", '"'),
    ("\u00ba", "\u00b0"), ("\u02da", "\u00b0"),
    ("\u2212", "-"), ("\u2013", "-"), ("\u2014", "-"),
)


def _normalize(text: str) -> str:
    s = str(text).strip()
    for old, new in _REPLACEMENTS:
        s = s.replace(old, new)
    return s


def _check_range(value: float, axis: str, label: str = "") -> None:
    limit = 90.0 if axis == LAT else 180.0
    if not -limit <= value <= limit:
        name = "Latitude" if axis == LAT else "Longitude"
        raise CoordinateError(f"{name} {label or value} is out of range (must be within \u00b1{limit:g}).")


# --------------------------------------------------------------------------
# Single coordinates
# --------------------------------------------------------------------------

_NUM_RE = re.compile(r"^(?:\d+(?:\.\d*)?|\.\d+)$")
_ALLOWED_RE = re.compile(r"^[\d.\s\u00b0dDmMsS:'\"]+$")
_SPLIT_RE = re.compile(r"[\s\u00b0dDmMsS:'\"]+")
_FORMAT_BY_COUNT = {1: "decimal", 2: "ddm", 3: "dms"}


def _parse_single(raw: str, axis: str | None) -> ParsedCoordinate:
    original = str(raw).strip()
    s = _normalize(raw)
    if not s:
        raise CoordinateError("Coordinate is empty.")
    if re.fullmatch(r"[+-]?\d+,\d+", s):
        raise CoordinateError(f"'{original}' uses a comma as the decimal mark; use '.' instead.")

    hemi = None
    m = re.match(r"^([NSEWnsew])\s*(?=[\d+.-])", s)
    if m:
        hemi = m.group(1).upper()
        s = s[m.end():].strip()
    m = re.search(r"([NSEWnsew])$", s)
    if m:
        letter = m.group(1)
        is_seconds_marker = (letter in "sS" and re.search(r"\d[sS]$", s)
                             and re.search(r"\d\s*[mM]", s))
        if not is_seconds_marker:
            if hemi is not None:
                raise CoordinateError(f"'{original}' has two hemisphere letters.")
            hemi = letter.upper()
            s = s[:-1].strip()

    negative = False
    if s[:1] in "+-":
        negative = s[0] == "-"
        s = s[1:].strip()
        if negative and hemi is not None:
            raise CoordinateError(f"'{original}' has both a minus sign and a hemisphere letter; use one or the other.")

    if not s or not _ALLOWED_RE.match(s):
        raise CoordinateError(f"'{original}' is not a recognised coordinate.")
    parts = [p for p in _SPLIT_RE.split(s) if p]
    if not 1 <= len(parts) <= 3 or not all(_NUM_RE.match(p) for p in parts):
        raise CoordinateError(f"'{original}' is not a recognised coordinate.")
    if any("." in p for p in parts[:-1]):
        raise CoordinateError(f"In '{original}', only the last of degrees/minutes/seconds may have a decimal part.")

    values = [float(p) for p in parts]
    deg = values[0]
    minutes = values[1] if len(values) > 1 else 0.0
    seconds = values[2] if len(values) > 2 else 0.0
    if minutes >= 60 or seconds >= 60:
        raise CoordinateError(f"In '{original}', minutes and seconds must be below 60.")
    value = deg + minutes / 60.0 + seconds / 3600.0

    letter_axis = None
    if hemi is not None:
        letter_axis = LAT if hemi in "NS" else LON
        if axis is not None and letter_axis != axis:
            kind = "latitude" if axis == LAT else "longitude"
            raise CoordinateError(f"'{original}' has an {hemi} hemisphere letter, which doesn't fit a {kind}.")
        negative = hemi in "SW"
    if negative:
        value = -value
    check_axis = axis or letter_axis
    if check_axis is not None:
        _check_range(value, check_axis, original)
    return ParsedCoordinate(value, letter_axis, _FORMAT_BY_COUNT[len(parts)])


def parse_coordinate(value, axis: str) -> tuple[float, str]:
    """Parse one latitude or longitude. Returns (decimal degrees, detected format)."""
    if axis not in (LAT, LON):
        raise ValueError("axis must be 'lat' or 'lon'")
    if isinstance(value, bool):
        raise CoordinateError("Coordinate must be a number or a string.")
    if isinstance(value, (int, float)):
        v = float(value)
        if not math.isfinite(v):
            raise CoordinateError("Coordinate must be a finite number.")
        _check_range(v, axis)
        return (_wrap_lon(v) if axis == LON else v), "decimal"
    if not isinstance(value, str):
        raise CoordinateError("Coordinate must be a number or a string.")
    parsed = _parse_single(value, axis)
    return (_wrap_lon(parsed.value) if axis == LON else parsed.value), parsed.format


# --------------------------------------------------------------------------
# Pairs
# --------------------------------------------------------------------------

def _assign(a: ParsedCoordinate, b: ParsedCoordinate, raw_a: str, raw_b: str) -> tuple[float, float]:
    if a.axis is not None and a.axis == b.axis:
        raise CoordinateError("Both values of the pair are marked as the same axis.")
    if a.axis == LON or b.axis == LAT:
        a, b, raw_a, raw_b = b, a, raw_b, raw_a
    if a.axis is None and b.axis is None and abs(a.value) > 90 >= abs(b.value):
        raise CoordinateError("The first value is out of latitude range; the pair looks like longitude, "
                              "latitude. Give latitude first.")
    _check_range(a.value, LAT, raw_a)
    _check_range(b.value, LON, raw_b)
    return a.value, b.value


def _pair_format(a: ParsedCoordinate, b: ParsedCoordinate) -> str:
    return a.format if a.format == b.format else "mixed"


def _parse_pair(s: str) -> ParsedPosition:
    if "," in s or ";" in s:
        parts = [p.strip() for p in re.split(r"[,;]", s)]
        if len(parts) != 2 or not all(parts):
            raise CoordinateError("Expected 'latitude, longitude'. If you used a comma as the decimal mark, "
                                  "use '.' instead.")
        a = _parse_single(parts[0], None)
        b = _parse_single(parts[1], None)
        lat, lon = _assign(a, b, parts[0], parts[1])
        return ParsedPosition(lat, lon, _pair_format(a, b))

    tokens = s.split()
    if len(tokens) < 2:
        raise CoordinateError(f"'{s}' is not a recognised position.")
    candidates = []
    first_error = None
    for k in range(1, len(tokens)):
        left, right = " ".join(tokens[:k]), " ".join(tokens[k:])
        try:
            a = _parse_single(left, None)
            b = _parse_single(right, None)
            lat, lon = _assign(a, b, left, right)
        except CoordinateError as exc:
            if first_error is None and "latitude range" in str(exc):
                first_error = exc
            continue
        candidates.append((k, lat, lon, _pair_format(a, b)))
    if not candidates:
        raise first_error or CoordinateError(f"'{s}' is not a recognised position.")
    distinct = {(round(c[1], 9), round(c[2], 9)) for c in candidates}
    if len(distinct) == 1:
        _, lat, lon, fmt = candidates[0]
        return ParsedPosition(lat, lon, fmt)
    half = len(tokens) / 2
    symmetric = [c for c in candidates if c[0] == half]
    if len(symmetric) == 1:
        _, lat, lon, fmt = symmetric[0]
        return ParsedPosition(lat, lon, fmt, notes=["Read as two equal halves; separate the values with a comma "
                                                    "to avoid ambiguity."])
    raise CoordinateError(f"'{s}' is ambiguous; separate latitude and longitude with a comma.")


# --------------------------------------------------------------------------
# geo URI and ISO 6709
# --------------------------------------------------------------------------

_GEO_URI_RE = re.compile(r"^geo:\s*([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)"
                         r"(?:\s*,\s*[+-]?\d+(?:\.\d+)?)?((?:;[^;]*)*)$", re.I)


def _parse_geo_uri(s: str) -> ParsedPosition | None:
    if not s.lower().startswith("geo:"):
        return None
    m = _GEO_URI_RE.match(s)
    if not m:
        raise CoordinateError(f"'{s}' is not a valid geo URI (expected geo:LAT,LON).")
    for param in filter(None, m.group(3).split(";")):
        key, _, val = param.partition("=")
        if key.strip().lower() == "crs" and val.strip().lower() != "wgs84":
            raise CoordinateError("Only WGS-84 geo URIs are supported.")
    lat, lon = float(m.group(1)), float(m.group(2))
    _check_range(lat, LAT)
    _check_range(lon, LON)
    return ParsedPosition(lat, lon, "geo_uri")


_ISO_RE = re.compile(r"^([+-])(\d+(?:\.\d+)?)([+-])(\d+(?:\.\d+)?)(?:[+-]\d+(?:\.\d+)?)?(?:CRS[\w:]+)?/?$", re.I)


def _iso_part(sign: str, text: str, deg_digits: int) -> float:
    whole, _, frac = text.partition(".")
    n = len(whole)
    frac_val = float("0." + frac) if frac else 0.0
    if n == deg_digits:
        value = int(whole) + frac_val
    elif n == deg_digits + 2:
        minutes = int(whole[deg_digits:]) + frac_val
        if minutes >= 60:
            raise CoordinateError("ISO 6709 minutes must be below 60.")
        value = int(whole[:deg_digits]) + minutes / 60.0
    elif n == deg_digits + 4:
        minutes = int(whole[deg_digits:deg_digits + 2])
        seconds = int(whole[deg_digits + 2:]) + frac_val
        if minutes >= 60 or seconds >= 60:
            raise CoordinateError("ISO 6709 minutes and seconds must be below 60.")
        value = int(whole[:deg_digits]) + minutes / 60.0 + seconds / 3600.0
    else:
        raise CoordinateError("ISO 6709 value has an unexpected number of digits.")
    return -value if sign == "-" else value


def _parse_iso6709(s: str) -> ParsedPosition | None:
    m = _ISO_RE.match(s)
    if not m:
        return None
    if len(m.group(2).partition(".")[0]) not in (2, 4, 6) or len(m.group(4).partition(".")[0]) not in (3, 5, 7):
        return None
    lat = _iso_part(m.group(1), m.group(2), 2)
    lon = _iso_part(m.group(3), m.group(4), 3)
    _check_range(lat, LAT)
    _check_range(lon, LON)
    return ParsedPosition(lat, lon, "iso6709")


# --------------------------------------------------------------------------
# UTM and MGRS (WGS-84, Krueger series)
# --------------------------------------------------------------------------

_A = 6_378_137.0
_F = 1 / 298.257223563
_K0 = 0.9996
_N = _F / (2 - _F)
_AA = _A / (1 + _N) * (1 + _N ** 2 / 4 + _N ** 4 / 64)
_ALPHA = (_N / 2 - 2 * _N ** 2 / 3 + 5 * _N ** 3 / 16 + 41 * _N ** 4 / 180,
          13 * _N ** 2 / 48 - 3 * _N ** 3 / 5 + 557 * _N ** 4 / 1440,
          61 * _N ** 3 / 240 - 103 * _N ** 4 / 140,
          49561 * _N ** 4 / 161280)
_BETA = (_N / 2 - 2 * _N ** 2 / 3 + 37 * _N ** 3 / 96 - _N ** 4 / 360,
         _N ** 2 / 48 + _N ** 3 / 15 - 437 * _N ** 4 / 1440,
         17 * _N ** 3 / 480 - 37 * _N ** 4 / 840,
         4397 * _N ** 4 / 161280)
_DELTA = (2 * _N - 2 * _N ** 2 / 3 - 2 * _N ** 3 + 116 * _N ** 4 / 45,
          7 * _N ** 2 / 3 - 8 * _N ** 3 / 5 - 227 * _N ** 4 / 45,
          56 * _N ** 3 / 15 - 136 * _N ** 4 / 35,
          4279 * _N ** 4 / 630)
_BANDS = "CDEFGHJKLMNPQRSTUVWX"


def _central_meridian(zone: int) -> float:
    return (zone - 1) * 6 - 180 + 3


def utm_zone(lat: float, lon: float) -> int:
    lon = ((lon + 180) % 360) - 180
    zone = int((lon + 180) // 6) + 1
    if zone > 60:
        zone = 60
    if 56 <= lat < 64 and 3 <= lon < 12:
        zone = 32
    if 72 <= lat < 84 and lon >= 0:
        if lon < 9:
            zone = 31
        elif lon < 21:
            zone = 33
        elif lon < 33:
            zone = 35
        elif lon < 42:
            zone = 37
    return zone


def latitude_band(lat: float) -> str:
    if not -80 <= lat <= 84:
        raise CoordinateError("UTM/MGRS covers latitudes 80\u00b0S to 84\u00b0N only.")
    return _BANDS[min(int((lat + 80) // 8), len(_BANDS) - 1)]


def _band_range(band: str) -> tuple[float, float]:
    i = _BANDS.index(band)
    south = -80 + 8 * i
    north = 84 if band == "X" else south + 8
    return south, north


def to_utm(lat: float, lon: float, zone: int | None = None) -> tuple[int, str, float, float]:
    """Return (zone, band, easting, northing)."""
    zone = zone or utm_zone(lat, lon)
    band = latitude_band(lat)
    phi = math.radians(lat)
    lam = math.radians(lon - _central_meridian(zone))
    lam = math.atan2(math.sin(lam), math.cos(lam))
    c = 2 * math.sqrt(_N) / (1 + _N)
    t = math.sinh(math.atanh(math.sin(phi)) - c * math.atanh(c * math.sin(phi)))
    xi = math.atan2(t, math.cos(lam))
    eta = math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
    east = eta + sum(a * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, a in enumerate(_ALPHA, 1))
    north = xi + sum(a * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, a in enumerate(_ALPHA, 1))
    easting = 500_000 + _K0 * _AA * east
    northing = _K0 * _AA * north + (10_000_000 if lat < 0 else 0)
    return zone, band, easting, northing


def from_utm(zone: int, easting: float, northing: float, northern: bool) -> tuple[float, float]:
    if not 1 <= zone <= 60:
        raise CoordinateError("UTM zone must be between 1 and 60.")
    if not 0 < easting < 1_000_000 or not 0 <= northing <= 10_000_000:
        raise CoordinateError("UTM easting or northing is out of range.")
    xi = (northing - (0 if northern else 10_000_000)) / (_K0 * _AA)
    eta = (easting - 500_000) / (_K0 * _AA)
    xi_p = xi - sum(b * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, b in enumerate(_BETA, 1))
    eta_p = eta - sum(b * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, b in enumerate(_BETA, 1))
    chi = math.asin(math.sin(xi_p) / math.cosh(eta_p))
    phi = chi + sum(d * math.sin(2 * j * chi) for j, d in enumerate(_DELTA, 1))
    lam = math.atan2(math.sinh(eta_p), math.cos(xi_p))
    lat = math.degrees(phi)
    lon = _central_meridian(zone) + math.degrees(lam)
    lon = ((lon + 180) % 360) - 180
    return lat, lon


def _in_band(lat: float, band: str, tolerance: float = 0.05) -> bool:
    south, north = _band_range(band)
    return south - tolerance <= lat <= north + tolerance


_UTM_RE = re.compile(r"^(\d{1,2})\s*([A-Za-z]{1,5})\s+(\d{6}(?:\.\d+)?)\s*(?:m?E)?[\s,]+(\d{1,8}(?:\.\d+)?)\s*(?:m?N)?$",
                     re.I)


def _parse_utm(s: str) -> ParsedPosition | None:
    m = _UTM_RE.match(s)
    if not m:
        return None
    zone = int(m.group(1))
    letter = m.group(2).upper()
    easting, northing = float(m.group(3)), float(m.group(4))
    if letter in ("NORTH", "SOUTH"):
        lat, lon = from_utm(zone, easting, northing, letter == "NORTH")
        return ParsedPosition(lat, lon, "utm")
    if len(letter) != 1 or letter not in _BANDS:
        raise CoordinateError(f"'{m.group(2)}' is not a UTM latitude band or hemisphere.")
    lat, lon = from_utm(zone, easting, northing, letter >= "N")
    if _in_band(lat, letter):
        return ParsedPosition(lat, lon, "utm")
    if letter in "NS":
        lat, lon = from_utm(zone, easting, northing, letter == "N")
        return ParsedPosition(lat, lon, "utm", notes=[f"'{letter}' read as the {'northern' if letter == 'N' else 'southern'} hemisphere."])
    raise CoordinateError(f"UTM position is not inside latitude band {letter}.")


_COL_SETS = ("ABCDEFGH", "JKLMNPQR", "STUVWXYZ")
_ROW_LETTERS = "ABCDEFGHJKLMNPQRSTUV"
_MGRS_RE = re.compile(r"^(\d{1,2})\s*([C-HJ-NP-X])\s*([A-HJ-NP-Z])\s*([A-HJ-NP-V])\s*(\d*)\s*(\d*)$", re.I)


def to_mgrs(lat: float, lon: float, digits: int = 5) -> str:
    zone, band, easting, northing = to_utm(lat, lon)
    col = _COL_SETS[(zone - 1) % 3][int(easting // 100_000) - 1]
    row = _ROW_LETTERS[(int(northing // 100_000) + (5 if zone % 2 == 0 else 0)) % 20]
    scale = 10 ** (5 - digits)
    e = int(easting % 100_000) // scale
    n = int(northing % 100_000) // scale
    return f"{zone}{band}{col}{row}{e:0{digits}d}{n:0{digits}d}"


def _parse_mgrs(s: str) -> ParsedPosition | None:
    m = _MGRS_RE.match(s)
    if not m:
        return None
    zone = int(m.group(1))
    band, col, row = m.group(2).upper(), m.group(3).upper(), m.group(4).upper()
    g5, g6 = m.group(5), m.group(6)
    if g6:
        if len(g5) != len(g6):
            raise CoordinateError("MGRS easting and northing must have the same number of digits.")
        e_txt, n_txt = g5, g6
    else:
        if len(g5) % 2:
            raise CoordinateError("MGRS needs an even number of digits.")
        e_txt, n_txt = g5[:len(g5) // 2], g5[len(g5) // 2:]
    digits = len(e_txt)
    if digits > 5:
        raise CoordinateError("MGRS supports at most 5 digits each for easting and northing.")
    if not 1 <= zone <= 60:
        raise CoordinateError("MGRS zone must be between 1 and 60.")
    col_set = _COL_SETS[(zone - 1) % 3]
    if col not in col_set:
        raise CoordinateError(f"MGRS column letter {col} is not valid for zone {zone}.")
    cell = 10 ** (5 - digits)
    e_in = (int(e_txt) if e_txt else 0) * cell + cell / 2
    n_in = (int(n_txt) if n_txt else 0) * cell + cell / 2
    easting = (col_set.index(col) + 1) * 100_000 + e_in
    row_index = (_ROW_LETTERS.index(row) - (5 if zone % 2 == 0 else 0)) % 20
    northing_cycle = row_index * 100_000 + n_in

    northern = band >= "N"
    south, _ = _band_range(band)
    _, _, _, min_northing = to_utm(south, _central_meridian(zone), zone)
    northing = northing_cycle
    while northing < min_northing - 100_000:
        northing += 2_000_000
    lat, lon = from_utm(zone, easting, northing, northern)
    if not _in_band(lat, band, tolerance=0.5):
        raise CoordinateError(f"MGRS square {col}{row} is not inside latitude band {band}.")
    result = ParsedPosition(lat, lon, "mgrs")
    if digits < 5:
        result.notes.append(f"MGRS precision is {cell:g} m; using the centre of the square.")
    if cell > COARSE_CELL_M:
        result.warnings.append(f"MGRS position is only precise to {cell:g} m.")
    return result


# --------------------------------------------------------------------------
# Maidenhead and Plus codes
# --------------------------------------------------------------------------

_MAIDENHEAD_RE = re.compile(r"^[A-R]{2}\d{2}(?:[A-X]{2}(?:\d{2}(?:[A-X]{2})?)?)?$", re.I)


def _cell_note(kind: str, result: ParsedPosition, lat_size: float, lon_size: float) -> None:
    h = lat_size * _M_PER_DEG
    w = lon_size * _M_PER_DEG * math.cos(math.radians(result.lat))
    result.notes.append(f"{kind} cell is about {_fmt_size(w)} \u00d7 {_fmt_size(h)}; using its centre.")
    if max(h, w) > COARSE_CELL_M:
        result.warnings.append(f"{kind} position is only precise to about {_fmt_size(max(h, w))}, "
                               "which is too coarse for exact tower placement.")


def _fmt_size(m: float) -> str:
    return f"{m / 1000:.1f} km" if m >= 1000 else f"{m:.0f} m"


def _parse_maidenhead(s: str) -> ParsedPosition | None:
    if not _MAIDENHEAD_RE.match(s):
        return None
    loc = s.upper()
    lon = (ord(loc[0]) - 65) * 20.0 - 180.0
    lat = (ord(loc[1]) - 65) * 10.0 - 90.0
    lon_size, lat_size = 2.0, 1.0
    lon += int(loc[2]) * lon_size
    lat += int(loc[3]) * lat_size
    pairs = [loc[i:i + 2] for i in range(4, len(loc), 2)]
    for i, pair in enumerate(pairs):
        if i % 2 == 0:
            lon_size, lat_size = lon_size / 24, lat_size / 24
            lon += (ord(pair[0]) - 65) * lon_size
            lat += (ord(pair[1]) - 65) * lat_size
        else:
            lon_size, lat_size = lon_size / 10, lat_size / 10
            lon += int(pair[0]) * lon_size
            lat += int(pair[1]) * lat_size
    result = ParsedPosition(lat + lat_size / 2, lon + lon_size / 2, "maidenhead")
    _cell_note("Maidenhead", result, lat_size, lon_size)
    return result


_OLC = "23456789CFGHJMPQRVWX"
_OLC_FULL_RE = re.compile(r"^([23456789CFGHJMPQRVWX0]{8})\+([23456789CFGHJMPQRVWX]*)$", re.I)
_OLC_SHORT_RE = re.compile(r"^[23456789CFGHJMPQRVWX]{2,7}\+[23456789CFGHJMPQRVWX]*$", re.I)
_OLC_RES = (20.0, 1.0, 0.05, 0.0025, 0.000125)


def _parse_plus_code(s: str) -> ParsedPosition | None:
    code = s.replace(" ", "").upper()
    if _OLC_SHORT_RE.match(code) and not _OLC_FULL_RE.match(code):
        raise CoordinateError("Short Plus codes need a nearby town; use the full code (8 characters before '+').")
    m = _OLC_FULL_RE.match(code)
    if not m:
        return None
    head, tail = m.group(1), m.group(2)
    if len(tail) == 1:
        raise CoordinateError("A Plus code can't have exactly one character after '+'.")
    if "0" in head:
        pad = head.index("0")
        if pad % 2 or pad == 0 or set(head[pad:]) != {"0"} or tail:
            raise CoordinateError("Invalid Plus code padding.")
        head = head[:pad]
    digits = head + tail
    if _OLC.index(digits[0]) > 8 or _OLC.index(digits[1]) > 17:
        raise CoordinateError("Plus code is out of range.")
    lat, lon = -90.0, -180.0
    lat_size = lon_size = 20.0
    pair_digits = digits[:10]
    for i in range(0, len(pair_digits), 2):
        res = _OLC_RES[i // 2]
        lat += _OLC.index(pair_digits[i]) * res
        lon += _OLC.index(pair_digits[i + 1]) * res
        lat_size = lon_size = res
    for ch in digits[10:]:
        idx = _OLC.index(ch)
        lat_size /= 5
        lon_size /= 4
        lat += (idx // 4) * lat_size
        lon += (idx % 4) * lon_size
    result = ParsedPosition(lat + lat_size / 2, lon + lon_size / 2, "pluscode")
    _cell_note("Plus code", result, lat_size, lon_size)
    return result


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

_POSITION_PARSERS = (_parse_geo_uri, _parse_iso6709, _parse_plus_code, _parse_maidenhead,
                     _parse_mgrs, _parse_utm, _parse_pair)


def parse_position(text: str) -> ParsedPosition:
    """Parse a whole position (latitude and longitude) in any supported format."""
    if not isinstance(text, str):
        raise CoordinateError("Position must be a string.")
    s = _normalize(text)
    if not s:
        raise CoordinateError("Position is empty.")
    for parser in _POSITION_PARSERS:
        result = parser(s)
        if result is not None:
            result.lon = _wrap_lon(result.lon)
            return result
    raise CoordinateError(f"'{text}' is not a recognised position.")


def _wrap_lon(lon: float) -> float:
    return 180.0 if lon == -180.0 else lon


# --------------------------------------------------------------------------
# Display
# --------------------------------------------------------------------------

DISPLAY_FORMATS = ("decimal", "dms", "ddm")


def _dms(value: float, pos: str, neg: str) -> str:
    hemi = pos if value >= 0 else neg
    tenths = round(abs(value) * 36000)
    deg, rem = divmod(tenths, 36000)
    minutes, sec_tenths = divmod(rem, 600)
    return f"{deg}\u00b0{minutes:02d}'{sec_tenths / 10:04.1f}\"{hemi}"


def _ddm(value: float, pos: str, neg: str) -> str:
    hemi = pos if value >= 0 else neg
    units = round(abs(value) * 600000)
    deg, rem = divmod(units, 600000)
    return f"{hemi}{deg}\u00b0{rem / 10000:07.4f}'"


def format_position(lat: float, lon: float, style: str = "decimal") -> str:
    if style == "decimal":
        return f"{lat:.7f}, {lon:.7f}"
    if style == "dms":
        return f"{_dms(lat, 'N', 'S')} {_dms(lon, 'E', 'W')}"
    if style == "ddm":
        return f"{_ddm(lat, 'N', 'S')} {_ddm(lon, 'E', 'W')}"
    raise ValueError(f"unknown display format {style!r}")
