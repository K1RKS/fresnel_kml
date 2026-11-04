# Installing the Fresnel KML web service on NGINX

The service is one self-contained folder: a static page plus a Python CGI script run by fcgiwrap. It needs only `nginx`, `fcgiwrap` and `python3` (3.10 or newer) on the server; no pip packages are needed.

## 1. Build the folder (on any machine with this repository)

```bash
python3 tools/build_site.py
```

This creates `dist/site/` and `dist/fresnel-site.tar.gz`.

## 2. One-time server setup

```bash
sudo apt install nginx fcgiwrap python3
```

fcgiwrap listens on `/run/fcgiwrap.socket` and runs as `www-data`.

Copy the blocks from [nginx-fresnel.conf](nginx-fresnel.conf) into your site's `server { ... }` block (on Debian/Ubuntu, `/etc/nginx/sites-available/default`), then:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

The `location /site/lib/ { deny all; }` block stops NGINX from serving the Python code as files.

## 3. Install or upgrade the folder

```bash
sudo tar xzf fresnel-site.tar.gz -C /var/www/html/
```

That's all an upgrade needs. Nothing has to be restarted, because every API request starts a fresh Python process.

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

Then open `http://SERVER/site/` in a browser.

## 4. Optional: elevation sources

With no config file, the server looks up ground elevation from the public OpenTopoData server, falling back to Open-Elevation. To change that, create `/etc/fresnelkml/config.json` from [config.example.json](config.example.json) and edit the `chain` list. Sources are tried in order, per point.

- `opentopodata` with a `url`: the public server or your own (`http://10.0.0.5:5000/v1`). Set `min_interval_s` to 0 for your own server.
- `local_hgt` with a `path`: a folder of SRTM tiles. Download them once with:

  ```bash
  sudo python3 tools/fetch_hgt.py --bbox 42,-125,49,-116 --out /var/lib/fresnelkml/hgt
  sudo chmod -R a+rX /var/lib/fresnelkml
  ```

  Oregon and Washington together are 63 tiles, about 1.6 GB uncompressed (about 650 MB with `--keep-gz`, but lookups are slower). The download can run on another machine; copy the folder over afterwards.

- For fully offline use, make the chain only the `local_hgt` entry, or only a LAN OpenTopoData entry.
- `cache_dir` caches lookups on disk. Create it and make it writable by `www-data`:

  ```bash
  sudo install -d -o www-data -g www-data /var/cache/fresnelkml
  ```

- A config file in another place can be selected with `fastcgi_param FRESNEL_CONFIG /path/config.json;`.

## 5. Optional: map tile servers

The page's map is drawn with Leaflet, which ships inside the folder (`vendor/leaflet/`, BSD 2-Clause licence). The map tiles come from the tile servers listed under `map` in the same `/etc/fresnelkml/config.json`. Without a `map` section, the page offers OpenStreetMap (the default) and OpenTopoMap. To use your own or an AREDN tileserver, list it, and make it the default if you like:

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

Each entry needs `name` and a `url` containing `{z}`, `{x}` and `{y}`. `{s}` with an optional `subdomains` string, `attribution` (HTML) and `max_zoom` are optional. The browser fetches tiles directly, so a `//host/...` URL follows the page's own http or https, and the page's user must be able to reach that host. `GET api/fresnel.cgi?map` shows what the page will use. The public OpenStreetMap servers are fine for light use under their [tile usage policy](https://operations.osmfoundation.org/policies/tiles/); for heavy use, point this at your own tileserver.

The public OpenTopoData limits (1 request/s, 1000/day) are shared by everyone using your server. The service already spaces requests out across concurrent users, and the commented `limit_req` lines in the NGINX snippet can cap how often each client may call the API.

## Troubleshooting

- `502 Bad Gateway` from NGINX itself (an HTML page, not JSON): fcgiwrap isn't running or the socket path differs. Check `systemctl status fcgiwrap`.
- `403` on the API: the CGI script isn't executable. `sudo chmod 755 /var/www/html/site/api/fresnel.cgi`.
- Python errors go to the NGINX error log (`/var/log/nginx/error.log`).
