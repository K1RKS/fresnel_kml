import json

import pytest

from fresnelkml import Options, Site, analyze, geo
from fresnelkml.config import ConfigError, load_config, parse_config
from fresnelkml.webapi import handle_request


def test_default_tile_servers(tmp_path, monkeypatch):
    monkeypatch.setattr("fresnelkml.config.DEFAULT_CONFIG_PATH", str(tmp_path / "nope.json"))
    cfg = load_config(env={})
    m = cfg.public_map()
    assert m["default"] == "OpenStreetMap"
    assert m["tile_servers"][0]["url"] == "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    assert "OpenStreetMap" in m["tile_servers"][0]["attribution"]


def test_elevation_only_config_keeps_default_map():
    cfg = parse_config({"elevation": {"chain": [{"type": "local_hgt", "path": "/x"}]}})
    assert cfg.default_tile_server == "OpenStreetMap"


def test_custom_tile_servers():
    cfg = parse_config({"map": {"tile_servers": [
        {"name": "AREDN", "url": "//k1rks-tileserver-world.local.mesh/hot/{z}/{x}/{y}.png", "max_zoom": 18},
        {"name": "OSM", "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png", "attribution": "OSM",
         "subdomains": "abc"}], "default": "AREDN"}})
    m = cfg.public_map()
    assert m["default"] == "AREDN"
    assert m["tile_servers"][0] == {"name": "AREDN", "url": "//k1rks-tileserver-world.local.mesh/hot/{z}/{x}/{y}.png",
                                    "attribution": "", "max_zoom": 18}
    assert m["tile_servers"][1]["subdomains"] == "abc"
    first = parse_config({"map": {"tile_servers": [{"name": "Only", "url": "https://t/{z}/{x}/{y}.png"}]}})
    assert first.default_tile_server == "Only"


@pytest.mark.parametrize("bad", [
    {"map": {"tile_servers": [{"name": "x", "url": "https://t/{z}/{x}.png"}]}},
    {"map": {"tile_servers": [{"name": "x", "url": "ftp://t/{z}/{x}/{y}.png"}]}},
    {"map": {"tile_servers": [{"url": "https://t/{z}/{x}/{y}.png"}]}},
    {"map": {"tile_servers": [{"name": "x", "url": "https://t/{z}/{x}/{y}.png"},
                              {"name": "x", "url": "https://u/{z}/{x}/{y}.png"}]}},
    {"map": {"tile_servers": [{"name": "x", "url": "https://t/{z}/{x}/{y}.png"}], "default": "y"}},
    {"map": {"tile_servers": [{"name": "x", "url": "https://t/{z}/{x}/{y}.png", "max_zoom": 40}]}},
    {"map": {"tile_servers": [{"name": "x", "url": "https://t/{z}/{x}/{y}.png", "style": "dark"}]}},
])
def test_invalid_map_configs(bad):
    with pytest.raises(ConfigError):
        parse_config(bad)


def test_map_endpoint(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"map": {"tile_servers": [
        {"name": "Mesh", "url": "//tiles.local.mesh/{z}/{x}/{y}.png"}]}}))
    resp = handle_request("GET", "map", env={"FRESNEL_CONFIG": str(path)})
    assert resp.status == 200
    assert json.loads(resp.body) == {"tile_servers": [{"name": "Mesh", "url": "//tiles.local.mesh/{z}/{x}/{y}.png",
                                                       "attribution": "", "max_zoom": 19}], "default": "Mesh"}


def test_report_footprint():
    r = analyze(Site("A", 37.0, -122.0, asl_m=150), Site("B", 37.05, -121.9, asl_m=180), Options(stations=64))
    m = r.to_dict()["map"]
    n = len(r.stations)
    assert len(m["los"]) == n and len(m["zone"]) == 2 * n and len(m["zone60"]) == 2 * n
    assert m["los"][0] == [37.0, -122.0] and m["los"][-1] == [37.05, -121.9]
    mid = n // 2
    right, left = m["zone"][mid], m["zone"][2 * n - 1 - mid]
    width = geo.haversine(right[0], right[1], left[0], left[1])
    assert width == pytest.approx(2 * r.stations[mid].radius_m, rel=0.01)
    assert width == pytest.approx(2 * r.r1_max_m, rel=0.01)
    assert m["zone"][0] == m["zone"][-1] == [37.0, -122.0]


def test_no_sixty_footprint_when_disabled():
    r = analyze(Site("A", 37.0, -122.0, asl_m=150), Site("B", 37.05, -121.9, asl_m=180), Options(sixty=False))
    assert r.to_dict()["map"]["zone60"] is None
