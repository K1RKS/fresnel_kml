---
name: Read mesh nodes from meshinfo sites.json
overview: Switch the site picker's node feeds from Nodographer's full nodes.json to the new meshinfo sites.json, addressed by a snapshot base URL. Fetch with ETag/304 and gzip, check the schema version, warn when the mesh data is stale (with the poller's reason when status.json is available), and use the new link fields on the page. Drop support for the old feed shape.
todos:
  - id: config
    content: "config.py: feeds become {name, base}; 'url' is an error that says it was replaced; default base for K1RKS MeshMap; cache_ttl_s default 60; new stale_after_s (default 3600)"
    status: pending
  - id: httpjson
    content: "httpjson.py: get_json_conditional(url, etag, timeout) -> (doc | None on 304, etag); always send Accept-Encoding: gzip; urllib path decompresses and treats 304 as not-modified"
    status: pending
  - id: nodes
    content: "nodes.py: fetch <base>sites.json conditionally; parse_sites() replaces slim_feed/slim_node, rejects unknown schema, drops old-feed tolerance; cache stores etag, snapshot, generated"
    status: pending
  - id: staleness
    content: "nodes.py: warn when generatedAt is older than stale_after_s; then try <base>status.json (404 ignored) and add state/lastError to the warning"
    status: pending
  - id: page
    content: "sites.js: SNR in the link list; a link target with lat/lon but no node can be Site B; picker rows show 'last seen N days ago' for nodes not seen in 7 days"
    status: pending
  - id: tests
    content: "Rewrite tests/test_nodes.py for sites.json (schema, conditional GET, 304 keeps cache, stale warnings, status.json optional); test_httpjson for gzip and 304; config errors for 'url'"
    status: pending
  - id: docs
    content: "README, deploy/INSTALL.md, deploy/config.example.json: nodes.feeds[].base, which Nodographer version is needed, staleness setting"
    status: pending
  - id: deploy
    content: "Check the server configs for nodes.feeds[].url before deploying; deploy to fresnel_kml_test per the deploy rule; main after testing"
    status: pending
isProject: false
---

# Read mesh nodes from meshinfo sites.json

**Depends on** Nodographer's `sites.json` export (`~/ai_projects/nodographer/.cursor/plans/sites_json_export.plan.md`) being live on the public host. Until then, this branch can only be tested against a local `sites.json`.

Fresnel only needs to support the new Nodographer (meshinfo). Today `nodes.py` downloads the full `nodes.json` (548 KB, not gzipped by the public host) every 5 minutes, although the poller only publishes every 15. It also carries code for older feed shapes: no `id`, no `antenna`, `freq: "None"`. After this change Fresnel reads `sites.json` (about 110 KB, 14 KB gzipped), usually gets a 304, and checks a schema number instead of guessing.

## Config (`fresnelkml/config.py`)

```json
"nodes": {
  "feeds": [{"name": "K1RKS MeshMap", "base": "https://meshmap.k1rks.me/meshmap/data/"}],
  "cache_ttl_s": 60,
  "stale_after_s": 3600,
  "timeout_s": 10
}
```

- `base` is the folder that holds the snapshot files, in any of Nodographer's modes: the API (`http://host:8090/api/v1/`), a proxy (`…/meshmap/meshinfo/`), local (`…/meshinfo/current/`) or a legacy rsync copy (`…/meshmap/data/`). It must be http(s). A missing trailing `/` is added.
- The default `base` is whatever the public host ends up serving (Nodographer plan, public-host step). Set it once that step is done.
- An old `url` key raises `ConfigError("nodes.feeds[0].url was replaced by base: the folder that holds Nodographer's sites.json (meshinfo)")`. That's clearer than the generic unknown-key message.
- `cache_ttl_s` defaults to 60, which is cheap now that a refresh is usually a 304.
- `stale_after_s` (new, non-negative; 0 turns the check off): how old a snapshot can be before a warning.

## HTTP (`fresnelkml/httpjson.py`)

- `get_json_conditional(url, etag=None, timeout=10.0) -> tuple[object | None, str | None]` returns `(doc, etag)`. `doc` is None on a 304; `etag` is the response's `ETag`, or the one passed in on a 304.
- Send `If-None-Match` when there's an ETag. Always send `Accept-Encoding: gzip`.
- `requests` path: it already decompresses; treat `status_code == 304` as not-modified.
- `urllib` path: `HTTPError` with code 304 means not-modified. On `Content-Encoding: gzip`, use `gzip.decompress` before `json.loads`.
- `get_json` and `post_form_json` are unchanged.

## Feed loading (`fresnelkml/nodes.py`)

- **Cache file:** same path scheme, keyed by `<base>sites.json`. It holds `{url, fetched, etag, snapshot, generated, nodes}`.
- **Refresh**, when the cache is older than `cache_ttl_s`:
  - Call `get_json_conditional` with the cached ETag.
  - A 304 bumps `fetched` and rewrites the cache.
  - A 200 runs `parse_sites`, then stores the result.
  - A failure keeps today's stale-cache fallback and warning text.
- **`parse_sites(doc) -> (nodes, generated_ms, snapshot)`** replaces `slim_feed`/`slim_node`/`_slim_link`:
  - `doc["schema"]` must be the int 1. Anything else raises `ValueError("unsupported sites.json schema N")`, which becomes a feed warning.
  - Per node: require `id`, `name`, finite `lat`/`lon`. Flatten `antenna` into today's page fields (`height_m`, `azimuth_deg`, `elevation_deg`, `gain_dbi`, `beamwidth_deg`, `antenna`), so `sites.js` field names don't change. Copy `freq_mhz`, `band`, `chanbw_mhz` and `last_seen`.
  - Per link: `id`, `name`, `type: "RF"`, `distance_km`, `bearing_deg`, `signal_dbm`, plus new `noise_dbm`, `lat` and `lon`.
  - Numbers still go through `_num`, as a guard against bad data rather than old shapes.
  - Remove `_short_host`, the `.local.mesh` stripping and the `"None"`/`"Not Available"` blanks; Nodographer does that now.
- **Staleness:**
  - When `now - generated > stale_after_s`, warn "K1RKS MeshMap: mesh data is 3 h old."
  - Only then, fetch `<base>status.json` once, at the cache TTL, best effort with a short timeout. Any error, including the 404 from legacy and local modes, adds nothing.
  - If `state` is `failed`, or `lastError` is set, append "; the last scan failed: <lastError>". If `state` is `scanning`, append "; a scan is in progress".
- **Response:** `feeds[]` entries gain `snapshot`. `generated` stays in ms, so the page code is unchanged. Merging and sorting are unchanged.

## Page (`web/sites.js`)

- **Link list (link selection):** add SNR when both values are known, e.g. `9.8 km, 237°, −64 dBm, SNR 28 dB`.
- **Link target without a node record:** if the link has `lat`/`lon`, list it without "(no location in the feed)". Choosing it fills Site B's name and position from the link and leaves the height empty, with the existing missing-height note. Without `lat`/`lon`, keep today's behaviour.
- **Picker rows:** when `last_seen` is more than 7 days old, add a muted "last seen 12 days ago". Nodographer keeps nodes for up to 30 days, so an operator can tell a node is probably gone.
- The aim check, frequency fill and ASL-height guess stay as they are.

## Tests

`tests/test_nodes.py`, rewritten:
- `parse_sites`:
  - a full schema-1 document;
  - a node missing `lat` is dropped;
  - a link with `lat`/`lon` and no matching node;
  - `schema: 2` and a missing `schema` are rejected;
  - a document without `nodes` is rejected.
- Loading:
  - a fresh cache makes no request;
  - an expired cache sends `If-None-Match` with the stored ETag;
  - a 304 keeps the nodes and bumps `fetched`;
  - a 200 replaces them;
  - a failure with a stale cache warns.
- Staleness:
  - an old `generatedAt` warns;
  - with `status.json` failed, the warning includes `lastError`;
  - a 404 on `status.json` gives the plain warning;
  - fresh data never requests `status.json`.
- Config:
  - `url` gives the replacement message;
  - a non-http `base`;
  - a missing trailing slash is added;
  - a negative `stale_after_s` is an error.
- Kept: merge order and duplicate ids, `?nodes` 200 with `Cache-Control: max-age=60`, feed down still 200.

`tests/test_httpjson.py`: the urllib path with a gzip body, the urllib 304 path, and that `If-None-Match` and `Accept-Encoding` are sent.

## Docs

- `README.md`, the Mesh node picker paragraph: needs Nodographer meshinfo with `sites.json`; `base` examples for the four modes.
- `deploy/INSTALL.md`, "Optional: mesh node feeds": the new config block, `stale_after_s`, and that `status.json` reasons need API or proxy mode.
- `deploy/config.example.json`: `base` instead of `url`.

## Deploy

1. The deployed server configs may still set `nodes.feeds[].url`, which now fails config loading, and with it every API call. Before copying, check the config each site's CGI loads and change `url` to `base`.
2. Build and copy `origin/site_selection_update` to `fresnel_kml_test` exactly as `.cursor/rules/deploy.mdc` says (temporary worktree, dry run first, no `--delete`). Check `api/fresnel.cgi?nodes` returns nodes, and that a second request within the TTL makes no upstream fetch.
3. Merge to `main` and deploy `fresnel_kml` the same way once the test site looks right.

## Out of scope

- Fetching from the browser directly (CORS is open, but the server-side fetch keeps the shared cache, reaches `.local.mesh` bases, and never fetches a URL from a request).
- A link-budget calculation from gain, signal and noise.
- Per-node API lookups; the picker needs the whole list for type-ahead.
