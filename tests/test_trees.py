import json
import xml.etree.ElementTree as ET

import pytest

from fresnelkml import Options, Site, analyze, render_kml
from fresnelkml.cli import main, summary
from fresnelkml.parsing import parse_options_dict
from fresnelkml.webapi import handle_request

from test_analysis import TEN_KM_LON

NS = {"k": "http://www.opengis.net/kml/2.2"}


def flat(fake_chain, agl=40, **opts):
    chain, _ = fake_chain(lambda lat, lon: 0.0)
    return analyze(Site("A", 0, 0, agl_m=agl), Site("B", 0, TEN_KM_LON, agl_m=agl),
                   Options(**opts), elevation=chain)


def test_defaults_are_off_100ft_50ft():
    opt = Options()
    assert opt.trees is False
    assert opt.tree_height_m == pytest.approx(30.48)
    assert opt.tree_clearance_a_m == pytest.approx(15.24)
    assert opt.tree_clearance_b_m == pytest.approx(15.24)


def test_trees_off_changes_nothing(fake_chain):
    r = flat(fake_chain)
    assert not r.trees_checked and r.tree_status is None
    assert all(p.tree_top_m is None for p in r.all_samples)


def test_tree_clearance_on_flat_ground(fake_chain):
    clear = flat(fake_chain, trees=True, tree_height_m=30)
    assert clear.status == "CLEAR"
    assert clear.min_tree_clearance == pytest.approx((40 - 1.4715 - 30) / 11.3675, abs=0.01)
    assert clear.tree_status == "CLEAR"
    assert clear.min_tree_clearance_m == pytest.approx(40 - 1.4715 - 30, abs=0.05)
    marginal = flat(fake_chain, trees=True, tree_height_m=35)
    assert marginal.tree_status == "MARGINAL" and marginal.status == "CLEAR"
    blocked = flat(fake_chain, trees=True, tree_height_m=45)
    assert blocked.tree_status == "BLOCKED" and blocked.tree_obstructions


def test_trees_ignored_near_both_sites(fake_chain):
    r = flat(fake_chain, trees=True, tree_height_m=45, tree_clearance_a_m=1000, tree_clearance_b_m=1000)
    for p in r.profile + r.profile_a + r.profile_b:
        inside = 1000 <= p.d_m <= r.distance_m - 1000
        assert (p.tree_top_m is not None) == inside
    assert 1000 <= r.min_tree_clearance_d_m <= r.distance_m - 1000
    # the close-up windows lie entirely inside the ignored zone
    assert all(p.tree_top_m is None for p in r.profile_a + r.profile_b)


def test_each_site_has_its_own_tree_clearance(fake_chain):
    r = flat(fake_chain, trees=True, tree_height_m=45, tree_clearance_a_m=0, tree_clearance_b_m=2000)
    assert r.profile_a[0].tree_top_m == pytest.approx(45)
    assert all(p.tree_top_m is None for p in r.profile_b)
    treed = [p.d_m for p in r.all_samples if p.tree_top_m is not None]
    assert min(treed) == 0 and max(treed) <= r.distance_m - 2000
    assert r.min_tree_clearance_d_m < 50


def test_too_short_for_tree_check(fake_chain):
    r = flat(fake_chain, trees=True, tree_clearance_a_m=6000, tree_clearance_b_m=6000)
    assert r.tree_status == "UNKNOWN"
    assert any("tree clearances" in w for w in r.warnings)


def test_feet_conversion_only_for_given_values():
    opt, _, _ = parse_options_dict({"units": "ft", "trees": True, "tree_height_m": 80, "clutter_m": 10})
    assert opt.trees is True
    assert opt.tree_height_m == pytest.approx(80 * 0.3048)
    assert opt.tree_clearance_a_m == pytest.approx(15.24)
    assert opt.clutter_m == pytest.approx(3.048)
    metric, _, _ = parse_options_dict({"units": "m", "tree_height_m": 20})
    assert metric.tree_height_m == 20


def test_tree_clearance_shorthand_sets_both_unless_overridden():
    opt, _, _ = parse_options_dict({"units": "ft", "tree_clearance_m": 200, "tree_clearance_b_m": 400})
    assert opt.tree_clearance_a_m == pytest.approx(200 * 0.3048)
    assert opt.tree_clearance_b_m == pytest.approx(400 * 0.3048)
    both, _, _ = parse_options_dict({"tree_clearance_m": 30})
    assert both.tree_clearance_a_m == both.tree_clearance_b_m == 30


def test_kml_and_report(fake_chain):
    r = flat(fake_chain, trees=True, tree_height_m=45, units="ft")
    root = ET.fromstring(render_kml(r))
    folders = [f.find("k:name", NS).text for f in root.iterfind(".//k:Folder", NS)]
    assert "Tree check (BLOCKED)" in folders
    desc = root.find("k:Document/k:description", NS).text
    assert "Min clearance over trees" in desc and "147.6 ft tall" in desc
    assert "ignored within 50 ft of A and 50 ft of B" in desc
    trees = r.to_dict()["trees"]
    assert trees["enabled"] and trees["status"] == "BLOCKED"
    assert any(p["tree_top_m"] for p in r.to_dict()["profile"])
    assert "Tree check" in summary(r)

    off = ET.fromstring(render_kml(flat(fake_chain)))
    assert not any(f.find("k:name", NS).text.startswith("Tree") for f in off.iterfind(".//k:Folder", NS))


def test_api_and_cli(tmp_path, local_config, capsys):
    body = json.dumps({"site_a": {"name": "A", "lat": 37.0, "lon": -122.0, "asl_m": 150},
                       "site_b": {"name": "B", "lat": 37.05, "lon": -121.9, "asl_m": 180},
                       "options": {"terrain_check": True, "trees": True, "units": "ft",
                                   "tree_height_m": 100, "tree_clearance_a_m": 50, "tree_clearance_b_m": 300},
                       "format": "json"}).encode()
    data = json.loads(handle_request("POST", "", body, env={"FRESNEL_CONFIG": local_config}).body)
    assert data["report"]["trees"]["height_m"] == pytest.approx(30.48)
    assert data["report"]["trees"]["clearance_a_m"] == pytest.approx(15.24)
    assert data["report"]["trees"]["clearance_b_m"] == pytest.approx(91.44)
    assert data["report"]["trees"]["status"] in ("CLEAR", "MARGINAL", "BLOCKED")

    out = tmp_path / "t.kml"
    code = main(["--a", "A,37.0,-122.0,150asl", "--b", "B,37.05,-121.9,180asl", "--terrain-check",
                 "--config", local_config, "--trees", "--tree-height", "100", "--units", "ft",
                 "--tree-clearance", "75", "--tree-clearance-b", "300", "-o", str(out)], env={})
    assert code == 0
    assert "Tree check (100.0 ft trees, ignored within 75 ft of A and 300 ft of B)" in capsys.readouterr().out
