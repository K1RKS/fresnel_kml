"""Ground elevation sources and the per-point fallback chain."""

from __future__ import annotations

import os
import re
import tempfile
import time
from dataclasses import dataclass

from . import httpjson
from .arcgis import ImageService, ImageServiceError

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None

MISSING = object()  # the source doesn't cover this point

_sleep = time.sleep


class ElevationError(Exception):
    """A source failed (network error, bad response, unreadable file)."""


class ElevationUnavailable(Exception):
    """No source in the chain could supply some of the requested points."""


@dataclass
class ElevationResult:
    elevation: float
    source: str
    assumed_zero: bool = False


Point = tuple[float, float]


class ElevationProvider:
    """Base class. Subclasses implement fetch() for one batch of points.

    fetch() returns, for each point, (elevation_m, source_label), None for a
    null value at a covered point (for example open water), or MISSING when the
    source doesn't cover the point. It raises ElevationError on failure.
    """

    type = "base"
    batch = 100
    min_interval_s = 0.0
    retries: int | None = None  # None: use the chain's setting

    def __init__(self, name: str):
        self.name = name

    def fetch(self, points: list[Point]) -> list:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"name": self.name, "type": self.type}


def _locations(points: list[Point]) -> str:
    return "|".join(f"{lat:.6f},{lon:.6f}" for lat, lon in points)


def _as_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ElevationError(f"unexpected elevation value {value!r}") from None


class OpenTopoDataProvider(ElevationProvider):
    type = "opentopodata"

    def __init__(self, name: str, url: str = "https://api.opentopodata.org/v1",
                 datasets: str = "ned10m,srtm30m", batch: int = 100, min_interval_s: float = 1.0,
                 timeout_s: float = 10.0):
        super().__init__(name)
        if not re.fullmatch(r"[A-Za-z0-9_.,-]+", datasets):
            raise ValueError(f"invalid dataset list {datasets!r}")
        self.url = url.rstrip("/")
        self.datasets = datasets
        self.batch = batch
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s

    def fetch(self, points):
        try:
            data = httpjson.get_json(f"{self.url}/{self.datasets}", {"locations": _locations(points)},
                                     timeout=self.timeout_s)
        except httpjson.HttpError as exc:
            raise ElevationError(str(exc)) from exc
        if not isinstance(data, dict) or data.get("status") != "OK":
            detail = data.get("error") if isinstance(data, dict) else None
            raise ElevationError(f"{self.name}: {detail or 'bad response'}")
        results = data.get("results") or []
        if len(results) != len(points):
            raise ElevationError(f"{self.name}: expected {len(points)} results, got {len(results)}")
        out = []
        for item in results:
            elev = _as_float(item.get("elevation"))
            dataset = item.get("dataset")
            out.append(None if elev is None else (elev, f"{self.name}:{dataset}" if dataset else self.name))
        return out


class OpenElevationProvider(ElevationProvider):
    type = "open-elevation"

    def __init__(self, name: str, url: str = "https://api.open-elevation.com/api/v1/lookup",
                 batch: int = 100, min_interval_s: float = 1.0, timeout_s: float = 10.0):
        super().__init__(name)
        self.url = url
        self.batch = batch
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s

    def fetch(self, points):
        try:
            data = httpjson.get_json(self.url, {"locations": _locations(points)}, timeout=self.timeout_s)
        except httpjson.HttpError as exc:
            raise ElevationError(str(exc)) from exc
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list) or len(results) != len(points):
            raise ElevationError(f"{self.name}: bad response")
        out = []
        for item in results:
            elev = _as_float(item.get("elevation"))
            out.append(None if elev is None else (elev, self.name))
        return out


class USGSProvider(ElevationProvider):
    type = "usgs"
    batch = 1
    NO_DATA = -1_000_000

    def __init__(self, name: str, url: str = "https://epqs.nationalmap.gov/v1/json",
                 min_interval_s: float = 0.2, timeout_s: float = 10.0):
        super().__init__(name)
        self.url = url
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s

    def fetch(self, points):
        out = []
        for lat, lon in points:
            params = {"x": f"{lon:.6f}", "y": f"{lat:.6f}", "units": "Meters", "wkid": "4326"}
            try:
                data = httpjson.get_json(self.url, params, timeout=self.timeout_s)
            except httpjson.HttpError as exc:
                raise ElevationError(str(exc)) from exc
            if not isinstance(data, dict) or "value" not in data:
                raise ElevationError(f"{self.name}: bad response")
            elev = _as_float(data["value"])
            if elev is None or elev <= self.NO_DATA:
                out.append(MISSING)
            else:
                out.append((elev, self.name))
        return out


class GoogleProvider(ElevationProvider):
    type = "google"

    def __init__(self, name: str, url: str = "https://maps.googleapis.com/maps/api/elevation/json",
                 key_env: str = "GOOGLE_ELEVATION_KEY", key: str | None = None, batch: int = 512,
                 min_interval_s: float = 0.0, timeout_s: float = 10.0, env=None):
        super().__init__(name)
        self.url = url
        self.batch = batch
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s
        env = os.environ if env is None else env
        self.key = key or env.get(key_env)

    def fetch(self, points):
        if not self.key:
            raise ElevationError(f"{self.name}: no Google API key configured")
        try:
            data = httpjson.get_json(self.url, {"locations": _locations(points), "key": self.key},
                                     timeout=self.timeout_s)
        except httpjson.HttpError as exc:
            raise ElevationError(str(exc).replace(self.key, "***")) from exc
        if not isinstance(data, dict) or data.get("status") != "OK":
            status = data.get("status") if isinstance(data, dict) else "bad response"
            raise ElevationError(f"{self.name}: {status}")
        results = data.get("results") or []
        if len(results) != len(points):
            raise ElevationError(f"{self.name}: bad response")
        return [(float(item["elevation"]), self.name) for item in results]


class ArcGISImageProvider(ElevationProvider):
    """Bare-earth elevation from an ArcGIS image service, such as USGS 3DEP."""
    type = "arcgis_image"

    def __init__(self, name: str, url: str, units: str = "m", batch: int = 1000, retries: int = 0,
                 timeout_s: float = 25.0):
        super().__init__(name)
        self.service = ImageService(url, units, timeout_s, interpolation="RSP_BilinearInterpolation", chunk=batch)
        self.batch = self.service.chunk
        self.retries = retries

    def fetch(self, points):
        try:
            values = self.service.sample_batch(points)
        except ImageServiceError as exc:
            raise ElevationError(f"{self.name}: {exc}") from exc
        return [MISSING if v is None else (v, self.name) for v in values]


# --------------------------------------------------------------------------
# Throttle and cache
# --------------------------------------------------------------------------

def wait_turn(key: str, min_interval_s: float) -> None:
    """Space requests for `key` at least min_interval_s apart, across processes."""
    if min_interval_s <= 0:
        return
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
    path = os.path.join(tempfile.gettempdir(), f"fresnelkml-{safe}.throttle")
    try:
        fh = open(path, "a+")
    except OSError:
        return
    with fh:
        if fcntl is not None:
            fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        text = fh.read().strip()
        try:
            last = float(text) if text else 0.0
        except ValueError:
            last = 0.0
        wait = last + min_interval_s - time.time()
        if wait > 0:
            _sleep(min(wait, min_interval_s))
        fh.seek(0)
        fh.truncate()
        fh.write(repr(time.time()))
        fh.flush()


# --------------------------------------------------------------------------
# Chain
# --------------------------------------------------------------------------

class ElevationChain:
    def __init__(self, providers: list[ElevationProvider], retries: int = 2, caches=None):
        """`caches` is a cache.CacheSet (or None): each provider gets its own size-limited cache."""
        self.providers = list(providers)
        self.retries = retries
        self.caches = caches
        self.errors: list[str] = []

    def _cache(self, provider: ElevationProvider):
        return self.caches.for_service(provider.name, provider.type) if self.caches else None

    def describe(self) -> list[dict]:
        return [p.describe() for p in self.providers]

    def only(self, name: str) -> "ElevationChain":
        matches = [p for p in self.providers if p.name == name]
        if not matches:
            raise KeyError(name)
        return ElevationChain(matches, self.retries, self.caches)

    def _fetch_with_retries(self, provider: ElevationProvider, batch: list[Point]) -> list:
        last = None
        retries = self.retries if provider.retries is None else provider.retries
        for attempt in range(retries + 1):
            wait_turn(provider.name, provider.min_interval_s)
            try:
                result = provider.fetch(batch)
                if len(result) != len(batch):
                    raise ElevationError(f"{provider.name}: wrong number of results")
                return result
            except ElevationError as exc:
                last = exc
                if attempt < retries:
                    _sleep(0.5 * (attempt + 1))
        raise last

    def lookup(self, points: list[Point]) -> list[ElevationResult]:
        results: list[ElevationResult | None] = [None] * len(points)
        saw_null = [False] * len(points)
        pending = list(range(len(points)))
        for provider in self.providers:
            if not pending:
                break
            still = []
            fetch_idx = []
            cache = self._cache(provider)
            hits = cache.get_many([points[i] for i in pending]) if cache else [None] * len(pending)
            for i, cached in zip(pending, hits):
                if cached is not None:
                    results[i] = ElevationResult(cached[0], cached[1])
                else:
                    fetch_idx.append(i)
            fresh = []
            failed = False
            for start in range(0, len(fetch_idx), max(1, provider.batch)):
                chunk = fetch_idx[start:start + max(1, provider.batch)]
                if failed:
                    still.extend(chunk)
                    continue
                try:
                    values = self._fetch_with_retries(provider, [points[i] for i in chunk])
                except ElevationError as exc:
                    self.errors.append(str(exc))
                    failed = True
                    still.extend(chunk)
                    continue
                for i, value in zip(chunk, values):
                    if value is MISSING:
                        still.append(i)
                    elif value is None:
                        saw_null[i] = True
                        still.append(i)
                    else:
                        results[i] = ElevationResult(float(value[0]), value[1])
                        fresh.append((points[i], float(value[0]), value[1]))
            if cache:
                cache.put_many(fresh)
            pending = sorted(still)

        unavailable = []
        for i in pending:
            if saw_null[i]:
                results[i] = ElevationResult(0.0, "null (assumed 0 m)", assumed_zero=True)
            else:
                unavailable.append(i)
        if unavailable:
            detail = "; ".join(dict.fromkeys(self.errors)) or "no configured source covers these points"
            raise ElevationUnavailable(
                f"Could not get ground elevation for {len(unavailable)} of {len(points)} point(s): {detail}")
        return results  # type: ignore[return-value]
