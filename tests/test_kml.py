import math
import xml.etree.ElementTree as ET

import pytest

from fresnelkml import Options, Site, analyze, geo, render_kml

NS = {"k": "http://www.opengis.net/kml/2.2"}


def zone_placemark(root, name="Fresnel zone 1"):
    for pm in root.iterfind(".//k:Placemark", NS):
        if pm.find("k:name", NS).text == name:
            return pm
    raise AssertionError(name)


def parse_coords(text):
    return [tuple(float(v) for v in c.split(",")) for c in text.split()]


@pytest.fixture
def link():
    return Site("A", 37.0, -122.0, asl_m=150), Site("B", 37.05, -121.9, asl_m=180)


def test_t8_structure(link):
    report = analyze(*link, Options(stations=64, ring_points=24))
    kml = render_kml(report)
    root = ET.fromstring(kml)
    assert root.tag == "{http://www.opengis.net/kml/2.2}kml"
    for el in root.iter("{http://www.opengis.net/kml/2.2}coordinates"):
        for triple in el.text.split():
            assert len(triple.split(",")) == 3
    zone = zone_placemark(root)
    polys = zone.findall(".//k:Polygon", NS)
    assert len(polys) == 64 * 24
    for poly in polys:
        assert poly.find("k:altitudeMode", NS).text == "absolute"
        assert poly.find("k:extrude", NS) is None and poly.find("k:tessellate", NS) is None
        pts = parse_coords(poly.find(".//k:coordinates", NS).text)
        assert pts[0] == pts[-1]
        assert len(pts) in (4, 5)
    folders = [f.find("k:name", NS).text for f in root.iterfind(".//k:Folder", NS)]
    assert folders == ["Sites", "Line of sight", "Fresnel zone 1 (100%)", "Fresnel 60%"]
    sixty = [f for f in root.iterfind(".//k:Folder", NS) if f.find("k:name", NS).text == "Fresnel 60%"][0]
    assert sixty.find("k:visibility", NS).text == "0"


def test_t8_coordinate_precision(link):
    kml = render_kml(analyze(*link))
    root = ET.fromstring(kml)
    text = zone_placemark(root).find(".//k:coordinates", NS).text.split()[0]
    lon, lat, alt = text.split(",")
    assert len(lon.split(".")[1]) == 7 and len(lat.split(".")[1]) == 7 and len(alt.split(".")[1]) == 2


def test_t9_ring_radius_and_orientation(link):
    report = analyze(*link, Options(stations=16, ring_points=12))
    from fresnelkml.kml import _ring
    for st in report.stations[1:-1]:
        m_lat, m_lon = geo.metres_per_degree(st.lat)
        fwd = (math.sin(math.radians(st.bearing)), math.cos(math.radians(st.bearing)))
        for lon, lat, alt in _ring(st, st.radius_m, 12):
            east = (lon - st.lon) * m_lon
            north = (lat - st.lat) * m_lat
            up = alt - st.los_m
            assert math.sqrt(east ** 2 + north ** 2 + up ** 2) == pytest.approx(st.radius_m, abs=0.01)
            assert abs(east * fwd[0] + north * fwd[1]) < 0.01


def test_zone_touches_tower_tops(link):
    report = analyze(*link)
    root = ET.fromstring(render_kml(report))
    los = parse_coords(zone_placemark(root, "Line of sight").find(".//k:coordinates", NS).text)
    assert los[0][2] == 150.0 and los[-1][2] == 180.0
    first = parse_coords(zone_placemark(root).find(".//k:Polygon//k:coordinates", NS).text)[0]
    assert first == pytest.approx((-122.0, 37.0, 150.0))


def test_rings_style(link):
    report = analyze(*link, Options(style="rings", stations=16, ring_every=4))
    root = ET.fromstring(render_kml(report))
    zone = zone_placemark(root)
    assert not zone.findall(".//k:Polygon", NS)
    assert len(zone.findall(".//k:LineString", NS)) == 3


def test_no_sixty(link):
    root = ET.fromstring(render_kml(analyze(*link, Options(sixty=False))))
    assert "Fresnel 60%" not in [f.find("k:name", NS).text for f in root.iterfind(".//k:Folder", NS)]


def test_relative_mode(link):
    a = Site("A", 37.0, -122.0, agl_m=30, ground_m=120)
    b = Site("B", 37.05, -121.9, agl_m=25, ground_m=150)
    report = analyze(a, b, Options(mode="relative"))
    root = ET.fromstring(render_kml(report))
    pm = zone_placemark(root, "A")
    assert pm.find("k:Point/k:altitudeMode", NS).text == "relativeToGround"
    assert pm.find("k:Point/k:coordinates", NS).text.endswith(",30.00")
    assert any("Relative mode" in w for w in report.warnings)
    assert zone_placemark(root).find(".//k:altitudeMode", NS).text == "absolute"


def test_terrain_folder(fake_chain):
    chain, _ = fake_chain(lambda lat, lon: 165.0)
    report = analyze(Site("A", 37.0, -122.0, asl_m=150), Site("B", 37.05, -121.9, asl_m=180),
                     Options(terrain_check=True), elevation=chain)
    root = ET.fromstring(render_kml(report))
    folder = [f for f in root.iterfind(".//k:Folder", NS) if f.find("k:name", NS).text == "Terrain check"][0]
    profile = folder.find("k:Placemark/k:LineString", NS)
    assert profile.find("k:altitudeMode", NS).text == "clampToGround"
    marks = folder.findall("k:Placemark/k:Point", NS)
    assert len(marks) == len(report.obstructions) > 0
    assert report.status == "BLOCKED"


def test_t11_antimeridian_kml():
    a = Site("A", 10.0, 179.99, asl_m=50)
    b = Site("B", 10.0, -179.99, asl_m=50)
    report = analyze(a, b, Options(stations=16, ring_points=8))
    assert report.distance_m == pytest.approx(2224 * math.cos(math.radians(10)), rel=0.01)
    root = ET.fromstring(render_kml(report))
    for el in root.iter("{http://www.opengis.net/kml/2.2}coordinates"):
        pts = parse_coords(el.text)
        lons = [p[0] for p in pts]
        assert max(lons) - min(lons) < 1.0
        assert all(-180 < geo.normalize_lon(lon) <= 180 for lon in lons)
        if el.text and len(pts) <= 5:
            assert all(-180 < lon <= 540 for lon in lons)
