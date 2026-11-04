import json

import pytest

from fresnelkml import httpjson
from fresnelkml.config import ConfigError, parse_config
from fresnelkml.nodes import load_nodes, slim_feed, slim_node
from fresnelkml.webapi import handle_request

TOWER = {
    "id": "k1rks-tower", "node": "K1RKS-Tower.local.mesh", "lat": 45.36683, "lon": -122.76617,
    "meshrf": {"freq": 5805.0}, "band": "5GHz",
    "antenna": {"height_m": 25.0, "height_ref": "agl", "azimuth_deg": 96.0, "elevation_deg": 0.0,
                "gain_dbi": 25.0, "beamwidth_deg": 8.0, "description": "Ubiquiti RD-5G30"},
    "links": [{"hostname": "Parrett-Sector", "id": "parrett-sector", "linkType": "RF", "distance_km": 9.8,
               "bearing_deg": 237.4, "signal_dbm": -64, "noise_dbm": -95},
              {"hostname": "K1RKS-Hub", "linkType": "DTD"}],
}
PARRETT = {"id": "parrett-sector", "node": "Parrett-Sector", "lat": 45.31005, "lon": -122.88422,
           "meshrf": {"freq": 5805.0}, "antenna": {"height_m": 12.0}, "links": []}
OLD = {"node": "Old-Node", "lat": "45.1", "lon": "-122.5", "meshrf": {"freq": "None"}, "links": []}


def feed(*nodes, date=1762230000000):
    return {"version": "1", "date": date, "nodeInfo": [{"data": n} for n in nodes]}


def config(tmp_path, feeds=None, ttl=300):
    feeds = feeds if feeds is not None else [{"name": "Main", "url": "https://mesh.example/nodes.json"}]
    return parse_config({"elevation": {"cache_dir": str(tmp_path)}, "nodes": {"feeds": feeds, "cache_ttl_s": ttl}})


def test_slim_new_feed():
    n = slim_node(TOWER)
    assert n["id"] == "k1rks-tower" and n["name"] == "K1RKS-Tower"
    assert (n["lat"], n["lon"]) == (45.36683, -122.76617)
    assert (n["height_m"], n["azimuth_deg"], n["beamwidth_deg"], n["gain_dbi"]) == (25.0, 96.0, 8.0, 25.0)
    assert n["elevation_deg"] == 0.0
    assert n["antenna"] == "Ubiquiti RD-5G30" and n["freq_mhz"] == 5805.0
    assert n["links"] == [{"id": "parrett-sector", "type": "RF", "distance_km": 9.8, "bearing_deg": 237.4,
                           "signal_dbm": -64.0}]


def test_slim_link_without_id_uses_hostname():
    n = slim_node({**PARRETT, "links": [{"hostname": "K1RKS-Tower.local.mesh", "linkType": "RF"}]})
    assert n["links"][0]["id"] == "k1rks-tower" and n["links"][0]["distance_km"] is None


def test_slim_old_feed_shape():
    n = slim_node(OLD)
    assert n["id"] == "old-node" and n["lat"] == 45.1
    assert n["height_m"] is None and n["azimuth_deg"] is None and n["freq_mhz"] is None and n["antenna"] is None


@pytest.mark.parametrize("data", [
    {"node": "A", "lat": 0, "lon": 0},
    {"node": "A", "lat": None, "lon": -122},
    {"node": "A", "lat": "Not Available", "lon": -122},
    {"node": "A", "lat": 95, "lon": -122},
    {"node": "", "lat": 45, "lon": -122},
    "not a dict",
])
def test_slim_drops_unusable(data):
    assert slim_node(data) is None


def test_slim_freq_zero_is_unknown():
    assert slim_node({**OLD, "meshrf": {"freq": 0}})["freq_mhz"] is None


def test_slim_feed_rejects_non_feed():
    with pytest.raises(ValueError):
        slim_feed({"error": "nope"})
    nodes, generated = slim_feed(feed(TOWER, {"node": "x", "lat": 0, "lon": 0}))
    assert [n["id"] for n in nodes] == ["k1rks-tower"] and generated == 1762230000000


def test_merge_keeps_first_feed_and_sorts(tmp_path, monkeypatch):
    docs = {"https://a/nodes.json": feed(TOWER, OLD),
            "https://b/nodes.json": feed({**TOWER, "node": "Dup"}, PARRETT)}
    monkeypatch.setattr(httpjson, "get_json", lambda url, params=None, timeout=10: docs[url])
    cfg = config(tmp_path, [{"name": "A", "url": "https://a/nodes.json"}, {"name": "B", "url": "https://b/nodes.json"}])
    nodes, feeds, warnings = load_nodes(cfg, now=lambda: 1000.0)
    assert [n["name"] for n in nodes] == ["K1RKS-Tower", "Old-Node", "Parrett-Sector"]
    assert nodes[0]["feed"] == "A" and nodes[2]["feed"] == "B"
    assert [(f["name"], f["count"]) for f in feeds] == [("A", 2), ("B", 1)]
    assert warnings == []


def test_cache_ttl_and_stale_fallback(tmp_path, monkeypatch):
    calls = []

    def get(url, params=None, timeout=10):
        calls.append(url)
        return feed(TOWER)

    monkeypatch.setattr(httpjson, "get_json", get)
    cfg = config(tmp_path, ttl=300)
    load_nodes(cfg, now=lambda: 1000.0)
    nodes, feeds, warnings = load_nodes(cfg, now=lambda: 1200.0)
    assert len(calls) == 1 and len(nodes) == 1 and feeds[0]["age_s"] == 200 and not warnings

    load_nodes(cfg, now=lambda: 1400.0)
    assert len(calls) == 2

    def down(*a, **k):
        raise httpjson.HttpError("https://mesh.example/nodes.json: HTTP 503")

    monkeypatch.setattr(httpjson, "get_json", down)
    nodes, feeds, warnings = load_nodes(cfg, now=lambda: 1400.0 + 900)
    assert len(nodes) == 1
    assert warnings == ["Main: feed unavailable (HTTP 503), showing data from 15 min ago."]


def test_failure_without_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(httpjson, "get_json", lambda *a, **k: {"not": "a feed"})
    nodes, feeds, warnings = load_nodes(config(tmp_path))
    assert nodes == [] and feeds == []
    assert warnings == ["Main: feed unavailable (response has no nodeInfo list)."]


def test_connection_error_reason_is_short(tmp_path, monkeypatch):
    def refused(*a, **k):
        raise httpjson.HttpError(
            "https://mesh.example/nodes.json: HTTPSConnectionPool(host='mesh.example', port=443): Max retries "
            "exceeded with url: /nodes.json (Caused by NewConnectionError(\"HTTPSConnection(host='mesh.example', "
            "port=443): Failed to establish a new connection: [Errno 111] Connection refused\"))")

    monkeypatch.setattr(httpjson, "get_json", refused)
    assert load_nodes(config(tmp_path))[2] == ["Main: feed unavailable (Connection refused)."]


def test_no_feeds_makes_no_requests(tmp_path):
    assert load_nodes(config(tmp_path, feeds=[])) == ([], [], [])


def test_config_defaults():
    cfg = parse_config({})
    assert cfg.node_feeds[0]["url"].endswith("/nodes.json") and cfg.nodes_cache_ttl_s == 300


@pytest.mark.parametrize("nodes,message", [
    ({"feeds": [{"name": "A", "url": "https://a"}, {"name": "A", "url": "https://b"}]}, "unique"),
    ({"feeds": [{"name": "A", "url": "file:///etc/passwd"}]}, "nodes.feeds[0].url"),
    ({"feeds": [{"name": "A", "url": "https://a", "key": 1}]}, "unknown key"),
    ({"feeds": "https://a"}, "must be a list"),
    ({"cache_ttl_s": -1}, "cache_ttl_s"),
    ({"timeout_s": 0}, "timeout_s"),
    ({"bogus": 1}, "unknown key"),
])
def test_config_errors(nodes, message):
    with pytest.raises(ConfigError, match=message.replace("[", r"\[").replace("]", r"\]")):
        parse_config({"nodes": nodes})


def write_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"elevation": {"cache_dir": str(tmp_path)},
                                "nodes": {"feeds": [{"name": "Main", "url": "https://mesh.example/nodes.json"}]}}))
    return {"FRESNEL_CONFIG": str(path)}


def test_api_nodes(tmp_path, monkeypatch):
    monkeypatch.setattr(httpjson, "get_json", lambda *a, **k: feed(TOWER, PARRETT))
    resp = handle_request("GET", "nodes", env=write_config(tmp_path))
    assert resp.status == 200
    assert dict(resp.headers)["Cache-Control"] == "max-age=60"
    data = json.loads(resp.body)
    assert set(data) == {"feeds", "nodes", "warnings"}
    assert [n["id"] for n in data["nodes"]] == ["k1rks-tower", "parrett-sector"]
    assert "url" not in data["feeds"][0]


def test_api_nodes_feed_down(tmp_path, monkeypatch):
    def down(*a, **k):
        raise httpjson.HttpError("https://mesh.example/nodes.json: timed out")

    monkeypatch.setattr(httpjson, "get_json", down)
    resp = handle_request("GET", "nodes", env=write_config(tmp_path))
    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["nodes"] == [] and len(data["warnings"]) == 1


def test_usage_lists_nodes():
    assert "GET ?nodes" in json.loads(handle_request("GET", "", env={}).body)["endpoints"]
