import xml.etree.ElementTree as ET

import pytest

from fresnelkml import Options, Site, analyze, render_kml
from fresnelkml.local_hgt import LocalHgtProvider

NS = {"k": "http://www.opengis.net/kml/2.2"}


@pytest.fixture
def forbid_files(monkeypatch):
    def boom(self, points):
        raise AssertionError("unexpected tile lookup")

    monkeypatch.setattr(LocalHgtProvider, "fetch", boom)


def test_two_asl_sites_make_no_lookups(forbid_files, local_config, monkeypatch):
    monkeypatch.setenv("FRESNEL_CONFIG", local_config)
    r = analyze(Site("A", 37.0, -122.0, asl_m=150), Site("B", 37.05, -121.9, asl_m=180))
    assert not r.terrain_checked
    assert r.site_a.ground_m is None and r.site_a.agl_m is None
    assert r.elevation_sources == []


def test_one_asl_one_agl_looks_up_one_site(fake_chain):
    chain, provider = fake_chain(lambda lat, lon: 100.0)
    r = analyze(Site("A", 37.0, -122.0, asl_m=150), Site("B", 37.05, -121.9, agl_m=20),
                Options(terrain_check=False), elevation=chain)
    assert provider.calls == [[(37.05, -121.9)]]
    assert r.site_b.asl_m == 120.0 and r.site_b.ground_source == "fake"


def test_default_terrain_check_with_one_agl_site_samples_the_path(fake_chain):
    chain, provider = fake_chain(lambda lat, lon: 100.0)
    r = analyze(Site("A", 37.0, -122.0, asl_m=150), Site("B", 37.05, -121.9, agl_m=20), elevation=chain)
    assert r.terrain_checked
    assert sum(len(c) for c in provider.calls) == len(r.profile) + len(r.profile_a) + len(r.profile_b)


def test_asl_sites_with_explicit_terrain_check(fake_chain):
    chain, provider = fake_chain(lambda lat, lon: 100.0)
    r = analyze(Site("A", 37.0, -122.0, asl_m=150), Site("B", 37.05, -121.9, asl_m=180),
                Options(terrain_check=True), elevation=chain)
    assert r.terrain_checked and r.status == "CLEAR"
    assert sum(len(c) for c in provider.calls) == len(r.profile) + len(r.profile_a) + len(r.profile_b)
    assert r.site_a.ground_m == 100.0 and r.site_a.agl_m == 50.0


def _masts(kml):
    root = ET.fromstring(kml)
    return {pm.find("k:name", NS).text: pm for pm in root.iterfind(".//k:Placemark", NS)
            if pm.find("k:name", NS).text.endswith("mast")}


def test_asl_mast_without_ground_is_extruded_point():
    r = analyze(Site("A", 37.0, -122.0, asl_m=150), Site("B", 37.05, -121.9, asl_m=180, ground_m=170))
    masts = _masts(render_kml(r))
    a = masts["A mast"]
    assert a.find("k:Point/k:extrude", NS).text == "1"
    assert a.find("k:Point/k:altitudeMode", NS).text == "absolute"
    b = masts["B mast"]
    coords = b.find("k:LineString/k:coordinates", NS).text.split()
    assert [float(c.split(",")[2]) for c in coords] == [170.0, 180.0]


def test_negative_agl_from_dem_warns(fake_chain):
    chain, _ = fake_chain(lambda lat, lon: 300.0)
    r = analyze(Site("A", 37.0, -122.0, asl_m=150), Site("B", 37.05, -121.9, asl_m=400),
                Options(terrain_check=True), elevation=chain)
    assert any("below the looked-up ground" in w for w in r.warnings)
