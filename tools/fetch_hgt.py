#!/usr/bin/env python3
"""Download 1 arc-second SRTM tiles for a region, for the local_hgt elevation source.

Tiles come from the public AWS terrain tiles bucket and are decompressed by
default so lookups can read them in place.
"""

from __future__ import annotations

import argparse
import gzip
import math
import os
import shutil
import sys
import urllib.error
import urllib.request

BASE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/skadi"
RAW_TILE_BYTES = 3601 * 3601 * 2


def tile_names(south: float, west: float, north: float, east: float) -> list[str]:
    names = []
    for lat in range(math.floor(south), math.ceil(north)):
        for lon in range(math.floor(west), math.ceil(east)):
            ns = "N" if lat >= 0 else "S"
            ew = "E" if lon >= 0 else "W"
            names.append(f"{ns}{abs(lat):02d}{ew}{abs(lon):03d}")
    return names


def parse_bbox(text: str) -> tuple[float, float, float, float]:
    try:
        s, w, n, e = (float(v) for v in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("bbox must be S,W,N,E in decimal degrees") from None
    if not (-90 <= s < n <= 90 and -180 <= w < e <= 180):
        raise argparse.ArgumentTypeError("bbox must satisfy S < N and W < E")
    return s, w, n, e


def fetch(name: str, out_dir: str, keep_gz: bool, base_url: str) -> str:
    target = os.path.join(out_dir, name + (".hgt.gz" if keep_gz else ".hgt"))
    if os.path.exists(target):
        return "exists"
    url = f"{base_url}/{name[:3]}/{name}.hgt.gz"
    tmp = target + ".part"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as fh:
            if keep_gz:
                shutil.copyfileobj(resp, fh)
            else:
                with gzip.GzipFile(fileobj=resp) as gz:
                    shutil.copyfileobj(gz, fh)
    except urllib.error.HTTPError as exc:
        if os.path.exists(tmp):
            os.remove(tmp)
        return "missing" if exc.code in (403, 404) else f"error HTTP {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        if os.path.exists(tmp):
            os.remove(tmp)
        return f"error {exc}"
    os.replace(tmp, target)
    return "ok"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bbox", required=True, type=parse_bbox, help="S,W,N,E, e.g. 42,-125,49,-116.5")
    p.add_argument("--out", required=True, help="tile folder, e.g. /var/lib/fresnelkml/hgt")
    p.add_argument("--keep-gz", action="store_true", help="store compressed .hgt.gz (smaller, slower lookups)")
    p.add_argument("--base-url", default=BASE_URL)
    p.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    args = p.parse_args(argv)

    names = tile_names(*args.bbox)
    est = len(names) * (10 if args.keep_gz else RAW_TILE_BYTES / 1e6)
    print(f"{len(names)} tile(s), roughly {est / 1000:.2f} GB on disk "
          f"({'compressed' if args.keep_gz else 'uncompressed'}).")
    if not args.yes:
        if input("Download? [y/N] ").strip().lower() not in ("y", "yes"):
            return 1
    os.makedirs(args.out, exist_ok=True)
    failures = 0
    for i, name in enumerate(names, 1):
        status = fetch(name, args.out, args.keep_gz, args.base_url)
        print(f"[{i}/{len(names)}] {name}: {status}")
        failures += status.startswith("error")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
