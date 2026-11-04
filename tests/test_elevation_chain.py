import json

import pytest

from fresnelkml import Options, Site, analyze, httpjson
from fresnelkml.analysis import AnalysisError
from fresnelkml.config import ConfigError, DEFAULT_CONFIG, build_chain, load_config, parse_config
from fresnelkml.elevation import ElevationUnavailable
from fresnelkml.webapi import handle_request


def test_missing_config_gives_internet_default(tmp_path, monkeypatch):
    monkeypatch.setattr("fresnelkml.config.DEFAULT_CONFIG_PATH", str(tmp_path / "nope.json"))
    cfg = load_config(env={})
    assert [s["name"] for s in cfg.chain] == ["opentopodata", "open-elevation"]
    assert cfg.chain[0]["url"] == DEFAULT_CONFIG["elevation"]["chain"][0]["url"]


def test_explicit_missing_config_is_an_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(env={"FRESNEL_CONFIG": str(tmp_path / "nope.json")})


@pytest.mark.parametrize("bad", [
    {"elevation": {"chain": []}},
    {"elevation": {"chain": [{"type": "magic"}]}},
    {"elevation": {"chain": [{"type": "local_hgt"}]}},
    {"elevation": {"chain": [{"type": "opentopodata", "url": "ftp://x"}]}},
    {"elevation": {"chain": [{"type": "opentopodata"}, {"type": "opentopodata"}]}},
    {"elevation": {"retries": -1}},
])
def test_invalid_configs(bad):
    with pytest.raises(ConfigError):
        parse_config(bad)


def test_custom_url_is_requested(monkeypatch):
    urls = []

    def fake_get(url, params=None, timeout=10):
        urls.append(url)
        n = len(params["locations"].split("|"))
        return {"status": "OK", "results": [{"elevation": 10.0, "dataset": "srtm30m"}] * n}

    monkeypatch.setattr(httpjson, "get_json", fake_get)
    cfg = parse_config({"elevation": {"chain": [
        {"name": "mesh-otd", "type": "opentopodata", "url": "http://10.0.0.5:5000/v1", "datasets": "srtm30m",
         "min_interval_s": 0}]}})
    r = analyze(Site("A", 37, -122, agl_m=10), Site("B", 37.05, -121.9, agl_m=10), config=cfg)
    assert urls and all(u == "http://10.0.0.5:5000/v1/srtm30m" for u in urls)
    assert r.site_a.ground_source == "mesh-otd:srtm30m"


def _chain_config(tile_dir):
    return parse_config({"elevation": {"chain": [
        {"name": "internet", "type": "opentopodata", "min_interval_s": 0},
        {"name": "local", "type": "local_hgt", "path": tile_dir}]}})


def test_internet_down_local_fills_in(tile_dir, monkeypatch):
    def down(*a, **k):
        raise httpjson.HttpError("offline")

    monkeypatch.setattr(httpjson, "get_json", down)
    r = analyze(Site("A", 37.0, -122.0, agl_m=10), Site("B", 37.05, -121.9, agl_m=10), config=_chain_config(tile_dir))
    assert r.terrain_checked
    assert set(r.elevation_sources) == {"local"}


def test_local_first_then_internet_for_missing_tiles(tile_dir, monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=10):
        pts = params["locations"].split("|")
        calls.append(pts)
        return {"status": "OK", "results": [{"elevation": 5.0, "dataset": "srtm30m"}] * len(pts)}

    monkeypatch.setattr(httpjson, "get_json", fake_get)
    cfg = parse_config({"elevation": {"chain": [
        {"name": "local", "type": "local_hgt", "path": tile_dir},
        {"name": "internet", "type": "opentopodata", "min_interval_s": 0}]}})
    # B is in tile N38W122, which doesn't exist locally
    r = analyze(Site("A", 37.9, -121.5, agl_m=10), Site("B", 38.1, -121.5, agl_m=10), Options(stations=16),
                config=cfg)
    assert r.site_a.ground_source == "local"
    assert r.site_b.ground_source == "internet:srtm30m"
    assert all(float(p.split(",")[0]) >= 38.0 for p in calls[0])


def test_everything_missing_raises(tmp_path, monkeypatch):
    def down(*a, **k):
        raise httpjson.HttpError("offline")

    monkeypatch.setattr(httpjson, "get_json", down)
    cfg = _chain_config(str(tmp_path))
    with pytest.raises(ElevationUnavailable, match="offline"):
        analyze(Site("A", 37.0, -122.0, agl_m=10), Site("B", 37.05, -121.9, agl_m=10), config=cfg)


def test_elev_source_restricts_chain(tile_dir):
    cfg = _chain_config(tile_dir)
    r = analyze(Site("A", 37.0, -122.0, agl_m=10), Site("B", 37.05, -121.9, agl_m=10),
                Options(elev_source="local"), config=cfg)
    assert r.elevation_sources == ["local"]
    with pytest.raises(AnalysisError) as exc:
        analyze(Site("A", 37.0, -122.0, agl_m=10), Site("B", 37.05, -121.9, agl_m=10),
                Options(elev_source="nope"), config=cfg)
    assert exc.value.field == "options.elev_source"
    with pytest.raises(KeyError):
        build_chain(cfg, only="nope")


def test_sources_endpoint_hides_urls_and_paths(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"elevation": {"chain": [
        {"name": "lan", "type": "opentopodata", "url": "http://10.9.9.9:5000/v1"},
        {"name": "tiles", "type": "local_hgt", "path": "/secret/hgt"}]}}))
    resp = handle_request("GET", "sources", env={"FRESNEL_CONFIG": str(path)})
    text = resp.body.decode()
    assert resp.status == 200
    assert "10.9.9.9" not in text and "/secret" not in text
    assert [s["name"] for s in json.loads(text)["sources"]] == ["auto", "lan", "tiles"]
