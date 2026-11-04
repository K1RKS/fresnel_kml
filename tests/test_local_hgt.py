import gzip
import os
import shutil

import pytest

from fresnelkml.elevation import MISSING, ElevationError
from fresnelkml.local_hgt import LocalHgtProvider, tile_name

from conftest import TILE_N, plane, write_tile


def expected(lat, lon, la0=37, lo0=-122, n=TILE_N):
    row = (la0 + 1 - lat) * (n - 1)
    col = (lon - lo0) * (n - 1)
    return row + col


@pytest.mark.parametrize("lat,lon", [
    (38.0, -122.0), (37.0, -121.0), (38.0, -121.0), (37.0, -122.0),
    (37.5, -121.5), (37.123456, -121.987654), (37.99999, -121.00001),
])
def test_bilinear_on_plane(tile_dir, lat, lon):
    p = LocalHgtProvider("local", tile_dir)
    value, source = p.elevation(lat, lon)
    assert value == pytest.approx(expected(lat, lon), abs=1e-6)
    assert source == "local"


def test_missing_tile(tile_dir):
    assert LocalHgtProvider("local", tile_dir).elevation(10.5, 10.5) is MISSING


def test_missing_directory():
    with pytest.raises(ElevationError):
        LocalHgtProvider("local", "/no/such/dir").fetch([(0, 0)])


@pytest.mark.parametrize("lat,lon,name", [
    (37.5, -121.5, "N37W122"), (-33.9, 151.2, "S34E151"), (0.0, 0.0, "N00E000"),
    (-0.1, -0.1, "S01W001"), (51.5, -0.1, "N51W001"), (-12.0, 179.999, "S12E179"),
])
def test_tile_names(lat, lon, name):
    assert tile_name(lat, lon) == name


def test_gz_tiles_and_voids(tmp_path):
    def with_voids(r, c):
        return -32768 if (r, c) == (0, 0) else 100

    raw = write_tile(str(tmp_path), "S34E151", with_voids, n=11)
    with open(raw, "rb") as src, gzip.open(raw + ".gz", "wb") as dst:
        shutil.copyfileobj(src, dst)
    os.remove(raw)
    p = LocalHgtProvider("local", str(tmp_path))
    value, _ = p.elevation(-33.5, 151.5)
    assert value == pytest.approx(100.0)
    # the north-west corner sample is void, so its weight is redistributed
    value, _ = p.elevation(-33.0 - 1e-9, 151.0 + 1e-9)
    assert value == pytest.approx(100.0)


def test_all_void_is_null(tmp_path):
    write_tile(str(tmp_path), "N10E010", lambda r, c: -32768, n=5)
    assert LocalHgtProvider("local", str(tmp_path)).elevation(10.5, 10.5) is None


def test_bad_tile_size(tmp_path):
    (tmp_path / "N10E010.hgt").write_bytes(b"\x00" * 10)
    with pytest.raises(ElevationError):
        LocalHgtProvider("local", str(tmp_path)).elevation(10.5, 10.5)


def test_plane_helper_matches_tile(tile_dir):
    p = LocalHgtProvider("local", tile_dir)
    assert p.elevation(38.0, -122.0)[0] == plane(0, 0)
