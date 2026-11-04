import json
import os
import stat
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_site  # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("dist")
    tar = build_site.build(str(out))
    return str(out), tar


def run_cgi(site_dir, method="POST", body=b"", query="", extra_env=None):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "REQUEST_METHOD": method,
        "QUERY_STRING": query,
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(body)),
        "FRESNEL_CONFIG": "",
    }
    env.update(extra_env or {})
    # run with an empty PYTHONPATH from an unrelated directory, so only the tree's own lib/ is importable
    proc = subprocess.run([sys.executable, os.path.join(site_dir, "api", "fresnel.cgi")], input=body,
                          capture_output=True, env=env, cwd="/", timeout=60)
    assert proc.returncode == 0, proc.stderr.decode()
    head, _, payload = proc.stdout.partition(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    headers = dict(line.split(": ", 1) for line in lines)
    return headers, payload


def test_tree_layout(built):
    out, tar = built
    site = os.path.join(out, "site")
    for rel in ("index.html", "app.js", "style.css", "VERSION", "api/fresnel.cgi", "lib/fresnelkml/webapi.py",
                "vendor/leaflet/leaflet.js", "vendor/leaflet/leaflet.css", "vendor/leaflet/LICENSE",
                "vendor/leaflet/images/layers.png"):
        assert os.path.isfile(os.path.join(site, rel)), rel
    assert os.stat(os.path.join(site, "api", "fresnel.cgi")).st_mode & stat.S_IXOTH
    assert not any("__pycache__" in root for root, _, _ in os.walk(site))
    with open(os.path.join(site, "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    assert 'src="app.js?v=' in html and 'href="style.css?v=' in html
    with tarfile.open(tar) as tf:
        names = tf.getnames()
    assert "site/api/fresnel.cgi" in names and "site/lib/fresnelkml/kml.py" in names


def test_cgi_returns_kml_offline(built, local_config):
    site = os.path.join(built[0], "site")
    payload = json.dumps({
        "site_a": {"name": "A", "lat": 37.0, "lon": -122.0, "asl_m": 150},
        "site_b": {"name": "B", "lat": 37.05, "lon": -121.9, "asl_m": 180},
        "options": {"terrain_check": True},
    }).encode()
    headers, kml = run_cgi(site, body=payload, extra_env={"FRESNEL_CONFIG": local_config})
    assert headers["Status"] == "200 OK"
    assert headers["Content-Type"].startswith("application/vnd.google-earth.kml+xml")
    assert int(headers["Content-Length"]) == len(kml)
    root = ET.fromstring(kml)
    assert "Terrain check" in [f.text for f in root.iter("{http://www.opengis.net/kml/2.2}name")]


def test_cgi_get_and_errors(built):
    site = os.path.join(built[0], "site")
    headers, payload = run_cgi(site, method="GET", query="parse=CM87xa08")
    assert headers["Status"] == "200 OK" and json.loads(payload)["format"] == "maidenhead"
    headers, payload = run_cgi(site, body=b"not json")
    assert headers["Status"] == "400 Bad Request" and "error" in json.loads(payload)
    headers, _ = run_cgi(site, method="HEAD")
    assert headers["Status"] == "200 OK"
