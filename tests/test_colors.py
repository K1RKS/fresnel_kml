import colorsys
import random
import re

import pytest

from fresnelkml import Options, Site, analyze, render_kml
from fresnelkml.colors import ColorError, parse_color


def test_hex_to_kml():
    assert parse_color("#00FF00").kml == "5900ff00"
    assert parse_color("#00FF0080").kml == "8000ff00"
    assert parse_color("#123456").kml == "59563412"
    assert parse_color("#123456").kml_opaque == "ff563412"


@pytest.mark.parametrize("name,hexval", [("green", "#00ff00"), ("Blue", "#0000ff"), ("ORANGE", "#ffa500"),
                                         ("purple", "#800080")])
def test_named(name, hexval):
    c = parse_color(name)
    assert c.hex == hexval and not c.random


@pytest.mark.parametrize("bad", ["#12345", "00ff00", "#gggggg", "chartreuse", 12])
def test_invalid(bad):
    with pytest.raises(ColorError):
        parse_color(bad)


def test_random_picks_stay_in_range():
    rng = random.Random(1234)
    for _ in range(1000):
        c = parse_color("random", rng)
        assert c.random and c.a == 0x59
        h, s, v = colorsys.rgb_to_hsv(c.r / 255, c.g / 255, c.b / 255)
        assert 74.5 <= h * 360 < 330.5
        assert s >= 0.64 and v >= 0.74


def test_random_is_default_and_varies():
    assert parse_color(None).random
    colors = {parse_color("random", random.Random(i)).hex for i in range(20)}
    assert len(colors) > 10


def test_kml_style_matches_report():
    a = Site("A", 37.0, -122.0, asl_m=150)
    b = Site("B", 37.05, -121.9, asl_m=180)
    report = analyze(a, b, Options(), rng=random.Random(7))
    kml = render_kml(report)
    style = re.search(r'<Style id="zone">.*?</Style>', kml, re.S).group(0)
    assert f"<color>{report.zone_color.kml}</color>" in style
    assert report.zone_color.hex in kml

    rings = render_kml(analyze(a, b, Options(style="rings", color="#ff0000")))
    style = re.search(r'<Style id="zone">.*?</Style>', rings, re.S).group(0)
    assert "<color>ff0000ff</color>" in style
