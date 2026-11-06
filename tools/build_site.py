#!/usr/bin/env python3
"""Assemble the self-contained web tree (dist/site/) and a tarball for NGINX."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fresnelkml import __version__  # noqa: E402


def stamp_assets(site: str) -> None:
    """Add a content hash to the script and stylesheet links so browsers fetch new versions."""
    index = os.path.join(site, "index.html")
    with open(index, encoding="utf-8") as fh:
        html = fh.read()
    for asset in ("app.js", "sites.js", "api.js", "engine.js", "style.css"):
        with open(os.path.join(site, asset), "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()[:10]
        html = html.replace(f'"{asset}"', f'"{asset}?v={digest}"')
    with open(index, "w", encoding="utf-8") as fh:
        fh.write(html)


def package_manifest(package_dir: str) -> list[str]:
    return sorted(name for name in os.listdir(package_dir) if name.endswith(".py"))


def build(out_dir: str, name: str = "site", tarball: bool = True) -> str:
    site = os.path.join(out_dir, name)
    if os.path.exists(site):
        shutil.rmtree(site)
    shutil.copytree(os.path.join(ROOT, "web"), site,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".*", "pyodide"))
    pkg = os.path.join(site, "lib", "fresnelkml")
    shutil.copytree(os.path.join(ROOT, "fresnelkml"), pkg,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    with open(os.path.join(site, "lib", "fresnelkml.json"), "w", encoding="utf-8") as fh:
        json.dump(package_manifest(pkg), fh)
    # Tests build the tree without downloading the runtime. A real build fetches it.
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import fetch_pyodide
        runtime = fetch_pyodide.fetch()
        shutil.copytree(runtime, os.path.join(site, "pyodide"))
    with open(os.path.join(site, "VERSION"), "w", encoding="utf-8") as fh:
        fh.write(__version__ + "\n")
    # GitHub Pages runs Jekyll unless this file is present, and Jekyll drops some of the
    # Pyodide runtime files. The Pages workflow must upload hidden files for this to survive.
    with open(os.path.join(site, ".nojekyll"), "w", encoding="utf-8") as fh:
        fh.write("")
    stamp_assets(site)
    cgi = os.path.join(site, "api", "fresnel.cgi")
    os.chmod(cgi, os.stat(cgi).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if tarball:
        tar_path = os.path.join(out_dir, f"fresnel-{name}.tar.gz")
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(site, arcname=name)
        return tar_path
    return site


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default=os.path.join(ROOT, "dist"), help="output directory (default dist/)")
    p.add_argument("--name", default="site", help="tree name, which is also the URL prefix (default site)")
    p.add_argument("--no-tarball", action="store_true")
    args = p.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    result = build(args.out, args.name, not args.no_tarball)
    print(f"Built {os.path.join(args.out, args.name)}/")
    if not args.no_tarball:
        print(f"Tarball {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
