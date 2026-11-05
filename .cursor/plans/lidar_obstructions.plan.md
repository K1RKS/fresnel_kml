---
name: Lidar ground and obstructions
overview: Use public lidar to replace guesswork in the terrain check. The bare-earth ground profile comes from USGS 3DEP (about 1 to 3 m, nationwide) ahead of OpenTopoData. The "Trees along the path" checkbox becomes a radio choice of None, Trees (fixed height) or LIDAR; LIDAR samples DOGAMI's highest-hit surface (trees and buildings, 3 ft cells, Oregon) across the 60% Fresnel zone, falling back to the fixed tree height where there is no lidar. Lidar surface is drawn in blue and fixed-height trees in green.
todos:
  - id: arcgis-client
    content: "New fresnelkml/arcgis.py: ArcGIS ImageServer getSamples client (multipoint POST, WGS84 in, chunks of at most 1000 with count checks, ft/ftUS/m units, NoData and 400 handling, thread pool, per-request timeout and overall time budget, raster-id to project-name lookup)"
    status: completed
  - id: ground-3dep
    content: "elevation.py/config.py: new `arcgis_image` provider type; default chain becomes 3DEP ImageServer, then OpenTopoData, then Open-Elevation"
    status: completed
  - id: surface-config
    content: "config.py: new `surface` section (sources list with url, units, optional WGS84 bbox, label; spacing_m, max_points, threads, time_budget_s); default DOGAMI DSM; public view for ?sources"
    status: completed
  - id: options
    content: "parsing/analysis Options: tree_source (fixed|lidar, default fixed) and lidar_width (fraction of r1, default 0.6, 0 to 1); validation; tree_height_m becomes the fallback height in lidar mode"
    status: completed
  - id: analysis
    content: "analysis.py: build lidar sample grid (centre plus ±w/2 and ±w times r1, every spacing_m between the near-site exclusions), fetch, lateral clearance, fixed-height fallback for uncovered samples, coverage %, worst point, status, report fields"
    status: completed
  - id: kml
    content: "kml.py: lidar obstruction placemarks and description lines (source projects, coverage, worst point with canopy/building height); blue style for lidar, green for fixed trees"
    status: completed
  - id: web
    content: "index.html/app.js/style.css/sites.js: replace the Trees along the path checkbox with a radio group (None / Trees / LIDAR) that enables the fields each choice uses, plus a Lidar width input; blue lidar fill and green fixed fill in the profile, map markers, result rows; saved state and link params (old t=1 links still mean Trees)"
    status: completed
  - id: tests
    content: "Tests with mocked HTTP: chunking and truncated responses, units, NoData/400, budget timeout, provider in chain, lateral clearance maths, fallback and coverage, option validation, API round trip"
    status: completed
  - id: docs-deploy
    content: "README, deploy/INSTALL.md, config.example.json: lidar sources, coverage, attribution, enabling cache_dir; verify against real paths on the dev server, then deploy to fresnel_kml_test"
    status: completed
isProject: false
---

# Lidar ground and obstructions

## What the services give us (checked 2026-11-04)

- **DOGAMI (Oregon Lidar Consortium)**, `https://gis.dogami.oregon.gov/arcgis/rest/services/lidar/`, public, no key:
  - `DIGITAL_SURFACE_MODEL_MOSAIC` (highest hit: tree tops, roofs), `DIGITAL_TERRAIN_MODEL_MOSAIC` (bare earth), `CANOPY_HEIGHT_MODEL_MOSAIC` (DSM minus DTM). All F32, 3 ft cells, Oregon Lambert (EPSG 6557), NAVD88 heights in feet. The `_HS` services are hillshade images and are not used.
  - `getSamples` with an `esriGeometryMultipoint` POST in WGS84 returns one value per point. **Hard cap of 1000 points per request; extra points are silently dropped**, so chunks must be at most 1000 and the returned count checked. About 1 to 2 s per 1000 points when warm; one cold request took 27 s.
  - A batch that crosses the state line still answers (coverage reaches into Vancouver, WA); points with no lidar come back as NoData. A request entirely outside coverage returns HTTP 400.
  - Each sample carries a `rasterId`, but the catalog `query` returns no features for those ids, so the report names the configured source label ("Oregon DOGAMI lidar") instead of per-project names.
  - Sanity check at AH6LE-CHEHALEM: DTM 919.9 ft, OpenTopoData ned10m 919.2 ft. Along Chehalem to K7ATV-ROCK the canopy reaches 99 ft.
- **USGS 3DEP bare earth**, `https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer`, nationwide, metres, 1/9 arc-second (about 3 m) where lidar exists. Same `getSamples` multipoint call, 1000 points in about 13 s. Chehalem: 280.30 m (ned10m 280.17 m). Bare earth only; 3DEP has no surface model service (DSM would need PDAL and the raw point clouds, out of scope).
- **Washington DNR** lidar image services require a token (`gis.dnr.wa.gov/image/rest/services/Lidar` answers 499). Some counties publish their own DSMs (for example Thurston County). Not in the defaults; the `surface` config can add any public ArcGIS DSM service later.

## Design decisions

- **One generic ArcGIS client** (`fresnelkml/arcgis.py`) serves both uses: ground (3DEP) and surface (DOGAMI). Pure stdlib (`urllib`, `json`, `concurrent.futures`), no new dependencies, so the CGI stays pip-free. Add a `post_form_json` helper to `httpjson.py`.
- **Ground**: new elevation provider type `arcgis_image` with `batch: 1000` and a `units` setting. Default chain becomes `3dep` (arcgis_image) -> `opentopodata` -> `open-elevation`; failures fall through as today. Existing `/etc/fresnelkml/config.json` files keep their own chain. The ground lookup line on the page and `?ground` pick this up automatically.
- **Surface, not canopy**: use the highest-hit DSM directly as the obstruction top (trees, buildings, anything standing). Using absolute DSM heights avoids interpolating a coarse ground under dense samples. Height above ground for display = DSM minus the interpolated ground profile (approximate, labelled "about").
- **Sampling grid**: from `tree_clearance_a_m` to `distance - tree_clearance_b_m` (the near-site exclusions still apply, so the site's own roof or mast is ignored), every `spacing_m` (default 3 m). At each station, five lateral offsets perpendicular to the path: 0, ±w/2, ±w times r1 at that station, where `w = lidar_width` (default 0.6, the 60% zone; `0` means centreline only). If the grid exceeds `max_points` (default 25,000), the spacing grows to fit and the report says so.
- **Lateral clearance**: for a sample at lateral offset y with surface top z and line of sight L, the distance from the zone axis is `sqrt(y^2 + max(0, L - z)^2)`, clearance = that / r1. On the centreline this reduces to today's `(L - z) / r1` (kept signed, so tops above the line of sight are negative). The reported worst point is the minimum over all samples.
- **Coverage fallback** (your choice): in LIDAR mode, samples with NoData or a failed batch use the fixed tree height on the ground profile instead (`effective + tree_height_m`). The report gives lidar coverage as a percentage of the checked path and names the uncovered stretches when they're long.
- **Time budget**: the CGI has `fastcgi_read_timeout 60s`. Lidar requests run on a small thread pool (default 6) with a per-request timeout and an overall budget (default 30 s, and never past about 45 s from the start of the request). Whatever isn't fetched in time falls back to the fixed height with a warning. Enable `elevation.cache_dir` on the server so repeat runs of the same link come from the sqlite cache (deterministic sample points make cache hits likely).
- **Colours** (your choice): lidar surface fill in **blue**, fixed-height trees in **green**, in the profile plot, the legend, and KML. Obstruction markers stay red because they are problems to fix.
- **API options**: `tree_source: "fixed" | "lidar"` (default `"fixed"`), `lidar_width` (0 to 1, default 0.6). `trees` must still be true. Report JSON gains `surface: {source, projects, coverage, spacing_m, width, min_clearance, min_clearance_d_m, min_clearance_m, top_m, height_m, status}` and per-sample markers for the plot (thinned to the max over each plot bin so the response stays small).
- **Web UI**: the "Trees along the path" checkbox becomes a radio group, "Along the path:" **None** / **Trees** / **LIDAR** (default None, as today's unchecked box).
  - None: the tree fields are disabled and no obstruction check runs (`trees: false`).
  - Trees: tree height and the two "No trees within" distances are enabled (`trees: true, tree_source: "fixed"`), exactly today's behaviour.
  - LIDAR: the same fields plus "Lidar width (x r1)" (default 0.6) are enabled; the tree height is labelled "Fallback tree height" since it's only used where there's no lidar (`trees: true, tree_source: "lidar"`).
  - The radio choice and values are saved in localStorage and copy-link params: `t=1` (Trees, so existing links keep working) or `t=lidar`, plus `th`, `ta`, `tb` and `tw` (lidar width). Saved states from before this change (`trees.on`) map to None or Trees.
  - Result rows: "Min clearance over lidar surface" with badge, coverage and projects in LIDAR mode; the existing "Min clearance over trees" row in Trees mode.
- **API stays compatible**: `trees` (bool) keeps its meaning and the new `tree_source` picks fixed or lidar, so existing API callers and the CLI's `--trees` are unaffected.

## Files

- `fresnelkml/httpjson.py`: `post_form_json(url, form, timeout)`.
- `fresnelkml/arcgis.py` (new): `ImageService(url, units)`, `sample(points) -> list[float | None]` with chunking, count checks, retries, budget; `project_names(raster_ids)`.
- `fresnelkml/elevation.py`: `ArcGISImageProvider` (`type = "arcgis_image"`).
- `fresnelkml/config.py`: `arcgis_image` in `SOURCE_TYPES` and `make_provider`; 3DEP first in `DEFAULT_CONFIG`; new `surface` section with validation and `public_surface()`.
- `fresnelkml/parsing.py`, `fresnelkml/analysis.py`: new options, `lidar_grid()`, `lateral_clearance()`, surface results on `LinkReport`.
- `fresnelkml/kml.py`: blue lidar style and placemarks, description lines.
- `web/index.html`, `web/app.js`, `web/sites.js`, `web/style.css`: controls, colours, rows, saved state.
- `tests/test_arcgis.py` (new), additions to `test_trees.py`, `test_analysis.py`, `test_api.py`, `test_config` tests.
- `README.md`, `deploy/INSTALL.md`, `deploy/config.example.json`.

## Verification

1. `pytest` with all network mocked.
2. Dev server against the real services: Chehalem to K7ATV-ROCK (short, wooded), a 30 to 45 km Oregon path (spacing growth, timing), a path crossing into Washington (partial coverage and fallback), a path entirely outside Oregon (no lidar, fixed fallback only).
3. Check the plot colours, the result rows, the KML in Google Earth Pro, and total request time under the 60 s limit.
4. Deploy to `fresnel_kml_test` and repeat one path there.

## Later (not in this plan)

- Washington and county DSM sources once public endpoints are confirmed.
- 3DEP point clouds via PDAL for a nationwide surface model.
- Showing the lidar flight year per project (from the DOGAMI project status layers).
