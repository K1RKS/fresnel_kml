import pytest

from fresnelkml import Options, Site, analyze
from fresnelkml.analysis import AnalysisError
from fresnelkml.parsing import RequestError, parse_request, parse_site_string, read_sites_csv


def req(site_a=None, site_b=None, **extra):
    data = {
        "site_a": site_a or {"name": "A", "lat": 37.0, "lon": -122.0, "asl_m": 150},
        "site_b": site_b or {"name": "B", "lat": 37.05, "lon": -121.9, "asl_m": 180},
    }
    data.update(extra)
    return data


def test_exactly_one_height():
    with pytest.raises(RequestError, match="exactly one"):
        parse_request(req({"name": "A", "lat": 37, "lon": -122, "asl_m": 1, "agl_m": 2}))
    with pytest.raises(RequestError, match="exactly one"):
        parse_request(req({"name": "A", "lat": 37, "lon": -122}))


def test_position_or_latlon_not_both():
    with pytest.raises(RequestError, match="not both"):
        parse_request(req({"name": "A", "lat": 37, "lon": -122, "position": "37,-122", "asl_m": 1}))


def test_position_string_and_dms():
    r = parse_request(req({"name": "A", "position": "10S 581526 4099055", "agl_m": 10},
                          {"name": "B", "lat": "37°03'00\"N", "lon": "121 54 W", "agl_m": 10}))
    assert r.site_a.position_format == "utm"
    assert r.site_b.lat == pytest.approx(37.05)
    assert r.site_b.lon == pytest.approx(-121.9)
    assert r.site_b.position_format == "mixed"


def test_feet_conversion():
    r = parse_request(req({"name": "A", "lat": 37, "lon": -122, "asl_m": 1000, "ground_m": 900},
                          options={"units": "ft", "clutter_m": 10}))
    assert r.site_a.asl_m == pytest.approx(304.8)
    assert r.site_a.ground_m == pytest.approx(274.32)
    assert r.options.clutter_m == pytest.approx(3.048)


def test_unknown_fields_rejected_but_google_key_ignored():
    with pytest.raises(RequestError, match="unknown"):
        parse_request(req(options={"colour": "red"}))
    with pytest.raises(RequestError, match="unknown"):
        parse_request(req({"name": "A", "lat": 37, "lon": -122, "asl_m": 1, "height": 3}))
    r = parse_request(req(options={"google_key": "secret"}))
    assert any("ignored" in w for w in r.warnings)


def test_option_types():
    r = parse_request(req(options={"stations": "32", "sixty": "false", "terrain_check": None, "zone": 2}))
    assert r.options.stations == 32 and r.options.sixty is False and r.options.terrain_check is None
    with pytest.raises(RequestError):
        parse_request(req(options={"stations": 3.5}))
    with pytest.raises(RequestError):
        parse_request(req(options={"units": "yards"}))


@pytest.mark.parametrize("text,ref,value", [
    ("TowerA,37.0,-122.0,30", "agl_m", 30.0),
    ("TowerA,37.0,-122.0,30agl", "agl_m", 30.0),
    ("TowerA,37.0,-122.0,412.5asl", "asl_m", 412.5),
    ("TowerA,37.0,-122.0,412.5 ASL", "asl_m", 412.5),
    ("TowerA,10S 581526 4099055,30", "agl_m", 30.0),
    ("TowerA,CM87xa08qr,20asl", "asl_m", 20.0),
])
def test_site_strings(text, ref, value):
    site, _ = parse_site_string(text)
    assert getattr(site, ref) == pytest.approx(value)
    assert site.name == "TowerA"


def test_site_string_errors():
    with pytest.raises(RequestError):
        parse_site_string("TowerA,37.0,30")
    with pytest.raises(RequestError):
        parse_site_string("TowerA,37,-122,30m")


def test_site_string_feet():
    site, _ = parse_site_string("A,37,-122,100asl", units="ft", ground_m=50)
    assert site.asl_m == pytest.approx(30.48)
    assert site.ground_m == pytest.approx(15.24)


def test_csv_both_height_columns(tmp_path):
    path = tmp_path / "sites.csv"
    path.write_text("name,lat,lon,position,agl_m,asl_m\n"
                    "A,37.0,-122.0,,30,\n"
                    "B,,,10S 597811 4100985,,412.5\n")
    (a, b), _ = read_sites_csv(str(path))
    assert a.agl_m == 30 and a.asl_m is None
    assert b.asl_m == 412.5 and b.lat == pytest.approx(37.05, abs=1e-4)


def test_csv_needs_two_rows(tmp_path):
    path = tmp_path / "sites.csv"
    path.write_text("name,lat,lon,agl_m\nA,37,-122,30\n")
    with pytest.raises(RequestError, match="exactly 2"):
        read_sites_csv(str(path))


def test_asl_below_ground_rejected():
    with pytest.raises(AnalysisError, match="below"):
        analyze(Site("A", 37, -122, asl_m=100, ground_m=120), Site("B", 37.05, -121.9, asl_m=150))


def test_relative_mode_needs_agl():
    with pytest.raises(AnalysisError, match="relative"):
        analyze(Site("A", 37, -122, asl_m=100), Site("B", 37.05, -121.9, asl_m=150), Options(mode="relative"))


@pytest.mark.parametrize("site,field", [
    (Site("A", 95, -122, asl_m=1), "site_a.lat"),
    (Site("A", 37, -190, asl_m=1), "site_a.lon"),
    (Site("A", 37, -122, agl_m=-1), "site_a.agl_m"),
    (Site("A", 37, -122, agl_m=1000), "site_a.agl_m"),
    (Site("", 37, -122, agl_m=10), "site_a.name"),
])
def test_site_validation(site, field):
    with pytest.raises(AnalysisError) as exc:
        analyze(site, Site("B", 37.05, -121.9, asl_m=150))
    assert exc.value.field == field


def test_identical_points_error():
    with pytest.raises(AnalysisError, match="1 m"):
        analyze(Site("A", 37, -122, asl_m=10), Site("B", 37, -122, asl_m=10))


def test_short_link_warns_and_clamps_stations():
    a = Site("A", 37.0, -122.0, asl_m=10)
    b = Site("B", 37.0003, -122.0, asl_m=10)
    r = analyze(a, b, Options(stations=4))
    assert any("short" in w for w in r.warnings)
    assert len(r.stations) == 9


def test_long_link_warns():
    r = analyze(Site("A", 37.0, -122.0, asl_m=500), Site("B", 38.2, -122.0, asl_m=500))
    assert any("Long link" in w for w in r.warnings)


def test_names_are_escaped_in_kml():
    from fresnelkml import build_kml
    import xml.etree.ElementTree as ET
    kml = build_kml(Site("A & <B> \"q\" 'x'", 37, -122, asl_m=100), Site("C", 37.05, -121.9, asl_m=150))
    root = ET.fromstring(kml)
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    names = [n.text for n in root.iterfind(".//k:Placemark/k:name", ns)]
    assert "A & <B> \"q\" 'x'" in names
