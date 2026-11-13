import json

import pytest

from fresnelkml import httpjson
from fresnelkml.config import ConfigError, parse_config
from fresnelkml.nodes import load_nodes, slim_feed, slim_node
from fresnelkml.webapi import handle_request

BASE = "https://mesh.example/api/v1/"
TOWER = {
    "id": "k1rks-tower", "node": "K1RKS-Tower", "lat": 45.36683, "lon": -122.76617,
    "meshrf": {"freq": 5805.0, "chanbw": "10"}, "band": "5.8 GHz", "last_seen": "2026-11-13T17:38:20Z",
    "antenna": {"height_m": 25.0, "height_ref": "agl", "azimuth_deg": 96.0, "elevation_deg": 0.0,
                "gain_dbi": 25.0, "beamwidth_deg": 8.0, "description": "Ubiquiti RD-5G30"},
    "links": [{"hostname": "Parrett-Sector", "id": "parrett-sector", "linkType": "RF", "distance_km": 9.8,
               "bearing_deg": 237.4, "signal_dbm": -64, "noise_dbm": -95, "lat": 45.31005, "lon": -122.88422},
              {"hostname": "Unplaced", "id": "unplaced", "linkType": "RF", "signal_dbm": -80, "noise_dbm": -92,
               "lat": None, "lon": None},
              {"hostname": "K1RKS-Hub", "id": "k1rks-hub", "linkType": "DTD"},
              {"hostname": "Far-Away", "id": "far-away", "linkType": "TUN"}],
}
PARRETT = {"id": "parrett-sector", "node": "Parrett-Sector", "lat": 45.31005, "lon": -122.88422,
           "meshrf": {"freq": 5805.0, "chanbw": "None"}, "antenna": {"height_m": 12.0}, "links": []}
NOW_MS = 1_000_000_000


def feed(*nodes, date=NOW_MS):
    return {"version": "1", "date": date, "generator": "Nodographer", "nodeInfo": [{"data": n} for n in nodes]}


def config(tmp_path, feeds=None, ttl=60, stale=3600):
    feeds = feeds if feeds is not None else [{"name": "Main", "base": BASE}]
    return parse_config({"elevation": {"cache_dir": str(tmp_path)},
                         "nodes": {"feeds": feeds, "cache_ttl_s": ttl, "stale_after_s": stale}})


class Server:
    """Stands in for get_json_conditional: serves docs by URL with an ETag and records each request."""

    def __init__(self, docs, etag='"snap1-nodes.json"'):
        self.docs, self.etag, self.calls = docs, etag, []

    def __call__(self, url, etag=None, timeout=10.0):
        self.calls.append((url, etag))
        doc = self.docs.get(url)
        if isinstance(doc, Exception):
            raise doc
        if doc is None:
            raise httpjson.HttpError(f"{url}: HTTP 404")
        if url.endswith("nodes.json") and etag == self.etag:
            return None, self.etag
        return doc, self.etag


def test_slim_node():
    n = slim_node(TOWER)
    assert n["id"] == "k1rks-tower" and n["name"] == "K1RKS-Tower"
    assert (n["lat"], n["lon"]) == (45.36683, -122.76617)
    assert (n["height_m"], n["azimuth_deg"], n["beamwidth_deg"], n["gain_dbi"]) == (25.0, 96.0, 8.0, 25.0)
    assert n["elevation_deg"] == 0.0 and n["antenna"] == "Ubiquiti RD-5G30"
    assert (n["freq_mhz"], n["chanbw_mhz"], n["band"]) == (5805.0, 10.0, "5.8 GHz")
    assert n["last_seen"] == "2026-11-13T17:38:20Z"
    assert n["links"] == [
        {"id": "parrett-sector", "name": "Parrett-Sector", "type": "RF", "distance_km": 9.8, "bearing_deg": 237.4,
         "signal_dbm": -64.0, "noise_dbm": -95.0, "lat": 45.31005, "lon": -122.88422},
        {"id": "unplaced", "name": "Unplaced", "type": "RF", "distance_km": None, "bearing_deg": None,
         "signal_dbm": -80.0, "noise_dbm": -92.0, "lat": None, "lon": None}]


def test_slim_node_without_optional_fields():
    n = slim_node(PARRETT)
    assert n["height_m"] == 12.0 and n["azimuth_deg"] is None and n["antenna"] is None
    assert n["chanbw_mhz"] is None and n["last_seen"] is None and n["links"] == []


def test_link_without_position_fields():
    n = slim_node({**PARRETT, "links": [{"hostname": "K1RKS-Tower", "id": "k1rks-tower", "linkType": "RF"}]})
    assert n["links"][0]["lat"] is None and n["links"][0]["noise_dbm"] is None


@pytest.mark.parametrize("data", [
    {**PARRETT, "lat": 0, "lon": 0},
    {**PARRETT, "lat": None},
    {**PARRETT, "lat": 95},
    {**PARRETT, "id": ""},
    {k: v for k, v in PARRETT.items() if k != "id"},
    {**PARRETT, "node": ""},
    "not a dict",
])
def test_slim_drops_unusable(data):
    assert slim_node(data) is None


def test_slim_freq_zero_is_unknown():
    assert slim_node({**PARRETT, "meshrf": {"freq": 0}})["freq_mhz"] is None


@pytest.mark.parametrize("doc", [{"error": "nope"}, {"version": "1", "nodeInfo": []},
                                 {**feed(), "generator": "worldmap"}, {**feed(), "nodeInfo": None}])
def test_slim_feed_rejects_other_documents(doc):
    with pytest.raises(ValueError, match="not a Nodographer nodes.json"):
        slim_feed(doc)


def test_slim_feed():
    nodes, generated = slim_feed(feed(TOWER, {**PARRETT, "lat": 0, "lon": 0}))
    assert [n["id"] for n in nodes] == ["k1rks-tower"] and generated == NOW_MS


def test_merge_keeps_first_feed_and_sorts(tmp_path, monkeypatch):
    other = {**PARRETT, "id": "other", "node": "Other"}
    server = Server({"https://a/nodes.json": feed(TOWER, other),
                     "https://b/nodes.json": feed({**TOWER, "node": "Dup"}, PARRETT)})
    monkeypatch.setattr(httpjson, "get_json_conditional", server)
    cfg = config(tmp_path, [{"name": "A", "base": "https://a"}, {"name": "B", "base": "https://b/"}])
    nodes, feeds, warnings = load_nodes(cfg, now=lambda: NOW_MS / 1000)
    assert [n["name"] for n in nodes] == ["K1RKS-Tower", "Other", "Parrett-Sector"]
    assert nodes[0]["feed"] == "A" and nodes[2]["feed"] == "B"
    assert [(f["name"], f["count"]) for f in feeds] == [("A", 2), ("B", 1)]
    assert warnings == []


def test_conditional_refresh(tmp_path, monkeypatch):
    server = Server({BASE + "nodes.json": feed(TOWER)})
    monkeypatch.setattr(httpjson, "get_json_conditional", server)
    cfg = config(tmp_path, ttl=60)
    t0 = NOW_MS / 1000
    load_nodes(cfg, now=lambda: t0)
    assert server.calls == [(BASE + "nodes.json", None)]

    nodes, feeds, warnings = load_nodes(cfg, now=lambda: t0 + 30)
    assert len(server.calls) == 1 and feeds[0]["age_s"] == 30 and len(nodes) == 1

    nodes, feeds, warnings = load_nodes(cfg, now=lambda: t0 + 90)
    assert server.calls[-1] == (BASE + "nodes.json", '"snap1-nodes.json"')
    assert len(nodes) == 1 and feeds[0]["age_s"] == 0 and not warnings

    server.etag = '"snap2-nodes.json"'
    server.docs[BASE + "nodes.json"] = feed(TOWER, PARRETT)
    nodes, _, _ = load_nodes(cfg, now=lambda: t0 + 200)
    assert len(nodes) == 2


def test_stale_cache_fallback(tmp_path, monkeypatch):
    server = Server({BASE + "nodes.json": feed(TOWER)})
    monkeypatch.setattr(httpjson, "get_json_conditional", server)
    cfg = config(tmp_path)
    t0 = NOW_MS / 1000
    load_nodes(cfg, now=lambda: t0)
    server.docs[BASE + "nodes.json"] = httpjson.HttpError(BASE + "nodes.json: HTTP 503")
    nodes, feeds, warnings = load_nodes(cfg, now=lambda: t0 + 900)
    assert len(nodes) == 1
    assert warnings == ["Main: feed unavailable (HTTP 503), showing data from 15 min ago."]


def test_failure_without_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(httpjson, "get_json_conditional", Server({BASE + "nodes.json": {"not": "a feed"}}))
    nodes, feeds, warnings = load_nodes(config(tmp_path))
    assert nodes == [] and feeds == []
    assert warnings == ["Main: feed unavailable (not a Nodographer nodes.json)."]


def test_connection_error_reason_is_short(tmp_path, monkeypatch):
    url = BASE + "nodes.json"
    refused = httpjson.HttpError(
        f"{url}: HTTPSConnectionPool(host='mesh.example', port=443): Max retries exceeded with url: /api/v1/nodes.json "
        "(Caused by NewConnectionError(\"HTTPSConnection(host='mesh.example', port=443): Failed to establish a new "
        "connection: [Errno 111] Connection refused\"))")
    monkeypatch.setattr(httpjson, "get_json_conditional", Server({url: refused}))
    assert load_nodes(config(tmp_path))[2] == ["Main: feed unavailable (Connection refused)."]


def test_fresh_data_never_reads_status(tmp_path, monkeypatch):
    server = Server({BASE + "nodes.json": feed(TOWER), BASE + "status.json": {"state": "failed"}})
    monkeypatch.setattr(httpjson, "get_json_conditional", server)
    assert load_nodes(config(tmp_path), now=lambda: NOW_MS / 1000 + 600)[2] == []
    assert [u for u, _ in server.calls] == [BASE + "nodes.json"]


@pytest.mark.parametrize("status,suffix", [
    (None, ""),
    ({"state": "idle", "lastError": None}, ""),
    ({"state": "failed", "lastError": "nodelistNode unreachable"}, "; the last scan failed: nodelistNode unreachable"),
    ({"state": "failed"}, "; the last scan failed"),
    ({"state": "scanning", "lastError": None}, "; a scan is in progress"),
])
def test_stale_data_warns_with_status(tmp_path, monkeypatch, status, suffix):
    docs = {BASE + "nodes.json": feed(TOWER)}
    if status is not None:
        docs[BASE + "status.json"] = status
    server = Server(docs)
    monkeypatch.setattr(httpjson, "get_json_conditional", server)
    cfg = config(tmp_path)
    t = NOW_MS / 1000 + 3 * 3600
    assert load_nodes(cfg, now=lambda: t)[2] == [f"Main: mesh data is 3 h old{suffix}."]
    calls = len(server.calls)
    assert load_nodes(cfg, now=lambda: t + 10)[2] == [f"Main: mesh data is 3 h old{suffix}."]
    assert len(server.calls) == calls


def test_stale_check_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setattr(httpjson, "get_json_conditional", Server({BASE + "nodes.json": feed(TOWER)}))
    assert load_nodes(config(tmp_path, stale=0), now=lambda: NOW_MS / 1000 + 86400 * 5)[2] == []


def test_no_feeds_makes_no_requests(tmp_path):
    assert load_nodes(config(tmp_path, feeds=[])) == ([], [], [])


def test_config_defaults():
    cfg = parse_config({})
    assert cfg.node_feeds[0]["base"].endswith("/data/") and cfg.nodes_cache_ttl_s == 60
    assert cfg.nodes_stale_after_s == 3600


def test_config_adds_trailing_slash():
    cfg = parse_config({"nodes": {"feeds": [{"name": "A", "base": "http://meshinfo:8090/api/v1"}]}})
    assert cfg.node_feeds[0]["base"] == "http://meshinfo:8090/api/v1/"


def test_config_old_nodes_json_url_is_its_folder():
    cfg = parse_config({"nodes": {"feeds": [{"name": "A", "url": "http://10.0.0.5/meshmap/data/nodes.json"}]}})
    assert cfg.node_feeds == [{"name": "A", "base": "http://10.0.0.5/meshmap/data/"}]


@pytest.mark.parametrize("nodes,message", [
    ({"feeds": [{"name": "A", "url": "https://a/out.json"}]}, "nodes.feeds[0].url was replaced by base"),
    ({"feeds": [{"name": "A", "url": "https://a/nodes.json", "base": "https://a/"}]}, "url was replaced by base"),
    ({"feeds": [{"name": "A", "url": "ftp://a/nodes.json"}]}, "nodes.feeds[0].base"),
    ({"feeds": [{"name": "A", "base": "https://a"}, {"name": "A", "base": "https://b"}]}, "unique"),
    ({"feeds": [{"name": "A", "base": "file:///etc/"}]}, "nodes.feeds[0].base"),
    ({"feeds": [{"name": "A", "base": "https://a", "key": 1}]}, "unknown key"),
    ({"feeds": "https://a"}, "must be a list"),
    ({"cache_ttl_s": -1}, "cache_ttl_s"),
    ({"stale_after_s": -1}, "stale_after_s"),
    ({"stale_after_s": True}, "stale_after_s"),
    ({"timeout_s": 0}, "timeout_s"),
    ({"bogus": 1}, "unknown key"),
])
def test_config_errors(nodes, message):
    with pytest.raises(ConfigError, match=message.replace("[", r"\[").replace("]", r"\]")):
        parse_config({"nodes": nodes})


def write_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"elevation": {"cache_dir": str(tmp_path)},
                                "nodes": {"feeds": [{"name": "Main", "base": BASE}], "stale_after_s": 0}}))
    return {"FRESNEL_CONFIG": str(path)}


def test_api_nodes(tmp_path, monkeypatch):
    monkeypatch.setattr(httpjson, "get_json_conditional", Server({BASE + "nodes.json": feed(TOWER, PARRETT)}))
    resp = handle_request("GET", "nodes", env=write_config(tmp_path))
    assert resp.status == 200
    headers = dict(resp.headers)
    assert headers["Cache-Control"] == "max-age=60" and headers["ETag"].startswith('"')
    data = json.loads(resp.body)
    assert set(data) == {"feeds", "nodes", "warnings"}
    assert [n["id"] for n in data["nodes"]] == ["k1rks-tower", "parrett-sector"]
    assert "base" not in data["feeds"][0] and "url" not in data["feeds"][0]


def test_api_nodes_revalidates(tmp_path, monkeypatch):
    server = Server({BASE + "nodes.json": feed(TOWER, PARRETT)})
    monkeypatch.setattr(httpjson, "get_json_conditional", server)
    env = write_config(tmp_path)
    etag = dict(handle_request("GET", "nodes", env=env).headers)["ETag"]

    for sent in (etag, f"W/{etag}", f'"other", {etag}', "*"):
        resp = handle_request("GET", "nodes", env={**env, "HTTP_IF_NONE_MATCH": sent})
        assert resp.status == 304 and resp.body == b"" and resp.reason == "Not Modified"
        assert dict(resp.headers)["ETag"] == etag
    assert handle_request("GET", "nodes", env={**env, "HTTP_IF_NONE_MATCH": '"other"'}).status == 200

    # a new snapshot with different nodes changes the ETag
    for f in tmp_path.glob("fresnelkml-nodes-*.json"):
        f.unlink()
    server.etag = '"snap2-nodes.json"'
    server.docs[BASE + "nodes.json"] = feed(TOWER)
    resp = handle_request("GET", "nodes", env={**env, "HTTP_IF_NONE_MATCH": etag})
    assert resp.status == 200 and dict(resp.headers)["ETag"] != etag


def test_api_nodes_feed_down(tmp_path, monkeypatch):
    server = Server({BASE + "nodes.json": httpjson.HttpError(BASE + "nodes.json: timed out")})
    monkeypatch.setattr(httpjson, "get_json_conditional", server)
    resp = handle_request("GET", "nodes", env=write_config(tmp_path))
    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["nodes"] == [] and len(data["warnings"]) == 1


def test_api_config_with_old_url_is_reported(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"nodes": {"feeds": [{"name": "Main", "url": "https://a/feed"}]}}))
    resp = handle_request("GET", "nodes", env={"FRESNEL_CONFIG": str(path)})
    assert resp.status == 500 and "was replaced by base" in json.loads(resp.body)["error"]


def test_usage_lists_nodes():
    assert "GET ?nodes" in json.loads(handle_request("GET", "", env={}).body)["endpoints"]
