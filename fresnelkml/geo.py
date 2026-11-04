"""Geodesy helpers: distance, bearings, path stations and small local offsets.

Uses pyproj's WGS-84 geodesics (Karney) when pyproj is installed and a
spherical model (R = 6 371 008.8 m) otherwise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

try:  # pragma: no cover - exercised only when pyproj is installed
    from pyproj import Geod as _Geod

    _GEOD = _Geod(ellps="WGS84")
except Exception:  # noqa: BLE001 - any import/initialisation failure means "no pyproj"
    _GEOD = None

MEAN_RADIUS_M = 6_371_008.8
WGS84_A = 6_378_137.0
WGS84_E2 = 6.69437999014e-3
POLAR_LIMIT_DEG = 85.0


def have_pyproj() -> bool:
    return _GEOD is not None


def normalize_lon(lon: float) -> float:
    """Normalise a longitude to the interval (-180, 180]."""
    lon = math.fmod(lon, 360.0)
    if lon <= -180.0:
        lon += 360.0
    elif lon > 180.0:
        lon -= 360.0
    return lon


def _norm_bearing(deg: float) -> float:
    deg = math.fmod(deg, 360.0)
    return deg + 360.0 if deg < 0 else deg


def haversine(lat1: float, lon1: float, lat2: float, lon2: float, radius: float = MEAN_RADIUS_M) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(h)))


def initial_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return _norm_bearing(math.degrees(math.atan2(y, x)))


def spherical_destination(lat: float, lon: float, bearing_deg: float, dist: float,
                          radius: float = MEAN_RADIUS_M) -> tuple[float, float]:
    p1, l1 = math.radians(lat), math.radians(lon)
    th = math.radians(bearing_deg)
    d = dist / radius
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(th))
    l2 = l1 + math.atan2(math.sin(th) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), normalize_lon(math.degrees(l2))


def inverse(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float, float]:
    """Return (distance_m, bearing A->B, bearing B->A), bearings in [0, 360)."""
    if _GEOD is not None:
        az12, az21, dist = _GEOD.inv(lon1, lat1, lon2, lat2)
        return dist, _norm_bearing(az12), _norm_bearing(az21)
    dist = haversine(lat1, lon1, lat2, lon2)
    return dist, initial_bearing(lat1, lon1, lat2, lon2), initial_bearing(lat2, lon2, lat1, lon1)


def forward(lat: float, lon: float, bearing_deg: float, dist: float) -> tuple[float, float, float]:
    """Return (lat, lon, forward bearing at the destination)."""
    if _GEOD is not None:
        lon2, lat2, back = _GEOD.fwd(lon, lat, bearing_deg, dist)
        return lat2, normalize_lon(lon2), _norm_bearing(back + 180.0)
    lat2, lon2 = spherical_destination(lat, lon, bearing_deg, dist)
    back = initial_bearing(lat2, lon2, lat, lon)
    return lat2, lon2, _norm_bearing(back + 180.0)


@dataclass(frozen=True)
class PathPoint:
    t: float
    lat: float
    lon: float
    bearing: float  # local forward bearing of the path, degrees clockwise from north


def path_points(lat1: float, lon1: float, lat2: float, lon2: float, n: int) -> list[PathPoint]:
    """n + 1 points at fractions t = i/n along the geodesic from A to B."""
    if n < 1:
        raise ValueError("n must be at least 1")
    dist, az_ab, az_ba = inverse(lat1, lon1, lat2, lon2)
    points = []
    for i in range(n + 1):
        t = i / n
        if i == 0:
            points.append(PathPoint(0.0, lat1, normalize_lon(lon1), az_ab))
        elif i == n:
            points.append(PathPoint(1.0, lat2, normalize_lon(lon2), _norm_bearing(az_ba + 180.0)))
        else:
            lat, lon, brg = forward(lat1, lon1, az_ab, t * dist)
            points.append(PathPoint(t, lat, lon, brg))
    return points


def metres_per_degree(lat: float) -> tuple[float, float]:
    """(metres per degree of latitude, metres per degree of longitude) on WGS-84."""
    phi = math.radians(lat)
    w = math.sqrt(1.0 - WGS84_E2 * math.sin(phi) ** 2)
    m_lat = math.pi / 180.0 * WGS84_A * (1.0 - WGS84_E2) / w ** 3
    m_lon = math.pi / 180.0 * WGS84_A * math.cos(phi) / w
    return m_lat, m_lon


def offset(lat: float, lon: float, east_m: float, north_m: float) -> tuple[float, float]:
    """Move a point by a small east/north offset in metres."""
    if east_m == 0.0 and north_m == 0.0:
        return lat, normalize_lon(lon)
    if abs(lat) > POLAR_LIMIT_DEG:
        dist = math.hypot(east_m, north_m)
        brg = math.degrees(math.atan2(east_m, north_m))
        lat2, lon2, _ = forward(lat, lon, brg, dist)
        return lat2, lon2
    m_lat, m_lon = metres_per_degree(lat)
    return lat + north_m / m_lat, normalize_lon(lon + east_m / m_lon)


def unwrap_lons(lons: list[float]) -> list[float]:
    """Keep a small polygon contiguous across the antimeridian.

    If the longitudes span more than 180 degrees, the polygon straddles +/-180,
    so 360 is added to its negative longitudes.
    """
    if lons and max(lons) - min(lons) > 180.0:
        return [lon + 360.0 if lon < 0 else lon for lon in lons]
    return lons
