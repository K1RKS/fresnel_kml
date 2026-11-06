import json
import xml.etree.ElementTree as ET

import pytest

from fresnelkml import httpjson
from fresnelkml.webapi import MAX_BODY, handle_request, run_progress, safe_filename


def body(**overrides):
    data = {
        "site_a": {"name": "TowerA", "lat": 37.0, "lon": -122.0, "asl_m": 150},
        "site_b": {"name": "TowerB", "position": "10S 597811 4100985", "asl_m": 180},
    }
    data.update(overrides)
    return json.dumps(data).encode()


def headers(resp):
    return dict(resp.headers)


def test_run_progress_stays_on_the_caller_thread(local_config):
    seen = []

    def progress(stage, done=None, total=None):
        seen.append((stage, done, total))

    result = run_progress(body(format="json", progress=True, options={"terrain_check": True}),
                          {"FRESNEL_CONFIG": local_config}, progress)
    assert [stage for stage, _, _ in seen][0] == "start"
    assert "ground" in [stage for stage, _, _ in seen] and seen[-1][0] == "report"
    assert "kml" in result["result"] and result["result"]["report"]["terrain_checked"]
    failed = run_progress(b"not json", {"FRESNEL_CONFIG": local_config}, lambda *a, **k: seen.append("x"))
    assert failed["status"] == 400 and seen[-1] != "x"


def test_kml_response(local_config):
    resp = handle_request("POST", "", body(), env={"FRESNEL_CONFIG": local_config})
    assert resp.status == 200
    h = headers(resp)
    assert h["Content-Type"].startswith("application/vnd.google-earth.kml+xml")
    assert h["Content-Disposition"] == 'attachment; filename="Fresnel_TowerA_TowerB.kml"'
    root = ET.fromstring(resp.body)
    assert root.tag.endswith("kml")


def test_json_response_with_terrain(local_config):
    resp = handle_request("POST", "", body(format="json", options={"terrain_check": True, "color": "#336699"}),
                          env={"FRESNEL_CONFIG": local_config})
    assert resp.status == 200
    data = json.loads(resp.body)
    r = data["report"]
    assert set(data) == {"kml", "report", "warnings"}
    assert r["terrain_checked"] and r["status"] in ("CLEAR", "MARGINAL", "BLOCKED")
    assert r["zone_color"] == {"hex": "#336699", "alpha": 0x59, "kml": "59996633", "random": False}
    assert r["sites"][1]["from"] == "utm"
    assert r["elevation_sources"] == ["local"]
    assert len(r["profile"]) == 65
    assert "<kml" in data["kml"]


@pytest.mark.parametrize("payload,field", [
    (body(site_a={"name": "A", "lat": 95, "lon": 0, "asl_m": 1}), "site_a.lat"),
    (body(site_a={"name": "A", "lat": 37, "lon": -122}), "site_a.agl_m"),
    (body(options={"color": "plaid"}), "options.color"),
    (body(options={"stations": 9999}), "options.stations"),
    (body(format="pdf"), "format"),
])
def test_bad_input_400(payload, field):
    resp = handle_request("POST", "", payload, env={})
    assert resp.status == 400
    data = json.loads(resp.body)
    assert data["field"] == field and data["error"]


def test_not_json():
    resp = handle_request("POST", "", b"{nope", env={})
    assert resp.status == 400


def test_method_and_size():
    assert handle_request("PUT", env={}).status == 405
    assert handle_request("POST", "", b"x" * (MAX_BODY + 1), env={}).status == 413
    usage = handle_request("GET", "", env={})
    assert usage.status == 200 and b"usage" in usage.body


def test_502_when_elevation_unavailable(tmp_path, monkeypatch):
    def down(*a, **k):
        raise httpjson.HttpError("network unreachable")

    monkeypatch.setattr(httpjson, "get_json", down)
    cfg = tmp_path / "c.json"
    cfg.write_text('{"elevation": {"chain": [{"type": "opentopodata", "min_interval_s": 0}]}}')
    payload = body(site_a={"name": "A", "lat": 37, "lon": -122, "agl_m": 10})
    resp = handle_request("POST", "", payload, env={"FRESNEL_CONFIG": str(cfg)})
    assert resp.status == 502
    assert "asl_m" in json.loads(resp.body)["error"]


def test_ground_lookup(local_config):
    env = {"FRESNEL_CONFIG": local_config}
    resp = handle_request("GET", "ground&lat=37.5&lon=-121.5", env=env)
    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["lat"] == 37.5 and data["lon"] == -121.5
    assert data["source"].startswith("local") and not data["assumed_zero"]
    assert isinstance(data["ground_m"], float)
    by_dms = json.loads(handle_request("GET", "ground&lat=37%C2%B030'N&lon=121%C2%B030'W", env=env).body)
    assert by_dms["ground_m"] == data["ground_m"]
    named = handle_request("GET", "ground&lat=37.5&lon=-121.5&source=local", env=env)
    assert json.loads(named.body)["ground_m"] == data["ground_m"]


@pytest.mark.parametrize("query,status,field", [
    ("ground&lat=95&lon=0", 400, "ground.lat"),
    ("ground&lat=37.5", 400, "ground.lat"),
    ("ground&lat=37.5&lon=-121.5&source=nope", 400, "source"),
    ("ground&lat=10&lon=10", 502, None),
])
def test_ground_lookup_errors(local_config, query, status, field):
    resp = handle_request("GET", query, env={"FRESNEL_CONFIG": local_config})
    assert resp.status == status
    data = json.loads(resp.body)
    assert data["error"] and data.get("field") == field


def test_google_key_in_request_is_ignored(monkeypatch, tmp_path):
    seen = []

    def fake_get(url, params=None, timeout=10):
        seen.append(params.get("key"))
        n = len(params["locations"].split("|"))
        return {"status": "OK", "results": [{"elevation": 1.0}] * n}

    monkeypatch.setattr(httpjson, "get_json", fake_get)
    cfg = tmp_path / "c.json"
    cfg.write_text('{"elevation": {"chain": [{"type": "google"}]}}')
    payload = body(site_a={"name": "A", "lat": 37, "lon": -122, "agl_m": 10},
                   options={"google_key": "client-key", "terrain_check": False}, format="json")
    resp = handle_request("POST", "", payload, env={"FRESNEL_CONFIG": str(cfg), "GOOGLE_ELEVATION_KEY": "server"})
    assert resp.status == 200
    assert seen == ["server"]
    assert any("ignored" in w for w in json.loads(resp.body)["warnings"])


@pytest.mark.parametrize("a,b,expected", [
    ("TowerA", "TowerB", "Fresnel_TowerA_TowerB.kml"),
    ('A"; rm -rf /', "B/../x", "Fresnel_A_rm_-rf_B_.._x.kml"),
    ("Ünïcode", "Site 2", "Fresnel_n_code_Site_2.kml"),
])
def test_safe_filename(a, b, expected):
    assert safe_filename(a, b) == expected
