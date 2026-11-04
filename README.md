# fresnel_kml

Generates a Google Earth Pro KML file showing the line of sight and the Fresnel zone (5.8 GHz by default) between two radio sites, as a 3D semi-transparent shape. It can also check where terrain intrudes into the zone.

It comes in three forms that share one Python package (`fresnelkml/`):

- a **web service**: a static page plus a JSON API, deployed as one folder dropped into an NGINX web root and run through fcgiwrap;
- a **command-line tool**: `fresnel_kml.py`;
- a **library**: `build_kml(site_a, site_b, options)` and `analyze(...)`.

The functional spec is [docs/SPEC.md](docs/SPEC.md); the implementation plan and design decisions are in [docs/PLAN.md](docs/PLAN.md).

Requirements: Python 3.10 or newer. Nothing else is required. `requests` and `pyproj` are used when installed (`pyproj` gives WGS-84 geodesics instead of a spherical model).

## Web service

Build the drop-in folder and tarball:

```bash
python3 tools/build_site.py          # -> dist/site/ and dist/fresnel-site.tar.gz
```

Install it on the server with `tar xzf fresnel-site.tar.gz -C /var/www/html/`. The one-time NGINX and fcgiwrap setup is in [deploy/INSTALL.md](deploy/INSTALL.md).

For local development, `python3 tools/dev_server.py` serves the page at http://127.0.0.1:8000/ with the API behind it.

### API

`POST /site/api/fresnel.cgi` with a JSON body:

```json
{
  "site_a": {"name": "TowerA", "lat": 37.0, "lon": -122.0, "agl_m": 30},
  "site_b": {"name": "TowerB", "position": "10S 597811 4100985", "asl_m": 412.5},
  "options": {"freq_ghz": 5.8, "color": "random", "terrain_check": true},
  "format": "kml"
}
```

```bash
curl -X POST -H 'Content-Type: application/json' -d @req.json https://HOST/site/api/fresnel.cgi -o link.kml
```

- **Position**: `lat` and `lon` (decimal degrees by default, or DMS/DDM strings such as `"37°02'03\"N"`), or one `position` string: a lat,lon pair, UTM, MGRS, Maidenhead locator, full Plus code, `geo:` URI or ISO 6709.
- **Height**: exactly one of `agl_m` (above ground; the ground is looked up) or `asl_m` (above sea level; no lookup needed). `ground_m` overrides the lookup.
- **Options** (all optional): `freq_ghz`, `zone`, `sixty`, `k`, `clutter_m`, `elev_source`, `stations`, `ring_points`, `style` (`mesh`/`rings`), `ring_every`, `terrain_check` (`true`/`false`/`null` = only when a lookup is needed anyway), `mode` (`absolute`/`relative`), `units` (`m`/`ft`), `color` (`random`, `#RRGGBB`, `#RRGGBBAA` or a name), `coord_display` (`decimal`/`dms`/`ddm`).
- **Response**: the KML file as a download, or `{"kml", "report", "warnings"}` with `"format": "json"`. Errors are JSON `{"error", "field"}` with status 400, 405, 413, 502 (no elevation available) or 504.
- `GET ?sources` lists the configured elevation sources; `GET ?parse=TEXT[&axis=lat|lon]` decodes a coordinate.
- `GET ?nodes` returns `{"feeds", "nodes", "warnings"}`: the mesh nodes from the configured Nodographer feeds, with name, position, antenna height (metres above ground), azimuth, beamwidth, gain, frequency (MHz) and RF links (tunnel and DTD links are left out). An unreachable feed is a warning, not an error.

### Mesh node picker

When the server has a Nodographer feed configured (by default the [K1RKS MeshMap](https://meshmap.k1rks.me/meshmap/) `nodes.json`), two tabs appear above the sites:

- **Node selection:** each site gets a "Mesh node" box. As you type, matching nodes are listed with the match in bold, and the first name that starts with what you typed is completed in the box; Tab or Enter accepts it, and the arrow keys or the mouse pick another. Once one site is picked, the other site's list starts with that node's RF links, nearest first. You can still type a site by hand without picking a node.
- **Link selection:** Site A must be a mesh node, and Site B is chosen from a "Link from" list of Site A's RF links (nearest first, with distance, bearing and signal). Choosing a link fills Site B and sets the frequency from Site A. A link to a node the feed has no location for is marked "(no location in the feed)"; choosing it fills Site B's name and the frequency, and you enter its position and antenna height.

A picked node fills the name, position, antenna height above ground and the link frequency. These are defaults: a field you change is highlighted in amber, its tooltip shows the node's value, and the site's note lists what you changed. Clearing a changed field puts the node's value back. Emptying the Mesh node box drops the pick and keeps the values as plain manual entry. Switching the height units converts picked heights, overrides included.

AREDN's antenna height is meant to be above ground, but some nodes have their height above sea level entered there. When a picked node's height is over 200 ft (61 m), picking the node switches "Height is" to "above sea level", highlighted as a change; set it back to "above ground" if the tower really is that tall. If a node's height still works out to more than 100 ft (30.5 m) above the ground, the results warn that it may be incorrect unless the antenna is on a high tower. If taking it as above sea level puts the antenna underground, the results say the node's height can't be right either way and needs entering by hand.

The page remembers the mode, the picks, your changes and the tree settings in this browser, so a reload brings them back (re-checked against the fresh feed; a node that has left the feed keeps its saved values with a note). "Copy link to these sites" copies a URL with the same choices, for sharing or bookmarking; opening it applies them and tidies the address bar.

For picked nodes that report an antenna azimuth, the result adds an "Antenna aim" row comparing it with the link bearing. It's flagged when the link is outside half the antenna's beamwidth. The server fetches the feeds itself and caches them for 5 minutes; see [deploy/INSTALL.md](deploy/INSTALL.md) to change or turn off the feeds.

### Map

The results include a top-down map (Leaflet, bundled in `web/assets/leaflet/`) showing the Fresnel zone outline, the line of sight, both sites and any obstructions. Tiles come from OpenStreetMap by default; the `map.tile_servers` section of the server config can add or swap in other tile servers, such as an AREDN mesh tileserver. See [deploy/INSTALL.md](deploy/INSTALL.md).

### Elevation sources

By default the server uses the public OpenTopoData API (`ned10m`, then `srtm30m`), with Open-Elevation as a fallback. `/etc/fresnelkml/config.json` can change the chain: add a self-hosted OpenTopoData server by URL, or a folder of SRTM tiles (`local_hgt`) for offline use. See [deploy/config.example.json](deploy/config.example.json) and `tools/fetch_hgt.py`.

## Command line

```bash
python3 fresnel_kml.py --a "TowerA,37.0,-122.0,30" --b "TowerB,37.05,-121.9,25" -o link.kml
python3 fresnel_kml.py --a "TowerA,37°00'00\"N,122°00'00\"W,150asl" --b "TowerB,10S 597811 4100985,180asl"
python3 fresnel_kml.py --csv sites.csv --hgt-dir /var/lib/fresnelkml/hgt --color "#3366ff"
```

A site is `Name,LAT,LON,HEIGHT` or `Name,POSITION,HEIGHT`; the height is above ground unless it ends in `asl`. Run `python3 fresnel_kml.py --help` for every flag. The tool prints a summary (and a table of the stations where clearance is below 60%) and exits with 2 for bad input or 3 when no elevation is available.

## Library

```python
from fresnelkml import Site, Options, analyze, build_kml

a = Site("TowerA", 37.0, -122.0, agl_m=30)
b = Site("TowerB", 37.05, -121.9, asl_m=366.9)
report = analyze(a, b, Options(color="#00ff00"))
print(report.status, report.min_clearance)
kml_text = build_kml(a, b, Options())
```

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install pytest requests pyproj
.venv/bin/python -m pytest
```

The tests never touch the network (elevation sources are mocked or use synthetic tiles), and run with both the pyproj and the spherical geodesy.

## Google Earth Pro acceptance check

Open a generated KML in Google Earth Pro and check that:

1. The two towers stand at the sites, with the yellow line of sight running between their tops.
2. The Fresnel zone is centred on the line of sight, narrows to a point at each tower top, and is semi-transparent in the chosen color.
3. The "Fresnel 60%" folder is off by default and shows the smaller orange zone when ticked.
4. With the terrain check on, the profile line follows the ground and red markers sit where clearance is below 60%.
5. The description balloons show the distance, bearings, r1, clearance, status and elevation sources.

Heights come from a DEM that can differ from Google Earth's terrain by several metres, so tower bases may float slightly or sink into the ground. For exact alignment, enter the ground elevation shown in Google Earth's status bar as the ground override.
