"""Size-limited local cache of point lookups (ground elevations, lidar samples): one SQLite file per service."""

from __future__ import annotations

import glob
import os
import re
import sqlite3
import sys
import time

DEFAULT_DIR = "/var/cache/fresnelkml"
DEFAULT_MAX_MB = 1024
QUERY_CHUNK = 500  # keys per IN (...) query, well under SQLite's variable limit
TRIM_TO = 0.9  # an over-full cache is trimmed to this share of its limit

_SCALE = 1_000_000  # 1e-6 degree steps, about 0.1 m
_LON_SPAN = 400_000_000  # more than 360e6, so lat and lon steps never overlap in the packed key

_SCHEMA = """
CREATE TABLE IF NOT EXISTS p (k INTEGER PRIMARY KEY, v REAL NOT NULL, s INTEGER NOT NULL, t INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS p_t ON p(t);
CREATE TABLE IF NOT EXISTS src (id INTEGER PRIMARY KEY, label TEXT NOT NULL UNIQUE);
"""


def point_key(lat: float, lon: float) -> int:
    """A point rounded to 1e-6 degrees, packed into one int64 (the table's rowid)."""
    return round((lat + 90.0) * _SCALE) * _LON_SPAN + round(((lon + 180.0) % 360.0) * _SCALE)


def key_point(key: int) -> tuple[float, float]:
    lat_q, lon_q = divmod(key, _LON_SPAN)
    return lat_q / _SCALE - 90.0, lon_q / _SCALE - 180.0


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "service"


def _today() -> int:
    return int(time.time() // 86400)


def _chunks(items: list, size: int = QUERY_CHUNK):
    for i in range(0, len(items), size):
        yield items[i:i + size]


class PointCache:
    """One service's cache. Any SQLite problem turns it off for the rest of the process; lookups then
    simply go to the service, so a request never fails because of the cache."""

    def __init__(self, path: str, max_bytes: int, name: str | None = None):
        self.path = path
        self.max_bytes = max_bytes
        self.name = name or os.path.splitext(os.path.basename(path))[0]
        self.disabled = False
        self._db: sqlite3.Connection | None = None
        self._labels: dict[int, str] = {}
        self._ids: dict[str, int] = {}

    def _conn(self) -> sqlite3.Connection:
        if self._db is None:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            db = sqlite3.connect(self.path, timeout=5, isolation_level=None, check_same_thread=False)
            db.execute("PRAGMA auto_vacuum=INCREMENTAL")  # only takes effect on a new, empty file
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA mmap_size=268435456")
            db.executescript(_SCHEMA)
            self._db = db
        return self._db

    def _fail(self, exc: Exception) -> None:
        if not self.disabled:
            print(f"fresnelkml: cache {self.name} turned off for this request: {exc}", file=sys.stderr)
        self.disabled = True
        self.close()

    def _label(self, db: sqlite3.Connection, sid: int) -> str:
        if sid not in self._labels:
            row = db.execute("SELECT label FROM src WHERE id = ?", (sid,)).fetchone()
            self._labels[sid] = row[0] if row else self.name
        return self._labels[sid]

    def _source_id(self, db: sqlite3.Connection, label: str) -> int:
        if label not in self._ids:
            db.execute("INSERT OR IGNORE INTO src (label) VALUES (?)", (label,))
            self._ids[label] = db.execute("SELECT id FROM src WHERE label = ?", (label,)).fetchone()[0]
        return self._ids[label]

    def get_many(self, points: list[tuple[float, float]]) -> list[tuple[float, str] | None]:
        """(value, source label) for each cached point, None for the others."""
        out: list[tuple[float, str] | None] = [None] * len(points)
        if self.disabled or not points:
            return out
        try:
            db = self._conn()
            keys = [point_key(lat, lon) for lat, lon in points]
            found: dict[int, tuple[float, int, int]] = {}
            for chunk in _chunks(list(set(keys))):
                marks = ",".join("?" * len(chunk))
                for k, v, s, t in db.execute(f"SELECT k, v, s, t FROM p WHERE k IN ({marks})", chunk):
                    found[k] = (v, s, t)
            today = _today()
            stale = [k for k, (_, _, t) in found.items() if t < today]
            if stale:
                db.execute("BEGIN IMMEDIATE")
                try:
                    for chunk in _chunks(stale):
                        db.execute(f"UPDATE p SET t = ? WHERE k IN ({','.join('?' * len(chunk))})", [today, *chunk])
                    db.execute("COMMIT")
                except BaseException:
                    db.execute("ROLLBACK")
                    raise
            for i, k in enumerate(keys):
                hit = found.get(k)
                if hit is not None:
                    out[i] = (hit[0], self._label(db, hit[1]))
        except sqlite3.Error as exc:
            self._fail(exc)
            return [None] * len(points)
        return out

    def put_many(self, items: list[tuple[tuple[float, float], float, str]]) -> None:
        """Store (point, value, source label) items in one transaction, then keep to the size limit."""
        if self.disabled or not items:
            return
        try:
            db = self._conn()
            today = _today()
            db.execute("BEGIN IMMEDIATE")
            try:
                rows = [(point_key(*pt), float(v), self._source_id(db, label), today) for pt, v, label in items]
                db.executemany("INSERT OR REPLACE INTO p (k, v, s, t) VALUES (?, ?, ?, ?)", rows)
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                self._ids.clear()
                raise
            self.enforce_limit()
        except sqlite3.Error as exc:
            self._fail(exc)

    def used_bytes(self) -> int:
        db = self._conn()
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        pages = db.execute("PRAGMA page_count").fetchone()[0]
        free = db.execute("PRAGMA freelist_count").fetchone()[0]
        return (pages - free) * page_size

    def enforce_limit(self) -> int:
        """Delete the least recently used points until the cache is under its limit; returns how many."""
        db = self._conn()
        removed = 0
        for _ in range(5):
            used = self.used_bytes()
            if used <= self.max_bytes:
                break
            rows = db.execute("SELECT count(*) FROM p").fetchone()[0]
            if not rows:
                break
            n = max(1, min(rows, int(rows * (1 - TRIM_TO * self.max_bytes / used)) + 1))
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("DELETE FROM p WHERE k IN (SELECT k FROM p ORDER BY t LIMIT ?)", (n,))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            removed += n
        if removed:
            db.execute("PRAGMA incremental_vacuum").fetchall()  # it frees pages one result row at a time
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        return removed

    def stats(self) -> dict:
        db = self._conn()
        rows, oldest, newest = db.execute("SELECT count(*), min(t), max(t) FROM p").fetchone()
        sources = [r[0] for r in db.execute("SELECT label FROM src ORDER BY id")]
        size = sum(os.path.getsize(f) for f in (self.path, self.path + "-wal") if os.path.exists(f))
        day = lambda d: None if d is None else time.strftime("%Y-%m-%d", time.gmtime(d * 86400))  # noqa: E731
        return {"service": self.name, "points": rows, "used_bytes": self.used_bytes(), "file_bytes": size,
                "max_bytes": self.max_bytes, "oldest_use": day(oldest), "last_use": day(newest), "sources": sources}

    def clear(self) -> None:
        db = self._conn()
        db.execute("DELETE FROM p")
        db.execute("DELETE FROM src")
        self._ids.clear()
        self._labels.clear()
        db.execute("VACUUM")

    def close(self) -> None:
        if self._db is not None:
            try:
                self._db.close()
            except sqlite3.Error:
                pass
            self._db = None


class CacheSet:
    """The caches under one folder, with the size limit for each service from the config."""

    def __init__(self, directory: str | None, max_mb: float = DEFAULT_MAX_MB, types: dict | None = None,
                 services: dict | None = None):
        self.directory = directory
        self.max_mb = max_mb
        self.types = dict(types or {})
        self.services = dict(services or {})
        self._open: dict[str, PointCache] = {}

    @classmethod
    def from_config(cls, config) -> CacheSet | None:
        c = getattr(config, "cache", None)
        if c is None or not c.dir:
            return None
        return cls(c.dir, c.max_mb, c.types, c.services)

    def limit_mb(self, name: str, stype: str) -> float:
        if name in self.services:
            return self.services[name]
        if stype in self.types:
            return self.types[stype]
        return self.max_mb

    @staticmethod
    def file_stem(name: str, stype: str) -> str:
        return f"surface-{slug(name)}" if stype == "surface" else slug(name)

    def path_for(self, stem: str) -> str:
        return os.path.join(self.directory, "points", f"{stem}.sqlite")

    def for_service(self, name: str, stype: str) -> PointCache | None:
        """The cache for one service, or None when caching is off for it."""
        if not self.directory:
            return None
        limit = self.limit_mb(name, stype)
        if not limit or limit <= 0:
            return None
        stem = self.file_stem(name, stype)
        if stem not in self._open:
            self._open[stem] = PointCache(self.path_for(stem), int(limit * 1024 * 1024), stem)
        return self._open[stem]

    def existing(self) -> list[str]:
        """File stems of the caches present in the folder."""
        if not self.directory:
            return []
        return sorted(os.path.splitext(os.path.basename(p))[0]
                      for p in glob.glob(os.path.join(self.directory, "points", "*.sqlite")))

    def close(self) -> None:
        for c in self._open.values():
            c.close()
        self._open.clear()


def default_dir() -> str | None:
    """DEFAULT_DIR when it exists and this process can write to it."""
    d = DEFAULT_DIR
    return d if os.path.isdir(d) and os.access(d, os.W_OK | os.X_OK) else None
