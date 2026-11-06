"""Point sampling from ArcGIS image services (getSamples), used for lidar ground and surface models."""

from __future__ import annotations

import json
import math
import queue
import re
import threading
import time
from dataclasses import dataclass, field

from . import httpjson

UNIT_SCALE = {"m": 1.0, "ft": 0.3048, "ftUS": 1200 / 3937}
MAX_POINTS_PER_REQUEST = 1000  # services drop anything past their limit without saying so

Point = tuple[float, float]  # (lat, lon)


class ImageServiceError(Exception):
    pass


@dataclass
class SampleResult:
    """Values in metres (None where the service has no data), plus how the fetch went."""
    values: list[float | None]
    failed: int = 0  # points in batches that errored
    timed_out: int = 0  # points in batches not finished within the time budget
    errors: list[str] = field(default_factory=list)
    rasters: list[int | None] = field(default_factory=list)  # the service's dataset (raster) id for each value


_RASTER_LABEL = re.compile(r"^raster:(\d+)$")
_ACRONYMS = {"OLC", "USACE", "USGS", "NOAA", "FEMA", "DOGAMI", "ODOT", "BLM", "NRCS", "OSIP", "QL1", "QL2"}
_NAME_NOISE = re.compile(r"_(HIGHEST_HIT|FIRST_RETURN|BARE_EARTH|DSM|DTM|DEM)(_.*)?$", re.IGNORECASE)


def dataset_name(raw: str) -> str:
    """A readable dataset name: OLC_SANTIAM_2018_HIGHEST_HIT_OGIC_2011 -> OLC Santiam 2018."""
    words = _NAME_NOISE.sub("", raw).replace("-", "_").split("_")
    return " ".join(w if w.upper() in _ACRONYMS or w.isdigit() else w.capitalize() for w in words if w) or raw


def dataset_year(name: str, zorder) -> int | None:
    """Image services that mosaic by date carry the year in ZOrder; otherwise take a year from the name."""
    if isinstance(zorder, (int, float)) and 1990 <= zorder <= 2100:
        return int(zorder)
    years = [int(y) for y in re.findall(r"(?<!\d)(?:19|20)\d\d(?!\d)", name)]
    return years[0] if years else None


class ImageService:
    def __init__(self, url: str, units: str = "m", timeout_s: float = 20.0,
                 interpolation: str = "RSP_NearestNeighbor", chunk: int = MAX_POINTS_PER_REQUEST):
        if units not in UNIT_SCALE:
            raise ValueError(f"units must be one of {', '.join(UNIT_SCALE)}")
        self.url = url.rstrip("/")
        self.scale = UNIT_SCALE[units]
        self.timeout_s = timeout_s
        self.interpolation = interpolation
        self.chunk = max(1, min(chunk, MAX_POINTS_PER_REQUEST))

    def sample_batch(self, points: list[Point], timeout_s: float | None = None) -> list[float | None]:
        """One getSamples request for up to `chunk` points."""
        return self.sample_batch_ids(points, timeout_s)[0]

    def sample_batch_ids(self, points: list[Point],
                         timeout_s: float | None = None) -> tuple[list[float | None], list[int | None]]:
        """Like sample_batch, plus the raster (dataset) id each value came from."""
        if len(points) > self.chunk:
            raise ValueError(f"at most {self.chunk} points per request")
        geometry = {"points": [[round(lon, 7), round(lat, 7)] for lat, lon in points],
                    "spatialReference": {"wkid": 4326}}
        form = {"geometry": json.dumps(geometry, separators=(",", ":")), "geometryType": "esriGeometryMultipoint",
                "returnFirstValueOnly": "true", "interpolation": self.interpolation, "f": "json"}
        try:
            data = httpjson.post_form_json(f"{self.url}/getSamples", form, timeout=timeout_s or self.timeout_s)
        except httpjson.HttpError as exc:
            raise ImageServiceError(str(exc)) from exc
        if not isinstance(data, dict):
            raise ImageServiceError(f"{self.url}: bad response")
        if "error" in data:
            err = data["error"] if isinstance(data["error"], dict) else {}
            if err.get("code") == 400:  # the whole batch is outside the service's coverage
                return [None] * len(points), [None] * len(points)
            raise ImageServiceError(f"{self.url}: {err.get('message') or 'error response'}")
        samples = data.get("samples")
        if not isinstance(samples, list):
            raise ImageServiceError(f"{self.url}: response has no samples")
        out: list[float | None] = [None] * len(points)
        rasters: list[int | None] = [None] * len(points)
        for s in samples:
            idx = s.get("locationId") if isinstance(s, dict) else None
            if not isinstance(idx, int) or not 0 <= idx < len(points):
                continue
            try:
                value = float(s.get("value"))
            except (TypeError, ValueError):
                continue  # "NoData"
            if math.isfinite(value):
                out[idx] = value * self.scale
                rid = s.get("rasterId")
                rasters[idx] = rid if isinstance(rid, int) and not isinstance(rid, bool) else None
        return out, rasters

    def datasets(self, raster_points: dict[int, Point], timeout_s: float = 8.0, threads: int = 4,
                 deadline: float | None = None) -> dict[int, dict]:
        """{raster id: {"name", "year"}} for each id, looked up with identify at a point that id covers."""
        def lookup(rid: int, point: Point) -> dict | None:
            geometry = {"x": round(point[1], 7), "y": round(point[0], 7), "spatialReference": {"wkid": 4326}}
            data = httpjson.get_json(f"{self.url}/identify", {
                "geometry": json.dumps(geometry, separators=(",", ":")), "geometryType": "esriGeometryPoint",
                "returnCatalogItems": "true", "returnGeometry": "false", "f": "json"}, timeout=timeout_s)
            items = ((data or {}).get("catalogItems") or {}).get("features") or []
            for item in items:
                attrs = item.get("attributes") or {}
                if attrs.get("OBJECTID") == rid and isinstance(attrs.get("Name"), str):
                    return {"name": dataset_name(attrs["Name"]), "year": dataset_year(attrs["Name"], attrs.get("ZOrder"))}
            return None

        pairs = list(raster_points.items())
        outcomes = run_parallel([lambda r=r, p=p: lookup(r, p) for r, p in pairs], threads, deadline)
        return {rid: info for (rid, _), info in zip(pairs, outcomes) if isinstance(info, dict)}

    def sample(self, points: list[Point], *, threads: int = 4, deadline: float | None = None,
               cache=None, progress=None) -> SampleResult:
        """Sample any number of points, in parallel batches, until `deadline` (time.monotonic()).

        `cache` is a cache.PointCache (or None). `progress(done, total)` is called with point counts as
        batches finish (from worker threads).
        """
        values: list[float | None] = [None] * len(points)
        rasters: list[int | None] = [None] * len(points)
        todo = list(range(len(points)))
        if cache is not None:
            todo = []
            for i, hit in enumerate(cache.get_many(points)):
                if hit is None:
                    todo.append(i)
                else:
                    values[i] = hit[0]
                    m = _RASTER_LABEL.match(hit[1] or "")
                    rasters[i] = int(m.group(1)) if m else None
        batches = [todo[i:i + self.chunk] for i in range(0, len(todo), self.chunk)]
        result = SampleResult(values, rasters=rasters)
        done = [len(points) - len(todo)]
        lock = threading.Lock()
        if progress:
            progress(done[0], len(points))
        if not batches:
            return result

        def task(batch: list[int]):
            try:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0.5:
                    raise TimeoutError
                timeout = self.timeout_s if remaining is None else min(self.timeout_s, remaining)
                return self.sample_batch_ids([points[i] for i in batch], timeout)
            finally:
                if progress:
                    with lock:
                        done[0] += len(batch)
                        progress(done[0], len(points))

        outcomes = run_parallel([lambda b=b: task(b) for b in batches], threads, deadline)
        fresh = []
        for batch, outcome in zip(batches, outcomes):
            if outcome is None or isinstance(outcome, TimeoutError):
                result.timed_out += len(batch)
            elif isinstance(outcome, Exception):
                result.failed += len(batch)
                result.errors.append(str(outcome))
            else:
                for i, v, rid in zip(batch, *outcome):
                    values[i] = v
                    rasters[i] = rid
                    if v is not None:
                        label = f"raster:{rid}" if rid is not None else (cache.name if cache is not None else "")
                        fresh.append((points[i], v, label))
        if cache is not None:
            cache.put_many(fresh)
        return result


def run_parallel(tasks: list, threads: int, deadline: float | None) -> list:
    """Run callables on daemon threads; each slot gets its result, its exception, or None if unfinished.

    Daemon threads let a CGI process exit as soon as its answer is written, even if a slow
    request is still waiting on the network.
    """
    results: list = [None] * len(tasks)
    pending: queue.Queue = queue.Queue()
    for i in range(len(tasks)):
        pending.put(i)

    def worker():
        while deadline is None or time.monotonic() < deadline:
            try:
                i = pending.get_nowait()
            except queue.Empty:
                return
            try:
                results[i] = tasks[i]()
            except Exception as exc:  # noqa: BLE001 - reported per batch
                results[i] = exc

    workers = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, min(threads, len(tasks))))]
    for w in workers:
        w.start()
    for w in workers:
        w.join(None if deadline is None else max(0.0, deadline - time.monotonic()))
    return list(results)
