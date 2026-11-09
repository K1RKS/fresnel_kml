#!/usr/bin/env python3
"""Download the pinned Pyodide core runtime into web/pyodide/ (not the scientific-package set)."""

from __future__ import annotations

import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION = "0.29.4"
DEST = os.path.join(ROOT, "web", "pyodide")
BASE = f"https://cdn.jsdelivr.net/pyodide/v{VERSION}/full/"
# pyodide.js (for the worker's importScripts) loads pyodide.asm.js, which instantiates
# pyodide.asm.wasm and unpacks python_stdlib.zip. loadPyodide also reads pyodide-lock.json.
FILES = ("pyodide.js", "pyodide.mjs", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip",
         "pyodide-lock.json")
# Unvendored from Pyodide's stdlib. The page loads this wheel from the same directory.
PACKAGES = ("sqlite3",)


def _present(name: str) -> bool:
    path = os.path.join(DEST, name)
    return os.path.isfile(path) and os.path.getsize(path) > 0


def package_files() -> list[str]:
    lock_path = os.path.join(DEST, "pyodide-lock.json")
    if not os.path.isfile(lock_path):
        return []
    with open(lock_path, encoding="utf-8") as fh:
        lock = json.load(fh)
    return [lock["packages"][name]["file_name"] for name in PACKAGES]


def all_files() -> list[str]:
    return list(FILES) + package_files()


def ready() -> bool:
    stamp = os.path.join(DEST, "VERSION")
    if not os.path.isfile(stamp):
        return False
    with open(stamp, encoding="utf-8") as fh:
        if fh.read().strip() != VERSION:
            return False
    return all(_present(name) for name in all_files())


def _download(name: str) -> None:
    url = BASE + name
    dest = os.path.join(DEST, name)
    partial = dest + ".part"
    print(f"Fetching {url}", file=sys.stderr)
    urllib.request.urlretrieve(url, partial)
    os.replace(partial, dest)


def fetch() -> str:
    """Download the runtime when it is missing or from a different pin. Returns the directory."""
    os.makedirs(DEST, exist_ok=True)
    stamp = os.path.join(DEST, "VERSION")
    if os.path.isfile(stamp):
        with open(stamp, encoding="utf-8") as fh:
            if fh.read().strip() != VERSION:
                for name in os.listdir(DEST):
                    os.remove(os.path.join(DEST, name))
    for name in FILES:
        if not _present(name):
            _download(name)
    for name in package_files():
        if not _present(name):
            _download(name)
    with open(stamp, "w", encoding="utf-8") as fh:
        fh.write(VERSION + "\n")
    return DEST


def main(argv=None) -> int:
    print(fetch())
    return 0


if __name__ == "__main__":
    sys.exit(main())
