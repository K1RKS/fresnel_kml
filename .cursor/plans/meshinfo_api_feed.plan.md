---
name: Read mesh nodes from the meshinfo API
overview: Fresnel's server reads Nodographer's meshinfo API directly and is the only thing users reach (apart from map tiles). Address each feed by a snapshot base URL, fetch nodes.json with ETag/304 and gzip, warn when the mesh data is stale (with the poller's reason from status.json), keep the cache somewhere persistent, let browsers revalidate ?nodes, and use the new link positions on the page. Drop support for the old feed shape.
todos:
  - id: config
    content: "config.py: feeds become {name, base}; 'url' is an error that says it was replaced; cache_ttl_s default 60; new stale_after_s (default 3600)"
    status: completed
  - id: httpjson
    content: "httpjson.py: get_json_conditional(url, etag, timeout) -> (doc | None on 304, etag); always send Accept-Encoding: gzip; urllib path decompresses and treats 304 as not-modified"
    status: completed
  - id: nodes
    content: "nodes.py: fetch <base>nodes.json conditionally; require the meshinfo shape (generator Nodographer, id, antenna block) and drop old-feed tolerance; keep RF links with noise and far-end lat/lon; cache stores etag and generated"
    status: completed
  - id: staleness
    content: "nodes.py: warn when the feed's date is older than stale_after_s; then read <base>status.json (best effort) and add the scan state and lastError"
    status: completed
  - id: nodes-etag
    content: "webapi.py: ?nodes sends an ETag hashed from the nodes, warnings and feed scan times; If-None-Match from the CGI environment (strong or NGINX's weak form) returns 304"
    status: completed
  - id: page
    content: "sites.js: SNR in the link list; a link target with lat/lon but no node can be Site B; picker rows show 'last seen N days ago' for nodes not seen in 7 days"
    status: completed
  - id: tests
    content: "Rewrite tests/test_nodes.py (meshinfo shape, conditional GET, 304 keeps cache, staleness and status.json); test_httpjson for gzip and 304; test_api for the ?nodes ETag and 304; config errors for 'url'"
    status: completed
  - id: docs
    content: "README, deploy/INSTALL.md, deploy/config.example.json: nodes.feeds[].base pointing at the meshinfo API, the persistent cache folder, stale_after_s"
    status: completed
  - id: deploy
    content: "Check internal-nginx reaches the meshinfo API; change the server configs from url to base and set cache.dir; deploy to fresnel_kml_test per the deploy rule; main after testing"
    status: pending
isProject: false
---

# Read mesh nodes from the meshinfo API

**Assumptions:**
- Users only reach the Fresnel page. Fresnel's server fetches, caches and serves everything they need, except map tiles, which browsers load from the tile servers (or through the NGINX tile cache).
- Fresnel's server can reach the meshinfo API it's configured for, so the public meshmap host's setup (proxy or rsync) doesn't matter here.
- Only the new Nodographer (meshinfo) is supported.

**Nodographer side:** `~/ai_projects/nodographer/.cursor/plans/nodes_json_for_fresnel.plan.md` adds `lat`/`lon` to links in `nodes.json`. Fresnel only uses them when present, so this plan doesn't wait for it.

Today `nodes.py` downloads the full `nodes.json` (548 KB, not gzipped by the public host) every 5 minutes, although the poller only publishes every 15. It also carries code for older feed shapes: no `id`, no `antenna`, `freq: "None"`. After this change it asks the API with `If-None-Match` and gzip. That is usually a 304, and about 60 KB when a new scan has been published.

## Config (`fresnelkml/config.py`)

```json
"nodes": {
  "feeds": [{"name": "K1RKS MeshMap", "base": "http://<meshinfo-host>:8090/api/v1/"}],
  "cache_ttl_s": 60,
  "stale_after_s": 3600,
  "timeout_s": 10
}
```

- **`base`** is the folder that holds the snapshot files. Normally that's the meshinfo API (`http://host:8090/api/v1/` or `http://host.local.mesh:8090/api/v1/`). Nodographer's other layouts also work for `nodes.json`: a proxy path, local `meshinfo/current/`, or a legacy `data/` copy. Only the API has `status.json`.
  - It must be http(s); a missing trailing `/` is added.
  - The built-in default is the public copy `https://meshmap.k1rks.me/meshmap/data/`, so a fresh install still works. Real deployments set their own API base.
- **`url`** (the old key) raises `ConfigError("nodes.feeds[0].url was replaced by base: the meshinfo API (or folder) that holds Nodographer's nodes.json")`.
- **`cache_ttl_s`** defaults to 60, which is cheap now that a refresh is usually a 304.
- **`stale_after_s`** (new, non-negative; 0 turns the check off): how old a snapshot can be before a warning.

## HTTP (`fresnelkml/httpjson.py`)

- `get_json_conditional(url, etag=None, timeout=10.0) -> tuple[object | None, str | None]` returns `(doc, etag)`. `doc` is None on a 304; `etag` is the response's `ETag`, or the one passed in on a 304.
- Send `If-None-Match` when there's an ETag. Always send `Accept-Encoding: gzip`.
- `requests` path: it already decompresses; treat `status_code == 304` as not-modified.
- `urllib` path: `HTTPError` with code 304 means not-modified. On `Content-Encoding: gzip`, use `gzip.decompress` before `json.loads`.
- `get_json` and `post_form_json` are unchanged.

## Feed loading (`fresnelkml/nodes.py`)

- **Cache file:** same path scheme, keyed by `<base>nodes.json`, in `cache.dir`. It holds `{url, fetched, etag, generated, nodes}`.
- **Refresh**, when the cache is older than `cache_ttl_s`:
  - Call `get_json_conditional` with the cached ETag.
  - A 304 bumps `fetched` and rewrites the cache.
  - A 200 parses, then stores the result.
  - A failure keeps today's stale-cache fallback and warning. The last good data is served however old it is.
- **Parsing:** `slim_feed`/`slim_node` require the meshinfo shape and drop the old-feed tolerance.
  - The document must have `generator == "Nodographer"` and a `nodeInfo` list. Otherwise `ValueError("not a Nodographer meshinfo nodes.json")`, which becomes a feed warning.
  - **Per node:** `id`, `node` and finite `lat`/`lon` are required; nodes without them are skipped. A missing `antenna` block leaves the antenna fields null. Flatten it into the page fields `height_m`, `azimuth_deg`, `elevation_deg`, `gain_dbi`, `beamwidth_deg` and `antenna`, so `sites.js` names don't change. Also copy `freq_mhz` (from `meshrf.freq`), `chanbw_mhz` (from `meshrf.chanbw` via `_num`), `band` and `last_seen`.
  - **Per RF link:** `id`, `name`, `type`, `distance_km`, `bearing_deg`, `signal_dbm`, plus `noise_dbm`, and `lat`/`lon` when Nodographer sends them.
  - Remove the `.local.mesh` stripping, the `"None"`/`"Not Available"` blanks and the `id` fallback. `_num` stays, as a guard against bad values.
- **Staleness:**
  - When `now - date > stale_after_s`, warn "K1RKS MeshMap: mesh data is 3 h old."
  - Only then, read `<base>status.json` once, at the cache TTL, best effort with a short timeout; any error adds nothing.
  - If `state` is `failed`, or `lastError` is set, append "; the last scan failed: <lastError>". If `state` is `scanning`, append "; a scan is in progress".
- **Response:** unchanged shape. Merging and sorting are unchanged.

## `?nodes` revalidation (`fresnelkml/webapi.py`)

- **ETag:** the first 16 hex characters of the sha1 of the nodes, the warnings and each feed's `(name, generated, count)`, quoted. `age_s` is left out because it changes every second. Hashing the content rather than combining upstream ETags also works for feeds whose server sends no ETag.
- **304:** `_nodes` reads `HTTP_IF_NONE_MATCH` from `env` (the CGI environment). NGINX turns the ETag weak (`W/"..."`) when it gzips, so the `W/` prefix is ignored; `*` and lists match too. On a match it returns `Response(304, b"", [Cache-Control, ETag])`. 304 is added to `Response.reason`.
- Keep `Cache-Control: max-age=60`. After a minute the browser revalidates and usually gets an empty 304, instead of the full list (about 100 KB, gzipped by NGINX).

## Page (`web/sites.js`)

- **Link list (link selection):** add SNR when both values are known, e.g. `9.8 km, 237°, −64 dBm, SNR 28 dB`.
- **Link target without a node record:** if the link has `lat`/`lon`, list it without "(no location in the feed)". Choosing it fills Site B's name and position from the link and leaves the height empty, with the existing missing-height note. Without `lat`/`lon`, keep today's behaviour.
- **Picker rows:** when `last_seen` is more than 7 days old, add a muted "last seen 12 days ago". Nodographer keeps nodes for up to 30 days, so an operator can tell a node is probably gone.
- The aim check, frequency fill and ASL-height guess stay as they are.

## Tests

`tests/test_nodes.py`, rewritten:
- Parsing:
  - a meshinfo document (antenna, chanbw, last_seen, RF link with noise and lat/lon);
  - a link without lat/lon;
  - DTD and TUN links dropped;
  - a node without `id` or position skipped;
  - a non-Nodographer document rejected.
- Loading:
  - a fresh cache makes no request;
  - an expired cache sends `If-None-Match` with the stored ETag;
  - a 304 keeps the nodes and bumps `fetched`;
  - a 200 replaces them;
  - a failure with an old cache still serves it, with a warning.
- Staleness:
  - an old `date` warns;
  - with `status.json` failed, the warning includes `lastError`;
  - a 404 on `status.json` gives the plain warning;
  - fresh data never requests `status.json`.
- Config:
  - `url` gives the replacement message;
  - a non-http `base`;
  - a missing trailing slash is added;
  - a negative `stale_after_s` is an error.
- Kept: merge order and duplicate ids, feed down still 200.

`tests/test_httpjson.py`: the urllib path with a gzip body, the urllib 304 path, and that `If-None-Match` and `Accept-Encoding` are sent.

`tests/test_api.py`:
- `?nodes` has an `ETag` and `Cache-Control: max-age=60`;
- the same request with `HTTP_IF_NONE_MATCH` set to it returns 304 with an empty body;
- a changed feed ETag gives a different `?nodes` ETag.

## Docs

- `README.md`, Mesh node picker: needs Nodographer meshinfo. Fresnel's server reads the API and users never contact it. Give a `base` example.
- `deploy/INSTALL.md`, "Optional: mesh node feeds":
  - the new config block and `stale_after_s`;
  - that `status.json` reasons need the API;
  - that the node cache lives in `cache.dir`. Recommend `/var/cache/fresnelkml`, so a reboot during a meshinfo outage doesn't leave the picker empty.
- `deploy/config.example.json`: `base` instead of `url`.

## Deploy

1. From `internal-nginx`: `curl -s http://<meshinfo-host>:8090/api/v1/status.json` returns JSON.
2. In the config each site's CGI loads:
   - change `nodes.feeds[].url` to `base` with the API address. A leftover `url` now fails config loading, and with it every API call.
   - Make sure `cache.dir` is set and writable by `www-data`.
3. Build and copy `origin/site_selection_update` to `fresnel_kml_test` exactly as `.cursor/rules/deploy.mdc` says (temporary worktree, dry run first, no `--delete`). Then check:
   - `api/fresnel.cgi?nodes` returns nodes;
   - a repeat with `If-None-Match` returns 304;
   - the cache file's `etag` is set.
4. Merge to `main` and deploy `fresnel_kml` the same way once the test site looks right.

## Out of scope

- A separate `sites.json` from Nodographer. `nodes.json` from the API is small enough once gzipped and revalidated.
- How `meshmap.k1rks.me` gets its data (proxy or rsync). That only affects the public map.
- Fetching from the browser directly. Users only reach Fresnel.
- A link-budget calculation from gain, signal and noise.
