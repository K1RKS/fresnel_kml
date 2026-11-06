# Installing the Fresnel KML page on NGINX

The page is one self-contained folder of static files. The analysis runs in the visitor's browser (Pyodide), so the server does not need Python or fcgiwrap for the page itself. An optional CGI script in the same folder still answers the HTTP API for scripts; that part needs `nginx`, `fcgiwrap` and `python3` (3.10 or newer), and no pip packages.

## 1. Build the folder (on any machine with this repository)

```bash
python3 tools/build_site.py
```

The first run downloads the pinned Pyodide runtime (about 15 MB) into `web/pyodide/`. This creates `dist/site/` and `dist/fresnel-site.tar.gz`.

## 2. One-time server setup

For the page alone:

```bash
sudo apt install nginx
```

Copy the folder into the web root (step 3) and open `http://SERVER/site/`. Nginx must serve `lib/fresnelkml/` and `pyodide/` as static files; the browser loads the Python package and the WebAssembly runtime from there. `.wasm` should be `application/wasm` (the default in Nginx 1.21 and later).

The optional CGI API needs fcgiwrap as well:

```bash
sudo apt install fcgiwrap python3
```

fcgiwrap listens on `/run/fcgiwrap.socket` and runs as `www-data`.

Copy the blocks from [nginx-fresnel.conf](nginx-fresnel.conf) into your site's `server { ... }` block (on Debian/Ubuntu, `/etc/nginx/sites-available/default`), then:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

`lib/` must stay reachable: the page fetches the Python package from there and runs it in Pyodide. The `fastcgi_param NO_BUFFERING 1;` line matters only for the optional CGI API. It makes Debian/Ubuntu's fcgiwrap pass that API's progress lines on as they're written.

Recommended: create the lookup cache folder, which turns on caching of elevation and lidar lookups (see [section 7](#7-optional-local-cache)):

```bash
sudo install -d -o www-data -g www-data /var/cache/fresnelkml
```

## 3. Install or upgrade the folder

```bash
sudo tar xzf fresnel-site.tar.gz -C /var/www/html/
```

That's all an upgrade of the page needs. Nothing has to be restarted. The optional CGI API also needs no restart, because every request starts a fresh Python process.

To deploy from another machine with rsync instead (here into a site at `/fresnel_kml/`; build with `--name fresnel_kml`):

```bash
python3 tools/build_site.py --name fresnel_kml --no-tarball
rsync -a --delete --exclude __pycache__ --chmod=Dug+rwx,Do+rx,Fug+rw,Fo+r \
  dist/fresnel_kml/ SERVER:/var/www/html/SITE/fresnel_kml/
```

Use `+` permission modes as shown. A fixed mode such as `F664` removes the execute bit from `api/fresnel.cgi`, and the API then returns 403. `__pycache__` is excluded because the web server creates it as `www-data`, so the deploying user may not be able to delete it.

Check it:

```bash
curl -s http://localhost/site/api/fresnel.cgi?sources
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"site_a":{"name":"A","lat":37,"lon":-122,"asl_m":150},"site_b":{"name":"B","lat":37.05,"lon":-121.9,"asl_m":180}}' \
  http://localhost/site/api/fresnel.cgi -o test.kml
```

Then open `http://SERVER/site/` in a browser. The first load fetches the WebAssembly runtime (about 15 MB) from this folder.

The page reads the `config.json` that was built into the folder, not `/etc/fresnelkml/config.json`. Edit that copy and reload to change what the page uses. `/etc/fresnelkml/config.json` applies to the command line and to the optional CGI API.

## 4. Optional: elevation sources

With no config file, the CGI API and the command line look up ground elevation from the USGS 3DEP image service, then the public OpenTopoData server, falling back to Open-Elevation. The page's shipped `config.json` adds Open-Meteo before Open-Elevation, because the public OpenTopoData API does not allow browser requests. To change the CGI API, create `/etc/fresnelkml/config.json` from [config.example.json](config.example.json) and edit the `chain` list. Sources are tried in order, per point. `open-meteo` is a valid chain type (Open-Meteo's elevation endpoint, global, about 90 m, and it allows browser requests).

- `arcgis_image` with a `url`: an ArcGIS image service sampled with `getSamples`, such as USGS 3DEP. `units` is `m`, `ft` or `ftUS`; `retries` defaults to 0 and `timeout_s` should allow for slow responses (3DEP can take 10 to 20 s for a long path).
- `opentopodata` with a `url`: the public server or your own (`http://10.0.0.5:5000/v1`). Set `min_interval_s` to 0 for your own server.
- `local_hgt` with a `path`: a folder of SRTM tiles. Download them once with:

  ```bash
  sudo python3 tools/fetch_hgt.py --bbox 42,-125,49,-116 --out /var/lib/fresnelkml/hgt
  sudo chmod -R a+rX /var/lib/fresnelkml
  ```

  Oregon and Washington together are 63 tiles, about 1.6 GB uncompressed (about 650 MB with `--keep-gz`, but lookups are slower). The download can run on another machine; copy the folder over afterwards.

- For fully offline use, make the chain only the `local_hgt` entry, or only a LAN OpenTopoData entry.
- Lookups are cached on disk when `/var/cache/fresnelkml` exists; see [section 7](#7-optional-local-cache).
- A config file in another place can be selected with `fastcgi_param FRESNEL_CONFIG /path/config.json;`.

### Lidar surface sources

The page's "Along the path: LIDAR" choice samples highest-hit surface models (tree tops and roofs). Without a `surface` section the server uses Oregon DOGAMI's public service. To add or replace sources:

```json
"surface": {
  "sources": [
    {"name": "dogami", "label": "Oregon DOGAMI lidar",
     "url": "https://gis.dogami.oregon.gov/arcgis/rest/services/lidar/DIGITAL_SURFACE_MODEL_MOSAIC/ImageServer",
     "units": "ftUS", "bbox": [-124.8, 41.9, -116.4, 46.4]}
  ],
  "spacing_m": 3, "max_points": 25000, "threads": 6, "timeout_s": 20, "time_budget_s": 30
}
```

- Each source is an ArcGIS image service of a highest-hit (first-return) surface, in heights above sea level, with `getSamples` enabled and no login. `units` is `m`, `ft` or `ftUS`. `label` is shown to users. `bbox` (`[west, south, east, north]` in degrees) skips the source for points outside it.
- Sources are tried in order; a later one only fills points an earlier one has no data for.
- `spacing_m` is the distance between samples along the path, `max_points` caps the samples per request (long paths get a wider spacing), and lidar fetching stops after `time_budget_s` or 40 s from the start of the request, whichever is first, so the request finishes well inside the 60 s `fastcgi_read_timeout`.
- `"sources": []` turns LIDAR off; the page then greys it out.

## 5. Optional: map tile servers

The page's map is drawn with Leaflet, which ships inside the folder (`assets/leaflet/`, BSD 2-Clause licence). If your NGINX config denies paths such as `/assets/`, allow them for this folder, or the map won't load. The map tiles come from the tile servers listed under `map` in the same `/etc/fresnelkml/config.json`. Without a `map` section, the page offers OpenStreetMap (the default), OpenTopoMap, and two satellite views: Satellite (Esri), worldwide and sharp to zoom 19 (Esri's World Imagery; fine for light, non-commercial use with the attribution shown, see [Esri's terms](https://www.esri.com/en-us/legal/terms/full-master-agreement)), and Satellite (USGS), public-domain US imagery that goes to zoom 16 and is stretched beyond that (`max_native_zoom`). To use your own or an AREDN tileserver, list it, and make it the default if you like:

```json
"map": {
  "tile_servers": [
    {"name": "AREDN K1RKS", "url": "//k1rks-tileserver-world.local.mesh/hot/{z}/{x}/{y}.png",
     "attribution": "&copy; OpenStreetMap contributors", "max_zoom": 19},
    {"name": "OpenStreetMap", "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
     "attribution": "&copy; OpenStreetMap contributors", "max_zoom": 19}
  ],
  "default": "AREDN K1RKS"
}
```

Each entry needs `name` and a `url` containing `{z}`, `{x}` and `{y}` (in any order; ArcGIS tile services use `{z}/{y}/{x}`). `{s}` with an optional `subdomains` string, `attribution` (HTML), `max_zoom`, `max_native_zoom` (the deepest zoom the server has; the map stretches its tiles past it) and `cache_mb` are optional. The browser fetches tiles directly, so a `//host/...` URL follows the page's own http or https, and the page's user must be able to reach that host. `GET api/fresnel.cgi?map` shows what the page will use. The public OpenStreetMap servers are fine for light use under their [tile usage policy](https://operations.osmfoundation.org/policies/tiles/); for heavy use, point this at your own tileserver.

The public OpenTopoData limits (1 request/s, 1000/day) are shared by everyone using your server. The service already spaces requests out across concurrent users, and the commented `limit_req` lines in the NGINX snippet can cap how often each client may call the API.

## 6. Optional: mesh node feeds

The page's "Mesh node" picker fills Site A and Site B from [Nodographer](https://github.com/K1RKS/nodographer) `nodes.json` feeds. The page's feeds are the `nodes` section of the site's `config.json`. Without a `nodes` section the CGI API uses the K1RKS MeshMap feed. To use other feeds, or several, list them:

```json
"nodes": {
  "feeds": [
    {"name": "K1RKS MeshMap", "url": "https://meshmap.k1rks.me/meshmap/data/nodes.json"},
    {"name": "Mesh poller", "url": "http://my-poller.local.mesh/meshmap/data/nodes.json"}
  ],
  "cache_ttl_s": 300,
  "timeout_s": 10
}
```

- The page fetches each feed from the browser, so the feed's host must send `Access-Control-Allow-Origin` (or the page must be served from the same origin). The K1RKS MeshMap feed does not send that header today; add `add_header Access-Control-Allow-Origin *;` on its `nodes.json` location, or host this site on `meshmap.k1rks.me`. Without it the remote list stays empty. **Load a nodes.json file** on the page reads a Nodographer file the visitor already has (up to 32 MB) and needs no CORS header; that copy wins for any node id it shares with a remote feed, and it is not stored on the server. The optional CGI API still fetches feeds on the server, so a `*.local.mesh` feed works there when the server is on the mesh and the feed needs no CORS header. Only the URLs in the config are ever fetched. `POST api/fresnel.cgi?nodes=FILENAME` accepts the same file.
- When the same node appears in more than one feed, the first feed in the list wins.
- Each feed is cached for `cache_ttl_s` seconds (default 300) in the cache folder (`cache.dir`), or in the system temp directory when there isn't one. If a feed can't be fetched, the last cached copy is used and the page shows a warning.
- `timeout_s` defaults to `elevation.timeout_s`.
- `"feeds": []` turns the picker off.

`GET api/fresnel.cgi?nodes` shows what the page will get.

Antenna heights and frequencies only come from feeds generated by Nodographer `330032d` or later. Older feeds still fill the name and position, and the height is left for you to enter.

## 7. Optional: local cache

The server can keep what it looks up from elevation and lidar services, so a link checked before comes back much faster. Each service has its own SQLite file in `<cache folder>/points/` and its own size limit (1 GB by default); the least recently used points go first. The README's [Local cache](../README.md#local-cache) section explains it in full.

1. Turn it on by creating the default folder (no restart needed):

   ```bash
   sudo install -d -o www-data -g www-data /var/cache/fresnelkml
   ```

2. Optionally, change the folder or the sizes in `/etc/fresnelkml/config.json`:

   ```json
   "cache": {
     "dir": "/var/cache/fresnelkml",
     "max_mb": 1024,
     "types": {"opentopodata": 256},
     "services": {"3dep": 2048, "dogami": 4096}
   }
   ```

   A service's limit is its `services` entry (by source name), else its `types` entry (lidar sources are type `surface`), else `max_mb`; `0` turns caching off for it, and `"dir": null` turns it all off. The old `elevation.cache_dir` setting still works; the `elevation.sqlite` file it used to hold is no longer read and can be deleted.

3. Check it, as the web server user so the files keep their owner:

   ```bash
   sudo -u www-data python3 tools/cache_admin.py stats --config /etc/fresnelkml/config.json
   ```

   `prune` applies the limits now and `clear [--service NAME]` empties a cache.

### Map tiles

Map tiles can be cached by NGINX. Give each tile server to cache a `"cache_mb"` in `map.tile_servers` (see [section 5](#5-optional-map-tile-servers)); the page then loads its tiles through `<site>/tiles/<slug>/`. Print the matching NGINX settings for your site path and add them:

```bash
python3 tools/tile_cache_conf.py --config /etc/fresnelkml/config.json --site /site/
```

The first part (`proxy_cache_path` lines, one zone and size limit per tile server) goes in the `http { }` block, for example in `/etc/nginx/conf.d/fresnelkml-tiles.conf`; the `location` blocks go in the site's `server { }` block. Then `sudo nginx -t && sudo systemctl reload nginx`. NGINX creates the cache folders under `/var/cache/nginx/fresnelkml/` itself. A repeated tile request should show `X-Cache-Status: HIT`. Don't cache a tile server that only the browser can reach, such as a `*.local.mesh` tileserver when the web server is off the mesh.

## Troubleshooting

- `502 Bad Gateway` from NGINX itself (an HTML page, not JSON): fcgiwrap isn't running or the socket path differs. Check `systemctl status fcgiwrap`.
- `403` on the API: the CGI script isn't executable. `sudo chmod 755 /var/www/html/site/api/fresnel.cgi`.
- Python errors go to the NGINX error log (`/var/log/nginx/error.log`).
