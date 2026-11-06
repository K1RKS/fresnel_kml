import pytest

from fresnelkml import Options, Site, analyze, elevation, httpjson
from fresnelkml.cache import CacheSet
from fresnelkml.elevation import (MISSING, ElevationChain, ElevationError, ElevationUnavailable,
                                  OpenElevationProvider, OpenMeteoProvider, OpenTopoDataProvider, USGSProvider)

from conftest import FakeProvider


def test_t12_manual_ground_makes_no_network_calls():
    # the autouse fixture makes any HTTP call fail the test
    a = Site("A", 37.0, -122.0, agl_m=30, ground_m=120)
    b = Site("B", 37.05, -121.9, agl_m=25, ground_m=150)
    r = analyze(a, b, Options())
    assert not r.terrain_checked
    assert r.site_a.asl_m == 150 and r.site_a.ground_source == "manual"


def test_opentopodata_request_and_parse(monkeypatch):
    seen = {}

    def fake_get(url, params=None, timeout=10):
        seen["url"], seen["params"] = url, params
        n = len(params["locations"].split("|"))
        return {"status": "OK", "results": [{"elevation": 100.0 + i, "dataset": "ned10m"} for i in range(n)]}

    monkeypatch.setattr(httpjson, "get_json", fake_get)
    p = OpenTopoDataProvider("otd", url="http://10.0.0.5:5000/v1/", datasets="srtm30m")
    out = p.fetch([(37.0, -122.0), (37.1, -122.1)])
    assert seen["url"] == "http://10.0.0.5:5000/v1/srtm30m"
    assert seen["params"]["locations"] == "37.000000,-122.000000|37.100000,-122.100000"
    assert out == [(100.0, "otd:ned10m"), (101.0, "otd:ned10m")]


def test_opentopodata_null_and_error(monkeypatch):
    monkeypatch.setattr(httpjson, "get_json", lambda *a, **k: {"status": "OK", "results": [{"elevation": None}]})
    assert OpenTopoDataProvider("otd").fetch([(0, 0)]) == [None]
    monkeypatch.setattr(httpjson, "get_json", lambda *a, **k: {"status": "INVALID_REQUEST", "error": "bad"})
    with pytest.raises(ElevationError, match="bad"):
        OpenTopoDataProvider("otd").fetch([(0, 0)])


def test_open_meteo_request_and_parse(monkeypatch):
    seen = {}

    def fake_get(url, params=None, timeout=10):
        seen["url"], seen["params"] = url, params
        n = len(params["latitude"].split(","))
        return {"elevation": [10.0 + i for i in range(n)]}

    monkeypatch.setattr(httpjson, "get_json", fake_get)
    out = OpenMeteoProvider("om").fetch([(45.0, -122.0), (46.0, -121.5)])
    assert seen["url"] == "https://api.open-meteo.com/v1/elevation"
    assert seen["params"] == {"latitude": "45.000000,46.000000", "longitude": "-122.000000,-121.500000"}
    assert out == [(10.0, "om"), (11.0, "om")]
    monkeypatch.setattr(httpjson, "get_json", lambda *a, **k: {"elevation": [None]})
    assert OpenMeteoProvider("om").fetch([(0, 0)]) == [None]
    monkeypatch.setattr(httpjson, "get_json", lambda *a, **k: {"elevation": [1.0]})
    with pytest.raises(ElevationError, match="bad response"):
        OpenMeteoProvider("om").fetch([(0, 0), (1, 1)])


def test_failed_source_falls_through_to_open_meteo(monkeypatch):
    def fake_get(url, params=None, timeout=10):
        if "open-meteo" not in url:
            raise httpjson.HttpError("blocked")
        n = len(params["latitude"].split(","))
        return {"elevation": [3.0] * n}

    monkeypatch.setattr(httpjson, "get_json", fake_get)
    from fresnelkml.config import parse_config
    cfg = parse_config({"elevation": {"retries": 0, "chain": [
        {"name": "otd", "type": "opentopodata", "min_interval_s": 0, "retries": 0},
        {"name": "meteo", "type": "open-meteo", "min_interval_s": 0},
    ]}})
    report = analyze(Site("A", 37, -122, agl_m=10), Site("B", 37.01, -122, agl_m=10),
                     Options(terrain_check=False), config=cfg)
    assert report.site_a.ground_source == "meteo"
    assert report.site_b.ground_source == "meteo"


def test_open_elevation_and_usgs(monkeypatch):
    monkeypatch.setattr(httpjson, "get_json", lambda *a, **k: {"results": [{"elevation": 12}, {"elevation": 13}]})
    assert OpenElevationProvider("oe").fetch([(0, 0), (1, 1)]) == [(12.0, "oe"), (13.0, "oe")]
    monkeypatch.setattr(httpjson, "get_json", lambda *a, **k: {"value": "-1000000"})
    assert USGSProvider("usgs").fetch([(0, 0)]) == [MISSING]
    monkeypatch.setattr(httpjson, "get_json", lambda *a, **k: {"value": 55.5})
    assert USGSProvider("usgs").fetch([(37, -122)]) == [(55.5, "usgs")]


def test_batches_respect_provider_limit():
    p = FakeProvider(func=lambda lat, lon: 5.0, batch=100)
    chain = ElevationChain([p])
    results = chain.lookup([(0.0, i * 0.001) for i in range(250)])
    assert [len(c) for c in p.calls] == [100, 100, 50]
    assert all(r.elevation == 5.0 for r in results)


def test_retries_then_falls_back():
    class Flaky(FakeProvider):
        def fetch(self, points):
            self.calls.append(points)
            raise ElevationError("down")

    flaky = Flaky("flaky")
    backup = FakeProvider("backup", lambda lat, lon: 42.0)
    chain = ElevationChain([flaky, backup], retries=2)
    results = chain.lookup([(1.0, 2.0)])
    assert len(flaky.calls) == 3
    assert results[0].elevation == 42.0 and results[0].source == "backup"


def test_null_everywhere_is_zero_with_warning():
    chain = ElevationChain([FakeProvider(func=lambda lat, lon: None)])
    r = chain.lookup([(0.0, 0.0)])[0]
    assert r.elevation == 0.0 and r.assumed_zero


def test_missing_everywhere_raises():
    chain = ElevationChain([FakeProvider(func=lambda lat, lon: MISSING)])
    with pytest.raises(ElevationUnavailable):
        chain.lookup([(0.0, 0.0)])


def test_cache_avoids_second_fetch(tmp_path):
    p = FakeProvider(func=lambda lat, lon: 7.0)
    ElevationChain([p], caches=CacheSet(str(tmp_path))).lookup([(1.0, 1.0)])
    again = ElevationChain([p], caches=CacheSet(str(tmp_path))).lookup([(1.0, 1.0)])
    assert len(p.calls) == 1
    assert (again[0].elevation, again[0].source) == (7.0, "fake")


def test_throttle_waits(monkeypatch, tmp_path):
    slept = []
    monkeypatch.setattr(elevation, "_sleep", slept.append)
    monkeypatch.setattr(elevation.tempfile, "gettempdir", lambda: str(tmp_path))
    elevation.wait_turn("unit-test", 1.0)
    elevation.wait_turn("unit-test", 1.0)
    assert slept and 0 < slept[0] <= 1.0
