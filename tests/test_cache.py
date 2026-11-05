import json
import os
import sqlite3
import sys
import time

import pytest

from fresnelkml import Options, Site, analyze, httpjson
from fresnelkml import cache as cache_mod
from fresnelkml.cache import CacheSet, PointCache, key_point, point_key
from fresnelkml.cli import main as cli_main
from fresnelkml.config import ConfigError, build_chain, parse_config
from fresnelkml.elevation import ElevationChain
from fresnelkml.surface import SurfaceSampler

from conftest import FakeProvider
from test_analysis import TEN_KM_LON

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import cache_admin  # noqa: E402
import tile_cache_conf  # noqa: E402


def cache_at(tmp_path, max_bytes=1 << 30, name="t"):
    return PointCache(str(tmp_path / "points" / f"{name}.sqlite"), max_bytes)


# ----- Keys and storage -----

@pytest.mark.parametrize("lat,lon", [(0, 0), (45.339261, -122.915953), (-89.999999, 179.999999), (90, -180)])
def test_key_round_trip(lat, lon):
    back = key_point(point_key(lat, lon))
    assert back[0] == pytest.approx(lat, abs=1e-6)
    assert (back[1] - lon) % 360 == pytest.approx(0, abs=1e-6) or (lon - back[1]) % 360 == pytest.approx(0, abs=1e-6)


def test_neighbouring_points_stay_distinct():
    base = point_key(45.0, -122.0)
    assert len({base, point_key(45.000001, -122.0), point_key(45.0, -121.999999), point_key(45.000001, -121.999999)}) == 4
    assert point_key(45.0, 180.0) == point_key(45.0, -180.0)


def test_get_and_put_many(tmp_path):
    c = cache_at(tmp_path)
    pts = [(45 + i * 1e-5, -122 - i * 1e-5) for i in range(1234)]
    assert c.get_many(pts) == [None] * len(pts)
    c.put_many([(p, float(i), "src:a" if i % 2 else "src:b") for i, p in enumerate(pts)])
    got = c.get_many(pts + [(10.0, 10.0)])
    assert got[-1] is None
    assert [g[0] for g in got[:-1]] == [float(i) for i in range(len(pts))]
    assert {g[1] for g in got[:-1]} == {"src:a", "src:b"}
    reopened = cache_at(tmp_path).get_many(pts[:3])
    assert [g[0] for g in reopened] == [0.0, 1.0, 2.0]


def test_eviction_keeps_under_the_limit_and_drops_oldest_first(tmp_path, monkeypatch):
    c = cache_at(tmp_path, max_bytes=150_000)
    monkeypatch.setattr(cache_mod, "_today", lambda: 100)
    old = [((1 + i * 1e-4, 0.0), 1.0, "x") for i in range(1000)]
    c.put_many(old)
    monkeypatch.setattr(cache_mod, "_today", lambda: 200)
    new = [((2 + i * 1e-4, 0.0), 2.0, "x") for i in range(6000)]
    c.put_many(new)
    assert c.used_bytes() <= 150_000
    assert c.get_many([p for p, _, _ in old]) == [None] * len(old)  # the oldest went first
    assert c.get_many([new[-1][0]])[0] is not None
    assert os.path.getsize(c.path) <= 150_000 + 8192


def test_reads_refresh_last_use(tmp_path, monkeypatch):
    c = cache_at(tmp_path)
    monkeypatch.setattr(cache_mod, "_today", lambda: 100)
    c.put_many([((1.0, 1.0), 1.0, "x")])
    monkeypatch.setattr(cache_mod, "_today", lambda: 150)
    c.get_many([(1.0, 1.0)])
    assert sqlite3.connect(c.path).execute("SELECT t FROM p").fetchone()[0] == 150


def test_two_connections_share_a_file(tmp_path):
    a, b = cache_at(tmp_path), cache_at(tmp_path)
    a.put_many([((1.0, 1.0), 1.0, "a")])
    b.put_many([((2.0, 2.0), 2.0, "b")])
    assert [g[0] for g in a.get_many([(1.0, 1.0), (2.0, 2.0)])] == [1.0, 2.0]


def test_a_broken_file_turns_the_cache_off(tmp_path, capsys):
    path = tmp_path / "points" / "bad.sqlite"
    path.parent.mkdir()
    path.write_bytes(b"this is not a database" * 100)
    c = PointCache(str(path), 1 << 20)
    assert c.get_many([(1.0, 1.0)]) == [None]
    c.put_many([((1.0, 1.0), 1.0, "x")])
    assert c.disabled and "turned off" in capsys.readouterr().err
    p = FakeProvider(func=lambda lat, lon: 7.0)
    caches = CacheSet(str(tmp_path))
    caches._open["fake"] = c
    assert ElevationChain([p], caches=caches).lookup([(1.0, 1.0)])[0].elevation == 7.0


# ----- Limits and config -----

def test_limit_resolution_and_zero(tmp_path):
    cs = CacheSet(str(tmp_path), max_mb=100, types={"opentopodata": 20, "surface": 0}, services={"3dep": 300})
    assert cs.limit_mb("3dep", "arcgis_image") == 300
    assert cs.limit_mb("otd", "opentopodata") == 20
    assert cs.limit_mb("other", "usgs") == 100
    assert cs.for_service("dogami", "surface") is None
    assert cs.for_service("3dep", "arcgis_image").max_bytes == 300 * 1024 * 1024
    assert cs.for_service("3dep", "arcgis_image") is cs.for_service("3dep", "arcgis_image")
    assert cs.for_service("Mesh OTD!", "opentopodata").path.endswith("points/mesh-otd.sqlite")
    assert CacheSet(None).for_service("3dep", "arcgis_image") is None


def test_cache_config(tmp_path, monkeypatch):
    assert parse_config({}).cache.dir is None  # the test default folder doesn't exist
    monkeypatch.setattr(cache_mod, "DEFAULT_DIR", str(tmp_path))
    assert parse_config({}).cache.dir == str(tmp_path)
    assert parse_config({"cache": {"dir": None}}).cache.dir is None
    assert parse_config({"elevation": {"cache_dir": "/x"}}).cache.dir == "/x"
    assert parse_config({"elevation": {"cache_dir": "/x"}, "cache": {"dir": "/y"}}).cache_dir == "/y"
    c = parse_config({"cache": {"max_mb": 50, "types": {"surface": 0}, "services": {"3dep": 2048}}}).cache
    assert (c.max_mb, c.types, c.services) == (50, {"surface": 0}, {"3dep": 2048})
    os.chmod(tmp_path, 0o500)
    try:
        if not os.access(tmp_path, os.W_OK):  # root can write anyway
            assert parse_config({}).cache.dir is None
    finally:
        os.chmod(tmp_path, 0o700)


@pytest.mark.parametrize("bad", [
    {"cache": []}, {"cache": {"size": 1}}, {"cache": {"dir": 5}}, {"cache": {"dir": ""}},
    {"cache": {"max_mb": -1}}, {"cache": {"max_mb": True}}, {"cache": {"types": []}},
    {"cache": {"services": {"3dep": "big"}}},
    {"map": {"tile_servers": [{"name": "x", "url": "https://x/{z}/{x}/{y}.png", "cache_mb": -5}]}},
    {"map": {"tile_servers": [{"name": "x", "url": "https://x{z}/{x}/{y}.png", "cache_mb": 5}]}},
])
def test_bad_cache_config(bad):
    with pytest.raises(ConfigError):
        parse_config(bad)


# ----- Integration -----

def test_chain_second_lookup_makes_no_calls(tmp_path):
    p = FakeProvider(func=lambda lat, lon: lat * 10)
    cfg = parse_config({"cache": {"dir": str(tmp_path)}})
    pts = [(i * 0.01, 0.0) for i in range(300)]
    ElevationChain([p], caches=CacheSet.from_config(cfg)).lookup(pts)
    again = ElevationChain([p], caches=CacheSet.from_config(cfg)).lookup(pts)
    assert len(p.calls) == 3  # three batches of 100, all on the first lookup
    assert [r.elevation for r in again] == pytest.approx([lat * 10 for lat, _ in pts])
    assert os.path.exists(tmp_path / "points" / "fake.sqlite")


def test_build_chain_uses_the_config_cache(tmp_path):
    cfg = parse_config({"cache": {"dir": str(tmp_path), "services": {"local": 0}},
                        "elevation": {"chain": [{"name": "local", "type": "local_hgt", "path": str(tmp_path)}]}})
    chain = build_chain(cfg)
    assert chain.caches is not None and chain._cache(chain.providers[0]) is None  # 0: not cached


def test_lidar_second_run_makes_no_requests(tmp_path, monkeypatch, fake_chain):
    calls = []

    def post(url, form, timeout=10.0):
        pts = json.loads(form["geometry"])["points"]
        calls.append(len(pts))
        return {"samples": [{"locationId": i, "value": 30.0} for i in range(len(pts))]}

    monkeypatch.setattr(httpjson, "post_form_json", post)
    cfg = parse_config({"cache": {"dir": str(tmp_path)}, "surface": {
        "spacing_m": 200, "sources": [{"name": "dogami", "url": "https://x.test/ImageServer"}]}})
    chain, _ = fake_chain(lambda lat, lon: 0.0)

    def run():
        return analyze(Site("A", 0, 0, agl_m=40), Site("B", 0, TEN_KM_LON, agl_m=40),
                       Options(trees=True, tree_source="lidar"), elevation=chain, config=cfg,
                       surface=SurfaceSampler(cfg.surface, CacheSet.from_config(cfg)))

    first = run()
    n = len(calls)
    second = run()
    assert n > 0 and len(calls) == n
    assert second.surface_coverage == first.surface_coverage == 1.0
    assert os.path.exists(tmp_path / "points" / "surface-dogami.sqlite")


def test_cli_cache_flags(tmp_path, local_config, capsys):
    hgt = json.loads(open(local_config).read())["elevation"]["chain"][0]["path"]
    out = tmp_path / "x.kml"
    args = ["--a", "A,37.0,-122.0,30", "--b", "B,37.05,-121.9,25", "--hgt-dir", hgt, "--config", local_config,
            "-o", str(out), "-q"]
    assert cli_main(args + ["--cache-dir", str(tmp_path / "c")], env={}) == 0
    assert [p.name for p in (tmp_path / "c" / "points").glob("*.sqlite")] == [f"local-{cache_mod.slug(hgt)}.sqlite"]
    assert cli_main(args + ["--no-cache", "--cache-dir", str(tmp_path / "d")], env={}) == 0
    assert not (tmp_path / "d").exists()


# ----- Map tiles -----

TILES = {"map": {"tile_servers": [
    {"name": "OpenStreetMap", "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png", "cache_mb": 1024},
    {"name": "OpenTopoMap", "url": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", "subdomains": "abc",
     "cache_mb": 256},
    {"name": "AREDN K1RKS", "url": "//k1rks-tileserver-world.local.mesh/hot/{z}/{x}/{y}.png"},
]}}


def test_public_map_sends_cached_tiles_through_the_site():
    servers = parse_config(TILES).public_map()["tile_servers"]
    assert servers[0]["url"] == "tiles/openstreetmap/{z}/{x}/{y}.png" and "cache_mb" not in servers[0]
    assert servers[1]["url"] == "tiles/opentopomap/{z}/{x}/{y}.png" and "subdomains" not in servers[1]
    assert servers[2]["url"].startswith("//k1rks-tileserver-world")


def test_tile_cache_conf_output():
    text = tile_cache_conf.render(parse_config(TILES), "fresnel_kml_test")
    assert "keys_zone=fresnel_openstreetmap:10m max_size=1024m" in text
    assert "keys_zone=fresnel_opentopomap:10m max_size=256m" in text
    assert "location ^~ /fresnel_kml_test/tiles/openstreetmap/ {" in text
    assert "proxy_pass https://tile.openstreetmap.org/;" in text
    assert "proxy_pass https://a.tile.opentopomap.org/;" in text
    assert "proxy_set_header Host a.tile.opentopomap.org;" in text
    assert "k1rks" not in text
    assert "nothing to cache" in tile_cache_conf.render(parse_config({}), "/x/")


# ----- Admin tool -----

def test_cache_admin(tmp_path, capsys):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"cache": {"dir": str(tmp_path / "cache"), "types": {"surface": 0.05}}}))
    cs = CacheSet(str(tmp_path / "cache"))
    cs.for_service("3dep", "arcgis_image").put_many([((45 + i * 1e-4, -122.0), 100.0, "3dep") for i in range(500)])
    cs.for_service("dogami", "surface").put_many([((45 + i * 1e-4, -122.0), 5.0, "d") for i in range(5000)])
    cs.close()
    assert cache_admin.main(["stats", "--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "3dep: 500 points" in out and "surface-dogami: 5,000 points" in out
    assert cache_admin.main(["prune", "--config", str(cfg), "--service", "dogami"]) == 0
    assert "surface-dogami: removed" in capsys.readouterr().out
    assert cache_admin.main(["clear", "--config", str(cfg), "--service", "3dep"]) == 0
    assert "3dep: 0 points" in capsys.readouterr().out
    assert cache_admin.main(["stats", "--config", str(cfg), "--service", "nope"]) == 1
    off = tmp_path / "off.json"
    off.write_text('{"cache": {"dir": null}}')
    assert cache_admin.main(["stats", "--config", str(off)]) == 1
