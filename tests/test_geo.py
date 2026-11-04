import math

import pytest

from fresnelkml import geo


def test_t6_haversine_equator():
    assert geo.haversine(0, 0, 0, 1) == pytest.approx(111_195, abs=1)
    assert geo.initial_bearing(0, 0, 0, 1) == pytest.approx(90.0)


def test_t7_bay_area_link():
    dist, az_ab, az_ba = geo.inverse(37.0, -122.0, 37.05, -121.9)
    assert dist == pytest.approx(10_400, rel=0.01)
    assert dist == pytest.approx(geo.haversine(37.0, -122.0, 37.05, -121.9), rel=0.005)
    assert az_ab == pytest.approx(57.9, abs=0.3)
    assert az_ba == pytest.approx(238.0, abs=0.3)


@pytest.mark.skipif(not geo.have_pyproj(), reason="pyproj not installed")
def test_t7_against_pyproj():  # pragma: no cover - only with pyproj
    from pyproj import Geod
    _, _, d = Geod(ellps="WGS84").inv(-122.0, 37.0, -121.9, 37.05)
    assert geo.haversine(37.0, -122.0, 37.05, -121.9) == pytest.approx(d, rel=0.005)


def test_t11_antimeridian_distance():
    dist, az, _ = geo.inverse(0.0, 179.99, 0.0, -179.99)
    assert dist == pytest.approx(2224, rel=0.01)
    assert az == pytest.approx(90.0, abs=0.01)
    dist40, _, _ = geo.inverse(40.0, 179.99, 40.0, -179.99)
    assert dist40 == pytest.approx(2224 * math.cos(math.radians(40)), rel=0.01)


def test_path_points_across_antimeridian_stay_in_range():
    pts = geo.path_points(0.0, 179.99, 0.0, -179.99, 16)
    assert len(pts) == 17
    for p in pts:
        assert -180 < p.lon <= 180
        assert abs(p.bearing - 90) < 0.01


def test_path_points_endpoints_and_spacing():
    pts = geo.path_points(37.0, -122.0, 37.05, -121.9, 10)
    assert (pts[0].lat, pts[0].lon) == (37.0, -122.0)
    assert (pts[-1].lat, pts[-1].lon) == (37.05, -121.9)
    total = geo.inverse(37.0, -122.0, 37.05, -121.9)[0]
    for p in pts[1:-1]:
        d = geo.inverse(37.0, -122.0, p.lat, p.lon)[0]
        assert d == pytest.approx(p.t * total, abs=0.5)


@pytest.mark.parametrize("lon,expected", [(180, 180), (-180, 180), (190, -170), (-190, 170), (540, 180), (0, 0)])
def test_normalize_lon(lon, expected):
    assert geo.normalize_lon(lon) == pytest.approx(expected)


def test_offset_round_trip():
    lat, lon = geo.offset(45.0, 10.0, 30.0, -40.0)
    m_lat, m_lon = geo.metres_per_degree(45.0)
    assert (lon - 10.0) * m_lon == pytest.approx(30.0, abs=1e-6)
    assert (lat - 45.0) * m_lat == pytest.approx(-40.0, abs=1e-6)


def test_offset_near_pole_uses_forward():
    lat, lon = geo.offset(89.0, 0.0, 0.0, 50.0)
    assert geo.haversine(89.0, 0.0, lat, lon) == pytest.approx(50.0, rel=0.01)


def test_unwrap_lons():
    assert geo.unwrap_lons([179.9, -179.9]) == [179.9, 180.1]
    assert geo.unwrap_lons([10.0, 10.1]) == [10.0, 10.1]
