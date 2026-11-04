#!/usr/bin/env python3
"""Command-line entry point: see `fresnel_kml.py --help`."""

import sys

from fresnelkml.cli import main

if __name__ == "__main__":
    sys.exit(main())
