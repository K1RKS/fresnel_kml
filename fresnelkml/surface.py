"""LIDAR obstruction check: highest-hit surface models (tree tops, roofs) sampled across the Fresnel zone."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field

from .arcgis import ImageService
from .cache import slug

# Sample lines across the zone, as fractions of the lidar width (which is itself a fraction of r1).
LATERAL_FRACTIONS = (0.0, -0.5, 0.5, -1.0, 1.0)
MAX_PLOT_POINTS = 600
MAX_MARKERS = 100


@dataclass
class SurfacePoint:
    """One station along the path; the worst sample across the zone at that distance."""
    d_m: float
    lat: float  # position of the worst sample
    lon: float
    offset_m: float  # its sideways distance from the line of sight
    top_m: float  # its surface height above sea level (no earth bulge)
    ground_m: float  # ground under the line of sight here
    effective_m: float  # ground + bulge + clutter, as in the terrain profile
    peak_m: float  # highest surface across the station, plus earth bulge (for the profile plot)
    los_m: float
    r1_m: float
    clearance: float | None
    lidar: bool  # False: no lidar here, the fixed tree height was used
    dataset: dict | None = None  # the lidar dataset of the worst sample: {"source", "name", "year"}

    @property
    def height_m(self) -> float:
        return self.top_m - self.ground_m


@dataclass
class SurfaceSamples:
    values: list[float | None]  # metres above sea level, None where no source has data
    labels: list[str | None]
    failed: int = 0
    timed_out: int = 0
    errors: list[str] = field(default_factory=list)
    datasets: list[tuple[int, int] | None] = field(default_factory=list)  # (source index, raster id) per value


def dataset_label(d: dict | None) -> str:
    """OLC Santiam 2018, or Some Survey (2016) when the name has no year in it."""
    if not d:
        return "unknown lidar"
    name, year = d.get("name") or "unnamed lidar", d.get("year")
    return f"{name} ({year})" if year and str(year) not in name else name


def flown_years(datasets: list[dict]) -> tuple[int, int] | None:
    """(oldest, newest) year of the lidar datasets used, or None when no year is known."""
    years = [d["year"] for d in datasets if isinstance(d.get("year"), int)]
    return (min(years), max(years)) if years else None


def flown_text(datasets: list[dict]) -> str:
    """Lidar flown in 2017, or Lidar flown between 2007 and 2018 when the datasets differ."""
    span = flown_years(datasets)
    if span is None:
        return ""
    return f"Lidar flown in {span[0]}" if span[0] == span[1] else f"Lidar flown between {span[0]} and {span[1]}"


def lateral_clearance(dz: float, offset_m: float, r1_m: float) -> float:
    """Clearance (in r1) of a surface top `dz` below the line of sight, `offset_m` to one side of it.

    On the centreline this is the usual signed (los - top) / r1. Off it, a top above the line of sight
    is still `offset_m` away from the zone's axis, so the distance is never less than that.
    """
    if offset_m == 0:
        return dz / r1_m
    return math.hypot(offset_m, max(0.0, dz)) / r1_m


def grid_spacing(length_m: float, spacing_m: float, lines: int, max_points: int) -> float:
    """The configured spacing, widened if the path would need more than max_points samples."""
    if length_m <= 0:
        return spacing_m
    stations = max(2, max_points // max(1, lines))
    return max(spacing_m, length_m / (stations - 1))


def _in_bbox(bbox, point) -> bool:
    if not bbox:
        return True
    lat, lon = point
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


class SurfaceSampler:
    """Samples the configured surface sources in order; later sources fill points earlier ones lack."""

    def __init__(self, cfg, caches=None):
        """`caches` is a cache.CacheSet (or None): each lidar source gets its own cache."""
        self.cfg = cfg
        self.caches = caches
        self.services = [(src, ImageService(src["url"], src["units"], cfg.timeout_s)) for src in cfg.sources]

    def sample(self, points: list[tuple[float, float]], deadline: float | None, progress=None) -> SurfaceSamples:
        """`progress(done, total)` counts points for each source in turn."""
        out = SurfaceSamples([None] * len(points), [None] * len(points), datasets=[None] * len(points))
        todo = list(range(len(points)))
        for si, (src, service) in enumerate(self.services):
            idx = [i for i in todo if _in_bbox(src.get("bbox"), points[i])]
            if not idx:
                continue
            cache = self.caches.for_service(src["name"], "surface") if self.caches else None
            res = service.sample([points[i] for i in idx], threads=self.cfg.threads, deadline=deadline,
                                 cache=cache, progress=progress)
            rasters = res.rasters or [None] * len(idx)
            for i, v, rid in zip(idx, res.values, rasters):
                if v is not None:
                    out.values[i] = v
                    out.labels[i] = src["label"]
                    out.datasets[i] = None if rid is None else (si, rid)
            out.failed += res.failed
            out.timed_out += res.timed_out
            out.errors += res.errors
            todo = [i for i in todo if out.values[i] is None]
        return out

    def _meta_path(self, src: dict) -> str | None:
        directory = getattr(self.caches, "directory", None) if self.caches else None
        return os.path.join(directory, "datasets", f"{slug(src['name'])}.json") if directory else None

    def _load_meta(self, src: dict) -> dict:
        path = self._meta_path(src)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (TypeError, OSError, ValueError):
            return {}

    def _save_meta(self, src: dict, data: dict) -> None:
        path = self._meta_path(src)
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, path)
        except OSError:
            pass  # a cache we can't write only costs a lookup next time

    def describe(self, wanted: dict[tuple[int, int], tuple[float, float]],
                 deadline: float | None) -> dict[tuple[int, int], dict]:
        """{(source index, raster id): {"source", "name", "year"}}, from the source's catalog (identify at a
        point the raster covers). Answers are kept in the cache folder, so each dataset is looked up once."""
        out = {}
        for si, (src, service) in enumerate(self.services):
            mine = {rid: pt for (s, rid), pt in wanted.items() if s == si}
            if not mine:
                continue
            known = self._load_meta(src)
            missing = {rid: pt for rid, pt in mine.items() if str(rid) not in known}
            if missing:
                found = service.datasets(missing, deadline=deadline)
                if found:
                    known.update({str(rid): info for rid, info in found.items()})
                    self._save_meta(src, known)
            for rid in mine:
                info = known.get(str(rid))
                if isinstance(info, dict):
                    out[(si, rid)] = {"source": src["label"], "name": info.get("name"), "year": info.get("year")}
        return out


def thin_for_plot(points: list[SurfacePoint], distance_m: float, bins: int = MAX_PLOT_POINTS) -> list[SurfacePoint]:
    """At most `bins` stations: the highest in each stretch of path, so peaks survive."""
    if len(points) <= bins:
        return list(points)
    best: dict[int, SurfacePoint] = {}
    for p in points:
        b = min(bins - 1, int(p.d_m / distance_m * bins))
        if b not in best or p.peak_m > best[b].peak_m:
            best[b] = p
    return [best[b] for b in sorted(best)]


def obstruction_markers(points: list[SurfacePoint], ratio: float, limit: int = MAX_MARKERS) -> list[SurfacePoint]:
    """The worst station of each run of stations below `ratio`, worst runs first if there are too many."""
    runs: list[SurfacePoint] = []
    worst = None
    for p in points:
        bad = p.clearance is not None and p.clearance < ratio
        if bad and (worst is None or p.clearance < worst.clearance):
            worst = p
        elif not bad and worst is not None:
            runs.append(worst)
            worst = None
    if worst is not None:
        runs.append(worst)
    if len(runs) > limit:
        keep = {id(p) for p in sorted(runs, key=lambda p: p.clearance)[:limit]}
        runs = [p for p in runs if id(p) in keep]
    return runs
