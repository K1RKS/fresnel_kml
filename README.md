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
- **Options** (all optional): `freq_ghz`, `zone`, `sixty`, `k`, `clutter_m`, `elev_source`, `stations`, `ring_points`, `style` (`mesh`/`rings`), `ring_every`, `terrain_check` (`true`/`false`/`null` = only when a lookup is needed anyway), `mode` (`absolute`/`relative`), `units` (`m`/`ft`), `color` (`random`, `#RRGGBB`, `#RRGGBBAA` or a name), `coord_display` (`decimal`/`dms`/`ddm`), `trees` (`true` to check obstructions above the ground), `tree_source` (`fixed` or `lidar`), `lidar_width` (0 to 1, default 0.6), `tree_height_m`, `tree_clearance_a_m` and `tree_clearance_b_m` (or `tree_clearance_m` for both). See [Trees and lidar](#trees-and-lidar).
- **Response**: the KML file as a download, or `{"kml", "report", "warnings"}` with `"format": "json"`. Errors are JSON `{"error", "field"}` with status 400, 405, 413, 502 (no elevation available) or 504.
- **Progress** (what the web page uses): add `"progress": true` (with `"format": "json"`) and the response is `application/x-ndjson`, one JSON object per line as the work happens: `{"progress": {"stage", "done", "total"}}` for the stages `start`, `ground`, `lidar` (samples done of total) and `report`, then `{"result": {"kml", "report", "warnings"}}` or `{"error", "field", "status"}`. The response carries `X-Accel-Buffering: no` so NGINX passes each line straight through.
- `GET ?sources` lists the configured elevation sources and lidar surface sources (`surface`); `GET ?parse=TEXT[&axis=lat|lon]` decodes a coordinate.
- `GET ?ground&lat=LAT&lon=LON` (or `&position=TEXT`, plus an optional `&source=NAME`) looks up the ground elevation at one point and returns `{"lat", "lon", "ground_m", "source", "assumed_zero"}`.
- `GET ?nodes` returns `{"feeds", "nodes", "warnings"}`: the mesh nodes from the configured Nodographer feeds, with name, position, antenna height (metres above ground), azimuth, beamwidth, gain, frequency (MHz) and RF links (tunnel and DTD links are left out). An unreachable feed is a warning, not an error.

### Mesh node picker

When the server has a Nodographer feed configured (by default the [K1RKS MeshMap](https://meshmap.k1rks.me/meshmap/) `nodes.json`), two tabs appear above the sites:

- **Node selection:** each site gets a "Mesh node" box. As you type, matching nodes are listed with the match in bold, and the first name that starts with what you typed is completed in the box; Tab or Enter accepts it, and the arrow keys or the mouse pick another. Once one site is picked, the other site's list starts with that node's RF links, nearest first. You can still type a site by hand without picking a node.
- **Link selection:** Site A must be a mesh node, and Site B is chosen from a "Link from" list of Site A's RF links (nearest first, with distance, bearing and signal). Choosing a link fills Site B and sets the frequency from Site A. A link to a node the feed has no location for is marked "(no location in the feed)"; choosing it fills Site B's name and the frequency, and you enter its position and antenna height.

A picked node fills the name, position, antenna height above ground and the link frequency. These are defaults: a field you change is highlighted in amber, its tooltip shows the node's value, and the site's note lists what you changed. Clearing a changed field puts the node's value back. Emptying the Mesh node box drops the pick and keeps the values as plain manual entry. Switching the height units converts picked heights, overrides included.

AREDN's antenna height is meant to be above ground, but some nodes have their height above sea level entered there. When a picked node's height is over 200 ft (61 m), picking the node switches "Height is" to "above sea level", highlighted as a change; set it back to "above ground" if the tower really is that tall. If a node's height still works out to more than 100 ft (30.5 m) above the ground, the results warn that it may be incorrect unless the antenna is on a high tower. If taking it as above sea level puts the antenna underground, the results say the node's height can't be right either way and needs entering by hand.

Under each site's heights, "Ground here (lookup)" shows what the elevation lookup gives for that position (using the chosen elevation source), and for a height above sea level, how far the antenna is above or below that ground. It's for reference only: the "Ground elevation" box still overrides the lookup for a height above ground.

The page remembers the mode, the picks, your changes and the "Along the path" settings in this browser, so a reload brings them back (re-checked against the fresh feed; a node that has left the feed keeps its saved values with a note). "Copy link to these sites" copies a URL with the same choices, for sharing or bookmarking; opening it applies them and tidies the address bar.

For picked nodes that report an antenna azimuth, the result adds an "Antenna aim" row comparing it with the link bearing. It's flagged when the link is outside half the antenna's beamwidth. The server fetches the feeds itself and caches them for 5 minutes; see [deploy/INSTALL.md](deploy/INSTALL.md) to change or turn off the feeds.

### Map

The results include a top-down map (Leaflet, bundled in `web/assets/leaflet/`) showing the Fresnel zone outline, the line of sight, both sites and any obstructions. Tiles come from OpenStreetMap by default; the `map.tile_servers` section of the server config can add or swap in other tile servers, such as an AREDN mesh tileserver. Tiles can be cached on your server; see [Local cache](#local-cache) and [deploy/INSTALL.md](deploy/INSTALL.md).

### Elevation sources

By default the server uses the USGS 3DEP bare-earth image service (lidar-derived where flown, about 3 m resolution, US only), then the public OpenTopoData API (`ned10m`, then `srtm30m`), with Open-Elevation as a last fallback. 3DEP can take 10 to 20 s for a long path; points it can't answer fall through to the next source. `/etc/fresnelkml/config.json` can change the chain: add a self-hosted OpenTopoData server by URL, or a folder of SRTM tiles (`local_hgt`) for offline use. See [deploy/config.example.json](deploy/config.example.json) and `tools/fetch_hgt.py`.

### Trees and lidar

"Along the path" on the page (option `trees`, with `tree_source`) checks clearance over things standing on the ground, as well as the terrain. Both modes ignore a stretch next to each site ("No trees within", default 50 ft), so a site's own roof or mast doesn't count.

- **None**: terrain only.
- **Trees**: a fixed tree height (default 100 ft) everywhere along the path. Drawn in green.
- **LIDAR**: the highest-hit lidar surface (tree tops, roofs and anything else standing), sampled every 3 m along the path and across the zone: on the line of sight and at ±half and ±all of the lidar width, a fraction of r1 (default 0.6, the 60% zone; 0 checks the line of sight only). Something beside the line of sight counts by its distance from the zone's axis. Where no lidar covers the path, the fixed tree height is used instead and the report says what share of the path had lidar. Lidar is drawn in blue, fixed-height trees in green. Long paths are sampled more coarsely (at most 25,000 points), and lidar that doesn't arrive within the time budget falls back to the fixed height with a warning.

The default lidar source is the Oregon DOGAMI highest-hit surface (3 ft cells, all of Oregon that's been flown, plus some of the Washington side of the Columbia). Other public ArcGIS image services can be added in the `surface` section of the server config; see [deploy/config.example.json](deploy/config.example.json). Washington DNR's lidar services need a login, so they aren't included. When Trees or LIDAR is chosen, the terrain check runs even in "auto" mode.

### Local cache

The server can keep a local copy of everything it looks up from outside services, so a link that has been checked before comes back in a fraction of the time and the public services see fewer requests:

- **Ground elevations** from each elevation source (USGS 3DEP, OpenTopoData, Open-Elevation, USGS point query, Google).
- **Lidar surface samples** from each lidar source (Oregon DOGAMI by default).
- **Map tiles** from each tile server you choose (OpenStreetMap, OpenTopoMap), cached by NGINX.

Each service has its own cache with its own size limit, 1 GB by default. When a cache is full, the entries used least recently are removed first.

#### Turning it on

The cache is on whenever `/var/cache/fresnelkml` exists and the web server can write to it. On a Debian or Ubuntu server, create it once:

```bash
sudo install -d -o www-data -g www-data /var/cache/fresnelkml
```

Without that folder, or with `"dir": null` in the config, nothing is cached and everything works as before. Elevation and lidar caching need no restart; map tile caching needs the NGINX setup below.

#### Sizes and other settings

Add a `cache` section to `/etc/fresnelkml/config.json` to change the folder or the sizes:

```json
"cache": {
  "dir": "/var/cache/fresnelkml",
  "max_mb": 1024,
  "types": {"opentopodata": 256},
  "services": {"3dep": 2048, "dogami": 4096}
}
```

- `dir`: where the cache files go. Leave it out to use `/var/cache/fresnelkml` when it's writable; set it to `null` to turn caching off.
- `max_mb`: the size limit for each service, in MB (default 1024).
- `types`: a limit for every source of one type, such as `opentopodata` or `arcgis_image`. Lidar sources have the type `surface`.
- `services`: a limit for one source, by the `name` in `elevation.chain` or `surface.sources`.
- A service's limit is its entry under `services`, else its type under `types`, else `max_mb`. A limit of `0` means that service isn't cached.
- The older setting `elevation.cache_dir` still works and means the same as `cache.dir`.

As a guide, 1 GB holds about 25 to 30 million points: roughly 1,000 long lidar links (25,000 samples each) or 50,000 ground profiles.

#### How it's stored

Each service has one SQLite file, `points/<service>.sqlite` in the cache folder, for example `points/3dep.sqlite` and `points/surface-dogami.sqlite`. Python's built-in `sqlite3` is all it needs, so the server still needs no extra packages. Reads and writes stay fast as the cache grows:

- Each point is stored as one compact row, keyed by its position rounded to about 10 cm and packed into a single integer, so a lookup is one B-tree search.
- A whole path is read with a few batched queries (500 points at a time) and written in one transaction, so even a 25,000-sample lidar link takes only tens of milliseconds to read or save.
- The files use SQLite's write-ahead log, so many requests can read at once while one writes.
- When a file passes its limit, the oldest-used points are deleted down to 90% of the limit and the file is trimmed.
- If a cache file can't be used (locked for too long, damaged, disk full, no permission), the lookup goes to the outside service as usual and the problem is logged; a request never fails because of the cache.

The `elevation.sqlite` file used by earlier versions is no longer read and can be deleted.

#### Map tiles (NGINX)

Map tiles are loaded by the browser, so they're cached by NGINX on the way through, using a separate cache zone, and so a separate size limit, for each tile server. To cache a tile server, give it a `cache_mb` in the config:

```json
"map": {
  "tile_servers": [
    {"name": "OpenStreetMap", "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
     "attribution": "&copy; OpenStreetMap contributors", "max_zoom": 19, "cache_mb": 1024}
  ]
}
```

The page then loads that server's tiles from `tiles/<slug>/...` under the site instead of directly, where the slug is the name in lower case with spaces and punctuation turned into dashes (`tiles/openstreetmap/`, `tiles/aredn-k1rks/`). Print the matching NGINX settings for your site path, add them to the NGINX config, and reload:

```bash
python3 tools/tile_cache_conf.py --config /etc/fresnelkml/config.json --site /fresnel_kml/
sudo nginx -t && sudo systemctl reload nginx
```

The output has two parts: `proxy_cache_path` lines for the `http { }` block (one zone per cached tile server, with its `max_size`) and `location` blocks for your `server { }` block. Tiles are kept for 7 days, old copies are still served if the tile server is down, and requests carry a proper `User-Agent` as the [OpenStreetMap tile usage policy](https://operations.osmfoundation.org/policies/tiles/) asks. Leave `cache_mb` off for a tile server that only the browser can reach, such as an AREDN `*.local.mesh` tileserver when the web server isn't on the mesh.

#### Checking and maintenance

```bash
python3 tools/cache_admin.py stats                     # points, size, limit and last use for each service
python3 tools/cache_admin.py prune                     # apply the size limits now
python3 tools/cache_admin.py clear --service dogami    # empty one service's cache (or all, without --service)
curl -sI https://HOST/SITE/tiles/openstreetmap/10/163/357.png | grep X-Cache-Status   # HIT once cached
```

Run the admin tool as the web server user (`sudo -u www-data ...`) so the files keep the right owner. On the command line, `--cache-dir DIR` uses a cache folder and `--no-cache` turns caching off for that run.

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
