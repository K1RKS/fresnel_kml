import array
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fresnelkml import elevation, httpjson  # noqa: E402
from fresnelkml.elevation import ElevationChain, ElevationProvider  # noqa: E402

TILE_N = 1201
REAL_GET_JSON = httpjson.get_json
REAL_GET_JSON_CONDITIONAL = httpjson.get_json_conditional


class FakeProvider(ElevationProvider):
    """Returns elevations from a function of (lat, lon) and records every call."""

    type = "fake"

    def __init__(self, name="fake", func=lambda lat, lon: 0.0, batch=100):
        super().__init__(name)
        self.func = func
        self.batch = batch
        self.calls = []

    def fetch(self, points):
        self.calls.append(list(points))
        out = []
        for lat, lon in points:
            v = self.func(lat, lon)
            out.append(v if v is None or v is elevation.MISSING else (float(v), self.name))
        return out


@pytest.fixture(autouse=True, params=["pyproj", "spherical"])
def geodesy(request, monkeypatch):
    from fresnelkml import geo
    if request.param == "pyproj":
        if not geo.have_pyproj():
            pytest.skip("pyproj not installed")
    else:
        monkeypatch.setattr(geo, "_GEOD", None)
    return request.param


@pytest.fixture(autouse=True)
def no_network_or_sleep(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError(f"unexpected network call: {args}")

    monkeypatch.setattr(httpjson, "get_json", blocked)
    monkeypatch.setattr(httpjson, "get_json_conditional", blocked)
    monkeypatch.setattr(httpjson, "post_form_json", blocked)
    # a real /var/cache/fresnelkml on the test machine must never be used
    monkeypatch.setattr("fresnelkml.cache.DEFAULT_DIR", "/nonexistent/fresnelkml-cache")
    monkeypatch.setattr(elevation, "_sleep", lambda s: None)
    monkeypatch.setenv("FRESNEL_CONFIG", "")


@pytest.fixture
def fake_chain():
    def make(func=lambda lat, lon: 0.0, name="fake"):
        provider = FakeProvider(name, func)
        return ElevationChain([provider]), provider
    return make


def plane(row, col):
    return (row + col) % 4000


def write_tile(directory, name, func=plane, n=TILE_N):
    data = array.array("h", (func(r, c) for r in range(n) for c in range(n)))
    if sys.byteorder == "little":
        data.byteswap()
    path = os.path.join(directory, name + ".hgt")
    with open(path, "wb") as fh:
        fh.write(data.tobytes())
    return path


@pytest.fixture(scope="session")
def tile_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("hgt")
    write_tile(str(d), "N37W122")
    return str(d)


@pytest.fixture
def local_config(tmp_path, tile_dir):
    path = tmp_path / "config.json"
    path.write_text('{"elevation": {"chain": [{"name": "local", "type": "local_hgt", "path": "%s"}]}, '
                    '"cache": {"dir": null}}' % tile_dir)
    return str(path)
