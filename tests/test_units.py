import json
import re
import xml.etree.ElementTree as ET

import pytest

from fresnelkml import Options, Site, analyze, render_kml
from fresnelkml.cli import summary
from fresnelkml.units import fmt_distance, fmt_gap, fmt_height
from fresnelkml.webapi import handle_request

NS = {"k": "http://www.opengis.net/kml/2.2"}


@pytest.mark.parametrize("metres,units,expected", [
    (850, "m", "850 m"), (10_474.8, "m", "10.475 km"), (250, "ft", "820 ft"),
    (10_474.8, "ft", "6.509 mi"), (304.0, "ft", "997 ft"), (400, "ft", "0.249 mi"),
])
def test_fmt_distance(metres, units, expected):
    assert fmt_distance(metres, units) == expected


def test_fmt_height_and_gap():
    assert fmt_height(3.6576, "ft") == "12.0 ft"
    assert fmt_height(3.6576, "m") == "3.7 m"
    assert fmt_height(None, "ft") == "unknown"
    assert fmt_gap(3.048, "ft") == "terrain 10.0 ft below the line of sight"
    assert fmt_gap(-2.0, "m") == "terrain 2.0 m above the line of sight"


def _terrain_report(fake_chain, units):
    chain, _ = fake_chain(lambda lat, lon: 165.0)
    return analyze(Site("A", 37.0, -122.0, asl_m=150), Site("B", 37.05, -121.9, asl_m=180),
                   Options(terrain_check=True, units=units), elevation=chain)


def test_min_clearance_height(fake_chain):
    r = _terrain_report(fake_chain, "m")
    worst = min((p for p in r.all_samples if p.clearance is not None), key=lambda p: p.clearance)
    assert r.min_clearance_m == pytest.approx(worst.los_m - worst.effective_m)
    assert r.to_dict()["min_clearance_m"] == round(r.min_clearance_m, 2)


def test_kml_descriptions_follow_units(fake_chain):
    root = ET.fromstring(render_kml(_terrain_report(fake_chain, "ft")))
    desc = root.find("k:Document/k:description", NS).text
    assert "Distance: 6.5" in desc and " mi" in desc and " km" not in desc
    assert "Max r1: 38." in desc and " ft" in desc
    names = [n.text for n in root.iter("{http://www.opengis.net/kml/2.2}name")]
    assert any(n.startswith("c=") and n.endswith(("ft", "mi")) for n in names)

    metric = ET.fromstring(render_kml(_terrain_report(fake_chain, "m")))
    desc = metric.find("k:Document/k:description", NS).text
    assert re.search(r"Distance: 10\.4\d\d km", desc) and "Max r1: 11.6" in desc


def test_cli_summary_in_feet(fake_chain):
    text = summary(_terrain_report(fake_chain, "ft"))
    assert re.search(r"Distance 6\.5\d\d mi", text)
    assert "d (mi)" in text and "heights in ft" in text
    assert " km" not in text


def test_api_report_carries_units():
    body = json.dumps({"site_a": {"name": "A", "lat": 37, "lon": -122, "asl_m": 500},
                       "site_b": {"name": "B", "lat": 37.05, "lon": -121.9, "asl_m": 600},
                       "options": {"units": "ft"}, "format": "json"}).encode()
    data = json.loads(handle_request("POST", "", body, env={}).body)
    assert data["report"]["units"] == "ft"
    assert data["report"]["sites"][0]["asl_m"] == pytest.approx(152.4)
    assert re.search(r"Distance: 6\.5\d\d mi", data["kml"])
