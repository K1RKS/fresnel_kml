---
name: Nodographer site picker
overview: Let the web page fill Site A and Site B from Nodographer's nodes.json feed. The API fetches and caches the configured feed(s) server-side and serves a slim node list at GET ?nodes; the page adds a type-ahead "Mesh node" picker to each site that fills name, position, antenna height (AGL) and frequency, suggests Site A's RF neighbours for Site B, and checks antenna aim against the link bearing in the result.
todos:
  - id: config
    content: "config.py: add a `nodes` section (feeds list of {name, url}, cache_ttl_s, timeout_s) with validation, defaults and a public_nodes() view"
    status: completed
  - id: nodes-module
    content: "New fresnelkml/nodes.py: fetch each feed, slim Nodographer JSON to the picker fields, merge feeds, file cache with TTL and stale fallback"
    status: completed
  - id: api
    content: "webapi.py: GET ?nodes returns {feeds, nodes, warnings}; never 5xx for an unreachable feed; add to USAGE"
    status: completed
  - id: ui-picker
    content: "index.html/app.js/style.css: per-site Mesh node type-ahead (keyboard navigable), fill rules, RF-neighbour suggestions for the other site, hidden when no feeds"
    status: completed
  - id: ui-aim
    content: "app.js: Antenna aim summary row comparing picked nodes' azimuth with the link bearings, flagged when outside half the beamwidth"
    status: completed
  - id: tests
    content: "tests/test_nodes.py and test_api additions: slimming (new and old feed shapes), merge, cache/stale, config errors, ?nodes endpoint with mocked fetch"
    status: completed
  - id: docs
    content: "README, deploy/INSTALL.md, deploy/config.example.json: document the nodes feed config, ?nodes endpoint and the picker"
    status: completed
  - id: verify
    content: Run pytest, then dev_server against a sample feed file and the live feed; check picking, unit conversion, neighbours and aim row in a browser
    status: completed
isProject: false
---

# Nodographer site picker

Nodographer's `nodes.json` (`https://meshmap.k1rks.me/meshmap/data/nodes.json`) now carries what Fresnel needs per node: `id`, `node` (name), `lat`, `lon`, an `antenna` block (`height_m` AGL, `azimuth_deg`, `elevation_deg`, `gain_dbi`, `beamwidth_deg`, `description`), numeric `meshrf.freq` in MHz, and `links[]` with `id`, `linkType`, `distance_km`, `bearing_deg`, `signal_dbm`. That came from the Nodographer change `330032d` (plan `~/.cursor/plans/fresnel_antenna_feed_eb3b0185.plan.md`).

**Prerequisite outside this repo:** the live feed only gets the new fields once the poller on `meshinfo` (`/svr/meshmap`) pulls `330032d` and restarts. Until then the live feed has no `antenna` block and `freq` is the string `"None"`. The picker must still work in that case: name and position fill, the height is left alone.

## Design decisions

- **Server-side fetch, not browser fetch.** The page calls `GET api/fresnel.cgi?nodes`; the CGI fetches the configured feed URLs. This avoids CORS on the meshmap host, lets a server on the mesh use `*.local.mesh` feeds, and lets the slim list be cached for every user. Only URLs from the server config are fetched, never URLs from the request, so there is no open proxy.
- **Slim list.** The full feed carries services, MACs, load averages and so on. The endpoint returns only picker fields (below), which keeps the page download small.
- **Cache.** Every API request is a fresh Python process, so the cache is a file: `<cache_dir>/nodes-<sha1(url)[:12]>.json`, or the system temp dir when `cache_dir` is unset. TTL defaults to 300 s, matching the 5-minute sync to the public host. On a fetch failure, a stale cache is served with a warning.
- **The API request format does not change.** The picker only fills the existing form fields; `POST` still takes `site_a`/`site_b` as today. Antenna aim is a page-side check (see below).

## Config (`fresnelkml/config.py`)

New optional top-level section, mirrored in `DEFAULT_CONFIG`:

```json
"nodes": {
  "feeds": [
    {"name": "K1RKS MeshMap", "url": "https://meshmap.k1rks.me/meshmap/data/nodes.json"}
  ],
  "cache_ttl_s": 300,
  "timeout_s": 10
}
```

- `feeds`: list of `{name, url}`; names unique; `url` must be `http://` or `https://`. An empty list disables the picker. The built-in default is the K1RKS feed (like the public tile-server defaults).
- `cache_ttl_s`: non-negative number. `timeout_s`: positive number, default `elevation.timeout_s`.
- `Config` gains `node_feeds`, `nodes_cache_ttl_s`, `nodes_timeout_s`; the cache directory reuses `elevation.cache_dir`.
- Validation follows `_validate_tile_server` / `_parse_map`: unknown keys and bad types raise `ConfigError` with the dotted path (`nodes.feeds[0].url ...`).

## Feed module (`fresnelkml/nodes.py`, new)

- `slim_node(data: dict, feed: str) -> dict | None`: from one `nodeInfo[i]["data"]`. Returns `None` when `lat`/`lon` are missing, zero or not finite.
  ```json
  {"id": "k1rks-tower", "name": "K1RKS-Tower", "lat": 45.36683, "lon": -122.76617, "feed": "K1RKS MeshMap",
   "height_m": 25.0, "azimuth_deg": 96.0, "elevation_deg": 0.0, "gain_dbi": 25.0, "beamwidth_deg": 8.0,
   "antenna": "Ubiquiti RD-5G30", "freq_mhz": 5805.0, "band": "5GHz",
   "links": [{"id": "parrett-sector", "type": "RF", "distance_km": 9.8, "bearing_deg": 237.4, "signal_dbm": -64}]}
  ```
  - `name`: `node` with a trailing `.local.mesh` removed. `id`: the feed's `id`, else the lowercase name (older feeds have no `id`).
  - Antenna fields come from `antenna` when present, else `null`. Numbers go through a `_num` helper (rejects `None`, `""`, `"None"`, `"Not Available"`, non-finite). `freq_mhz` of `0` becomes `null`.
  - `links`: keep only RF entries (`linkType` `RF`) with an `id` or `hostname`; `type` from `linkType`. Tunnel and DTD links can't be a Fresnel path, and dropping them halves the live response (about 104 KB for 315 nodes).
- `load_nodes(config, env=None, now=time.time) -> tuple[list[dict], list[dict], list[str]]`: returns `(nodes, feeds, warnings)`. For each feed: use the cache when younger than the TTL, else fetch with `httpjson.get_json` and rewrite the cache atomically (temp file + `os.replace`). On fetch failure, use the stale cache with a warning such as "K1RKS MeshMap: feed unreachable, showing data from 14 min ago", or skip the feed with a warning when there is no cache. A response without a `nodeInfo` list counts as a failure.
- Merging: feeds in config order; a duplicate `id` keeps the first feed's node. Sort by `name`, case-insensitive.
- `feeds` entries in the response: `{"name", "generated": <feed "date" in ms or null>, "count", "age_s"}`. The feed URL is not echoed, only the name.

## API (`fresnelkml/webapi.py`)

- `GET ?nodes` loads the config (config errors return 500, as `?map` does), then returns `200 {"feeds": [...], "nodes": [...], "warnings": [...]}`. Unreachable feeds never turn into a 5xx; the page just gets fewer nodes and the warnings.
- Response header `Cache-Control: max-age=60` instead of `no-store`, so a browser reload doesn't refetch immediately.
- Add `"GET ?nodes": "list mesh nodes from the configured Nodographer feeds"` to `USAGE["endpoints"]`.

## Page (`web/index.html`, `web/app.js`, `web/style.css`)

**Markup:** at the top of each site fieldset, before Name:

```html
<div class="node-pick" hidden>
  <label>Mesh node <input name="node_search" placeholder="Type a node name&hellip;" autocomplete="off"
         role="combobox" aria-autocomplete="list" aria-expanded="false"></label>
  <ul class="node-list" role="listbox" hidden></ul>
</div>
```

**Loading:** `loadNodes()` runs from `init()` alongside `loadSources()` and `loadMapConfig()`. If the request fails or returns no nodes, the pickers stay hidden. Feed warnings go into the existing `#lookup-hint` area as a second line.

**Type-ahead:**
- Filters by case-insensitive substring on `name`, up to 20 matches, prefix matches first.
- Each row shows the name, plus `Ht 25 m · 5805 MHz` when known.
- Arrow keys, Enter and Escape work, and so does a mouse click. The list closes on blur.
- The matching part of each name is bold.
- Inline completion: the rest of the first name that starts with the typed text is added to the box, selected, so typing continues over it. Tab or Enter accepts it. Arrow keys move the completion to the highlighted row. Backspace doesn't re-add it. Escape and blur drop it.
- When the other site holds a picked node, its RF neighbours come first under a heading "RF links of <name>", with `distance` (shown with `dfmt`) and bearing. They show even while the box is empty, on focus.

**Fill rules (`fillSite(fs, node)`):**
- Name ← `node.name`, then fire the existing `input` event so the tree labels update.
- Switch to the latitude/longitude mode if the single-position box is showing. Latitude and longitude ← values with 7 decimals. Clear `.decoded`.
- When `height_m` is known: height ← `height_m`, converted to feet when `#units` is `ft` (`/ 0.3048`, one decimal); height reference ← `agl`; ground override cleared; `updateLookupHint()`. When it's unknown, leave the height and show "No antenna height in the feed; enter it." in `.decoded`.
- When `freq_mhz` is known: `#freq_ghz` ← `freq_mhz / 1000` rounded to 4 decimals. If the other picked site has a different known frequency, show "The two nodes report different frequencies (5805 vs 5745 MHz)." in the hint and keep the latest pick.
- Remember the pick in `fs.dataset.nodeId`. A manual edit to latitude, longitude or position (or switching the position mode) clears it. Renaming the site keeps it. A manual height edit keeps the pick but stops the height refill, so manual edits are never overwritten.
- When `#units` changes and a site still holds an unedited pick, refill its height in the new unit. Manual heights keep today's behaviour: the number stays and only the label changes.

**Antenna aim (`ui-aim`):** in `showResult`, for each site that still holds a pick with a known `azimuth_deg`:
- `off = |((azimuth - bearing_to_other + 540) % 360) - 180|`, using `bearing_ab` for Site A and `bearing_ba` for Site B.
- Add a summary row "Antenna aim" with one line per site: "K1RKS-Tower aims 96°, link 101.3° (5.3° off, beamwidth 8°)".
- Mark the line `MARGINAL` when `beamwidth_deg` is known and below 360 and `off > beamwidth / 2`; omnis (beamwidth 360 or unknown) only show the numbers.
- Elevation tilt is out of scope.

**Style:** `.node-list` is an absolutely positioned dropdown under the input, with max height, scroll, an active-row highlight and a muted group heading. It uses the same colours as `.decoded`.

## Tests (no network; the autouse fixture already blocks it)

`tests/test_nodes.py`:
- `slim_node` with the new feed shape, with an old feed (no `antenna`, no `id`, `freq: "None"`), with `freq: 0`, with `.local.mesh` names, and with `lat: 0` dropped.
- Merging two feeds with a duplicate `id` keeps the first; sorting by name.
- Cache: a fresh cache is used without fetching; an expired cache refetches (fake `now`); a fetch failure with a stale cache returns its nodes plus a warning; failure with no cache returns a warning and no nodes; a body without `nodeInfo` is a failure.
- Config: defaults, `feeds: []`, and errors for duplicate names, a non-http URL, unknown keys and a negative TTL.

`tests/test_api.py`:
- `GET ?nodes` with `httpjson.get_json` monkeypatched to a sample feed: 200, the slim shape, and `Cache-Control: max-age=60`.
- `GET ?nodes` with the fetch raising `HttpError`: 200, empty `nodes`, one warning.
- `USAGE` lists `?nodes`.

## Docs

- `README.md`: one paragraph under Web service on the Mesh node picker, plus a `GET ?nodes` bullet in the API section.
- `deploy/INSTALL.md`: a new "Optional: mesh node feeds" section with the `nodes` config block, the cache file location (`cache_dir` or the temp dir, writable by `www-data`), and how to disable it (`"feeds": []`).
- `deploy/config.example.json`: add the `nodes` section.

## Verify

1. `.venv/bin/python -m pytest`.
2. `python3 tools/dev_server.py` with `FRESNEL_CONFIG` pointing at a config whose feed is a local sample `nodes.json` served by `python3 -m http.server`, covering the new feed shape. Then check in a browser:
   - pick Site A, then see its RF links offered for Site B;
   - heights in both ft and m, and the units switch;
   - frequency fill and the mismatch hint;
   - a manual edit stops the refill;
   - the Antenna aim row and its MARGINAL flag.
3. Point the feed at the live `https://meshmap.k1rks.me/meshmap/data/nodes.json`. Before the poller update, names and positions fill and heights don't; after it, heights and frequencies fill too.

## Out of scope / later

- Sending antenna azimuth, beamwidth and gain in the `POST` body so the KML balloon and CLI report can show aim and a link budget.
- Picking nodes by clicking the Leaflet map.
- Reading `nodes.kml` or `nodes.csv` feeds.
