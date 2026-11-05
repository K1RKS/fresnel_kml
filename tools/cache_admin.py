#!/usr/bin/env python3
"""Look after the local lookup cache: show its size, apply the size limits now, or empty it."""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fresnelkml.cache import CacheSet, PointCache  # noqa: E402
from fresnelkml.config import ConfigError, load_config  # noqa: E402


def services(config, caches: CacheSet) -> dict[str, tuple[str, str]]:
    """Cache file stem -> (service name, type), for the configured services and any other files present."""
    known = {caches.file_stem(s["name"], s["type"]): (s["name"], s["type"]) for s in config.chain}
    known.update({caches.file_stem(s["name"], "surface"): (s["name"], "surface") for s in config.surface.sources})
    for stem in caches.existing():
        known.setdefault(stem, (stem, ""))
    return known


def open_cache(caches: CacheSet, stem: str, name: str, stype: str) -> PointCache:
    limit = caches.limit_mb(name, stype)
    return PointCache(caches.path_for(stem), int(limit * 1024 * 1024), stem)


def mb(n: int) -> str:
    return f"{n / 1024 / 1024:,.1f} MB"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("stats", "prune", "clear"))
    p.add_argument("--config", help="server config file (default: FRESNEL_CONFIG or /etc/fresnelkml/config.json)")
    p.add_argument("--dir", help="cache folder (default: the config's)")
    p.add_argument("--service", help="only this service (its name in the config, such as 3dep or dogami)")
    args = p.parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    directory = args.dir or config.cache.dir
    if not directory:
        print("Caching is off: no cache folder (set cache.dir, or create /var/cache/fresnelkml).", file=sys.stderr)
        return 1
    caches = CacheSet(directory, config.cache.max_mb, config.cache.types, config.cache.services)
    present = set(caches.existing())
    chosen = {stem: ns for stem, ns in services(config, caches).items()
              if stem in present and (not args.service or args.service in (ns[0], stem))}
    if args.service and not chosen:
        print(f"No cache for {args.service} in {directory}.", file=sys.stderr)
        return 1
    if not chosen:
        print(f"No caches yet in {directory}.")
        return 0
    for stem, (name, stype) in sorted(chosen.items()):
        cache = open_cache(caches, stem, name, stype)
        try:
            if args.command == "prune":
                removed = cache.enforce_limit()
                print(f"{stem}: removed {removed:,} points")
            elif args.command == "clear":
                cache.clear()
                print(f"{stem}: emptied")
            s = cache.stats()
            print(f"{stem}: {s['points']:,} points, {mb(s['used_bytes'])} of {mb(s['max_bytes'])}"
                  f" (file {mb(s['file_bytes'])}), used {s['oldest_use'] or '-'} to {s['last_use'] or '-'}"
                  + (f", sources: {', '.join(s['sources'])}" if s["sources"] else ""))
        finally:
            cache.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
