"""Offline elevation from SRTM .hgt / .hgt.gz tiles (pure Python, no GDAL)."""

from __future__ import annotations

import gzip
import math
import mmap
import os
import struct

from .elevation import MISSING, ElevationError, ElevationProvider

VOID = -32768


def tile_name(lat: float, lon: float) -> str:
    la = math.floor(lat)
    lo = math.floor(lon)
    if lo >= 180:
        lo -= 360
    ns = "N" if la >= 0 else "S"
    ew = "E" if lo >= 0 else "W"
    return f"{ns}{abs(la):02d}{ew}{abs(lo):03d}"


class _Tile:
    def __init__(self, path: str):
        self.path = path
        if path.endswith(".gz"):
            with gzip.open(path, "rb") as fh:
                self.data = fh.read()
            self._fh = None
        else:
            self._fh = open(path, "rb")
            size = os.fstat(self._fh.fileno()).st_size
            self.data = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ) if size else b""
        samples = len(self.data) // 2
        n = math.isqrt(samples)
        if n < 2 or n * n * 2 != len(self.data):
            raise ElevationError(f"{os.path.basename(path)}: not a square SRTM tile ({len(self.data)} bytes)")
        self.n = n

    def sample(self, row: int, col: int) -> int:
        return struct.unpack_from(">h", self.data, (row * self.n + col) * 2)[0]

    def close(self) -> None:
        if self._fh is not None:
            if isinstance(self.data, mmap.mmap):
                self.data.close()
            self._fh.close()


class LocalHgtProvider(ElevationProvider):
    type = "local_hgt"
    batch = 100_000

    def __init__(self, name: str, path: str):
        super().__init__(name)
        self.path = path
        self._tiles: dict[str, _Tile | None] = {}

    def _find(self, name: str) -> str | None:
        for candidate in (name, name.lower()):
            for ext in (".hgt", ".hgt.gz"):
                full = os.path.join(self.path, candidate + ext)
                if os.path.isfile(full):
                    return full
        return None

    def _tile(self, name: str) -> _Tile | None:
        if name not in self._tiles:
            found = self._find(name)
            self._tiles[name] = _Tile(found) if found else None
        return self._tiles[name]

    def elevation(self, lat: float, lon: float):
        lon = ((lon + 180.0) % 360.0) - 180.0
        la0, lo0 = math.floor(lat), math.floor(lon)
        # Adjacent tiles share their edge rows/columns, so a point exactly on an
        # edge can also be read from the neighbouring tile.
        candidates = [(la0, lo0)]
        if lat == la0:
            candidates.append((la0 - 1, lo0))
        if lon == lo0:
            candidates.append((la0, lo0 - 1))
            if lat == la0:
                candidates.append((la0 - 1, lo0 - 1))
        tile = None
        for la0, lo0 in candidates:
            tile = self._tile(tile_name(la0 + 0.5, lo0 + 0.5))
            if tile is not None:
                break
        if tile is None:
            return MISSING
        n1 = tile.n - 1
        y = (la0 + 1 - lat) * n1
        x = (lon - lo0) * n1
        r0 = min(int(y), n1 - 1)
        c0 = min(int(x), n1 - 1)
        fy, fx = y - r0, x - c0
        corners = ((r0, c0, (1 - fy) * (1 - fx)), (r0, c0 + 1, (1 - fy) * fx),
                   (r0 + 1, c0, fy * (1 - fx)), (r0 + 1, c0 + 1, fy * fx))
        total = weight = 0.0
        for r, c, w in corners:
            v = tile.sample(r, c)
            if v != VOID:
                total += v * w
                weight += w
        if weight <= 1e-9:
            return None
        return total / weight, self.name

    def fetch(self, points):
        if not os.path.isdir(self.path):
            raise ElevationError(f"{self.name}: tile directory {self.path} not found")
        try:
            return [self.elevation(lat, lon) for lat, lon in points]
        except OSError as exc:
            raise ElevationError(f"{self.name}: {exc}") from exc

    def close(self) -> None:
        for tile in self._tiles.values():
            if tile is not None:
                tile.close()
        self._tiles.clear()
