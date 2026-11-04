"""Command-line interface (spec section 5)."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .analysis import AnalysisError, LinkReport, Options, analyze
from .config import ConfigError, Config, build_chain, load_config
from .coords import DISPLAY_FORMATS, format_position
from .elevation import ElevationChain, ElevationUnavailable
from .kml import render_kml
from .parsing import FEET, RequestError, parse_site_string, read_sites_csv
from .units import MILE, fmt_distance, fmt_gap, fmt_height
from .webapi import safe_filename

EXIT_INPUT = 2
EXIT_ELEVATION = 3

PROVIDER_CHOICES = ("opentopodata", "usgs", "open-elevation", "google", "none")


class _ChainAction(argparse.Action):
    """Collect elevation-source flags in the order they appear on the command line."""

    def __call__(self, parser, namespace, values, option_string=None):
        specs = list(getattr(namespace, "chain_specs", None) or [])
        specs.append((self.dest, values))
        namespace.chain_specs = specs


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fresnel_kml.py",
        description="Generate a Google Earth Pro KML showing the Fresnel zone between two radio sites.",
        epilog="Sites: 'Name,LAT,LON,HEIGHT' or 'Name,POSITION,HEIGHT'. HEIGHT is above ground unless it ends "
               "in 'asl' (e.g. 412.5asl). LAT/LON accept decimal, DMS or DDM; POSITION accepts UTM, MGRS, "
               "Maidenhead, Plus codes, geo: URIs and ISO 6709.")
    p.add_argument("--a", help="site A")
    p.add_argument("--b", help="site B")
    p.add_argument("--csv", help="CSV with 2 rows: name,lat,lon (or position),agl_m or asl_m[,ground_m]")
    p.add_argument("-o", "--output", help="output KML path ('-' for stdout; default derived from the site names)")
    p.add_argument("--json-report", metavar="PATH", help="also write the analysis report as JSON")
    p.add_argument("--freq-ghz", type=float, default=5.8)
    p.add_argument("--zone", type=int, default=1)
    p.add_argument("--sixty", action=argparse.BooleanOptionalAction, default=True,
                   help="include the hidden 60%% zone (default on)")
    p.add_argument("--k", type=float, default=4.0 / 3.0, help="effective earth radius factor (default 4/3)")
    p.add_argument("--clutter", type=float, default=0.0, help="clutter height added to terrain")
    p.add_argument("--config", help="server-style config file with the elevation chain")
    p.add_argument("--elev-provider", choices=PROVIDER_CHOICES, action=_ChainAction, dest="provider",
                   help="add a provider to a one-off elevation chain (repeatable, in order)")
    p.add_argument("--opentopodata-url", action=_ChainAction, dest="otd_url", metavar="URL",
                   help="add an OpenTopoData server (e.g. self-hosted) to the chain")
    p.add_argument("--hgt-dir", action=_ChainAction, dest="hgt_dir", metavar="DIR",
                   help="add a folder of SRTM .hgt tiles to the chain")
    p.add_argument("--dataset", default=None, help="OpenTopoData dataset list (default ned10m,srtm30m)")
    p.add_argument("--elev-source", default="auto", help="use only this named source from --config")
    p.add_argument("--google-key", default=None, help="Google Elevation API key (or env GOOGLE_ELEVATION_KEY)")
    p.add_argument("--ground-a", type=float, help="ground elevation at site A (overrides lookups)")
    p.add_argument("--ground-b", type=float, help="ground elevation at site B (overrides lookups)")
    p.add_argument("--stations", type=int, default=64)
    p.add_argument("--ring-points", type=int, default=24)
    p.add_argument("--style", choices=("mesh", "rings"), default="mesh")
    p.add_argument("--ring-every", type=int, default=4, help="with --style rings, draw every Nth ring")
    p.add_argument("--terrain-check", action=argparse.BooleanOptionalAction, default=None,
                   help="sample terrain along the path (default: on when a lookup is needed anyway)")
    p.add_argument("--mode", choices=("absolute", "relative"), default="absolute")
    p.add_argument("--units", choices=("m", "ft"), default="m", help="units for heights, ground and clutter")
    p.add_argument("--color", default="random", help="zone color: random, #RRGGBB, #RRGGBBAA or a name")
    p.add_argument("--coord-display", choices=DISPLAY_FORMATS, default="decimal")
    p.add_argument("-q", "--quiet", action="store_true", help="don't print the summary table")
    return p


def _chain_from_args(args, env) -> ElevationChain | None:
    specs = getattr(args, "chain_specs", None)
    if not specs and (args.dataset or args.google_key):
        specs = [("provider", "opentopodata" if args.dataset else "google")]
    if not specs:
        return None
    chain_cfg = []
    for kind, value in specs:
        if kind == "provider":
            if value == "none":
                continue
            entry = {"type": value, "name": value}
            if value == "opentopodata" and args.dataset:
                entry["datasets"] = args.dataset
            if value == "google" and args.google_key:
                entry["key"] = args.google_key
            chain_cfg.append(entry)
        elif kind == "otd_url":
            entry = {"type": "opentopodata", "name": f"opentopodata@{value}", "url": value}
            if args.dataset:
                entry["datasets"] = args.dataset
            chain_cfg.append(entry)
        elif kind == "hgt_dir":
            chain_cfg.append({"type": "local_hgt", "name": f"local:{value}", "path": value})
    seen = set()
    for entry in chain_cfg:
        while entry["name"] in seen:
            entry["name"] += "+"
        seen.add(entry["name"])
    return build_chain(Config(chain=chain_cfg), env=env)


def summary(report: LinkReport) -> str:
    opt = report.options
    u = opt.units
    rows = []
    for s in (report.site_a, report.site_b):
        rows.append(f"  {s.name}: {format_position(s.lat, s.lon, opt.coord_display)} "
                    f"(from {s.position_format})")
        ground = "unknown" if s.ground_m is None else f"{fmt_height(s.ground_m, u)} ({s.ground_source})"
        rows.append(f"    ground {ground}, AGL {fmt_height(s.agl_m, u)}, ASL {fmt_height(s.asl_m, u)}")
    lines = [
        f"Fresnel {report.site_a.name} - {report.site_b.name} at {opt.freq_ghz:g} GHz",
        *rows,
        f"  Distance {fmt_distance(report.distance_m, u)}, bearing A->B {report.bearing_ab:.1f} deg, "
        f"B->A {report.bearing_ba:.1f} deg",
        f"  Max r1 {fmt_height(report.r1_max_m, u, 2)}, 0.6*r1 {fmt_height(report.r60_max_m, u, 2)}"
        + (f", max r{opt.zone} {fmt_height(report.zone_max_m, u, 2)}" if opt.zone != 1 else ""),
        f"  Zone color {report.zone_color.hex} ({'random' if report.zone_color.random else 'chosen'})",
    ]
    if report.terrain_checked:
        c = "n/a" if report.min_clearance is None else f"{report.min_clearance:.2f} x r1"
        if report.min_clearance_m is not None:
            c += f" ({fmt_gap(report.min_clearance_m, u)}) at {fmt_distance(report.min_clearance_d_m, u, 2)}"
        lines.append(f"  Terrain check: {report.status}, min clearance {c}")
        lines.append(f"    k={opt.k:.4g}, clutter {fmt_height(opt.clutter_m, u)}")
        if report.obstructions:
            h = 1 / FEET if u == "ft" else 1.0
            dist_unit, dist_scale = ("mi", 1 / MILE) if u == "ft" else ("km", 1 / 1000)
            lines.append(f"    d ({dist_unit})   ground   effective   LOS      r1     clearance   heights in {u}")
            for p in report.obstructions:
                lines.append(f"    {p.d_m * dist_scale:7.3f}  {p.ground_m * h:7.1f}  {p.effective_m * h:9.1f}  "
                             f"{p.los_m * h:7.1f}  {p.r1_m * h:6.2f}  {p.clearance:8.2f}")
    else:
        lines.append("  Terrain check: not run")
    for w in report.warnings:
        lines.append(f"  Warning: {w}")
    return "\n".join(lines)


def main(argv=None, env=None) -> int:
    env = os.environ if env is None else env
    parser = build_parser()
    args = parser.parse_args(argv)
    warnings: list[str] = []
    try:
        if args.csv:
            if args.a or args.b:
                parser.error("use either --csv or --a/--b, not both")
            (site_a, site_b), warnings = read_sites_csv(args.csv, args.units)
            scale = FEET if args.units == "ft" else 1.0
            if args.ground_a is not None:
                site_a.ground_m = args.ground_a * scale
            if args.ground_b is not None:
                site_b.ground_m = args.ground_b * scale
        else:
            if not (args.a and args.b):
                parser.error("give --a and --b (or --csv)")
            site_a, wa = parse_site_string(args.a, "--a", args.units, args.ground_a)
            site_b, wb = parse_site_string(args.b, "--b", args.units, args.ground_b)
            warnings = wa + wb
        opt = Options(freq_ghz=args.freq_ghz, zone=args.zone, sixty=args.sixty, k=args.k,
                      clutter_m=args.clutter * (FEET if args.units == "ft" else 1.0),
                      elev_source=args.elev_source, stations=args.stations, ring_points=args.ring_points,
                      style=args.style, ring_every=args.ring_every, terrain_check=args.terrain_check,
                      mode=args.mode, color=args.color, coord_display=args.coord_display, units=args.units)
        run_env = dict(env)
        if args.google_key:
            run_env["GOOGLE_ELEVATION_KEY"] = args.google_key
        chain = _chain_from_args(args, run_env)
        config = None if chain is not None else load_config(args.config, env=run_env)
        report = analyze(site_a, site_b, opt, elevation=chain, config=config, env=run_env)
    except (RequestError, AnalysisError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT
    except ElevationUnavailable as exc:
        print(f"error: {exc}\nGive --ground-a/--ground-b, or heights ending in 'asl', and --no-terrain-check "
              "to work without elevation lookups.", file=sys.stderr)
        return EXIT_ELEVATION

    report.warnings[:0] = warnings
    kml = render_kml(report)
    output = args.output or safe_filename(report.site_a.name, report.site_b.name)
    if output == "-":
        sys.stdout.write(kml)
        info = sys.stderr
    else:
        with open(output, "w", encoding="utf-8") as fh:
            fh.write(kml)
        info = sys.stdout
    if args.json_report:
        with open(args.json_report, "w", encoding="utf-8") as fh:
            json.dump(report.to_dict(), fh, indent=1, ensure_ascii=False)
    if not args.quiet:
        print(summary(report), file=info)
        if output != "-":
            print(f"Wrote {output}", file=info)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
