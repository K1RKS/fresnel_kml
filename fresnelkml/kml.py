"""KML rendering for a LinkReport (spec section 4)."""

from __future__ import annotations

import html
import math
import xml.etree.ElementTree as ET

from . import geo
from .analysis import CLEAR_RATIO, LinkReport, MeshStation, Options, Site, analyze
from .coords import format_position
from .units import fmt_distance, fmt_gap, fmt_height

KML_NS = "http://www.opengis.net/kml/2.2"
GX_NS = "http://www.google.com/kml/ext/2.2"

SIXTY_COLOR = "6600a5ff"
LOS_COLOR = "ff00ffff"
TOWER_COLOR = "ff0000ff"
OBSTRUCTION_COLOR = "ff0000ff"
PROFILE_COLOR = "ff3c8cd2"
TREE_OBSTRUCTION_COLOR = "ff228b22"
LIDAR_OBSTRUCTION_COLOR = "ffd27a1e"


def _sub(parent: ET.Element, tag: str, text=None, **attrib) -> ET.Element:
    el = ET.SubElement(parent, tag, attrib)
    if text is not None:
        el.text = str(text)
    return el


def _coord(lon: float, lat: float, alt: float) -> str:
    return f"{lon:.7f},{lat:.7f},{alt:.2f}"


def _coords(points: list[tuple[float, float, float]]) -> str:
    lons = geo.unwrap_lons([p[0] for p in points])
    return " ".join(_coord(lon, p[1], p[2]) for lon, p in zip(lons, points))


def _ring(station: MeshStation, radius: float, m: int) -> list[tuple[float, float, float]]:
    """M ring vertices (lon, lat, alt) around the LOS point, in the plane normal to the path."""
    if radius <= 0:
        return [(station.lon, station.lat, station.los_m)] * m
    theta = math.radians(station.bearing)
    out = []
    for j in range(m):
        phi = 2 * math.pi * j / m
        h = radius * math.cos(phi)
        v = radius * math.sin(phi)
        lat, lon = geo.offset(station.lat, station.lon, h * math.cos(theta), -h * math.sin(theta))
        out.append((lon, lat, station.los_m + v))
    return out


def _style(doc: ET.Element, sid: str, line=None, width=None, poly=None, outline=None, icon=None) -> None:
    st = _sub(doc, "Style", id=sid)
    if icon is not None:
        ic = _sub(st, "IconStyle")
        _sub(ic, "color", icon)
    if line is not None:
        ls = _sub(st, "LineStyle")
        _sub(ls, "color", line)
        if width is not None:
            _sub(ls, "width", width)
    if poly is not None:
        ps = _sub(st, "PolyStyle")
        _sub(ps, "color", poly)
        if outline is not None:
            _sub(ps, "outline", outline)


def _site_description(report: LinkReport, s) -> str:
    u = report.options.units
    lines = [
        f"Position: {html.escape(format_position(s.lat, s.lon, report.options.coord_display))}",
        f"Ground: {fmt_height(s.ground_m, u)}" + (f" ({html.escape(s.ground_source)})" if s.ground_source else ""),
        f"AGL: {fmt_height(s.agl_m, u)}",
        f"ASL: {fmt_height(s.asl_m, u)}" + (" (given)" if s.height_ref == "asl" else ""),
    ]
    return "<br/>".join(lines)


def _link_description(report: LinkReport) -> str:
    opt = report.options
    u = opt.units
    status = report.status or "not checked"
    clearance = "n/a" if report.min_clearance is None else f"{report.min_clearance:.2f} \u00d7 r1"
    if report.min_clearance_m is not None:
        clearance += f" ({fmt_gap(report.min_clearance_m, u)})"
    if report.min_clearance_d_m is not None:
        clearance += f" at {fmt_distance(report.min_clearance_d_m, u, 2)}"
    lines = [
        f"Frequency: {opt.freq_ghz:g} GHz (\u03bb = {report.wavelength_m:.7f} m)",
        f"Distance: {fmt_distance(report.distance_m, u)}",
        f"Bearing A\u2192B: {report.bearing_ab:.1f}\u00b0, B\u2192A: {report.bearing_ba:.1f}\u00b0",
    ]
    if opt.zone != 1:
        lines.append(f"Max r{opt.zone}: {fmt_height(report.zone_max_m, u, 2)}")
    lines += [
        f"Max r1: {fmt_height(report.r1_max_m, u, 2)}, 0.6\u00b7r1: {fmt_height(report.r60_max_m, u, 2)}",
        f"Min clearance: {clearance}",
        f"Status: {status}",
    ]
    if report.trees_checked:
        trees = "n/a"
        if report.min_tree_clearance is not None:
            trees = (f"{report.min_tree_clearance:.2f} \u00d7 r1 ({fmt_gap(report.min_tree_clearance_m, u, 'tree tops')})"
                     f" at {fmt_distance(report.min_tree_clearance_d_m, u, 2)}")
        lines += [
            f"Trees: {fmt_height(opt.tree_height_m, u)} tall, ignored within "
            f"{fmt_distance(opt.tree_clearance_a_m, u)} of {html.escape(report.site_a.name)} and "
            f"{fmt_distance(opt.tree_clearance_b_m, u)} of {html.escape(report.site_b.name)}",
            f"Min clearance over trees: {trees}",
            f"Tree status: {report.tree_status}",
        ]
    if report.surface_checked:
        lines += _surface_lines(report)
    lines += [
        f"k = {opt.k:.4g}, clutter {fmt_height(opt.clutter_m, u)}",
        "Elevation source: " + html.escape(", ".join(report.elevation_sources) or "none (heights given ASL)"),
        f"Zone color: {report.zone_color.hex} ({'random' if report.zone_color.random else 'chosen'})",
    ]
    return "<br/>".join(lines)


def _surface_lines(report: LinkReport) -> list[str]:
    opt, u = report.options, report.options.units
    w = report.surface_worst
    worst = "n/a"
    if w is not None and w.clearance is not None:
        what = f"lidar surface {fmt_height(w.height_m, u)} above ground" if w.lidar else "fixed-height trees"
        side = f", {fmt_distance(abs(w.offset_m), u, 2)} off the line of sight" if w.offset_m else ""
        worst = (f"{w.clearance:.2f} \u00d7 r1 ({what}{side}) at {fmt_distance(w.d_m, u, 2)}")
    sources = ", ".join(report.surface_sources) or "none"
    return [
        f"Lidar: {html.escape(sources)}, {100 * (report.surface_coverage or 0):.0f}% coverage, across "
        f"\u00b1{opt.lidar_width:g} \u00d7 r1, every {fmt_distance(report.surface_spacing_m, u, 1)}; "
        f"{fmt_height(opt.tree_height_m, u)} trees where there's no lidar",
        f"Ignored within {fmt_distance(opt.tree_clearance_a_m, u)} of {html.escape(report.site_a.name)} and "
        f"{fmt_distance(opt.tree_clearance_b_m, u)} of {html.escape(report.site_b.name)}",
        f"Min clearance over the lidar surface: {worst}",
        f"Lidar status: {report.surface_status}",
    ]


def _zone_placemark(folder: ET.Element, name: str, style: str, report: LinkReport, radius_attr: str,
                    description: str | None = None) -> None:
    opt = report.options
    pm = _sub(folder, "Placemark")
    _sub(pm, "name", name)
    if description:
        _sub(pm, "description", description)
    _sub(pm, "styleUrl", f"#{style}")
    mg = _sub(pm, "MultiGeometry")
    m = opt.ring_points
    rings = [_ring(st, getattr(st, radius_attr), m) for st in report.stations]
    if opt.style == "rings":
        for i, ring in enumerate(rings):
            if getattr(report.stations[i], radius_attr) <= 0:
                continue
            if i % opt.ring_every and i != len(rings) // 2:
                continue
            ls = _sub(mg, "LineString")
            _sub(ls, "altitudeMode", "absolute")
            _sub(ls, "coordinates", _coords(ring + [ring[0]]))
        return
    last = len(rings) - 1
    for i in range(last):
        a, b = rings[i], rings[i + 1]
        for j in range(m):
            k = (j + 1) % m
            if i == 0:
                pts = [a[0], b[k], b[j], a[0]]
            elif i + 1 == last:
                pts = [a[j], a[k], b[0], a[j]]
            else:
                pts = [a[j], a[k], b[k], b[j], a[j]]
            poly = _sub(mg, "Polygon")
            _sub(poly, "altitudeMode", "absolute")
            outer = _sub(poly, "outerBoundaryIs")
            lr = _sub(outer, "LinearRing")
            _sub(lr, "coordinates", _coords(pts))


def render_kml(report: LinkReport) -> str:
    opt = report.options
    a, b = report.site_a, report.site_b
    root = ET.Element("kml", {"xmlns": KML_NS, "xmlns:gx": GX_NS})
    doc = _sub(root, "Document")
    _sub(doc, "name", f"Fresnel {a.name} \u2013 {b.name} {opt.freq_ghz:g} GHz")
    _sub(doc, "description", _link_description(report))

    color = report.zone_color
    if opt.style == "rings":
        _style(doc, "zone", line=color.kml_opaque, width=1.5)
        _style(doc, "zone60", line="ff00a5ff", width=1.5)
    else:
        _style(doc, "zone", line="00000000", poly=color.kml, outline=0)
        _style(doc, "zone60", line="00000000", poly=SIXTY_COLOR, outline=0)
    _style(doc, "los", line=LOS_COLOR, width=3)
    _style(doc, "tower", line=TOWER_COLOR, width=4)
    _style(doc, "obstruction", icon=OBSTRUCTION_COLOR)
    _style(doc, "profile", line=PROFILE_COLOR, width=2)
    _style(doc, "tree_obstruction", icon=TREE_OBSTRUCTION_COLOR)
    _style(doc, "lidar_obstruction", icon=LIDAR_OBSTRUCTION_COLOR)

    sites = _sub(doc, "Folder")
    _sub(sites, "name", "Sites")
    for s in (a, b):
        pm = _sub(sites, "Placemark")
        _sub(pm, "name", s.name)
        _sub(pm, "description", _site_description(report, s))
        pt = _sub(pm, "Point")
        if opt.mode == "relative":
            _sub(pt, "altitudeMode", "relativeToGround")
            _sub(pt, "coordinates", _coord(s.lon, s.lat, s.agl_m))
        else:
            _sub(pt, "altitudeMode", "absolute")
            _sub(pt, "coordinates", _coord(s.lon, s.lat, s.asl_m))

        mast = _sub(sites, "Placemark")
        _sub(mast, "name", f"{s.name} mast")
        _sub(mast, "styleUrl", "#tower")
        if opt.mode == "relative":
            ls = _sub(mast, "LineString")
            _sub(ls, "altitudeMode", "relativeToGround")
            _sub(ls, "coordinates", f"{_coord(s.lon, s.lat, 0)} {_coord(s.lon, s.lat, s.agl_m)}")
        elif s.ground_m is not None and s.asl_m >= s.ground_m:
            ls = _sub(mast, "LineString")
            _sub(ls, "altitudeMode", "absolute")
            _sub(ls, "coordinates", f"{_coord(s.lon, s.lat, s.ground_m)} {_coord(s.lon, s.lat, s.asl_m)}")
        else:
            pt = _sub(mast, "Point")
            _sub(pt, "extrude", 1)
            _sub(pt, "altitudeMode", "absolute")
            _sub(pt, "coordinates", _coord(s.lon, s.lat, s.asl_m))

    los = _sub(doc, "Folder")
    _sub(los, "name", "Line of sight")
    pm = _sub(los, "Placemark")
    _sub(pm, "name", "Line of sight")
    _sub(pm, "styleUrl", "#los")
    ls = _sub(pm, "LineString")
    _sub(ls, "altitudeMode", "absolute")
    _sub(ls, "coordinates", _coords([(st.lon, st.lat, st.los_m) for st in report.stations]))

    zone = _sub(doc, "Folder")
    _sub(zone, "name", f"Fresnel zone {opt.zone} (100%)")
    _zone_placemark(zone, f"Fresnel zone {opt.zone}", "zone", report, "radius_m", _link_description(report))

    if opt.sixty:
        sixty = _sub(doc, "Folder")
        _sub(sixty, "name", "Fresnel 60%")
        _sub(sixty, "visibility", 0)
        _zone_placemark(sixty, "Fresnel zone 1, 60%", "zone60", report, "radius60_m")

    if report.terrain_checked:
        tc = _sub(doc, "Folder")
        _sub(tc, "name", "Terrain check")
        pm = _sub(tc, "Placemark")
        _sub(pm, "name", f"Terrain profile ({report.status})")
        _sub(pm, "styleUrl", "#profile")
        ls = _sub(pm, "LineString")
        _sub(ls, "tessellate", 1)
        _sub(ls, "altitudeMode", "clampToGround")
        _sub(ls, "coordinates", _coords([(p.lon, p.lat, 0.0) for p in report.profile]))
        u = opt.units
        for p in report.obstructions:
            pm = _sub(tc, "Placemark")
            _sub(pm, "name", f"c={p.clearance:.2f} @ {fmt_distance(p.d_m, u, 2)}")
            _sub(pm, "description", f"Ground {fmt_height(p.ground_m, u)}, effective {fmt_height(p.effective_m, u)}, "
                                    f"LOS {fmt_height(p.los_m, u)}, r1 {fmt_height(p.r1_m, u, 2)} "
                                    f"(needs {CLEAR_RATIO:.0%} = {fmt_height(CLEAR_RATIO * p.r1_m, u, 2)})")
            _sub(pm, "styleUrl", "#obstruction")
            pt = _sub(pm, "Point")
            _sub(pt, "altitudeMode", "absolute")
            _sub(pt, "coordinates", _coord(p.lon, p.lat, p.effective_m))

    if report.trees_checked:
        tf = _sub(doc, "Folder")
        _sub(tf, "name", f"Tree check ({report.tree_status})")
        u = opt.units
        for p in report.tree_obstructions:
            pm = _sub(tf, "Placemark")
            _sub(pm, "name", f"trees c={p.tree_clearance:.2f} @ {fmt_distance(p.d_m, u, 2)}")
            _sub(pm, "description", f"Tree tops {fmt_height(p.tree_top_m, u)} "
                                    f"({fmt_height(opt.tree_height_m, u)} trees on {fmt_height(p.effective_m, u)}), "
                                    f"LOS {fmt_height(p.los_m, u)}, r1 {fmt_height(p.r1_m, u, 2)}")
            _sub(pm, "styleUrl", "#tree_obstruction")
            pt = _sub(pm, "Point")
            _sub(pt, "altitudeMode", "absolute")
            _sub(pt, "coordinates", _coord(p.lon, p.lat, p.tree_top_m))

    if report.surface_checked:
        sf = _sub(doc, "Folder")
        _sub(sf, "name", f"Lidar check ({report.surface_status})")
        u = opt.units
        for p in report.surface_obstructions:
            pm = _sub(sf, "Placemark")
            kind = "lidar" if p.lidar else "trees"
            _sub(pm, "name", f"{kind} c={p.clearance:.2f} @ {fmt_distance(p.d_m, u, 2)}")
            what = (f"Lidar surface {fmt_height(p.top_m, u)} ({fmt_height(p.height_m, u)} above ground)" if p.lidar
                    else f"No lidar: {fmt_height(opt.tree_height_m, u)} trees, tops {fmt_height(p.top_m, u)}")
            side = f", {fmt_distance(abs(p.offset_m), u, 2)} off the line of sight" if p.offset_m else ""
            _sub(pm, "description", f"{what}{side}, LOS {fmt_height(p.los_m, u)}, r1 {fmt_height(p.r1_m, u, 2)}")
            _sub(pm, "styleUrl", "#lidar_obstruction" if p.lidar else "#tree_obstruction")
            pt = _sub(pm, "Point")
            _sub(pt, "altitudeMode", "absolute")
            _sub(pt, "coordinates", _coord(p.lon, p.lat, p.top_m))

    ET.indent(root, space=" ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def build_kml(site_a: Site, site_b: Site, options: Options | None = None, **kwargs) -> str:
    """Analyse the link and return the KML document as a string."""
    return render_kml(analyze(site_a, site_b, options, **kwargs))
