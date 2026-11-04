import pytest

from fresnelkml import coords, geo
from fresnelkml.coords import CoordinateError, format_position, parse_coordinate, parse_position
from fresnelkml.webapi import handle_request

LAT0, LON0 = 37.0341667, -122.0833333


@pytest.mark.parametrize("text,fmt", [
    ("37.034167", "decimal"), ("+37.034167", "decimal"), ("37.034167N", "decimal"), ("N37.034167", "decimal"),
    ("37°02'03\"N", "dms"), ("37° 2′ 3.0″ N", "dms"), ("37 02 03 N", "dms"), ("37:02:03N", "dms"),
    ("37d02m03sN", "dms"), ("37°02.050'N", "ddm"), ("N37 02.050", "ddm"), ("37 2.05N", "ddm"),
])
def test_latitude_formats(text, fmt):
    value, detected = parse_coordinate(text, "lat")
    assert value == pytest.approx(LAT0, abs=1e-6)
    assert detected == fmt


@pytest.mark.parametrize("text,fmt", [
    ("-122.083333", "decimal"), ("122.083333 W", "decimal"), ("-122 05 00", "dms"), ("-122 05.000", "ddm"),
    ("122°05'00\"W", "dms"), ("W122 05.000", "ddm"),
])
def test_longitude_formats(text, fmt):
    value, detected = parse_coordinate(text, "lon")
    assert value == pytest.approx(LON0, abs=1e-6)
    assert detected == fmt


def test_numbers_are_decimal():
    assert parse_coordinate(37.5, "lat") == (37.5, "decimal")
    assert parse_coordinate(-180, "lon") == (180.0, "decimal")


@pytest.mark.parametrize("text,fmt,tol", [
    ("37.034167, -122.083333", "decimal", 1e-6),
    ("37°02'03\"N 122°05'00\"W", "dms", 1e-6),
    ("N37 02.050 W122 05.000", "ddm", 1e-6),
    ("37 02 03 -122 05 00", "dms", 1e-6),
    ("37.034167 -122.083333", "decimal", 1e-6),
    ("122°05'00\"W, 37°02'03\"N", "dms", 1e-6),
    ("+37.034167-122.083333/", "iso6709", 1e-6),
    ("+370203-1220500/", "iso6709", 1e-6),
    ("geo:37.034167,-122.083333", "geo_uri", 1e-6),
    ("geo:37.034167,-122.083333,12;crs=wgs84;u=5", "geo_uri", 1e-6),
    ("10S 581526 4099055", "utm", 1e-5),
    ("10N 581526 4099055", "utm", 1e-5),
    ("10 north 581526mE 4099055mN", "utm", 1e-5),
    ("10SEF8152599055", "mgrs", 1e-5),
    ("10S EF 81525 99055", "mgrs", 1e-5),
    ("849V2WM8+MM", "pluscode", 1e-4),
])
def test_position_formats(text, fmt, tol):
    p = parse_position(text)
    assert p.lat == pytest.approx(LAT0, abs=tol)
    assert p.lon == pytest.approx(LON0, abs=tol)
    assert p.format == fmt


@pytest.mark.parametrize("text,lat,lon,coarse", [
    ("CM87", 37.5, -123.0, True),
    ("CM87xa", 37.0208333, -122.0416667, True),
    ("CM87xa08", 37.0354167, -122.0791667, True),
    ("cm87xa08qr", 37.0363715, -122.0776042, False),
])
def test_maidenhead(text, lat, lon, coarse):
    p = parse_position(text)
    assert p.format == "maidenhead"
    assert p.lat == pytest.approx(lat, abs=1e-6)
    assert p.lon == pytest.approx(lon, abs=1e-6)
    assert bool(p.warnings) is coarse


def test_plus_code_cell_and_padding():
    p = parse_position("849V2WM8+MM")
    assert p.notes and not p.warnings
    padded = parse_position("849V0000+")
    assert padded.warnings
    assert padded.lat == pytest.approx(37.5)
    assert padded.lon == pytest.approx(-122.5)


@pytest.mark.parametrize("text,message", [
    ("37,5", "comma"),
    ("-37N", "minus sign"),
    ("37 60 00", "below 60"),
    ("37 02.5 03", "decimal part"),
    ("95", "out of range"),
    ("37E", "doesn't fit"),
])
def test_latitude_errors(text, message):
    with pytest.raises(CoordinateError, match=message):
        parse_coordinate(text, "lat")


@pytest.mark.parametrize("text,message", [
    ("-122.08, 37.03", "looks like longitude"),
    ("37,5, -122,3", "decimal mark"),
    ("9V2WM8+MM", "full code"),
    ("10Q 581526 4099055", "band Q"),
    ("37N, 38N", "same axis"),
    ("", "empty"),
    ("hello", "not a recognised"),
])
def test_position_errors(text, message):
    with pytest.raises(CoordinateError, match=message):
        parse_position(text)


@pytest.mark.parametrize("lat,lon", [(37.0341667, -122.0833333), (-33.8688, 151.2093), (60.0, 5.0), (78.0, 15.0),
                                     (0.5, 0.5), (-45.5, -70.25), (64.1, 10.9)])
def test_utm_mgrs_round_trip(lat, lon):
    zone, band, e, n = coords.to_utm(lat, lon)
    hemisphere = "north" if lat >= 0 else "south"
    p = parse_position(f"{zone} {hemisphere} {e:.3f} {n:.3f}")
    assert (p.lat, p.lon) == (pytest.approx(lat, abs=1e-7), pytest.approx(lon, abs=1e-7))
    p = parse_position(coords.to_mgrs(lat, lon))
    assert geo.haversine(lat, lon, p.lat, p.lon) < 1.5


def test_utm_matches_pyproj_when_installed():
    pyproj = pytest.importorskip("pyproj")
    for lat, lon in [(37.03, -122.08), (-45.5, -70.25), (64.1, 10.9), (83.5, 40.0), (-79.5, -179.0)]:
        zone, band, e, n = coords.to_utm(lat, lon)
        epsg = (32600 if lat >= 0 else 32700) + zone
        pe, pn = pyproj.Transformer.from_crs(4326, epsg, always_xy=True).transform(lon, lat)
        assert e == pytest.approx(pe, abs=0.001)
        assert n == pytest.approx(pn, abs=0.001)


def test_utm_special_zones():
    assert coords.utm_zone(60.0, 5.0) == 32
    assert coords.utm_zone(78.0, 15.0) == 33
    assert coords.utm_zone(78.0, 8.0) == 31


@pytest.mark.parametrize("lat,lon,zone,band,e,n", [
    # reference values from PROJ (pyproj 3.8, EPSG:326xx/327xx)
    (40.74844, -73.984559, 18, "T", 585724.6838, 4511328.0016),
    (37.0341667, -122.0833333, 10, "S", 581525.8537, 4099055.4139),
    (-33.8688, 151.2093, 56, "H", 334368.6336, 6250948.3454),
    (78.0, 15.0, 33, "X", 500000.0000, 8658369.5858),
    (60.0, 5.0, 32, "V", 276979.9264, 6658157.2024),
    (0.5, 0.5, 31, "N", 221734.2217, 55318.0400),
])
def test_utm_reference_points(lat, lon, zone, band, e, n):
    z, b, e2, n2 = coords.to_utm(lat, lon)
    assert (z, b) == (zone, band)
    assert e2 == pytest.approx(e, abs=0.001)
    assert n2 == pytest.approx(n, abs=0.001)
    p = parse_position(f"{zone}{band} {e} {n}")
    assert p.lat == pytest.approx(lat, abs=1e-8)
    assert p.lon == pytest.approx(lon, abs=1e-8)


def test_utm_southern_hemisphere_s_letter():
    zone, band, e, n = coords.to_utm(-33.8688, 151.2093)
    p = parse_position(f"{zone}S {e:.0f} {n:.0f}")
    assert p.lat == pytest.approx(-33.8688, abs=1e-4)
    assert p.notes


def test_mgrs_low_precision_warns():
    p = parse_position("10SEF8199")
    assert p.warnings and "1000 m" in p.warnings[0]


@pytest.mark.parametrize("style,expected", [
    ("decimal", "37.0341667, -122.0833333"),
    ("dms", "37°02'03.0\"N 122°05'00.0\"W"),
    ("ddm", "N37°02.0500' W122°05.0000'"),
])
def test_display(style, expected):
    assert format_position(LAT0, LON0, style) == expected


def test_display_round_trips():
    for style in ("decimal", "dms", "ddm"):
        text = format_position(LAT0, LON0, style)
        p = parse_position(text)
        assert p.lat == pytest.approx(LAT0, abs=1e-4)
        assert p.lon == pytest.approx(LON0, abs=1e-4)


def test_dms_rounding_carries():
    assert format_position(10.999999999, 0, "dms").startswith("11°00'00.0\"N")


def test_parse_endpoint():
    import json
    resp = handle_request("GET", "parse=CM87xa08", env={})
    data = json.loads(resp.body)
    assert resp.status == 200 and data["format"] == "maidenhead" and data["notes"]
    resp = handle_request("GET", "parse=37%C2%B002%2703%22N&axis=lat", env={})
    data = json.loads(resp.body)
    assert data["lat"] == pytest.approx(LAT0, abs=1e-6) and data["format"] == "dms"
    resp = handle_request("GET", "parse=nonsense", env={})
    assert resp.status == 400
