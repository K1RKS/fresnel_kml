# Functional Spec: 5.8 GHz Fresnel Zone KML Generator for Google Earth Pro

## 0. Purpose
Build a tool that takes two radio sites, each given as **Site name, GPS (lat, lon WGS‑84 decimal degrees), Height above ground (AGL, m)**, and writes a `.kml` file. When opened in Google Earth Pro, it shows the two towers, the line of sight (LOS) and the first Fresnel zone (plus an optional 60% zone) as a 3D semi‑transparent ellipsoid. It can optionally flag where terrain enters the zone.

Language: **Python 3.10+**, standard library XML generation (`xml.etree.ElementTree` or f-strings). `simplekml` is optional. Only third-party dependency: `requests` for elevation lookups. Optional: `pyproj` (Geod) for Vincenty/Karney geodesics.

---
## 1. Fresnel zone math
Sources: https://en.wikipedia.org/wiki/Fresnel_zone , ITU-R P.530 (https://www.itu.int/rec/R-REC-P.530), Cisco link-budget notes.

- Wavelength: `λ = c / f`, with c = 299 792 458 m/s and f = 5.8e9 Hz, so **λ = 0.0516884 m**. Frequency must be a parameter (default 5.8 GHz). Allow 5.15–5.95 GHz and other bands.
- Radius of the nth zone at a point that is d1 from site A and d2 from site B (metres):
  `r_n = sqrt( n · λ · d1 · d2 / (d1 + d2) )`
- Maximum at the midpoint (d1 = d2 = D/2): `r_max = 0.5 · sqrt(n · λ · D)`.
- Handy form: `r1[m] = 17.32 · sqrt( d1[km]·d2[km] / (f[GHz]·D[km]) )`.
- **60% rule:** keep at least 0.6·r1 clear of obstructions (terrain, trees, buildings). Clear to 0.6·r1 or more = "clear". Some LOS but < 60% = "marginal". LOS blocked = "blocked".
- **Earth bulge (optional, default ON):** `h_bulge(d1) = d1·d2 / (2·k·R)` with R = 6 371 000 m and k = 4/3 (standard refraction; make it configurable, e.g. 2/3 for a worst case). Add the bulge to terrain height (or subtract it from the LOS) only in the obstruction check. The rendered geometry stays geometric.

### Worked example (f = 5.8 GHz, n = 1)
| Link D | r1 at midpoint | 0.6·r1 | Earth bulge at midpoint (k=4/3) |
|---|---|---|---|
| 1 km | 3.595 m | 2.157 m | 0.015 m |
| 10 km | **11.368 m** | 6.821 m | 1.472 m |
| 50 km | 25.419 m | 15.251 m | 36.788 m |

Calculation for 10 km: r1 = sqrt(0.0516884 × 5000 × 5000 / 10000) = sqrt(129.22) = 11.37 m.

---
## 2. AGL → ASL (absolute) and Google Earth altitude modes
### 2.1 Ground elevation sources (pluggable `ElevationProvider` interface)
| Provider | Endpoint | Key | Data / accuracy | Limits |
|---|---|---|---|---|
| **OpenTopoData** (default) | `https://api.opentopodata.org/v1/{dataset}?locations=lat,lon|lat,lon` (docs: https://www.opentopodata.org/api/) | none | `srtm30m` (~30 m grid, ~±16 m abs vertical spec, typical ±5–10 m), `aster30m`, `ned10m` (US, 10 m, best in US), `mapzen` | Public: max 100 locations/request, 1 req/s, 1000/day (https://www.opentopodata.org/#public-api). Can self-host. Supports `samples=N` along a path. |
| USGS EPQS | `https://epqs.nationalmap.gov/v1/json?x=LON&y=LAT&units=Meters&wkid=4326` (https://apps.nationalmap.gov/epqs/) | none | 3DEP 1 m–10 m, US only, NAVD88 | One point per request. Throttle it. |
| Open-Elevation | `https://api.open-elevation.com/api/v1/lookup?locations=lat,lon|...` (https://open-elevation.com/) | none | SRTM 250 m–90 m. Coarser. | The public instance is often slow or down. Use only as a fallback. |
| Google Elevation API | `https://maps.googleapis.com/maps/api/elevation/json?locations=...&key=KEY` (https://developers.google.com/maps/documentation/elevation/overview) | **API key + billing** | Mixed sources, closest to Google Earth terrain | Max 512 locations/request. Paid per request. Supports `path=` + `samples=`. |
| Manual | CLI `--ground-a/--ground-b` | n/a | user-surveyed | Overrides any lookup |

Default chain: manual → OpenTopoData `ned10m` (if the point is in the US) → `srtm30m` → Open-Elevation. Record which source was used in the KML `<description>`. Heights are treated as orthometric (≈ MSL). This matches Google Earth "absolute" (sea level), which is close enough at this scale.

### 2.2 KML altitudeMode (https://developers.google.com/kml/documentation/altitudemode)
- `clampToGround` (default if omitted): ignores altitude. Use it for the ground track and the terrain-profile marks.
- `relativeToGround`: altitude above the terrain directly below *each vertex* in Google Earth's own terrain.
- `absolute`: altitude above sea level. Needed for the LOS and the Fresnel zone, because they are straight lines in space and must not follow terrain.

### 2.3 Terrain-mismatch handling (decision)
Google Earth's terrain differs from any DEM by a few metres (sometimes 10–20 m in rough terrain). Rules:
1. Site ASL = `ground_dem + AGL`. Compute LOS and Fresnel geometry in **absolute** mode from these values.
2. Draw tower Placemarks (extruded LineString from ground to antenna) in **absolute** mode with the same ASL, so the tower top meets the LOS exactly. Accept that the tower base may float above or sink below GE terrain slightly.
3. `--mode relative` option: endpoints use `relativeToGround` = AGL. The tool also prints a warning that the zone is still absolute.
4. Optional `--ground-a/--ground-b` lets the user enter the elevation shown in Google Earth's status bar. This is the best way to align the model with GE.
5. Put the DEM ground elevation, AGL and ASL for each site into the description balloon.

---
## 3. Geometry
- Distance/bearing: haversine on R = 6 371 008.8 m is acceptable (error < 0.5%). Use `pyproj.Geod(ellps="WGS84").inv/fwd` (Karney) when available. Sources: https://www.movable-type.co.uk/scripts/latlong.html , https://www.movable-type.co.uk/scripts/latlong-vincenty.html
- Path sampling: N+1 stations at fractions t_i = i/N (default N = 64; also ensure spacing ≤ D/16 and ≤ 250 m for the terrain check, capped at 512). Station position comes from the great-circle intermediate-point formula (movable-type "intermediate point") or Geod.fwd(az, t·D).
- LOS altitude: `z_los(t) = (1−t)·ASL_A + t·ASL_B` (linear in slant; along-path distance ≈ slant distance for the usual tower heights, error negligible). d1 = t·D, d2 = (1−t)·D.
- Cross-section ring at station i: radius r = r_n(d1,d2). Use M points (default 24) at angle φ_j = 2πj/M. Local frame: forward = path bearing θ; right unit vector (east, north) = (cos θ, −sin θ) [bearing measured clockwise from north, forward = (sin θ, cos θ)].
  - horizontal offset h = r·cos φ → east = h·cos θ, north = −h·sin θ
  - vertical offset v = r·sin φ → alt = z_los + v
  - convert the offset to lat/lon: `Δlat = north / M_lat`, `Δlon = east / (M_lon)`, where M_lat ≈ 111 132.954 − 559.822 cos 2φ + 1.175 cos 4φ m/deg, M_lon ≈ (π/180)·6 378 137·cos φ/ sqrt(1−e²sin²φ). (Simpler: R·π/180 and R·cos(lat)·π/180.) Offsets are ≤ tens of metres, so a flat approximation is fine.
- Endpoints (t=0,1) have r=0, so they collapse to a single point (cone tip). Generate the triangles there instead of degenerate quads.
- Elevation of the zone bottom: `z_los − r`. The terrain clearance test uses it (see 4.5).

---
## 4. KML output
Namespace: `http://www.opengis.net/kml/2.2` + `gx`. Document structure:
```
Document (name "Fresnel <A> – <B> 5.8 GHz")
 ├ Styles
 ├ Folder "Sites"      : Point Placemarks + extruded tower LineStrings
 ├ Folder "Line of sight" : LOS LineString (absolute, yellow, width 3)
 ├ Folder "Fresnel zone 1 (100%)" : mesh polygons (+ optional ring lines)
 ├ Folder "Fresnel 60%" (optional, default ON, hidden by default <visibility>0)
 └ Folder "Terrain check" (optional): profile line + obstruction Placemarks
```
- **Colors are `aabbggrr` hex** (https://developers.google.com/kml/documentation/kmlreference#colorstyle). Defaults: zone fill `5900ff00` (green ~35% opacity); 60% zone `6600a5ff` (orange); LOS `ff00ffff` (yellow); obstruction `ff0000ff` (red).
- **Fresnel mesh (recommended renderer):** KML has no ellipsoid primitive. Emit one `<Polygon>` per quad between ring i and ring i+1 (vertices j, j+1). Each Polygon gets `<altitudeMode>absolute</altitudeMode>` and a closed LinearRing of 5 coordinates. Group all quads into a single `<MultiGeometry>` in one Placemark so the user can toggle it as a whole. Size = N·M polygons (64×24 = 1536, which renders fine). Set `PolyStyle <outline>0</outline>` to avoid wireframe clutter. Option `--style rings` emits ring LineStrings only (lighter, wireframe) every k stations.
- Do **not** use `<extrude>` or `<tessellate>` on mesh polygons.
- Placemark description: frequency, λ, distance, bearing A→B and B→A, max r1, 0.6·r1, min clearance, status, elevation source.

### 4.5 Terrain/obstruction check (optional `--terrain-check`, default ON when online)
1. Fetch ground elevation at all path stations (OpenTopoData `samples=` or batch ≤100 points).
2. Effective terrain `g'(t) = g(t) + h_bulge(t) + clutter` (clutter default 0 m, `--clutter 10` for trees).
3. Clearance ratio `c(t) = (z_los(t) − g'(t)) / r1(t)` (skip ends where r1 < 0.5 m).
4. Status: min c ≥ 0.6 → CLEAR; 0 ≤ min c < 0.6 → MARGINAL; min c < 0 → BLOCKED.
5. KML: clampToGround profile LineString, plus a red Placemark at each station where c < 0.6 (absolute altitude g'(t)) with the clearance in its name. Print a summary table to stdout.

### Sample KML snippet
```xml
<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">
<Document>
  <name>Fresnel TowerA – TowerB 5.8 GHz</name>
  <Style id="zone"><LineStyle><color>00000000</color></LineStyle>
    <PolyStyle><color>5900ff00</color><outline>0</outline></PolyStyle></Style>
  <Style id="los"><LineStyle><color>ff00ffff</color><width>3</width></LineStyle></Style>
  <Style id="tower"><LineStyle><color>ff0000ff</color><width>4</width></LineStyle></Style>
  <Placemark><name>TowerA</name>
    <description>Ground 120.0 m (srtm30m), AGL 30 m, ASL 150.0 m</description>
    <Point><altitudeMode>absolute</altitudeMode><coordinates>-122.000000,37.000000,150.0</coordinates></Point>
  </Placemark>
  <Placemark><name>TowerA mast</name><styleUrl>#tower</styleUrl>
    <LineString><altitudeMode>absolute</altitudeMode>
      <coordinates>-122.000000,37.000000,120.0 -122.000000,37.000000,150.0</coordinates></LineString>
  </Placemark>
  <Placemark><name>Line of sight</name><styleUrl>#los</styleUrl>
    <LineString><altitudeMode>absolute</altitudeMode>
      <coordinates>-122.000000,37.000000,150.0 -121.900000,37.050000,180.0</coordinates></LineString>
  </Placemark>
  <Placemark><name>Fresnel zone 1</name><styleUrl>#zone</styleUrl>
    <MultiGeometry>
      <Polygon><altitudeMode>absolute</altitudeMode><outerBoundaryIs><LinearRing><coordinates>
        lon1,lat1,alt1 lon2,lat2,alt2 lon3,lat3,alt3 lon4,lat4,alt4 lon1,lat1,alt1
      </coordinates></LinearRing></outerBoundaryIs></Polygon>
      <!-- ... one Polygon per quad ... -->
    </MultiGeometry>
  </Placemark>
</Document>
</kml>
```
Coordinates are always `lon,lat,alt` with 7 decimals for lat/lon and 2 for altitude.

---
## 5. Interface, validation, edge cases, tests
### CLI
```
fresnel_kml.py --a "TowerA,37.000000,-122.000000,30" --b "TowerB,37.050000,-121.900000,25"
  [--freq-ghz 5.8] [--zone 1] [--sixty/--no-sixty] [--k 1.3333] [--clutter 0]
  [--elev-provider opentopodata|usgs|open-elevation|google|none] [--dataset srtm30m]
  [--google-key KEY | env GOOGLE_ELEVATION_KEY] [--ground-a M --ground-b M]
  [--stations 64] [--ring-points 24] [--style mesh|rings] [--terrain-check/--no-terrain-check]
  [--mode absolute|relative] [--units m|ft] -o link.kml
```
Also accept a CSV with columns `name,lat,lon,agl_m` (2 rows). Library API: `build_kml(site_a, site_b, options) -> str` and `analyze(site_a, site_b, options) -> LinkReport`. `Site` is a dataclass with `name, lat, lon, agl_m, ground_m=None`.

### Validation
- lat ∈ [−90, 90], lon ∈ [−180, 180]. Also accept DMS (`37°02'03"N`) and convert it. AGL ≥ 0 and < 1000 m. `--units ft` converts AGL ×0.3048.
- Names: XML-escape them (`& < > " '`).
- Elevation failure: retry ×2, then use the next provider. If all fail, require `--ground-a/b` or exit non-zero with a clear message.

### Edge cases
- Identical or near-identical points (D < 1 m): error. D < 50 m: warn, and clamp the stations to ≥ 8.
- Long links (> 100 km): warn that 5.8 GHz is unusual at this range. Use Geod. Bulge matters (≈37 m at 50 km midpoint).
- Antimeridian: compute in the geodesic, then normalise each lon to (−180, 180]. Google Earth draws polygons crossing ±180 correctly when the vertices are individually normalised and each quad is small. If a quad's lon span > 180°, add 360 to its negative lons.
- Poles (|lat| > 85°): the lon-offset conversion diverges. Use Geod.fwd for the offsets.
- Sites over water: the DEM may return null or 0. Treat null as 0 and warn.

### Test cases (λ = 0.0516884 m)
| # | Input | Expected |
|---|---|---|
| T1 | f=5.8 GHz | λ = 0.05168835 ± 1e‑8 |
| T2 | D=10 000 m, midpoint | r1 = 11.3675 m ±0.001, 0.6r1 = 6.8205, bulge(k=4/3) = 1.4715 |
| T3 | D=1 000 m midpoint | r1 = 3.5947 m |
| T4 | D=50 000 m midpoint | r1 = 25.4186 m, bulge = 36.788 m |
| T5 | D=10 km, d1=2.5 km | r1 = sqrt(λ·2500·7500/10000) = 9.8446 m |
| T6 | (0,0)→(0,1) | haversine D ≈ 111 195 m (R=6371008.8), bearing 90° |
| T7 | (37,−122)→(37.05,−121.9) | D ≈ 10.4 km (check against pyproj within 0.5%) |
| T8 | Generated KML | parses as XML. Every coordinate triple has 3 values. Mesh polygon count = N·M. Every ring is closed. |
| T9 | Ring geometry | for each station, the distance of every ring vertex from the LOS point (ENU) = r ± 1 cm |
| T10 | Synthetic terrain: flat ground 0 m, towers 10 m AGL, D=10 km | min clearance c = (10−1.47)/11.37 ≈ 0.75 → CLEAR. Towers 5 m → c ≈ 0.31 → MARGINAL |
| T11 | lon 179.99 → −179.99 | D ≈ 1.1 km × cos(lat). All output lons in range. No world-spanning polygons. |
| T12 | Manual ground override | no network calls (mock asserts) |

Mock the elevation providers in unit tests. Run a manual acceptance test in Google Earth Pro: open the file, check that the ellipsoid is centred on the LOS and touches the tower tops, and check transparency.

## References
- Fresnel zone: https://en.wikipedia.org/wiki/Fresnel_zone
- ITU-R P.530 (LOS design, k-factor): https://www.itu.int/rec/R-REC-P.530
- KML altitude modes: https://developers.google.com/kml/documentation/altitudemode
- KML reference (colors, Polygon, MultiGeometry): https://developers.google.com/kml/documentation/kmlreference
- OpenTopoData API: https://www.opentopodata.org/api/ ; limits: https://www.opentopodata.org/#public-api
- USGS EPQS: https://apps.nationalmap.gov/epqs/
- Open-Elevation: https://open-elevation.com/
- Google Elevation API: https://developers.google.com/maps/documentation/elevation/overview
- Geodesy formulas: https://www.movable-type.co.uk/scripts/latlong.html
- simplekml: https://simplekml.readthedocs.io/
