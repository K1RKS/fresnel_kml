"""Link analysis: site heights, path geometry, Fresnel radii and terrain clearance."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from . import fresnel, geo
from .colors import ColorError, ZoneColor, parse_color
from .coords import DISPLAY_FORMATS, format_position
from .elevation import ElevationResult
from .units import fmt_distance, fmt_height

MIN_DISTANCE_M = 1.0
SHORT_LINK_M = 50.0
LONG_LINK_M = 100_000.0
MAX_STATIONS = 512
MIN_TERRAIN_STATIONS = 16
TERRAIN_SPACING_M = 250.0
MIN_R1_FOR_CLEARANCE = 0.5
CLEAR_RATIO = 0.6
END_WINDOW_M = 500 * 0.3048  # close-up terrain sampling next to each tower
END_SAMPLES = 25


class AnalysisError(ValueError):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


@dataclass
class Site:
    name: str
    lat: float
    lon: float
    agl_m: float | None = None
    ground_m: float | None = None
    asl_m: float | None = None
    position_format: str = "decimal"


@dataclass
class Options:
    freq_ghz: float = 5.8
    zone: int = 1
    sixty: bool = True
    k: float = 4.0 / 3.0
    clutter_m: float = 0.0
    elev_source: str = "auto"
    stations: int = 64
    ring_points: int = 24
    style: str = "mesh"
    ring_every: int = 4
    terrain_check: bool | None = None  # None: on only when an elevation lookup is needed anyway
    mode: str = "absolute"
    color: str = "random"
    coord_display: str = "decimal"
    units: str = "m"  # display units for heights and distances; inputs are converted to metres before this
    trees: bool = False
    tree_height_m: float = 100 * 0.3048
    tree_clearance_a_m: float = 50 * 0.3048  # trees are ignored this far from site A
    tree_clearance_b_m: float = 50 * 0.3048  # and this far from site B


@dataclass
class SiteResult:
    name: str
    lat: float
    lon: float
    position_format: str
    height_ref: str
    asl_m: float
    agl_m: float | None
    ground_m: float | None
    ground_source: str | None


@dataclass
class MeshStation:
    t: float
    lat: float
    lon: float
    bearing: float
    los_m: float
    radius_m: float
    radius60_m: float


@dataclass
class ProfilePoint:
    t: float
    d_m: float
    lat: float
    lon: float
    ground_m: float
    bulge_m: float
    effective_m: float
    los_m: float
    r1_m: float
    clearance: float | None
    tree_top_m: float | None = None
    tree_clearance: float | None = None


@dataclass
class LinkReport:
    site_a: SiteResult
    site_b: SiteResult
    options: Options
    freq_hz: float
    wavelength_m: float
    distance_m: float
    bearing_ab: float
    bearing_ba: float
    zone_max_m: float
    r1_max_m: float
    r60_max_m: float
    stations: list[MeshStation]
    zone_color: ZoneColor
    terrain_checked: bool = False
    profile: list[ProfilePoint] = field(default_factory=list)
    profile_a: list[ProfilePoint] = field(default_factory=list)  # close-up next to site A
    profile_b: list[ProfilePoint] = field(default_factory=list)  # close-up next to site B
    end_window_m: float | None = None
    min_clearance: float | None = None
    min_clearance_d_m: float | None = None
    min_clearance_m: float | None = None  # line of sight minus effective terrain at the worst point
    status: str | None = None
    trees_checked: bool = False
    min_tree_clearance: float | None = None
    min_tree_clearance_d_m: float | None = None
    min_tree_clearance_m: float | None = None  # line of sight minus tree tops at the worst point
    tree_status: str | None = None
    elevation_sources: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def all_samples(self) -> list[ProfilePoint]:
        """Main profile plus the close-up samples near each tower, in path order."""
        merged = {round(p.d_m, 3): p for p in self.profile + self.profile_a + self.profile_b}
        return [merged[d] for d in sorted(merged)]

    @property
    def obstructions(self) -> list[ProfilePoint]:
        return [p for p in self.all_samples if p.clearance is not None and p.clearance < CLEAR_RATIO]

    def footprint(self, radius_attr: str = "radius_m") -> list[list[float]]:
        """Top-down outline of a zone: its widest left and right edges, as a closed [lat, lon] ring."""
        right, left = [], []
        for st in self.stations:
            r = getattr(st, radius_attr)
            theta = math.radians(st.bearing)
            east, north = r * math.cos(theta), -r * math.sin(theta)
            right.append(geo.offset(st.lat, st.lon, east, north))
            left.append(geo.offset(st.lat, st.lon, -east, -north))
        ring = right + left[::-1]
        return [[round(lat, 7), round(lon, 7)] for lat, lon in ring]

    @property
    def tree_obstructions(self) -> list[ProfilePoint]:
        return [p for p in self.all_samples if p.tree_clearance is not None and p.tree_clearance < CLEAR_RATIO]

    def to_dict(self) -> dict:
        def site(s: SiteResult) -> dict:
            return {
                "name": s.name, "lat": round(s.lat, 7), "lon": round(s.lon, 7),
                "display": format_position(s.lat, s.lon, self.options.coord_display),
                "from": s.position_format, "height_ref": s.height_ref,
                "asl_m": _r(s.asl_m), "agl_m": _r(s.agl_m), "ground_m": _r(s.ground_m),
                "ground_source": s.ground_source,
            }

        return {
            "sites": [site(self.site_a), site(self.site_b)],
            "freq_ghz": self.freq_hz / 1e9,
            "wavelength_m": round(self.wavelength_m, 8),
            "zone": self.options.zone,
            "k": self.options.k,
            "clutter_m": self.options.clutter_m,
            "distance_m": _r(self.distance_m),
            "bearing_ab": round(self.bearing_ab, 2),
            "bearing_ba": round(self.bearing_ba, 2),
            "zone_max_m": _r(self.zone_max_m, 3),
            "r1_max_m": _r(self.r1_max_m, 3),
            "r60_max_m": _r(self.r60_max_m, 3),
            "terrain_checked": self.terrain_checked,
            "min_clearance": None if self.min_clearance is None else round(self.min_clearance, 3),
            "min_clearance_d_m": _r(self.min_clearance_d_m),
            "min_clearance_m": _r(self.min_clearance_m),
            "units": self.options.units,
            "trees": {
                "enabled": self.trees_checked,
                "height_m": _r(self.options.tree_height_m),
                "clearance_a_m": _r(self.options.tree_clearance_a_m),
                "clearance_b_m": _r(self.options.tree_clearance_b_m),
                "min_clearance": None if self.min_tree_clearance is None else round(self.min_tree_clearance, 3),
                "min_clearance_d_m": _r(self.min_tree_clearance_d_m),
                "min_clearance_m": _r(self.min_tree_clearance_m),
                "status": self.tree_status,
            },
            "status": self.status,
            "zone_color": self.zone_color.to_dict(),
            "elevation_sources": self.elevation_sources,
            "map": {
                "los": [[round(st.lat, 7), round(st.lon, 7)] for st in self.stations],
                "zone": self.footprint("radius_m"),
                "zone60": self.footprint("radius60_m") if self.options.sixty else None,
            },
            "profile": [_point_dict(p) for p in self.profile],
            "profile_a": [_point_dict(p) for p in self.profile_a],
            "profile_b": [_point_dict(p) for p in self.profile_b],
            "end_window_m": _r(self.end_window_m),
            "warnings": self.warnings,
        }


def _r(value, digits: int = 2):
    return None if value is None else round(value, digits)


def _point_dict(p: ProfilePoint) -> dict:
    return {
        "d_m": _r(p.d_m), "lat": round(p.lat, 7), "lon": round(p.lon, 7),
        "ground_m": _r(p.ground_m), "effective_m": _r(p.effective_m), "los_m": _r(p.los_m),
        "r1_m": _r(p.r1_m, 3), "zone_bottom_m": _r(p.los_m - p.r1_m),
        "r60_bottom_m": _r(p.los_m - CLEAR_RATIO * p.r1_m),
        "clearance": None if p.clearance is None else round(p.clearance, 3),
        "tree_top_m": _r(p.tree_top_m),
        "tree_clearance": None if p.tree_clearance is None else round(p.tree_clearance, 3),
    }


def clearance_status(ratio: float | None) -> str:
    if ratio is None:
        return "UNKNOWN"
    if ratio >= CLEAR_RATIO:
        return "CLEAR"
    return "MARGINAL" if ratio >= 0 else "BLOCKED"


def end_window_points(site_a: Site, site_b: Site, distance: float, bearing_ab: float, bearing_ba: float,
                      window: float, samples: int = END_SAMPLES) -> tuple[list[geo.PathPoint], list[geo.PathPoint]]:
    """Closely spaced path points over the first and last `window` metres, each in path order."""
    near_a, near_b = [], []
    for i in range(samples + 1):
        d = window * i / samples
        lat, lon, brg = geo.forward(site_a.lat, site_a.lon, bearing_ab, d) if d else (site_a.lat, site_a.lon,
                                                                                         bearing_ab)
        near_a.append(geo.PathPoint(d / distance, lat, geo.normalize_lon(lon), brg))
        lat, lon, brg = geo.forward(site_b.lat, site_b.lon, bearing_ba, d) if d else (site_b.lat, site_b.lon,
                                                                                         bearing_ba)
        near_b.append(geo.PathPoint(1 - d / distance, lat, geo.normalize_lon(lon), (brg + 180) % 360))
    near_b.reverse()
    return near_a, near_b


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_options(opt: Options) -> None:
    checks = [
        ("freq_ghz", _finite(opt.freq_ghz) and 0.03 <= opt.freq_ghz <= 100, "must be between 0.03 and 100 GHz"),
        ("zone", isinstance(opt.zone, int) and not isinstance(opt.zone, bool) and 1 <= opt.zone <= 10,
         "must be an integer from 1 to 10"),
        ("k", _finite(opt.k) and 0.1 <= opt.k <= 100, "must be between 0.1 and 100"),
        ("clutter_m", _finite(opt.clutter_m) and 0 <= opt.clutter_m <= 200, "must be between 0 and 200 m"),
        ("stations", isinstance(opt.stations, int) and not isinstance(opt.stations, bool)
         and 2 <= opt.stations <= MAX_STATIONS, f"must be an integer from 2 to {MAX_STATIONS}"),
        ("ring_points", isinstance(opt.ring_points, int) and 6 <= opt.ring_points <= 72,
         "must be an integer from 6 to 72"),
        ("ring_every", isinstance(opt.ring_every, int) and opt.ring_every >= 1, "must be a positive integer"),
        ("style", opt.style in ("mesh", "rings"), "must be 'mesh' or 'rings'"),
        ("mode", opt.mode in ("absolute", "relative"), "must be 'absolute' or 'relative'"),
        ("coord_display", opt.coord_display in DISPLAY_FORMATS, f"must be one of {', '.join(DISPLAY_FORMATS)}"),
        ("units", opt.units in ("m", "ft"), "must be 'm' or 'ft'"),
        ("trees", isinstance(opt.trees, bool), "must be true or false"),
        ("tree_height_m", _finite(opt.tree_height_m) and 0 <= opt.tree_height_m <= 150,
         "must be between 0 and 150 m (about 490 ft)"),
        ("tree_clearance_a_m", _finite(opt.tree_clearance_a_m) and 0 <= opt.tree_clearance_a_m <= 50_000,
         "must be between 0 and 50 km"),
        ("tree_clearance_b_m", _finite(opt.tree_clearance_b_m) and 0 <= opt.tree_clearance_b_m <= 50_000,
         "must be between 0 and 50 km"),
        ("terrain_check", opt.terrain_check in (None, True, False), "must be true, false or null"),
        ("sixty", isinstance(opt.sixty, bool), "must be true or false"),
        ("elev_source", isinstance(opt.elev_source, str) and opt.elev_source != "", "must be a source name"),
    ]
    for name, ok, message in checks:
        if not ok:
            raise AnalysisError(f"options.{name} {message}.", f"options.{name}")


def validate_site(site: Site, label: str) -> None:
    if not isinstance(site.name, str) or not site.name.strip():
        raise AnalysisError(f"{label}.name is required.", f"{label}.name")
    if len(site.name) > 100:
        raise AnalysisError(f"{label}.name is longer than 100 characters.", f"{label}.name")
    if not _finite(site.lat) or not -90 <= site.lat <= 90:
        raise AnalysisError(f"{label}.lat must be within \u00b190.", f"{label}.lat")
    if not _finite(site.lon) or not -180 <= site.lon <= 180:
        raise AnalysisError(f"{label}.lon must be within \u00b1180.", f"{label}.lon")
    if (site.agl_m is None) == (site.asl_m is None):
        raise AnalysisError(f"{label} needs exactly one of agl_m (above ground) or asl_m (above sea level).",
                            f"{label}.agl_m")
    if site.agl_m is not None and (not _finite(site.agl_m) or not 0 <= site.agl_m < 1000):
        raise AnalysisError(f"{label}.agl_m must be at least 0 and below 1000 m.", f"{label}.agl_m")
    if site.asl_m is not None and (not _finite(site.asl_m) or not -500 <= site.asl_m <= 9000):
        raise AnalysisError(f"{label}.asl_m must be between -500 and 9000 m.", f"{label}.asl_m")
    if site.ground_m is not None:
        if not _finite(site.ground_m) or not -500 <= site.ground_m <= 9000:
            raise AnalysisError(f"{label}.ground_m must be between -500 and 9000 m.", f"{label}.ground_m")
        if site.asl_m is not None and site.asl_m - site.ground_m < 0:
            raise AnalysisError(f"{label}.asl_m is below its ground_m.", f"{label}.asl_m")


def terrain_station_count(distance_m: float, stations: int) -> int:
    n = max(stations, math.ceil(distance_m / TERRAIN_SPACING_M), MIN_TERRAIN_STATIONS)
    return min(n, MAX_STATIONS)


def analyze(site_a: Site, site_b: Site, options: Options | None = None, *, elevation=None, config=None,
            rng: random.Random | None = None, env=None) -> LinkReport:
    """Compute everything needed to render the KML. `elevation` is an ElevationChain (built from
    the server config when omitted and a lookup is needed)."""
    opt = options or Options()
    validate_options(opt)
    validate_site(site_a, "site_a")
    validate_site(site_b, "site_b")
    warnings: list[str] = []

    try:
        zone_color = parse_color(opt.color, rng)
    except ColorError as exc:
        raise AnalysisError(str(exc), "options.color") from None

    for label, s in (("site_a", site_a), ("site_b", site_b)):
        if opt.mode == "relative" and s.asl_m is not None and s.ground_m is None:
            raise AnalysisError(f"{label}: relative mode needs the height above ground; give agl_m, or ground_m "
                                "along with asl_m.", f"{label}.asl_m")

    distance, bearing_ab, bearing_ba = geo.inverse(site_a.lat, site_a.lon, site_b.lat, site_b.lon)
    if distance < MIN_DISTANCE_M:
        raise AnalysisError("The two sites are less than 1 m apart.", "site_b")
    n_mesh = opt.stations
    if distance < SHORT_LINK_M:
        warnings.append(f"Very short link ({fmt_distance(distance, opt.units)}); results may not be meaningful.")
        n_mesh = max(n_mesh, 8)
    if distance > LONG_LINK_M:
        warnings.append(f"Long link ({fmt_distance(distance, opt.units, 1)}); {opt.freq_ghz:g} GHz is unusual at "
                        "this range and earth bulge matters.")
    if not geo.have_pyproj() and max(abs(site_a.lat), abs(site_b.lat)) > geo.POLAR_LIMIT_DEG:
        warnings.append("Near the pole without pyproj installed; geometry uses a spherical approximation.")

    needs_ground = [s.agl_m is not None and s.ground_m is None for s in (site_a, site_b)]
    terrain = opt.terrain_check if opt.terrain_check is not None else any(needs_ground)

    profile_pts = geo.path_points(site_a.lat, site_a.lon, site_b.lat, site_b.lon,
                                  terrain_station_count(distance, n_mesh)) if terrain else []
    window = min(END_WINDOW_M, distance / 2)
    end_a_pts, end_b_pts = (end_window_points(site_a, site_b, distance, bearing_ab, bearing_ba, window)
                            if terrain else ([], []))
    lookup_pts: list[tuple[float, float]] = []
    if terrain:
        lookup_pts = [(p.lat, p.lon) for p in profile_pts + end_a_pts + end_b_pts]
    else:
        lookup_pts = [(s.lat, s.lon) for s, need in zip((site_a, site_b), needs_ground) if need]

    looked_up = []
    if lookup_pts:
        chain = elevation
        if chain is None:
            from .config import build_chain, load_config
            cfg = config or load_config(env=env)
            try:
                chain = build_chain(cfg, only=opt.elev_source, env=env)
            except KeyError:
                raise AnalysisError(f"Unknown elevation source '{opt.elev_source}'.", "options.elev_source") from None
        elif opt.elev_source not in ("auto", None):
            try:
                chain = chain.only(opt.elev_source)
            except KeyError:
                raise AnalysisError(f"Unknown elevation source '{opt.elev_source}'.", "options.elev_source") from None
        looked_up = chain.lookup(lookup_pts)
        if any(r.assumed_zero for r in looked_up):
            count = sum(r.assumed_zero for r in looked_up)
            warnings.append(f"{count} point(s) returned no elevation (open water?); treated as 0 m.")

    if terrain:
        main_found = looked_up[:len(profile_pts)]
        end_a_found = looked_up[len(profile_pts):len(profile_pts) + len(end_a_pts)]
        end_b_found = looked_up[len(profile_pts) + len(end_a_pts):]
        site_lookup = [main_found[0], main_found[-1]]
    else:
        it = iter(looked_up)
        site_lookup = [next(it) if need else None for need in needs_ground]

    results = []
    for label, s, found in (("site_a", site_a, site_lookup[0]), ("site_b", site_b, site_lookup[1])):
        if s.ground_m is not None:
            ground, source = float(s.ground_m), "manual"
        elif found is not None:
            ground, source = found.elevation, found.source
        else:
            ground, source = None, None
        if s.agl_m is not None:
            asl = ground + s.agl_m
            agl = float(s.agl_m)
            ref = "agl"
        else:
            asl = float(s.asl_m)
            ref = "asl"
            agl = None if ground is None else asl - ground
            if agl is not None and agl < 0 and s.ground_m is None:
                warnings.append(f"{s.name}: the given height above sea level is {fmt_height(-agl, opt.units)} "
                                f"below the looked-up ground ({source}).")
        results.append(SiteResult(s.name, s.lat, s.lon, s.position_format, ref, asl, agl, ground, source))
    ra, rb = results

    lam = fresnel.wavelength(opt.freq_ghz * 1e9)
    stations = []
    for p in geo.path_points(site_a.lat, site_a.lon, site_b.lat, site_b.lon, n_mesh):
        d1 = p.t * distance
        d2 = distance - d1
        los = (1 - p.t) * ra.asl_m + p.t * rb.asl_m
        r_n = fresnel.zone_radius(d1, d2, lam, opt.zone)
        r1 = fresnel.zone_radius(d1, d2, lam, 1)
        stations.append(MeshStation(p.t, p.lat, p.lon, p.bearing, los, r_n, CLEAR_RATIO * r1))

    report = LinkReport(
        site_a=ra, site_b=rb, options=opt, freq_hz=opt.freq_ghz * 1e9, wavelength_m=lam,
        distance_m=distance, bearing_ab=bearing_ab, bearing_ba=bearing_ba,
        zone_max_m=fresnel.max_radius(distance, lam, opt.zone),
        r1_max_m=fresnel.max_radius(distance, lam, 1),
        r60_max_m=CLEAR_RATIO * fresnel.max_radius(distance, lam, 1),
        stations=stations, zone_color=zone_color, warnings=warnings,
    )

    if terrain:
        report.terrain_checked = True
        report.end_window_m = window

        def sample(p: geo.PathPoint, found: ElevationResult) -> ProfilePoint:
            d1 = p.t * distance
            d2 = distance - d1
            bulge = fresnel.earth_bulge(d1, d2, opt.k)
            effective = found.elevation + bulge + opt.clutter_m
            los = (1 - p.t) * ra.asl_m + p.t * rb.asl_m
            r1 = fresnel.zone_radius(d1, d2, lam, 1)
            c = (los - effective) / r1 if r1 >= MIN_R1_FOR_CLEARANCE else None
            return ProfilePoint(p.t, d1, p.lat, p.lon, found.elevation, bulge, effective, los, r1, c)

        report.profile = [sample(p, f) for p, f in zip(profile_pts, main_found)]
        report.profile_a = [sample(p, f) for p, f in zip(end_a_pts, end_a_found)]
        report.profile_b = [sample(p, f) for p, f in zip(end_b_pts, end_b_found)]
        min_c = None
        min_d = None
        min_gap = None
        for p in report.all_samples:
            if p.clearance is not None and (min_c is None or p.clearance < min_c):
                min_c, min_d, min_gap = p.clearance, p.d_m, p.los_m - p.effective_m
        report.min_clearance = min_c
        report.min_clearance_d_m = min_d
        report.min_clearance_m = min_gap
        report.status = clearance_status(min_c)
        if min_c is None:
            warnings.append("Link too short for a meaningful Fresnel clearance check.")

        if opt.trees:
            report.trees_checked = True
            start, stop = opt.tree_clearance_a_m, distance - opt.tree_clearance_b_m
            for p in report.profile + report.profile_a + report.profile_b:
                if start <= p.d_m <= stop:
                    p.tree_top_m = p.effective_m + opt.tree_height_m
                    if p.r1_m >= MIN_R1_FOR_CLEARANCE:
                        p.tree_clearance = (p.los_m - p.tree_top_m) / p.r1_m
            worst = min((p for p in report.all_samples if p.tree_clearance is not None),
                        key=lambda p: p.tree_clearance, default=None)
            if worst is not None:
                report.min_tree_clearance = worst.tree_clearance
                report.min_tree_clearance_d_m = worst.d_m
                report.min_tree_clearance_m = worst.los_m - worst.tree_top_m
            else:
                warnings.append("No part of the path is outside both tree clearances, so trees weren't checked.")
            report.tree_status = clearance_status(report.min_tree_clearance)

    sources = [r.ground_source for r in (ra, rb) if r.ground_source]
    sources += [r.source for r in looked_up]
    report.elevation_sources = list(dict.fromkeys(sources))
    if opt.mode == "relative":
        warnings.append("Relative mode: towers follow Google Earth terrain, but the line of sight and the "
                        "Fresnel zone are still drawn at absolute altitudes.")
    return report
