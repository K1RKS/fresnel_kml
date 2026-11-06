"""Point sampling from ArcGIS image services (getSamples), used for lidar ground and surface models."""

from __future__ import annotations

import json
import math
import queue
import sys
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
                return [None] * len(points)
            raise ImageServiceError(f"{self.url}: {err.get('message') or 'error response'}")
        samples = data.get("samples")
        if not isinstance(samples, list):
            raise ImageServiceError(f"{self.url}: response has no samples")
        out: list[float | None] = [None] * len(points)
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
        return out

    def sample(self, points: list[Point], *, threads: int = 4, deadline: float | None = None,
               cache=None, progress=None) -> SampleResult:
        """Sample any number of points, in parallel batches, until `deadline` (time.monotonic()).

        `cache` is a cache.PointCache (or None). `progress(done, total)` is called with point counts as
        batches finish (from worker threads).
        """
        values: list[float | None] = [None] * len(points)
        todo = list(range(len(points)))
        if cache is not None:
            todo = []
            for i, hit in enumerate(cache.get_many(points)):
                if hit is None:
                    todo.append(i)
                else:
                    values[i] = hit[0]
        batches = [todo[i:i + self.chunk] for i in range(0, len(todo), self.chunk)]
        result = SampleResult(values)
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
                return self.sample_batch([points[i] for i in batch], timeout)
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
                for i, v in zip(batch, outcome):
                    values[i] = v
                    if v is not None:
                        fresh.append((points[i], v, cache.name if cache is not None else ""))
        if cache is not None:
            cache.put_many(fresh)
        return result


def _run_serial(tasks: list, deadline: float | None) -> list:
    """Same result slots as run_parallel, on this thread. Pyodide has no real threads."""
    results: list = [None] * len(tasks)
    for i, task in enumerate(tasks):
        if deadline is not None and time.monotonic() >= deadline:
            break
        try:
            results[i] = task()
        except Exception as exc:  # noqa: BLE001 - reported per batch
            results[i] = exc
    return results


def run_parallel(tasks: list, threads: int, deadline: float | None) -> list:
    """Run callables on daemon threads; each slot gets its result, its exception, or None if unfinished.

    Daemon threads let a CGI process exit as soon as its answer is written, even if a slow
    request is still waiting on the network. The browser build has no threads, so it runs the
    tasks one after another on the worker that called it.
    """
    if sys.platform == "emscripten":
        return _run_serial(tasks, deadline)
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
