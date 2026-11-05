import json
import time
import xml.etree.ElementTree as ET

import pytest

from fresnelkml import Options, Site, analyze, httpjson, render_kml
from fresnelkml.analysis import AnalysisError, validate_options
from fresnelkml.arcgis import ImageService, ImageServiceError
from fresnelkml.cli import summary
from fresnelkml.config import ConfigError, SurfaceConfig, load_config, parse_config
from fresnelkml.elevation import ArcGISImageProvider, ElevationChain
from fresnelkml.parsing import parse_options_dict
from fresnelkml.surface import SurfaceSampler, SurfaceSamples, grid_spacing, lateral_clearance
from fresnelkml.webapi import handle_request

from conftest import FakeProvider
from test_analysis import TEN_KM_LON

NS = {"k": "http://www.opengis.net/kml/2.2"}
FT_US = 1200 / 3937


def fake_post(handler, calls=None):
    """post_form_json stand-in: handler(points as (lat, lon)) -> list of sample values."""
    def post(url, form, timeout=10.0):
        pts = [(lat, lon) for lon, lat in json.loads(form["geometry"])["points"]]
        if calls is not None:
            calls.append((url, len(pts), form))
        out = handler(pts)
        if isinstance(out, dict):
            return out
        return {"samples": [{"locationId": i, "value": v} for i, v in enumerate(out) if v is not ...]}
    return post


# ----- ArcGIS client -----

def test_sample_batch_units_nodata_and_order(monkeypatch):
    calls = []
    monkeypatch.setattr(httpjson, "post_form_json",
                        fake_post(lambda pts: ["100.0", "NoData", None, ...][:len(pts)], calls))
    svc = ImageService("https://example.test/ImageServer/", units="ftUS")
    got = svc.sample_batch([(45.0, -122.0), (45.1, -122.1), (45.2, -122.2), (45.3, -122.3)])
    assert got[0] == pytest.approx(100 * FT_US)
    assert got[1:] == [None, None, None]
    url, n, form = calls[0]
    assert url == "https://example.test/ImageServer/getSamples" and n == 4
    assert json.loads(form["geometry"])["points"][0] == [-122.0, 45.0]
    assert form["geometryType"] == "esriGeometryMultipoint" and form["f"] == "json"


def test_sample_batch_errors(monkeypatch):
    svc = ImageService("https://example.test/ImageServer")
    monkeypatch.setattr(httpjson, "post_form_json", fake_post(lambda pts: {"error": {"code": 400}}))
    assert svc.sample_batch([(1, 1), (2, 2)]) == [None, None]  # outside coverage
    monkeypatch.setattr(httpjson, "post_form_json", fake_post(lambda pts: {"error": {"code": 500, "message": "x"}}))
    with pytest.raises(ImageServiceError):
        svc.sample_batch([(1, 1)])

    def down(*a, **k):
        raise httpjson.HttpError("unreachable")
    monkeypatch.setattr(httpjson, "post_form_json", down)
    with pytest.raises(ImageServiceError):
        svc.sample_batch([(1, 1)])
    with pytest.raises(ValueError):
        ImageService("https://x.test", units="furlongs")


def test_sample_chunks_and_cache(monkeypatch, tmp_path):
    from fresnelkml.cache import CacheSet
    calls = []
    monkeypatch.setattr(httpjson, "post_form_json", fake_post(lambda pts: [lat for lat, _ in pts], calls))
    svc = ImageService("https://example.test/ImageServer")
    pts = [(i * 0.001, 0.0) for i in range(2500)]
    cache = CacheSet(str(tmp_path)).for_service("t", "surface")
    res = svc.sample(pts, threads=3, cache=cache)
    assert sorted(n for _, n, _ in calls) == [500, 1000, 1000]
    assert res.values == pytest.approx([p[0] for p in pts])
    assert res.failed == res.timed_out == 0
    calls.clear()
    again = svc.sample(pts[:10] + [(9.0, 9.0)], cache=cache)
    assert [n for _, n, _ in calls] == [1]  # only the new point is fetched
    assert again.values[:10] == pytest.approx([p[0] for p in pts[:10]])


def test_sample_reports_progress(monkeypatch):
    monkeypatch.setattr(httpjson, "post_form_json", fake_post(lambda pts: [1.0] * len(pts)))
    seen = []
    ImageService("https://example.test/ImageServer", chunk=10).sample(
        [(0.0, i * 0.001) for i in range(25)], threads=2, progress=lambda d, t: seen.append((d, t)))
    assert seen[0] == (0, 25) and seen[-1] == (25, 25)
    assert sorted(d for d, _ in seen) == [d for d, _ in seen]


def stream_items(resp):
    return [json.loads(line) for line in b"".join(resp.stream).decode().splitlines()]


def test_api_progress_stream(local_config, tmp_path):
    cfg = tmp_path / "c.json"
    data = json.loads(open(local_config).read())
    data["surface"] = {"sources": []}
    cfg.write_text(json.dumps(data))
    env = {"FRESNEL_CONFIG": str(cfg)}
    body = {"site_a": {"name": "A", "lat": 37.0, "lon": -122.0, "agl_m": 10},
            "site_b": {"name": "B", "lat": 37.05, "lon": -121.9, "agl_m": 10},
            "options": {"trees": True, "tree_source": "lidar"}, "format": "json", "progress": True}
    resp = handle_request("POST", "", json.dumps(body).encode(), env=env)
    assert resp.status == 200 and dict(resp.headers)["Content-Type"].startswith("application/x-ndjson")
    items = stream_items(resp)
    stages = [i["progress"]["stage"] for i in items if "progress" in i]
    assert stages[0] == "start" and stages[-1] == "report"
    ground = [i["progress"] for i in items if "progress" in i and i["progress"]["stage"] == "ground"]
    assert ground[-1]["done"] == ground[-1]["total"] > 0
    assert items[-1]["result"]["report"]["surface"]["enabled"]

    failing = {**body, "site_a": {"name": "A", "lat": 10.0, "lon": 10.0, "agl_m": 10},
               "site_b": {"name": "B", "lat": 10.01, "lon": 10.0, "agl_m": 10}}
    items = stream_items(handle_request("POST", "", json.dumps(failing).encode(), env=env))
    assert items[-1]["status"] == 502 and items[-1]["error"]

    bad = handle_request("POST", "", json.dumps({**body, "format": "kml"}).encode(), env=env)
    assert bad.status == 400 and json.loads(bad.body)["field"] == "progress"
    bad = handle_request("POST", "", json.dumps({**body, "site_a": {"name": "A"}}).encode(), env=env)
    assert bad.status == 400 and bad.stream is None


def test_sample_failures_and_deadline(monkeypatch):
    svc = ImageService("https://example.test/ImageServer", chunk=10)
    monkeypatch.setattr(httpjson, "post_form_json", fake_post(lambda pts: {"error": {"code": 500}}))
    res = svc.sample([(0.0, i * 0.001) for i in range(25)])
    assert res.failed == 25 and res.errors and res.values == [None] * 25
    res = svc.sample([(0.0, 0.0)] * 25, deadline=time.monotonic() - 1)
    assert res.timed_out == 25


def test_arcgis_provider_falls_through_on_nodata(monkeypatch):
    monkeypatch.setattr(httpjson, "post_form_json",
                        fake_post(lambda pts: [100.0 if lon > 0 else "NoData" for _, lon in pts]))
    backup = FakeProvider("backup", lambda lat, lon: 7.0)
    chain = ElevationChain([ArcGISImageProvider("3dep", "https://example.test/ImageServer"), backup])
    found = chain.lookup([(0.0, 1.0), (0.0, -1.0)])
    assert (found[0].elevation, found[0].source) == (100.0, "3dep")
    assert (found[1].elevation, found[1].source) == (7.0, "backup")


# ----- Config and options -----

def test_surface_config_defaults_and_validation():
    cfg = parse_config({})
    assert cfg.chain[0]["type"] == "arcgis_image"
    assert cfg.surface.public() == [{"name": "dogami", "label": "Oregon DOGAMI lidar"}]
    assert cfg.surface.sources[0]["units"] == "ftUS" and cfg.surface.spacing_m == 3.0
    off = parse_config({"surface": {"sources": []}})
    assert off.surface.sources == [] and off.surface.max_points == 25000
    custom = parse_config({"surface": {"sources": [{"name": "c", "url": "https://c.test/ImageServer"}],
                                       "spacing_m": 5, "threads": 2}})
    assert custom.surface.sources[0]["label"] == "c" and custom.surface.spacing_m == 5.0


@pytest.mark.parametrize("bad", [
    {"surface": []},
    {"surface": {"nope": 1}},
    {"surface": {"sources": [{"name": "x"}]}},
    {"surface": {"sources": [{"name": "x", "url": "ftp://x"}]}},
    {"surface": {"sources": [{"name": "x", "url": "https://x", "units": "yd"}]}},
    {"surface": {"sources": [{"name": "x", "url": "https://x", "bbox": [1, 2, 0, 3]}]}},
    {"surface": {"sources": [{"name": "x", "url": "https://x"}, {"name": "x", "url": "https://y"}]}},
    {"surface": {"spacing_m": 0}},
    {"surface": {"threads": 1.5}},
    {"elevation": {"chain": [{"type": "arcgis_image"}]}},
    {"elevation": {"chain": [{"type": "arcgis_image", "url": "https://x", "units": "yd"}]}},
])
def test_bad_surface_config(bad):
    with pytest.raises(ConfigError):
        parse_config(bad)


def test_options_parse_and_validate():
    opt, _, _ = parse_options_dict({"units": "ft", "trees": True, "tree_source": "LIDAR", "lidar_width": 0.4})
    assert opt.tree_source == "lidar" and opt.lidar_width == 0.4  # a ratio, not converted from feet
    for bad in (Options(tree_source="radar"), Options(lidar_width=1.5), Options(lidar_width=-0.1)):
        with pytest.raises(AnalysisError):
            validate_options(bad)


def test_clearance_maths():
    assert lateral_clearance(3.0, 0, 10.0) == pytest.approx(0.3)
    assert lateral_clearance(-2.0, 0, 10.0) == pytest.approx(-0.2)  # top above the line of sight
    assert lateral_clearance(-2.0, 4.0, 10.0) == pytest.approx(0.4)  # beside it: never closer than the offset
    assert lateral_clearance(3.0, 4.0, 10.0) == pytest.approx(0.5)
    assert grid_spacing(1000, 3.0, 5, 25000) == 3.0
    assert grid_spacing(100_000, 3.0, 5, 1000) == pytest.approx(100_000 / 199)


# ----- Analysis -----

class FakeSampler:
    def __init__(self, func, label="Fake lidar", spacing_m=100.0, max_points=25000):
        self.cfg = SurfaceConfig([], spacing_m=spacing_m, max_points=max_points)
        self.func = func
        self.label = label
        self.points = []

    def sample(self, points, deadline, progress=None):
        self.points = list(points)
        values = [self.func(lat, lon) for lat, lon in points]
        return SurfaceSamples(values, [None if v is None else self.label for v in values])


def lidar(fake_chain, func, agl=40, sampler=None, **opts):
    chain, _ = fake_chain(lambda lat, lon: 0.0)
    sampler = sampler or FakeSampler(func)
    opts = {"trees": True, "tree_source": "lidar", **opts}
    return analyze(Site("A", 0, 0, agl_m=agl), Site("B", 0, TEN_KM_LON, agl_m=agl),
                   Options(**opts), elevation=chain, surface=sampler)


def test_lidar_surface_on_the_centreline(fake_chain):
    r = lidar(fake_chain, lambda lat, lon: 35.0)
    assert r.surface_checked and not r.trees_checked
    assert r.surface_coverage == 1.0 and r.surface_sources == ["Fake lidar"]
    w = r.surface_worst
    assert w.offset_m == 0 and w.lidar
    assert w.clearance == pytest.approx((40 - 35 - 1.4715) / 11.3675, abs=0.01)
    assert r.surface_status == "MARGINAL" and r.status == "CLEAR"
    assert r.min_surface_clearance_m == pytest.approx(40 - 35 - 1.4715, abs=0.05)
    assert w.height_m == pytest.approx(35.0)
    assert not r.warnings


def test_lidar_samples_across_the_zone(fake_chain):
    beside = lambda lat, lon: 100.0 if abs(lat) > 1e-9 else 0.0  # tall only off the line of sight
    r = lidar(fake_chain, beside)
    assert r.surface_worst.offset_m != 0
    assert r.surface_worst.clearance == pytest.approx(0.3, abs=0.02)  # the ±0.5 × 0.6 r1 lines
    assert r.surface_status == "MARGINAL"
    narrow = lidar(fake_chain, beside, lidar_width=0)
    assert narrow.surface_status == "CLEAR" and narrow.surface_worst.offset_m == 0


def test_lidar_respects_the_site_exclusions(fake_chain):
    s = FakeSampler(lambda lat, lon: 0.0)
    r = lidar(fake_chain, None, sampler=s, tree_clearance_a_m=1000, tree_clearance_b_m=2000)
    ds = [p.d_m for p in r.surface_points]
    assert min(ds) == pytest.approx(1000) and max(ds) == pytest.approx(r.distance_m - 2000)
    assert len(s.points) == 5 * len(ds)


def test_fixed_height_where_there_is_no_lidar(fake_chain):
    none = lidar(fake_chain, lambda lat, lon: None, tree_height_m=45)
    assert none.surface_coverage == 0 and none.surface_status == "BLOCKED"
    assert not none.surface_worst.lidar and none.surface_sources == []
    assert any("No lidar covers this path" in w for w in none.warnings)

    half = lidar(fake_chain, lambda lat, lon: 0.0 if lon < TEN_KM_LON / 2 else None, tree_height_m=45)
    assert 0.4 < half.surface_coverage < 0.6
    assert any(w.startswith("Lidar covered 5") for w in half.warnings)
    assert {p.lidar for p in half.surface_points} == {True, False}


def test_long_path_widens_the_spacing(fake_chain):
    r = lidar(fake_chain, lambda lat, lon: 0.0, sampler=FakeSampler(lambda lat, lon: 0.0, max_points=500))
    assert r.surface_spacing_m > 100
    assert any("lidar was sampled every" in w for w in r.warnings)


def test_lidar_service_errors_fall_back(fake_chain, monkeypatch):
    def down(*a, **k):
        raise httpjson.HttpError("unreachable")
    monkeypatch.setattr(httpjson, "post_form_json", down)
    cfg = SurfaceConfig([{"name": "x", "label": "X lidar", "url": "https://x.test/ImageServer", "units": "m",
                          "bbox": None}], spacing_m=200.0)
    r = lidar(fake_chain, None, sampler=SurfaceSampler(cfg), tree_height_m=45)
    assert r.surface_coverage == 0 and r.surface_status == "BLOCKED"
    assert any(w.startswith("Lidar source error") for w in r.warnings)


def test_bbox_skips_sources_that_cant_cover(fake_chain, monkeypatch):
    monkeypatch.setattr(httpjson, "post_form_json", lambda *a, **k: pytest.fail("shouldn't be called"))
    cfg = SurfaceConfig([{"name": "x", "label": "X", "url": "https://x.test/ImageServer", "units": "m",
                          "bbox": [10, 10, 20, 20]}], spacing_m=200.0)
    r = lidar(fake_chain, None, sampler=SurfaceSampler(cfg))
    assert r.surface_coverage == 0


def test_lidar_needs_the_terrain_check(fake_chain):
    r = lidar(fake_chain, lambda lat, lon: 0.0, terrain_check=False)
    assert not r.surface_checked
    assert any("terrain check on" in w for w in r.warnings)


def test_report_kml_and_summary(fake_chain):
    r = lidar(fake_chain, lambda lat, lon: 45.0 if lon < TEN_KM_LON / 2 else None, units="ft", tree_height_m=10)
    d = r.to_dict()["surface"]
    assert d["enabled"] and d["status"] == "BLOCKED" and d["sources"] == ["Fake lidar"]
    assert d["markers"] and all(m["clearance"] < 0.6 for m in d["markers"])
    assert {p["lidar"] for p in d["plot"]} == {True, False}
    assert d["worst"]["lidar"] and d["width"] == 0.6
    root = ET.fromstring(render_kml(r))
    folders = [f.find("k:name", NS).text for f in root.iterfind(".//k:Folder", NS)]
    assert "Lidar check (BLOCKED)" in folders and not any(f.startswith("Tree check") for f in folders)
    desc = root.find("k:Document/k:description", NS).text
    assert "Min clearance over the lidar surface" in desc and "Fake lidar" in desc
    styles = [p.find("k:styleUrl", NS).text for p in root.iterfind(".//k:Placemark", NS)
              if p.find("k:styleUrl", NS) is not None]
    assert "#lidar_obstruction" in styles
    assert "Lidar check (Fake lidar" in summary(r)


def test_api_sources_and_lidar_request(local_config, tmp_path):
    cfg = tmp_path / "c.json"
    data = json.loads(open(local_config).read())
    data["surface"] = {"sources": []}
    cfg.write_text(json.dumps(data))
    env = {"FRESNEL_CONFIG": str(cfg)}
    assert json.loads(handle_request("GET", "sources", env=env).body)["surface"] == []
    default = json.loads(handle_request("GET", "sources", env={"FRESNEL_CONFIG": local_config}).body)
    assert default["surface"][0]["name"] == "dogami"
    body = json.dumps({"site_a": {"name": "A", "lat": 37.0, "lon": -122.0, "asl_m": 150},
                       "site_b": {"name": "B", "lat": 37.05, "lon": -121.9, "asl_m": 180},
                       "options": {"trees": True, "tree_source": "lidar", "lidar_width": 0.5},
                       "format": "json"}).encode()
    resp = json.loads(handle_request("POST", "", body, env=env).body)
    s = resp["report"]["surface"]
    assert resp["report"]["terrain_checked"]  # auto turns the terrain check on for trees and lidar
    assert s["enabled"] and s["coverage"] == 0 and s["width"] == 0.5
    assert any("No lidar covers this path" in w for w in resp["warnings"])


def test_default_config_has_lidar(tmp_path, monkeypatch):
    monkeypatch.setattr("fresnelkml.config.DEFAULT_CONFIG_PATH", str(tmp_path / "nope.json"))
    cfg = load_config(env={})
    assert cfg.surface.sources[0]["url"].endswith("DIGITAL_SURFACE_MODEL_MOSAIC/ImageServer")
